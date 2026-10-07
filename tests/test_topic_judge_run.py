import json
import tempfile
import threading
import unittest
from pathlib import Path

from dart_search_mcp.topic_judge.postcheck import Verdict
from dart_search_mcp.topic_judge.run import (
    ROW_COLUMNS, build_row, combine, dry_judge, is_rate_limited, progress_path_for, read_progress, repostcheck_progress,
    retry_after_seconds, run_topics, summary_path_for, with_retries,
)

LEASE_SELF = "회사는 단기리스와 소액자산 리스의 경우 예외규정을 선택하여 리스료를 리스기간에 걸쳐 정액기준으로 비용 인식합니다."
NOTYET = ("2.2 제정·개정 기준서 공표되었으나 아직 시행되지 않은 제·개정 기준서 기업회계기준서 제1116호 '리스' 개정 - "
          "리스이용자는 단기리스와 소액 기초자산 리스에 인식 면제를 선택할 수 있습니다. ")
FILL = "회사는 해당 거래를 검토하였습니다. " * 20

DOCS = {
    # 키워드 있음(리스) → 창 있음
    "20260401000001": "<P>" + FILL + "</P><P>3. 리스 " + LEASE_SELF + "</P><P>" + FILL + "</P>",
    # 키워드 있음(개발비) → 창 있음
    "20260401000002": "<P>" + FILL + "</P><P>무형자산 개발비 1,523,640 (312,000) 1,211,640</P><P>" + FILL + "</P>",
    # 미시행 절 안에만 키워드 → 창 없음
    "20260401000003": "<P>" + FILL + NOTYET + "</P><P>3. 중요한 회계정책 " + FILL + "</P>",
}


def make_env(d: Path) -> tuple[Path, Path]:
    docs = d / "docs"
    for i, (rc, body) in enumerate(DOCS.items()):
        (docs / rc).mkdir(parents=True)
        (docs / rc / f"{rc}.xml").write_text("<DOCUMENT>" + body + "</DOCUMENT>", encoding="utf-8")
    facts = d / "audit_facts.jsonl"
    with open(facts, "w", encoding="utf-8") as f:
        for i, rc in enumerate(DOCS):
            f.write(json.dumps({"rcept_no": rc, "corp_code": f"0000000{i}", "corp_name": f"회사{i}",
                                "fiscal_year": 2025}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"rcept_no": "20260401000009", "corp_code": "", "corp_name": "코드없음"}) + "\n")
        f.write("{깨진 줄\n")
    return facts, docs


class FakeJudge:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, prompt: str) -> dict:
        with self.lock:
            self.calls += 1
        if "LEASE_EXEMPTION" in prompt:
            return {"value": "BOTH", "evidence": LEASE_SELF, "subject": "self"}
        return {"value": "UNDETERMINED", "evidence": "", "subject": "none"}


TOPICS = ["LEASE_EXEMPTION", "DEV_COST_CAPITALIZATION"]


