"""정정 계보(공시 단위) — ledger/lineage.py·scripts/ledger_lineage.py."""
import json
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.ledger.lineage import build_lineage, revision_tags


def A(rc, name, corp="00000001", dt=None, opinion="적정"):
    return {"rcept_no": rc, "corp_code": corp, "report_name": name, "rcept_dt": dt or rc[:8], "audit_opinion": opinion}


class LineageTest(unittest.TestCase):
    def test_tinkware_attachment_correction_is_latest(self):
        # 팅크웨어: 원본에 2024년 감사보고서가 붙었고 [첨부정정]이 2025년분
        lin, mem, st = build_lineage([A("20260320001267", "사업보고서 (2025.12)"),
                                      A("20260323001094", "[첨부정정]사업보고서 (2025.12)")])
        self.assertEqual(lin, [{"corp_code": "00000001", "base_report_nm": "사업보고서 (2025.12)",
                                "first_rcept_no": "20260320001267", "last_rcept_no": "20260323001094",
                                "first_rcept_dt": "20260320", "last_rcept_dt": "20260323",
                                "revision_count": 1, "revision_types": "첨부정정"}])
        self.assertEqual([(m["rcept_no"], m["is_latest"], m["revision_type"]) for m in mem],
                         [("20260320001267", False, None), ("20260323001094", True, "첨부정정")])
        self.assertTrue(all(m["last_rcept_no"] == "20260323001094" for m in mem))
        self.assertEqual((st["lineage"], st["members"], st["not_latest"]), (1, 2, 1))

    def test_stacked_tags_same_day_and_order(self):
        lin, mem, _ = build_lineage([A("20260331000900", "[기재정정][첨부추가] 감사보고서 (2025.12)"),
                                     A("20260331000100", "감사보고서 (2025.12)"),
                                     A("20260402000005", "[기재정정]감사보고서 (2025.12)")])
        self.assertEqual(lin[0]["revision_types"], "기재정정|첨부추가")
        self.assertEqual(lin[0]["revision_count"], 2)
        self.assertEqual(lin[0]["last_rcept_no"], "20260402000005")
        self.assertEqual([m["rcept_no"] for m in mem], ["20260331000100", "20260331000900", "20260402000005"])
        self.assertEqual(mem[1]["revision_type"], "기재정정|첨부추가")

    def test_lone_and_other_reports_not_chained(self):
        lin, mem, st = build_lineage([A("20260320000001", "[기재정정]사업보고서 (2025.12)"),
                                      A("20260320000002", "사업보고서 (2024.12)"),
                                      A("20260320000003", "감사보고서 (2025.12)", corp="00000002"),
                                      A("20260320000004", "사업보고서 (2025.12)", corp="00000002")])
        self.assertEqual((lin, mem), ([], []))
        self.assertEqual(st["lone_correction"], 1)

    def test_corrections_only_chain_and_bad_rows(self):
        lin, mem, st = build_lineage([A("20260401000001", "[기재정정]사업보고서 (2025.12)"),
                                      A("20260405000001", "[기재정정]사업보고서 (2025.12)"),
                                      A("20260405000001", "[기재정정]사업보고서 (2025.12)"),
                                      {"rcept_no": "", "corp_code": "1", "report_name": "x"}])
        self.assertEqual((lin[0]["first_rcept_no"], lin[0]["revision_count"]), ("20260401000001", 2))
        self.assertIsNotNone(mem[0]["revision_type"])
        self.assertEqual((st["duplicate_rcept"], st["skipped_no_key"]), (1, 1))

    def test_revision_tags_prefix_only(self):
        self.assertEqual(revision_tags("사업보고서 [첨부]"), [])
        self.assertEqual(revision_tags(" [ 기재정정 ] 사업보고서"), ["기재정정"])


class LineageScriptTest(unittest.TestCase):
    def test_outputs_review_and_superseded_check(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ledger_lineage_cli",
                                                      Path(__file__).parents[1] / "scripts" / "ledger_lineage.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        main = cli.main
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            audit = d / "audit.jsonl"
            rows = [A("20260320001267", "사업보고서 (2025.12)"),
                    A("20260323001094", "[첨부정정]사업보고서 (2025.12)", opinion="unknown"),
                    A("20260320000001", "[기재정정]사업보고서 (2025.12)", corp="00000009")]
            audit.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            sup = d / "sup.jsonl"
            sup.write_text(json.dumps({"rcept_no": "20260320001267", "superseded_by": "20260323001094"}) + "\n"
                           + json.dumps({"rcept_no": "20260101000001", "superseded_by": "x"}) + "\n", encoding="utf-8")
            out = d / "out"
            self.assertEqual(main(["--audit", str(audit), "--out-dir", str(out), "--superseded", str(sup)]), 0)
            s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual((s["lineage"], s["members"], s["not_latest"]), (1, 2, 1))
            self.assertEqual(s["review"], {"latest_opinion_unread": 1, "opinion_changed": 0, "lone_correction": 1})
            self.assertEqual(s["superseded_check"], {"file": str(sup), "superseded_rcept": 2, "in_members_false": 1,
                                                     "mismatch": 1})
            self.assertEqual(main(["--audit", str(audit), "--out-dir", str(out)]), 2)  # 덮지 않는다


if __name__ == "__main__":
    unittest.main()


class LineageShapeTest(unittest.TestCase):
    def test_bad_shape_skipped_and_empty_tag_ignored(self):
        lin, mem, st = build_lineage([A("2026032000126", "사업보고서 (2025.12)"),  # 13자리
                                      A("20260320000001", "사업보고서 (2025.12)", corp="126380"),
                                      A("20260320000002", "x" * 257),
                                      A("20260320000003", "[]사업보고서 (2025.12)"),
                                      A("20260321000003", "사업보고서 (2025.12)")])
        self.assertEqual(st["skipped_bad_shape"], 3)
        self.assertEqual((lin[0]["revision_count"], lin[0]["revision_types"]), (0, ""))
        self.assertIsNone(mem[0]["revision_type"])


class DropSupersededRightsTest(unittest.TestCase):
    def test_tinkware_shape_and_no_replacement_counted(self):
        from dart_search_mcp.ledger.supersede import drop_superseded_rights

        def r(rc, key, scope="consolidated"):
            return {"rcept_no": rc, "fs_scope": scope, "product_key": key, "right": "conversion"}
        rights = [r("20260320001267", "RCPS-1"), r("20260323001094", "RCPS-4"), r("20260316001138", "CB-3"),
                  r("20260316001138", "CB-3", "separate")]
        dropped = [{"rcept_no": "20260320001267", "fs_scope": "consolidated", "superseded_by": "20260323001094"},
                   {"rcept_no": "20260316001138", "fs_scope": "consolidated", "superseded_by": "20260316001151"}]
        kept, st = drop_superseded_rights(rights, dropped)
        self.assertEqual([(x["rcept_no"], x["fs_scope"], x["product_key"]) for x in kept],
                         [("20260323001094", "consolidated", "RCPS-4"), ("20260316001138", "separate", "CB-3")])
        self.assertEqual(st, {"rights_dropped_superseded": 2, "rights_dropped_no_replacement": 1})
