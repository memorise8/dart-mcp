import re
import unittest

from dart_search_mcp.topic_judge.prompt import render_prompt
from dart_search_mcp.topic_judge.topics import TOPIC_DEFS, TopicDef

EXPECTED = {
    "CONVERSION_OPTION_CLASS": ("EQUITY", "LIABILITY", "UNDETERMINED"),
    "EMBEDDED_DERIVATIVE_SEPARATION": ("SEPARATED", "NOT_SEPARATED", "NOT_APPLICABLE", "UNDETERMINED"),
    "BUSINESS_COMBINATION_COST_CLASS": ("OPERATING", "NON_OPERATING", "UNDETERMINED"),
    "PPA_MEASUREMENT_PERIOD": ("PROVISIONAL", "FINAL", "NO_BUSINESS_COMBINATION", "UNDETERMINED"),
    "IP_FAIR_VALUE": ("FV_DIFFERS", "FV_EQUALS", "FV_NOT_DISCLOSED", "UNDETERMINED"),
    "LEASE_EXEMPTION": ("SHORT_TERM", "LOW_VALUE", "BOTH", "NONE", "UNDETERMINED"),
    "FVOCI_DESIGNATION": ("DESIGNATED", "NOT_DESIGNATED", "UNDETERMINED"),
    "DEV_COST_CAPITALIZATION": ("CAPITALIZED", "EXPENSED", "UNDETERMINED"),
}

# 스파이크(adjudication.md·common.py)에서 가져온 주제별 대표 문장.
SAMPLES = {
    "CONVERSION_OPTION_CLASS": "(주4) 전환상환우선주, 전환권 및 상환권 금융부채 인식",
    "EMBEDDED_DERIVATIVE_SEPARATION": "상환전환우선주의 내재파생상품 … 회계처리를 하지 않음",
    "BUSINESS_COMBINATION_COST_CLASS": "취득관련원가는 발생시 당기 비용으로 인식합니다.",
    "PPA_MEASUREMENT_PERIOD": "측정기간은 취득일부터 1년을 초과할 수 없습니다.",
    "IP_FAIR_VALUE": "투자부동산의 공정가치는 독립된 평가인이 수행한 평가에 기초하여 결정합니다.",
    "LEASE_EXEMPTION": "단기리스와 소액자산 리스의 경우 예외규정을 선택하여 리스기간에 걸쳐 비용으로 인식합니다.",
    "FVOCI_DESIGNATION": "단기매매항목이 아닌 지분상품에 대한 투자는 최초 인식시점에 기타포괄손익-공정가치 측정 항목으로 지정하는 취소불가능한 선택을 할 수 있습니다.",
    "DEV_COST_CAPITALIZATION": "자본화된 개발비는 추정내용연수 5년 동안 정액법으로 상각합니다.",
}


