"""2층 정책·예외 문장 표시(policy_marks.py) 테스트."""
import unittest

from dart_search_mcp.ledger.policy_marks import mark_policies
from dart_search_mcp.ledger.tables import build_filing
from tests.test_ledger_tables import load_fixture


def paras(*texts):
    return [{"id": f"s{i}", "file": "f.xml", "page": 1, "text": t} for i, t in enumerate(texts, 1)]


def find(rows, topic, **kw):
    return [r for r in rows if r["topic"] == topic and all(r[k] == v for k, v in kw.items())]


class PolicyMarksTest(unittest.TestCase):
    def test_fvoci_standard_sentence_is_policy(self):
        # 크레버스에프 픽스처는 범주별 금융상품 표만 잘라 둬 이 문장이 없다 — 원문 문장을 손으로 넣는다.
        s = ("단기매매항목이 아닌 지분상품에 대한 투자에 대하여 후속적인 공정가치 변동을 기타포괄손익으로 "
             "표시하도록 지정하는 취소불가능한 선택을 할 수 있습니다.")
        rows = mark_policies("20260330000644", paras(s), [])
        (r,) = find(rows, "FVOCI_DESIGNATION")
        self.assertEqual(r["kind"], "policy")
        self.assertIsNone(r["exception"])

    def test_crevers_fixture_runs(self):
        f = build_filing("20260330000644", load_fixture("20260330000644_category.xml"), {})
        self.assertEqual(mark_policies("20260330000644", f["paragraphs"], f["tables"]), [])

    def test_dev_amortization_sentence_is_exception(self):
        rows = mark_policies("R1", paras("개발비는 5년 동안 정액법으로 상각합니다."), [])
        (r,) = find(rows, "DEV_COST_CAPITALIZATION")
        self.assertEqual((r["kind"], r["exception"], r["sentence_id"]), ("exception", "dev_amortization", "s1"))

    def test_intangible_life_table_row_is_exception(self):
        table = {"table_id": "R2:00760:t005", "title": "무형자산의 내용연수",
                 "region": {"kind": "note", "note_title": "무형자산"}, "footnotes": [],
                 "rows": [{"row": 2, "label": "개발비", "cells": [{"col": 1, "raw": "5년"}]},
                          {"row": 3, "label": "회원권", "cells": [{"col": 1, "raw": "비한정"}]}]}
        rows = mark_policies("R2", [], [table])
        (r,) = rows
        self.assertEqual((r["topic"], r["kind"], r["exception"], r["sentence_id"]),
                         ("DEV_COST_CAPITALIZATION", "exception", "dev_amortization", "t:R2:00760:t005:r2"))

    def test_explicit_separation_footnote_is_company_statement(self):
        s = "당사는 상기 전환사채의 조기상환청구권 및 전환권을 내재파생상품으로 별도로 분리하여 공정가치로 인식하였습니다."
        table = {"table_id": "R3:t1", "title": "x", "region": {}, "rows": [], "footnotes": [{"mark": "(*)", "text": s}]}
        rows = mark_policies("R3", [], [table])
        (r,) = find(rows, "EMBEDDED_DERIVATIVE_SEPARATION")
        self.assertEqual((r["kind"], r["exception"]), ("company_statement", "explicit_separation"))

    def test_company_past_tense_in_body(self):
        rows = mark_policies("R4", paras("당사는 단기리스에 대하여 인식 면제를 적용하였습니다."), [])
        (r,) = find(rows, "LEASE_EXEMPTION")
        self.assertEqual(r["kind"], "company_statement")

    def test_lease_standard_sentence_is_policy(self):
        rows = mark_policies("R5", paras("리스이용자는 단기리스와 소액 기초자산 리스에 인식 면제를 선택할 수 있습니다."), [])
        (r,) = find(rows, "LEASE_EXEMPTION")
        self.assertEqual(r["kind"], "policy")

    def test_footnote_fixture_metanet(self):
        f = build_filing("20251205000494", load_fixture("20251205000494_fv.xml"), {})
        rows = mark_policies("20251205000494", f["paragraphs"], f["tables"])
        hits = [r for r in find(rows, "EMBEDDED_DERIVATIVE_SEPARATION") if "내재파생상품 분리금액" in r["text"]]
        self.assertEqual(len(hits), 1)  # 반복 각주는 한 번만
        self.assertEqual(hits[0]["kind"], "company_statement")

    def test_no_topic_keyword_no_row(self):
        self.assertEqual(mark_policies("R6", paras("재고자산은 선입선출법으로 평가합니다."), []), [])


if __name__ == "__main__":
    unittest.main()
