import re
import unittest
from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED, evidence_sentence, judge, positive_pattern_matches


def _blk(text: str, std: str | None = None, topic: str | None = None) -> PolicyBlock:
    return PolicyBlock(heading=text[:60], standard_no=std, topic_heading=topic, start=0, text=text, capped=False)


class EarlyAdoptionRulesTests(unittest.TestCase):
    spec = TOPICS["STD_EARLY_ADOPTION"]

    def test_단정형_조기적용은_EARLY_ADOPTED(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1109호 개정. 조기적용이 허용됨에 따라 당사는 동 기준서를 조기적용하였습니다.", "1109"))
        self.assertEqual((j.value, j.subject, j.method), ("EARLY_ADOPTED", "1109", "rule"))
        self.assertIn("조기적용하였습니다", j.evidence)

    def test_조건문은_긍정이_아니다(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1008호 개정. 이러한 개정사항을 조기적용하기로 선택하는 경우, 다음 중 하나를 적용할 것이 요구됩니다. 조기적용이 허용됩니다.", "1008"))
        self.assertEqual(j.value, "PERMITTED_ONLY")

    def test_부재문은_NOT_ADOPTED(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1101호. 회사가 조기도입하기로 한 기준서 및 해석서는 없습니다.", "1101"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_미적용_서술은_NOT_ADOPTED(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1118호 '재무제표 표시와 공시' 제정. 회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_시행일_서술만_있어도_NOT_ADOPTED(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1118호 제정. 기준서는 2027년 1월 1일 이후 시작하는 회계연도부터 적용되며, 조기도입이 가능합니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_비교정보_재작성은_긍정_패턴이_아니다(self) -> None:
        self.assertFalse(positive_pattern_matches(self.spec, "EARLY_ADOPTED", "2026년 비교정보는 제1118호에 따라 재작성됩니다."))
        self.assertTrue(positive_pattern_matches(self.spec, "EARLY_ADOPTED", "당사는 동 기준서를 조기적용하였습니다."))

    def test_설명만_있으면_UNDETERMINED(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1118호 제정. 손익계산서에 새로운 중간합계를 도입합니다.", "1118"))
        self.assertEqual((j.value, j.method), (UNDETERMINED, "none"))

    def test_기준서_블록이_아니면_적용_대상이_아니다(self) -> None:
        self.assertFalse(self.spec.applies(_blk("(5) 유형자산 재평가모형", None, "유형자산")))

    def test_허용_문장이_앞에_있어도_미적용이_이긴다(self) -> None:
        j = judge(self.spec, _blk("동 기준서는 조기 적용이 허용되며, 회사는 아직 채택하지 않았습니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_strip_이후_근거는_원문_축자다_Q_I2_재현(self) -> None:
        """리뷰 Q-I2 재현: _T1_STRIP("조기적용하기로 선택하는 경우…") 이 문장 중간의 실제
        보일러플레이트를 지운다. 길이 보존 치환 전에는 지워진 구절 뒤에서 잡힌 pos 로 뽑은
        evidence 가 원문에 없는 문자열이었다 — 이제는 항상 block.text 의 부분 문자열이어야 한다."""
        text = (
            "기업회계기준서 제1118호 수익 당사가 동 기준서를 조기적용하기로 선택하는 경우 "
            "그 사실과 영향을 주석에 공시합니다. 회사는 동 기준서의 조기적용이 허용됨을 확인하였습니다."
        )
        block = _blk(text, "1118")
        j = judge(self.spec, block)
        self.assertEqual(j.value, "PERMITTED_ONLY")
        self.assertIn(j.evidence, text)
        self.assertEqual(j.evidence, "회사는 동 기준서의 조기적용이 허용됨을 확인하였습니다.")

    def test_적용하되_조기적용_허용은_NOT_ADOPTED(self) -> None:
        """R5: "…부터 적용하되 조기적용이 허용됩니다"는 미적용 서술이지 허용만이 아니다."""
        j = judge(self.spec, _blk("새로운 기준서는 2027년 1월 1일 이후 최초로 시작되는 회계연도부터 적용하되 조기적용이 허용됩니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_영향_미치지_않을_것으로_예상은_NOT_ADOPTED(self) -> None:
        j = judge(self.spec, _blk("동 개정사항이 재무제표에 중요한 영향을 미치지 않을 것으로 예상됩니다. 조기적용이 허용됩니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")

    def test_조기적용_허용_단독은_여전히_PERMITTED_ONLY(self) -> None:
        j = judge(self.spec, _blk("조기적용이 허용됩니다.", "1118"))
        self.assertEqual(j.value, "PERMITTED_ONLY")

    def test_시기_영향_변형_6문장은_NOT_ADOPTED_V7_R1(self) -> None:
        """V7 R1: 1118호 표본 140건 판독(NOT_ADOPTED 80/80 정확, PERMITTED_ONLY 30건 중 0건 정확)
        에서 나온 실제 시기·영향 서술 변형. 조기적용 허용 문구가 뒤에 붙어도 미적용 서술이
        이긴다(NOT_ADOPTED 대안이 목록에서 PERMITTED_ONLY 보다 앞이라 순서상 이긴다)."""
        sentences = [
            "최초 적용 시에 연결기업의 연결재무제표에 중요한 영향을 미칠 것으로 예상되는 항목은 다음과 같습니다.",
            "회사가 기업회계기준서 제1118호를 적용하여 재무제표를 작성하는 경우 현행 재무제표와 유의적인 "
            "차이를 발생시킬 것으로 예상되는 주요 회계정책은 다음과 같습니다.",
            "2027년 1월 1일 이후에 시작되는 보고기간에 적용됩니다.",
            "2027년 1월 1일 이후 시작하는 회계연도에 적용해야 하며, 조기적용이 허용됩니다.",
            "2026년 12월 31일로 종료되는 회계연도의 비교정보는 제1118호에 따라 재작성됩니다.",
            "표시와 공시에 미치는 영향은 광범위할 것으로 예상합니다.",
        ]
        for sent in sentences:
            with self.subTest(sent=sent):
                j = judge(self.spec, _blk(sent + " 조기적용이 허용됩니다.", "1118"))
                self.assertEqual(j.value, "NOT_ADOPTED")

    def test_의무_소급_적용_시기_서술_2문장은_NOT_ADOPTED_round3_addendum(self) -> None:
        """Round 3 addendum(item 7, T1): 중간 실행에서 1118호 PERMITTED_ONLY 로 남은 4건이
        전부 이 두 시기 서술(의무/소급 적용) 변형을 놓쳐서였다."""
        sentences = [
            "기업회계기준서 제1118호는 2027년 1월 1일 이후 시작하는 회계연도부터 의무 적용되며, "
            "2026년 1월 1일 이후 개시하는 회계연도에 조기 적용이 가능합니다.",
            "기업회계기준서 제1118호는 2027년 1월 1일 이후 개시하는 연차 보고기간의 시작일부터 "
            "소급적용되며 조기적용이 허용됩니다.",
        ]
        for sent in sentences:
            with self.subTest(sent=sent):
                j = judge(self.spec, _blk(sent, "1118"))
                self.assertEqual(j.value, "NOT_ADOPTED")

    def test_조기적용이_허용됩니다_단독은_여전히_PERMITTED_ONLY_round3_addendum(self) -> None:
        """Round 3 addendum 회귀 확인: 새 시기 서술 가드가 "부터"·"의무"·"소급" 토큰이 아예
        없는 순수 "조기적용이 허용됩니다." 단독 문장까지 NOT_ADOPTED 로 끌어내리면 안 된다."""
        j = judge(self.spec, _blk("조기적용이 허용됩니다.", "1118"))
        self.assertEqual(j.value, "PERMITTED_ONLY")

    def test_소급_적용합니다는_UNDETERMINED_재리뷰_X2_20260316001380(self) -> None:
        """재리뷰 Important X2 재현(20260316001380/1008, dart_collected 실측): "기업회계기준서
        제1008호에 따라 소급 적용합니다."는 회계정책을 소급 **적용한다**는 진술이지 미적용
        선언이 아니다 — Round 3 addendum 의 첫 대안이 "합니다" 어미까지 허용해 2,000 보고서
        표본 21건 중 14건을 오발화시켰다. "합니다" 를 뺀다."""
        text = (
            "기업회계기준서 제1008호에 따라 소급 적용합니다. 위험회피회계 요구사항에 대한 "
            "개정사항은 최초 적용일 이후 지정된 새로운 위험회피관계에 전진적으로 적용합니다."
        )
        j = judge(self.spec, _blk(text, "1008"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_부터_적용한_회계정책은_UNDETERMINED_재리뷰_X2_20260331002049(self) -> None:
        """재리뷰 Important X2 재현(20260331002049/1017, dart_collected 실측): "1) 2019년
        1월 1일부터 적용한 회계정책"은 회사가 2019년부터 이미 적용해 왔다는 서술인데, 둘째
        대안이 어미를 전혀 요구하지 않아 과거·현재형("적용한")까지 NOT_ADOPTED 로 잡았다 —
        형제 대안들처럼 미래·피동 어미를 요구한다."""
        text = (
            "기업회계기준서 제1017호와 기업회계기준서 제1116호 하에서 적용한 회계정책의 세부 "
            "내용은 아래와 같습니다. 1) 2019년 1월 1일부터 적용한 회계정책 1-1) 당사가 리스이용자인 "
            "경우 당사는 계약 약정일에 계약이 리스인지 또는 계약에 리스가 포함되어 있는지를 평가합니다."
        )
        j = judge(self.spec, _blk(text, "1017"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_파_악_중_띄어쓰기_실측도_NOT_ADOPTED_V7_R1(self) -> None:
        """실측: 원문에 "파 악 중"처럼 글자 사이에 띄어쓰기가 있는 경우도 잡아야 한다."""
        j = judge(self.spec, _blk("회사는 동 기준서 적용에 따른 영향을 파 악 중입니다.", "1118"))
        self.assertEqual(j.value, "NOT_ADOPTED")


class PpeRulesTests(unittest.TestCase):
    spec = TOPICS["PPE_MEASUREMENT"]

    def test_재평가모형_적용(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 당사는 토지에 대해 재평가모형을 적용하고 있습니다.", None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_원가모형_적용(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 유형자산은 원가모형을 적용하여 취득원가에서 감가상각누계액을 차감합니다.", None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_기준서_설명형_선택지는_UNDETERMINED(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 유형자산은 원가모형 또는 재평가모형 중 하나를 선택하여 적용할 수 있습니다.", None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_둘_다_단정하면_MIXED(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 토지는 재평가모형을 적용하고 건물과 기계장치는 원가모형을 적용하고 있습니다.", None, "유형자산"))
        self.assertEqual(j.value, "MIXED")

    def test_투자부동산_블록_안의_유형자산_언급으로는_적용_대상이_아니다(self) -> None:
        block = _blk("(6) 투자부동산 유형자산은 원가모형을 적용합니다.", None, "투자부동산")
        self.assertFalse(TOPICS["PPE_MEASUREMENT"].applies(block))

    def test_유형자산_표제_블록은_원가모형_적용(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 유형자산은 원가모형을 적용합니다.", None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_유형자산_근접_원가모형_사용도_적용(self) -> None:
        j = judge(self.spec, _blk("(5) 유형자산 당사는 원가모형을 사용합니다.", None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_원가모형적용_시_비교표는_회사의_선택이_아니다(self) -> None:
        """R1: 재평가 내역 표의 '원가모형적용 시 장부금액'은 비교 표시일 뿐 회사 선택이 아니다."""
        table = (
            "4) 재평가된 유형자산이 원가모형으로 평가되었을 경우의 장부금액 "
            "(단위 : 천원) 분류별 유형자산 재평가금액 원가모형적용 시 장부금액 토 지 2,348,650 975,509"
        )
        j = judge(self.spec, _blk(table, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_원가모형적용_시_비교표_옆에_재평가모형_단정이_있으면_REVALUATION_MODEL(self) -> None:
        table = (
            "4) 재평가된 유형자산이 원가모형으로 평가되었을 경우의 장부금액 "
            "(단위 : 천원) 분류별 유형자산 재평가금액 원가모형적용 시 장부금액 토 지 2,348,650 975,509"
        )
        text = "당사는 토지에 대하여 재평가모형을 적용하고 있습니다. " + table
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_비교표_뒤에_재평가모형_단정이_있어도_삼켜지지_않는다_V5_R1_narrowing(self) -> None:
        """재리뷰 V5-R1 narrowing 재현: 표(마침표 없음) 자체는 strip 트리거만 지워지고, 마침표
        없이 바로 뒤에 이어지는 진짜 긍정 문장은 삼켜지지 않아야 한다. 문장 확장형(앞뒤 [^.]*)
        strip 이었을 때는 표 뒤 문장까지 통째로 지워져 UNDETERMINED 로 뒤집혔다."""
        table = (
            "4) 재평가된 유형자산이 원가모형으로 평가되었을 경우의 장부금액 "
            "(단위 : 천원) 분류별 유형자산 재평가금액 원가모형적용 시 장부금액 토 지 2,348,650 975,509"
        )
        text = table + " 당사는 토지에 대하여 재평가모형을 적용하고 있습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")
        self.assertIn("재평가모형을 적용하고 있습니다", j.evidence)

    def test_열거_쉼표로_구분된_재평가모형과_원가모형은_MIXED_V6_R1(self) -> None:
        """V6 R1: "토지는 재평가모형을, 기타 유형자산은 원가모형을 적용"처럼 REVAL 이 쉼표로
        끊기는 열거형이면 기존 REVAL 패턴("적용/채택/사용" 동사 필요)이 못 잡아 COST_MODEL 로만
        판정됐다. 열거 쉼표 변형을 추가한다."""
        text = "당사는 유형자산중 토지는 재평가모형을, 기타 유형자산은 원가모형을 적용하고 있습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "MIXED")

    def test_원가모형_동사가_평가_측정이어도_MIXED_V6_R1(self) -> None:
        """V6 R1: COST 트리거 동사가 "평가/측정"(으로/로 조사)이면 기존 패턴("적용/채택/사용"만
        허용)이 못 잡았다."""
        text = "토지를 제외한 유형자산은 원가모형으로 평가하고 있습니다. 토지에 대하여 재평가모형을 적용하고 있습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "MIXED")

    def test_취득원가로_평가도_COST로_잡혀_MIXED_V6_R1(self) -> None:
        """V6 R1: "취득원가로 평가"도 COST_MODEL 트리거다(유형자산 앵커 유지)."""
        text = "(5) 유형자산 재평가모형을 적용한 토지를 제외하고는 취득원가로 평가하고 있습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "MIXED")

    def test_취득원가로_평가_앵커가_다른_자산_문장을_넘지_않는다_재리뷰_I2(self) -> None:
        """재리뷰 Important #2 재현(20260424000208): "유형자산…취득원가로 평가" 앵커가
        `.{0,60}` 간격 안의 실제 문장 마침표를 넘어, 60자 앞의 블록 표제 토큰 "유형자산"이
        전혀 다른 자산(매도가능증권)의 "취득원가로 평가" 문장에 앵커를 채워버렸다 —
        `[^.]{0,60}` 로 문장 경계를 넘지 않게 좁힌다."""
        text = (
            "다. 유형자산 유형자산의 내용연수 및 잔존가액의 결정은 법인세법의 규정을 적용하였습니다. "
            "매도가능증권 시장성 없는 지분증권은 취득원가로 평가하였으나 손상차손 인식 대상은 아닙니다."
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_원가모형에서_재평가모형으로_회계정책_변경은_REVALUATION_MODEL_V6_R1(self) -> None:
        """V6 R1: "원가모형에서 재평가모형으로 회계정책을 변경하였습니다"는 변경 후 모형(REVAL)이
        최종값이다. "원가모형에서"는 strip(구절만) 해 COST 오탐을 막는다."""
        text = "토지 및 건물에 대해 후속측정방법을 원가모형에서 재평가모형으로 회계정책을 변경하였습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_재평가모형에서_원가모형으로_회계정책_변경은_COST_MODEL_재리뷰_Minor_2(self) -> None:
        """재리뷰 Minor #2: REVAL 방향 정책변경 패턴만 있고 반대 방향(COST)이 없어 "재평가모형에서
        원가모형으로 회계정책을 변경하였습니다"가 UNDETERMINED 로 남아 있었다 — 대칭 패턴을
        추가한다(IP_MEASUREMENT 의 _T3_POS 가 이미 공정가치·원가 양방향을 갖는 것과 같은 구조).
        재리뷰 N3 로 이 패턴에 "유형자산" 앵커가 붙어, 형제 COST 패턴들처럼 앵커 토큰이 필요해
        졌다(문장에 직접 넣어 앵커를 공급 — 브리프 "문장 그대로" 관례는 R1 때부터 앵커가 필요한
        패턴에는 적용되지 않았다)."""
        text = "(5) 유형자산 재평가모형에서 원가모형으로 회계정책을 변경하였습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_유형자산_토큰이_멀리_있으면_투자부동산_정책변경이_COST로_새지_않는다_재리뷰_N3(
        self,
    ) -> None:
        """재리뷰 N3 재현: COST 방향 정책변경 패턴에 "유형자산" 앵커가 없어 PPE 로 분류된
        블록 안의 다른 자산(투자부동산) 문장에서도 발화할 수 있었다. 형제 COST 패턴들과
        같은 앵커를 넣으면, 블록 표제의 "유형자산" 토큰에서 60자 넘게 떨어진 투자부동산
        정책변경 문장은 더 이상 이 패턴으로 안 잡힌다(60자 이내인 경우까지는 못 막는다 —
        형제 COST 패턴들도 같은 근접 기반 한계를 공유한다, 실데이터 0건이라 이번 라운드
        범위 밖)."""
        filler = "당사는 매 분기 정기 이사회를 통해 내부통제 절차를 점검하고 그 결과를 문서로 남깁니다. "
        text = "(5) 유형자산 " + filler * 2 + "투자부동산에 대하여는 공정가치모형에서 원가모형으로 회계정책을 변경하였습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_재평가모형에_따라는_REVALUATION_MODEL_V6_R1(self) -> None:
        text = "유형자산 재평가모형에 따라 토지, 건물에 대해 재평가를 실시하였습니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_재평가모형에_따라_손상차손_조건절은_REVAL이_아니다_재리뷰_I1(self) -> None:
        """재리뷰 Important #1 재현(20260331004511/20260610000623): "재평가모형을 도입하여
        재평가모형에 따라 재평가금액을 장부금액으로 하는 경우 …"는 손상차손 회계처리 규정을
        서술하는 조건절이지 회사의 측정모형 선택 진술이 아니다 — R2 가 막 제거한 "적용하는
        자산" 오탐 클래스를 다른 표면형으로 다시 들여온 사례다."""
        text = (
            "다.유형자산 재평가모형을 도입하여 재평가모형에 따라 재평가금액을 장부금액으로 하는 경우 "
            "재평가되는 자산의 손상차손은 당해 사업연도의 손익으로 인식합니다."
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_재평가하여_계상하고_있습니다는_REVALUATION_MODEL_재리뷰_N1_20260407000993(
        self,
    ) -> None:
        """재리뷰 Critical N1 재현(20260407000993, dart_collected 실측): I1 가드의 첫 버전이
        피동·연결형("재평가하여 … 계상하고 있습니다")을 놓쳐 진짜 REVAL 진술을 UNDETERMINED
        로 잃었다."""
        text = (
            "5-4. 토지의 재평가당사는 유형자산의 인식시점 후 재평가모형에 따라 주기적으로 공정가치로 "
            "토지를 재평가하여 재평가액을 장부가액으로 계상하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_평가되었으며는_REVALUATION_MODEL_재리뷰_N1_20251110000027(self) -> None:
        """재리뷰 Critical N1 재현(20251110000027, dart_collected 실측): "재평가모형에 따라
        평가되었으며 원가모형으로 평가되었을 경우의 장부금액은 …"(IAS 16.77(e) 비교표 도입문)도
        잃었던 진짜 REVAL 진술."""
        text = (
            "(3) 토지 및 건물은 재평가모형에 따라 평가되었으며 원가모형으로 평가되었을 경우의 "
            "장부금액은 다음과 같습니다(주석15 참조)."
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_평가되었으며는_REVALUATION_MODEL_재리뷰_N1_20260407003334(self) -> None:
        """재리뷰 Critical N1 재현(20260407003334, dart_collected 실측): 위와 같은 결함의 세
        번째 실측."""
        text = (
            "(2) 토지 및 건물은 재평가모형에 따라 평가되었으며 당기말 현재 원가모형으로 평가되었을 "
            "경우의 장부금액은 다음과 같습니다(단위:원)."
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    def test_재평가모형에_따라_계상하는_경우_손상차손_조건절은_REVAL이_아니다_재리뷰_X4(
        self,
    ) -> None:
        """재리뷰 Minor X4 재현: N1 이 새로 넓힌 "계상" 표면형이 I1 이 닫으려던 손상 조건절
        클래스를 다시 열었다 — "재평가모형에 따라 재평가금액을 장부금액으로 계상하는 경우
        손상차손은 …"는 손상 회계처리 규정이지 회사의 측정모형 선택 진술이 아니다."""
        text = "다.유형자산 재평가모형에 따라 재평가금액을 장부금액으로 계상하는 경우 손상차손은 재평가잉여금과 상계합니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertNotEqual(j.value, "REVALUATION_MODEL")

    def test_재평가모형에_따라_평가되어_있는_자산의_손상차손은_REVAL이_아니다_재리뷰_X4(
        self,
    ) -> None:
        """재리뷰 Minor X4 재현: N1 이 새로 넓힌 "평가되어" 표면형도 같은 손상 조건절
        클래스를 열었다 — "재평가모형에 따라 평가되어 있는 자산의 손상차손은 …"도 손상
        회계처리 규정이다."""
        text = "다.유형자산 재평가모형에 따라 평가되어 있는 자산의 손상차손은 재평가잉여금과 우선 상계합니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertNotEqual(j.value, "REVALUATION_MODEL")

    def test_원가모형_적용하여_측정하였을_경우도_가정형_strip_V6_R2(self) -> None:
        """V6 R2: 기존 가정형 strip("원가모형으로 평가되었을 경우")은 동사와 어미 사이에
        "하여 측정"이 끼는 변형("적용하여 측정하였을 경우")을 못 잡아 COST_MODEL 로 오판했다."""
        text = "재평가된 유형자산이 원가모형을 적용하여 측정하였을 경우의 장부금액입니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_재평가모형을_적용하는_자산_문구는_손상_회계정책이지_선택이_아니다_V6_R2(self) -> None:
        """V6 R2: "재평가모형을 적용하는 자산으로서 …"는 손상 회계정책 문구지 회사의 측정모형
        선택 진술이 아니다."""
        text = "재평가모형을 적용하는 자산으로서 손상징후가 있는 경우 회수가능액을 추정합니다."
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_C1_repro_20260409002974_측정하는_경우는_COST가_아니다(self) -> None:
        """재리뷰 Critical #1 재현(20260409002974): R1 이 COST 긍정 동사에 "평가|측정"을
        추가했는데 R2 가정형 strip 의 동사는 "적용|평가" 뿐이라 "원가모형으로 측정하는 경우"가
        strip 을 통과해 COST_MODEL 로 오판됐다 — 재평가모형 회사의 IAS 16.77(e) 의무 공시표
        열 제목이 트리거가 된 사례다."""
        text = (
            "다. 재평가된 유형자산이 원가모형으로 측정하는 경우의 장부가액 "
            "(단위 : 천원) 구 분 재평가금액 원가모형으로 측정하는 경우 장부가액 토 지 63,171,840 21,930,172"
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_C1_repro_20260213000544_측정하는_경우는_COST가_아니다(self) -> None:
        """재리뷰 Critical #1 재현(20260213000544): 위와 같은 결함의 두 번째 실측."""
        text = "구 분 재평가금액 원가모형으로 측정하는 경우 장부금액 토 지 10,858,000 3,797,622"
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_C1_repro_20260429000737_표_제목이_REVAL_진술을_MIXED로_강등하지_않는다(self) -> None:
        """재리뷰 Critical #1 재현(20260429000737): 조사·어미 없이 표 열 제목으로만 나오는
        "원가모형 평가 장부가액"(가정형 strip 의 시/했을/하였을/…/하는 경우 어미를 아예 안
        가짐)이 같은 블록의 정확한 REVALUATION_MODEL 진술("재평가모델을 적용하고 있으며")을
        MIXED 로 강등시켰다 — 표 열 제목 형태 전용 strip 으로 고친다."""
        text = (
            "연결회사는 토지의 인식시점 이후의 측정과 관련하여 재평가모델을 적용하고 있으며, "
            "④ 재평가된 유형자산이 원가모형으로 평가되었을 경우의 장부가액 분류별 유형자산 재평가금액 "
            "원가모형 평가 장부가액"
        )
        j = judge(self.spec, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")


_KOREAN_ALT_GROUP_RE = re.compile(r"\(\?:([가-힣|]+)\)")


def _last_pure_korean_alt_group(pattern_source: str) -> list[str]:
    """정규식 SOURCE 문자열(`Pattern.pattern`)에서, regex 메타문자가 섞이지 않은(=순수 한글과
    "|" 로만 된) `(?:…)` 대안 그룹을 전부 찾아 마지막 것을 돌려준다. "원가…(?:모형|모델)…"
    구조에서는 조사 그룹(을|를|으로|로 등)도 순수 한글+"|" 라 같이 걸리지만, 조사 그룹은 항상
    동사 그룹보다 앞에 나오고 어미 그룹(…했을|하였을|…\\s*경우 등)은 "\\s*" 가 섞여 있어
    이 필터에 안 걸린다 — 그래서 "마지막"을 취하면 항상 동사 그룹이 남는다."""
    groups = _KOREAN_ALT_GROUP_RE.findall(pattern_source)
    return groups[-1].split("|") if groups else []


def _cost_positive_verbs() -> set[str]:
    """`TOPICS["PPE_MEASUREMENT"].positive`(=_T2_POS)에서 "원가…(?:모형|모델)…<동사>" 측정
    패턴들(취득원가 패턴·정책변경 패턴은 다른 구조라 제외)의 동사 alternation 을 소스에서
    직접 유도해 합집합으로 돌려준다. 하드코딩 리터럴이 없어, positive 쪽에 새 동사가
    추가되면 다음 실행에서 자동으로 이 집합에 들어온다(N5)."""
    spec = TOPICS["PPE_MEASUREMENT"]
    verbs: set[str] = set()
    for pat, value in spec.positive:
        s = pat.pattern
        if (
            value == "COST_MODEL"
            and "(?:모형|모델)" in s
            and "취득원가" not in s
            and "변경" not in s
            and "전환" not in s
        ):
            verbs.update(_last_pure_korean_alt_group(s))
    return verbs


def _cost_strip_verbs() -> set[str]:
    """`TOPICS["PPE_MEASUREMENT"].strip` 에서 가정형(…했을|하였을|되었을|…경우 어미) strip 의
    동사 alternation 을 소스에서 직접 유도한다. 마찬가지로 하드코딩 없음(N5)."""
    spec = TOPICS["PPE_MEASUREMENT"]
    verbs: set[str] = set()
    for pat in spec.strip:
        s = pat.pattern
        if "(?:모형|모델)" in s and ("했을" in s or "하였을" in s or "되었을" in s or "경우" in s):
            verbs.update(_last_pure_korean_alt_group(s))
    return verbs


class PpeCostVerbSyncInvariantTests(unittest.TestCase):
    """C1/I4: R1(positive 동사 확장)과 R2(가정형 strip)가 서로 다른 동사 집합을 쓰면, positive
    가 strip 보다 넓은 동사를 인정하는 순간 그 동사로만 나오는 표 열 제목·가정형 문구가
    strip 을 통과해 COST 트리거로 오판된다(C1 이 실측한 사고). 이 불변식을 고정한다:
    "유형자산…원가모형(을|를|으로|로)?<동사>" 로 COST_MODEL 이 되는 모든 동사는, 같은 동사가
    가정형 어미(…하는 경우 등)와 결합했을 때도 반드시 strip 되어 UNDETERMINED 가 되어야 한다.

    재리뷰 N5: 첫 버전은 검증할 동사 목록(`_COST_VERBS`)이 테스트에 하드코딩된 리터럴이라,
    C1 을 만든 바로 그 변경(positive 동사 집합만 넓히고 strip 은 그대로 두기)이 이 테스트를
    그대로 통과했다(테스트가 "무엇이 실제로 바뀌었는지"를 전혀 보지 않았기 때문). 이제
    `_cost_positive_verbs()`/`_cost_strip_verbs()` 가 `policy_rules.py` 의 실제 정규식
    SOURCE 문자열에서 동사 alternation 을 파싱해 유도한다 — 어느 쪽 목록만 바뀌어도(동사를
    추가하거나 빼도) 두 집합이 갈라져 이 테스트가 깨진다(양방향)."""

    spec = TOPICS["PPE_MEASUREMENT"]

    def test_동사_목록을_소스에서_유도할_수_있다(self) -> None:
        """유도 자체가 실패(빈 집합)하면 이후 테스트가 공허하게 통과하므로 먼저 확인한다."""
        self.assertTrue(_cost_positive_verbs())
        self.assertTrue(_cost_strip_verbs())

    def test_positive_동사_집합과_strip_동사_집합이_같다(self) -> None:
        positive_verbs = _cost_positive_verbs()
        strip_verbs = _cost_strip_verbs()
        self.assertEqual(
            positive_verbs, strip_verbs,
            f"positive={sorted(positive_verbs)} strip={sorted(strip_verbs)} "
            f"— 대칭 차: {sorted(positive_verbs ^ strip_verbs)}",
        )

    def test_모든_COST_동사가_긍정에서_발화한다(self) -> None:
        for verb in sorted(_cost_positive_verbs()):
            with self.subTest(verb=verb):
                text = f"(5) 유형자산 당사는 원가모형을 {verb}하고 있습니다."
                j = judge(self.spec, _blk(text, None, "유형자산"))
                self.assertEqual(j.value, "COST_MODEL")

    def test_모든_COST_동사가_가정형_strip에서도_제거된다(self) -> None:
        for verb in sorted(_cost_positive_verbs()):
            with self.subTest(verb=verb):
                text = f"(5) 유형자산 원가모형을 {verb}하는 경우의 장부금액입니다."
                j = judge(self.spec, _blk(text, None, "유형자산"))
                self.assertEqual(j.value, UNDETERMINED)


class RevenueRulesTests(unittest.TestCase):
    spec = TOPICS["REVENUE_METHOD"]

    def test_진행기준(self) -> None:
        j = judge(self.spec, _blk("(12) 수익인식 당사는 건설계약 수익을 진행률에 따라 기간에 걸쳐 인식합니다.", None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_인도기준(self) -> None:
        j = judge(self.spec, _blk("(12) 수익인식 재화의 통제가 이전되는 시점에 수익을 인식합니다.", None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_둘_다면_BOTH(self) -> None:
        j = judge(self.spec, _blk("(12) 수익인식 공사수익은 진행률에 따라 인식하고 제품매출은 인도시점에 인식합니다.", None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_기준서_인용문은_회사_선택이_아니다(self) -> None:
        j = judge(self.spec, _blk("기업회계기준서 제1115호에 따르면, 기간에 걸쳐 이행하는 수행의무는 진행기준에 따라 수익을 인식한다.", None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_용역_제공시점과_인도기준은_POINT_IN_TIME(self) -> None:
        text = (
            "당사는 객실료, 스키ㆍ골프장ㆍ워터파크 수입, 봉사료 수입 및 회원권사용수익은 용역의 제공시점에 따라 "
            "수익으로 인식하고 있으며, 상품 등의 매출은 인도기준에 따라 수익을 인식하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_통제_이전_시점과_제공하는_기간에_걸쳐는_BOTH(self) -> None:
        text = "재화의 판매는 통제가 이전되는 시점에 수익을 인식하고 있습니다. 용역은 제공하는 기간에 걸쳐 수익을 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_위험과_보상_이전_시점과_진행률은_BOTH(self) -> None:
        text = "재화의 소유에 따른 위험과 보상이 구매자에게 이전되는 시점에 수익을 인식하고 있습니다. 공사계약은 거래의 진행률에 따라 수익을 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_위험과_보상이_이전되지_않는다는_부정절은_BOTH로_승격하지_않는다_재리뷰_Minor_4(self) -> None:
        """재리뷰 Minor #4 재현: "위험과 보상이 …이전되지 않"(리스 분류·수익 미인식 서술)에도
        부정 가드 없이 매치돼, OVER_TIME 단독 회사가 잘못 BOTH 로 승격됐다."""
        text = (
            "공사계약은 거래의 진행률에 따라 수익을 인식하고 있습니다. "
            "자산의 소유에 따른 위험과 보상이 대부분 이전되지 않기 때문에 운용리스로 분류하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_시점에_수익을_인식과_기간에_걸쳐는_BOTH_V6_R3(self) -> None:
        """V6 R3: "시점에 수익을 인식"(통제·인도 언급 없이 "시점에"만)도 POINT_IN_TIME 트리거다."""
        text = "Food service 수입에 대하여 시점에 수익을 인식하고 있습니다. 임대수입에 대하여 기간에 걸쳐 수익을 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_통제가_고객에게_이전되는_시점은_POINT_IN_TIME_V6_R3(self) -> None:
        """V6 R3: 기존 패턴은 "통제가"와 "이전"이 바로 붙어야 했다 — "통제가 고객에게 이전되는
        시점"처럼 사이에 "고객에게"가 끼는 변형을 놓쳤다."""
        text = "재화의 통제가 고객에게 이전되는 시점에 수익으로 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_진행률을_곱하여_인식은_OVER_TIME_V6_R3(self) -> None:
        """V6 R3: "진행률을 곱하여"는 기존 "진행률에 따라/의해/적용/기준으로" 목록에 없던 동사다."""
        text = "도급공사에 대한 수익은 도급금액에 공사진행률을 곱하여 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_시점에_수익을_인식과_기간에_걸쳐_BOTH는_회귀하지_않는다_재리뷰_I3(self) -> None:
        """재리뷰 Important #3 가드 추가 후에도 브리프의 원래 BOTH 시나리오(POINT_IN_TIME 이
        OVER_TIME 보다 문장 순서상 먼저 나옴)는 그대로 BOTH 여야 한다 — "기간에 걸쳐" 가
        "시점에 수익을 인식" 보다 뒤에 나오므로 신규 strip_fn(같은 문장에서 "기간에 걸쳐" 가
        먼저 나올 때만 뒤의 POINT 구절을 지움)이 적용되지 않는다."""
        text = "Food service 수입에 대하여 시점에 수익을 인식하고 있습니다. 임대수입에 대하여 기간에 걸쳐 수익을 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_기간에_걸쳐_뒤의_시점에_수익을_인식하지_않습니다는_OVER_TIME_재리뷰_I3(self) -> None:
        """재리뷰 Important #3(b) 재현: "공사수익은 진행률에 따라 기간에 걸쳐 인식합니다.
        통제가 이전되지 않는 시점에 수익을 인식하지 않습니다."는 OVER_TIME 단독 회사인데,
        부정 가드 없는 "시점에 수익을 인식"이 그대로 매치돼 BOTH 로 잘못 승격됐었다."""
        text = "공사수익은 진행률에 따라 기간에 걸쳐 인식합니다. 통제가 이전되지 않는 시점에 수익을 인식하지 않습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_기간에_걸쳐_뒤의_시점에_수익을_인식은_부정_없어도_OVER_TIME_재리뷰_I3b(self) -> None:
        """재리뷰 Important #3(b) 단독 검증(부정어 없이도): 같은 문장 안에서 "기간에 걸쳐"가
        먼저 나오고 그 뒤에 "시점에 수익을 인식"이 이어지면(부정형이 아니어도) POINT_IN_TIME
        으로 승격하지 않는다 — strip_point_after_over_time 전처리가 이 케이스를 지운다."""
        text = "공사수익은 기간에 걸쳐 인식하고 있으며, 참고로 다른 시점에 수익을 인식하기도 합니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_리스기간에_걸쳐는_OVER_TIME_트리거가_아니라_뒤의_POINT를_지우지_않는다_재리뷰_N2(
        self,
    ) -> None:
        """재리뷰 N2 재현: "리스기간에 걸쳐"는 OVER_TIME 트리거가 아닌 복합어인데, 앵커가 맨
        문자열 "기간에 걸쳐"였던 첫 버전은 이것도 앵커로 잡아 뒤의 진짜 POINT 진술("검수가
        완료되는 시점에 수익을 인식")을 지워 UNDETERMINED 로 뒤집었다."""
        text = "용역수익은 리스기간에 걸쳐 정액 배분하며, 상품매출은 검수가 완료되는 시점에 수익을 인식합니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_보증기간에_걸쳐는_OVER_TIME_트리거가_아니라_뒤의_POINT를_지우지_않는다_재리뷰_N2(
        self,
    ) -> None:
        """재리뷰 N2 재현: "보증기간에 걸쳐"도 같은 종류의 오탐 앵커였다."""
        text = "제품보증은 보증기간에 걸쳐 배분하고, 재화의 판매는 고객에게 인도되는 시점에 수익을 인식합니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_상각기간에_걸쳐는_OVER_TIME_트리거가_아니라_뒤의_POINT를_지우지_않는다_재리뷰_N2(
        self,
    ) -> None:
        """재리뷰 N2 재현: "상각기간에 걸쳐"도 같은 종류의 오탐 앵커였다."""
        text = "개발비는 상각기간에 걸쳐 배분하며, 제품 매출은 출고되는 시점에 수익을 인식합니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_진행률_트리거와_진짜_POINT가_한_문장에_있으면_BOTH_재리뷰_X3(self) -> None:
        """재리뷰 Minor X3 재현: N2 가 pre-pass 앵커를 좁히면서 동시에 `진행률|진행기준|
        투입법` 을 넓혀 새 오탐 경로를 만들었다 — "공사수익은 진행률에 따라 인식하며,
        상품매출은 인도되는 시점에 수익을 인식합니다."에서 진행기준 서술이 앵커가 돼 뒤의
        진짜 시점기준 진술까지 지워 BOTH 가 OVER_TIME 으로 강등됐다. N2(b)(group2 가 "시점"
        토큰을 같이 지우는 문제)를 닫기 전까지는 앵커에서 진행률/진행기준/투입법 을 뺀다."""
        text = "공사수익은 진행률에 따라 인식하며, 상품매출은 인도되는 시점에 수익을 인식합니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_시점에_수익을_인식하지_않습니다_단독은_UNDETERMINED_재리뷰_I3a(self) -> None:
        """재리뷰 Important #3(a) 단독 검증("기간에 걸쳐" 없이도): 부정형 "시점에 수익을
        인식하지 않습니다"만 있으면 POINT_IN_TIME 으로 잡히지 않아야 한다."""
        text = "회사는 특정 조건이 충족되는 시점에 수익을 인식하지 않습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_배당수익_보일러플레이트는_OVER_TIME_단독_회사를_BOTH로_승격하지_않는다_재리뷰_I3a_round2(
        self,
    ) -> None:
        """Fix round 2(리뷰 I3(a) 실측, 표본 A OVER_TIME→BOTH 9건 중 4건): 부수수익(K-IFRS
        1115 수익이 아닌 기타수익) 보일러플레이트 "배당수익은 …확정되는 시점에 수익을
        인식하고 있습니다"는 회사의 시점기준 방침 선언이 아니다 — 진행기준 단독 회사가 이
        문장 하나로 BOTH 로 잘못 승격되면 안 된다."""
        text = "배당수익은 배당금을 받을 권리가 확정되는 시점에 수익을 인식하고 있습니다. 공사수익은 진행률에 따라 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")

    def test_통제가_이전되는_시점은_배당보일러플레이트_가드의_영향을_받지_않는다_재리뷰_round2(
        self,
    ) -> None:
        """Fix round 2 회귀 확인: 부수수익 가드는 배당/이자/로열티 키워드가 문장 시작에서
        조사와 함께 선행할 때만 적용된다(재리뷰 F1 로 임대·수수료는 목록에서 빠지고
        문장-시작·조사 조건이 추가됐다) — 진짜 회사 진술("통제가 이전되는 시점에 수익을
        인식")은 여전히 POINT_IN_TIME 으로 잡혀 BOTH 로 승격돼야 한다."""
        text = "재화의 판매는 통제가 이전되는 시점에 수익을 인식하고 있습니다. 공사수익은 진행률에 따라 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "BOTH")

    def test_이자수익_보일러플레이트는_POINT_IN_TIME이_아니다_재리뷰_round2(self) -> None:
        """Fix round 2: 이자수익도 부수수익 가드 대상이다(문장 시작에서 "이자수익은"으로
        시작하는 형태)."""
        text = "이자수익은 유효이자율법을 적용하여 시점에 수익으로 인식하고 있습니다."
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertNotEqual(j.value, "POINT_IN_TIME")

    def test_수수료수익_대리인_회계_진술은_POINT_IN_TIME_재리뷰_F1_20260430000055(self) -> None:
        """재리뷰 Important F1 재현(20260430000055, dart_collected 실측): "수수료수익"은
        중개·대리인 회사에서는 기타수익이 아니라 주된 1115 수익이다 — "…수수료수익으로
        인식하고 있으며, 상품이 …배송완료된 시점에 수익으로 인식하고 있습니다"는 대리인
        회계의 정석적 시점기준 진술인데, 첫 버전 가드(임대·수수료수익 포함)가 이를 지워
        POINT_IN_TIME 을 UNDETERMINED 로 잃었다 — 임대·수수료수익을 키워드 목록에서 뺀다."""
        text = (
            "연결실체는 재화의 판매 등을 중개하고 이에 대한 수수료를 수취하는 거래는 "
            "수수료수익으로 인식하고 있으며, 해당 용역의 경우 상품이 고객에게 배송완료된 "
            "시점에 수익으로 인식하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "POINT_IN_TIME")

    def test_쉼표_등위절_배당정형구는_UNDETERMINED_재리뷰_X1_20260401001583(self) -> None:
        """재리뷰 Critical X1 재현(20260401001583, dart_collected 실측): 코퍼스의 배당·이자
        정형구는 거의 언제나 "…이자수익은 …하고, 배당수익은 …합니다." 처럼 쉼표로 이어지는
        등위절이다 — "배당수익은" 앞이 마침표가 아니라 쉼표라, 문장-시작 앵커(`^`/마침표
        직후)가 걸리지 않아 부수수익 가드가 통째로 무력화됐다(2,000 보고서 표본에서 가드
        발화 63건 → 0건). 문장-시작 앵커를 제거해 가드가 등위절에서도 걸리게 한다."""
        text = (
            "또한 자산을 타인에게 사용하게 함으로써 창출되는 이자수익, 배당수익은 거래와 관련된 "
            "경제적효익의 유입가능성이 높으며, 수익금액을 신뢰성 있게 측정할 수 있을 때수익을 "
            "인식하며, 이자수익은 원칙적으로 유효이자율법으로 인식하고, 배당수익은 주주로서 "
            "배당을 받을 권리와 금액이 확정되는 시점에 수익을 인식하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_진행률_회사가_배당정형구_쉼표절_때문에_BOTH로_새지_않는다_재리뷰_X1_20260303000854(
        self,
    ) -> None:
        """재리뷰 Critical X1 재현(20260303000854, dart_collected 실측 발췌): 진행기준으로
        용역수익을 인식하는 회사가 뒤에 이어지는 배당·이자·로열티 정형구(쉼표 등위절) 때문에
        BOTH 로 잘못 라벨링됐었다 — 문장-시작 앵커 제거로 정형구가 다시 가드된다."""
        text = (
            "보고기간말에 그 거래의 진행률에 따라 용역의 제공으로 인한 수익을 인식하고 있습니다. "
            "2) 이자수익, 로열티수익 및 배당수익자산을 타인에게 사용하게 함으로써 창출되는 이자수익, "
            "로열티수익 및 배당수익은거래와 관련된 경제적효익의 유입가능성이 높으며, 수익금액을 "
            "신뢰성 있게 측정할 수 있을 때 수익을 인식하며, 이자수익은 원칙적으로 유효이자율법으로 "
            "인식하고, 로열티수익은 관련된 계약의 경제적 실질을 반영하여 발생기준에 따라 인식하며, "
            "배당수익은 주주로서 배당을 받을 권리와 금액이 확정되는 시점에 수익을 인식하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, "OVER_TIME")


class IpRulesTests(unittest.TestCase):
    spec = TOPICS["IP_MEASUREMENT"]

    def test_공정가치모형(self) -> None:
        j = judge(self.spec, _blk("(6) 투자부동산 당사는 투자부동산을 공정가치모형으로 측정하고 있습니다.", None, "투자부동산"))
        self.assertEqual(j.value, "FAIR_VALUE_MODEL")

    def test_원가모형(self) -> None:
        j = judge(self.spec, _blk("(6) 투자부동산 당사는 투자부동산에 대하여 원가모형을 적용합니다.", None, "투자부동산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_기준서_설명형_선택지는_UNDETERMINED(self) -> None:
        j = judge(self.spec, _blk("(6) 투자부동산 투자부동산은 원가모형 또는 공정가치모형 중 하나를 선택하여 적용할 수 있습니다.", None, "투자부동산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_표제_앞_투자부동산_토큰_때문에_엉뚱한_문장이_근거로_뽑히지_않는다(self) -> None:
        """컨트롤러 지시(재리뷰 실측: IP 66행 중 10행·PPE 2,924행 중 2행의 evidence 에 모형 문구가
        없었다): "투자부동산.{0,80}원가모형…적용" 처럼 "." 로 임의 글자를 건너뛰는 간격 패턴은 그
        간격에 실제 문장 마침표가 있으면 매치 시작이 이전 문장(표제)에 남는다 — evidence_sentence
        가 m.start() 를 쓰면 트리거 구절이 아예 없는 문장을 뽑는다. 매치의 끝(m.end()-1)을 쓰면
        항상 트리거 구절이 있는 문장이 나온다."""
        text = (
            "(9) 투자부동산 연결실체는 임대수익이나 시세차익을 목적으로 보유하고 있는 부동산은 "
            "투자부동산으로 분류하고 있습니다. 최초 인식 후 원가모형을 적용하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, "COST_MODEL")
        self.assertIn("원가모형을 적용", j.evidence)
        self.assertEqual(j.evidence, "최초 인식 후 원가모형을 적용하고 있습니다.")

    def test_범주_대체_상용문은_회사의_선택이_아니다(self) -> None:
        """R2: "(주1) …범주에서…범주로 대체한 사항은 없습니다"는 표 각주 상용문이다."""
        text = "(주1) 원가모형을 적용하는 투자부동산 범주에서 공정가치모형을 적용하는 투자부동산의 범주로 대체한 사항은 없습니다."
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_범주_대체_각주_앞에_마침표_없어도_진술이_삼켜지지_않는다_재리뷰_Important_1(self) -> None:
        """재리뷰 Important #1 재현: 정책 진술이 표 셀처럼 마침표 없이 각주 앞에 바로 붙으면,
        문장 확장형(선두 [^.]*) strip 은 그 진술까지 거꾸로 삼켜 COST_MODEL 이 UNDETERMINED 로
        뒤집혔다. phrase-bounded strip 은 "범주에서" 앞을 건드리지 않아야 한다."""
        text = (
            "(6) 투자부동산 당사는 투자부동산에 대하여 원가모형을 적용하고 있습니다 (주1) 원가모형을 "
            "적용하는 투자부동산 범주에서 공정가치모형을 적용하는 투자부동산의 범주로 대체한 사항은 없습니다."
        )
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_범주_대체_상용문_옆에_원가모형_단정이_있으면_COST_MODEL(self) -> None:
        text = (
            "(주1) 원가모형을 적용하는 투자부동산 범주에서 공정가치모형을 적용하는 투자부동산의 범주로 대체한 사항은 없습니다. "
            "당사는 투자부동산에 대하여 원가모형을 적용하고 있습니다."
        )
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_정책_변경_문장은_변경_후_모형이다(self) -> None:
        """R3: "과거에 원가모형이었으나 …으로 변경하기로 선택하였습니다"는 변경 후 모형이 맞다."""
        text = (
            "연결기업은 과거에 모든 투자부동산을 원가모형을 적용하여 측정하였습니다. "
            "2025년 1월 1일에 연결기업은 투자부동산의 회계처리방법을 공정가치모형으로 변경하기로 선택하였습니다."
        )
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, "FAIR_VALUE_MODEL")
        self.assertIn("공정가치모형으로 변경", j.evidence)

    def test_과거에와_변경_트리거가_같은_문장이어도_삼켜지지_않는다_V5_R1_narrowing(self) -> None:
        """재리뷰 V5-R1 narrowing 재현: "과거에" strip 이 문장 확장형이면, "과거에"와 변경 후
        모형 트리거가 같은 문장(마침표 하나) 안에 있을 때 트리거까지 통째로 지워
        UNDETERMINED 로 뒤집혔다. phrase-bounded strip 은 "과거에 …모형 적용" 구절만 지운다."""
        text = "회사는 과거에 원가모형을 적용하였으나 당기부터 공정가치모형으로 변경하였습니다."
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, "FAIR_VALUE_MODEL")
        self.assertIn("공정가치모형으로 변경", j.evidence)

    def test_변경한_사항_없다는_부정형은_긍정이_아니다_재리뷰_Important_2(self) -> None:
        """재리뷰 Important #2 재현: "…변경한 사항은 없습니다"는 부정형인데 단정 어미 가드가
        없으면 그대로 매치돼 FAIR_VALUE_MODEL 로 확정됐다(목록 맨 앞이라 최우선)."""
        text = "당사는 투자부동산에 대하여 원가모형에서 공정가치모형으로 변경한 사항은 없습니다."
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_변경하는_경우_조건형은_긍정이_아니다_재리뷰_Important_2(self) -> None:
        """재리뷰 Important #2 재현: "…변경하는 경우"는 조건형이라 회사의 확정된 선택이 아니다."""
        text = "회사가 공정가치모형으로 변경하는 경우 이를 소급 적용합니다."
        j = judge(self.spec, _blk(text, None, "투자부동산"))
        self.assertNotEqual(j.value, "FAIR_VALUE_MODEL")


class EvidenceSentenceTests(unittest.TestCase):
    """R1: 400자 넘는(문장 경계 없는 표) 텍스트에서도 pos 를 품은 구절이 살아남는다."""

    def test_400자_이하_문장은_기존과_동일(self) -> None:
        text = "앞 문장입니다. 당사는 토지에 대해 재평가모형을 적용하고 있습니다. 뒤 문장입니다."
        pos = text.index("재평가모형")
        result = evidence_sentence(text, pos)
        self.assertEqual(result, "당사는 토지에 대해 재평가모형을 적용하고 있습니다.")

    def test_문장_경계_없는_표_뒤_구절도_400자_창_안에_포함된다(self) -> None:
        """pos 가 긴 문장의 끝 부근(hi=end 로 잘려 lo 를 당기는 분기)."""
        table = "1,234,567 " * 130  # 문장 경계(다./니다./음.) 없는 표 나열 — 1,300자
        phrase = "재평가모형을 적용하고 있습니다."
        text = table + phrase
        self.assertGreater(len(text), 400)
        pos = text.index(phrase)
        result = evidence_sentence(text, pos)
        self.assertIn("재평가모형을 적용", result)
        self.assertLessEqual(len(result), 400)

    def test_pos가_문장_한가운데면_앞뒤_400자_창을_그대로_받는다(self) -> None:
        """Q-M3: 양끝 다 안 닿는 순수 한가운데 분기(lo=pos-150, hi=pos+250)를 지나는 테스트가
        없었다 — 위치까지 정확히 확인한다(assertIn 한 글자짜리 단언은 표 텍스트가 우연히 그
        글자를 담기만 해도 통과해 무의미하므로 쓰지 않는다)."""
        before = "9,999,999 " * 60  # 문장 경계 없는 표 — 600자, pos-150 이 여유 있게 앞에 남는다
        phrase = "재평가모형을 적용하고 있습니다"
        after = "8,888,888 " * 60  # 뒤에도 600자 — hi 가 end 에 닿지 않는다
        text = before + phrase + after
        pos = text.index(phrase)
        result = evidence_sentence(text, pos)
        lo = pos - 150
        self.assertEqual(len(result), 400)
        self.assertEqual(result, text[lo : lo + 400].strip())
        self.assertIn("재평가모형을 적용", result)

    def test_표_조각_접두어는_마지막_항목_기호부터_시작한다_V6_R4(self) -> None:
        """V6 R4: 문장 경계가 없는 표 조각("(단위: 천원) 구분 …") 뒤에 실제 항목("(4) 연결회사는
        …")이 이어지면, 문장 시작(start)과 pos 사이의 마지막 항목 기호("(4)")부터 근거가
        시작해야 한다 — 표 숫자열이 근거 앞에 그대로 남아 있으면 안 된다."""
        text = (
            "(단위: 천원) 구분 당기말 전기말 1년 이내 12,338,413 12,185,353 "
            "(4) 연결회사는 투자부동산에 대해 원가모형을 적용하고 있으며, "
            "당기말 투자부동산의 공정가치는 207,195,626천원입니다."
        )
        pos = text.index("원가모형을 적용")
        result = evidence_sentence(text, pos)
        self.assertTrue(result.startswith("(4) 연결회사는"))


class NegativeControlTests(unittest.TestCase):
    """재리뷰 Important #4(I4): V6 R1~R3 신규 패턴은 전부 "발화하는가"만 확인하는 긍정
    테스트였다 — 각 패턴마다 "발화하면 안 되는 근접 문구" 하나씩을 짝지어 고정한다. 일부는
    이번 라운드에서 실제 오탐으로 확인돼 가드/좁히기를 같이 했다(재리뷰 Minor #1, #3)."""

    ppe = TOPICS["PPE_MEASUREMENT"]
    rev = TOPICS["REVENUE_METHOD"]

    # --- R1 ---------------------------------------------------------------
    def test_쉼표_REVAL_뒤에_바로_부정이_오면_발화하지_않는다_재리뷰_Minor_1(self) -> None:
        """재리뷰 Minor #1 재현: 가드 없는 "재평가모형을," 쉼표는 바로 뒤가 부정("적용하지
        않고")이어도 매치돼 COST_MODEL 단독 회사를 MIXED 로 잘못 승격시켰다."""
        text = "당사는 유형자산 중 재평가모형을, 적용하지 않고 원가모형을 적용하고 있습니다."
        j = judge(self.ppe, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_원가모형_토큰_없이_평가_동사만_있으면_발화하지_않는다(self) -> None:
        """R1 의 COST 확장 동사(평가/측정)는 "원가모형" 토큰 없이 "유형자산"·"평가"만 근처에
        있어도 발화하면 안 된다 — 패턴은 "원가모형" 리터럴을 요구한다."""
        text = "유형자산의 손상 여부를 매 보고기간말에 평가하고 있습니다."
        j = judge(self.ppe, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_재평가모형으로_변경하는_경우_조건형은_발화하지_않는다(self) -> None:
        """R1 의 정책변경 REVAL 패턴은 단정 어미(하였|했|하기로)를 요구한다 — "변경하는 경우"
        조건형은 회사의 확정된 선택이 아니므로 발화하면 안 된다."""
        text = "회사가 향후 재평가모형으로 변경하는 경우 그 영향을 공시할 예정입니다."
        j = judge(self.ppe, _blk(text, None, "유형자산"))
        self.assertNotEqual(j.value, "REVALUATION_MODEL")

    # --- R2(가정형·손상정책 strip 의 부정 대조) -----------------------------
    def test_원가모형을_적용하여_측정하고_있습니다는_진짜_진술이라_strip되지_않는다(self) -> None:
        """R2 확장 strip("적용하여 측정하였을/하는 경우" 등 가정형 어미)이 진짜 현재형 진술
        "적용하여 측정하고 있습니다"(가정형 어미 없음)까지 지우면 안 된다."""
        text = "(5) 유형자산 당사는 원가모형을 적용하여 측정하고 있습니다."
        j = judge(self.ppe, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "COST_MODEL")

    def test_적용하는_자산은_토지이며_뒤에_재평가_실시가_이어지면_strip되지_않는다_재리뷰_Minor_3(
        self,
    ) -> None:
        """재리뷰 Minor #3 재현: "재평가모형을 적용하는 자산은 토지이며, 매 보고기간말
        재평가를 실시합니다"는 진짜 REVAL 진술("자산은"으로 이어짐)인데, 가드 없는 strip 이
        "적용하는 자산으로서"(손상정책 문구)와 구분하지 못하고 지워 UNDETERMINED 로
        뒤집었다 — "자산으로서" 표면형일 때만 지우도록 좁힌다."""
        text = "당사가 재평가모형을 적용하는 자산은 토지이며, 매 보고기간말 재평가를 실시합니다."
        j = judge(self.ppe, _blk(text, None, "유형자산"))
        self.assertEqual(j.value, "REVALUATION_MODEL")

    # --- R3 -----------------------------------------------------------------
    def test_진행률_언급만_있고_트리거_동사가_없으면_발화하지_않는다(self) -> None:
        text = "(12) 수익인식 진행률 산정 방식은 매년 재검토합니다."
        j = judge(self.rev, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_통제가_이전되는_뒤에_시점이_없으면_발화하지_않는다(self) -> None:
        """R3 의 "통제가 (고객에게)? 이전되는 시점" 은 "시점" 토큰을 요구한다 — "이전되는 방식"
        처럼 다른 명사가 오면 발화하면 안 된다."""
        text = "재화에 대한 통제가 이전되는 방식은 계약별로 다릅니다."
        j = judge(self.rev, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)

    def test_인도_일정_검토는_인도_시점에_수익_인식이_아니다(self) -> None:
        """R3 의 "(인도|이전) 시점에 수익(을|으로)? 인식" 은 "인도" 뒤에 곧바로 "시점에 수익
        …인식" 이 이어져야 한다 — "인도 일정에 따라 향후 시점을 검토" 처럼 수익 인식 서술이
        없으면 발화하면 안 된다."""
        text = "(12) 수익인식 제품 인도 일정에 따라 향후 시점을 검토하고 있습니다."
        j = judge(self.rev, _blk(text, None, "수익인식"))
        self.assertEqual(j.value, UNDETERMINED)


if __name__ == "__main__":
    unittest.main()