class RunTopicsTests(unittest.TestCase):
    def test_행_수와_16칸과_값(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "out" / "topic_stance.jsonl"
            judge = FakeJudge()
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out,
                           complete_json=judge, workers=2)
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(judge.calls, 2)
            self.assertEqual([(r["rcept_no"], r["topic"]) for r in rows],
                             [("20260401000001", "LEASE_EXEMPTION"), ("20260401000002", "DEV_COST_CAPITALIZATION")])
            for r in rows:
                self.assertEqual(tuple(r), ROW_COLUMNS)
                self.assertEqual((r["method"], r["parser_version"], r["subject"]), ("llm_luna", "ws8-v1", "회사"))
                self.assertEqual(r["fiscal_year"], 2025)
            lease, dev = rows
            self.assertEqual((lease["value"], lease["evidence"], lease["confidence"]), ("BOTH", LEASE_SELF, 0.8))
            self.assertEqual((lease["sentence_kind"], lease["sentence_subject"], lease["held_reason"]),
                             ("company_statement", "회사", ""))
            self.assertEqual(lease["evidence_heading_path"], "단기·소액")
            self.assertFalse(lease["is_conditional"])
            self.assertEqual((dev["value"], dev["confidence"], dev["held_reason"]), ("UNDETERMINED", 0.0, "model_undetermined"))
            self.assertTrue(dev["evidence"].startswith("판정 불가: "))
            self.assertLessEqual(len(dev["evidence"]), len("판정 불가: ") + 120)
            self.assertEqual((dev["sentence_kind"], dev["sentence_subject"]), ("", "불명"))
            self.assertEqual(s["rows"], 2)
            self.assertEqual(s["calls_this_run"], 2)
            self.assertEqual(s["by_topic"]["LEASE_EXEMPTION"], {"BOTH": 1})
            self.assertEqual(s["held_reason"]["DEV_COST_CAPITALIZATION"], {"model_undetermined": 1})
            self.assertEqual(s["no_window"]["LEASE_EXEMPTION"], 1)  # 미시행 절에만 있는 공시
            self.assertEqual(json.loads(summary_path_for(out).read_text(encoding="utf-8"))["rows"], 2)
            self.assertEqual(summary_path_for(out).name, "topic_stance.summary.json")
            prog = progress_path_for(out)
            self.assertEqual(prog.name, "topic_stance.progress.jsonl")
            recs = [json.loads(l) for l in prog.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(recs), 2)
            self.assertIn(LEASE_SELF, next(r for r in recs if r["topic"] == "LEASE_EXEMPTION")["excerpt"])

    def test_이어_하면_호출_0(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "topic_stance.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge())
            first = out.read_text(encoding="utf-8")
            # 잘린 마지막 줄이 있어도 읽힌다.
            with open(progress_path_for(out), "a", encoding="utf-8") as f:
                f.write('{"rcept_no": "2026')
            judge = FakeJudge()
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge)
            self.assertEqual(judge.calls, 0)
            self.assertEqual(s["calls_this_run"], 0)
            self.assertEqual(s["resumed_skipped"], 2)
            self.assertEqual(out.read_text(encoding="utf-8"), first)

    def test_잘린_줄_뒤에_이어_써도_새_기록이_온전하다(self) -> None:  # 리뷰 M1
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge())
            prog = progress_path_for(out)
            lines = prog.read_text(encoding="utf-8").splitlines()
            lease = next(l for l in lines if "LEASE_EXEMPTION" in l)
            # 리스 기록을 지우고(아직 안 한 후보로 만든다) 끝에 잘린 줄을 남긴다.
            prog.write_text("\n".join(l for l in lines if l != lease) + "\n" + lease[:40], encoding="utf-8")
            judge = FakeJudge()
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge)
            self.assertEqual(judge.calls, 1)
            self.assertEqual(s["rows"], 2)
            recs = read_progress(prog)
            self.assertEqual(sorted(r["topic"] for r in recs), sorted(TOPICS))
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(next(r for r in rows if r["topic"] == "LEASE_EXEMPTION")["value"], "BOTH")

    def test_retry_errors는_호출_실패만_다시_판정(self) -> None:  # 리뷰 L2
        def boom(prompt: str) -> dict:
            raise RuntimeError("gateway down")

        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=boom)
            judge = FakeJudge()
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge)
            self.assertEqual(judge.calls, 0)  # 기본은 실패 기록도 끝난 것으로 본다
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge,
                           retry_errors=True)
            self.assertEqual((judge.calls, s["calls_this_run"], s["rows"]), (2, 2, 2))
            rows = {r["topic"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
            self.assertEqual(rows["LEASE_EXEMPTION"]["value"], "BOTH")
            self.assertEqual(rows["DEV_COST_CAPITALIZATION"]["held_reason"], "model_undetermined")  # llm_error 기록은 버려진다
            self.assertEqual(s["errors_total"]["llm_error"], 0)
            judge2 = FakeJudge()
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge2,
                       retry_errors=True)
            self.assertEqual(judge2.calls, 0)  # 이미 판정된 것은 다시 부르지 않는다

    def test_model_used와_진행_로그(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            logs = []
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge(),
                           usage_getter=lambda: {"prompt_tokens": 10, "model_used": "gpt-5.6-luna"},
                           log=logs.append, log_every=1)
            self.assertEqual(s["models_this_run"], {"gpt-5.6-luna": 2})
            self.assertEqual(s["tokens_this_run"]["prompt_tokens"], 20)
            self.assertTrue(any(l.startswith("scan ") for l in logs))
            recs = read_progress(progress_path_for(out))
            self.assertTrue(all(r["usage"]["model_used"] == "gpt-5.6-luna" for r in recs))

    def test_usage_getter_예외는_기록을_막지_않는다(self) -> None:
        def bad_usage():
            raise RuntimeError("x")

        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge(),
                           usage_getter=bad_usage)
            self.assertEqual(s["rows"], 2)

    def test_limit와_rcept_filter(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "a.jsonl"
            judge = FakeJudge()
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=judge, limit=1)
            self.assertEqual((judge.calls, s["rows"]), (1, 1))
            out2 = Path(d) / "b.jsonl"
            judge2 = FakeJudge()
            s2 = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out2, complete_json=judge2,
                            rcept_filter={"20260401000002"})
            self.assertEqual((judge2.calls, s2["rows"], s2["facts"]), (1, 1, 1))

    def test_예외에도_행은_llm_error로_남는다(self) -> None:
        def boom(prompt: str) -> dict:
            raise RuntimeError("gateway down")

        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=boom)
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["value"] == "UNDETERMINED" and r["held_reason"] == "llm_error" for r in rows))
            self.assertEqual(s["errors_this_run"]["llm_error"], 2)

    def test_알_수_없는_주제(self) -> None:
        with self.assertRaises(ValueError):
            run_topics(facts_path=Path("x"), docs_dir=Path("y"), topics=["NOPE"], out_path=Path("z"),
                       complete_json=dry_judge)


