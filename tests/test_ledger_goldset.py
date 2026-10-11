import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.ledger.compare import compare
from dart_search_mcp.ledger.goldset import (GOLDSET_PATH, load_goldset, score, score_layer1, score_layer2,
                                           score_layer3)
from dart_search_mcp.ledger.tables import write_filing


def _measure_module():
    spec = importlib.util.spec_from_file_location("ledger_measure_cli", Path(__file__).parents[1] / "scripts" / "ledger_measure.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ledger_measure_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def _table(tid: str, *, title="전환사채의 내역", status="verified", checks=None, rows=None, columns=None,
           region=None):
    return {
        "table_id": tid, "title": title, "status": status, "fs_scope": "consolidated",
        "region": region or {"kind": "note", "note_no": "17", "note_title": "사채", "sub_path": []},
        "columns": columns if columns is not None else [
            {"col": 0, "header_path": ["구 분"], "role": "label"},
            {"col": 1, "header_path": ["당기말"], "role": "value", "period": "current_end", "date": None},
            {"col": 2, "header_path": ["전기말"], "role": "value", "period": "prior_end", "date": None},
        ],
        "rows": rows if rows is not None else [
            {"row": 7, "label": "전환사채의 자본요소", "label_path": ["전환사채의 자본요소"], "level": 0, "acode": None,
             "kind": "item", "cells": [{"col": 1, "raw": "121,352", "value": 121352, "value_krw": 121352000},
                                       {"col": 2, "raw": "519,263", "value": 519263, "value_krw": 519263000}]},
        ],
        "checks": checks or {"period_found": True, "unit_found": True, "sum_ok": None, "identity_ok": None},
        "issues": [],
    }


def _filing(rcept: str, tables: list[dict]) -> dict:
    return {"rcept_no": rcept, "amends_rcept_no": None, "period_end": "2025-12-31", "parser_version": "x",
            "paragraphs": [], "tables": tables}


