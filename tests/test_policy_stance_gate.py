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


if __name__ == "__main__":
    unittest.main()
