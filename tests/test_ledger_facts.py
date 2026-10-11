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

    def test_version_c7(self):  # c6 = T6b, c7 = T6b 수정 1차(R25)
        self.assertEqual(self.concepts[0].version, "c7")

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
        self.assertEqual(self._equity(rows, title="기타포괄손익-공정가치 측정 지분상품 및 채무상품"), [])
        self.assertEqual(self._equity(rows, title="당기손익-공정가치 및 기타포괄손익-공정가치 측정 지분상품"), [])

    def test_debt_row_in_equity_titled_table_no_fact(self):
        # 20260311004362 t082 모양: 제목은 지분증권 지정, 행에 「채무증권」 → 합계가 채무를 포함
        rows = [("시장성 없는 지분증권", [10, 8]), ("채무증권", [5, 5]), ("합 계", [15, 13])]
        self.assertEqual(self._equity(rows, title="장기로 보유할 지분증권을 기타포괄손익-공정가치 측정 항목으로 지정하였습니다."), [])

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
        self.assertEqual(self._equity(rows, title="기타포괄손익-공정가치 측정 금융자산 내역"), [])
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


if __name__ == "__main__":
    unittest.main()
