import json
import tempfile
import unittest
from pathlib import Path
from dart_search_mcp.tools.policy_stance_cli import (
    PARSER_VERSION,
    PolicyStanceSourceError,
    run_extract_policy_stances,
)

FIX = Path(__file__).parent / "fixtures" / "policy"


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class RunExtractPolicyStancesTests(unittest.TestCase):
    def test_규칙만으로_기준서와_주제_행을_낸다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION", "PPE_MEASUREMENT", "REVENUE_METHOD"), use_llm=False)
            rows = _rows(out)
            by = {(r["rcept_no"], r["topic"], r["subject"]): r for r in rows}
            self.assertEqual(by[("20260001000001", "STD_EARLY_ADOPTION", "1109")]["value"], "EARLY_ADOPTED")
            self.assertEqual(by[("20260001000001", "STD_EARLY_ADOPTION", "1118")]["value"], "NOT_ADOPTED")
            self.assertEqual(by[("20260001000001", "PPE_MEASUREMENT", None)]["value"], "REVALUATION_MODEL")
            self.assertEqual(by[("20260001000002", "STD_EARLY_ADOPTION", "1118")]["value"], "UNDETERMINED")
            self.assertEqual(by[("20260001000002", "REVENUE_METHOD", None)]["value"], "OVER_TIME")
            self.assertTrue(all(r["parser_version"] == PARSER_VERSION for r in rows))
            self.assertTrue(all(r["evidence"] for r in rows))
            self.assertTrue(by[("20260001000002", "STD_EARLY_ADOPTION", "1118")]["evidence"].startswith("판정 불가: "))
            self.assertEqual(summary["reports"], 2)
            self.assertEqual(summary["by_topic"]["STD_EARLY_ADOPTION"]["EARLY_ADOPTED"], 1)
            self.assertTrue((Path(tmp) / "policy_stance.jsonl.summary.json").exists())

    def test_LLM은_UNDETERMINED_블록에만_호출된다(self) -> None:
        calls: list[str] = []
        def fake(messages, **kw):
            calls.append(messages[-1]["content"]); return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(len(calls), 1)
            self.assertIn("중간합계", calls[0])
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"], row["confidence"]), ("NOT_ADOPTED", "llm", 0.6))
            self.assertEqual(summary["llm_calls"], 1)

    def test_출력이_원자적이고_같은_키가_두_번_안_나온다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs",
                                       output_path=out, topics=("STD_EARLY_ADOPTION",), use_llm=False)
            keys = [(r["rcept_no"], r["topic"], r["subject"]) for r in _rows(out)]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertFalse((Path(tmp) / "policy_stance.jsonl.tmp").exists())

    def test_limit_0_은_아무것도_처리하지_않는다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=False, limit=0)
            self.assertEqual(summary["reports"], 0)
            self.assertEqual(_rows(out), [])

    def test_XML_없는_보고서는_skipped_no_xml_로_센다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_lines = FIX.joinpath("audit_facts.jsonl").read_text(encoding="utf-8").splitlines()
            facts_lines.append(json.dumps({"rcept_no": "20260001000099", "corp_code": "00000099", "fiscal_year": "2025"}))
            facts_path = Path(tmp) / "audit_facts.jsonl"
            facts_path.write_text("\n".join(facts_lines) + "\n", encoding="utf-8")
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=facts_path, docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=False)
            self.assertEqual(summary["facts"], 3)
            self.assertEqual(summary["reports"], 2)
            self.assertEqual(summary["skipped_no_xml"], 1)

    def test_출력_경로가_입력과_같으면_거부한다(self) -> None:
        with self.assertRaises(PolicyStanceSourceError):
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=FIX / "audit_facts.jsonl",
                topics=("STD_EARLY_ADOPTION",), use_llm=False)

    def test_llm_subjects_가_있으면_그_대상만_LLM_에_보낸다(self) -> None:
        calls: list[str] = []
        def fake(messages, **kw):
            calls.append(messages[-1]["content"]); return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            s = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                                           topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_subjects=frozenset({"9999"}))
            self.assertEqual(len(calls), 0)
            self.assertEqual(s["llm_calls"], 0)
            self.assertEqual(s["llm_subjects"], ["9999"])
            s2 = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                                            topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_subjects=frozenset({"1118"}))
            self.assertEqual(len(calls), 1)
            self.assertEqual(s2["llm_calls"], 1)

    def test_llm_workers_병렬도_같은_결과를_낸다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.jsonl"; b = Path(tmp) / "b.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=a,
                                       topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_workers=1)
            s = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=b,
                                           topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_workers=4)
            self.assertEqual(a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8"))
            self.assertEqual(s["llm_workers"], 4)


if __name__ == "__main__":
    unittest.main()
