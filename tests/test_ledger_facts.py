"""2층 ① 항목 금액 사실(facts.py)·개념 사전(concepts.json) 테스트."""
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from dart_search_mcp.ledger.facts import (
    CONCEPTS_PATH,
    Concept,
    coverage,
    extract_facts,
    load_concepts,
    unmatched_rows,
)
from dart_search_mcp.ledger.tables import build_filing
from tests.test_ledger_tables import load_fixture

KRW = {"raw": "(단위: 원)", "kind": "krw", "multiplier": 1, "currency": None}
KRW1000 = {"raw": "(단위: 천원)", "kind": "krw", "multiplier": 1000, "currency": None}
SHARES = {"raw": "주", "kind": "shares", "multiplier": 1, "currency": None}


def _table(rows, *, note_title="사채", title="사채의 내역", kind="note", periods=("current_end", "prior_end"),
           headers=None, unit=KRW, status="verified", tid="R:00760:t001", overrides=None):
    """손으로 만든 표 기록. rows = [(label, [값…]) | (label_path, [값…], level)], 값 열은 1부터."""
    headers = headers or [p for p in periods]
    columns = [{"col": 0, "header_path": ["구 분"], "role": "label"}]
    for i, (p, h) in enumerate(zip(periods, headers), start=1):
        columns.append({"col": i, "header_path": h if isinstance(h, list) else [h], "role": "value",
                        "period": p, "date": None})
    out_rows = []
    for k, spec in enumerate(rows, start=1):
        label, values = spec[0], spec[1]
        level = spec[2] if len(spec) > 2 else 0
        path = label if isinstance(label, list) else [label]
        cells = []
        for c, v in enumerate(values, start=1):
            ov = (overrides or {}).get(c)
            mult = (ov or unit)["multiplier"]
            ukind = (ov or unit)["kind"]
            cells.append({"col": c, "raw": "" if v is None else f"{v:,}", "value": v,
                          "value_krw": v * mult if v is not None and ukind == "krw" else None,
                          "unit_override": ov})
        kind_ = "total" if path[-1] in ("합계", "합 계") else "item"
        out_rows.append({"row": k, "label": path[-1], "label_path": path, "level": level, "acode": None,
                         "kind": kind_, "cells": cells})
    region = {"kind": kind, "note_no": "1" if kind == "note" else None,
              "note_title": note_title if kind == "note" else None, "sub_path": []}
    return {"table_id": tid, "rcept_no": "R", "amends_rcept_no": None, "file": "R_00760.xml",
            "fs_scope": "separate", "page": 3, "region": region, "title": title, "unit": unit,
            "columns": columns, "rows": out_rows, "footnotes": [], "status": status,
            "checks": {}, "issues": [], "parser_version": "ledger-t3"}


def _fixture_facts(name):
    rcept = name.split("_")[0]
    filing = build_filing(rcept, load_fixture(name), {})
    return extract_facts(filing["tables"], load_concepts())


def _by(facts, concept):
    return [f for f in facts if f["concept"] == concept]


class ConceptDictionaryTest(unittest.TestCase):
    def test_load_default(self):
        concepts = load_concepts()
        ids = [c.id for c in concepts]
        self.assertEqual(ids[:3], ["CONV_OPTION_EQUITY", "CONV_OPTION_LIABILITY", "HOST_LIABILITY"])
        for cid in ("EMB_DERIV_LIABILITY", "EMB_DERIV_ASSET", "FVOCI_EQUITY", "FVOCI_DEBT", "FVOCI_EQUITY_RESERVE",
                    "DEV_COST_ASSET", "DEV_COST_ADDITION", "DEV_COST_AMORT", "RND_EXPENSE", "LEASE_SHORT_TERM_EXP",
                    "LEASE_LOW_VALUE_EXP", "LEASE_LIABILITY", "IP_CARRYING", "IP_FAIR_VALUE", "BC_CONSIDERATION",
                    "BC_NET_ASSETS_FV", "BC_RELATED_COST"):
            self.assertIn(cid, ids)
        self.assertIsInstance(concepts[0], Concept)
        self.assertTrue(all(c.value_kind in ("balance", "flow") for c in concepts))
        self.assertTrue(all(c.topic for c in concepts))
        meta = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
        self.assertRegex(meta["concepts_version"], r"^c\d+$")

    def test_load_custom_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"concepts_version": "c9", "topics": {}, "concepts": [
                {"id": "X", "topic": "t", "label_patterns": ["^현금$"], "regions": ["note:현금"],
                 "value_kind": "balance"}]}), encoding="utf-8")
            (c,) = load_concepts(p)
            self.assertEqual((c.id, c.version, c.context_patterns, c.exclude_patterns, c.target), ("X", "c9", (), (), None))


