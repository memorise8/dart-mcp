import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.ledger.goldset import GOLDSET_PATH, load_goldset, score_layer1
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
        layer1 = [g for g in gold if g["layer"] == 1]
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


if __name__ == "__main__":
    unittest.main()
