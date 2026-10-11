import unittest

from dart_search_mcp.ledger.grid import parse_table
from dart_search_mcp.ledger.periods import classify_columns
from dart_search_mcp.ledger.units import Unit, column_units, table_unit, value_krw
from tests.test_ledger_grid import FINANCE, TWO_LEVEL


class PeriodTest(unittest.TestCase):
    def test_two_level_header_period(self):   # Review Focus 1
        cols = classify_columns(parse_table(TWO_LEVEL), "2025-12-31", "")
        self.assertEqual([(c.role, c.period) for c in cols],
            [("label", "unknown"), ("value", "current_end"), ("value", "current_end"),
             ("value", "prior_end"), ("value", "prior_end")])
        self.assertEqual(cols[2].header_path, ["당기말", "공정가치"])

    def test_numbered_terms(self):  # 「제 12(당)기말」·「제 11(전)기말」
        g = parse_table("<TABLE><THEAD><TR><TH>과목</TH><TH>제 12(당)기말</TH><TH>제 11(전)기말</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:], ["current_end", "prior_end"])

    def test_dates_against_period_end(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>2025.12.31</TH><TH>2024.12.31</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
        cols = classify_columns(g, "2025-12-31", "")
        self.assertEqual([(c.period, c.date) for c in cols][1:], [("current_end", "2025-12-31"), ("prior_end", "2024-12-31")])

    def test_flow_and_opening(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>기초</TH><TH>증가</TH><TH>기말</TH></TR></THEAD><TBODY><TR><TD>개발비</TD><TD>1</TD><TD>2</TD><TD>3</TD></TR></TBODY></TABLE>")
        cols = classify_columns(g, "2025-12-31", "(2) 당기 중 무형자산의 변동내역은 다음과 같습니다.")
        self.assertEqual([c.period for c in cols][1:], ["opening", "current_period", "current_end"])

    def test_no_period_words_uses_context(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>금액</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD></TR></TBODY></TABLE>")
        self.assertEqual(classify_columns(g, None, "당기말 현재 전환사채의 내역은 다음과 같습니다.")[1].period, "current_end")
        self.assertEqual(classify_columns(g, None, "")[1].period, "unknown")

    def test_finance_grid_uses_statement_header_context(self):
        g = parse_table(FINANCE)
        cols = classify_columns(g, None, "제 12(당) 기  제 11(전) 기")
        self.assertEqual([(c.role, c.period) for c in cols],
            [("label", "unknown"), ("value", "current_end"), ("label", "unknown"),
             ("value", "prior_end"), ("label", "unknown")])
        cols = classify_columns(g, None, "제 11(전) 기  제 12(당) 기")  # 순서가 뒤바뀐 머리글
        self.assertEqual([c.period for c in cols if c.role == "value"], ["prior_end", "current_end"])
        cols = classify_columns(g, None, "")
        self.assertEqual([c.period for c in cols if c.role == "value"], ["unknown", "unknown"])

    def test_theadless_term_header_row_is_header(self):  # T1 회귀: 「제 5 기」는 금액이 아니다
        g = parse_table("<TABLE><TBODY><TR><TD>구분</TD><TD>제 5 기</TD><TD>제 4 기</TD></TR><TR><TD>a</TD><TD>1,000</TD><TD>2,000</TD></TR></TBODY></TABLE>")
        self.assertEqual(g.header_rows, 1)

    def _one(self, head, context=""):
        g = parse_table(f"<TABLE><THEAD><TR><TH>구분</TH><TH>{head}</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD></TR></TBODY></TABLE>")
        return classify_columns(g, "2025-12-31", context)[1].period

    def test_non_period_headers_stay_unknown(self):
        for head in ("당기손익-공정가치측정금융자산", "당기순이익", "기초자산", "전기요금"):
            self.assertEqual(self._one(head), "unknown", head)
        self.assertEqual(self._one("상각후원가", "당기 중 변동"), "unknown")

    def test_parent_child_combine(self):
        g = parse_table("<TABLE><THEAD><TR><TH ROWSPAN=\"2\">구분</TH><TH COLSPAN=\"2\">당기</TH><TH COLSPAN=\"2\">전기</TH></TR>"
                        "<TR><TH>기초</TH><TH>기말</TH><TH>기초</TH><TH>기말</TH></TR></THEAD>"
                        "<TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD><TD>3</TD><TD>4</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:],
                         ["opening", "current_end", "opening", "prior_end"])


class UnitTest(unittest.TestCase):
    def test_krw_variants(self):
        for raw, m in [("(단위: 원)", 1), ("(단위 : 천원)", 1000), ("(단위:백만원)", 1_000_000), ("(단위: 억원)", 100_000_000)]:
            u, found = table_unit(raw, "", None)
            self.assertEqual((u.kind, u.multiplier, found), ("krw", m, True))

    def test_foreign(self):
        u, _ = table_unit("(단위: USD, 천원)", "", None)
        self.assertEqual((u.kind, u.multiplier), ("krw", 1000))
        u2, _ = table_unit("(단위: 천USD)", "", None)
        self.assertEqual((u2.kind, u2.currency, u2.multiplier), ("foreign", "USD", 1000))

    def test_shares_percent(self):
        self.assertEqual(table_unit("(단위: 주)", "", None)[0].kind, "shares")
        self.assertEqual(table_unit("(단위: %)", "", None)[0].kind, "percent")
        self.assertEqual(table_unit("(단위: 명)", "", None)[0].kind, "count")

    def test_mixed_unit_columns(self):   # Review Focus 2
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>주식수(주)</TH><TH>금액(천원)</TH></TR></THEAD><TBODY><TR><TD>보통주</TD><TD>1,000</TD><TD>5,000</TD></TR></TBODY></TABLE>")
        cols = classify_columns(g, None, "")
        cu = column_units(cols, Unit("(단위: 원)", "krw", 1, "KRW"))
        self.assertEqual((cu[1].kind, cu[2].kind, cu[2].multiplier), ("shares", "krw", 1000))
        self.assertIsNone(value_krw(1000, cu[1]))
        self.assertEqual(value_krw(5000, cu[2]), 5_000_000)

    def test_column_foreign_and_percent(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>금액(USD)</TH><TH>비율(%)</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
        cu = column_units(classify_columns(g, None, ""), Unit("(단위: 천원)", "krw", 1000, "KRW"))
        self.assertEqual((cu[1].kind, cu[1].currency), ("foreign", "USD"))
        self.assertEqual(cu[2].kind, "percent")
        self.assertEqual(cu[0].kind, "krw")

    def test_corporate_marker_not_shares(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>삼성전자(주)</TH><TH>발행주식수(주)</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>5</TD><TD>7</TD></TR></TBODY></TABLE>")
        cu = column_units(classify_columns(g, None, ""), Unit("(단위: 천원)", "krw", 1000, "KRW"))
        self.assertEqual((cu[1].kind, cu[1].multiplier), ("krw", 1000))
        self.assertEqual(value_krw(5, cu[1]), 5000)
        self.assertEqual(cu[2].kind, "shares")

    def test_missing_uses_default(self):
        u, found = table_unit("", "", Unit("(단위: 천원)", "krw", 1000, "KRW"))
        self.assertEqual((u.multiplier, found), (1000, False))
        self.assertEqual(table_unit("", "", None)[0].kind, "unknown")


if __name__ == "__main__":
    unittest.main()
