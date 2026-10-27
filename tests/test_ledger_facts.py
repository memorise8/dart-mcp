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
        ids = list(dict.fromkeys(c.id for c in concepts))  # 같은 id 여러 항목 = 같은 개념의 다른 표 모양
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
        # c3: 회차 아래 「액면금액」도 주계약 금액(c1·c2에서는 안 걸렸음)
        self.assertEqual({f["label"] for f in host}, {"전환권조정", "액면금액"})

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
        t = _table([("전환권대가", [1, 2]), ("전환사채", [3, 4]), ("미지급이자", [5, 6]), ("전환권 행사", [1, 1]),
                    ("전환권조정", [1, 1])])
        unmatched = {}
        cov = coverage([t], concepts, unmatched=unmatched)["conversion"]
        self.assertEqual((cov["rows"], cov["matched"]), (5, 3))
        # 정의 B: 주제 낱말 행(「전환권 행사」는 변동 행이라 kw_exclude)
        self.assertEqual((cov["kw_rows"], cov["kw_matched"]), (3, 3))
        self.assertEqual(unmatched, {})


class DictionaryC2Test(unittest.TestCase):
    """c2 보강(300건 표본 미매칭 상위에서)."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_bumped(self):
        self.assertNotEqual(self.concepts[0].version, "c1")

    def test_rnd_expense_not_excluded_by_own_word(self):
        t = _table([("경상연구개발비", [10, 9]), ("경상개발비", [3, 2])], note_title="판매비와관리비", title="판관비",
                   periods=("current_period", "prior_period"))
        self.assertEqual({f["label"] for f in _by(self.facts(t), "RND_EXPENSE")}, {"경상연구개발비", "경상개발비"})

    def test_rnd_expense_in_income_statement(self):
        t = _table([("경상연구개발비", [10, 9])], kind="statement_is", title="손익계산서",
                   periods=("current_period", "prior_period"))
        self.assertEqual(len(_by(self.facts(t), "RND_EXPENSE")), 2)

    def test_option_under_fvtpl_header_is_option_liability(self):
        t = _table([("전환사채", [None]), ("액면가액", [500]), ("전환권조정", [-44]), ("소계", [456]),
                    ("당기손익-공정가치측정금융부채", [None]), ("조기상환청구권", [11]), ("전환권", [19]), ("소계", [30])],
                   note_title="차입금", title="전환사채 관련 잔액의 변동", periods=("current_end",), headers=["기말"])
        t["rows"][3]["kind"] = t["rows"][7]["kind"] = "subtotal"
        got = {(f["label"], f["concept"]) for f in self.facts(t)}
        self.assertIn(("전환권", "CONV_OPTION_LIABILITY"), got)
        self.assertIn(("조기상환청구권", "EMB_DERIV_LIABILITY"), got)
        self.assertIn(("전환권조정", "HOST_LIABILITY"), got)
        # c3: 사채 머리 행 아래 「액면가액」은 주계약 구성 금액(「전환사채」 행이 액면인 것과 같은 뜻)
        self.assertIn(("액면가액", "HOST_LIABILITY"), got)

    def test_host_in_financial_liability_note_and_series_rows(self):
        t = _table([("제1회 무보증사모전환사채", [100, 0]), ("차감: 사채할인발행차금", [-5, 0]), ("신주인수권조정", [-3, 0]),
                    ("채무증권-전환사채", [7, 7])], note_title="금융부채", title="사채 내역")
        got = {f["label"] for f in _by(self.facts(t), "HOST_LIABILITY")}
        self.assertEqual(got, {"제1회 무보증사모전환사채", "차감: 사채할인발행차금", "신주인수권조정"})

    def test_conv_equity_in_capital_note_but_not_tax_effect(self):
        t = _table([("전환권대가", [10, 0]), ("전환권대가 세율변동효과", [1, 0])], note_title="자본금과 자본잉여금",
                   title="기타자본")
        self.assertEqual({f["label"] for f in _by(self.facts(t), "CONV_OPTION_EQUITY")}, {"전환권대가"})

    def test_fvoci_total_and_reserves_separate(self):
        t = _table([("기타포괄손익-공정가치측정금융자산", [100, 90]), ("기타포괄손익-공정가치측정금융자산평가손익", [5, 4]),
                    ("기타포괄손익-공정가치측정 지분상품 평가손익", [3, 2])], note_title="금융상품", title="범주별")
        got = {(f["label"], f["concept"]) for f in self.facts(t)}
        self.assertIn(("기타포괄손익-공정가치측정금융자산", "FVOCI_FA_TOTAL"), got)
        self.assertNotIn("FVOCI_EQUITY", {c for _, c in got})
        self.assertNotIn("FVOCI_DEBT", {c for _, c in got})

    def test_dev_cost_columnwise_table(self):
        t = _table([("취득원가:", [None, None]), ("기말금액", [900, 1000]), ("장부금액:", [None, None]), ("기초", [50, 60]),
                    ("취득", [20, 25]), ("상각", [-10, -12]), ("기말", [60, 73])], note_title="무형자산",
                   title="무형자산 변동", periods=("current_period", "current_period"), headers=["개발비", "합계"])
        facts = self.facts(t)
        self.assertEqual([(f["amount"], f["period"]) for f in _by(facts, "DEV_COST_ASSET")], [(60, "current_end")])
        self.assertEqual([f["amount"] for f in _by(facts, "DEV_COST_ADDITION")], [20])
        self.assertEqual([f["amount"] for f in _by(facts, "DEV_COST_AMORT")], [-10])

    def test_dev_cost_amort_rowwise(self):
        t = _table([("개발비", [100, 30, -10, 120])], note_title="무형자산", title="무형자산의 변동",
                   periods=("opening", "current_period", "current_period", "current_end"),
                   headers=["기초", "취득", "상각", "기말"])
        self.assertEqual([f["amount"] for f in _by(self.facts(t), "DEV_COST_AMORT")], [-10])

    def test_dev_cost_asset_prefers_carrying_column(self):
        t = _table([("개발비", [100, -40, 60])], note_title="무형자산", title="무형자산 내역",
                   periods=("current_end",) * 3, headers=["취득원가", "상각누계액", "장부금액"])
        self.assertEqual([f["amount"] for f in _by(self.facts(t), "DEV_COST_ASSET")], [60])

    def test_lease_variants(self):
        t = _table([("단기리스", [3, 2]), ("소액자산리스", [1, 1]), ("단기리스료 및 소액자산리스료", [4, 3]),
                    ("리스부채에 대한 이자비용", [9, 9])], note_title="리스", title="리스 비용",
                   periods=("current_period", "prior_period"))
        got = {(f["label"], f["concept"]) for f in self.facts(t)}
        self.assertEqual(got, {("단기리스", "LEASE_SHORT_TERM_EXP"), ("소액자산리스", "LEASE_LOW_VALUE_EXP"),
                               ("단기리스료 및 소액자산리스료", "LEASE_SHORT_LOW_EXP")})

    def test_lease_liability_variants(self):
        t = _table([("유동성리스부채", [1, 1]), ("리스부채-비유동", [2, 2]), ("리스부채(유동)", [3, 3]), ("리스부채 합계", [4, 4]),
                    ("유동리스부채의 상환", [5, 5])], note_title="리스", title="리스부채")
        self.assertEqual({f["label"] for f in _by(self.facts(t), "LEASE_LIABILITY")},
                         {"유동성리스부채", "리스부채-비유동", "리스부채(유동)", "리스부채 합계"})

    def test_ip_carrying_and_fair_value_rows(self):
        t = _table([("토지(*1),(*2)", [10, 15]), ("건물", [20, 22]), ("합계", [30, 37])], note_title="투자부동산",
                   title="투자부동산의 공정가치", periods=("current_end", "current_end"),
                   headers=[["당기말", "장부금액"], ["당기말", "공정가치"]])
        facts = self.facts(t)
        self.assertEqual({(f["label"], f["amount"]) for f in _by(facts, "IP_CARRYING")},
                         {("토지(*1),(*2)", 10), ("건물", 20), ("합계", 30)})
        self.assertEqual({f["amount"] for f in _by(facts, "IP_FAIR_VALUE")}, {15, 22, 37})

    def test_ip_movement_transposed(self):
        t = _table([("기초", [100, 110]), ("감가상각", [-5, -6]), ("기말", [95, 104])], note_title="투자부동산",
                   title="투자부동산 변동", periods=("current_period", "current_period"), headers=["건물", "합계"])
        self.assertEqual({(f["amount"], f["period"]) for f in _by(self.facts(t), "IP_CARRYING")},
                         {(104, "current_end")})

    def test_ip_movement_rowwise_closing_column(self):
        t = _table([("토지", [100, 0, 100]), ("합계", [300, -5, 295])], note_title="투자부동산", title="변동",
                   periods=("opening", "current_period", "current_end"), headers=["기초순장부가액", "감가상각", "기말순장부가액"])
        self.assertEqual({f["amount"] for f in _by(self.facts(t), "IP_CARRYING")}, {100, 295})

    def test_bc_lettered_rows_and_consideration_column(self):
        t1 = _table([("A. 이전대가", [2113]), ("차감: 식별가능한 취득한 순자산의 공정가치", [1294])], note_title="사업결합",
                    title="영업권 산정", periods=("unknown",), headers=["금 액"])
        t2 = _table([("하나아이티엠㈜", [100, 12000])], note_title="사업결합", title="사업결합 내역",
                    periods=("prior_period", "prior_period"), headers=["취득한 지분(%)", "이전대가"],
                    overrides={1: {"raw": "%", "kind": "percent", "multiplier": 1, "currency": None}}, tid="R:00760:t002")
        facts = self.facts(t1, t2)
        self.assertEqual({f["amount"] for f in _by(facts, "BC_CONSIDERATION")}, {2113, 12000})
        self.assertEqual({f["amount"] for f in _by(facts, "BC_NET_ASSETS_FV")}, {1294})

    def test_embedded_convertible_note_english_context(self):
        t = _table([("유동파생상품부채(Convertible Note)", [5, 6])], note_title="파생상품", title="파생상품 내역")
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual(f["target"], "conversion")

    def test_option_rows_in_fair_value_level_table(self):
        t = _table([("유동파생상품부채", [None, None]), ("전환사채 전환권", [0, 5]), ("상환전환우선주부채 전환권", [0, 7])],
                   note_title="금융상품의 공정가치", title="수준별 공정가치", periods=("current_end", "current_end"),
                   headers=["수준 3", "합 계"])
        got = {(f["label"], f["amount"]) for f in _by(self.facts(t), "CONV_OPTION_LIABILITY")}
        self.assertEqual(got, {("전환사채 전환권", 5), ("상환전환우선주부채 전환권", 7)})

    def test_combined_option_rows_embedded(self):
        t = _table([(["전환상환우선주", "1차", "전환권및상환권"], [5, 6])], note_title="상환전환우선주부채",
                   title="파생상품부채의 내역")
        facts = self.facts(t)
        self.assertEqual([(f["concept"], f["target"]) for f in facts][:1], [("EMB_DERIV_LIABILITY", "conversion")])

    def test_host_carrying_rows_and_rcps(self):
        t = _table([("전환사채의 장부금액", [10, 9]), ("기명식상환전환우선주", [20, 19]), ("상환전환우선주조정", [-2, -1])],
                   note_title="전환사채", title="사채 내역")
        self.assertEqual({f["label"] for f in _by(self.facts(t), "HOST_LIABILITY")},
                         {"전환사채의 장부금액", "기명식상환전환우선주", "상환전환우선주조정"})

    def test_host_series_name_when_label_is_date(self):
        t = _table([(["제1회 기명식 사모전환사채(플랜티팜)", "2023.03.10", "2027.03.09"], [100, 120])],
                   note_title="전환사채", title="미상환잔액", periods=("current_end", "prior_end"))
        self.assertEqual(len(_by(self.facts(t), "HOST_LIABILITY")), 2)

    def test_host_in_fair_value_note_needs_liability_context(self):
        liab = _table([("금융부채", [None, None]), ("전환사채", [10, 9])], note_title="금융상품의 공정가치", title="장부금액")
        asset = _table([("당기손익-공정가치금융자산", [None, None]), ("전환사채", [10, 9])], note_title="금융상품의 공정가치",
                       title="장부금액", tid="R:00760:t002")
        self.assertEqual({f["source"]["table_id"] for f in _by(self.facts(liab, asset), "HOST_LIABILITY")},
                         {"R:00760:t001"})

    def test_balance_ignores_percent_column_for_end_detection(self):
        t = _table([("사채할인발행차금", [None, -5, -7])], note_title="사채", title="전환사채 내역",
                   periods=("current_end", "current_period", "current_period"), headers=["이자율(%)", "유동성", "비유동성"],
                   overrides={1: {"raw": "%", "kind": "percent", "multiplier": 1, "currency": None}})
        t["rows"][0]["cells"][0]["value"] = 3.5
        facts = _by(self.facts(t), "HOST_LIABILITY")
        self.assertEqual({(f["amount"], f["period"]) for f in facts}, {(-5, "current_end"), (-7, "current_end")})

    def test_coverage_excludes_holder_side_and_conversion_flows(self):
        t1 = _table([("당기손익-공정가치측정금융자산", [None, None]), ("상환전환우선주", [5, 5])],
                    note_title="당기손익-공정가치측정금융자산", title="우선주 투자")
        t2 = _table([("전환사채의 전환", [5, 5])], note_title="자본금", title="자본금 변동", tid="R:00760:t002")
        cov = coverage([t1, t2], self.concepts)["conversion"]
        self.assertEqual(cov["kw_rows"], 0)

    def test_low_value_not_short_term(self):
        t = _table([("단기리스가 아닌 소액자산 리스료(관리비에 포함)", [3, 2])], note_title="리스", title="리스 비용",
                   periods=("current_period", "prior_period"))
        self.assertEqual({f["concept"] for f in self.facts(t)}, {"LEASE_LOW_VALUE_EXP"})

    def test_conversion_consideration_labelled_derivative_liability(self):
        t = _table([("- 전환권대가（파생상품부채）", [3, 2])], note_title="사채", title="전환사채")
        self.assertEqual({f["concept"] for f in self.facts(t)}, {"CONV_OPTION_LIABILITY"})

    def test_dev_cost_gross_cost_column_is_not_addition(self):
        t = _table([("개발비", [100, -40, 60])], note_title="무형자산", title="무형자산 내역",
                   periods=("current_end",) * 3, headers=["취득원가", "상각누계액", "장부금액"])
        self.assertEqual(_by(self.facts(t), "DEV_COST_ADDITION"), [])

    def test_fvoci_parent_label_with_share_child_is_equity(self):
        t = _table([(["기타포괄손익-공정가치측정금융자산", "비상장주식(주1)"], [1000, 3000])], note_title="금융상품",
                   title="내역")
        t["rows"][0]["label"] = "기타포괄손익-공정가치측정금융자산"  # 1층이 평평한 표에서 부모 칸을 대표로 고른 경우
        self.assertEqual({f["concept"] for f in self.facts(t)}, {"FVOCI_EQUITY"})

    def test_balance_skips_issue_date_column(self):
        t = _table([("전환권대가 등 (파생상품부채(자산))", [3999, -101, -53])], note_title="전환사채",
                   title="부채로 분류된 각 금융부채의 장부금액", periods=("unknown", "current_end", "prior_end"),
                   headers=["발행일", "당기말", "전기말"])
        self.assertEqual({f["period"] for f in self.facts(t)}, {"current_end", "prior_end"})

    def test_non_amount_columns_skipped(self):
        t = _table([("유동파생상품부채(전환사채)", [3, 500, 1200])], note_title="공정가치", title="가치평가기법",
                   periods=("current_end",) * 3, headers=["수준", "공정가치", "주당전환가격"])
        self.assertEqual([f["amount"] for f in self.facts(t)], [500])

    def test_coverage_keyword_scope_excludes_flows(self):
        t = _table([("전환권대가", [1, 2]), ("전환사채 이자비용", [3, 4]), ("유동파생상품부채(전환사채)", [1, 1])])
        cov = coverage([t], self.concepts)["conversion"]
        self.assertEqual((cov["kw_rows"], cov["kw_matched"]), (1, 1))


class FixRound1Test(unittest.TestCase):
    """T6 수정 1차(리뷰 지적·R10)."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_after_c2(self):  # c3에서 올림, 이후 판은 FixRound2Test 가 확인
        self.assertNotIn(self.concepts[0].version, ("c1", "c2"))

    # 지적 1: target 은 행 이름 → 경로 → 상위 행·각주 순, 근거 표시
    def test_target_specific_option_word_beats_instrument_name(self):
        t = _table([(["전환상환우선주", "조기상환청구권"], [5, 6]), (["전환사채", "콜옵션"], [1, 2])],
                   note_title="파생상품", title="파생상품 내역")
        got = {(f["concept"], f["amount"]): (f["target"], f["target_basis"]) for f in self.facts(t)}
        self.assertEqual(got[("EMB_DERIV_LIABILITY", 5)], ("redemption", "label"))
        self.assertEqual(got[("EMB_DERIV_ASSET", 1)], ("redemption", "label"))

    def test_target_non_convertible_instrument_label(self):
        t = _table([("비유동파생상품자산(신종자본증권)", [50, 40])], note_title="금융상품 공정가치",
                   title="전환사채 관련 파생상품")
        (f, _) = _by(self.facts(t), "EMB_DERIV_ASSET")
        self.assertEqual((f["target"], f["target_basis"]), ("other", "label"))

    def test_target_from_path_parent(self):
        t = _table([(["제3회 전환사채", "파생상품부채"], [5, 6])], note_title="사채", title="파생상품의 내역")
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual((f["target"], f["target_basis"]), ("conversion", "path"))

    def test_target_from_footnote_is_context(self):
        t = _table([("파생상품부채(*)", [5, 6])], note_title="공정가치", title="① 당기말")
        t["footnotes"] = [{"mark": "(*)", "text": "상환전환우선주부채 중 내재파생상품 분리금액입니다."}]
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual((f["target"], f["target_basis"]), ("conversion", "context"))

    def test_target_not_from_title_alone(self):
        # 일동 연결 t072 모양: 「전환사채 및 파생상품부채의 내역」 표의 「파생상품부채」
        t = _table([("유동:", [None, None]), ("전환사채", [0, 59520394]), ("파생상품부채", [0, 23816450])],
                   note_title="금융부채", title="① 당기말과 전기말 현재 전환사채 및 파생상품부채의 내역은 다음과 같습니다.",
                   unit=KRW1000)
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertIsNone(f["target"])
        self.assertIsNone(f["target_basis"])
        self.assertEqual(f["target_hint"], "conversion")

    def test_non_embedded_concepts_have_fixed_target_basis(self):
        t = _table([("전환권대가", [1, 2])])
        f = self.facts(t)[0]
        self.assertEqual((f["target"], f["target_basis"]), (None, None))

    # 지적 2: 값 빈 행이 머리 행이 되는 조건
    def test_blank_continuation_rows_are_not_headers(self):
        # 20260320001084 t047 모양(가치평가기법 표: 같은 이름의 빈 이어짐 행)
        rows = [("파생상품자산", [None]), ("유동파생상품자산(전환사채)", [1185896], 1), ("유동파생상품자산(전환사채)", [None], 1),
                ("유동파생상품자산(전환사채)", [None], 1), ("비유동파생상품자산(신종자본증권)", [50274958], 1),
                ("비유동파생상품자산(신종자본증권)", [None], 1)]
        t = _table(rows, note_title="금융상품 공정가치", title="가치평가기법과 투입변수", periods=("current_end",),
                   headers=["공정가치"])
        from dart_search_mcp.ledger.facts import _parents
        self.assertEqual(_parents(t["rows"])[4], ["파생상품자산"])
        got = {f["label"]: f["target"] for f in _by(self.facts(t), "EMB_DERIV_ASSET")}
        self.assertEqual(got["유동파생상품자산(전환사채)"], "conversion")
        self.assertNotEqual(got.get("비유동파생상품자산(신종자본증권)"), "conversion")

    def test_blank_row_followed_by_same_label_is_not_header(self):
        from dart_search_mcp.ledger.facts import _parents
        t = _table([("전환권", [None]), ("전환권", [5]), ("기타", [1])], periods=("current_end",), headers=["금액"])
        self.assertEqual(_parents(t["rows"])[2], [])

    # 지적 3: 부모 경로로만 걸린 소계·합계, 잔액 개념의 최초 인식 행
    def test_subtotal_not_matched_by_parent_only(self):
        t = _table([(["전환사채", "제2회차", "전환권 및 조기상환청구권"], [515, 720]), (["전환사채", "소계"], [1514, 1985])],
                   note_title="상환전환우선주부채", title="파생상품부채의 내역")
        t["rows"][1]["kind"] = "subtotal"
        self.assertEqual(_by(self.facts(t), "HOST_LIABILITY"), [])

    def test_initial_recognition_row_not_balance(self):
        t = _table([(["4회차 전환사채 파생상품부채", "최초 인식시점 장부금액"], [300, 300]),
                    (["4회차 전환사채 파생상품부채", "당기말 장부금액"], [120, 150])],
                   note_title="전환사채", title="파생상품부채")
        self.assertEqual({f["amount"] for f in _by(self.facts(t), "EMB_DERIV_LIABILITY")}, {120, 150})

    # 지적 4: 표 제목으로 구간 맞추기는 사채·전환 개념만, 법인세 등 주석 제외
    def test_title_region_only_for_instrument_concepts(self):
        lease = _table([("리스부채", [10, 9])], note_title="기타자산", title="리스부채 내역")
        tax = _table([("전환사채", [10, 9])], note_title="법인세비용", title="전환사채 관련 일시적차이", tid="R:00760:t002")
        host = _table([("전환사채", [10, 9])], note_title="기타부채", title="전환사채 내역", tid="R:00760:t003")
        risk = _table([("전환사채", [10, 9])], note_title="유동성 위험", title="금융부채별 계약상 만기", tid="R:00760:t004")
        seg = _table([("전환사채", [10, 9])], note_title="영업부문", title="2) 금융부채", tid="R:00760:t005")
        facts = self.facts(lease, tax, host, risk, seg)
        self.assertEqual({(f["concept"], f["source"]["table_id"]) for f in facts}, {("HOST_LIABILITY", "R:00760:t003")})
        self.assertEqual({f.get("region_basis") for f in facts}, {"title"})

    # 작은 것: 행 경로가 기간인 표
    def test_period_from_row_path(self):
        t = _table([(["당기말", "전환권대가"], [100]), (["전기말", "전환권대가"], [90])], periods=("unknown",),
                   headers=["금액"])
        self.assertEqual({(f["period"], f["amount"]) for f in self.facts(t)}, {("current_end", 100), ("prior_end", 90)})

    # c3 사전 보강(두 번째 조정 표본 tune2, seed 20261013)
    def test_c3_host_generic_instrument_rows(self):
        t = _table([("전환사채(액면가액)", [10, 9]), ("단기전환사채", [5, 4]), ("전환사채의 부채요소", [8, 7]),
                    ("㈜도이프 제1회 사모전환사채", [3, 3]), ("전환권조정 및 할인발행차금", [-1, -1]), ("주식할인발행차금", [-2, -2]),
                    ("전환사채 콜옵션", [1, 1]), ("전환우선주부채", [6, 6])], note_title="사채", title="사채 내역")
        got = {f["label"] for f in _by(self.facts(t), "HOST_LIABILITY")}
        self.assertEqual(got, {"전환사채(액면가액)", "단기전환사채", "전환사채의 부채요소", "㈜도이프 제1회 사모전환사채",
                               "전환권조정 및 할인발행차금", "전환우선주부채"})

    def test_c3_embedded_more_labels(self):
        t = _table([("전환상환우선주부채(파생상품)", [4, 4]), ("내재파생상품(제2차전환사채)", [3, 3]),
                    ("복합금융상품 매도청구권", [2, 2]), ("파생상품금융부채", [1, 1])], note_title="파생상품", title="파생상품 내역")
        got = {f["label"]: f["concept"] for f in self.facts(t)}
        self.assertEqual(got["전환상환우선주부채(파생상품)"], "EMB_DERIV_LIABILITY")
        self.assertEqual(got["내재파생상품(제2차전환사채)"], "EMB_DERIV_LIABILITY")
        self.assertEqual(got["복합금융상품 매도청구권"], "EMB_DERIV_ASSET")
        self.assertNotIn("파생상품금융부채", got)  # 문맥 없는 일반 파생상품은 사실로 내지 않는다(알려진 재현율 틈)

    def test_c3_lease_liability_variants_and_regions(self):
        t = _table([("리스부채의 현재가치", [1, 1]), ("장기리스부채", [2, 2]), ("부동산리스부채", [3, 3]),
                    ("유동성금융리스부채", [4, 4])], note_title="기타부채", title="기타부채 내역")
        self.assertEqual(len(_by(self.facts(t), "LEASE_LIABILITY")), 8)
        e = _table([("단기/소액자산리스료", [1, 1])], note_title="비용의 성격별 분류", title="성격별 비용",
                   periods=("current_period", "prior_period"))
        self.assertEqual({f["concept"] for f in self.facts(e)}, {"LEASE_SHORT_LOW_EXP"})

    def test_c3_fvoci_total_prefixes(self):
        t = _table([("단기기타포괄손익-공정가치측정금융자산", [1, 1]), ("장기기타포괄손익-공정가치측정금융자산", [2, 2])],
                   note_title="금융자산", title="금융자산")
        self.assertEqual(len(_by(self.facts(t), "FVOCI_FA_TOTAL")), 4)

    def test_c3_rnd_more_labels_and_intangible_note(self):
        t = _table([("경상연구개발비", [5, 4]), ("위탁연구비", [1, 1]), ("시험연구비", [1, 1]), ("시장개발비", [9, 9])],
                   note_title="무형자산", title="연구개발 지출", periods=("current_period", "prior_period"))
        self.assertEqual({f["label"] for f in _by(self.facts(t), "RND_EXPENSE")}, {"경상연구개발비", "위탁연구비", "시험연구비"})

    def test_c3_ip_period_columns_and_construction(self):
        t = _table([("기말순장부금액", [100, 90])], note_title="투자부동산", title="투자부동산 변동",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"])
        self.assertEqual({(f["period"], f["amount"]) for f in _by(self.facts(t), "IP_CARRYING")},
                         {("current_end", 100), ("prior_end", 90)})
        t2 = _table([("건설중인투자부동산", [5, 6])], note_title="투자부동산", title="장부금액",
                    periods=("current_end", "current_end"), headers=["장부금액", "공정가치"], tid="R:00760:t002")
        self.assertEqual({f["concept"] for f in self.facts(t2)}, {"IP_CARRYING", "IP_FAIR_VALUE"})

    def test_c3_bc_net_assets_total_row(self):
        t = _table([("순자산", [1294])], note_title="사업결합", title="취득한 자산과 인수한 부채의 공정가치",
                   periods=("unknown",), headers=["금액"])
        self.assertEqual({f["concept"] for f in self.facts(t)}, {"BC_NET_ASSETS_FV"})
        t2 = _table([("인수한 순자산 장부금액", [1000])], note_title="사업결합", title="공정가치",
                    periods=("unknown",), headers=["금액"], tid="R:00760:t002")
        self.assertEqual(self.facts(t2), [])

    def test_c3_tax_and_cashflow_notes_never_match(self):
        tax = _table([("리스부채", [10, 9]), ("전환권조정", [1, 1])], note_title="법인세비용 및 이연법인세", title="일시적차이")
        cf = _table([("리스부채", [10, 9])], note_title="현금흐름표", title="재무활동 부채", tid="R:00760:t002")
        self.assertEqual(self.facts(tax, cf), [])

    def test_c3_share_count_and_price_rows_are_not_amount_rows(self):
        t = _table([("제7회전환사채", [1000, 5000])], note_title="주당이익", title="잠재적보통주",
                   periods=("unknown", "unknown"), headers=["전환가능 주식수", "전환가격"])
        self.assertEqual(coverage([t], self.concepts)["conversion"]["kw_all_rows"], 0)

    def test_c3_host_in_category_table_by_column_context(self):
        t = _table([("전환사채", [10, 10]), ("전환사채(유동)", [5, 5])], note_title="금융상품의 범주", title="2) 전기말",
                   periods=("prior_end", "prior_end"), headers=["상각후원가측정 금융부채", "합계"])
        self.assertEqual(len(_by(self.facts(t), "HOST_LIABILITY")), 2)

    def test_c3_host_series_child_face_amount(self):
        t = _table([(["제1회 무보증사모전환사채", "액면금액"], [100, 100]), (["제1회 무보증사모전환사채", "전환가액"], [5, 5]),
                    (["파생상품부채(전환사채)", "장부금액"], [7, 7])], note_title="전환사채", title="전환사채의 내역")
        self.assertEqual({f["amount"] for f in _by(self.facts(t), "HOST_LIABILITY")}, {100})

    def test_c3_movement_change_column_not_inferred_balance(self):
        t = _table([("기타포괄손익-공정가치금융자산", [10, 3, 13])], note_title="기타자본구성요소", title="기타포괄손익누계액의 증감",
                   periods=("opening", "current_period", "current_period"), headers=["기 초", "증 감", "기 말"])
        self.assertEqual({(f["concept"], f["amount"], f["period"]) for f in self.facts(t)},
                         {("FVOCI_FA_RESERVE", 13, "current_end")})

    def test_c3_fvoci_reserve_loss_allowance(self):
        t = _table([("기타포괄손익-공정가치측정금융자산신용손실충당금", [1, 2])], note_title="기타포괄손익누계액", title="구성")
        self.assertEqual({f["concept"] for f in self.facts(t)}, {"FVOCI_DEBT_RESERVE"})

    def test_c3_lease_regions_and_ip_closing_column(self):
        t = _table([("리스료-단기리스", [1, 1])], note_title="차입금", title="리스 손익", periods=("current_period", "prior_period"))
        u = _table([("유동리스부채", [2, 2])], note_title="사용권자산", title="리스부채의 내역", tid="R:00760:t002")
        v = _table([("건설중인투자부동산", [1, 2, 3])], note_title="투자부동산", title="변동", tid="R:00760:t003",
                   periods=("opening", "current_period", "current_end"), headers=["기초", "취득", "기말"])
        got = {(f["concept"], f["amount"]) for f in self.facts(t, u, v)}
        self.assertIn(("LEASE_SHORT_TERM_EXP", 1), got)
        self.assertIn(("LEASE_LIABILITY", 2), got)
        self.assertIn(("IP_CARRYING", 3), got)

    def test_c3_fvoci_category_gain_rows_excluded_by_named_rule(self):
        t = _table([("기타포괄손익-공정가치측정금융자산평가손익", [3, 2])], note_title="범주별 금융상품", title="범주별 순손익",
                   periods=("current_period", "prior_period"))
        cov = coverage([t], self.concepts)["fvoci"]
        self.assertEqual(cov["excluded_by_rule"], {"oci_flow_in_category_table": 1})

    # R10: 정의 B 제외 규칙 이름·되돌린 값, 내재파생 문맥 없는 행은 미매칭으로 셈
    def test_coverage_named_exclusions_and_restored(self):
        t = _table([("전환권대가", [1, 2]), ("전환사채 이자비용", [3, 4]), ("유동파생상품부채(전환사채)", [1, 1])])
        cov = coverage([t], self.concepts)["conversion"]
        self.assertEqual((cov["kw_rows"], cov["kw_matched"]), (1, 1))
        self.assertEqual((cov["kw_all_rows"], cov["kw_all_matched"]), (3, 1))
        self.assertEqual(cov["excluded_by_rule"], {"flow_row": 1, "derivative_other_topic": 1})

    def test_embedded_bare_derivative_counts_as_unmatched(self):
        t = _table([("파생상품부채", [5, 6]), ("통화선도 파생상품부채", [1, 1])], note_title="금융상품",
                   title="범주별 금융상품")
        un = {}
        cov = coverage([t], self.concepts, unmatched=un)["embedded"]
        self.assertEqual((cov["kw_rows"], cov["kw_matched"]), (1, 0))
        self.assertEqual(un["embedded"], Counter({"파생상품부채": 1}))
        self.assertEqual(cov["excluded_by_rule"], {"hedging_derivative": 1})

    def test_topic_exclusions_are_named_with_reasons(self):
        topics = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))["topics"]
        for name, spec in topics.items():
            for rule in spec.get("exclusions", []):
                self.assertTrue(rule["name"] and rule["pattern"] and rule["why"], (name, rule))
                self.assertIn(rule.get("on", "row"), ("row", "context"))
            self.assertNotIn("kw_exclude", spec)
            self.assertNotIn("require_context", spec)