class ScoreLayer1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        write_filing(self.tmp, "R1", _filing("R1", [_table("R1:00761:t087")]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_right_and_wrong_answers(self):
        gold = [
            {"id": "ok", "layer": 1, "rcept_no": "R1", "label_contains": "자본요소", "period": "prior_end", "value_krw": 519263000},
            {"id": "wrong-period", "layer": 1, "rcept_no": "R1", "label_contains": "자본요소", "period": "current_end", "value_krw": 519263000},
        ]
        res = {r["id"]: r for r in score_layer1(gold, self.tmp)}
        self.assertTrue(res["ok"]["ok"])
        self.assertIn("R1:00761:t087", res["ok"]["detail"])
        self.assertIn("r7", res["ok"]["detail"])
        self.assertIn("c2", res["ok"]["detail"])
        self.assertFalse(res["wrong-period"]["ok"])
        self.assertIn("121352000", res["wrong-period"]["detail"])  # 가까운 후보(같은 행)의 값을 보여 준다

    def test_missing_filing_and_other_layers(self):
        gold = [{"id": "none", "layer": 1, "rcept_no": "R9", "label_contains": "자본요소", "period": "current_end", "value_krw": 1},
                {"id": "l2", "layer": 2, "rcept_no": "R1"}]
        res = score_layer1(gold, self.tmp)
        self.assertEqual([r["id"] for r in res], ["none"])
        self.assertFalse(res[0]["ok"])
        self.assertIn("파일 없음", res[0]["detail"])

    def test_header_and_title_filters(self):
        cols = [{"col": 0, "header_path": ["구분"], "role": "label"},
                {"col": 1, "header_path": ["당기말", "상각후원가"], "role": "value", "period": "current_end", "date": None},
                {"col": 2, "header_path": ["당기말", "기타포괄손익-공정가치"], "role": "value", "period": "current_end", "date": None}]
        rows = [{"row": 3, "label": "합계", "label_path": ["합계"], "level": 0, "acode": None, "kind": "total",
                 "cells": [{"col": 1, "raw": "500", "value": 500, "value_krw": 500000},
                           {"col": 2, "raw": "-", "value": 0, "value_krw": 0}]}]
        write_filing(self.tmp, "R2", _filing("R2", [_table("R2:00760:t010", title="범주별 금융상품", rows=rows, columns=cols)]))
        base = {"layer": 1, "rcept_no": "R2", "label_contains": "합계", "period": "current_end"}
        gold = [dict(base, id="fvoci", header_contains="기타포괄", value_krw=0),
                dict(base, id="fvoci-wrong", header_contains="기타포괄", value_krw=500000),
                dict(base, id="title", title_contains="범주", header_contains="상각후원가", value_krw=500000),
                dict(base, id="title-wrong", title_contains="사채", value_krw=500000)]
        res = {r["id"]: r["ok"] for r in score_layer1(gold, self.tmp)}
        self.assertEqual(res, {"fvoci": True, "fvoci-wrong": False, "title": True, "title-wrong": False})

    def test_read_failed_table_does_not_count(self):
        write_filing(self.tmp, "R3", _filing("R3", [_table("R3:main:t001", status="read_failed")]))
        gold = [{"id": "rf", "layer": 1, "rcept_no": "R3", "label_contains": "자본요소", "period": "prior_end", "value_krw": 519263000}]
        self.assertFalse(score_layer1(gold, self.tmp)[0]["ok"])

    def test_goldset_file_has_seven_layer1_items(self):
        gold = load_goldset(GOLDSET_PATH)
        layer1 = [g for g in gold if g["layer"] == 1 and g.get("source", "accountant") == "accountant"]
        self.assertEqual(len({g["rcept_no"] for g in layer1}), 7)
        for g in layer1:
            for key in ("id", "rcept_no", "label_contains", "period", "value_krw"):
                self.assertIn(key, g, g.get("id"))
        ildong = {g["id"]: g for g in layer1 if g["rcept_no"] == "20260318001076"}
        self.assertEqual({(g["period"], g["value_krw"]) for g in ildong.values()},
                         {("current_end", 121352000), ("prior_end", 519263000)})


class MeasureTablesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cli = _measure_module()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ratios(self):
        t = lambda tid, **kw: _table(tid, **kw)  # noqa: E731
        tables = [
            t("A:t1", checks={"period_found": True, "unit_found": True, "sum_ok": True, "identity_ok": None}),
            t("A:t2", status="needs_review", checks={"period_found": False, "unit_found": True, "sum_ok": False, "identity_ok": None}),
            t("A:t3", checks={"period_found": None, "unit_found": None, "sum_ok": None, "identity_ok": None}),  # 값 열 없음
            t("A:t4", status="read_failed", checks={"period_found": None, "unit_found": None, "sum_ok": None, "identity_ok": None}),
        ]
        write_filing(self.tmp, "A", _filing("A", tables))
        m = self.cli.measure_tables(self.tmp, ["A", "B"])
        self.assertEqual(m["filings"], 1)
        self.assertEqual(m["filings_missing"], ["B"])
        self.assertEqual(m["tables"], 4)
        self.assertAlmostEqual(m["period_found_ratio"], 0.5)   # 값 열 있는 표 2개 중 1
        self.assertAlmostEqual(m["unit_found_ratio"], 1.0)
        self.assertAlmostEqual(m["sum_ok_ratio"], 0.5)        # 합계 행 있는 표 2개 중 1
        self.assertAlmostEqual(m["read_failed_ratio"], 0.25)  # 전체 표 4개 중 1
        self.assertEqual(m["counts"]["period_found"], {"true": 1, "false": 1})

    def test_adjusted_ratios_exclude_non_financial_tables(self):  # R8
        chk = lambda pf: {"period_found": pf, "unit_found": pf, "sum_ok": None, "identity_ok": None}  # noqa: E731
        tables = [
            _table("A:t1", checks=chk(True)),
            _table("A:t2", checks=chk(False)),
            _table("A:t3", checks=chk(False), region={"kind": "other", "note_no": None, "note_title": None, "sub_path": []}),
            _table("A:t4", checks=chk(False), region={"kind": "auditor_report", "note_no": None, "note_title": None, "sub_path": []}),
            _table("A:t5", checks=chk(False), region={"kind": "statement_ce", "note_no": None, "note_title": None, "sub_path": []}),
            _table("A:t6", checks=chk(True), region={"kind": "statement_bs", "note_no": None, "note_title": None, "sub_path": []}),
        ]
        write_filing(self.tmp, "A", _filing("A", tables))
        m = self.cli.measure_tables(self.tmp, ["A"])
        self.assertAlmostEqual(m["period_found_ratio"], 2 / 6, places=4)  # 원 정의 그대로
        adj = m["adjusted"]
        self.assertEqual(adj["excluded_region_kinds"], ["auditor_report", "other", "statement_ce"])
        self.assertEqual(adj["tables"], 3)
        self.assertEqual(adj["excluded_tables"], 3)
        self.assertAlmostEqual(adj["period_found_ratio"], 2 / 3, places=4)
        self.assertAlmostEqual(adj["unit_found_ratio"], 2 / 3, places=4)
        self.assertEqual(adj["counts"]["period_found"], {"true": 2, "false": 1})



def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def _fact(rcept, concept, period, amount_krw, scope="consolidated"):
    return {"rcept_no": rcept, "fs_scope": scope, "concept": concept, "period": period,
            "amount": amount_krw, "amount_krw": amount_krw, "label": concept}


def _stance(rcept, topic, value, held="", scope="separate"):
    return {"rcept_no": rcept, "topic": topic, "value": value, "held_reason": held, "fs_scope": scope,
            "parser_version": "ws8-v2"}


CONV, EMB, FVOCI = "CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION", "FVOCI_DESIGNATION"


class ScoreLayer2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.facts = _write_jsonl(self.tmp / "facts.jsonl", [
            _fact("R1", "CONV_OPTION_EQUITY", "prior_end", 519263000),
            _fact("R1", "HOST_LIABILITY", "current_end", 0),
            _fact("R2", "EMB_DERIV_LIABILITY", "current_end", -2500419206),  # 괄호 음수
            _fact("R3", "HOST_LIABILITY", "prior_end", 517994000),
        ])
        rec = lambda oc, es: {"option_class": oc, "embedded_separation": es}  # noqa: E731
        self.run1 = _write_jsonl(self.tmp / "run1.jsonl", [
            {"rcept_no": "R1", "fs_scope": "consolidated", "records": [rec("equity", "unconfirmed")]},
            {"rcept_no": "R2", "fs_scope": "consolidated", "records": [rec("unconfirmed", "separated")]},
        ])
        self.run2 = _write_jsonl(self.tmp / "run2.jsonl", [
            {"rcept_no": "R1", "fs_scope": "consolidated", "records": [rec("equity", "unconfirmed"),
                                                                       rec("derivative_liability", "unconfirmed")]},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fact_present_absent_and_scope(self):
        base = {"layer": 2, "source": "fact"}
        gold = [
            dict(base, id="eq", rcept_no="R1", concept="CONV_OPTION_EQUITY", period="prior_end", value_krw=519263000),
            dict(base, id="eq-wrong-period", rcept_no="R1", concept="CONV_OPTION_EQUITY", period="current_end",
                 value_krw=519263000),
            dict(base, id="eq-wrong-scope", rcept_no="R1", concept="CONV_OPTION_EQUITY", fs_scope="separate",
                 period="prior_end", value_krw=519263000),
            dict(base, id="emb-abs", rcept_no="R2", concept=["EMB_DERIV_LIABILITY", "CONV_OPTION_LIABILITY"],
                 period="current_end", value_krw=2500419206),
            dict(base, id="no-host-cur", rcept_no="R1", concept="HOST_LIABILITY", period="current_end", absent=True),
            dict(base, id="no-host-r3", rcept_no="R3", concept="HOST_LIABILITY", absent=True),
            dict(base, id="no-host-r3-cur", rcept_no="R3", concept="HOST_LIABILITY", period=["current_end"], absent=True),
        ]
        res = {r["id"]: r for r in score_layer2(gold, self.facts, [])}
        self.assertEqual({k: v["ok"] for k, v in res.items()},
                         {"eq": True, "eq-wrong-period": False, "eq-wrong-scope": False, "emb-abs": True,
                          "no-host-cur": True, "no-host-r3": False, "no-host-r3-cur": True})
        self.assertIn("519263000", res["eq-wrong-period"]["detail"])  # 가까운 후보
        self.assertIn("517994000", res["no-host-r3"]["detail"])        # 있어선 안 되는 사실

    def test_instrument_items_need_every_run(self):
        base = {"layer": 2, "source": "instrument"}
        gold = [
            dict(base, id="eq", rcept_no="R1", field="option_class", equals="equity"),
            dict(base, id="no-liab", rcept_no="R1", field="option_class", equals="derivative_liability", absent=True),
            dict(base, id="sep", rcept_no="R2", field="embedded_separation", equals="separated"),
            dict(base, id="sep-scope", rcept_no="R2", fs_scope="separate", field="embedded_separation",
                 equals="separated"),
        ]
        one = {r["id"]: r["ok"] for r in score_layer2(gold, self.facts, [self.run1])}
        self.assertEqual(one, {"eq": True, "no-liab": True, "sep": True, "sep-scope": False})
        both = {r["id"]: r for r in score_layer2(gold, self.facts, [self.run1, self.run2])}
        self.assertTrue(both["eq"]["ok"])
        self.assertFalse(both["no-liab"]["ok"])   # run2 에 부채 기록
        self.assertFalse(both["sep"]["ok"])       # run2 에 R2 없음
        self.assertIn("run2", both["sep"]["detail"])

    def test_rcepts_filter_skips_items(self):
        gold = [{"id": "eq", "layer": 2, "source": "fact", "rcept_no": "R1", "concept": "CONV_OPTION_EQUITY",
                 "period": "prior_end", "value_krw": 519263000}]
        self.assertEqual(score_layer2(gold, self.facts, [], rcepts={"R9"}), [])


class ScoreLayer3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.stance = _write_jsonl(self.tmp / "v2.jsonl", [
            _stance("A", CONV, "EQUITY", scope="consolidated"),
            _stance("A", CONV, "UNDETERMINED", "no_facts", scope="separate"),
            _stance("B", CONV, "LIABILITY"),
            _stance("C", EMB, "UNDETERMINED", "needs_review"),
            _stance("D", FVOCI, "UNDETERMINED", "policy_only"),
            _stance("E", FVOCI, "NOT_DESIGNATED"),
            _stance("F", EMB, "SEPARATED", scope="separate"),
            _stance("F", EMB, "NOT_SEPARATED", scope="consolidated"),
            _stance("G", FVOCI, "UNDETERMINED", "no_facts"),
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gold(self, id_, rcept, topic, expect, **kw):
        return dict({"id": id_, "layer": 3, "rcept_no": rcept, "topic": topic, "expect": expect}, **kw)

    def test_statuses(self):
        gold = [
            self._gold("pass", "A", CONV, ["EQUITY"]),
            self._gold("wrong", "B", CONV, ["EQUITY"]),                         # s015 유형: 부채가 나오면 틀림
            self._gold("miss", "C", EMB, ["SEPARATED"]),                        # 판단불가 = 재현율 놓침(틀림 아님)
            self._gold("und-ok", "D", FVOCI, ["UNDETERMINED"], held_reason="policy_only"),
            self._gold("und-wrong", "E", FVOCI, ["UNDETERMINED"]),              # s110: 값이 나오면 틀림
            self._gold("und-alt", "E", FVOCI, ["UNDETERMINED", "NOT_DESIGNATED"]),
            self._gold("split", "F", EMB, ["SEPARATED"]),                       # 한 범위라도 다른 값이면 틀림
            self._gold("scope", "F", EMB, ["SEPARATED"], fs_scope="separate"),
            self._gold("held-diff", "G", FVOCI, ["UNDETERMINED"], held_reason="policy_only"),
            self._gold("no-row-und", "Z", CONV, ["UNDETERMINED"]),
            self._gold("no-row-val", "Z", CONV, ["EQUITY"]),
        ]
        res = {r["id"]: r for r in score_layer3(gold, self.stance)}
        self.assertEqual({k: v["status"] for k, v in res.items()}, {
            "pass": "pass", "wrong": "wrong", "miss": "miss", "und-ok": "pass", "und-wrong": "wrong",
            "und-alt": "pass", "split": "wrong", "scope": "pass", "held-diff": "pass", "no-row-und": "pass",
            "no-row-val": "miss"})
        self.assertEqual(res["wrong"]["got"], ["LIABILITY"])
        self.assertIn("no_facts", res["held-diff"]["detail"])   # 기대 사유와 다르면 적어 둔다
        self.assertIn("행 없음", res["no-row-val"]["detail"])

    def test_score_combines_layers(self):
        tables = self.tmp / "tables"
        tables.mkdir()
        write_filing(tables, "R1", _filing("R1", [_table("R1:00761:t087")]))
        facts = _write_jsonl(self.tmp / "facts.jsonl", [_fact("R1", "CONV_OPTION_EQUITY", "prior_end", 519263000)])
        gold = [
            {"id": "l1", "layer": 1, "rcept_no": "R1", "label_contains": "자본요소", "period": "prior_end",
             "value_krw": 519263000},
            {"id": "l1-out", "layer": 1, "rcept_no": "R9", "label_contains": "자본요소", "period": "prior_end",
             "value_krw": 1},
            {"id": "l2", "layer": 2, "source": "fact", "rcept_no": "R1", "concept": "CONV_OPTION_EQUITY",
             "period": "current_end", "value_krw": 1},
            self._gold("l3", "B", CONV, ["EQUITY"]),
            self._gold("l3-miss", "C", EMB, ["SEPARATED"]),
            self._gold("l3-ok", "A", CONV, ["EQUITY"]),
        ]
        out = score(gold, tables, facts, None, self.stance, rcepts={"R1", "A", "B", "C"})
        self.assertEqual(out[1]["passed"], 1)
        self.assertEqual(out[1]["failed"], [])
        self.assertEqual(out[1]["skipped"], 1)           # 표본 밖 항목
        self.assertEqual(out[2]["failed"], ["l2"])
        self.assertEqual(out[3]["passed"], 1)
        self.assertEqual(out[3]["failed"], ["l3"])       # 틀린 판정만 failed(스펙 §7.2 관문)
        self.assertEqual(out[3]["missed"], ["l3-miss"])
        self.assertEqual(len(out[3]["items"]), 3)


class GoldsetFileTest(unittest.TestCase):
    def test_layer3_items_follow_accountant_reading(self):
        gold = load_goldset(GOLDSET_PATH)
        l3 = {g["case"]: g for g in gold if g["layer"] == 3 and g.get("case")}
        for case in [f"s{i:03d}" for i in range(1, 41)] + ["s110"]:
            self.assertIn(case, l3, case)
        self.assertEqual(l3["s015"]["expect"], ["EQUITY"])
        self.assertEqual(l3["s026"]["expect"], ["SEPARATED"])
        self.assertEqual(l3["s110"]["expect"], ["UNDETERMINED"])
        self.assertEqual(l3["s014"]["expect"], ["UNDETERMINED"])
        self.assertEqual(l3["s016"]["expect"], ["UNDETERMINED"])
        self.assertIn("UNDETERMINED", l3["s038"]["expect"])
        self.assertNotIn("NOT_SEPARATED", l3["s038"]["expect"])
        self.assertEqual(l3["s001"]["expect"], ["EQUITY"])     # ✓ → v1 값
        for g in gold:
            if g["layer"] == 3:
                self.assertTrue(g.get("comment"), g["id"])     # 메모 읽기를 남긴다
                self.assertIn(g["topic"], (CONV, EMB, FVOCI, "DEV_COST_CAPITALIZATION", "LEASE_EXEMPTION"))
        # 빠르게 넘긴 103건(s041~s143, s110 제외)은 정답으로 쓰지 않는다(스펙 §7.1)
        rushed = {f"s{i:03d}" for i in range(41, 144)} - {"s110"}
        self.assertFalse(rushed & {g.get("case") for g in gold})

    def test_handcheck_items_present(self):
        gold = load_goldset(GOLDSET_PATH)
        hc = [g for g in gold if g["layer"] == 3 and g.get("source", "").startswith("handcheck")]
        self.assertGreaterEqual(len(hc), 10)
        ids = [g["id"] for g in gold]
        self.assertEqual(len(ids), len(set(ids)))


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.v1 = _write_jsonl(self.tmp / "v1.jsonl", [
            {"rcept_no": "A", "topic": CONV, "value": "EQUITY", "held_reason": ""},
            {"rcept_no": "B", "topic": CONV, "value": "EQUITY", "held_reason": ""},
            {"rcept_no": "C", "topic": CONV, "value": "LIABILITY", "held_reason": ""},
            {"rcept_no": "D", "topic": CONV, "value": "UNDETERMINED", "held_reason": "model_undetermined"},
            {"rcept_no": "E", "topic": EMB, "value": "NOT_SEPARATED", "held_reason": ""},
            {"rcept_no": "F", "topic": EMB, "value": "SEPARATED", "held_reason": ""},
        ])
        self.v2 = _write_jsonl(self.tmp / "v2.jsonl", [
            _stance("A", CONV, "EQUITY", scope="separate"),
            _stance("A", CONV, "UNDETERMINED", "no_facts", scope="consolidated"),
            _stance("B", CONV, "UNDETERMINED", "policy_only"),
            _stance("C", CONV, "EQUITY"),
            _stance("D", CONV, "LIABILITY"),
            _stance("E", EMB, "SEPARATED"),
            _stance("G", EMB, "UNDETERMINED", "needs_review"),
        ])
        self.cls = _write_jsonl(self.tmp / "classified.jsonl", [
            {"rcept_no": "B", "topic": CONV, "value": "EQUITY", "cls_final": "policy+no-holding"},
            {"rcept_no": "A", "topic": CONV, "value": "EQUITY", "cls_final": "policy+no-holding"},
            {"rcept_no": "E", "topic": EMB, "value": "NOT_SEPARATED", "cls_final": "policy+contradicted"},
            {"rcept_no": "F", "topic": EMB, "value": "SEPARATED", "cls_final": "policy+no-holding"},
            {"rcept_no": "C", "topic": CONV, "value": "LIABILITY", "cls_final": "fact"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_matrix_and_directions(self):
        out = compare(self.v1, self.v2)
        conv = out["topics"][CONV]
        self.assertEqual(conv["matrix"]["EQUITY"], {"EQUITY": 1, "UNDETERMINED/policy_only": 1})
        self.assertEqual(conv["matrix"]["LIABILITY"], {"EQUITY": 1})
        self.assertEqual(conv["matrix"]["UNDETERMINED"], {"LIABILITY": 1})
        self.assertEqual(conv["directions"], {"same": 1, "value→UNDETERMINED": 1, "value→other": 1,
                                              "UNDETERMINED→value": 1})
        self.assertEqual(conv["changed"], 3)
        emb = out["topics"][EMB]
        self.assertEqual(emb["directions"], {"value→other": 1, "v1_only": 1, "v2_only": 1})
        self.assertEqual(emb["matrix"]["(없음)"], {"UNDETERMINED/needs_review": 1})

    def test_measure_rows(self):
        out = compare(self.v1, self.v2, classified_path=self.cls)
        po = out["measure"]["policy+no-holding"]
        self.assertEqual(po["n"], 3)
        self.assertEqual(po["by_topic"][CONV], {"same": 1, "UNDETERMINED/policy_only": 1})
        self.assertEqual(po["by_topic"][EMB], {"v2 행 없음": 1})
        ct = out["measure"]["policy+contradicted"]
        self.assertEqual(ct["by_topic"][EMB], {"other:SEPARATED": 1})
        self.assertNotIn("fact", out["measure"])

    def test_rcepts_filter(self):
        out = compare(self.v1, self.v2, classified_path=self.cls, rcepts={"A", "B"})
        self.assertEqual(out["topics"][CONV]["directions"], {"same": 1, "value→UNDETERMINED": 1})
        self.assertNotIn(EMB, out["topics"])
        self.assertEqual(out["measure"]["policy+no-holding"]["n"], 2)



class MeasureFullTest(unittest.TestCase):
    """scripts/ledger_measure.py 전체 층: 판정 실행(또는 주어진 v2) → 정답 채점 → v1·v2 비교 → ledger.measure.md."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cli = _measure_module()
        self.tables = self.tmp / "tables"
        self.tables.mkdir()
        write_filing(self.tables, "R1", _filing("R1", [_table("R1:00761:t087")]))
        self.sample = self.tmp / "sample.txt"
        self.sample.write_text("R1\n", encoding="utf-8")
        self.v1 = _write_jsonl(self.tmp / "v1.jsonl", [
            {"rcept_no": "R1", "corp_code": "1", "fiscal_year": 2025, "topic": CONV, "value": "LIABILITY",
             "held_reason": ""},
            {"rcept_no": "R2", "corp_code": "2", "fiscal_year": 2025, "topic": CONV, "value": "EQUITY",
             "held_reason": ""},
        ])
        self.cls = _write_jsonl(self.tmp / "classified.jsonl", [
            {"rcept_no": "R1", "topic": CONV, "value": "LIABILITY", "cls_final": "policy+contradicted"}])
        self.gold = _write_jsonl(self.tmp / "gold.jsonl", [
            {"id": "l1", "layer": 1, "rcept_no": "R1", "label_contains": "자본요소", "period": "prior_end",
             "value_krw": 519263000},
            {"id": "l2", "layer": 2, "source": "fact", "rcept_no": "R1", "concept": "CONV_OPTION_EQUITY",
             "period": "prior_end", "value_krw": 519263000},
            {"id": "l3", "layer": 3, "rcept_no": "R1", "topic": CONV, "expect": ["EQUITY"], "comment": "x"},
            {"id": "l3-out", "layer": 3, "rcept_no": "R2", "topic": CONV, "expect": ["EQUITY"], "comment": "x"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_runs_judge_and_writes_markdown(self):
        facts = _write_jsonl(self.tmp / "facts.jsonl", [
            dict(_fact("R1", "CONV_OPTION_EQUITY", "prior_end", 519263000), concepts_version="c7"),
            dict(_fact("R2", "CONV_OPTION_EQUITY", "prior_end", 1), concepts_version="c7")])
        marks = _write_jsonl(self.tmp / "marks.jsonl", [])
        empty = _write_jsonl(self.tmp / "run.jsonl", [])
        audit = _write_jsonl(self.tmp / "audit.jsonl", [{"rcept_no": "R1", "corp_code": "1", "fiscal_year": 2025}])
        work = self.tmp / "work"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.cli.main(["--out-dir", str(self.tables), "--sample", str(self.sample), "--gold", str(self.gold),
                                "--facts", str(facts), "--marks", str(marks), "--facts-audit", str(audit),
                                "--instruments", str(empty), "--instruments2", str(empty),
                                "--v1-stance", str(self.v1), "--classified", str(self.cls), "--work-dir", str(work)])
        self.assertEqual(rc, 0)
        res = json.loads((work / "ledger.measure.json").read_text(encoding="utf-8"))
        # 판정기 입력은 표본으로 좁힌다(R2 사실은 빠진다)
        kept = [json.loads(x)["rcept_no"] for x in (work / "ledger" / "facts.jsonl").read_text().splitlines()]
        self.assertEqual(kept, ["R1"])
        gs = res["goldset"]
        self.assertEqual(gs["1"]["passed"], 1)
        self.assertEqual(gs["2"]["passed"], 1)
        self.assertEqual(gs["3"]["passed"], 1)
        self.assertEqual(gs["3"]["skipped"], 1)
        self.assertEqual(res["compare"]["topics"][CONV]["directions"], {"value→other": 1})
        self.assertEqual(res["compare"]["measure"]["policy+contradicted"]["totals"], {"other:EQUITY": 1})
        md = (work / "ledger.measure.md").read_text(encoding="utf-8")
        self.assertIn("3층", md)
        self.assertIn("틀린 판정 0", md)
        self.assertIn("LIABILITY", md)

    def test_given_v2_stance_reports_wrong(self):
        v2 = _write_jsonl(self.tmp / "v2.jsonl", [_stance("R1", CONV, "LIABILITY", scope="consolidated")])
        md = self.tmp / "out.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.cli.main(["--out-dir", str(self.tables), "--sample", str(self.sample), "--gold", str(self.gold),
                                "--v1-stance", str(self.v1), "--v2-stance", str(v2), "--md", str(md)])
        self.assertEqual(rc, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("틀린 판정 1", text)
        self.assertIn("l3", text)


if __name__ == "__main__":
    unittest.main()
