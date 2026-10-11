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

    def test_numbered_terms_with_gi_before_marker(self):  # 본문 「제49기(당) 기말」「제24(당)기 기말」
        g = parse_table("<TABLE><THEAD><TR><TH>과목</TH><TH>제49기(당) 기말</TH><TH>제48기(전) 기말</TH><TH>제24(당)기 기말</TH></TR></THEAD>"
                        "<TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD><TD>3</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:], ["current_end", "prior_end", "current_end"])

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

    def test_finance_thead_colspan_groups(self):  # T4: 「제 26(당) 기」가 두 칸(소계·합계)을 덮는 FINANCE 본표
        g = parse_table(
            '<TABLE ACLASS="FINANCE"><THEAD><TR><TH>과 목</TH><TH COLSPAN="2">제 26(당) 기</TH><TH COLSPAN="2">제 25(전) 기</TH></TR></THEAD>'
            '<TBODY><TR><TE ALEVEL="1">현금</TE><TE>10</TE><TE></TE><TE>8</TE><TE></TE></TR>'
            '<TR><TE ALEVEL="0">자산총계</TE><TE></TE><TE>30</TE><TE></TE><TE>20</TE></TR></TBODY></TABLE>')
        cols = classify_columns(g, None, "제 26(당) 기 제 25(전) 기")
        self.assertEqual([c.period for c in cols][1:], ["current_end", "current_end", "prior_end", "prior_end"])

    def test_all_dash_column_is_value(self):  # T4: 잔액 없는 열(전부 「-」)도 값 열 — 「0」이라는 사실이다
        g = parse_table('<TABLE><THEAD><TR><TH>구분</TH><TH>당기말</TH><TH>전기말</TH></TR></THEAD>'
                        '<TBODY><TR><TD>FVOCI</TD><TD>-</TD><TD>1,000</TD></TR><TR><TD>합계</TD><TD>-</TD><TD>1,000</TD></TR></TBODY></TABLE>')
        cols = classify_columns(g, None, "")
        self.assertEqual([(c.role, c.period) for c in cols][1:], [("value", "current_end"), ("value", "prior_end")])

    def test_theadless_term_header_row_is_header(self):  # T1 회귀: 「제 5 기」는 금액이 아니다
        g = parse_table("<TABLE><TBODY><TR><TD>구분</TD><TD>제 5 기</TD><TD>제 4 기</TD></TR><TR><TD>a</TD><TD>1,000</TD><TD>2,000</TD></TR></TBODY></TABLE>")
        self.assertEqual(g.header_rows, 1)

    def _one(self, head, context=""):
        g = parse_table(f"<TABLE><THEAD><TR><TH>구분</TH><TH>{head}</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD></TR></TBODY></TABLE>")
        return classify_columns(g, "2025-12-31", context)[1].period

    def test_non_period_headers_stay_unknown(self):
        for head in ("당기손익-공정가치측정금융자산", "당기순이익", "기초자산", "전기요금"):
            self.assertEqual(self._one(head), "unknown", head)
        self.assertEqual(self._one("상각후원가", "변동 내역"), "unknown")
        # T5(R7): 시간이 없는 열(범주)은 문맥이 밝힌 표 기간을 받는다
        self.assertEqual(self._one("상각후원가", "당기 중 변동"), "current_period")

    def test_parent_child_combine(self):
        g = parse_table("<TABLE><THEAD><TR><TH ROWSPAN=\"2\">구분</TH><TH COLSPAN=\"2\">당기</TH><TH COLSPAN=\"2\">전기</TH></TR>"
                        "<TR><TH>기초</TH><TH>기말</TH><TH>기초</TH><TH>기말</TH></TR></THEAD>"
                        "<TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD><TD>3</TD><TD>4</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:],
                         ["opening", "current_end", "opening", "prior_end"])

    # ---- T5: 표 단위 기간 표지(문맥 끝 줄)·보고기간 문구·머리글 접미사·FINANCE 머리글 행 ----
    _ASSETS = ("<TABLE><THEAD><TR><TH>구분</TH><TH>토지</TH><TH>건물</TH></TR></THEAD>"
               "<TBODY><TR><TD>기초</TD><TD>1</TD><TD>2</TD></TR><TR><TD>취득</TD><TD>3</TD><TD>4</TD></TR></TBODY></TABLE>")

    def _ctx(self, context):
        return [c.period for c in classify_columns(parse_table(self._ASSETS), "2025-12-31", context) if c.role == "value"]

    def test_table_marker_at_context_end(self):
        cases = {
            "(당기)": "current_period", "〈전기〉": "prior_period", "[당기말]": "current_end", "<전기말>": "prior_end",
            "변동내역은 다음과 같습니다.\n- 전기": "prior_period", "나. 전기": "prior_period", "① 당기말": "current_end",
            "(1) 당기": "current_period", "재무성과는 다음과 같습니다.(당기)": "current_period",
            "(2) 당기와 전기 중 변동은 다음과 같습니다.\n<당기>": "current_period",
            "제 27(당)기": "current_period", "(당기) (단위:천원)": "current_period", "당기 :": "current_period",
        }
        for ctx, want in cases.items():
            self.assertEqual(self._ctx(ctx), [want, want], ctx)

    def test_table_marker_negatives(self):
        for ctx in ("(2) 당기와 전기 중 변동은 다음과 같습니다.", "당기순이익", "본사 설비는 전기",
                    "(당기)와 (전기) 비교", "회사는 전기\n공급 계약을 맺었습니다."):
            self.assertEqual(self._ctx(ctx), ["unknown", "unknown"], ctx)

    def test_table_marker_sets_side_for_close_columns(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>기초</TH><TH>증가</TH><TH>기말</TH></TR></THEAD>"
                        "<TBODY><TR><TD>개발비</TD><TD>1</TD><TD>2</TD><TD>3</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "(2) 무형자산 변동\n<전기>")][1:],
                         ["opening", "prior_period", "prior_end"])

    def test_reporting_date_and_term_phrases_in_context(self):
        self.assertEqual(self._ctx("보고기간종료일 현재 담보로 제공한 자산은 다음과 같습니다."), ["current_end"] * 2)
        self.assertEqual(self._ctx("보고기간말 현재 차입금 내역"), ["current_end"] * 2)
        self.assertEqual(self._ctx("당기 중 특수관계자와의 거래 내역은 다음과 같습니다."), ["current_period"] * 2)
        self.assertEqual(self._ctx("(2) 전기 중 무형자산의 내용은 다음과 같습니다."), ["prior_period"] * 2)
        for ctx in ("당기와 전기 중 특수관계자와의 거래", "당기 및 전기 중 변동", "보고기간종료일 현재와 전기말 현재",
                    "보고기간종료일로부터 1년 이내", "당기 중 차입하였고 전기말 현재 잔액",
                    "당기 중 리스의 총 현금유출은 519백만원입니다.\n11.자본금"):  # 새 낱말은 마지막 줄(표 제목)에서만
            self.assertEqual(self._ctx(ctx), ["unknown", "unknown"], ctx)
        self.assertEqual(self._ctx("당기말 현재 담보 내역\n(2) 차입금 상환계획"), ["current_end"] * 2)  # T2 낱말은 문맥 전체
        self.assertEqual(self._ctx("앞 문단에 당기말 현재\n(2) 보고기간종료일 현재 잔액"), ["current_end"] * 2)
        self.assertEqual(self._ctx("당기 중 차입하였습니다. 전기말 현재 잔액"), ["prior_end"] * 2)  # 마지막 문장이 제목
        self.assertEqual(self._ctx("10. 자본금 당기 및 전기 중 자본금의 변동은 없으며, 당기말 현재 자본금의 내역은 다음과 같습니다."),
                         ["current_end"] * 2)
        self.assertEqual(self._ctx("전기말 현재 잔액입니다.\n(2) 당기 중 거래 내역"), ["current_period"] * 2)  # 마지막 줄 우선
        # 마지막 줄 안에서도 마지막 문장(표 제목)이 먼저 — 앞 문장의 「당기와 전기 중 … 없습니다」에 막히지 않는다
        self.assertEqual(self._ctx("(2) 당기와 전기 중 자본금의 변동내역은 없습니다. 19. 자기주식당기말 현재 보유중인 자기주식의 내역은 다음과 같습니다."),
                         ["current_end"] * 2)
        self.assertEqual(self._ctx("당기와 전기에 인식한 이자비용은 각각 89천원입니다.(5) 제공받은 담보당기말 현재 담보내역은 다음과 같습니다."),
                         ["current_end"] * 2)

    def test_acquisition_amount_under_period_parent(self):  # 회귀: 「전기말 / 취득가액」은 전기말(취득 흐름이 아니다)
        g = parse_table("<TABLE><THEAD><TR><TH ROWSPAN=\"2\">구분</TH><TH COLSPAN=\"2\">전기말</TH></TR>"
                        "<TR><TH>취득가액</TH><TH>장부가액</TH></TR></THEAD>"
                        "<TBODY><TR><TD>a</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:], ["prior_end", "prior_end"])

    def test_header_amount_suffix_and_paren_term(self):
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>기초장부가액</TH><TH>취득</TH><TH>기말가액</TH></TR></THEAD>"
                        "<TBODY><TR><TD>토지</TD><TD>1</TD><TD>2</TD><TD>3</TD></TR></TBODY></TABLE>")
        self.assertEqual([c.period for c in classify_columns(g, None, "〈전기〉")][1:], ["opening", "prior_period", "prior_end"])
        for head, want in (("당기말 금액", "current_end"), ("전기말금액", "prior_end"), ("당기 합계", "current_period"),
                           ("전기계", "prior_period"), ("2023년(전기)", "prior_period"), ("2025년 (당기말)", "current_end")):
            self.assertEqual(self._one(head), want, head)
        for head, want in (("최소리스료(당기말)", "current_end"), ("당기말원화환산금액", "current_end"),
                           ("전기말 원화환산금액", "prior_end"), ("장부금액(전기)", "prior_period")):
            self.assertEqual(self._one(head), want, head)
        for head in ("당기손익-공정가치측정금융자산 금액", "2023년(재작성)", "당기순이익", "당기손익인식금융자산"):
            self.assertEqual(self._one(head), "unknown", head)

    def test_corner_header_marker(self):  # 레이블 열 머리글 칸이 「전기」「제27(당)기」 같은 표지
        for corner, want in (("전기", "prior_period"), ("〈당기말〉", "current_end"), ("제27(당)기", "current_period")):
            g = parse_table(f"<TABLE><THEAD><TR><TH>{corner}</TH><TH>의장권</TH><TH>소프트웨어</TH></TR></THEAD>"
                            "<TBODY><TR><TD>기초</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
            self.assertEqual([c.period for c in classify_columns(g, None, "")][1:], [want, want], corner)
        g = parse_table("<TABLE><THEAD><TR><TH>구분</TH><TH>의장권</TH></TR></THEAD><TBODY><TR><TD>a</TD><TD>1</TD></TR></TBODY></TABLE>")
        self.assertEqual(classify_columns(g, None, "")[1].period, "unknown")

    def test_enumerated_marker_at_line_end(self):
        self.assertEqual(self._ctx("17. 부가가치 당기와 전기 중 금액은 다음과 같습니다. (1) 당기"), ["current_period"] * 2)
        self.assertEqual(self._ctx("내용은 다음과 같습니다.1) 담보제공"), ["unknown"] * 2)
        self.assertEqual(self._ctx("구성내역은 다음과 같습니다.가. 당기말 (단위 : 천원)"), ["current_end"] * 2)
        self.assertEqual(self._ctx("만기는 다음과 같습니다.(1) 당기\n(단위: 천원)"), ["current_period"] * 2)
        self.assertEqual(self._ctx("14-2 전 기 (단위:천원)"), ["prior_period"] * 2)
        self.assertEqual(self._ctx("7.2 전기 (단위: 천원)"), ["prior_period"] * 2)
        self.assertEqual(self._ctx("중요한 거래는 다음과 같습니다. 당기"), ["current_period"] * 2)  # 문장 끝 뒤 맨 표지
        self.assertEqual(self._ctx("회사는 다음과 같이 전기"), ["unknown"] * 2)
        self.assertEqual(self._ctx("나. 전 기 (원화단위 : 원)"), ["prior_period"] * 2)
        self.assertEqual(self._ctx("(1) 당기 재무제표일 현재 담보로 제공된 자산"), ["current_end"] * 2)
        self.assertEqual(self._ctx("재무상태표일 현재 보험가입내역"), ["current_end"] * 2)
        self.assertEqual(self._ctx("기말 현재 금융부채별 계약상 만기"), ["current_end"] * 2)

    def _heads(self, heads, period_end="2025-12-31"):
        th = "".join(f"<TH>{h}</TH>" for h in heads)
        td = "".join(f"<TD>{i + 1}</TD>" for i in range(len(heads)))
        g = parse_table(f"<TABLE><THEAD><TR><TH>구분</TH>{th}</TR></THEAD><TBODY><TR><TD>a</TD>{td}</TR></TBODY></TABLE>")
        return [c.period for c in classify_columns(g, period_end, "")][1:]

    def test_term_number_and_year_pairs(self):
        self.assertEqual(self._heads(["제24기", "제23기"]), ["current_period", "prior_period"])
        self.assertEqual(self._heads(["제 23 기말", "제 24 기말"]), ["prior_end", "current_end"])
        self.assertEqual(self._heads(["2025년", "2024년"]), ["current_period", "prior_period"])
        self.assertEqual(self._heads(["2025년말", "2024년말"]), ["current_end", "prior_end"])
        self.assertEqual(self._heads(["2025", "2024"]), ["current_period", "prior_period"])
        # 짝이 아니면(상환 연도·기수 하나·기수 차가 1이 아님·보고기간을 모름) 정하지 않는다
        self.assertEqual(self._heads(["2026년", "2027년", "2028년 이후"]), ["unknown"] * 3)
        self.assertEqual(self._heads(["제24기"]), ["unknown"])
        self.assertEqual(self._heads(["제24기", "제21기"]), ["unknown"] * 2)
        self.assertEqual(self._heads(["2025년", "2024년"], None), ["unknown"] * 2)
        self.assertEqual(self._heads(["2026년", "2025년"]), ["unknown"] * 2)

    def test_finance_without_thead_leading_td_rows_are_header(self):  # 감사참여자 표: 머리글은 TD, 숫자는 TE
        g = parse_table('<TABLE><TBODY><TR><TD ROWSPAN="2">구분</TD><TD COLSPAN="2">감사업무 담당 회계사</TD></TR>'
                        '<TR><TD>당기</TD><TD>전기</TD></TR>'
                        '<TR><TD>투입 인원수</TD><TE>3</TE><TE>2</TE></TR><TR><TD>투입시간</TD><TE>146</TE><TE>168</TE></TR></TBODY></TABLE>')
        self.assertEqual(g.header_rows, 2)
        self.assertEqual([c.period for c in classify_columns(g, None, "")][1:], ["current_period", "prior_period"])
        self.assertEqual(parse_table(FINANCE).header_rows, 0)  # 본문 FINANCE 표(TE만)는 그대로


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

    def test_prefixed_and_unopened_unit_phrases(self):  # T5
        u, found = table_unit("보고기간종료일 현재 외화자산의 내역은 다음과 같습니다.(원화단위:천원)", "", None)
        self.assertEqual((u.kind, u.multiplier, found), ("krw", 1000, True))
        u, found = table_unit("〈전기〉 단위:천원)", "", None)
        self.assertEqual((u.kind, u.multiplier, found), ("krw", 1000, True))
        self.assertFalse(table_unit("단위당 원가는 다음과 같습니다", "", None)[1])

    def test_count_unit_lists(self):  # 감사참여자 표 「(단위 : 명, 시간)」
        for raw in ("(단위 : 명, 시간)", "(단위: 시간)", "(단위: 일)", "(단위: 회)"):
            u, found = table_unit(raw, "", None)
            self.assertEqual((u.kind, found), ("count", True), raw)

    def test_missing_uses_default(self):
        u, found = table_unit("", "", Unit("(단위: 천원)", "krw", 1000, "KRW"))
        self.assertEqual((u.multiplier, found), (1000, False))
        self.assertEqual(table_unit("", "", None)[0].kind, "unknown")


if __name__ == "__main__":
    unittest.main()
