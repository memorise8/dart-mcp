"""Phase 1 재추출 보조 스크립트(`scripts/phase1_regression.py`·`scripts/phase1_diff.py`) 단위 시험.

산출물 전량(47k 공시)이 아니라 3~5행짜리 픽스처 jsonl 을 임시 폴더에 만들어 검사한다 —
네트워크·LLM·실제 산출물을 건드리지 않는다. 실제 기대값 표(회계사 확인 27건)는 스크립트
상수에 있고, 여기서는 그 표를 **주입**해 판정 로직만 본다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import phase1_diff  # noqa: E402
import phase1_regression as reg  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _out_dir(tmp: str, *, audit: list[dict], kam: list[dict], policy: list[dict]) -> Path:
    d = Path(tmp)
    _write_jsonl(d / "audit_facts.jsonl", audit)
    _write_jsonl(d / "kam_items.jsonl", kam)
    _write_jsonl(d / "policy_stance.jsonl", policy)
    return d


_AUDIT_ROW = {
    "rcept_no": "1",
    "corp_name": "가",
    "audit_opinion": "적정",
    "opinion_status": "confirmed",
    "icfr_opinion": "부적정",
    "going_concern": False,
    "gc_material_uncertainty": "not_disclosed",
    "gc_location": "",
    "emphasis_status": "present",
    "emphasis_raw": "강조",
    "parser_version": "af-v2",
}
_POLICY_ROW = {
    "rcept_no": "1",
    "topic": "STD_EARLY_ADOPTION",
    "subject": "1118",
    "value": "NOT_ADOPTED",
    "evidence": "회사는 검토 중에 있습니다.",
    "held_reason": "",
    "sentence_kind": "company_statement",
    "parser_version": "ps-v2",
}


class RegressionChecks(unittest.TestCase):
    def test_matching_output_passes(self) -> None:
        """기대값과 산출물이 같으면 전 항목 PASS 이고 main 은 0 을 돌려준다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(
                tmp,
                audit=[_AUDIT_ROW],
                kam=[{"rcept_no": "1", "item_kind": "kam"}, {"rcept_no": "1", "item_kind": "preamble"}],
                policy=[_POLICY_ROW],
            )
            checks = reg.run_checks(
                d,
                audit_expectations=(
                    reg.AuditExpectation("1", "가", (("audit_opinion", "적정"), ("icfr_opinion", "부적정")), kam_items=1),
                ),
                policy_expectations=(
                    reg.PolicyExpectation("1", "가", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED",
                                          evidence_contains="검토 중"),
                ),
            )
            self.assertTrue(all(c.passed for c in checks), [c for c in checks if not c.passed])
            # kam_items 는 item_kind == "kam" 인 행만 센다(preamble 은 제외).
            self.assertIn(("KAM", "kam 항목 수", "1"), [(c.section, c.field, c.expected) for c in checks])

    def test_evidence_contains_accepts_alternatives(self) -> None:
        """근거 기대가 튜플이면 그중 하나만 들어도 통과한다 — 한 블록에 회사 주어 문장이 둘일 때
        어느 쪽이 뽑히는지는 위치가 정하므로, 축("회사 주어 문장인가")만 시험한다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(tmp, audit=[], kam=[], policy=[dict(_POLICY_ROW, evidence="회사는 아직 채택하지 않았다.")])
            def _checks(wanted):
                return reg.run_checks(
                    d,
                    audit_expectations=(),
                    policy_expectations=(
                        reg.PolicyExpectation("1", "가", "STD_EARLY_ADOPTION", "1118",
                                              evidence_contains=wanted),
                    ),
                )
            hit = _checks(("검토 중", "채택하지 않"))[0]
            self.assertTrue(hit.passed)
            self.assertEqual(hit.expected, "검토 중 또는 채택하지 않")
            self.assertFalse(_checks(("검토 중", "평가 중"))[0].passed)
            # 문자열 하나를 주는 기존 계약은 그대로다.
            self.assertFalse(_checks("검토 중")[0].passed)

    def test_value_mismatch_fails_and_records_actual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(tmp, audit=[_AUDIT_ROW], kam=[], policy=[_POLICY_ROW])
            checks = reg.run_checks(
                d,
                audit_expectations=(reg.AuditExpectation("1", "가", (("audit_opinion", "한정"),)),),
                policy_expectations=(),
            )
            bad = [c for c in checks if not c.passed]
            self.assertEqual(len(bad), 1)
            self.assertEqual(bad[0].expected, "한정")
            self.assertEqual(bad[0].actual, "적정")

    def test_missing_row_is_a_failure_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(tmp, audit=[_AUDIT_ROW], kam=[], policy=[_POLICY_ROW])
            checks = reg.run_checks(
                d,
                audit_expectations=(reg.AuditExpectation("9", "없음", (("audit_opinion", "적정"),)),),
                policy_expectations=(
                    reg.PolicyExpectation("9", "없음", "IP_MEASUREMENT", None, value="COST_MODEL"),
                ),
            )
            self.assertTrue(all(not c.passed for c in checks))
            self.assertTrue(all(reg.MISSING in c.actual for c in checks), [c.actual for c in checks])

    def test_absent_and_present_value_expectations(self) -> None:
        """디와이디형 기대: 같은 (rcept, topic) 안에 특정 값 행이 **없어야** 한다."""
        rows = [
            dict(_POLICY_ROW, topic="PPE_MEASUREMENT", subject=None, value="COST_MODEL"),
            dict(_POLICY_ROW, topic="PPE_MEASUREMENT", subject="토지", value="REVALUATION_MODEL"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(tmp, audit=[], kam=[], policy=rows)
            checks = reg.run_checks(
                d,
                audit_expectations=(),
                policy_expectations=(
                    reg.PolicyExpectation("1", "가", "PPE_MEASUREMENT", present_values=("COST_MODEL",),
                                          absent_values=("REVALUATION_MODEL",)),
                ),
            )
            by_field = {c.field: c for c in checks}
            self.assertTrue(by_field["값 COST_MODEL 존재"].passed)
            self.assertFalse(by_field["값 REVALUATION_MODEL 없음"].passed)

    def test_version_checks_cover_three_artifacts_with_their_own_columns(self) -> None:
        """판 표시 열은 파일마다 다르다 — 감사·정책은 parser_version, KAM 은 splitter_version.
        섞인 판이 하나라도 있으면 FAIL 이어야 한다."""
        self.assertEqual(
            [(v.filename, v.column, v.value) for v in reg.VERSION_EXPECTATIONS],
            [("audit_facts.jsonl", "parser_version", "af-v2"),
             ("kam_items.jsonl", "splitter_version", "kam-v2"),
             ("policy_stance.jsonl", "parser_version", "ps-v2")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(
                tmp,
                audit=[_AUDIT_ROW],
                kam=[{"rcept_no": "1", "item_kind": "kam", "splitter_version": "kam-v2"},
                     {"rcept_no": "1", "item_kind": "kam", "splitter_version": "kam-v1"}],
                policy=[_POLICY_ROW],
            )
            checks = reg.run_checks(d, audit_expectations=(), policy_expectations=(),
                                    version_expectations=reg.VERSION_EXPECTATIONS)
            by_file = {c.target: c for c in checks}
            self.assertTrue(by_file["audit_facts.jsonl"].passed)
            self.assertTrue(by_file["policy_stance.jsonl"].passed)
            # kam-v1 이 한 줄 섞여 있으므로 FAIL 이고, 실제 칸에 두 판이 다 보여야 한다.
            self.assertFalse(by_file["kam_items.jsonl"].passed)
            self.assertIn("kam-v1", by_file["kam_items.jsonl"].actual)

    def test_main_exit_code_and_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = _out_dir(tmp, audit=[_AUDIT_ROW], kam=[], policy=[])
            report = d / "regression-report.md"
            code = reg.main([str(d), "--report", str(report),
                             "--only", "20260000000000"])  # 기대 대상이 하나도 없으면 표는 비고 종료코드는 0
            self.assertEqual(code, 0)
            self.assertTrue(report.exists())
            self.assertIn("# Phase 1 회귀", report.read_text(encoding="utf-8"))


class DiffReport(unittest.TestCase):
    def _dirs(self, tmp: str, old: dict[str, list[dict]], new: dict[str, list[dict]]) -> tuple[Path, Path]:
        o, n = Path(tmp) / "old", Path(tmp) / "new"
        o.mkdir(), n.mkdir()
        for d, data in ((o, old), (n, new)):
            for name, rows in data.items():
                _write_jsonl(d / name, rows)
        return o, n

    def test_audit_crosstab_and_going_concern_flips(self) -> None:
        old = [
            {"rcept_no": "1", "audit_opinion": "적정", "going_concern": True, "emphasis_raw": ""},
            {"rcept_no": "2", "audit_opinion": "unknown", "going_concern": False, "emphasis_raw": "강조"},
            {"rcept_no": "3", "audit_opinion": "적정", "going_concern": False, "emphasis_raw": ""},
        ]
        new = [
            dict(_AUDIT_ROW, rcept_no="1", audit_opinion="적정", gc_material_uncertainty="not_disclosed"),
            dict(_AUDIT_ROW, rcept_no="2", audit_opinion="적정", gc_material_uncertainty="disclosed",
                 gc_location="separate_section"),
            dict(_AUDIT_ROW, rcept_no="3", audit_opinion="적정", gc_material_uncertainty="not_disclosed"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            o, n = self._dirs(tmp, {"audit_facts.jsonl": old}, {"audit_facts.jsonl": new})
            d = phase1_diff.diff_audit(o / "audit_facts.jsonl", n / "audit_facts.jsonl")
            self.assertEqual(d["counts"], {"old": 3, "new": 3})
            self.assertEqual(d["opinion_crosstab"][("unknown", "적정")], 1)
            self.assertEqual(d["gc_crosstab"][(True, "not_disclosed")], 1)
            self.assertEqual(d["gc_crosstab"][(False, "disclosed")], 1)
            self.assertEqual(d["gc_true_to_not_disclosed"], ["1"])
            self.assertEqual(d["gc_false_to_disclosed"], ["2"])
            self.assertEqual(d["gc_location"], {"": 2, "separate_section": 1})

    def test_kam_item_count_histogram_and_examples(self) -> None:
        old = [{"rcept_no": "1", "item_kind": "kam"}, {"rcept_no": "1", "item_kind": "kam"},
               {"rcept_no": "2", "item_kind": "kam"}]
        new = [{"rcept_no": "1", "item_kind": "kam"},
               {"rcept_no": "2", "item_kind": "kam"}, {"rcept_no": "2", "item_kind": "kam"},
               {"rcept_no": "2", "item_kind": "preamble"}]
        with tempfile.TemporaryDirectory() as tmp:
            o, n = self._dirs(tmp, {"kam_items.jsonl": old}, {"kam_items.jsonl": new})
            d = phase1_diff.diff_kam(o / "kam_items.jsonl", n / "kam_items.jsonl")
            self.assertEqual(d["decreased"], [("1", 2, 1)])
            self.assertEqual(d["increased"], [("2", 1, 2)])
            self.assertEqual(d["kind_new"]["preamble"], 1)

    def test_policy_transitions_and_asset_class_rows(self) -> None:
        old = [
            dict(_POLICY_ROW, value="UNDETERMINED"),
            dict(_POLICY_ROW, subject="1117", value="NOT_ADOPTED"),
            {"rcept_no": "1", "topic": "PPE_MEASUREMENT", "subject": None, "value": "REVALUATION_MODEL"},
        ]
        new = [
            dict(_POLICY_ROW, value="NOT_ADOPTED"),
            dict(_POLICY_ROW, subject="1117", value="ADOPTED"),
            {"rcept_no": "1", "topic": "PPE_MEASUREMENT", "subject": None, "value": "MIXED",
             "held_reason": "", "sentence_kind": ""},
            {"rcept_no": "1", "topic": "PPE_MEASUREMENT", "subject": "토지", "value": "REVALUATION_MODEL",
             "held_reason": "", "sentence_kind": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            o, n = self._dirs(tmp, {"policy_stance.jsonl": old}, {"policy_stance.jsonl": new})
            d = phase1_diff.diff_policy(o / "policy_stance.jsonl", n / "policy_stance.jsonl")
            self.assertEqual(d["transitions"]["STD_EARLY_ADOPTION"][("UNDETERMINED", "NOT_ADOPTED")], 1)
            self.assertEqual(d["transitions"]["STD_EARLY_ADOPTION"][("NOT_ADOPTED", "ADOPTED")], 1)
            self.assertEqual(d["new_only"]["PPE_MEASUREMENT"][("—", "REVALUATION_MODEL")], 1)
            self.assertEqual(d["ppe_asset_class_rows"], {"old": 0, "new": 1})
            self.assertEqual(d["sentence_kind"]["company_statement"], 2)

    def test_main_writes_report_with_all_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            o, n = self._dirs(
                tmp,
                {"audit_facts.jsonl": [{"rcept_no": "1", "audit_opinion": "적정", "going_concern": False,
                                        "emphasis_raw": ""}],
                 "kam_items.jsonl": [{"rcept_no": "1", "item_kind": "kam"}],
                 "policy_stance.jsonl": [_POLICY_ROW]},
                {"audit_facts.jsonl": [dict(_AUDIT_ROW)],
                 "kam_items.jsonl": [{"rcept_no": "1", "item_kind": "kam"}],
                 "policy_stance.jsonl": [_POLICY_ROW]},
            )
            report = Path(tmp) / "diff-report.md"
            self.assertEqual(phase1_diff.main([str(o), str(n), "--output", str(report)]), 0)
            text = report.read_text(encoding="utf-8")
            for heading in ("## 1. 감사 메타", "## 2. KAM", "## 3. 회계정책"):
                self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
