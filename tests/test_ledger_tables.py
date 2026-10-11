import gzip
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.ledger.tables import (
    PARSER_VERSION,
    build_filing,
    build_filing_tables,
    load_filing,
    write_filing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ledger"


def load_fixture(name: str) -> list[tuple[str, bytes]]:
    """픽스처 한 파일 → [(원 파일명, bytes)]. 원 파일명은 DOCUMENT-NAME ACODE로 되살린다(별도·연결 구분)."""
    raw = (FIXTURES / name).read_bytes()
    m = re.search(rb'<DOCUMENT-NAME ACODE="(\d+)"', raw)
    rcept = name.split("_")[0]
    fname = f"{rcept}_{m.group(1).decode()}.xml" if m else f"{rcept}.xml"
    return [(fname, raw)]


TWO_LEVEL_TABLE = """<TABLE BORDER="1"><THEAD>
<TR><TH ROWSPAN="2">구 분</TH><TH COLSPAN="2">당기말</TH><TH COLSPAN="2">전기말</TH></TR>
<TR><TH>장부금액</TH><TH>공정가치</TH><TH>장부금액</TH><TH>공정가치</TH></TR>
</THEAD><TBODY>
<TR><TD>토지</TD><TD>1,000</TD><TD>1,500</TD><TD>900</TD><TD>1,400</TD></TR>
<TR><TD>합 계</TD><TD>1,000</TD><TD>1,500</TD><TD>900</TD><TD>1,400</TD></TR>
</TBODY></TABLE>"""

TWO_LEVEL_DOC = "<DOCUMENT><BODY><P>(단위: 원)</P>" + TWO_LEVEL_TABLE + "</BODY></DOCUMENT>"

BAD_SUM_DOC = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>당기말</TH><TH>전기말</TH></TR></THEAD><TBODY>
<TR><TD>현금</TD><TD>100</TD><TD>50</TD></TR>
<TR><TD>예금</TD><TD>200</TD><TD>60</TD></TR>
<TR><TD>합 계</TD><TD>310</TD><TD>110</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""

PAGED_DOC = """<DOCUMENT><BODY><P>앞 쪽 문단입니다.</P><PGBRK/>
<P>(2) 당기말 현재 전환사채의 내역은 다음과 같습니다.</P><P>(단위: 천원)</P>
<TABLE BORDER="1"><THEAD><TR><TH>구 분</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD>전환사채</TD><TD>1,000</TD></TR></TBODY></TABLE></BODY></DOCUMENT>"""

RAGGED_DOC = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><TBODY>
<TR><TD COLSPAN="0">구 분</TD><TD>당기말</TD></TR>
<TR><TD>현금</TD><TD>100</TD></TR></TBODY></TABLE></BODY></DOCUMENT>"""

NESTED_SUM_DOC = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD>  사채</TD><TD>100</TD></TR>
<TR><TD>  차입금</TD><TD>200</TD></TR>
<TR><TD>소  계</TD><TD>300</TD></TR>
<TR><TD>  파생상품부채</TD><TD>50</TD></TR>
<TR><TD>합  계</TD><TD>350</TD></TR>
</TBODY></TABLE><P>(*1) 첫째 각주입니다.</P><P></P><P>주1) 둘째 각주입니다.</P>
<P>본문 문단입니다.</P><P>(*2) 떨어진 각주는 담지 않습니다.</P></BODY></DOCUMENT>"""

DASH_DOC = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>당기말</TH><TH>전기말</TH></TR></THEAD><TBODY>
<TR><TD>현금</TD><TD>- </TD><TD></TD></TR>
<TR><TD>예금</TD><TD>(1,200)</TD><TD>300</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""

SECTIONS_DOC = """<DOCUMENT><BODY>
<TITLE>독립된 감사인의 감사보고서</TITLE><P>감사의견 문단입니다.</P>
<TABLE BORDER="1"><TBODY><TR><TD>구분</TD><TD>당기</TD></TR><TR><TD>보수</TD><TD>10</TD></TR></TBODY></TABLE>
<TITLE>(첨부)재 무 제 표</TITLE><P>현 금 흐 름 표</P><P>(단위: 원)</P>
<TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>당기</TH></TR></THEAD><TBODY><TR><TD>영업활동</TD><TD>10</TD></TR></TBODY></TABLE>
<TITLE>주석</TITLE>
<P>1. 일반사항</P><P>회사는 설립되었습니다. 본사는 서울에 있습니다.</P>
<P>2. 중요한 회계정책</P><P>정책 문장입니다.</P>
<P>3. 현금및현금성자산</P><P>(단위: 원)</P>
<TABLE BORDER="1"><THEAD><TR><TH>구 분</TH><TH>당기말</TH></TR></THEAD><TBODY><TR><TD>현금</TD><TD>10</TD></TR></TBODY></TABLE>
</BODY></DOCUMENT>"""


class FilingTablesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ildong_equity_component_row(self):
        # 브리프 예시는 519,263천원을 당기말로 적었으나 원문 표(연결 17. ⑤)는 「당기말 121,352 / 전기말 519,263」이다.
        tabs = build_filing_tables("20260318001076", load_fixture("20260318001076_cb.xml"), {})
        hit = [(t, r) for t in tabs for r in t["rows"] if "자본요소" in r["label"]]
        self.assertTrue(hit)
        t, r = hit[0]
        self.assertEqual(t["region"]["kind"], "note")
        self.assertEqual(t["fs_scope"], "consolidated")
        self.assertEqual(t["unit"]["multiplier"], 1000)
        by_period = {t["columns"][c["col"]]["period"]: c for c in r["cells"]}
        self.assertEqual(by_period["prior_end"]["value_krw"], 519_263_000)
        self.assertEqual(by_period["current_end"]["value_krw"], 121_352_000)
        self.assertTrue(t["table_id"].startswith("20260318001076:00761:t"))

    def test_row_kinds_and_sum_check(self):
        t = build_filing_tables("X", [("x.xml", TWO_LEVEL_DOC.encode())], {})[0]
        self.assertEqual([r["kind"] for r in t["rows"]], ["item", "total"])
        self.assertTrue(t["checks"]["sum_ok"]); self.assertEqual(t["status"], "verified")

    def test_sum_mismatch_needs_review(self):
        t = build_filing_tables("X", [("x.xml", BAD_SUM_DOC.encode())], {})[0]
        self.assertEqual(t["status"], "needs_review")
        self.assertFalse(t["checks"]["sum_ok"])

    def test_subtotal_then_total_with_indent(self):
        t = build_filing_tables("X", [("x.xml", NESTED_SUM_DOC.encode())], {})[0]
        self.assertEqual([r["kind"] for r in t["rows"]], ["item", "item", "subtotal", "item", "total"])
        self.assertTrue(t["checks"]["sum_ok"])
        self.assertEqual([r["level"] for r in t["rows"]][:3], [2, 2, 0])

    def test_sum_descends_into_blank_parent(self):
        # 값이 빈 상위 행(「이후 인식될 비용」)은 그 아래 하위 행들의 합으로 센다
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD> 누적인식액</TD><TD>100</TD></TR>
<TR><TD> 이후 인식될 비용</TD><TD></TD></TR>
<TR><TD>  1년이내</TD><TD>30</TD></TR>
<TR><TD>  1년이후</TD><TD>-</TD></TR>
<TR><TD>합 계</TD><TD>130</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertTrue(t["checks"]["sum_ok"])

    def test_rowspan_parent_label_keeps_specific_label(self):  # 리뷰 1차: ROWSPAN 상위 글이 하위 레이블을 덮지 않게
        doc = """<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>항 목</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD ROWSPAN="3">제3회 전환사채</TD><TD>액면가액</TD><TD>1,000</TD></TR>
<TR><TD>  전환권조정</TD><TD>(100)</TD></TR>
<TR><TD>장부금액</TD><TD>900</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["label"] for r in t["rows"]], ["액면가액", "전환권조정", "장부금액"])
        self.assertEqual([r["label_path"] for r in t["rows"]],
                         [["제3회 전환사채", "액면가액"], ["제3회 전환사채", "전환권조정"], ["제3회 전환사채", "장부금액"]])
        self.assertEqual([r["level"] for r in t["rows"]], [0, 2, 0])  # 들여쓰기는 고른 칸에서

    def test_flat_record_table_uses_first_label_and_skips_numbers(self):
        # ROWSPAN 없는 평평한 표: 회사명이 레이블(오른쪽 소재국·지분율 칸이 아님), 숫자 칸은 레이블 아님
        doc = """<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>회사명</TH><TH>소재국가</TH><TH>지분율</TH><TH>장부가액</TH></TR></THEAD><TBODY>
<TR><TD>에이사</TD><TD>중국</TD><TD>100%</TD><TD>159,923</TD></TR>
<TR><TD>비사</TD><TD>대한민국</TD><TD>100%</TD><TD>183,201</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["label"] for r in t["rows"]], ["에이사", "비사"])
        self.assertEqual(t["rows"][0]["label_path"], ["에이사", "중국"])

    def test_flat_table_picks_most_distinct_label_column(self):
        # 「구분」이 줄마다 같은 글로 반복되는 평평한 표: 회사명 열이 레이블
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>회사명</TH><TH>당기</TH></TR></THEAD><TBODY>
<TR><TD>종속기업</TD><TD>가사</TD><TD>10</TD></TR>
<TR><TD>종속기업</TD><TD>나사</TD><TD>20</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["label"] for r in t["rows"]], ["가사", "나사"])
        self.assertEqual(t["rows"][1]["label_path"], ["종속기업", "나사"])

    def test_single_stub_numeric_labels_kept(self):  # 리뷰 2차: 레이블 열이 하나면 숫자 레이블도 그대로
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>연 도</TH><TH>금액</TH></TR></THEAD><TBODY>
<TR><TD>2025</TD><TD>100</TD></TR>
<TR><TD>2024</TD><TD>200</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["label"] for r in t["rows"]], ["2025", "2024"])

    def test_two_stub_percent_is_fallback_only(self):
        # 두 행 머리 열 중 하나가 비율: 글 칸이 있으면 글, 비율만 있으면 비율을 레이블로
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>회사명</TH><TH>지분율</TH><TH>장부가액</TH></TR></THEAD><TBODY>
<TR><TD>에이사</TD><TD>10%</TD><TD>100</TD></TR>
<TR><TD></TD><TD>20%</TD><TD>200</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["label"] for r in t["rows"]], ["에이사", "20%"])
        self.assertEqual(t["rows"][1]["label_path"], ["20%"])

    def test_total_in_second_label_column(self):
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>지 역</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD ROWSPAN="3">매출채권</TD><TD>국내</TD><TD>100</TD></TR>
<TR><TD>해외</TD><TD>50</TD></TR>
<TR><TD>소 계</TD><TD>150</TD></TR>
<TR><TD></TD><TD>합 계</TD><TD>150</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["kind"] for r in t["rows"]], ["item", "item", "subtotal", "total"])
        self.assertTrue(t["checks"]["sum_ok"])

    def test_bad_subtotal_in_second_label_column_needs_review(self):
        doc = """<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>지 역</TH><TH>당기말</TH></TR></THEAD><TBODY>
<TR><TD ROWSPAN="3">매출채권</TD><TD>국내</TD><TD>100</TD></TR>
<TR><TD>해외</TD><TD>50</TD></TR>
<TR><TD>소 계</TD><TD>999</TD></TR>
</TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual(t["status"], "needs_review")

    def test_unit_override_ignores_raw_text(self):
        # 표 단위 「(단위: 천원)」과 열 「금액(천원)」은 같은 단위 — 덮어쓰기 아님
        doc = """<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>주식수(주)</TH><TH>금액(천원)</TH></TR></THEAD><TBODY>