class FixRound2Test(unittest.TestCase):
    """T6 수정 2차."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_c5_or_later(self):
        self.assertRegex(self.concepts[0].version, r"^c([5-9]|\d\d)$")

    def test_fvpl_equity(self):
        # T10: FVOCI 「지정 안 함」 판정용 당기손익-공정가치 지분상품 개념(c5)
        t = _table([("당기손익-공정가치측정 지분상품", [100, 90]), ("기타포괄손익-공정가치측정 지분상품", [50, 40]),
                    ("당기손익-공정가치측정금융자산(전환사채)", [7, 7]), ("당기손익-공정가치측정 채무상품", [3, 3]),
                    ("당기손익-공정가치측정 지분상품 평가이익", [1, 1])], note_title="금융상품", title="범주별")
        got = {(f["label"], f["concept"]) for f in self.facts(t)}
        self.assertIn(("당기손익-공정가치측정 지분상품", "FVPL_EQUITY"), got)
        self.assertIn(("기타포괄손익-공정가치측정 지분상품", "FVOCI_EQUITY"), got)
        self.assertEqual({lb for lb, c in got if c == "FVPL_EQUITY"}, {"당기손익-공정가치측정 지분상품"})

    def test_plain_bond_face_and_carrying_not_host(self):
        # 20260402000760 t061 「(1) 사채내역」(FRN), 20260422000648 t047 「제1회 무보증사모사채」 모양
        t1 = _table([(["제1회 FRN 사채", "액면가액"], [35256000, 35256000]), (["제1회 FRN 사채", "장부가액"], [35156422, 35034394])],
                    note_title="사채", title="(1) 사채내역")
        t2 = _table([("제1회 무보증사모사채", [None, None]), ("액면금액", [2000000, 2000000]), ("장부금액", [1978000, 1970000]),
                     ("사채할인발행차금", [-22000, 0]), ("차감: 사채할인발행차금", [-1, -2])],
                    note_title="사채", title="사채 내역", tid="R:00760:t002")
        self.assertEqual(_by(self.facts(t1, t2), "HOST_LIABILITY"), [])

    def test_convertible_series_face_still_host(self):
        t = _table([(["제1회 무보증사모전환사채", "액면금액"], [100, 100])], note_title="사채", title="사채 내역")
        self.assertEqual(len(_by(self.facts(t), "HOST_LIABILITY")), 2)

    def test_embedded_asset_rows_not_liability(self):
        t = _table([("내재파생상품자산", [3, 3]), ("당기손익-공정가치측정금융자산(파생상품)", [2, 2]),
                    ("내재파생상품부채", [4, 4]), ("상환전환우선주부채(파생상품)", [5, 5])],
                   note_title="파생상품", title="전환사채 관련 파생상품")
        got = {f["label"]: f["concept"] for f in self.facts(t)}
        self.assertNotEqual(got.get("내재파생상품자산"), "EMB_DERIV_LIABILITY")
        self.assertNotEqual(got.get("당기손익-공정가치측정금융자산(파생상품)"), "EMB_DERIV_LIABILITY")
        self.assertEqual(got["내재파생상품부채"], "EMB_DERIV_LIABILITY")
        self.assertEqual(got["상환전환우선주부채(파생상품)"], "EMB_DERIV_LIABILITY")

    def test_target_default_basis(self):
        t = _table([("파생상품부채", [5, 6])], note_title="사채", title="파생상품의 내역")
        t["rows"][0]["label_path"] = ["파생상품부채"]
        t["footnotes"] = []
        # 문맥은 상위 행에서 「사채」만(옵션·상품 낱말 없음) → 대상 모름
        t["rows"].insert(0, {"row": 0, "label": "사채 관련", "label_path": ["사채 관련"], "level": 0, "acode": None,
                             "kind": "item", "cells": [{"col": 1, "raw": "", "value": None, "value_krw": None,
                                                        "unit_override": None}]})
        t["rows"][1]["level"] = 1
        (f, _) = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual((f["target"], f["target_basis"]), ("other", "default"))


_T6B = json.loads((Path(__file__).parent / "fixtures" / "ledger" / "t6b_tables.json").read_text(encoding="utf-8"))


def _real(tid):
    return json.loads(json.dumps(_T6B[tid]))  # 1층 표 기록 그대로(dart_collected/tables 에서 옮김), 테스트마다 사본


class T6bTest(unittest.TestCase):
    """T6b: FVOCI_EQUITY 표 제목 합계 행, DEV_COST_ADDITION 내부창출 열(c6)."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables, stats=None):
        return extract_facts(list(tables), self.concepts, stats=stats)

    def test_version_c8_or_later(self):  # c6 = T6b, c7 = T6b 수정 1차(R25), c8 = R27, c9 = T11b(T11bEmbRecallTest)
        self.assertRegex(self.concepts[0].version, r"^c([89]|\d\d)$")

    # ---- 1. FVOCI_EQUITY 표 제목 + 합계 행 ----
    def test_real_title_equity_total_row(self):
        # 20260319001100 t073 「(1) 기타포괄손익-공정가치 측정 지분상품」: 행 = 투자처 이름, 「계」 행
        got = _by(self.facts(_real("20260319001100:00760:t073")), "FVOCI_EQUITY")
        self.assertEqual({(f["period"], f["amount"], f["amount_krw"]) for f in got},
                         {("current_end", 1607496, 1607496000), ("prior_end", 1611093, 1611093000)})
        self.assertTrue(all(f["label"] == "계" and f["match_basis"] == "table_total" for f in got))

    def test_real_subheading_equity_total_row(self):
        # 20260319001078 t043: 제목은 「기타포괄손익-공정가치측정금융자산」, 바로 아래 소제목 행 「상장지분상품」.
        # 열 = 취득원가(안 씀) / 공정가치(기간 모름 — 기간 열이 있으므로 안 씀) / 당기 / 전기
        got = _by(self.facts(_real("20260319001078:00760:t043")), "FVOCI_EQUITY")
        self.assertEqual({(f["period"], f["amount_krw"]) for f in got},
                         {("current_end", 509200000), ("prior_end", 456000000)})
        self.assertTrue(all(f["period_inferred"] for f in got))
        self.assertNotIn(938271605, {f["amount"] for f in got})

    def _equity(self, rows, title="(1) 기타포괄손익-공정가치 측정 지분상품", **kw):
        kw.setdefault("note_title", "기타포괄손익-공정가치측정금융자산")
        kw.setdefault("headers", ["당기말", "전기말"])
        return _by(self.facts(_table(rows, title=title, **kw)), "FVOCI_EQUITY")

    def test_hand_title_equity_total(self):
        got = self._equity([("상장주식", [10, 8]), ("비상장주식", [5, 5]), ("합 계", [15, 13])])
        self.assertEqual({(f["period"], f["amount"]) for f in got}, {("current_end", 15), ("prior_end", 13)})

    def test_debt_title_no_fact(self):
        rows = [("국공채", [10, 8]), ("회사채", [5, 5]), ("합계", [15, 13])]
        self.assertEqual(self._equity(rows, title="(2) 기타포괄손익-공정가치 측정 채무상품"), [])
        rows = [("A사 주식", [10, 8]), ("B사 주식", [5, 5]), ("합계", [15, 13])]
        # c12: 합계 행은 쓰지 않는다. 다만 FVOCI 주석 안의 지분 행(「A사 주식」)은 행 사실이 된다(성우하이텍 s056)
        got = self._equity(rows, title="기타포괄손익-공정가치 측정 지분상품 및 채무상품")
        self.assertEqual([f for f in got if f.get("match_basis") == "table_total"], [])
        self.assertEqual({f["label"] for f in got}, {"A사 주식", "B사 주식"})
        self.assertEqual(self._equity(rows, title="당기손익-공정가치 및 기타포괄손익-공정가치 측정 지분상품"), [])

    def test_debt_row_in_equity_titled_table_no_fact(self):
        # 20260311004362 t082 모양: 제목은 지분증권 지정, 행에 「채무증권」 → 합계가 채무를 포함
        rows = [("시장성 없는 지분증권", [10, 8]), ("채무증권", [5, 5]), ("합 계", [15, 13])]
        # c12: 합계(채무 포함)는 쓰지 않고, 지분 행만 행 사실이 된다
        got = self._equity(rows, title="장기로 보유할 지분증권을 기타포괄손익-공정가치 측정 항목으로 지정하였습니다.")
        self.assertEqual({(f["label"], f["amount"]) for f in got}, {("시장성 없는 지분증권", 10), ("시장성 없는 지분증권", 8)})

    def test_fvpl_section_row_no_fact(self):
        # 20260311004398 t112 모양: 소제목은 FVOCI 이지만 같은 표에 당기손익-공정가치 구획
        rows = [("기타포괄손익-공정가치측정금융상품:", [None, None]), ("A사", [10, 8]), ("당기손익-공정가치측정금융상품:", [None, None]),
                ("B사", [5, 5]), ("합 계", [15, 13])]
        self.assertEqual(self._equity(rows, title="(2) 시장성 없는 지분증권"), [])

    def test_dividend_or_gain_title_no_fact(self):
        # 20260331004180 t089 모양: 배당금수익 표(흐름)
        rows = [("주식", [19, 19]), ("기타유가증권", [281, 2910]), ("합계", [300, 2929])]
        self.assertEqual(self._equity(rows, title="(5) 기타포괄손익-공정가치측정 항목으로 지정한 지분상품으로부터 인식한 배당금수익",
                                      periods=("current_period", "prior_period"), headers=["당기", "전기"]), [])
        self.assertEqual(self._equity(rows, title="기타포괄손익-공정가치 측정 지분상품 평가손익"), [])

    def test_acquisition_cost_column_no_fact(self):
        rows = [("A사", [100, 90]), ("B사", [50, 40]), ("합계", [150, 130])]
        got = self._equity(rows, periods=("current_end", "current_end"), headers=[["당기말", "취득원가"], ["당기말", "평가손익"]])
        self.assertEqual(got, [])
        # 위 단계가 취득원가인 기간 열(「취득원가 / 당기말」)도 안 쓴다
        got = self._equity(rows, headers=[["취득원가", "당기말"], ["취득원가", "전기말"]])
        self.assertEqual(got, [])
        # 취득원가·장부금액이 함께면 장부금액만
        got = self._equity(rows, periods=("current_end", "current_end"), headers=[["당기말", "취득원가"], ["당기말", "장부금액"]])
        self.assertEqual([(f["column"], f["amount"]) for f in got], [("장부금액", 130)])

    def test_later_non_equity_section_no_fact(self):
        # 20260414002701 t059 모양: 첫 소제목 「비상장주식」 뒤에 「수익증권」 구획 — 전기 합계 12,175,691 에 수익증권 10,002,600 포함
        title = "9. 기타포괄손익-공정가치 측정 금융자산 9.1 기타포괄손익-공정가치 측정 금융자산의 내역은 다음과 같습니다."
        rows = [("비상장주식", [None, None]), (["비상장주식", "㈜레저플러스"], [2000000, 2000000], 3),
                (["비상장주식", "전문건설공제조합"], [173091, 173091], 3), ("수익증권", [None, None]),
                (["수익증권", "파인앤파트너스 일반사모부동산 제5호 투자유한회사"], [0, 10002600], 3), ("합계", [2173091, 12175691])]
        self.assertEqual(self._equity(rows, title=title), [])
        # 20260318001659 t099 모양: 뒤쪽 「매각예정자산」 구획
        rows = [("상장주식", [None, None]), ("㈜A", [10, 20]), ("비상장주식", [5, 5]), ("매각예정자산", [None, None]),
                ("비상장주식(주2)", [0, 7]), ("합 계", [15, 32])]
        # c12: 합계는 쓰지 않는다. 지분 행 「비상장주식」은 행 사실, 매각예정 구획 아래 행은 아니다
        got = self._equity(rows, title="기타포괄손익-공정가치 측정 금융자산 내역")
        self.assertEqual({(f["label"], f["amount"]) for f in got}, {("비상장주식", 5)})
        # 뒤쪽 구획이 지분도 중립도 아니면(「기타」) 사실 없음
        rows = [("상장주식", [None, None]), ("㈜A", [10, 20]), ("기타", [None, None]), ("B", [1, 1]), ("합계", [11, 21])]
        self.assertEqual(self._equity(rows, title="기타포괄손익-공정가치 측정 금융자산 내역"), [])
        # 지분 구획 둘(상장·비상장)과 중립 머리(비유동항목)는 그대로 사실
        rows = [("비유동항목", [None, None]), ("상장주식", [None, None]), ("㈜A", [10, 20]), ("비상장주식", [None, None]),
                ("㈜B", [1, 1]), ("합계", [11, 21])]
        got = self._equity(rows, title="기타포괄손익-공정가치 측정 지분상품")
        self.assertEqual({(f["period"], f["amount"]) for f in got}, {("current_end", 11), ("prior_end", 21)})

    def test_neutral_section_headers_keep_fact(self):
        # 20260318000626 t075 「비유동금융자산」, 20260312000856 t053 「(국내법인)」「(해외법인)」 구획 머리는 중립
        for heads in (["비유동금융자산"], ["(국내법인)", "(해외법인)"], ["기타비유동금융자산"]):
            rows = []
            for h in heads:
                rows += [(h, [None, None]), (f"{h} 소속 ㈜A", [10, 8])]
            rows.append(("합계", [10 * len(heads), 8 * len(heads)]))
            got = self._equity(rows, title="기타포괄손익-공정가치 측정 금융자산 중 비상장주식")
            self.assertEqual({f["amount"] for f in got}, {10 * len(heads), 8 * len(heads)}, heads)

    def test_non_equity_item_words_no_fact(self):
        for word in ("수익증권", "국채", "금융채", "특수채", "CP", "기업어음", "신탁수익권", "파생상품"):
            rows = [("㈜A 주식", [10, 8]), (word, [5, 5]), ("합계", [15, 13])]
            self.assertEqual(self._equity(rows), [], word)

    def test_cp_inside_company_name_keeps_fact(self):
        # 20260414002601 t062: 투자처 「TPC POWER」 의 CP 는 기업어음이 아니다
        got = self._equity([("TPC POWER", [10, 8]), ("㈜B", [5, 5]), ("합계", [15, 13])])
        self.assertEqual({f["amount"] for f in got}, {15, 13})

    def test_face_net_asset_capital_columns_no_fact(self):
        rows = [("㈜A", [10, 8]), ("㈜B", [5, 5]), ("합계", [15, 13])]
        for head in ("액면금액", "순자산", "자본금"):
            got = self._equity(rows, periods=("current_end", "current_end"), headers=[["당기말", head], [head, "당기말"]])
            self.assertEqual(got, [], head)

    def test_multi_row_without_total_no_fact(self):
        rows = [("A사", [100, 90]), ("B사", [50, 40]), ("C사", [5, 4])]
        self.assertEqual(self._equity(rows), [])

    def test_single_row_table(self):
        # 20260331000648 t055 모양: 소제목 「지분증권」 + 투자처 한 행, 합계 없음
        got = self._equity([("지분증권", [None, None]), ("ALTAVA GROUP", [381588, 425025])],
                           title="(1) 기타포괄손익-공정가치측정금융자산의 상세 내역")
        self.assertEqual({(f["period"], f["amount"], f["match_basis"]) for f in got},
                         {("current_end", 381588, "table_single_row"), ("prior_end", 425025, "table_single_row")})

    def test_title_needs_both_fvoci_and_equity(self):
        rows = [("A사", [100, 90]), ("B사", [50, 40]), ("합계", [150, 130])]
        self.assertEqual(self._equity(rows, title="(1) 기타포괄손익-공정가치측정금융자산의 내역"), [])
        self.assertEqual(self._equity(rows, title="(1) 지분상품의 내역", note_title="장기투자증권"), [])

    def test_fair_value_hierarchy_title_no_fact(self):
        rows = [("A사", [10, 8]), ("B사", [3, 2]), ("합계", [13, 10])]
        self.assertEqual(self._equity(rows, title="기타포괄손익-공정가치 측정 지분상품의 공정가치 서열체계",
                                      note_title="공정가치"), [])

    def test_row_matched_equity_table_not_duplicated(self):
        rows = [("기타포괄손익-공정가치측정 지분상품", [10, 8]), ("합계", [10, 8])]
        got = self._equity(rows)
        self.assertEqual({f["label"] for f in got}, {"기타포괄손익-공정가치측정 지분상품"})

    def test_fa_total_row_in_equity_titled_table_becomes_equity(self):
        t = _table([("기타포괄손익-공정가치측정금융자산", [10, 8])], title="(1) 기타포괄손익-공정가치 측정 지분상품",
                   note_title="금융자산", headers=["당기말", "전기말"])
        got = self.facts(t)
        self.assertEqual({f["concept"] for f in got}, {"FVOCI_EQUITY"})

    # ---- 2. DEV_COST_ADDITION 내부창출 열 ----
    def test_real_internal_generation_column(self):
        # 20260319000795 t058: 개발비 행 × 「내부창출취득」 181,190(천원)
        got = _by(self.facts(_real("20260319000795:00760:t058")), "DEV_COST_ADDITION")
        self.assertIn(("내부창출취득", 181190, "current_period"), {(f["column"], f["amount"], f["period"]) for f in got})
        self.assertNotIn("상각비(*1)", {f["column"] for f in got})

    def test_addition_column_words(self):
        heads = ["내부창출", "자본화", "개발비대체", "대체(증가)", "대체", "대체(감소)", "감소", "처분", "손상"]
        t = _table([("개발비", [1, 2, 3, 4, -5, -6, -7, -8, -9])], note_title="무형자산", title="무형자산의 변동",
                   periods=("current_period",) * len(heads), headers=heads)
        got = {f["column"]: f["amount"] for f in _by(self.facts(t), "DEV_COST_ADDITION")}
        self.assertEqual(got, {"내부창출": 1, "자본화": 2, "개발비대체": 3, "대체(증가)": 4})

    def test_addition_row_words(self):
        t = _table([("내부창출", [5]), ("대체", [-3])], note_title="무형자산", title="무형자산의 변동",
                   periods=("current_period",), headers=["개발비"])
        got = {f["label"]: f["amount"] for f in _by(self.facts(t), "DEV_COST_ADDITION")}
        self.assertEqual(got, {"내부창출": 5})


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
            self.assertEqual(summary["filings_missing"], 1)
            cov = summary["coverage"]["conversion"]
            for k in ("ratio_all_rows", "ratio_keyword_rows", "ratio_keyword_restored", "excluded_by_rule"):
                self.assertIn(k, cov)
            self.assertIn("conversion", summary["coverage"])
            self.assertTrue((out / "facts.unmatched.tsv").read_text(encoding="utf-8").startswith("count\tlabel"))
            # 다시 돌리면 덮지 않고 멈춘다
            self.assertEqual(ledger_facts.main(["--tables", str(tables), "--out-dir", str(out), "--workers", "1"]), 2)

    def test_runner_adds_sentence_facts(self):
        # c11: 실행기는 표 사실에 문장 사실(투자부동산 공정가치)을 더한다
        from dart_search_mcp.ledger.tables import write_filing
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("ledger_facts_cli", Path(__file__).parents[1] / "scripts" / "ledger_facts.py")
        ledger_facts = importlib.util.module_from_spec(spec)
        sys.modules["ledger_facts_cli"] = ledger_facts
        spec.loader.exec_module(ledger_facts)
        with tempfile.TemporaryDirectory() as d:
            filing = {"rcept_no": "20260312001016", "amends_rcept_no": None, "parser_version": "ledger-t3", "tables": [],
                      "paragraphs": [{"id": "s382", "file": "20260312001016_00760.xml", "page": 14,
                                      "text": "(4) 보고기간말 현재 투자부동산의 공정가치는 11,900,000천원입니다."}]}
            write_filing(Path(d), "20260312001016", filing)
            ledger_facts._init(None)
            res = ledger_facts.process(("20260312001016", d))
            self.assertIsNone(res["error"])
            self.assertEqual([(f["concept"], f["amount_krw"], f["source"]["sentence_id"]) for f in res["facts"]],
                             [("IP_FAIR_VALUE", 11900000000, "s382")])



