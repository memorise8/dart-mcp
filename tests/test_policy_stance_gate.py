import json
import tempfile
import unittest
from pathlib import Path
from dart_search_mcp.tools.policy_stance_gate import build_gate_report


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"); return path


class GateReportTests(unittest.TestCase):
    def test_분포_재현_오탐_판단불가를_계산하고_통과를_판정한다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"},
                {"rcept_no": "B", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"},
                {"rcept_no": "C", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "UNDETERMINED", "method": "none"},
                {"rcept_no": "D", "topic": "STD_EARLY_ADOPTION", "subject": "1008", "value": "PERMITTED_ONLY", "method": "rule"},
            ])
            pos = _write(t / "p.jsonl", [{"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "expected": "EARLY_ADOPTED"}])
            neg = _write(t / "n.jsonl", [{"rcept_no": "D", "topic": "STD_EARLY_ADOPTION", "subject": "1008", "expected": "NOT_POSITIVE"}])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=neg, report_path=t / "gate.md")
            topic = rep["topics"]["STD_EARLY_ADOPTION"]
            self.assertEqual(topic["distribution"]["NOT_ADOPTED"], 1)
            self.assertEqual(topic["positive_recall"], 1.0)
            self.assertEqual(topic["hardneg_false_positive"], 0)
            self.assertEqual(topic["undetermined_ratio"], 0.25)
            self.assertFalse(topic["controls_sufficient"])  # 대조군 표본(양성1<10, 하드네거1<50) 미달
            self.assertFalse(topic["passed"])          # 판단불가 25% > 10%
            self.assertFalse(rep["passed"])
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("STD_EARLY_ADOPTION", md); self.assertIn("판단불가", md)

    def test_양성이_빠지면_재현율이_떨어진다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [{"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": None, "value": "COST_MODEL", "method": "rule"}])
            pos = _write(t / "p.jsonl", [
                {"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": None, "expected": "REVALUATION_MODEL"},
                {"rcept_no": "Z", "topic": "PPE_MEASUREMENT", "subject": None, "expected": "REVALUATION_MODEL"}])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=None, report_path=t / "g.md")
            self.assertEqual(rep["topics"]["PPE_MEASUREMENT"]["positive_recall"], 0.0)

    def test_stances_에_없는_주제도_대조군이_있으면_리포트에_나온다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "X", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"},
            ])
            pos = _write(t / "p.jsonl", [
                {"rcept_no": f"P{i}", "topic": "PPE_MEASUREMENT", "subject": None, "expected": "REVALUATION_MODEL"}
                for i in range(1, 11)
            ])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=None, report_path=t / "gate.md")
            self.assertIn("PPE_MEASUREMENT", rep["topics"])
            topic = rep["topics"]["PPE_MEASUREMENT"]
            self.assertEqual(topic["positive_recall"], 0.0)
            self.assertEqual(topic["rows"], 0)
            self.assertFalse(topic["passed"])
            self.assertFalse(rep["passed"])

    def test_대조군이_충분하고_기준을_넘으면_통과한다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            rows = (
                [
                    {"rcept_no": f"A{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"}
                    for i in range(1, 11)
                ]
                + [
                    {"rcept_no": f"B{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"}
                    for i in range(1, 3)
                ]
                + [
                    {"rcept_no": f"H{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"}
                    for i in range(1, 51)
                ]
            )
            self.assertEqual(len(rows), 62)
            stances = _write(t / "s.jsonl", rows)
            pos = _write(t / "p.jsonl", [
                {"rcept_no": f"A{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "expected": "EARLY_ADOPTED"}
                for i in range(1, 11)
            ])
            neg = _write(t / "n.jsonl", [
                {"rcept_no": f"H{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "expected": "NOT_POSITIVE"}
                for i in range(1, 51)
            ])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=neg, report_path=t / "gate.md")
            topic = rep["topics"]["STD_EARLY_ADOPTION"]
            self.assertTrue(topic["controls_sufficient"])
            self.assertEqual(topic["positive_recall"], 1.0)
            self.assertEqual(topic["hardneg_false_positive"], 0)
            self.assertTrue(topic["passed"])
            self.assertTrue(rep["passed"])


if __name__ == "__main__":
    unittest.main()