class FixtureFactsTest(unittest.TestCase):
    def test_ildong_equity_component(self):
        facts = _fixture_facts("20260318001076_cb.xml")
        eq = {f["period"]: f for f in _by(facts, "CONV_OPTION_EQUITY")}
        self.assertEqual(eq["current_end"]["amount_krw"], 121_352_000)
        self.assertEqual(eq["current_end"]["amount"], 121_352)
        self.assertEqual(eq["prior_end"]["amount_krw"], 519_263_000)
        self.assertEqual(eq["current_end"]["label"], "전환사채의 자본요소")
        self.assertEqual(eq["current_end"]["fs_scope"], "consolidated")
        self.assertEqual(eq["current_end"]["source_status"], "verified")
        self.assertEqual(set(eq["current_end"]["source"]), {"table_id", "page", "row", "col"})
        self.assertEqual(_by(facts, "CONV_OPTION_LIABILITY"), [])
        host = {(f["label"], f["period"]): f["amount_krw"] for f in _by(facts, "HOST_LIABILITY")}
        self.assertEqual(host[("전환사채", "current_end")], 1_000_000_000)
        self.assertEqual(host[("전환권조정", "prior_end")], -30_905_828_000)
        self.assertIn(("전환사채상환할증금", "current_end"), host)

    def test_metanetx_embedded_derivative_liability(self):
        facts = _fixture_facts("20251205000494_fv.xml")
        emb = _by(facts, "EMB_DERIV_LIABILITY")
        self.assertTrue(emb)
        self.assertTrue(all(f["target"] in ("conversion", "redemption") for f in emb))
        # 공정가치 서열 표는 수준 1·2·3·합계가 같은 기간 → 합계 열 하나만
        cur = [f for f in emb if f["period"] == "current_end"]
        self.assertEqual([f["amount_krw"] for f in cur], [2_500_419_206])
        prior = [f for f in emb if f["period"] == "prior_end"]
        self.assertEqual([f["amount_krw"] for f in prior], [12_213_899_634])

    def test_creverse_no_fvoci_equity(self):
        facts = _fixture_facts("20260330000644_category.xml")
        self.assertEqual(_by(facts, "FVOCI_EQUITY"), [])

    def test_melcon_investor_side_cb_is_not_host(self):
        facts = _fixture_facts("20260406002825_cbinvest.xml")
        self.assertEqual([f for f in facts if f["concept"].startswith(("HOST", "CONV"))], [])


