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

    def test_LLM_호출이_예외를_던져도_실행이_끝나고_UNDETERMINED로_남는다(self) -> None:
        def boom(messages, **kw):
            raise RuntimeError("gateway timeout")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertEqual(summary["llm_failures"], 1)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_LLM_단계_진입_전에_규칙_결과만으로도_출력이_존재한다(self) -> None:
        def boom(messages, **kw):
            raise RuntimeError("gateway timeout")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertTrue(out.exists())
            rows = _rows(out)
            self.assertTrue(any(r["rcept_no"] == "20260001000001" for r in rows))
            self.assertFalse(out.with_name(out.name + ".tmp").exists())

    def test_출력이_원자적이고_같은_키가_두_번_안_나온다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs",
                                       output_path=out, topics=("STD_EARLY_ADOPTION",), use_llm=False)
            keys = [(r["rcept_no"], r["topic"], r["subject"]) for r in _rows(out)]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertFalse((Path(tmp) / "policy_stance.jsonl.tmp").exists())

    def test_summary_json도_원자적으로_쓴다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs",
                                       output_path=out, topics=("STD_EARLY_ADOPTION",), use_llm=False)
            summary_path = Path(tmp) / "policy_stance.jsonl.summary.json"
            self.assertTrue(summary_path.exists())
            self.assertFalse(summary_path.with_name(summary_path.name + ".tmp").exists())

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
            self.assertIsNotNone(s["llm_subjects_note"])
            s2 = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                                            topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_subjects=frozenset({"1118"}))
            self.assertEqual(len(calls), 1)
            self.assertEqual(s2["llm_calls"], 1)

    def test_llm_model_base_url이_judge_with_llm까지_전달된다(self) -> None:
        seen: dict = {}
        def fake(messages, **kw):
            seen["model"] = kw.get("model")
            seen["base_url"] = kw.get("base_url")
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake,
                llm_model="custom-model", llm_base_url="http://gw.local/v1")
            self.assertEqual(seen["model"], "custom-model")
            self.assertEqual(seen["base_url"], "http://gw.local/v1")
            self.assertEqual(summary["llm_model"], "custom-model")
            self.assertEqual(summary["llm_base_url"], "http://gw.local/v1")

    def test_summary에_llm_model_base_url_unanchored_키가_있다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertIn("llm_model", summary)
            self.assertIn("llm_base_url", summary)
            self.assertIn("llm_unanchored", summary)
            self.assertEqual(summary["llm_unanchored"], 0)

    def test_LLM_근거가_블록에_없으면_앵커_실패로_센다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "블록에는 없는 완전히 다른 구절입니다"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_unanchored"], 1)
            self.assertEqual(summary["llm_failures"], 0)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_게이트웨이_오류는_llm_gateway로_센다(self) -> None:
        """S-I1/Q-I1: KamLlmError(게이트웨이 다운)는 llm_unanchored 가 아니라 llm_gateway 로 센다 —
        예전엔 앵커 실패와 구분이 안 돼 "게이트웨이 장애"를 "앵커링 과실"로 오인할 수 있었다."""
        from dart_search_mcp.tools.kam_tagger import KamLlmError

        def boom(messages, **kw):
            raise KamLlmError("gateway down")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertEqual(summary["llm_gateway"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)
            self.assertEqual(summary["llm_failures"], 0)  # KamLlmError는 judge_with_llm 내부에서 삼켜진다 — 예외가 아니다

    def test_파싱_실패는_llm_parse로_센다(self) -> None:
        def fake(messages, **kw):
            return "이것은 JSON이 아닙니다"

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_parse"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)

    def test_R18_가드_거부는_llm_unverified로_센다(self) -> None:
        """긍정 라벨인데 인용구가 단정 패턴과 안 맞아 거부된 경우(R18 가드) — 앵커 실패와는
        다른 사유이므로 llm_unverified 로 별도로 센다."""
        def fake(messages, **kw):
            return json.dumps({"label": "EARLY_ADOPTED", "evidence": "새로운 중간합계를 도입합니다"})

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_unverified"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

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