class R27LeaseExcludingShortTermTest(unittest.TestCase):
    """R27: 「소액자산 리스 중 단기리스를 제외한 …」은 소액자산 리스료 행이다(단기·소액 합친 행 아님, c8)."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *rows):
        t = _table(list(rows), note_title="리스", title="리스 비용", periods=("current_period", "prior_period"))
        return {f["label"]: f["concept"] for f in extract_facts([t], self.concepts)}

    def test_real_taelim_rows(self):
        # 20260319001237 주석 33 리스(연결): 단기리스 비용 행과 소액자산 리스 비용 행이 따로 있다
        got = self.facts(("단기리스와 관련된 비용", [623569, 753347]),
                         ("소액자산 리스 중 단기리스를 제외한 리스에 관련된 비용", [6210514, 4753039]))
        self.assertEqual(got, {"단기리스와 관련된 비용": "LEASE_SHORT_TERM_EXP",
                               "소액자산 리스 중 단기리스를 제외한 리스에 관련된 비용": "LEASE_LOW_VALUE_EXP"})

    def test_excluding_short_term_variants(self):
        got = self.facts(("단기리스를 제외한 소액자산 리스료", [1, 1]), ("소액자산리스료(단기리스 제외)", [2, 2]),
                         ("소액자산 리스료(단기리스는 제외)", [3, 3]),
                         ("소액자산 중 단기리스를 제외한 리스에 관련된 비용", [4, 4]))  # c7 합친 행, 「리스」 낱말 앞에 「중」
        self.assertEqual(set(got.values()), {"LEASE_LOW_VALUE_EXP"})
        self.assertEqual(len(got), 4)

    def test_genuine_combined_rows_stay_combined(self):
        got = self.facts(("단기리스 및 소액자산리스료", [4, 3]), ("단기 및 소액리스료(매출원가 및 판매비와관리비)", [5, 5]))
        self.assertEqual(set(got.values()), {"LEASE_SHORT_LOW_EXP"})
        self.assertEqual(len(got), 2)

    def test_excluding_both_is_not_combined(self):
        # 「단기리스 및 소액자산리스를 제외한 …」은 면제 리스료가 아니다(합친 행으로 읽으면 안 됨)
        got = self.facts(("단기리스 및 소액자산리스를 제외한 리스료", [7, 7]),
                         ("단기리스및소액자산리스를제외한 리스의 총현금유출", [8, 8]))
        self.assertNotIn("LEASE_SHORT_LOW_EXP", set(got.values()))
        self.assertNotIn("LEASE_LOW_VALUE_EXP", set(got.values()))


_T11B = json.loads((Path(__file__).parent / "fixtures" / "ledger" / "t11b_tables.json").read_text(encoding="utf-8"))


class T11bEmbRecallTest(unittest.TestCase):
    """T11b: 수준 3 공정가치 표의 전환사채·우선주 내재파생상품부채 행(f-kemtec-emb·f-baropharm-emb, c9)."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def real(self, tid):
        return json.loads(json.dumps(_T11B[tid]))  # 1층 표 기록 그대로(dart_collected/tables 에서 옮김)

    def test_version_c9_or_later(self):  # c10 = T11b 수정 1차(T11bFix1Test)
        self.assertRegex(self.concepts[0].version, r"^c(9|\d\d)$")

    def test_real_kemtec_embedded_financial_derivative_liability(self):
        # 케이이엠텍 「내재파생금융상품부채 2,928,440」(천원) — 「금융」이 끼어 c8 「내재파생(상품)?부채」에 안 걸렸다
        for tid in ("20260408000011:00761:t060", "20260408000011:00760:t039"):
            # 「수준」 열은 머리글이 [수준, 구획 이름] 순서여도 금액 열이 아니다(T11b 수정 1차, c10)
            got = _by(self.facts(self.real(tid)), "EMB_DERIV_LIABILITY")
            self.assertEqual([(f["period"], f["amount_krw"], f["label"]) for f in got],
                             [("current_end", 2_928_440_000, "내재파생금융상품부채")])
            # 행·경로·문맥에 대상 낱말이 없다 → 대상 모름(other/default), 판정은 전환권이 아니라고 보지 않는다
            self.assertEqual((got[0]["target"], got[0]["target_basis"]), ("other", "default"))
            self.assertEqual(_by(self.facts(self.real(tid)), "EMB_DERIV_ASSET"), [])

    def test_real_baropharm_rcps_embedded_derivative(self):
        # 바로팜 「전환상환우선주 내재파생상품 47,171,059」(천원), 값 빈 머리 행 「파생상품부채」 아래
        got = _by(self.facts(self.real("20260211000330:00760:t063")), "EMB_DERIV_LIABILITY")
        self.assertEqual([(f["period"], f["amount_krw"]) for f in got], [("current_end", 47_171_059_000)])
        self.assertEqual((got[0]["target"], got[0]["target_basis"]), ("conversion", "label"))
        # 주계약 행은 내재파생이 아니다
        self.assertFalse(any("주계약" in f["label"] for f in self.facts(self.real("20260211000330:00760:t063"))
                             if f["concept"].startswith("EMB")))

    def test_real_dasan_already_extracted(self):
        # 다산솔루에타 「전환사채의 내재파생부채 87,300」(천원): c8 에서도 사실이었다(판정이 안 본 것이 원인)
        got = [f for f in _by(self.facts(self.real("20260324000018:00760:t150")), "EMB_DERIV_LIABILITY")
               if f["label"] == "전환사채의 내재파생부채"]
        self.assertEqual({(f["period"], f["amount_krw"], f["target"]) for f in got},
                         {("current_end", 87_300_000, "conversion"), ("prior_end", 1_711_500_000, "conversion")})

    def test_instrument_embedded_derivative_under_asset_header_not_liability(self):
        t = _table([("파생상품자산", [None, None]), ("전환사채 내재파생상품", [3, 3]),
                    ("파생상품부채", [None, None]), ("상환전환우선주 내재파생상품", [4, 4])],
                   note_title="금융상품 공정가치", title="수준 3 공정가치")
        got = {f["label"]: f["concept"] for f in self.facts(t)}
        self.assertNotEqual(got.get("전환사채 내재파생상품"), "EMB_DERIV_LIABILITY")
        self.assertEqual(got["상환전환우선주 내재파생상품"], "EMB_DERIV_LIABILITY")

    def test_hedging_and_valuation_rows_still_excluded(self):
        t = _table([("내재파생금융상품부채평가이익", [1, 1]), ("파생금융상품부채(통화스왑)", [2, 2]),
                    ("이자율스왑 내재파생금융상품부채", [3, 3]), ("전환사채 내재파생상품평가손실", [4, 4]),
                    ("통화선도 내재파생상품", [5, 5])],
                   note_title="파생상품", title="전환사채 관련 파생상품")
        self.assertEqual([f for f in self.facts(t) if f["concept"].startswith("EMB")], [])