class HandTableTest(unittest.TestCase):
    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables, stats=None):
        return extract_facts(list(tables), self.concepts, stats=stats)

    def test_fvoci_debt_only(self):
        t = _table([("기타포괄손익-공정가치측정 채무상품", [100, 90]),
                    ("기타포괄손익-공정가치측정 지분상품", [50, 40])], note_title="금융상품")
        got = {(f["label"], f["concept"]) for f in self.facts(t)}
        self.assertEqual(got, {("기타포괄손익-공정가치측정 채무상품", "FVOCI_DEBT"),
                               ("기타포괄손익-공정가치측정 지분상품", "FVOCI_EQUITY")})

    def test_currency_swap_not_embedded(self):
        t = _table([("통화스왑 파생상품부채", [10, 20]), ("파생상품부채", [5, 6])],
                   note_title="파생상품", title="전환사채 관련 파생상품부채")
        facts = self.facts(t)
        self.assertEqual({f["label"] for f in _by(facts, "EMB_DERIV_LIABILITY")}, {"파생상품부채"})

    def test_currency_swap_child_under_parent(self):
        t = _table([(["파생상품부채", "통화스왑"], [10, 20])], note_title="파생상품", title="사채 관련")
        self.assertEqual(_by(self.facts(t), "EMB_DERIV_LIABILITY"), [])

    def test_embedded_needs_context(self):
        t = _table([("파생상품부채", [5, 6])], note_title="파생상품", title="파생상품의 내역")
        self.assertEqual(_by(self.facts(t), "EMB_DERIV_LIABILITY"), [])

    def test_embedded_target_from_label_path_parent(self):
        t = _table([(["제3회 전환사채", "파생상품부채"], [5, 6]), (["상환우선주", "파생상품부채"], [7, 8]),
                    (["조기상환청구권", "파생상품자산"], [1, 2])], note_title="사채", title="파생상품의 내역")
        facts = self.facts(t)
        tgt = {(f["concept"], f["amount"]): f["target"] for f in facts}
        self.assertEqual(tgt[("EMB_DERIV_LIABILITY", 5)], "conversion")
        self.assertEqual(tgt[("EMB_DERIV_LIABILITY", 7)], "redemption")
        self.assertEqual(tgt[("EMB_DERIV_ASSET", 1)], "redemption")

    def test_footnote_gives_context(self):
        t = _table([("파생상품부채(*)", [5, 6])], note_title="공정가치", title="① 당기말")
        t["footnotes"] = [{"mark": "(*)", "text": "상환전환우선주부채 중 내재파생상품 분리금액입니다."}]
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual(f["label"], "파생상품부채(*)")

    def test_rowspan_child_host(self):
        t = _table([(["제3회 전환사채", "전환권조정"], [-30, -40]), (["제3회 전환사채", "액면금액"], [1000, 1000])])
        host = _by(self.facts(t), "HOST_LIABILITY")
        self.assertEqual({f["label"] for f in host}, {"전환권조정"})

    def test_read_failed_table_has_no_facts(self):
        t = _table([("전환권대가", [100, 200])], status="read_failed")
        self.assertEqual(self.facts(t), [])

    def test_needs_review_status_kept(self):
        t = _table([("전환권대가", [100, 200])], status="needs_review")
        self.assertEqual({f["source_status"] for f in self.facts(t)}, {"needs_review"})

    def test_region_must_match(self):
        t = _table([("전환권대가", [100, 200])], note_title="재고자산", title="재고자산의 내역")
        self.assertEqual(self.facts(t), [])

    def test_exclude_movement_rows(self):
        t = _table([("전환권대가", [100, 200]), ("전환권대가 행사", [-10, 0]), ("전환으로 인한 대체", [5, 0])])
        self.assertEqual({f["label"] for f in _by(self.facts(t), "CONV_OPTION_EQUITY")}, {"전환권대가"})

    def test_dev_cost_addition_row_by_column(self):
        t = _table([("개발비", [100, 30, -10, 120]), ("소프트웨어", [5, 1, 0, 6])], note_title="무형자산",
                   title="무형자산의 변동내역",
                   periods=("opening", "current_period", "current_period", "current_end"),
                   headers=["기초", "취득", "상각", "기말"])
        facts = self.facts(t)
        add = _by(facts, "DEV_COST_ADDITION")
        self.assertEqual([(f["amount"], f["period"]) for f in add], [(30, "current_period")])
        self.assertEqual([(f["amount"], f["period"]) for f in _by(facts, "DEV_COST_ASSET")], [(120, "current_end")])

    def test_balance_period_inferred_from_current_period_table(self):
        t = _table([("전환권대가", [100])], periods=("current_period",), headers=["금액"])
        (f,) = self.facts(t)
        self.assertEqual((f["period"], f.get("period_inferred")), ("current_end", True))

    def test_balance_not_inferred_when_end_columns_exist(self):
        t = _table([("전환권대가", [100, 7])], periods=("current_end", "current_period"), headers=["당기말", "당기 증감"])
        self.assertEqual([(f["period"], f["amount"]) for f in self.facts(t)], [("current_end", 100)])

    def test_flow_uses_period_columns(self):
        t = _table([("단기리스료", [10, 9])], note_title="리스", periods=("current_period", "prior_period"),
                   headers=["당기", "전기"])
        self.assertEqual({(f["concept"], f["period"]) for f in self.facts(t)},
                         {("LEASE_SHORT_TERM_EXP", "current_period"), ("LEASE_SHORT_TERM_EXP", "prior_period")})

    def test_shares_cells_skipped(self):
        t = _table([("전환권대가", [100, 3000])], periods=("current_end", "current_end"), headers=["금액", "주식수"],
                   overrides={2: SHARES})
        self.assertEqual([f["amount"] for f in self.facts(t)], [100])

    def test_zero_and_dash_kept(self):
        t = _table([("전환권대가", [0, 200])])
        self.assertEqual(sorted(f["amount"] for f in self.facts(t)), [0, 200])

    def test_statement_bs_region(self):
        t = _table([("리스부채", [10, 20]), ("유동리스부채", [3, 4])], kind="statement_bs", title="재무상태표")
        self.assertEqual(len(_by(self.facts(t), "LEASE_LIABILITY")), 4)

    def test_ip_fair_value_by_column(self):
        t = _table([("투자부동산", [100, 150, 90, 140])], note_title="투자부동산", title="공정가치",
                   periods=("current_end", "current_end", "prior_end", "prior_end"),
                   headers=[["당기말", "장부금액"], ["당기말", "공정가치"], ["전기말", "장부금액"], ["전기말", "공정가치"]])
        facts = self.facts(t)
        self.assertEqual({(f["period"], f["amount"]) for f in _by(facts, "IP_CARRYING")},
                         {("current_end", 100), ("prior_end", 90)})
        self.assertEqual({(f["period"], f["amount"]) for f in _by(facts, "IP_FAIR_VALUE")},
                         {("current_end", 150), ("prior_end", 140)})

    def test_conflict_first_concept_wins_and_counted(self):
        # 「상환전환우선주부채」는 HOST_LIABILITY 와 (내재파생 문맥이 있으면) 다른 개념에 함께 걸릴 수 있다 → 앞 개념 하나
        t = _table([("전환권 파생상품부채", [10, 20])], note_title="파생상품", title="사채")
        stats = Counter()
        facts = self.facts(t, stats=stats)
        self.assertEqual({f["concept"] for f in facts}, {"CONV_OPTION_LIABILITY"})
        self.assertEqual(stats["conflict_rows"], 1)
        self.assertIn("conflict:CONV_OPTION_LIABILITY>EMB_DERIV_LIABILITY", stats)

    def test_fact_shape(self):
        t = _table([("전환권대가", [100, 200])], unit=KRW1000)
        f = self.facts(t)[0]
        for k in ("rcept_no", "amends_rcept_no", "fs_scope", "concept", "period", "amount", "unit_kind", "amount_krw",
                  "label", "target", "source", "source_status", "concepts_version"):
            self.assertIn(k, f)
        self.assertEqual((f["amount"], f["amount_krw"], f["unit_kind"]), (100, 100_000, "krw"))
        self.assertEqual(f["source"], {"table_id": "R:00760:t001", "page": 3, "row": 1, "col": 1})


