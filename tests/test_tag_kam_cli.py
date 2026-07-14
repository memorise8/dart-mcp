"""`dart_search_mcp.tools.tag_kam_cli`(Task 3: `dart tag-kam` 배치 CLI)에 대한
테스트.

**실제 네트워크 호출은 절대 하지 않는다** - `call_fn`은 fake 함수로 주입하고,
`client=None` 기본 경로 검증만 `httpx.Client`를 monkeypatch spy로 대체해
소켓 없이 확인한다."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import httpx

# 레포 관례: `server` -> `cli` -> `dart_search_mcp.tools.*` 순서로 import해
# MCP 도구 등록 순서(`tests/test_public_surface.py`)를 보존한다
# (`tests/test_extract_facts.py`와 동일한 이유).
import server  # noqa: F401
import cli
from click.testing import CliRunner

from dart_search_mcp.kam_taxonomy import KAM_TAXONOMY
from dart_search_mcp.tools.kam_tagger import KamLlmError, tag_one_kam
from dart_search_mcp.tools.tag_kam_cli import (
    TagKamSourceError,
    _kam_cache_key,
    load_kam_targets,
    tag_kam_batch,
)

_FIXED_TAGGED_AT = "2026-07-01T00:00:00Z"


def _write_facts(tmp_dir: str, rows: list[dict[str, Any]], name: str = "audit_facts.jsonl") -> str:
    path = os.path.join(tmp_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
    return path


def _fact_row(rcept_no: str, *, kam_present: bool = True, kam_raw: str = "원문") -> dict[str, Any]:
    return {
        "rcept_no": rcept_no,
        "corp_code": "00000001",
        "corp_name": "테스트법인",
        "corp_cls": "Y",
        "kam_present": kam_present,
        "kam_raw": kam_raw,
    }


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


class KamPresentFilterTests(unittest.TestCase):
    def test_kam_present_false_rows_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1", kam_present=True, kam_raw="원문 A"),
                    _fact_row("2", kam_present=False, kam_raw="원문 B"),
                    _fact_row("3", kam_present=True, kam_raw=""),  # kam_raw 빈 문자열
                    _fact_row("4", kam_present=True, kam_raw="원문 D"),
                ],
            )

            targets = load_kam_targets(facts_path)

            self.assertEqual([t["rcept_no"] for t in targets], ["1", "4"])

    def test_limit_applied_after_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1"),
                    _fact_row("2", kam_present=False),
                    _fact_row("3"),
                    _fact_row("4"),
                ],
            )

            targets = load_kam_targets(facts_path, limit=1)

            self.assertEqual([t["rcept_no"] for t in targets], ["1"])

    def test_missing_facts_file_raises_source_error(self) -> None:
        with self.assertRaises(TagKamSourceError):
            load_kam_targets("/no/such/file.jsonl")


class DryRunTests(unittest.TestCase):
    def test_dry_run_reports_targets_and_taxonomy_without_calling_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1"), _fact_row("2"), _fact_row("3", kam_present=False)])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            def fail_call_fn(*args, **kwargs):
                raise AssertionError("dry-run은 call_fn을 호출하면 안 된다")

            result = tag_kam_batch(facts_path, output_path, dry_run=True, call_fn=fail_call_fn)

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["targets"], 2)
            self.assertEqual(result["taxonomy"], [tag for tag, _ in KAM_TAXONOMY])
            self.assertFalse(os.path.exists(output_path))
            self.assertFalse(os.path.exists(output_path + ".cache.json"))
            self.assertFalse(os.path.exists(output_path + ".checkpoint.json"))
            self.assertFalse(os.path.exists(output_path + ".summary.json"))


class NormalBatchTests(unittest.TestCase):
    def test_batch_tags_all_targets_and_writes_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B"), _fact_row("3", kam_raw="원문 C")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            def fake_call_fn(messages, *, model, base_url):
                return '["수익인식"]'

            summary = tag_kam_batch(
                facts_path,
                output_path,
                tagged_at=_FIXED_TAGGED_AT,
                model="gpt-5.4-mini",
                base_url="http://x/v1",
                call_fn=fake_call_fn,
            )

            self.assertEqual(summary["targets"], 3)
            self.assertEqual(summary["tagged_ok"], 3)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["cache_hits"], 0)
            self.assertEqual(summary["dropped_total"], 0)
            self.assertEqual(summary["tag_distribution"], {"수익인식": 3})

            kam_raw_by_rcept = {"1": "원문 A", "2": "원문 B", "3": "원문 C"}
            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 3)
            self.assertEqual({r["rcept_no"] for r in rows}, {"1", "2", "3"})
            for row in rows:
                self.assertEqual(row["tags"], ["수익인식"])
                self.assertEqual(row["dropped"], [])
                self.assertEqual(row["model"], "gpt-5.4-mini")
                self.assertEqual(row["base_url"], "http://x/v1")
                self.assertEqual(row["tagged_at"], _FIXED_TAGGED_AT)
                expected_hash = hashlib.sha256(
                    f"gpt-5.4-mini\n{kam_raw_by_rcept[row['rcept_no']]}".encode("utf-8")
                ).hexdigest()
                self.assertEqual(row["kam_hash"], expected_hash)

            self.assertTrue(os.path.exists(output_path + ".summary.json"))
            with open(output_path + ".summary.json", encoding="utf-8") as f:
                on_disk_summary = json.load(f)
            self.assertEqual(on_disk_summary, summary)

    def test_requires_tagged_at_unless_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            with self.assertRaises(ValueError):
                tag_kam_batch(facts_path, output_path, call_fn=lambda *a, **k: "[]")

    def test_kam_cache_key_matches_kam_tagger_formula(self) -> None:
        cache: dict[str, Any] = {}
        result = tag_one_kam("원문 X", model="m", base_url="http://x", cache=cache, call_fn=lambda *a, **k: '["손상"]')
        self.assertEqual(_kam_cache_key("m", "원문 X"), result["kam_hash"])


class ResumeNoDuplicateTests(unittest.TestCase):
    def test_resume_does_not_recall_or_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            call_count = 0

            def counting_call_fn(messages, *, model, base_url):
                nonlocal call_count
                call_count += 1
                return '["손상"]'

            first_summary = tag_kam_batch(facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, call_fn=counting_call_fn)
            self.assertEqual(first_summary["tagged_ok"], 2)
            self.assertEqual(call_count, 2)

            second_summary = tag_kam_batch(
                facts_path, output_path, tagged_at="2026-07-02T00:00:00Z", resume=True, call_fn=counting_call_fn
            )

            self.assertEqual(call_count, 2)  # 재호출 없음
            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 2)  # 중복행 0
            self.assertEqual({r["rcept_no"] for r in rows}, {"1", "2"})
            # resume 스캔으로 회수된 rcept는 이번 실행의 tagged_ok로도 재계상된다(누적 집계).
            self.assertEqual(second_summary["tagged_ok"], 2)


class ExceptionIsolationTests(unittest.TestCase):
    def test_one_rcept_failure_does_not_abort_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1", kam_raw="괜찮은 원문"),
                    _fact_row("2", kam_raw="터지는 원문"),
                    _fact_row("3", kam_raw="괜찮은 원문 2"),
                ],
            )
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            def flaky_call_fn(messages, *, model, base_url):
                if "터지는" in messages[-1]["content"]:
                    raise KamLlmError("일부러 실패")
                return '["손상"]'

            summary = tag_kam_batch(facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, call_fn=flaky_call_fn)

            self.assertEqual(summary["tagged_ok"], 2)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["by_error_kind"], {"KamLlmError": 1})

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["rcept_no"] for r in rows}, {"1", "3"})


class TagDistributionSummaryTests(unittest.TestCase):
    def test_tag_distribution_and_dropped_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1", kam_raw="A"),
                    _fact_row("2", kam_raw="B"),
                    _fact_row("3", kam_raw="C"),
                ],
            )
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            responses = {
                "A": '["수익인식"]',
                "B": '["손상","수익인식"]',
                "C": '["없는태그"]',
            }

            def fake_call_fn(messages, *, model, base_url):
                return responses[messages[-1]["content"]]

            summary = tag_kam_batch(facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, call_fn=fake_call_fn)

            self.assertEqual(summary["tag_distribution"], {"수익인식": 2, "손상": 1})
            self.assertEqual(summary["dropped_total"], 1)
            self.assertEqual(summary["tagged_ok"], 3)
            self.assertEqual(summary["failed"], 0)


class ConcurrencyTests(unittest.TestCase):
    def test_concurrency_produces_correct_unique_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_fact_row(str(i), kam_raw=f"원문 {i}") for i in range(8)]
            facts_path = _write_facts(tmp, rows)
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            def slow_call_fn(messages, *, model, base_url):
                time.sleep(0.02)
                return '["수익인식"]'

            summary = tag_kam_batch(
                facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, concurrency=4, call_fn=slow_call_fn
            )

            self.assertEqual(summary["targets"], 8)
            self.assertEqual(summary["tagged_ok"], 8)
            self.assertEqual(summary["failed"], 0)

            written_rows = _read_jsonl(output_path)
            self.assertEqual(len(written_rows), 8)
            self.assertEqual({r["rcept_no"] for r in written_rows}, {str(i) for i in range(8)})


class DefaultCallFnClientSpyTests(unittest.TestCase):
    """Task 2 미테스트분 보강: dry-run이 아닌 실행의 기본 경로가 실제로
    `httpx.Client`를 생성 시도하는지 monkeypatch spy로만 확인한다(실제 소켓
    없이)."""

    def test_default_call_path_constructs_httpx_client(self) -> None:
        constructed: list[Any] = []

        class _SpyClient:
            def __init__(self, *args, **kwargs) -> None:
                constructed.append(self)

            def __enter__(self) -> "_SpyClient":
                return self

            def __exit__(self, *exc_info) -> bool:
                return False

            def post(self, url: str, json: dict[str, Any]) -> Any:
                raise httpx.ConnectError("실제 네트워크 없음 - spy가 의도적으로 발생시킨 예외")

        original_client = httpx.Client
        httpx.Client = _SpyClient  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문")])
                output_path = os.path.join(tmp, "kam_tags.jsonl")

                summary = tag_kam_batch(facts_path, output_path, tagged_at=_FIXED_TAGGED_AT)

                self.assertGreaterEqual(len(constructed), 1)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(summary["by_error_kind"], {"KamLlmError": 1})
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


class CliCommandTests(unittest.TestCase):
    def test_cli_dry_run_prints_targets_and_taxonomy_and_creates_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1"), _fact_row("2", kam_present=False)])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["tag-kam", "--facts", facts_path, "-o", output_path, "--dry-run"],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("대상 1건", result.output)
            self.assertIn(KAM_TAXONOMY[0][0], result.output)
            self.assertFalse(os.path.exists(output_path))

    def test_cli_wires_options_through_to_batch_end_to_end(self) -> None:
        # `tag_kam_batch`의 `call_fn` 기본값(`call_llm`)은 def 시점에 바인딩되므로
        # 모듈 속성 재할당으로는 가로챌 수 없다 - 대신 `call_llm`이 내부에서 여는
        # `httpx.Client`만 소켓 없는 spy로 바꿔 CLI 전체 배선(옵션 -> run_tag_kam
        # -> tag_kam_batch -> call_llm)을 실제 네트워크 없이 검증한다.
        class _FakeResponse:
            status_code = 200

            def json(self) -> Any:
                return {"choices": [{"message": {"content": '["손상"]'}}]}

        class _FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *exc_info) -> bool:
                return False

            def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
                return _FakeResponse()

        original_client = httpx.Client
        httpx.Client = _FakeClient  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문")])
                output_path = os.path.join(tmp, "kam_tags.jsonl")

                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    ["tag-kam", "--facts", facts_path, "-o", output_path],
                )

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("태깅 완료", result.output)
                rows = _read_jsonl(output_path)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["tags"], ["손상"])
                self.assertIn("tagged_at", rows[0])
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