<TR><TD>보통주</TD><TD>1,000</TD><TD>5,000</TD></TR></TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        shares, amount = t["rows"][0]["cells"]
        self.assertEqual(shares["unit_override"]["kind"], "shares")
        self.assertIsNone(shares["value_krw"])
        self.assertIsNone(amount["unit_override"])
        self.assertEqual(amount["value_krw"], 5_000_000)

    def test_unit_found_from_column_units(self):
        doc = """<DOCUMENT><BODY><TABLE BORDER="1"><THEAD>
<TR><TH>구 분</TH><TH>당기말(천원)</TH><TH>전기말(천원)</TH></TR></THEAD><TBODY>
<TR><TD>현금</TD><TD>10</TD><TD>20</TD></TR></TBODY></TABLE></BODY></DOCUMENT>"""
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertTrue(t["checks"]["unit_found"])
        doc2 = doc.replace("(천원)", "")
        t2 = build_filing_tables("X", [("x.xml", doc2.encode())], {})[0]
        self.assertFalse(t2["checks"]["unit_found"])

    def test_footnotes_only_consecutive(self):
        t = build_filing_tables("X", [("x.xml", NESTED_SUM_DOC.encode())], {})[0]
        self.assertEqual([f["mark"] for f in t["footnotes"]], ["(*1)", "주1)"])
        self.assertEqual(t["footnotes"][0]["text"], "첫째 각주입니다.")

    def test_footnote_attached(self):
        tabs = build_filing_tables("20251205000494", load_fixture("20251205000494_fv.xml"), {})
        fns = [f for t in tabs for f in t["footnotes"]]
        self.assertTrue(any("내재파생상품 분리금액" in f["text"] for f in fns))

    def test_page_and_title(self):
        t = build_filing_tables("X", [("x.xml", PAGED_DOC.encode())], {})[0]
        self.assertEqual(t["page"], 2)
        self.assertTrue(t["title"].startswith("(2) 당기말 현재"))
        self.assertEqual(t["unit"]["multiplier"], 1000)

    def test_title_falls_back_to_first_header_row(self):
        doc = ("<DOCUMENT><BODY><TABLE BORDER=\"1\"><TBODY><TR><TD>앞 표</TD></TR></TBODY></TABLE>"
               "<TABLE BORDER=\"1\"><THEAD><TR><TH COLSPAN=\"3\">(단위: 천원)</TH></TR>"
               "<TR><TH>재무상태표 상 부채</TH><TH>당기말</TH><TH>전기말</TH></TR></THEAD>"
               "<TBODY><TR><TD>차입금</TD><TD>10</TD><TD>20</TD></TR></TBODY></TABLE></BODY></DOCUMENT>")
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[1]
        self.assertEqual(t["title"], "재무상태표 상 부채 당기말 전기말")

    # ---- T5: 표 단위 기간 표지 ----
    def _periods(self, doc):
        return [[c["period"] for c in t["columns"] if c["role"] == "value"]
                for t in build_filing_tables("X", [("x.xml", doc.encode())], {}) if any(c["role"] == "value" for c in t["columns"])]

    _MOVE = ("<TABLE BORDER=\"1\"><THEAD><TR><TH>구분</TH><TH>산업재산권</TH><TH>소프트웨어</TH></TR></THEAD>"
             "<TBODY><TR><TD>기초</TD><TD>1</TD><TD>2</TD></TR><TR><TD>취득</TD><TD>3</TD><TD>4</TD></TR></TBODY></TABLE>")

    def test_korean_angle_marker_paragraph_kept(self):  # 원문 <P><당기></P>는 태그가 아니라 글이다
        doc = ("<DOCUMENT><BODY><P>(2) 당기와 전기 중 무형자산의 변동은 다음과 같습니다.</P><P><당기></P><P>(단위: 원)</P>"
               + self._MOVE + "<P><전기></P><P>(단위: 원)</P>" + self._MOVE + "</BODY></DOCUMENT>")
        self.assertEqual(self._periods(doc), [["current_period"] * 2, ["prior_period"] * 2])

    def test_korean_angle_marker_header_cell_kept(self):
        doc = ("<DOCUMENT><BODY><P>(단위: 원)</P><TABLE BORDER=\"1\"><THEAD><TR><TH>구분</TH><TH><당기말></TH><TH><전기말></TH></TR></THEAD>"
               "<TBODY><TR><TD>현금</TD><TD>1</TD><TD>2</TD></TR></TBODY></TABLE></BODY></DOCUMENT>")
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([c["period"] for c in t["columns"] if c["role"] == "value"], ["current_end", "prior_end"])
        self.assertEqual(t["columns"][1]["header_path"], ["〈당기말〉"])

    def test_borderless_unit_table_passes_on_preceding_context(self):
        unit = "<TABLE BORDER=\"0\"><TBODY><TR><TD>(단위: 천원)</TD></TR></TBODY></TABLE>"
        doc = "<DOCUMENT><BODY><P>(1) 당기</P>" + unit + self._MOVE + "</BODY></DOCUMENT>"
        self.assertEqual(self._periods(doc), [["current_period"] * 2])
        plan = ("<TABLE BORDER=\"1\"><THEAD><TR><TH>구분</TH><TH>2026년</TH><TH>2027년</TH></TR></THEAD>"
                "<TBODY><TR><TD>장기차입금</TD><TD>10</TD><TD>20</TD></TR></TBODY></TABLE>")
        doc = "<DOCUMENT><BODY><P>(2) 당기말 현재 장기차입금의 연도별 상환계획은 다음과 같습니다.</P>" + unit + plan + "</BODY></DOCUMENT>"
        tabs = build_filing_tables("X", [("x.xml", doc.encode())], {})
        self.assertEqual([c["period"] for c in tabs[1]["columns"] if c["role"] == "value"], ["current_end"] * 2)
        self.assertTrue(tabs[1]["title"].startswith("(2) 당기말 현재"))
        # 테두리 있는 앞 표의 문맥은 넘겨받지 않는다
        doc = "<DOCUMENT><BODY><P>(1) 당기</P>" + self._MOVE + self._MOVE + "</BODY></DOCUMENT>"
        self.assertEqual(self._periods(doc), [["current_period"] * 2, ["unknown"] * 2])

    def test_paired_table_inherits_unit_across_marker(self):  # 「(단위: 천원) 〈당기〉 표 〈전기〉 표」
        doc = ("<DOCUMENT><BODY><TITLE>주석</TITLE><P>1. 일반사항</P><P>회사는 설립되었습니다.</P>"
               "<P>5. 유형자산</P><P>(1) 변동내역은 다음과 같습니다.</P><P>(단위: 천원)</P><P>〈당기〉</P>" + self._MOVE
               + "<P>② 전기</P>" + self._MOVE + "<P>(2) 담보 내역은 다음과 같습니다.</P>" + self._MOVE + "</BODY></DOCUMENT>")
        doc = doc.replace("〈", "<").replace("〉", ">")
        tabs = build_filing_tables("X", [("x.xml", doc.encode())], {})
        self.assertEqual([t["checks"]["unit_found"] for t in tabs], [True, True, False])
        self.assertEqual(tabs[1]["unit"]["multiplier"], 1000)
        self.assertEqual(tabs[1]["rows"][0]["cells"][0]["value_krw"], 1000)

    def test_sum_within_rowspan_group(self):  # 감사참여자 표: 「투입시간」 묶음의 합계는 그 묶음 행만 더한다
        doc = ("<DOCUMENT><BODY><P>(단위 : 명, 시간)</P><TABLE BORDER=\"1\"><THEAD><TR><TH COLSPAN=\"2\">구분</TH><TH>당기</TH></TR></THEAD><TBODY>"
               "<TR><TD COLSPAN=\"2\">투입 인원수</TD><TD>6</TD></TR>"
               "<TR><TD ROWSPAN=\"3\">투입시간</TD><TD>분ㆍ반기검토</TD><TD>-</TD></TR>"
               "<TR><TD>감사</TD><TD>197</TD></TR><TR><TD>합계</TD><TD>197</TD></TR></TBODY></TABLE></BODY></DOCUMENT>")
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual(t["rows"][-1]["kind"], "total")
        self.assertTrue(t["checks"]["sum_ok"])
        bad = doc.replace("<TD>197</TD></TR><TR><TD>합계</TD><TD>197</TD>", "<TD>197</TD></TR><TR><TD>합계</TD><TD>200</TD>")
        self.assertFalse(build_filing_tables("X", [("x.xml", bad.encode())], {})[0]["checks"]["sum_ok"])

    def test_sum_uses_total_level_run(self):  # 손익계산서 「계」(ALEVEL 2) = 바로 위 같은 단계 행들
        doc = ('<DOCUMENT><BODY><P>(단위: 원)</P><TABLE ACLASS="FINANCE"><THEAD><TR><TH>과 목</TH><TH>제 5(당) 기</TH></TR></THEAD><TBODY>'
               '<TR><TE ALEVEL="0">Ⅰ.매출액</TE><TE>500</TE></TR>'
               '<TR><TE ALEVEL="1">제품매출원가</TE><TE>300</TE></TR>'
               '<TR><TE ALEVEL="2">1.기초제품재고액</TE><TE>100</TE></TR>'
               '<TR><TE ALEVEL="2">2.당기제품제조원가</TE><TE>250</TE></TR>'
               '<TR><TE ALEVEL="2">계</TE><TE>350</TE></TR>'
               '<TR><TE ALEVEL="2">3.기말제품재고액</TE><TE>(50)</TE></TR></TBODY></TABLE></BODY></DOCUMENT>')
        t = build_filing_tables("X", [("x.xml", doc.encode())], {})[0]
        self.assertEqual([r["kind"] for r in t["rows"]][4], "total")
        self.assertTrue(t["checks"]["sum_ok"])
        self.assertFalse(build_filing_tables("X", [("x.xml", doc.replace("<TE>350</TE>", "<TE>360</TE>").encode())], {})[0]["checks"]["sum_ok"])

    def test_level_run_does_not_break_blank_parent_sections(self):  # 회귀: 「〈단기금융상품〉」 빈 머리 행 사이 합계
        doc = ("<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER=\"1\"><THEAD><TR><TH>구분</TH><TH>당기말</TH></TR></THEAD><TBODY>"
               "<TR><TD>법정적립금 :</TD><TD></TD></TR><TR><TD>  이익준비금</TD><TD>135</TD></TR>"
               "<TR><TD>임의적립금 :</TD><TD></TD></TR><TR><TD>  사업확정적립금</TD><TD>300</TD></TR>"
               "<TR><TD>  합 계</TD><TD>435</TD></TR></TBODY></TABLE></BODY></DOCUMENT>")
        self.assertTrue(build_filing_tables("X", [("x.xml", doc.encode())], {})[0]["checks"]["sum_ok"])

    def _sum_doc(self, rows):
        body = "".join(f"<TR><TD>{a}</TD><TD>{b}</TD></TR>" for a, b in rows)
        doc = ("<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER=\"1\"><THEAD><TR><TH>구분</TH><TH>당기말</TH></TR></THEAD>"
               f"<TBODY>{body}</TBODY></TABLE></BODY></DOCUMENT>")
        return build_filing_tables("X", [("x.xml", doc.encode())], {})[0]["checks"]["sum_ok"]

    def test_running_total_after_adjustment(self):  # 「계 / 차감:유동성대체 / 계」
        self.assertTrue(self._sum_doc([("시설자금", "11,377"), ("일반자금", "156"), ("계", "11,533"),
                                       ("차감:유동성대체", "(9,740)"), ("계", "1,793")]))
        self.assertFalse(self._sum_doc([("시설자금", "11,377"), ("일반자금", "156"), ("계", "11,533"),
                                        ("차감:유동성대체", "(9,740)"), ("계", "1,800")]))

    def test_consecutive_totals_by_currency_not_judged_by_carry(self):  # 「합계 KRW / 합계 USD」 연달은 합계
        self.assertTrue(self._sum_doc([("기업은행", "500"), ("국민은행", "500"), ("합 계", "1,000"), ("합 계", "300")]))

    def test_subtotal_of_children_after_blank_parent(self):  # 「급여비용: (빈 칸) / 근무원가 / 이자비용 / 소계」
        self.assertTrue(self._sum_doc([("기초 부채", "7,233"), ("급여비용:", ""), ("  근무원가", "943"),
                                       ("  이자비용", "229"), ("  재측정요소", "(4,509)"), ("소계", "(3,337)")]))

    def test_columns_must_agree_on_one_reading(self):  # 리뷰 4: 열마다 다른 해석으로 통과하면 안 된다
        doc = ("<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER=\"1\"><THEAD><TR><TH COLSPAN=\"2\">구분</TH><TH>당기말</TH><TH>전기말</TH></TR></THEAD><TBODY>"
               "<TR><TD>채무상품</TD><TD>A</TD><TD>10</TD><TD>5</TD></TR>"
               "<TR><TD ROWSPAN=\"3\">지분상품</TD><TD>B</TD><TD>20</TD><TD>7</TD></TR><TR><TD>C</TD><TD>30</TD><TD>8</TD></TR>"
               "<TR><TD>소계</TD><TD>60</TD><TD>15</TD></TR></TBODY></TABLE></BODY></DOCUMENT>")
        self.assertFalse(build_filing_tables("X", [("x.xml", doc.encode())], {})[0]["checks"]["sum_ok"])
        ok = doc.replace("<TD>15</TD>", "<TD>20</TD>")  # 두 열 모두 구간 전체 해석으로 맞음
        self.assertTrue(build_filing_tables("X", [("x.xml", ok.encode())], {})[0]["checks"]["sum_ok"])

    def test_subtotal_spanning_two_groups_still_ok(self):  # 소계가 앞 묶음까지 더한 원문 — 묶음 안·밖 어느 쪽이든 맞으면 통과
        doc = ("<DOCUMENT><BODY><P>(단위: 천원)</P><TABLE BORDER=\"1\"><THEAD><TR><TH COLSPAN=\"2\">구분</TH><TH>당기말</TH></TR></THEAD><TBODY>"
               "<TR><TD ROWSPAN=\"1\">채무상품</TD><TD>A</TD><TD>10</TD></TR>"
               "<TR><TD ROWSPAN=\"3\">지분상품</TD><TD>B</TD><TD>20</TD></TR><TR><TD>C</TD><TD>30</TD></TR><TR><TD>소계</TD><TD>60</TD></TR>"
               "</TBODY></TABLE></BODY></DOCUMENT>")
        self.assertTrue(build_filing_tables("X", [("x.xml", doc.encode())], {})[0]["checks"]["sum_ok"])

    def test_read_failed_keeps_raw_text(self):
        t = build_filing_tables("X", [("x.xml", RAGGED_DOC.encode())], {})[0]
        self.assertEqual(t["status"], "read_failed"); self.assertIn("raw_text", t)
        self.assertIn("현금", t["raw_text"])

    def test_fs_scope_from_filename(self):
        tabs = build_filing_tables("X", [("X_00761.xml", TWO_LEVEL_DOC.encode())], {})
        self.assertEqual(tabs[0]["fs_scope"], "consolidated")
        tabs = build_filing_tables("X", [("X_00760.xml", TWO_LEVEL_DOC.encode())], {})
        self.assertEqual(tabs[0]["fs_scope"], "separate")
        self.assertEqual(tabs[0]["table_id"], "X:00760:t001")

    def test_fs_scope_from_text_without_suffix(self):
        doc = "<DOCUMENT><BODY><P>연결재무제표에 대한 감사의견</P>" + TWO_LEVEL_TABLE + "</BODY></DOCUMENT>"
        t = build_filing_tables("X", [("X.xml", doc.encode())], {})[0]
        self.assertEqual(t["fs_scope"], "consolidated")
        self.assertEqual(t["table_id"], "X:main:t001")

    def test_dash_zero_blank_null_and_negative(self):
        t = build_filing_tables("X", [("x.xml", DASH_DOC.encode())], {})[0]
        cash, dep = t["rows"]
        self.assertEqual([(c["raw"], c["value"]) for c in cash["cells"]], [("-", 0), ("", None)])
        self.assertEqual(dep["cells"][0]["value"], -1200)
        self.assertEqual(dep["cells"][0]["value_krw"], -1200)

    def test_regions_and_note_numbers(self):
        tabs = build_filing_tables("X", [("x.xml", SECTIONS_DOC.encode())], {})
        kinds = [t["region"]["kind"] for t in tabs]
        self.assertEqual(kinds, ["auditor_report", "statement_cf", "note"])
        self.assertEqual(tabs[2]["region"]["note_no"], "3")
        self.assertEqual(tabs[2]["region"]["note_title"], "현금및현금성자산")

    def test_finance_balance_sheet_identity(self):
        tabs = build_filing_tables("20260318001113", load_fixture("20260318001113_bs.xml"), {})
        self.assertEqual({t["region"]["kind"] for t in tabs}, {"other", "statement_bs"})  # 표지 기간 표·제목 표·본표
        bs = [t for t in tabs if t["region"]["kind"] == "statement_bs" and any(r["acode"] for r in t["rows"])]
        self.assertEqual(len(bs), 1)
        t = bs[0]
        self.assertEqual(t["fs_scope"], "consolidated")
        self.assertTrue(t["checks"]["identity_ok"])
        self.assertTrue(t["checks"]["period_found"])
        self.assertEqual(t["unit"]["kind"], "krw")
        total = [r for r in t["rows"] if r["acode"] == "11500000010000"]  # 자산총계
        self.assertTrue(total)
        self.assertNotIn("과목", [r["label"].replace(" ", "") for r in t["rows"]])  # THEAD 행은 본문이 아니다

    def test_build_filing_header_and_paragraphs(self):
        f = build_filing("X", [("x.xml", SECTIONS_DOC.encode())], {})
        self.assertEqual(f["rcept_no"], "X")
        self.assertIsNone(f["amends_rcept_no"])
        self.assertEqual(f["parser_version"], PARSER_VERSION)
        ids = [p["id"] for p in f["paragraphs"]]
        self.assertEqual(ids, [f"s{i}" for i in range(1, len(ids) + 1)])
        texts = [p["text"].strip() for p in f["paragraphs"]]
        self.assertIn("회사는 설립되었습니다.", texts)
        self.assertIn("본사는 서울에 있습니다.", texts)
        self.assertEqual(len(f["tables"]), 3)

    def test_period_end_from_cover(self):
        f = build_filing("20260318001076", load_fixture("20260318001076_cb.xml"), {})
        self.assertEqual(f["period_end"], "2025-12-31")

    def test_amends_only_with_known_original(self):
        man = {"report_name": "[기재정정]감사보고서 (2024.12)", "orig_rcept_no": "20250301000001"}
        self.assertEqual(build_filing("X", [("x.xml", TWO_LEVEL_DOC.encode())], man)["amends_rcept_no"], "20250301000001")
        man = {"report_name": "[기재정정]감사보고서 (2024.12)"}
        self.assertIsNone(build_filing("X", [("x.xml", TWO_LEVEL_DOC.encode())], man)["amends_rcept_no"])

    def test_roundtrip_gz(self):
        f = {"rcept_no": "X", "amends_rcept_no": None, "period_end": None, "paragraphs": [], "tables": [{"table_id": "X:main:t001"}]}
        path = write_filing(Path(self.tmp), "X", f)
        self.assertEqual(path.name, "X.json.gz")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            json.load(fh)
        self.assertEqual(load_filing(path), f)