class RepostcheckTests(unittest.TestCase):
    def test_raw에_사후검사를_다시_걸고_창이_바뀐_기록은_둔다(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge())
            prog = progress_path_for(out)
            recs = read_progress(prog)
            # 리스 기록: 그 당시 사후 검사가 (지금과 달리) UNDETERMINED로 보류했다고 꾸민다.
            # 개발비 기록: 창 위치를 바꿔 「창 바뀜」으로 만든다.
            for r in recs:
                if r["topic"] == "LEASE_EXEMPTION":
                    r.update(value="UNDETERMINED", evidence="", held_reason="conditional")
                else:
                    r["block_offset"] = r["block_offset"] + 7
            recs.append({"rcept_no": "20260401000002", "topic": "LEASE_EXEMPTION", "corp_code": "00000001",
                         "fiscal_year": 2025, "value": "UNDETERMINED", "evidence": "", "subject": "",
                         "held_reason": "llm_error", "raw": None, "anchor": "", "block_offset": 0, "window_head": "창"})
            prog.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            before = prog.read_text(encoding="utf-8")
            rows_path = Path(d) / "re" / "rows.jsonl"
            rows_path.parent.mkdir()
            s = repostcheck_progress(progress_path=prog, docs_dir=docs, out_path=rows_path)
            self.assertEqual(prog.read_text(encoding="utf-8"), before)  # progress는 읽기만
            self.assertEqual((s["records"], s["rechecked"], s["changed"], s["window_changed"], s["kept_no_raw"]),
                             (3, 1, 1, 1, 1))
            self.assertEqual(s["value_changes"], {"LEASE_EXEMPTION": {"UNDETERMINED->BOTH": 1}})
            self.assertEqual(s["held_reason_changes"], {"LEASE_EXEMPTION": {"conditional->None": 1}})
            rows = {(r["rcept_no"], r["topic"]): r for r in map(json.loads, rows_path.read_text(encoding="utf-8").splitlines())}
            self.assertEqual(len(rows), 3)
            lease = rows[("20260401000001", "LEASE_EXEMPTION")]
            self.assertEqual((lease["value"], lease["evidence"], lease["held_reason"]), ("BOTH", LEASE_SELF, ""))
            self.assertEqual(tuple(lease), ROW_COLUMNS)
            self.assertEqual(rows[("20260401000002", "DEV_COST_CAPITALIZATION")]["held_reason"], "model_undetermined")
            self.assertEqual(rows[("20260401000002", "LEASE_EXEMPTION")]["held_reason"], "llm_error")
            self.assertTrue((rows_path.parent / "rows.repostcheck.json").exists())

    def test_cli(self) -> None:
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge())
            rows_path = Path(d) / "rows.jsonl"
            root = Path(__file__).resolve().parent.parent
            p = subprocess.run([sys.executable, str(root / "cli.py"), "extract-topic-stances", "--repostcheck",
                                "--progress", str(progress_path_for(out)), "--docs", str(docs), "-o", str(rows_path)],
                               capture_output=True, text=True, cwd=str(root))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("재검사 2", p.stdout)
            self.assertEqual(rows_path.read_text(encoding="utf-8"), out.read_text(encoding="utf-8"))


