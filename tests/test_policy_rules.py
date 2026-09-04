import unittest
from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED, judge, positive_pattern_matches


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


if __name__ == "__main__":
    unittest.main()
