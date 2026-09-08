import json
import tempfile
import unittest
from pathlib import Path
from dart_search_mcp.tools.policy_stance_gate import PolicyGateControlError, build_gate_report


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

    def test_주제_전체_판단불가가_높아도_대조군_대상만_보면_통과한다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            rows = (
                [
                    {"rcept_no": f"A{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "EARLY_ADOPTED", "method": "rule"}
                    for i in range(1, 11)
                ]
                + [
                    {"rcept_no": f"H{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"}
                    for i in range(1, 51)
                ]
                + [
                    {"rcept_no": f"X{i}", "topic": "STD_EARLY_ADOPTION", "subject": "9999", "value": "UNDETERMINED", "method": "none"}
                    for i in range(1, 106)
                ]
            )
            stances = _write(t / "s.jsonl", rows)
            pos = _write(t / "p.jsonl", [
                {"rcept_no": f"A{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "expected": "EARLY_ADOPTED"}
                for i in range(1, 11)
            ])
            neg = _write(t / "n.jsonl", [
                {"rcept_no": f"H{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "expected": "NOT_POSITIVE"}
                for i in range(1, 51)
            ])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=neg, report_path=t / "gate.md")
            topic = rep["topics"]["STD_EARLY_ADOPTION"]
            self.assertGreater(topic["undetermined_ratio"], 0.5)  # 주제 전체로는 60% 근처
            self.assertEqual(topic["undetermined_scope"], "controls-subjects:1118")
            self.assertTrue(topic["controls_sufficient"])
            self.assertTrue(topic["passed"])  # 대조군 대상(1118)만 보면 0%
            self.assertTrue(rep["passed"])
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("대상별 분포", md)

    def test_대조군_행이_stances에_없으면_미매칭으로_불충분_처리한다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "Z1", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"}
            ])
            # 하드 네거티브 50건 모두 stances 에 없는 rcept_no — 전부 미매칭
            neg = _write(t / "n.jsonl", [
                {"rcept_no": f"NOMATCH{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "expected": "NOT_POSITIVE"}
                for i in range(1, 51)
            ])
            pos = _write(t / "p.jsonl", [
                {"rcept_no": f"P{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "expected": "EARLY_ADOPTED"}
                for i in range(1, 11)
            ])
            rep = build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=neg, report_path=t / "gate.md")
            topic = rep["topics"]["STD_EARLY_ADOPTION"]
            self.assertEqual(topic["hardnegs_matched"], 0)
            self.assertEqual(topic["positives_matched"], 0)
            self.assertEqual(topic["unmatched_controls"], 60)
            self.assertFalse(topic["controls_sufficient"])
            self.assertFalse(topic["passed"])
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("대조군 매칭", md)
            self.assertIn("미매칭은 통제 불충분으로 처리", md)

    def test_알수없는_주제_코드는_경고와_함께_미달로_처리한다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"},
            ])
            neg = _write(t / "n.jsonl", [
                {"rcept_no": "A", "topic": "BOGUS_TOPIC", "subject": None, "expected": "NOT_POSITIVE"},
            ])
            rep = build_gate_report(stances_path=stances, positives_path=None, hardneg_path=neg, report_path=t / "gate.md")
            self.assertIn("BOGUS_TOPIC", rep["topics"])
            self.assertFalse(rep["topics"]["BOGUS_TOPIC"]["passed"])
            self.assertFalse(rep["passed"])
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertLess(md.index("BOGUS_TOPIC"), md.index("## "))  # 첫 주제 항목보다 위쪽(상단 경고)

    def test_expected_가_없는_대조군_행은_명확한_에러(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"},
            ])
            pos = _write(t / "p.jsonl", [
                {"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109"},  # expected 없음
            ])
            with self.assertRaises(PolicyGateControlError) as ctx:
                build_gate_report(stances_path=stances, positives_path=pos, hardneg_path=None, report_path=t / "gate.md")
            self.assertIn("A", str(ctx.exception))
            self.assertIn("STD_EARLY_ADOPTION", str(ctx.exception))

    def test_summary_json의_capped_ratio가_리포트에_들어간다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": None, "value": "COST_MODEL", "method": "rule"},
            ])
            (t / "s.jsonl.summary.json").write_text(
                json.dumps({"capped_ratio": {"PPE_MEASUREMENT": 0.12}}), encoding="utf-8"
            )
            rep = build_gate_report(stances_path=stances, positives_path=None, hardneg_path=None, report_path=t / "gate.md")
            self.assertEqual(rep["topics"]["PPE_MEASUREMENT"]["capped_ratio"], 0.12)
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("상한 도달률", md)
            self.assertIn("12.0%", md)

    def test_판단불가_비율은_회사_수준_행으로만_잰다(self) -> None:
        """Fix round 1(T5 리뷰 I3): 유형자산 자산군별 행(subject 있음)은 정의상 UNDETERMINED 가
        아니라, 전체 행으로 비율을 재면 단위가 섞여 판단불가가 실제보다 낮게 나온다. 회사 수준
        행(subject None) 1건이 판단불가고 자산군 행 2건이 붙으면 비율은 1.0 이지 0.33 이 아니다."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": None, "value": "UNDETERMINED", "method": "none"},
                {"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": "토지", "value": "REVALUATION_MODEL", "method": "rule"},
                {"rcept_no": "A", "topic": "PPE_MEASUREMENT", "subject": "토지를 제외한 유형자산", "value": "COST_MODEL", "method": "rule"},
            ])
            rep = build_gate_report(stances_path=stances, positives_path=None, hardneg_path=None, report_path=t / "gate.md")
            topic = rep["topics"]["PPE_MEASUREMENT"]
            self.assertEqual(topic["undetermined_ratio"], 1.0)
            self.assertEqual((topic["company_rows"], topic["rows"]), (1, 3))
            self.assertEqual(topic["undetermined_scope_unit"], "company-rows")
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("회사 수준(subject 없음) 1 / 전체 3", md)

    def test_대상별_행만_있는_주제는_주제_전체로_잰다(self) -> None:
        """STD_EARLY_ADOPTION 은 subject 가 기준서 번호라 회사 수준 행이 없다 — 기존 동작 유지."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = _write(t / "s.jsonl", [
                {"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"},
                {"rcept_no": "B", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "UNDETERMINED", "method": "none"},
            ])
            rep = build_gate_report(stances_path=stances, positives_path=None, hardneg_path=None, report_path=t / "gate.md")
            topic = rep["topics"]["STD_EARLY_ADOPTION"]
            self.assertEqual((topic["undetermined_ratio"], topic["company_rows"]), (0.5, 0))
            self.assertEqual(topic["undetermined_scope_unit"], "all-rows")

    def test_회계사_표본_문구와_종합_통과_문구(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            rows = (
                [
                    {"rcept_no": f"A{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "EARLY_ADOPTED", "method": "rule"}
                    for i in range(1, 11)
                ]
                + [
                    {"rcept_no": f"H{i}", "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED", "method": "rule"}
                    for i in range(1, 51)
                ]
            )
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
            self.assertTrue(rep["passed"])
            md = (t / "gate.md").read_text(encoding="utf-8")
            self.assertIn("회계사 표본 20건 일치: 미확인 (게이트 통과에 포함되지 않음)", md)
            self.assertIn("통과(회계사 표본 미확인)", md)


if __name__ == "__main__":
    unittest.main()