class FallbackMethodTests(unittest.TestCase):  # 최종 리뷰 m4
    def test_폴백_모델_기록은_llm_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            models = {"LEASE_EXEMPTION": "gpt-5.4-mini", "DEV_COST_CAPITALIZATION": "gpt-5.6-luna"}
            current = threading.local()

            class Judge(FakeJudge):
                def __call__(self, prompt: str) -> dict:
                    current.topic = "LEASE_EXEMPTION" if "LEASE_EXEMPTION" in prompt else "DEV_COST_CAPITALIZATION"
                    return super().__call__(prompt)

            s = run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=Judge(),
                           usage_getter=lambda: {"model_used": models[current.topic]})
            rows = {r["topic"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
            self.assertEqual((rows["LEASE_EXEMPTION"]["method"], rows["LEASE_EXEMPTION"]["confidence"]), ("llm_fallback", 0.8))
            self.assertEqual((rows["DEV_COST_CAPITALIZATION"]["method"], rows["DEV_COST_CAPITALIZATION"]["confidence"]),
                             ("llm_luna", 0.0))
            self.assertEqual(s["by_method"], {"llm_fallback": 1, "llm_luna": 1})
            # 재검사에서도 같은 규칙
            rp = Path(d) / "re" / "rows.jsonl"
            r = repostcheck_progress(progress_path=progress_path_for(out), docs_dir=docs, out_path=rp)
            self.assertEqual(r["by_method"], {"llm_fallback": 1, "llm_luna": 1})

    def test_method_for(self) -> None:
        fact = {"rcept_no": "1", "corp_code": "2", "fiscal_year": 2025}
        v = Verdict("BOTH", "근거", "self", None, None)
        for model, method in ((None, "llm_luna"), ("gpt-5.6-luna", "llm_luna"), ("GPT-5.6-Luna-2", "llm_luna"),
                              ("gpt-5.4-mini", "llm_fallback"), ("gpt-4o", "llm_fallback")):
            r = build_row(fact, "LEASE_EXEMPTION", v, window_text="창", block_offset=0, anchor="a", model_used=model)
            self.assertEqual(r["method"], method, model)


class RecordsFileTests(unittest.TestCase):  # 최종 리뷰 m6
    def test_repostcheck가_기록_파일을_쓰고_표본이_그_발췌를_읽는다(self) -> None:
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            facts, docs = make_env(Path(d))
            out = Path(d) / "t.jsonl"
            run_topics(facts_path=facts, docs_dir=docs, topics=TOPICS, out_path=out, complete_json=FakeJudge())
            prog = progress_path_for(out)
            recs = read_progress(prog)
            for r in recs:  # 옛 판정: 리스 보류, 발췌도 낡은 것
                if r["topic"] == "LEASE_EXEMPTION":
                    r.update(value="UNDETERMINED", evidence="", held_reason="conditional", excerpt="낡은 발췌")
            prog.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            rows_path = Path(d) / "re" / "rows.jsonl"
            s = repostcheck_progress(progress_path=prog, docs_dir=docs, out_path=rows_path)
            rec_path = rows_path.parent / "rows.records.jsonl"
            self.assertEqual(s["records_path"], str(rec_path))
            updated = {r["topic"]: r for r in read_progress(rec_path)}
            self.assertEqual(len(updated), 2)
            self.assertEqual(updated["LEASE_EXEMPTION"]["value"], "BOTH")
            self.assertIn(LEASE_SELF, updated["LEASE_EXEMPTION"]["excerpt"])
            root = Path(__file__).resolve().parent.parent
            samples = Path(d) / "s.json"
            for extra, expect_new in ((["--records", str(rec_path)], True), ([], False)):
                p = subprocess.run([sys.executable, str(root / "cli.py"), "topic-stance-sample", "--stances", str(rows_path),
                                    "--facts", str(facts), "--progress", str(prog), "-o", str(samples), *extra],
                                   capture_output=True, text=True, cwd=str(root))
                self.assertEqual(p.returncode, 0, p.stderr)
                sm = {x["topic"]: x for x in json.loads(samples.read_text(encoding="utf-8"))}
                if expect_new:
                    self.assertIn(LEASE_SELF, sm["LEASE_EXEMPTION"]["excerpt"])
                else:
                    self.assertEqual(sm["LEASE_EXEMPTION"]["excerpt"], "낡은 발췌")


class CombineTests(unittest.TestCase):
    def v(self, value: str, held=None) -> Verdict:
        return Verdict(value, "근거" if value != "UNDETERMINED" else "", "self", held, None)

    def test_엇갈리면_conflict(self) -> None:
        c = combine([self.v("BOTH"), self.v("SHORT_TERM")])
        self.assertEqual((c.value, c.held_reason), ("UNDETERMINED", "conflict"))

    def test_같은_값과_판정불가는_판정값(self) -> None:
        self.assertEqual(combine([self.v("UNDETERMINED", "not_self"), self.v("BOTH"), self.v("BOTH")]).value, "BOTH")
        u = combine([self.v("UNDETERMINED", "parse"), self.v("UNDETERMINED", "not_self")])
        self.assertEqual((u.value, u.held_reason), ("UNDETERMINED", "parse"))

    def test_빈_목록(self) -> None:
        with self.assertRaises(ValueError):
            combine([])


class RowMetaTests(unittest.TestCase):
    fact = {"rcept_no": "1", "corp_code": "2", "fiscal_year": "2025"}

    def test_subject_매핑과_conditional(self) -> None:
        cases = {"self": ("company_statement", "회사"), "standard_text": ("standard_description", "기준서"),
                 "conditional": ("conditional", "회사"), "other": ("", "불명"), "none": ("", "불명"), "": ("", "불명")}
        for subj, meta in cases.items():
            r = build_row(self.fact, "LEASE_EXEMPTION", Verdict("UNDETERMINED", "", subj, "conditional", None),
                          window_text="창" * 200, block_offset=7, anchor="리스이용자")
            self.assertEqual((r["sentence_kind"], r["sentence_subject"]), meta)
            self.assertTrue(r["is_conditional"])
            self.assertEqual((r["block_offset"], r["evidence_heading_path"], r["fiscal_year"]), (7, "리스이용자", 2025))
            self.assertEqual(r["evidence"], "판정 불가: " + "창" * 120)


class RetryTests(unittest.TestCase):
    class E429(Exception):
        status_code = 429

    def test_429는_백오프로_다시_부른다(self) -> None:
        n, sleeps = [0], []

        def flaky(p: str) -> dict:
            n[0] += 1
            if n[0] < 3:
                raise self.E429()
            return {"value": "BOTH"}

        self.assertEqual(with_retries(flaky, sleep=sleeps.append, jitter=lambda: 0.0)("p"), {"value": "BOTH"})
        self.assertEqual(sleeps, [5.0, 10.0])

    def test_5번_넘으면_예외(self) -> None:
        n, sleeps = [0], []

        def always(p: str) -> dict:
            n[0] += 1
            raise self.E429()

        with self.assertRaises(self.E429):
            with_retries(always, sleep=sleeps.append, jitter=lambda: 0.5)("p")
        self.assertEqual((n[0], sleeps), (6, [5.5, 10.5, 20.5, 40.5, 80.5]))

    def test_Retry_After를_따른다(self) -> None:
        class Resp:
            headers = {"retry-after": "30"}

        class E(Exception):
            status_code = 429
            response = Resp()

        n, sleeps = [0], []

        def once(p: str) -> dict:
            n[0] += 1
            if n[0] == 1:
                raise E()
            return {}

        with_retries(once, sleep=sleeps.append, jitter=lambda: 0.0)("p")
        self.assertEqual(sleeps, [30.0])
        self.assertEqual(retry_after_seconds(E()), 30.0)
        self.assertIsNone(retry_after_seconds(RuntimeError()))

    def test_429가_아니면_바로_예외(self) -> None:
        n = [0]

        def bad(p: str) -> dict:
            n[0] += 1
            raise ValueError("x")

        with self.assertRaises(ValueError):
            with_retries(bad, sleep=lambda s: None)("p")
        self.assertEqual(n[0], 1)

    def test_is_rate_limited(self) -> None:
        class RateLimitError(Exception):
            pass

        self.assertTrue(is_rate_limited(RateLimitError()))
        self.assertTrue(is_rate_limited(self.E429()))
        self.assertFalse(is_rate_limited(RuntimeError()))


if __name__ == "__main__":
    unittest.main()