class T11bFix1Test(unittest.TestCase):
    """T11b 수정 1차(c10): 자산 열의 「<상품> 내재파생상품」, 머리글 어느 단계든 「수준」인 열."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_c10_or_later(self):  # c11 = 리스·투자부동산 수정(LeaseIpFixC11Test)
        self.assertIn(self.concepts[0].version, ("c10", "c11", "c12", "c13", "c15"))

    def test_instrument_embedded_derivative_in_asset_column_not_liability(self):
        # 20260323001209 t106 모양: 열이 [당기말, 파생상품자산] / [당기말, 파생상품부채]
        t = _table([("전환사채 내재파생상품", [3, 4])], note_title="금융상품 공정가치", title="수준 3 공정가치",
                   periods=("current_end", "current_end"), headers=[["당기말", "파생상품자산"], ["당기말", "파생상품부채"]])
        got = [(f["concept"], f["source"]["col"]) for f in self.facts(t) if f["concept"].startswith("EMB")]
        self.assertNotIn(("EMB_DERIV_LIABILITY", 1), got)
        self.assertIn(("EMB_DERIV_LIABILITY", 2), got)
        # 열 이름이 「자산」 하나뿐이어도(20260324000733 t107 모양) 부채가 아니다
        t = _table([("상환전환우선주 내재파생상품", [5, 6])], note_title="파생상품", title="파생상품 부채 및 자산",
                   periods=("current_end", "prior_end"), headers=[["당기말", "자산"], ["전기말", "자산"]])
        self.assertEqual(_by(self.facts(t), "EMB_DERIV_LIABILITY"), [])
        # 「금융자산ㆍ부채」처럼 부채가 같이 있는 열은 그대로
        t = _table([("상환전환우선주 내재파생상품", [7, 8])], note_title="파생상품", title="파생상품 부채",
                   headers=[["금융자산ㆍ부채", "당기말"], ["금융자산ㆍ부채", "전기말"]])
        self.assertEqual(len(_by(self.facts(t), "EMB_DERIV_LIABILITY")), 2)

    def test_level_column_any_header_element_not_amount(self):
        t = _table([("내재파생상품부채", [2928440, 3])], note_title="금융상품 공정가치", title="전환사채 수준 3",
                   periods=("current_end", "current_end"),
                   headers=[["공정가치", "당기손익-공정가치 측정 금융부채"], ["수준", "당기손익-공정가치 측정 금융부채"]])
        got = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual([(f["source"]["col"], f["amount"]) for f in got], [(1, 2928440)])
        for lv in ("Level", "서열"):
            t["columns"][2]["header_path"] = [lv, "x"]
            self.assertEqual(len(_by(self.facts(t), "EMB_DERIV_LIABILITY")), 1, lv)

    def test_level_number_columns_still_amounts(self):
        # 수준 1·2·3 열(「수준1」)과 그 위 머리글은 금액 열 그대로 — 합계 열 하나만 쓰는 규칙도 그대로
        t = _table([("내재파생상품부채", [0, 0, 9, 9])], note_title="금융상품 공정가치", title="전환사채 공정가치 서열",
                   periods=("current_end",) * 4,
                   headers=[["공정가치 서열", "수준1"], ["공정가치 서열", "수준2"], ["공정가치 서열", "수준3"], ["공정가치 서열", "합계"]])
        got = _by(self.facts(t), "EMB_DERIV_LIABILITY")
        self.assertEqual([(f["source"]["col"], f["amount"]) for f in got], [(4, 9)])
        # 「수 준」이 수준1·2·3·합계 위 묶음 머리글이면 합계 열은 금액이다(20260316001039 t197 모양)
        t = _table([("기타포괄손익-공정가치측정 금융자산", [761512, 0, 2290, 763802])], note_title="범주별 금융상품",
                   title="(당기말)", periods=("current_end",) * 4,
                   headers=[["(단위: 백만원)", "수 준", h] for h in ("수준1", "수준2", "수준3", "합 계")])
        got = _by(self.facts(t), "FVOCI_FA_TOTAL")
        self.assertEqual([(f["source"]["col"], f["amount"]) for f in got], [(4, 763802)])


class LeaseIpFixC11Test(unittest.TestCase):
    """회계사 v2 표본 검토(10-15) 리스·투자부동산 수정(c11) — 실제 표본의 표·문장 조각."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_c11_or_later(self):
        meta = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
        self.assertIn(meta["concepts_version"], ("c11", "c12", "c13", "c15"))

    def test_starflex_low_value_recognition_exemption_row(self):
        # s088 스타플렉스 20260323001102 주석 13 사용권자산(연결, 천원): 소액기초자산 인식면제 행을 못 읽어 SHORT_TERM 이었다
        t = _table([("단기리스 인식면제를 적용한 리스료", [14435, 7350]),
                    ("소액기초자산 인식면제를 적용한 리스료", [8240, 8200])],
                   note_title="사용권자산", title="(2) 당기와 전기 중 리스와 관련하여 인식한 비용의 내역은 다음과 같습니다.",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"], unit=KRW1000)
        got = {(f["label"], f["concept"], f["amount"]) for f in self.facts(t) if f["period"] == "current_period"}
        self.assertEqual(got, {("단기리스 인식면제를 적용한 리스료", "LEASE_SHORT_TERM_EXP", 14435),
                               ("소액기초자산 인식면제를 적용한 리스료", "LEASE_LOW_VALUE_EXP", 8240)})

    def test_seongbo_low_value_not_short_term_row(self):
        # s096 성보산업 20260402003142 주석 13 리스(천원): 「단기리스료가 아닌 소액자산 리스료」는 소액 행(합친 행 아님)
        t = _table([("단기리스료", [0, 0], 3), ("단기리스료가 아닌 소액자산 리스료", [4830, 4830], 3)],
                   note_title="리스", title="(3) 리스와 관련해서 손익계산서에 인식될 금액은 다음과 같습니다.",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"], unit=KRW1000)
        got = {f["label"]: f["concept"] for f in self.facts(t)}
        self.assertEqual(got, {"단기리스료": "LEASE_SHORT_TERM_EXP",
                               "단기리스료가 아닌 소액자산 리스료": "LEASE_LOW_VALUE_EXP"})

    def test_short_term_not_without_ga_is_low_value(self):
        # 수정 1차: 「단기리스아닌소액자산리스료」(조사 「가」 없음, c11에서 4행)도 소액 행
        t = _table([("단기리스료", [0, 0]), ("단기리스아닌소액자산리스료", [120, 80])], note_title="리스", title="리스 비용",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"])
        got = {f["label"]: f["concept"] for f in self.facts(t)}
        self.assertEqual(got["단기리스아닌소액자산리스료"], "LEASE_LOW_VALUE_EXP")

    def test_parenthesized_lease_costs_kept_negative(self):
        # s083 트레져헌터 20260508000176 주석 14 리스: 「(22,385,400)」 괄호 금액은 음수 사실로 남는다(판정이 부호를 무시)
        t = _table([("단기리스료", [-22385400, -33353260]), ("단기리스가 아닌 소액자산 리스료", [-9286674, -10086590])],
                   note_title="리스", title="(4) 당기와 전기 중 리스와 관련하여 손익계산서에 인식된 금액은 다음과 같습니다.",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"])
        got = {(f["concept"], f["amount"]) for f in self.facts(t) if f["period"] == "current_period"}
        self.assertEqual(got, {("LEASE_SHORT_TERM_EXP", -22385400), ("LEASE_LOW_VALUE_EXP", -9286674)})

    def test_kcttc_ip_listing_period_columns_are_carrying(self):
        # s117 케이씨티시 20260319001225 주석 15: 「(1) 투자부동산 내역」 당기말·전기말 열 = 장부금액
        t = _table([("토지", [46012668, 51222080])], note_title="투자부동산", title="(1) 투자부동산 내역",
                   headers=["당기말", "전기말"], unit=KRW1000)
        fv = _table([("공정가치", [95702928, 100159160])], note_title="투자부동산",
                    title="(3) 투자부동산과 관련한 공정가치 내역", headers=["당기말", "전기말"], unit=KRW1000,
                    tid="R:00760:t002")
        facts = self.facts(t, fv)
        self.assertEqual({(f["period"], f["amount"]) for f in _by(facts, "IP_CARRYING")},
                         {("current_end", 46012668), ("prior_end", 51222080)})
        self.assertEqual({(f["period"], f["amount"]) for f in _by(facts, "IP_FAIR_VALUE")},
                         {("current_end", 95702928), ("prior_end", 100159160)})

    def test_seohan_carrying_composition_table(self):
        # s120 서한 20260323001404 주석 14(연결, 천원): 「장부금액의 구성내역」 당기말 열
        t = _table([("토지", [113489290, 111908220]), ("건물", [77341627, 78910105]),
                    ("건설중인자산", [21265909, 20597376]), ("합계", [212096826, 211415701])],
                   note_title="투자부동산",
                   title="14. 투자부동산(1) 당기말과 전기말 현재 투자부동산 장부금액의 구성내역은 다음과 같습니다.",
                   headers=["당기말", "전기말"], unit=KRW1000)
        got = {(f["label"], f["period"], f["amount"]) for f in _by(self.facts(t), "IP_CARRYING")}
        self.assertIn(("합계", "current_end", 212096826), got)

    def test_seohan_fair_value_gain_column_is_not_fair_value(self):
        # s120 서한 변동표의 「공정가치평가」 열은 평가손익(흐름)이다 — 공정가치 잔액이 아니다
        t = _table([("토지", [111908220, 0, 100670, 113489290]), ("합계", [211415701, 668533, -1442608, 212096826])],
                   note_title="투자부동산", title="(2) 당기와 전기 중 연결회사의 투자부동산 변동내역은 다음과 같습니다.① 당기",
                   periods=("opening", "current_period", "current_period", "current_end"),
                   headers=["기초", "취득", "공정가치평가", "당기말"], unit=KRW1000)
        self.assertEqual(_by(self.facts(t), "IP_FAIR_VALUE"), [])

    def test_hlb_genex_gross_listing_not_carrying_rows(self):
        # s105 HLB제넥스 20260320001238 주석 12(별도, 천원): 내역 표에 「감가상각누계액」 행이 있으면 토지·건물 행은
        # 취득원가다 — 새 「당기말 열」 장부금액 개념을 이 표에 쓰지 않는다(✓ 표본 FV_EQUALS 가 DIFFERS 로 깨졌었다)
        t = _table([(["토 지", "대전광역시 유성구 테크노2로 187"], [52500, 52500]), ("건 물", [607500, 607500]),
                    ("감가상각누계액", [-212625, -197438]), ("합 계", [447375, 462562])],
                   note_title="투자부동산", title="(1) 당기말과 전기말 현재 투자부동산의 내역은 다음과 같습니다.",
                   headers=["당기말", "전기말"], unit=KRW1000)
        self.assertEqual(_by(self.facts(t), "IP_CARRYING"), [])
        # 연결(t078)은 「감가상가누계액」 오타 — 「누계」로 막는다
        t["rows"][2]["label"] = t["rows"][2]["label_path"][0] = "감가상가누계액"
        self.assertEqual(_by(self.facts(t), "IP_CARRYING"), [])

    def test_listing_title_guard(self):
        # 담보·공정가치·수익 표의 당기말 열은 장부금액 개념이 아니다
        for title in ("담보로 제공된 투자부동산 내역", "투자부동산 공정가치 내역", "투자부동산 관련 수익 내역"):
            t = _table([("토지", [10, 20])], note_title="투자부동산", title=title, headers=["당기말", "전기말"])
            self.assertEqual(_by(self.facts(t), "IP_CARRYING"), [], title)


def _filing(paragraphs, tables=()):
    return {"rcept_no": "R", "amends_rcept_no": None, "paragraphs": paragraphs, "tables": list(tables)}


def _p(sid, text, file="R_00760.xml"):
    return {"id": sid, "file": file, "page": 1, "text": text}


class IpFairValueSentenceTest(unittest.TestCase):
    """투자부동산 공정가치 문장 → IP_FAIR_VALUE 사실(c11). 표로 공시하지 않은 회사가 많다."""

    def test_real_sentences(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([
            # s116 미스터블루 20260312001016(별도)
            _p("s382", "(4) 보고기간말 현재 투자부동산의 공정가치는 11,900,000천원입니다."),
            # s115 미래나노텍 20260319000008(연결)
            _p("s1186", "(6) 당기말 현재 투자부동산의 공정가치는 15,857백만원이며, 공정가치 평가는 독립된 평가인에 의해 수행됩니다.",
               file="R_00761.xml"),
        ]))
        self.assertEqual({(f["fs_scope"], f["period"], f["amount"], f["amount_krw"], f["source"]["sentence_id"])
                          for f in got},
                         {("separate", "current_end", 11900000, 11900000000, "s382"),
                          ("consolidated", "current_end", 15857, 15857000000, "s1186")})
        for f in got:
            self.assertEqual((f["concept"], f["unit_kind"], f["match_basis"]), ("IP_FAIR_VALUE", "krw", "sentence"))
            self.assertIn(f["concepts_version"], ("c11", "c12", "c13", "c15"))

    def test_prior_in_parentheses_and_units(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([
            # 미래나노텍 별도 s501
            _p("s501", "(6) 당기말 현재 투자부동산의 공정가치는 2,846백만원(전기말: 2,740백만원)이며, 공정가치 평가는 독립된 평가인에 의해 수행됩니다."),
            # s119 상신금속 20260306000295
            _p("s301", "16-4. 당기말 현재 투자부동산의 공정가치는 10,343백만원이며, 공정가치 평가는 독립된 평가인에 의해 수행됩니다.",
               file="Q_00761.xml"),
        ]))
        self.assertEqual({(f["fs_scope"], f["period"], f["amount_krw"]) for f in got},
                         {("separate", "current_end", 2846000000), ("separate", "prior_end", 2740000000),
                          ("consolidated", "current_end", 10343000000)})

    def test_both_periods_each(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([_p("s9", "(4) 당기말과 전기말 현재 투자부동산의 공정가치는 각각 3,272,000천원 및 "
                                                "3,204,000천원으로 당사는 공시지가 등을 반영하여 추정하였습니다.")]))
        self.assertEqual({(f["period"], f["amount_krw"]) for f in got},
                         {("current_end", 3272000000), ("prior_end", 3204000000)})

    def test_not_whole_or_not_amount(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([
            _p("s1", "투자부동산의 공정가치는 보고기간말 현재의 시장상황을 반영하고 있으며, 필요한 경우 조정됩니다."),
            _p("s2", "(5) 보고기간말 현재 투자부동산 토지의 공정가치는 3,428,781천원 (전기: 3,243,104천원)입니다."),
            _p("s3", "당기말 현재 투자부동산 중 토지의 공정가치는 1,041백만원입니다."),
            _p("s4", "(4) 공정가치당기말 현재 투자부동산의 공정가치는 토지 1,138,321천원 및 건물 1,298,411천원입니다."),
        ]))
        self.assertEqual(got, [])

    def test_mixed_units_sentence_skipped(self):
        # 20260320001390: 「공정가치는 13,595,625원(전기말 12,834,777천원)」 — 당기 단위 오기로 보인다. 단위가 섞이면 쓰지 않는다
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([_p("s1", "(4) 당기말 현재 투자부동산의 공정가치는 13,595,625원(전기말 12,834,777천원)이며, "
                                                "취득 당시의 독립된 평가인에 의해 수행된 감정평가금액입니다.")]))
        self.assertEqual(got, [])

    def test_fair_value_model_marker(self):
        # s120 서한 20260323001404: 공정가치 모형 회사 → IP_FV_MODEL 표시 사실(범위마다 하나, 금액 없음)
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([
            _p("s350", "(4) 투자부동산의 공정가치 모형의 적용당사는 투자부동산에 대한 후속측정을 공정가치로 평가하고 있으며, "
                       "투자부동산의 공정가치는 평가대상 투자부동산과 유사한 부동산을 평가한 경험이 있고 전문적 자격이 있는 "
                       "독립된 평가기관에서 측정한 가치에 따라 결정되었습니다."),
            _p("s926", "투자부동산은 최초 인식시점에 원가로 측정하고 있으며, 최초 인식 후에는 공정가치로 측정하고 공정가치 변동으로 "
                       "발생하는 손익은 발생한 기간의 당기손익에 반영하고 있습니다.", file="R_00761.xml"),
            _p("s927", "연결실체는 당기 중 투자부동산 인식 후의 측정모형을 원가모형에서 공정가치모형으로 변경하였습니다.",
               file="R_00761.xml"),
        ]))
        self.assertEqual({(f["fs_scope"], f["concept"], f["amount"], f["source"]["sentence_id"]) for f in got},
                         {("separate", "IP_FV_MODEL", None, "s350"), ("consolidated", "IP_FV_MODEL", None, "s926")})

    def test_model_marker_other_wordings(self):
        # 말뭉치 영향 점검에서 놓친 공정가치 모형 문장들(20260227007246·20260408000789·20260408001128·20260403000047)
        from dart_search_mcp.ledger.facts import sentence_facts
        texts = ["최초 인식 후 투자부동산은 공정가치로 측정됩니다.",
                 "투자부동산은 취득시 발생한 거래원가를 포함하여최초 인식시점에 원가로 측정하며, 최초 인식 후에 보고기간말 "
                 "현재의 사장상황을 반영한 공정가치로 계상하고 공정가치의 변동에 따라 발생하는 손익은 당기손익에 반영합니다.",
                 "유형자산과 투자부동산의 재평가연결회사는 투자부동산을 공정가치로 평가하고 있으며 공정가치의 변동은 "
                 "손익계산서에 반영하고 있습니다.",
                 "최초 인식 후 투자부동산은 보고기간말 현재의 시장상황을 반영하여 공정가치로 측정하고 공정가치 변동으로 발생하는 "
                 "손익은 발생한 기간의 당기손익에 반영하고 있습니다"]
        for t in texts:
            got = sentence_facts(_filing([_p("s1", t)]))
            self.assertEqual([f["concept"] for f in got], ["IP_FV_MODEL"], t)
        got = sentence_facts(_filing([_p("s1", "투자부동산을 공정가치로 측정하는 경우 공정가치 변동은 당기손익에 반영합니다.")]))
        self.assertEqual(got, [])

    def test_not_material_difference_marker(self):
        # 20260318001435: 「당기말 현재 투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다」 — 공정가치 금액은 없다
        # (회계사 Q4 대기: 엘엑스판토스 s118 「차이가 미미해 공시 안 함」과 같은 부류) → IP_FV_NO_MATERIAL_DIFF 표시
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([_p("s657", "한편, 당기말 현재 투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다.")]))
        self.assertEqual([(f["concept"], f["amount"]) for f in got], [("IP_FV_NO_MATERIAL_DIFF", None)])

    def test_fair_value_leadin_marker(self):
        # 20260323000601: 「(3) 당기말 현재 투자부동산의 공정가치는 다음과 같습니다.」 + 글 칸 표(평가금액 14,780,000천원)
        # 표 금액을 못 읽어도 공정가치를 공시한 것이다 → IP_FV_LEADIN 표시(공시 안 함으로 정하지 않게)
        from dart_search_mcp.ledger.facts import sentence_facts
        for t in ("(3) 당기말 현재 투자부동산의 공정가치는 다음과 같습니다.",
                  "당기말과 전기말 현재 공정가치로 측정되는 투자부동산의 서열체계에 따른 수준별 공정가치 측정치는 다음과 같습니다."):
            got = sentence_facts(_filing([_p("s489", t)]))
            self.assertEqual([f["concept"] for f in got], ["IP_FV_LEADIN"], t)

    def test_model_marker_negation_and_other_entity(self):
        # 수정 1차: 부정문(20260408000296)·다른 회사의 공정가치 모형(20260105000006·20251230000872)은 모형 표시가 아니다
        from dart_search_mcp.ledger.facts import sentence_facts
        for t in ("따라서 당사는투자부동산을 공정가치로 측정하지는 않지만 투자부동산의 공정가치를 주석으로 공시합니다.",
                  "당사는 투자부동산을 공정가치로 측정하지 아니하고 원가에서 감가상각누계액을 차감한 금액으로 표시합니다.",
                  "지분법 피투자회사가 보유한 투자부동산의 후속측정 방법을 원가모형에서 공정가치모형으로 변경하였습니다.",
                  "종속기업이 보유하고 있는 투자부동산은 최초 인식 후 공정가치로 측정합니다.",
                  "관계기업이 보유한 투자부동산은 공정가치모형을 적용하고 있습니다."):
            got = sentence_facts(_filing([_p("s1", t)]))
            self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got], t)

    def test_model_marker_gated_by_cost_model_evidence(self):
        # 같은 범위에 투자부동산 표의 감가상각누계 행이나 원가모형 문장이 있으면 모형 표시를 내지 않는다
        from dart_search_mcp.ledger.facts import sentence_facts
        model = "최초 인식 후 투자부동산은 공정가치로 측정됩니다."
        table = {"file": "R_00760.xml", "fs_scope": "separate", "region": {"kind": "note", "note_title": "투자부동산"},
                 "title": "투자부동산 내역",
                 "columns": [{"col": 1, "role": "value", "period": "current_end", "header_path": ["당기말"]}],
                 "rows": [{"label": "감가상각누계액", "label_path": ["감가상각누계액"], "cells": [{"col": 1, "value": -7}]}]}
        got = sentence_facts(_filing([_p("s1", model)], [table]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])
        got = sentence_facts(_filing([_p("s1", model), _p("s2", "투자부동산은 원가모형으로 측정하고 있습니다.")]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])
        # 연결 범위의 누계 행은 별도 모형 표시를 막지 않는다
        table["fs_scope"], table["file"] = "consolidated", "R_00761.xml"
        got = sentence_facts(_filing([_p("s1", model)], [table]))
        self.assertEqual([(f["fs_scope"], f["concept"]) for f in got], [("separate", "IP_FV_MODEL")])

    def test_round2_cost_evidence_does_not_block_true_fv_model(self):
        # 수정 2차: 전환 회사의 과거 원가모형 문장(20260309001896 등), 「원가모형으로 평가되었을 경우의 장부금액」 공시
        # (20260317000660 등), 전기·비교 표의 상각누계 행은 원가모형 근거가 아니다
        from dart_search_mcp.ledger.facts import sentence_facts
        model = "최초 인식 후 투자부동산은 공정가치로 측정됩니다."
        for cost in ("회사는 과거에 모든 투자부동산을 취득원가에서 감가상각누계액을 차감한 원가모형을 적용하여 측정하였으며, "
                     "당기 중 공정가치모형으로 변경하였습니다.",
                     "투자부동산이 원가모형으로 평가되었을 경우의 장부금액은 다음과 같습니다."):
            got = sentence_facts(_filing([_p("s1", model), _p("s2", cost)]))
            self.assertIn("IP_FV_MODEL", [f["concept"] for f in got], cost)

        def tbl(periods, title="투자부동산 내역", value=-5):
            cols = [{"col": i, "role": "value", "period": pr, "header_path": [pr]} for i, pr in enumerate(periods, 1)]
            return {"file": "R_00760.xml", "fs_scope": "separate", "title": title, "columns": cols,
                    "region": {"kind": "note", "note_title": "투자부동산"},
                    "rows": [{"label": "감가상각누계액", "label_path": ["감가상각누계액"],
                              "cells": [{"col": i, "value": value} for i in range(1, len(periods) + 1)]}]}
        for t in (tbl(["prior_end"]), tbl(["current_end"], title="원가모형으로 평가되었을 경우의 장부금액"),
                  tbl(["current_end"], value=0)):
            got = sentence_facts(_filing([_p("s1", model)], [t]))
            self.assertIn("IP_FV_MODEL", [f["concept"] for f in got], t["title"])
        got = sentence_facts(_filing([_p("s1", model)], [tbl(["current_end", "prior_end"])]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])

    def test_round2_negation_scoped_to_measurement_verb(self):
        # 20260319000246: 「…공정가치모형을 적용하며 감가상각을 하지 않습니다」 는 모형 문장 그대로, 20260408000296 은 계속 막는다
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([_p("s1", "투자부동산은 공정가치모형을 적용하며 감가상각을 하지 않습니다.")]))
        self.assertIn("IP_FV_MODEL", [f["concept"] for f in got])
        got = sentence_facts(_filing([_p("s1", "따라서 당사는투자부동산을 공정가치로 측정하지는 않지만 투자부동산의 공정가치를 "
                                                "주석으로 공시합니다.")]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])

    def test_round3_cost_sentences_movement_and_land_only(self):
        # 수정 3차: 원가 표시·정액법 상각 문장(20260319001219·20260316000090), 당기 변동표의 감가상각 칸
        # (20260312001302 등), 토지만 공정가치모형(20260319001238)은 모형 표시를 내지 않는다
        from dart_search_mcp.ledger.facts import sentence_facts
        model = "최초 인식 후 투자부동산은 공정가치로 측정됩니다."
        for cost in ("투자부동산은 원가에서 감가상각누계액과 손상차손누계액을 차감한 금액으로 표시하고 있습니다.",
                     "투자부동산 중 토지를 제외한 투자부동산은 경제적 내용연수에 걸쳐 정액법으로 상각됩니다."):
            got = sentence_facts(_filing([_p("s1", model), _p("s2", cost)]))
            self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got], cost)
        # 유형자산 문장(투자부동산 낱말 없음)은 근거가 아니다
        got = sentence_facts(_filing([_p("s1", model), _p("s2", "유형자산은 원가에서 감가상각누계액을 차감한 금액으로 표시합니다.")]))
        self.assertIn("IP_FV_MODEL", [f["concept"] for f in got])
        got = sentence_facts(_filing([_p("s1", "회사는 투자부동산 중 토지에 대해 공정가치모형을 적용하고 있습니다.")]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])

        def mv(periods, label="감가상각비", value=-90, title="투자부동산 변동내역"):
            cols = [{"col": i, "role": "value", "period": pr, "header_path": [pr]} for i, pr in enumerate(periods, 1)]
            return {"file": "R_00760.xml", "fs_scope": "separate", "title": title, "columns": cols,
                    "region": {"kind": "note", "note_title": "투자부동산"},
                    "rows": [{"label": label, "label_path": [label],
                              "cells": [{"col": i, "value": value} for i in range(1, len(periods) + 1)]}]}
        got = sentence_facts(_filing([_p("s1", model)], [mv(["current_period"])]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])
        for t in (mv(["prior_period"]), mv(["current_period"], value=0)):
            got = sentence_facts(_filing([_p("s1", model)], [t]))
            self.assertIn("IP_FV_MODEL", [f["concept"] for f in got])
        # 감가상각이 열 머리글인 변동표(행 = 토지·건물)
        t = mv(["current_period"], label="건물")
        t["columns"][0]["header_path"] = ["감가상각"]
        got = sentence_facts(_filing([_p("s1", model)], [t]))
        self.assertNotIn("IP_FV_MODEL", [f["concept"] for f in got])

    def test_round4_fair_value_gain_column_overrides_depreciation(self):
        # 수정 4차: 20260408001128·20260408003196(연결) t105 — 공정가치평가 열 3,138,000 이 있는 변동표의 작은 감가상각비
        # (-13,047)는 원가모형 근거가 아니다. 막힌 모형 문장은 IP_FV_MODEL_BLOCKED 로 남긴다
        from dart_search_mcp.ledger.facts import sentence_facts
        model = "최초 인식 후 투자부동산은 보고기간말 현재의 시장상황을 반영하여 공정가치로 측정하고 있습니다."
        cols = [{"col": 1, "role": "value", "period": "opening", "header_path": ["기초"]},
                {"col": 2, "role": "value", "period": "current_period", "header_path": ["감가상각비"]},
                {"col": 3, "role": "value", "period": "current_period", "header_path": ["공정가치평가"]},
                {"col": 4, "role": "value", "period": "current_end", "header_path": ["기말"]}]
        t = {"file": "R_00761.xml", "fs_scope": "consolidated", "title": "투자부동산 변동내역", "columns": cols,
             "region": {"kind": "note", "note_title": "투자부동산"},
             "rows": [{"label": "합계", "label_path": ["합계"],
                       "cells": [{"col": 1, "value": 292305000}, {"col": 2, "value": -13047},
                                 {"col": 3, "value": 3138000}, {"col": 4, "value": 295429547}]}]}
        got = sentence_facts(_filing([_p("s1", model, file="R_00761.xml")], [t]))
        self.assertEqual([f["concept"] for f in got], ["IP_FV_MODEL"])
        t["rows"][0]["cells"][2]["value"] = 0  # 평가 열이 0 이면 감가상각이 원가모형 근거
        got = sentence_facts(_filing([_p("s1", model, file="R_00761.xml")], [t]))
        self.assertEqual([f["concept"] for f in got], ["IP_FV_MODEL_BLOCKED"])

    def test_cost_model_or_construction_not_model_marker(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        got = sentence_facts(_filing([
            _p("s1", "(*) 과거회계기준에서는 임대목적으로 보유중인 부동산을 투자부동산으로 인식하고 공정가치모형을 적용하였으나, "
                     "일반기업회계기준에서는 유형자산으로 인식하고 원가모형을 적용하였습니다."),
            _p("s2", "건설 중인 투자부동산의 공정가치를 신뢰성 있게 산정할 수 없지만 건설이 완료된 시점에서는 공정가치를 신뢰성 "
                     "있게 산정할 수 있다고 예상되는 경우 원가로 측정됩니다."),
            _p("s3", "투자부동산은 원가모형으로 측정하며, 투자부동산의 공정가치 측정에 대한 공시는 주석 12를 참조하십시오."),
        ]))
        self.assertEqual(got, [])

    def test_scope_from_table_file_map(self):
        from dart_search_mcp.ledger.facts import sentence_facts
        tables = [{"file": "R.xml", "fs_scope": "consolidated"}]
        got = sentence_facts(_filing([_p("s1", "당기말 현재 투자부동산의 공정가치는 1,643백만원입니다.", file="R.xml")], tables))
        self.assertEqual([f["fs_scope"] for f in got], ["consolidated"])



class C12AgreedFixTest(unittest.TestCase):
    """회계사 두 명이 같은 답을 준 v2 표본(10-19) 수정(c12) — 실제 표본의 표·문장 조각."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_version_c12(self):
        meta = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
        self.assertIn(meta["concepts_version"], ("c12", "c13", "c15"))

    # ---- 1. FVOCI: FVOCI 주석 안의 지분 행 ----
    def test_seongwoo_equity_rows_in_fvoci_note(self):
        # s056 성우하이텍 20260319000729 연결 t043(천원): 행 이름에 FVOCI 낱말이 없고, 표 제목에도 지분이 없다
        t = _table([("시장성 있는 지분증권", [22825786, 17809551]), ("시장성 없는 지분증권", [18315981, 17886521]),
                    ("합 계", [41141767, 35696072])],
                   note_title="기타포괄손익-공정가치측정금융자산", unit=KRW1000, headers=["당기말", "전기말"],
                   title="7. 기타포괄손익-공정가치측정금융자산(1) 당기말과 전기말 현재 기타포괄손익-공정가치측정금융자산의 내역은 다음과 같습니다.")
        got = {(f["label"], f["period"], f["amount"]) for f in _by(self.facts(t), "FVOCI_EQUITY")}
        self.assertEqual(got, {("시장성 있는 지분증권", "current_end", 22825786), ("시장성 있는 지분증권", "prior_end", 17809551),
                               ("시장성 없는 지분증권", "current_end", 18315981), ("시장성 없는 지분증권", "prior_end", 17886521)})

    def test_equity_rows_outside_pure_fvoci_note_not_matched(self):
        rows = [("시장성 있는 지분증권", [100, 90]), ("시장성 없는 지분증권", [50, 40]), ("합 계", [150, 130])]
        for note in ("당기손익-공정가치측정금융자산", "당기손익-공정가치측정금융자산 및 기타포괄손익-공정가치측정금융자산",
                     "금융자산", "관계기업투자"):
            t = _table(rows, note_title=note, title="(1) 내역", headers=["당기말", "전기말"])
            self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [], note)
        # 주석 제목을 못 읽은 표(region_unknown)는 받지 않는다 — 행 이름만으로는 FVOCI 인지 알 수 없다
        t = _table(rows, note_title=None, title="(1) 내역", headers=["당기말", "전기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    def test_fvoci_note_debt_flow_or_entity_rows_not_equity_rows(self):
        note = "기타포괄손익-공정가치측정금융자산"
        t = _table([("채무증권", [10, 8]), ("국공채", [5, 5])], note_title=note, title="(1) 내역", headers=["당기말", "전기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 변동표(제목에 변동)·평가손익 행은 잔액이 아니다
        t = _table([("상장주식", [10, 8])], note_title=note, title="(2) 당기와 전기 중 기타포괄손익-공정가치측정금융자산의 변동 내역",
                   headers=["당기말", "전기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        t = _table([("지분상품 평가손익", [10, 8])], note_title=note, title="(1) 내역", headers=["당기말", "전기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 투자처 이름 행(「…주식회사」)은 행 사실로 쓰지 않는다(합계 행 규칙이 맡는다)
        t = _table([("삼성전자주식회사", [10, 8]), ("합계", [10, 8])], note_title=note, title="(1) 내역", headers=["당기말", "전기말"])
        self.assertFalse([f for f in _by(self.facts(t), "FVOCI_EQUITY") if f["label"] == "삼성전자주식회사"])

    # ---- 1. FVOCI: 표 제목 합계 행이 주석 제목도 본다 ----
    def test_seongwoo_entity_table_total_uses_note_title(self):
        # s056 성우하이텍 t045: 제목 「(3) … 시장성 있는 지분증권의 내역」(FVOCI 낱말 없음), 주석 제목이 FVOCI
        t = _table([("현대제철(주)", [17631277, 17631277, 11952926]), ("(주)케이엔엔", [5193032, 5193032, 5855034]),
                    ("KG모빌리티", [1477, 1477, 1591]), ("합 계", [22825786, 22825786, 17809551])],
                   note_title="기타포괄손익-공정가치측정금융자산", unit=KRW1000,
                   periods=("current_end", "current_end", "prior_end"),
                   headers=[["당기말", "공정가치"], ["당기말", "장부금액"], ["전기말", "장부금액"]],
                   title="(3) 당기말과 전기말 현재 시장성 있는 지분증권의 내역은 다음과 같습니다.")
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual({(f["period"], f["amount"], f["column"]) for f in got},
                         {("current_end", 22825786, "장부금액"), ("prior_end", 17809551, "장부금액")})
        # 근거 줄이 무엇의 합계인지 보이게 합계 행이 더한 행 이름을 단다(회계사 두 명 요청, s055·S053)
        self.assertEqual(got[0]["components"], ["현대제철(주)", "(주)케이엔엔", "KG모빌리티"])

    def test_rochesystems_total_has_section_and_components(self):
        # s055 로체시스템즈 20260317000688 연결 t051: 구획 「지분상품(비상장주식)」 + 투자처 3 + 합계
        t = _table([("지분상품(비상장주식)", [None, None]), ("(주)베스테크(주1)", [1050000, 1050000]),
                    ("(주)에이마크", [17215, 17215]), ("윈텔코퍼레이션(주)", [18329, 18329]), ("합계", [1085544, 1085544])],
                   note_title="기타포괄손익-공정가치측정금융자산", unit=KRW1000, headers=["당기말", "전기말"],
                   title="당기말 및 전기말 현재 기타포괄손익-공정가치측정금융자산의 내역은 다음과 같습니다.")
        got = [f for f in _by(self.facts(t), "FVOCI_EQUITY") if f["period"] == "current_end"]
        self.assertEqual([(f["label"], f["amount"], f["match_basis"]) for f in got], [("합계", 1085544, "table_total")])
        self.assertEqual(got[0]["section"], "지분상품(비상장주식)")
        self.assertEqual(got[0]["components"], ["(주)베스테크", "(주)에이마크", "윈텔코퍼레이션(주)"])

    # ---- 1. FVOCI: 투자처 이름 표(제목·소제목에 지분 낱말 없음) ----
    def _samla(self, extra=(), note_title="기타포괄손익-공정가치측정금융자산"):
        rows = [(["비유동항목", "㈜한국경제신문"], [160638, 160638], 1), (["비유동항목", "㈜기협기술금융"], [300000, 300000], 1),
                (["비유동항목", "에스엠중공업㈜"], [9597161, 7773589], 1), (["비유동항목", "에스엠벡셀"], [32053805, 3021858], 1),
                (["비유동항목", "전문건설공제조합"], [0, 18648960], 1), *extra,
                (["비유동항목", "합 계"], [44475529, 44111864], 1)]
        return _table(rows, note_title=note_title, unit=KRW1000, headers=[["당기", "장부금액"], ["전기", "장부금액"]],
                      title="보고기간종료일 현재 기타포괄손익-공정가치측정금융자산의 내용은 다음과 같습니다.")

    def test_samla_entity_name_table_total(self):
        # s059 삼라마이다스 20260414000104 연결 t065(천원)
        got = _by(self.facts(self._samla()), "FVOCI_EQUITY")
        self.assertEqual({(f["period"], f["amount"]) for f in got}, {("current_end", 44475529), ("prior_end", 44111864)})
        self.assertEqual(got[0]["section"], "비유동항목")

    def test_entity_name_table_with_debt_or_fund_row_or_fvpl_note_not_matched(self):
        self.assertEqual(_by(self.facts(self._samla(extra=[(["비유동항목", "㈜신한은행 후순위채권"], [100, 100], 1)])),
                             "FVOCI_EQUITY"), [])
        self.assertEqual(_by(self.facts(self._samla(extra=[(["비유동항목", "케이투자조합 수익증권"], [100, 100], 1)])),
                             "FVOCI_EQUITY"), [])
        self.assertEqual(_by(self.facts(self._samla(note_title="당기손익-공정가치측정금융자산")), "FVOCI_EQUITY"), [])
        # 이름 표지(㈜·조합…)가 있는 행이 절반 미만이면 투자처 표로 보지 않는다
        t = _table([("단기투자자산", [10, 10]), ("장기투자자산", [20, 20]), ("기타", [5, 5]), ("합계", [35, 35])],
                   note_title="기타포괄손익-공정가치측정금융자산", headers=["당기말", "전기말"],
                   title="기타포괄손익-공정가치측정금융자산의 내용은 다음과 같습니다.")
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    # ---- 1. FVOCI: 열 머리글의 FVOCI(옛 낱말 「기타포괄손익인식」) × 지분 행 ----
    def test_hyosung_fvoci_column_old_wording(self):
        # s058 효성티앤씨 20260311004548 별도 t102(백만원) 모양 — 실제 표는 기간 열이 없다(여기서는 당기말로 둠)
        rows = [(["금융자산", "상장주식", "공정가치 서열체계의 모든 수준 합계"], [30, 20885], 1),
                (["금융자산", "비상장 주식", "공정가치 서열체계의 모든 수준 합계"], [7273, 0], 1),
                (["금융자산", "파생상품", "공정가치 서열체계의 모든 수준 합계"], [0, 1371], 1),
                (["금융자산", "수익증권", "공정가치 서열체계의 모든 수준 합계"], [0, 5570], 1)]
        t = _table(rows, note_title="금융상품 공정가치", title="기타포괄손익인식금융자산 당기손익인식금융자산",
                   periods=("current_end", "current_end"), headers=["기타포괄손익인식금융자산", "당기손익인식금융자산"])
        got = {(f["label_path"][1], f["amount"]) for f in _by(self.facts(t), "FVOCI_EQUITY")}
        self.assertEqual(got, {("상장주식", 30), ("비상장 주식", 7273)})

    def test_fvoci_column_not_from_pl_or_fvpl_columns(self):
        t = _table([("상장주식", [5, 6])], note_title="금융상품", title="범주별 금융상품",
                   periods=("current_end", "current_end"), headers=["당기손익인식금융자산", "상각후원가측정금융자산"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        t = _table([("지분상품", [5])], note_title="금융상품", title="범주별 순손익", periods=("current_period",),
                   headers=["기타포괄손익-공정가치측정금융자산"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    def test_c12_review_false_positives(self):
        note = "기타포괄손익-공정가치 측정 금융자산"
        # 20260401004773 t041 「(2) 포괄손익으로 인식된 금액」(흐름)
        t = _table([("출자금", [-79027, 163807])], note_title=note, title="(2) 포괄손익으로 인식된 금액",
                   periods=("current_period", "prior_period"), headers=["당기", "전기"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 20260401003961 t066 「미실현보유손익의 구성내역」 × 열 「전기/기타포괄손익-공정가치금융자산」
        t = _table([("출자금", [193915, 314721])], note_title="당기손익-공정가치측정 금융자산",
                   title="(4) 보고기간말 현재 당기손익-공정가치측정 금융자산의 미실현보유손익의 구성내역은 다음과 같습니다.",
                   periods=("current_end", "prior_end"),
                   headers=[["당기", "당기손익-공정가치금융자산"], ["전기", "기타포괄손익-공정가치금융자산"]])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 범주표의 「비지배지분부채」 행(지분 낱말)은 지분상품이 아니다(20260319000859 t121)
        t = _table([("비지배지분부채", [0]), ("지분증권", [16028416])], note_title="범주별 금융상품", title="범주별 장부금액",
                   periods=("current_end",), headers=["기타포괄손익-공정가치 측정금융상품"])
        self.assertEqual({f["label"] for f in _by(self.facts(t), "FVOCI_EQUITY")}, {"지분증권"})
        # 공정가치 서열 표의 「…투입변수에 대한 기술,부채」 열은 금액 열이 아니다(20260311004548 연결 t093)
        t = _table([(["공정가치 평가를 기타포괄 손익 으로 인식하는 금융자산", "비상장주식", "RedGene"], [200, 0], 2)],
                   note_title="공정가치", title="금융 자산, 공정 가치", periods=("current_end", "current_end"),
                   headers=["금융 자산, 공정 가치", "공정가치 측정에 사용된 투입변수에 대한 기술,부채"])
        self.assertEqual({(f["column"], f["amount"]) for f in _by(self.facts(t), "FVOCI_EQUITY")},
                         {("금융 자산, 공정 가치", 200)})

    def test_oci_recognised_amount_column_not_fvoci(self):
        # 20260318000761 「기타포괄손익인식액」 열은 기타포괄손익으로 인식한 금액(흐름)이다
        t = _table([("지분증권", [535776])], note_title="기타포괄손익금융자산", title="〈당기〉", periods=("current_end",),
                   headers=["기타포괄손익인식액"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    def test_total_after_subtotals_lists_sections(self):
        # 20260318000745 t056 「① 당기말」: 구획(활성시장 있는·없는 지분증권)마다 소계, 끝에 합계
        t = _table([("활성시장이 있는 지분증권", [None, None]), ("㈜대한항공", [1082, 1082]), ("소 계", [1082, 1082]),
                    ("활성시장이 없는 지분증권(*1)", [None, None]), ("㈜선운", [7313, 7313]), ("소 계", [7313, 7313]),
                    ("합 계", [8395, 8395])],
                   note_title="기타포괄손익-공정가치측정금융자산", title="① 당기말", periods=("current_end", "current_end"),
                   headers=["공정가치", "장부금액"])
        for r in t["rows"]:
            if r["label"] == "소 계":
                r["kind"] = "subtotal"
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual([(f["amount"], f["column"]) for f in got], [(8395, "장부금액")])
        self.assertEqual(got[0]["components"], ["활성시장이 있는 지분증권", "활성시장이 없는 지분증권"])

    def test_old_wording_row_label(self):
        # 「기타포괄손익인식」(옛 낱말) 행 이름
        t = _table([("기타포괄손익인식지분상품", [10, 8])], note_title="금융상품", title="범주별 금융상품", headers=["당기말", "전기말"])
        self.assertEqual({f["amount"] for f in _by(self.facts(t), "FVOCI_EQUITY")}, {10, 8})
        t = _table([("공정가치평가를 기타포괄손익으로 인식하는 지분상품", [10, 8])], note_title="금융상품", title="범주별 금융상품",
                   headers=["당기말", "전기말"])
        self.assertEqual({f["amount"] for f in _by(self.facts(t), "FVOCI_EQUITY")}, {10, 8})

    # ---- 2. EMB: 「(조기)상환권」 행 ----
    def _yukil(self):
        # s039 육일씨엔에쓰 20260319000768 연결 t143(천원): 값 빈 부모 행 「파생상품부채(*1)」 아래 「조기상환권」
        return _table([("파생상품부채(*1)", [None, None]), ("전환권대가", [0, 0]), ("조기상환권", [172558, 1215318]),
                       ("파생상품자산(*2)", [None, None]), ("매도청구권", [0, 0])],
                      note_title="전환사채", unit=KRW1000, headers=["당기말", "전기말"],
                      title="(3) 당기말과 전기말 현재 파생상품부채 및 파생상품자산의 내역은 다음과 같습니다.")

    def test_yukil_early_redemption_option_row(self):
        got = [f for f in self.facts(self._yukil()) if f["label"] == "조기상환권"]
        self.assertEqual({(f["concept"], f["period"], f["amount"], f["target"], f["target_basis"]) for f in got},
                         {("EMB_DERIV_LIABILITY", "current_end", 172558, "redemption", "label"),
                          ("EMB_DERIV_LIABILITY", "prior_end", 1215318, "redemption", "label")})

    def test_bare_redemption_option_needs_liability_parent_and_bond_note(self):
        # 자산 구획 아래 「상환권」은 부채가 아니다
        t = _table([("파생상품자산", [None, None]), ("상환권", [5, 5])], note_title="전환사채", headers=["당기말", "전기말"],
                   title="(3) 파생상품의 내역")
        self.assertEqual([f for f in self.facts(t) if f["concept"] == "EMB_DERIV_LIABILITY"], [])
        # 사채·우선주·파생 주석이 아니면(예: 공정가치 서열 주석) 「상환권」 행만으로 쓰지 않는다
        t = _table([("파생상품부채", [None, None]), ("조기상환권", [5, 5])], note_title="공정가치", headers=["당기말", "전기말"],
                   title="(1) 공정가치 서열체계")
        self.assertEqual([f for f in self.facts(t) if f["label"] == "조기상환권"], [])

    def test_bare_redemption_option_asset_label_or_pl_table_not_liability(self):
        # 「상환권(파생상품자산)」 행, 「(4) 파생금융자산과 부채에서 인식한 평가손익」 표(당기·전기 열)는 부채 잔액이 아니다
        t = _table([("상환권(파생상품자산)", [5, 5])], note_title="차입금 및 사채", headers=["당기말", "전기말"],
                   title="(2) 전환사채 당기말 및 전기말 전환사채의 내역은 다음과 같습니다. 파생상품")
        self.assertEqual([f for f in self.facts(t) if f["concept"] == "EMB_DERIV_LIABILITY"], [])
        t = _table([("조기상환권", [120, 80])], note_title="전환사채", periods=("current_period", "prior_period"),
                   headers=["당기", "전기"], title="(4) 파생금융자산과 부채에서 인식한 평가손익")
        self.assertEqual([f for f in self.facts(t) if f["concept"] == "EMB_DERIV_LIABILITY"], [])

    # ---- 5. 손익 열의 파생상품 행은 잔액 사실이 아니다 ----
    def test_yukil_pl_column_derivative_row_dropped(self):
        # s039 t238 「(7) … 범주별 순손익」: 행 「파생상품부채(자산)」 × 열 「파생상품평가손익」 655,182 가 잔액 사실이 되어
        # 전환권 자본 판정을 막았다
        t = _table([("상각후원가 측정금융자산", [0, 0]), ("파생상품부채(자산)", [0, 655182]), ("합 계", [0, 655182])],
                   note_title="금융상품", unit=KRW1000, periods=("current_period", "current_period"),
                   headers=["이자수익", "파생상품평가손익"],
                   title="(7) 당기 및 전기 중 금융자산, 금융부채의 범주별 순손익은 다음과 같습니다.1) 당기 전환사채")
        self.assertEqual([f for f in self.facts(t) if f["concept"].startswith("EMB_")], [])
        for header in ("평가이익", "거래손실", "이자비용", "외화환산손익", "기타수익", "금융손익"):
            t = _table([("전환사채 파생상품부채", [10])], note_title="파생상품", periods=("current_end",), headers=[header],
                       title="파생상품 손익")
            self.assertEqual([f for f in self.facts(t) if f["concept"].startswith("EMB_")], [], header)

    def test_valuation_amount_column_is_balance(self):
        # 「평가액」「평가금액」 열은 평가한 잔액(공정가치)이다 — 손익 열이 아니다(20260331001148 모양)
        for header in ("평가액", "평가금액"):
            t = _table([("전환사채 파생상품부채", [394218])], note_title="파생상품", periods=("current_end",), headers=[header],
                       title="파생상품 내역")
            self.assertEqual({(f["concept"], f["amount"]) for f in self.facts(t)}, {("EMB_DERIV_LIABILITY", 394218)}, header)

    def test_pl_column_conversion_liability_dropped_too(self):
        # EMB 를 손익 열에서 빼면 같은 칸이 CONV_OPTION_LIABILITY(「파생상품부채…전환권」)로 넘어갔다(20260316001316 t046 「평가」 열)
        t = _table([(["파생상품부채", "제1회 사모 전환사채 상환권/전환권"], [20320838, 940992, 21261830], 1)], note_title="공정가치",
                   periods=("current_end", "current_end", "current_end"), headers=[["기초"], ["평가"], ["기말"]],
                   title="공정가치 서열체계 수준3 변동")
        got = {(f["concept"], f["amount"]) for f in self.facts(t) if f["concept"] != "DERIV_PL_COLUMN"}
        self.assertNotIn(940992, {a for _, a in got})
        # 「평가후 장부가액」은 잔액이다
        t = _table([("전환권 파생상품부채", [500])], note_title="파생상품", periods=("current_end",), headers=["평가후장부가액"],
                   title="파생상품 내역")
        self.assertEqual({(f["concept"], f["amount"]) for f in self.facts(t)}, {("CONV_OPTION_LIABILITY", 500)})

    def test_redemption_row_under_rcps_liability_is_not_derivative(self):
        # 20260406002852 t089: 「상환전환우선주부채 / 상환권」 — 파생 낱말이 없는 부모 아래 「상환권」은 내재파생 행으로 보지 않는다
        t = _table([(["상환전환우선주부채", "상환권"], [0, 0], 1)], note_title="상환전환우선주부채", headers=["당기말", "전기말"],
                   title="(2) 상환전환우선주부채의 내역")
        self.assertEqual([f for f in self.facts(t) if f["concept"] == "EMB_DERIV_LIABILITY"], [])

    def test_fvpl_equity_row_in_fvoci_column_not_fvoci(self):
        # 20260213002543 t040 범주표: 행 「당기손익-공정가치측정 지분상품」 × 열 「기타포괄손익-공정가치 측정 금융자산」
        t = _table([("당기손익-공정가치측정 지분상품", [0, 0])], note_title="금융상품", title="범주별 금융상품",
                   periods=("current_end", "current_end"), headers=["기타포괄손익-공정가치 측정 금융자산", "합계"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    def test_category_table_fvpl_column_derivative_still_matched(self):
        # 범주표의 「당기손익-공정가치측정금융부채」 열은 잔액 열이다(손익 열이 아님)
        t = _table([("파생상품부채(전환사채)", [500])], note_title="금융상품", periods=("current_end",),
                   headers=["당기손익-공정가치측정금융부채"], title="범주별 금융상품")
        self.assertEqual({(f["concept"], f["amount"]) for f in self.facts(t)}, {("EMB_DERIV_LIABILITY", 500)})


class FvociDesignationSentenceTest(unittest.TestCase):
    """회사가 지분상품을 FVOCI 로 지정했다는 직접 문장(c12) → FVOCI_DESIGNATION_STMT 표시 사실(금액 없음)."""

    def got(self, paragraphs, tables=()):
        from dart_search_mcp.ledger.facts import sentence_facts
        return [f for f in sentence_facts(_filing(paragraphs, tables)) if f["concept"] == "FVOCI_DESIGNATION_STMT"]

    def test_seongwoo_irrevocable_election_sentence(self):
        # s056 성우하이텍 20260319000729 연결 s928·s929
        got = self.got([
            _p("s928", "7. 기타포괄손익-공정가치측정금융자산(1) 당기말과 전기말 현재 기타포괄손익-공정가치측정금융자산의 내역은 다음과 같습니다.",
               file="R_00761.xml"),
            _p("s929", "연결실체는 단기매매항목이 아닌 전략적 투자목적으로 보유하는 지분상품에 대해 최초 적용일에 "
                       "기타포괄손익-공정가치측정항목으로 지정하는 취소불가능한 선택권을 적용하였습니다.", file="R_00761.xml"),
        ])
        self.assertEqual([(f["fs_scope"], f["period"], f["amount"], f["source"]["sentence_id"]) for f in got],
                         [("consolidated", "current_end", None, "s929")])
        self.assertIn("취소불가능한 선택권을 적용하였습니다", got[0]["text"])

    def test_hyosung_sentence_in_text_table_title(self):
        # s058 효성티앤씨 20260311004548 별도 t614: 주석 30 기타자본구성요소의 글 표(값 열 없음) 제목
        title = ("상기 기타포괄손익누계액의 변동내역은 법인세효과가 차감된 후의 금액입니다.회사는 특정 지분상품투자의 공정가치를 "
                 "기타포괄손익으로 인식하도록 지정하였습니다(주석 5). 이러한 공정가치 변동은 자본 내에서 기타포괄손익-공정가치 측정 "
                 "금융자산 잉여금으로 누적됩니다.")
        t = {"table_id": "R:00760:t614", "file": "R_00760.xml", "fs_scope": "separate", "page": 9, "title": title,
             "region": {"kind": "note", "note_no": "30", "note_title": "기타자본구성요소"},
             "columns": [{"col": 0, "header_path": [""], "role": "label"}], "rows": []}
        got = self.got([], [t])
        self.assertEqual([(f["fs_scope"], f["source"]["sentence_id"]) for f in got], [("separate", "t:R:00760:t614:title")])
        self.assertTrue(got[0]["text"].startswith("회사는 특정 지분상품투자의 공정가치를 기타포괄손익으로 인식하도록 지정하였습니다"))

    def test_footnote_election_made(self):
        got = self.got([_p("s1", "10. 기타금융자산"),
                        _p("s2", "(*) 상기 금융자산은 단기매매항목이 아닌 지분상품으로 후속적인 공정가치 변동을 기타포괄손익으로 표시하는 "
                                 "취소 불가능한 선택을 하였습니다.")])
        self.assertEqual(len(got), 1)

    def test_generic_policy_or_policy_note_or_negation_not_evidence(self):
        # 「지정할 수 있다」 일반 정책은 근거가 아니다(회계사 두 명)
        self.assertEqual(self.got([_p("s1", "9. 기타포괄손익-공정가치측정금융자산"),
                                   _p("s2", "단기매매항목이 아닌 지분상품에 대한 투자는 최초 인식시점에 후속적인 공정가치 변동을 "
                                            "기타포괄손익으로 표시할 것을 지정하는 취소불가능한 선택을 할 수 있습니다.")]), [])
        # 회계정책 주석 안의 문장은 보유 내역 주석이 아니다
        self.assertEqual(self.got([_p("s1", "2. 재무제표 작성기준 및 중요한 회계정책"),
                                   _p("s2", "당사는 최초 인식시점에 단기매매항목이 아닌 모든 지분상품투자에 대하여 기타포괄손익-공정가치 "
                                            "항목으로 지정하였습니다.")]), [])
        # 주석 머리를 모르면 쓰지 않는다
        self.assertEqual(self.got([_p("s2", "당사는 지분상품을 기타포괄손익-공정가치 측정 항목으로 지정하였습니다.")]), [])
        self.assertEqual(self.got([_p("s1", "9. 금융자산"),
                                   _p("s2", "당사는 보유 지분상품을 기타포괄손익-공정가치 측정 항목으로 지정하지 않았습니다.")]), [])
        # 채무상품만 있는 문장(지분 낱말 없음)은 아니다
        self.assertEqual(self.got([_p("s1", "9. 금융자산"),
                                   _p("s2", "당사는 회사채를 기타포괄손익-공정가치 측정 항목으로 지정하였습니다.")]), [])



class C12Fix1FactsTest(unittest.TestCase):
    """e54903f 리뷰 수정 1차(c12): 변동표 잔액 금지, 기간 소제목, 손익 열 사실 대리 표시, 「(**)」 표지, 새 사실 표시."""

    def setUp(self):
        self.concepts = load_concepts()

    def facts(self, *tables):
        return extract_facts(list(tables), self.concepts)

    def test_split_reserve_movement_tables_give_no_fvoci_equity(self):
        note = "기타포괄손익-공정가치측정금융자산"
        # 20260331000133 t074 「② 전기」: 기초·평가·법인세효과·기말 열(미실현보유손익 변동표의 이어진 표)
        t = _table([("평가로 인한 기타포괄손익", [None, None, None, None]), ("지분증권", [-86271, -15839, 3310, -98800])],
                   note_title=note, title="② 전기", periods=("opening", "unknown", "unknown", "close"),
                   headers=["기초", "평가", "법인세효과", "기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 20260330001754 t046 「〈당기〉」: 기 초 · 증가(감소) · 처분 등 실현 · 법인세효과 · 당기말
        t = _table([("주식", [79176, -535497, 0, 128458, -327863])], note_title=note, title="〈당기〉",
                   periods=("opening", "current_period", "current_period", "current_period", "current_end"),
                   headers=["기 초", "증가(감소)", "처분 등 실현", "법인세효과", "당기말"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])
        # 20260325000538 t165 「- 당기」: 평가손익 열·법인세효과 행(적립금 변동표)
        t = _table([("채무증권", [-19743982, -25700787, -45143994]), ("지분증권", [0, -200780, -200780]),
                    ("법인세효과", [5212411, 7346725, 12469813])], note_title=note, title="- 당기",
                   periods=("opening", "current_period", "current_end"), headers=["기초잔액", "평가손익", "기말잔액"])
        self.assertEqual(_by(self.facts(t), "FVOCI_EQUITY"), [])

    def test_asset_movement_table_closing_balance_kept(self):
        # 수정 2차: 20260331003450 t078 「〈당기〉」 자산 변동표(기초·평가·취득·처분·기말) — 기말 열은 당기말 잔액이다
        note = "기타포괄손익-공정가치 측정 금융자산"
        t = _table([("비상장주식", [326347157, 18109686, 1273689, -4950774, 340779758])], note_title=note, title="〈당기〉",
                   periods=("opening", "current_period", "current_period", "current_period", "current_end"),
                   headers=["기초", "평가", "취득", "처분", "기말(주1)"], unit=KRW1000)
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual([(f["period"], f["amount"]) for f in got], [("current_end", 340779758)])
        # 「〈전기〉」 이어진 표: 1층이 기말 열을 당기말로 읽어도 소제목 기간(전기말)을 쓴다
        t["title"] = "〈전기〉"
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual([(f["period"], f["amount"]) for f in got], [("prior_end", 340779758)])

    def test_unrealised_columns_skipped_balance_columns_kept(self):
        # 20260318001149 t042 「(당기말)」: 미실현손익(기초·평가·실현·기말) 열 + 장부금액(공정가치) 유동·비유동 열
        t = _table([("채무상품", [15263485, 96911, 55208, 0, 15318693]), ("지분상품", [29066852, 789968, 557849, 2428538, 27196163])],
                   note_title="기타포괄손익-공정가치 측정 금융자산", title="(당기말)", unit=KRW1000,
                   periods=("current_end", "opening", "current_end", "current_end", "current_end"),
                   headers=[["취득원가"], ["미실현손익", "기초잔액"], ["미실현손익", "기말잔액"], ["장부금액(공정가치)", "유동"],
                            ["장부금액(공정가치)", "비유동"]])
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual({(f["column"], f["amount"]) for f in got}, {("유동", 2428538), ("비유동", 27196163)})

    def test_period_from_subtitle(self):
        # 기간이 행·열에 없는 표(「〈전기〉」 소제목, 열 「장부금액」) — 소제목의 기간을 쓴다
        t = _table([("상장주식", [500])], note_title="기타포괄손익-공정가치측정금융자산", title="〈전기〉",
                   periods=("current_end",), headers=["장부금액"])
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual([(f["period"], f["amount"]) for f in got], [("prior_end", 500)])

    def test_pl_column_cells_become_proxy_facts(self):
        # 손익 열 칸은 판정에 쓰는 개념이 아니라 DERIV_PL_COLUMN(원래 개념 기록)으로 남는다 — 전환권 값 동결 확인용
        t = _table([("상각후원가 측정금융자산", [0, 0]), ("파생상품부채(자산)", [0, 655182])], note_title="금융상품",
                   periods=("current_period", "current_period"), headers=["이자수익", "파생상품평가손익"],
                   title="(7) 범주별 순손익 전환사채")
        got = self.facts(t)
        self.assertFalse([f for f in got if f["concept"].startswith(("EMB_", "CONV_"))])
        proxy = [f for f in got if f["concept"] == "DERIV_PL_COLUMN"]
        self.assertEqual({(f["orig_concept"], f["amount"]) for f in proxy},
                         {("EMB_DERIV_LIABILITY", 0), ("EMB_DERIV_LIABILITY", 655182)})

    def test_coverage_ignores_proxy_concept(self):
        # 손익 열 대리 사실이 있는 표에서 매칭률 계산이 멈추면 안 된다(전체 실행 117공시 KeyError)
        t = _table([("파생상품부채(자산)", [0, 655182])], note_title="금융상품", periods=("current_period", "current_period"),
                   headers=["이자수익", "파생상품평가손익"], title="범주별 순손익 전환사채")
        coverage([t], self.concepts)

    def test_double_star_mark_stripped_from_components(self):
        # 20260323001115 연결: 「㈜인핸스드바이오(**)」가 「㈜인핸스드바이오(」로 보였다
        t = _table([("㈜와우매니지먼트그룹(*)", [300, 300]), ("㈜인핸스드바이오(**)", [200, 200]), ("합계", [500, 500])],
                   note_title="기타포괄손익-공정가치 측정 금융자산", headers=["당기말", "전기말"],
                   title="(1) 당기말과 전기말 현재 기타포괄손익-공정가치 측정 금융자산의 내역")
        got = _by(self.facts(t), "FVOCI_EQUITY")
        self.assertEqual(got[0]["components"], ["㈜와우매니지먼트그룹", "㈜인핸스드바이오"])

    def test_new_c12_facts_marked(self):
        note = "기타포괄손익-공정가치측정금융자산"
        t = _table([("시장성 있는 지분증권", [0, 100])], note_title=note, title="(1) 내역", headers=["당기말", "전기말"])
        self.assertEqual({f.get("since") for f in _by(self.facts(t), "FVOCI_EQUITY")}, {"c12"})
        # c11 부터 있던 행 이름 개념(「기타포괄손익-공정가치측정 지분상품」)은 표시하지 않는다
        t = _table([("기타포괄손익-공정가치측정 지분상품", [0, 100])], note_title="금융상품", title="범주", headers=["당기말", "전기말"])
        self.assertEqual({f.get("since") for f in _by(self.facts(t), "FVOCI_EQUITY")}, {None})
        # c12 낱말(「기타포괄손익인식」)로만 걸린 행은 c12
        t = _table([("기타포괄손익인식지분상품", [0, 100])], note_title="금융상품", title="범주", headers=["당기말", "전기말"])
        self.assertEqual({f.get("since") for f in _by(self.facts(t), "FVOCI_EQUITY")}, {"c12"})
        # 주석 제목으로만 걸린 합계 행(성우하이텍 t045 모양)은 c12, 표 제목에 FVOCI·지분이 다 있는 합계 행은 c11 부터
        rows = [("A(주)", [10, 8]), ("B(주)", [5, 5]), ("합 계", [15, 13])]
        t = _table(rows, note_title=note, title="(3) 시장성 있는 지분증권의 내역", headers=["당기말", "전기말"])
        self.assertEqual({f.get("since") for f in _by(self.facts(t), "FVOCI_EQUITY")}, {"c12"})
        t = _table(rows, note_title=note, title="(1) 기타포괄손익-공정가치 측정 지분상품", headers=["당기말", "전기말"])
        self.assertEqual({f.get("since") for f in _by(self.facts(t), "FVOCI_EQUITY")}, {None})



class LeaseCashOutflowSentenceTest(unittest.TestCase):
    """c13(합의 규칙 28·29): 현금유출액 문장 속 단기·소액 리스료 → 리스 문장 사실. 총 현금유출액은 쓰지 않는다."""

    def got(self, paragraphs):
        from dart_search_mcp.ledger.facts import sentence_facts
        return [f for f in sentence_facts(_filing(paragraphs)) if f["concept"].startswith("LEASE_")]

    def test_daebong_sentences(self):
        # s098 대봉엘에스 20260323001154 연결 s1079·s1080, 별도 s434
        got = self.got([
            _p("s1079", "당기 중 변동리스에 따른 리스료 8,031,888원, 소액자산 리스에 따른 리스료 124,303,438원, 단기자산에 따른 "
                        "리스료 7,008,183원 및 관련 이자비용 59,880,643원을 포함하여 리스와 관련한 총 현금유출액은 830,452,195원입니다.",
               file="R_00761.xml"),
            _p("s1080", "전기 중 변동리스에 따른 리스료 4,390,776원, 소액자산 리스에 따른 리스료 27,801,024원, 단기자산에 따른 "
                        "리스료 1,896,362원 및 관련 이자비용 34,570,227원을 포함하여 리스와 관련한 총 현금유출액은 654,461,468원입니다.",
               file="R_00761.xml"),
            _p("s434", "당기 중 변동리스에 따른 리스료 8,031,888원, 소액자산 리스에 따른 리스료 108,751,356원 및 관련 이자비용 "
                       "13,827,336원을 포함하여 리스와 관련한 총 현금유출액은293,254,400원입니다."),
        ])
        self.assertEqual({(f["fs_scope"], f["concept"], f["period"], f["amount_krw"]) for f in got},
                         {("consolidated", "LEASE_LOW_VALUE_EXP", "current_period", 124303438),
                          ("consolidated", "LEASE_SHORT_TERM_EXP", "current_period", 7008183),
                          ("consolidated", "LEASE_LOW_VALUE_EXP", "prior_period", 27801024),
                          ("consolidated", "LEASE_SHORT_TERM_EXP", "prior_period", 1896362),
                          ("separate", "LEASE_LOW_VALUE_EXP", "current_period", 108751356)})
        self.assertTrue(all(f["match_basis"] == "sentence" for f in got))

    def test_total_outflow_never_an_expense(self):
        got = self.got([_p("s1", "당기 중 리스와 관련한 총 현금유출액은 830,452,195원입니다."),
                        _p("s2", "단기리스 및 소액자산리스와 관련된 현금유출을 포함한 총 현금유출액은 5,000원입니다."),
                        # 20260414002015: 「단기리스 또는 소액자산 리스료」는 합친 금액 — 소액으로 읽지 않는다
                        _p("s3", "한편, 당기 중 리스부채의 총 현금유출은 286,853천원(전기: 227,939천원), 당기비용으로 인식한 단기리스 또는 "
                                 "소액자산 리스료는 36,201천원(전기:29,489천원) 입니다.")])
        self.assertEqual(got, [])



class PpaCompletionSentenceTest(unittest.TestCase):
    """c13: 사업결합 회계처리 완료를 명시한 회사 문장 → PPA_COMPLETED_STMT(금액 없음)."""

    def got(self, paragraphs):
        from dart_search_mcp.ledger.facts import sentence_facts
        return [f for f in sentence_facts(_filing(paragraphs)) if f["concept"] == "PPA_COMPLETED_STMT"]

    def test_jiran_measurement_period_completed(self):
        # s137 지란지교시큐리티 20260323001717 연결 주석 39.(7) s1345
        got = self.got([_p("s1345", "(7) 측정기간2024년 7월 19일 취득한 소프트웨어 개발 및 보안 사업부문과 관련된 영업권은 기업회계기준서 "
                                    "제1103호 '사업결합' 문단 45에 따라 잠정금액으로 인식되었으며, 당기 중 식별가능 순자산에 대한 가치평가를 "
                                    "반영하여 측정기간 내에 사업결합 회계처리를 완료하였습니다.", file="R_00761.xml")])
        self.assertEqual([(f["fs_scope"], f["source"]["sentence_id"]) for f in got], [("consolidated", "s1345")])

    def test_other_completion_wordings(self):
        got = self.got([_p("s1", "당기 중 매수가격배분이 완료되어 영업권 금액이 조정되었습니다."),
                        _p("s2", "당기 중 피취득자의 식별가능한 순자산 공정가치 평가가 확정되었습니다.", file="R_00761.xml")])
        self.assertEqual({f["fs_scope"] for f in got}, {"separate", "consolidated"})

    def test_not_completion(self):
        self.assertEqual(self.got([
            _p("s1", "2016년 8월 30일을 합병기일로 하여 합병을 완료하였습니다."),
            _p("s2", "사업결합 회계처리가 보고기간말 현재 완료되지 못한다면, 잠정금액을 재무제표에 보고합니다."),
            _p("s3", "당기말 현재 매수가격배분이 완료되지 않아 잠정금액으로 표시하였습니다."),
            _p("s4", "배당을 받을 권리가 확정되는 시점에 당기손익으로 인식하고 있습니다."),
        ]), [])


if __name__ == "__main__":
    unittest.main()
