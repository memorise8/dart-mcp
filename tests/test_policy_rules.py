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


if __name__ == "__main__":
    unittest.main()