class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_runner_writes_skips_and_summarizes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ledger_tables_cli", Path(__file__).parents[1] / "scripts" / "ledger_tables.py")
        cli = importlib.util.module_from_spec(spec)
        import sys
        sys.modules["ledger_tables_cli"] = cli  # Pool 작업 함수 pickle 용
        spec.loader.exec_module(cli)
        docs = self.tmp / "docs"
        (docs / "R1").mkdir(parents=True)
        (docs / "R1" / "R1_00760.xml").write_bytes(BAD_SUM_DOC.encode())
        (docs / "R2").mkdir()
        (docs / "R2" / "R2_00760.xml").write_bytes(RAGGED_DOC.encode())
        out = self.tmp / "tables"
        argv = ["--docs", str(docs), "--out-dir", str(out), "--workers", "1"]
        self.assertEqual(cli.main(argv), 0)
        self.assertTrue((out / "R1.json.gz").exists())
        summary = json.loads((self.tmp / "tables.summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["filings"], 2)
        self.assertEqual(summary["status"], {"needs_review": 1, "read_failed": 1})
        self.assertIn(["sum_mismatch", 1], summary["top_failure_reasons"])
        mtime = (out / "R1.json.gz").stat().st_mtime_ns
        cli.main(argv)
        summary = json.loads((self.tmp / "tables.summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["filings_skipped"], 2)
        self.assertEqual((out / "R1.json.gz").stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