class TopicDefsTests(unittest.TestCase):
    def test_코드와_순서와_값목록이_정확하다(self) -> None:
        self.assertEqual(list(TOPIC_DEFS), list(EXPECTED))
        for code, values in EXPECTED.items():
            d = TOPIC_DEFS[code]
            self.assertIsInstance(d, TopicDef)
            self.assertEqual(d.code, code)
            self.assertEqual(d.values, values)
            self.assertEqual(set(d.value_help), set(values))
        self.assertNotIn("IP_FAIR_VALUE_DISCLOSED", TOPIC_DEFS)

    def test_모든_주제에_UNDETERMINED가_있다(self) -> None:
        for d in TOPIC_DEFS.values():
            self.assertIn("UNDETERMINED", d.values)

    def test_정의는_고정이고_정규식을_가진다(self) -> None:
        for d in TOPIC_DEFS.values():
            self.assertTrue(d.title)
            self.assertGreaterEqual(len(d.traps), 2)
            self.assertIsInstance(d.keyword_re, re.Pattern)
            self.assertGreaterEqual(len(d.priority_res), 1)
            for p in d.priority_res:
                self.assertIsInstance(p, re.Pattern)
        with self.assertRaises(Exception):
            TOPIC_DEFS["IP_FAIR_VALUE"].title = "x"  # type: ignore[misc]

    def test_우선위치_이름과_불변(self) -> None:
        for d in TOPIC_DEFS.values():
            self.assertEqual(len(d.priority_names), len(d.priority_res))
            self.assertTrue(all(d.priority_names))
            hash(d)
            with self.assertRaises(TypeError):
                d.value_help["UNDETERMINED"] = "x"  # type: ignore[index]

    def test_키워드가_대표문장에_맞는다(self) -> None:
        for code, sentence in SAMPLES.items():
            self.assertIsNotNone(TOPIC_DEFS[code].keyword_re.search(sentence), code)

    def test_우선위치_정규식_순서(self) -> None:
        conv = TOPIC_DEFS["CONVERSION_OPTION_CLASS"].priority_res
        self.assertEqual(len(conv), 3)
        self.assertTrue(conv[0].search("자본 항목 전환권대가 1,112,489,070"))
        self.assertTrue(conv[0].search("파생상품부채로 인식하였습니다"))
        self.assertTrue(conv[1].search("복합금융상품"))
        self.assertTrue(conv[2].search("19. 사채"))
        self.assertIsNone(conv[2].search("회사채 투자"))
        emb = TOPIC_DEFS["EMBEDDED_DERIVATIVE_SEPARATION"].priority_res
        self.assertTrue(emb[0].search("상환전환우선주") and emb[0].search("신주인수권부사채"))
        self.assertIsNone(emb[0].search("회사채"))
        self.assertTrue(emb[1].search("내재파생상품"))
        bcc = TOPIC_DEFS["BUSINESS_COMBINATION_COST_CLASS"].priority_res
        self.assertTrue(bcc[0].search("회계정책 … 31. 사업결합 당기 중"))
        self.assertIsNone(bcc[0].search("사업결합과 관련하여 발생한 원가"))
        self.assertTrue(bcc[1].search("취득관련원가"))
        ppa = TOPIC_DEFS["PPA_MEASUREMENT_PERIOD"].priority_res
        self.assertTrue(ppa[0].search("주석 … 34. 사업결합 (1) 당기 중"))
        self.assertIsNone(ppa[0].search("2.5 사업결합 회계정책"))
        self.assertTrue(ppa[1].search("사업결합"))
        ip = TOPIC_DEFS["IP_FAIR_VALUE"].priority_res
        self.assertIsNone(ip[0].search("투자부동산의 공정가치는 독립된 평가인이 수행한 평가에 기초하여 결정합니다."))
        self.assertTrue(ip[0].search("당기말 투자부동산의 공정가치는 889,079천원(전기말 : 870,367천원)입니다."))
        self.assertTrue(ip[1].search("투자부동산의 장부금액과 공정가치는 다음과 같습니다"))
        lease = TOPIC_DEFS["LEASE_EXEMPTION"].priority_res
        self.assertTrue(lease[0].search("(2) 리스이용자"))
        self.assertTrue(lease[1].search("소액 기초자산"))
        fv = TOPIC_DEFS["FVOCI_DESIGNATION"].priority_res
        for head in ("(3) 금융자산", "2.4 금융자산", "2.4. 금융자산", "3. 금융자산", "가. 비파생금융자산"):
            self.assertTrue(fv[0].search("앞 " + head), head)
        for not_head in (" 3 금융자산", "각. 금융자산", "회사. 금융자산"):
            self.assertIsNone(fv[0].search(not_head), not_head)
        self.assertIsNone(fv[0].search("당기손익-공정가치 측정 금융자산"))
        self.assertTrue(fv[1].search("기타포괄손익-공정가치") and fv[1].search("기타포괄손익공정가치"))
        dev = TOPIC_DEFS["DEV_COST_CAPITALIZATION"].priority_res
        self.assertTrue(dev[0].search("개발비 1,234,567") and dev[0].search("개발비 12"))
        self.assertIsNone(dev[0].search("개발비는 5년"))
        self.assertTrue(dev[1].search("연구 및 개발") and dev[1].search("연구개발"))


class RenderPromptTests(unittest.TestCase):
    def test_회사명_값코드_창이_들어간다(self) -> None:
        window = "당사는 단기리스와 소액자산 리스의 경우 예외규정을 선택하여 비용 인식합니다."
        for d in TOPIC_DEFS.values():
            p = render_prompt(d, corp_name="동진홀딩스", window=window)
            self.assertIn("[회사] 동진홀딩스", p)
            self.assertIn(f"[주제] {d.code} — {d.title}", p)
            for v in d.values:
                self.assertIn(f"- {v}: {d.value_help[v]}", p)
            for t in d.traps:
                self.assertIn(t, p)
            self.assertIn("<<<\n" + window + "\n>>>", p)
            self.assertIn('{"value": "<선택지 중 하나>"', p)
            self.assertIn("5. \"아직 시행일이 도래하지 않은", p)

    def test_중괄호가_든_창도_안전하다(self) -> None:
        d = TOPIC_DEFS["IP_FAIR_VALUE"]
        p = render_prompt(d, corp_name="{x}", window="{corp_name} {}")
        self.assertIn("[회사] {x}", p)
        self.assertIn("<<<\n{corp_name} {}\n>>>", p)


if __name__ == "__main__":
    unittest.main()
