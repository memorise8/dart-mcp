import unittest

from dart_search_mcp.ledger import _dart_agent
from dart_search_mcp.ledger.grid import header_paths, parse_table

TWO_LEVEL = """<TABLE><THEAD>
<TR><TH ROWSPAN="2">구 분</TH><TH COLSPAN="2">당기말</TH><TH COLSPAN="2">전기말</TH></TR>
<TR><TH>장부금액</TH><TH>공정가치</TH><TH>장부금액</TH><TH>공정가치</TH></TR>
</THEAD><TBODY>
<TR><TD>토지</TD><TD>1,000</TD><TD>1,500</TD><TD>900</TD><TD>1,400</TD></TR>
<TR><TD>합 계</TD><TD>1,000</TD><TD>1,500</TD><TD>900</TD><TD>1,400</TD></TR>
</TBODY></TABLE>"""

FINANCE = """<TABLE><TBODY>
<TR><TE ADELIM="0" ALEVEL="1">자산총계</TE><TE ADELIM="1">5,000</TE><TE ADELIM="2"></TE><TE ADELIM="3">4,000</TE><TE ADELIM="4"></TE></TR>
</TBODY></TABLE>"""


class GridTest(unittest.TestCase):
    def test_rowspan_colspan_expanded(self):
        g = parse_table(TWO_LEVEL)
        self.assertEqual(g.n_cols, 5)
        self.assertEqual(g.header_rows, 2)
        self.assertEqual([c.text for c in g.rows[2]], ["토지", "1,000", "1,500", "900", "1,400"])
        self.assertEqual(g.rows[1][0].origin, (0, 0))  # ROWSPAN 칸이 아래 행에 펼쳐짐

    def test_header_paths(self):
        self.assertEqual(
            header_paths(parse_table(TWO_LEVEL)),
            [["구 분"], ["당기말", "장부금액"], ["당기말", "공정가치"], ["전기말", "장부금액"], ["전기말", "공정가치"]],
        )

    def test_finance_format(self):
        g = parse_table(FINANCE)
        self.assertEqual(g.fmt, "FINANCE")
        self.assertEqual([c.text for c in g.rows[0]][:4], ["자산총계", "5,000", "", "4,000"])

    def test_header_rows_without_thead(self):
        # THEAD 없이 첫 행이 「구분/당기/전기」 글자만이면 머리글로 본다(숫자 칸이 없는 행).
        xml = "<TABLE><TBODY><TR><TD>구분</TD><TD>당기</TD><TD>전기</TD></TR><TR><TD>A</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>"
        self.assertEqual(parse_table(xml).header_rows, 1)

    def test_ragged_returns_none(self):
        self.assertIsNone(parse_table('<TABLE><TBODY><TR><TD COLSPAN="0">x</TD></TR></TBODY></TABLE>'))

    def test_short_row_returns_none(self):
        xml = "<TABLE><TBODY><TR><TD>a</TD><TD>b</TD></TR><TR><TD>c</TD></TR></TBODY></TABLE>"
        self.assertIsNone(parse_table(xml))

    def test_dart_agent_adapter(self):
        self.assertEqual(_dart_agent.fs_table().clean_text("<P>a  b</P>"), "a b")
        self.assertTrue(hasattr(_dart_agent.note_heading(), "__name__"))
        self.assertTrue(hasattr(_dart_agent.note_section(), "__name__"))


if __name__ == "__main__":
    unittest.main()