class UnmatchedAndCoverageTest(unittest.TestCase):
    def test_unmatched_rows_in_relevant_notes_only(self):
        concepts = load_concepts()
        t1 = _table([("전환권대가", [1, 2]), ("미지급이자", [3, 4]), ("합계", [4, 6])])
        t2 = _table([("원재료", [1, 2])], note_title="재고자산", title="재고자산의 내역", tid="R:00760:t002")
        t3 = _table([("미지급이자", [5, 5])], status="read_failed", tid="R:00760:t003")
        c = unmatched_rows([t1, t2, t3], concepts)
        self.assertEqual(c, Counter({"미지급이자": 1}))

    def test_coverage_per_topic(self):
        concepts = load_concepts()
        t = _table([("전환권대가", [1, 2]), ("전환사채", [3, 4]), ("미지급이자", [5, 6]), ("전환권 행사", [1, 1])])
        cov = coverage([t], concepts)["conversion"]
        self.assertEqual((cov["rows"], cov["matched"]), (4, 2))
        self.assertEqual((cov["kw_rows"], cov["kw_matched"]), (3, 2))


class RunnerTest(unittest.TestCase):
    def test_runner_outputs_and_no_overwrite(self):
        from dart_search_mcp.ledger.tables import write_filing
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("ledger_facts_cli", Path(__file__).parents[1] / "scripts" / "ledger_facts.py")
        ledger_facts = importlib.util.module_from_spec(spec)
        sys.modules["ledger_facts_cli"] = ledger_facts  # Pool 작업 함수 pickle 용
        spec.loader.exec_module(ledger_facts)

        with tempfile.TemporaryDirectory() as d:
            tables, out = Path(d) / "tables", Path(d) / "out"
            filing = build_filing("20260318001076", load_fixture("20260318001076_cb.xml"), {})
            write_filing(tables, "20260318001076", filing)
            lst = Path(d) / "list.txt"
            lst.write_text("20260318001076\n99999999999999\n", encoding="utf-8")
            rc = ledger_facts.main(["--tables", str(tables), "--out-dir", str(out), "--rcept-list", str(lst),
                                    "--workers", "1"])
            self.assertEqual(rc, 0)
            facts = [json.loads(x) for x in (out / "facts.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("CONV_OPTION_EQUITY", {f["concept"] for f in facts})
            summary = json.loads((out / "facts.summary.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["filings"], summary["facts"]), (1, len(facts)))
            self.assertIn("conversion", summary["coverage"])
            self.assertTrue((out / "facts.unmatched.tsv").read_text(encoding="utf-8").startswith("count\tlabel"))
            # 다시 돌리면 덮지 않고 멈춘다
            self.assertEqual(ledger_facts.main(["--tables", str(tables), "--out-dir", str(out), "--workers", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
