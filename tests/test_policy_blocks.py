import unittest
from dart_search_mcp.policy_blocks import BLOCK_CAP, split_policy_blocks


class SplitPolicyBlocksTests(unittest.TestCase):
    def test_기준서_헤더마다_블록이_갈린다(self) -> None:
        text = ("(3) 기업회계기준서 제1109호 '금융상품' 개정 위험회피회계 요건을 변경합니다. "
                "조기적용이 허용됨에 따라 당사는 동 기준서를 조기적용하였습니다. "
                "(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 기업회계기준서 제1118호는 "
                "제1001호 '재무제표 표시'를 대체하며 2027년 1월 1일 이후 적용됩니다.")
        blocks = split_policy_blocks(text)
        self.assertEqual([b.standard_no for b in blocks], ["1109", "1118"])
        self.assertIn("조기적용하였습니다", blocks[0].text)
        self.assertNotIn("조기적용하였습니다", blocks[1].text)

    def test_본문_안의_번호는_헤더가_아니다(self) -> None:
        text = ("(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 기업회계기준서 제1118호는 "
                "제1001호 '재무제표 표시'를 대체하며 및 기업회계기준서 제1007호 '현금흐름표' 개정을 수반합니다. "
                "회사는 제1118호를 아직 채택하지 않았습니다.")
        blocks = split_policy_blocks(text)
        self.assertEqual([b.standard_no for b in blocks], ["1118"])
        self.assertIn("아직 채택하지 않았습니다", blocks[0].text)

    def test_주제_표제도_블록이다(self) -> None:
        text = ("(5) 유형자산 당사는 토지에 대해 재평가모형을 적용하고 있습니다. "
                "(6) 투자부동산 당사는 투자부동산을 원가모형으로 측정합니다.")
        blocks = split_policy_blocks(text)
        self.assertEqual([b.topic_heading for b in blocks], ["유형자산", "투자부동산"])
        self.assertNotIn("원가모형", blocks[0].text)

    def test_상한을_넘으면_capped(self) -> None:
        text = "(1) 기업회계기준서 제1118호 제정 " + "가나다라마바사 " * 1000
        blocks = split_policy_blocks(text)
        self.assertTrue(blocks[0].capped)
        self.assertLessEqual(len(blocks[0].text), BLOCK_CAP)

    def test_헤더가_없으면_빈_목록(self) -> None:
        self.assertEqual(split_policy_blocks("특별한 내용이 없습니다."), [])

    def test_굽은따옴표_제목도_헤더다(self) -> None:
        text = '회사는 새로운 기준서를 검토하였다  기업회계기준서 제1118호 ‘재무제표 표시와 이연법인세 및 기타포괄손익 공시에 관한 종합기준서’는 2027년부터 적용된다.'
        blocks = split_policy_blocks(text)
        self.assertEqual([b.standard_no for b in blocks], ["1118"])

    def test_곧은따옴표_제목도_헤더다(self) -> None:
        text = "회사는 새로운 기준서를 검토하였다  기업회계기준서 제1118호 '재무제표 표시와 이연법인세 및 기타포괄손익 공시에 관한 종합기준서'는 2027년부터 적용된다."
        blocks = split_policy_blocks(text)
        self.assertEqual([b.standard_no for b in blocks], ["1118"])

    def test_일반_항목_기호에서_주제_블록이_끝난다(self) -> None:
        text = ("(5) 유형자산 당사는 토지에 대해 재평가모형을 적용하고 있습니다. "
                "(6) 무형자산 무형자산은 취득원가로 측정합니다. "
                "(7) 투자부동산 당사는 투자부동산을 원가모형으로 측정합니다.")
        blocks = split_policy_blocks(text)
        ppe = next(b for b in blocks if b.topic_heading == "유형자산")
        self.assertNotIn("무형자산", ppe.text)
        self.assertNotIn("원가모형", ppe.text)
        self.assertFalse(ppe.capped)

    def test_기준서_블록도_다음_항목_기호에서_끝난다(self) -> None:
        text = ("(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 2027년부터 적용됩니다. "
                "(5) 유형자산 당사는 원가모형을 적용합니다.")
        blocks = split_policy_blocks(text)
        std = next(b for b in blocks if b.standard_no == "1118")
        self.assertNotIn("원가모형", std.text)


if __name__ == "__main__":
    unittest.main()
