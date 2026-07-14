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
    load_kam_tags_map,
    merge_kam_tags,
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


def _write_tags_raw(tmp_dir: str, lines: list[str], name: str = "kam_tags.jsonl") -> str:
    """`merge_kam_tags`(Task 4)의 손상 라인 방어 테스트용 - dict가 아니라
    이미 직렬화된 원문 줄을 그대로 쓴다(손상된 줄도 그대로 끼워넣기 위해)."""
    path = os.path.join(tmp_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line)
            f.write("\n")
    return path


def _tag_row_json(rcept_no: str, tags: list[str]) -> str:
    return json.dumps(
        {
            "rcept_no": rcept_no,
            "tags": tags,
            "dropped": [],
            "kam_hash": "h",
            "model": "gpt-5.4-mini",
            "base_url": "http://x/v1",
            "tagged_at": "2026-07-01T00:00:00Z",
        },
        ensure_ascii=False,
    )


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
                    f"gpt-5.4-mini\nhttp://x/v1\n{kam_raw_by_rcept[row['rcept_no']]}".encode("utf-8")
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

    def test_kam_cache_key_includes_base_url_and_diverges_from_kam_tagger_formula(self) -> None:
        # Fix 2: `_kam_cache_key`는 base_url도 해시에 포함한다(provenance 정합을
        # 위해 `kam_tagger._cache_key`와 의도적으로 다른 공식). 같은 model/kam_raw라도
        # base_url이 다르면 키가 달라야 한다.
        key_a = _kam_cache_key("m", "http://a/v1", "원문 X")
        key_b = _kam_cache_key("m", "http://b/v1", "원문 X")
        self.assertNotEqual(key_a, key_b)
        self.assertEqual(
            key_a,
            hashlib.sha256("m\nhttp://a/v1\n원문 X".encode("utf-8")).hexdigest(),
        )

        # kam_tagger.tag_one_kam(base_url 미포함 공식)의 kam_hash와는 더 이상
        # 같지 않다 - 이 CLI는 tag_one_kam을 호출하지 않고 캐시를 자체 관리하므로
        # 두 공식이 일치할 필요가 없다.
        cache: dict[str, Any] = {}
        result = tag_one_kam("원문 X", model="m", base_url="http://a/v1", cache=cache, call_fn=lambda *a, **k: '["손상"]')
        self.assertNotEqual(key_a, result["kam_hash"])


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


class DuplicateRceptWithinRunTests(unittest.TestCase):
    """Fix 1: 같은 실행(run) 안에서 같은 rcept_no가 입력에 두 번 있으면(캐시
    콜드) 둘 다 완료 전에 풀에 제출될 수 있는데, 출력 JSONL에는 그 rcept_no가
    정확히 1행만 남아야 한다."""

    def test_duplicate_rcept_no_in_input_produces_single_output_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1", kam_raw="원문 A"),
                    _fact_row("1", kam_raw="원문 A"),  # 같은 rcept_no 중복
                    _fact_row("2", kam_raw="원문 B"),
                ],
            )
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            def fake_call_fn(messages, *, model, base_url):
                return '["수익인식"]'

            summary = tag_kam_batch(
                facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, concurrency=4, call_fn=fake_call_fn
            )

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 2)  # rcept "1" 중복이 아니라 1행만
            self.assertEqual(sorted(r["rcept_no"] for r in rows), ["1", "2"])
            self.assertEqual(summary["targets"], 3)

    def test_duplicate_rcept_no_cache_hit_path_also_dedupes(self) -> None:
        # 같은 rcept_no가 2번, kam_raw도 동일 -> 첫 처리 후 캐시가 즉시 채워져
        # 두 번째는 캐시 히트 경로를 타지만 여전히 1행만 남아야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp,
                [
                    _fact_row("1", kam_raw="같은 원문"),
                    _fact_row("1", kam_raw="같은 원문"),
                ],
            )
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            call_count = 0

            def counting_call_fn(messages, *, model, base_url):
                nonlocal call_count
                call_count += 1
                return '["손상"]'

            summary = tag_kam_batch(
                facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, concurrency=1, call_fn=counting_call_fn
            )

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rcept_no"], "1")
            self.assertEqual(call_count, 1)
            self.assertEqual(summary["tagged_ok"], 1)


class CacheKeyBaseUrlTests(unittest.TestCase):
    """Fix 2: 캐시 키에 base_url이 포함되어야 한다 - 같은 model/kam_raw라도
    base_url이 다르면 캐시 미스로 재호출되고, 저장되는 행의 base_url도 그
    실행의 base_url과 일치해야 한다."""

    def test_different_base_url_causes_cache_miss_and_correct_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")
            cache_path = os.path.join(tmp, "shared.cache.json")

            call_count = 0

            def counting_call_fn(messages, *, model, base_url):
                nonlocal call_count
                call_count += 1
                return '["손상"]'

            first_summary = tag_kam_batch(
                facts_path,
                output_path,
                tagged_at=_FIXED_TAGGED_AT,
                base_url="http://endpoint-a/v1",
                cache_path=cache_path,
                call_fn=counting_call_fn,
            )
            self.assertEqual(call_count, 1)
            self.assertEqual(first_summary["cache_hits"], 0)

            rows_a = _read_jsonl(output_path)
            self.assertEqual(rows_a[0]["base_url"], "http://endpoint-a/v1")

            # 같은 model/kam_raw, 다른 base_url, 같은 캐시 파일을 재사용해도
            # 캐시가 히트되면 안 된다(콜드 -> 재호출).
            output_path_b = os.path.join(tmp, "kam_tags_b.jsonl")
            second_summary = tag_kam_batch(
                facts_path,
                output_path_b,
                tagged_at=_FIXED_TAGGED_AT,
                base_url="http://endpoint-b/v1",
                cache_path=cache_path,
                call_fn=counting_call_fn,
            )
            self.assertEqual(call_count, 2)  # 재호출됨 - 캐시 미스
            self.assertEqual(second_summary["cache_hits"], 0)

            rows_b = _read_jsonl(output_path_b)
            self.assertEqual(rows_b[0]["base_url"], "http://endpoint-b/v1")
            # 캐시 키(kam_hash)도 base_url별로 달라야 한다.
            self.assertNotEqual(rows_a[0]["kam_hash"], rows_b[0]["kam_hash"])


class RunParamsGuardResumeTests(unittest.TestCase):
    """Fix 3: `--resume`에서 model/base_url이 이전 실행과 다르면
    `_ensure_run_params_match_checkpoint`가 `ValueError`를 던져야 하고,
    CLI 경로에서는 exit code 1로 매핑되어야 한다."""

    def test_resume_with_different_model_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            tag_kam_batch(
                facts_path, output_path, tagged_at=_FIXED_TAGGED_AT, model="model-a", call_fn=lambda *a, **k: "[]"
            )

            with self.assertRaises(ValueError):
                tag_kam_batch(
                    facts_path,
                    output_path,
                    tagged_at="2026-07-02T00:00:00Z",
                    model="model-b",
                    resume=True,
                    call_fn=lambda *a, **k: "[]",
                )

    def test_resume_with_different_base_url_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

            tag_kam_batch(
                facts_path,
                output_path,
                tagged_at=_FIXED_TAGGED_AT,
                base_url="http://endpoint-a/v1",
                call_fn=lambda *a, **k: "[]",
            )

            with self.assertRaises(ValueError):
                tag_kam_batch(
                    facts_path,
                    output_path,
                    tagged_at="2026-07-02T00:00:00Z",
                    base_url="http://endpoint-b/v1",
                    resume=True,
                    call_fn=lambda *a, **k: "[]",
                )

    def test_cli_resume_with_different_model_exits_with_code_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문")])
            output_path = os.path.join(tmp, "kam_tags.jsonl")

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
                runner = CliRunner()
                first_result = runner.invoke(
                    cli.cli,
                    ["tag-kam", "--facts", facts_path, "-o", output_path, "--model", "model-a"],
                )
                self.assertEqual(first_result.exit_code, 0, first_result.output)

                second_result = runner.invoke(
                    cli.cli,
                    ["tag-kam", "--facts", facts_path, "-o", output_path, "--model", "model-b", "--resume"],
                )

                self.assertEqual(second_result.exit_code, 1)
                self.assertIn("오류", second_result.output)
            finally:
                httpx.Client = original_client  # type: ignore[assignment]


class LoadKamTagsMapTests(unittest.TestCase):
    """Task 4: `load_kam_tags_map`(rcept_no -> tags 매핑 로딩)."""

    def test_loads_valid_rows_into_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tags_path = _write_tags_raw(
                tmp, [_tag_row_json("1", ["수익인식"]), _tag_row_json("2", ["손상", "수익인식"])]
            )

            mapping = load_kam_tags_map(tags_path)

            self.assertEqual(mapping, {"1": ["수익인식"], "2": ["손상", "수익인식"]})

    def test_corrupted_and_malformed_lines_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tags_path = _write_tags_raw(
                tmp,
                [
                    _tag_row_json("1", ["수익인식"]),
                    "{not valid json",  # torn/손상 라인
                    '{"rcept_no": 123, "tags": ["x"]}',  # rcept_no가 문자열이 아님
                    '{"tags": ["y"]}',  # rcept_no 없음
                    '{"rcept_no": "2", "tags": "손상"}',  # tags가 리스트가 아님
                    "",  # 빈 줄
                ],
            )

            mapping = load_kam_tags_map(tags_path)

            self.assertEqual(mapping, {"1": ["수익인식"]})

    def test_duplicate_rcept_no_last_line_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tags_path = _write_tags_raw(
                tmp, [_tag_row_json("1", ["수익인식"]), _tag_row_json("1", ["손상"])]
            )

            mapping = load_kam_tags_map(tags_path)

            self.assertEqual(mapping, {"1": ["손상"]})

    def test_missing_tags_file_raises_source_error(self) -> None:
        with self.assertRaises(TagKamSourceError):
            load_kam_tags_map("/no/such/kam_tags.jsonl")


class MergeKamTagsJoinTests(unittest.TestCase):
    """Task 4: `merge_kam_tags` join 정확성 + 미매칭 처리 + 다른 필드 불변."""

    def test_join_fills_matched_rows_and_keeps_unmatched_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_rows = [
                _fact_row("1", kam_raw="원문 A"),
                _fact_row("2", kam_raw="원문 B"),
                _fact_row("3", kam_raw="원문 C"),
            ]
            for row in facts_rows:
                row["kam_tags"] = []  # ①의 실제 산출물과 동형(항상 kam_tags: [] 보유)
            facts_path = _write_facts(tmp, facts_rows)
            tags_path = _write_tags_raw(
                tmp, [_tag_row_json("1", ["수익인식"]), _tag_row_json("2", ["손상", "수익인식"])]
            )
            output_path = os.path.join(tmp, "audit_facts.enriched.jsonl")

            result = merge_kam_tags(facts_path, tags_path, output_path)

            self.assertEqual(result["facts_rows"], 3)
            self.assertEqual(result["matched"], 2)
            self.assertEqual(result["unmatched"], 1)

            rows = _read_jsonl(output_path)
            by_rcept = {r["rcept_no"]: r for r in rows}
            self.assertEqual(by_rcept["1"]["kam_tags"], ["수익인식"])
            self.assertEqual(by_rcept["2"]["kam_tags"], ["손상", "수익인식"])
            self.assertEqual(by_rcept["3"]["kam_tags"], [])  # 미매칭 -> ①의 [] 유지

            # 나머지 필드는 손대지 않는다.
            self.assertEqual(by_rcept["1"]["kam_raw"], "원문 A")
            self.assertEqual(by_rcept["1"]["corp_name"], "테스트법인")
            self.assertEqual(by_rcept["1"]["corp_cls"], "Y")
            self.assertEqual(by_rcept["1"]["kam_present"], True)

    def test_corrupted_tags_lines_do_not_crash_and_leave_rows_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B")])
            tags_path = _write_tags_raw(
                tmp,
                [
                    _tag_row_json("1", ["수익인식"]),
                    "{not valid json",
                    '{"rcept_no": "2", "tags": "손상"}',  # tags가 리스트가 아님 -> 무시
                ],
            )
            output_path = os.path.join(tmp, "enriched.jsonl")

            result = merge_kam_tags(facts_path, tags_path, output_path)

            rows = _read_jsonl(output_path)
            by_rcept = {r["rcept_no"]: r for r in rows}
            self.assertEqual(by_rcept["1"]["kam_tags"], ["수익인식"])
            self.assertEqual(by_rcept["2"]["kam_tags"], [])
            self.assertEqual(result["matched"], 1)
            self.assertEqual(result["unmatched"], 1)

    def test_duplicate_rcept_no_in_tags_last_one_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A")])
            tags_path = _write_tags_raw(
                tmp, [_tag_row_json("1", ["수익인식"]), _tag_row_json("1", ["손상"])]
            )
            output_path = os.path.join(tmp, "enriched.jsonl")

            result = merge_kam_tags(facts_path, tags_path, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kam_tags"], ["손상"])  # 마지막 줄 값 우선
            self.assertEqual(result["matched"], 1)

    def test_missing_facts_file_raises_source_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tags_path = _write_tags_raw(tmp, [_tag_row_json("1", ["수익인식"])])
            with self.assertRaises(TagKamSourceError):
                merge_kam_tags("/no/such/facts.jsonl", tags_path, os.path.join(tmp, "out.jsonl"))

    def test_missing_tags_file_raises_source_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1")])
            with self.assertRaises(TagKamSourceError):
                merge_kam_tags(facts_path, "/no/such/tags.jsonl", os.path.join(tmp, "out.jsonl"))


class MergeKamTagsInputImmutableTests(unittest.TestCase):
    """Task 4: 입력 facts 파일은 바이트 단위로 불변이어야 한다."""

    def test_input_facts_bytes_unchanged_after_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B")])
            tags_path = _write_tags_raw(tmp, [_tag_row_json("1", ["수익인식"])])
            output_path = os.path.join(tmp, "enriched.jsonl")

            before = Path(facts_path).read_bytes()
            merge_kam_tags(facts_path, tags_path, output_path)
            after = Path(facts_path).read_bytes()

            self.assertEqual(before, after)


class MergeKamTagsDeterminismTests(unittest.TestCase):
    """Task 4: 같은 두 입력이면 항상 같은 출력 바이트(시계/네트워크 미접근)."""

    def test_same_input_produces_same_output_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B")])
            tags_path = _write_tags_raw(tmp, [_tag_row_json("1", ["수익인식"]), _tag_row_json("2", ["손상"])])
            output_path_1 = os.path.join(tmp, "enriched1.jsonl")
            output_path_2 = os.path.join(tmp, "enriched2.jsonl")

            merge_kam_tags(facts_path, tags_path, output_path_1)
            merge_kam_tags(facts_path, tags_path, output_path_2)

            self.assertEqual(Path(output_path_1).read_bytes(), Path(output_path_2).read_bytes())


class MergeKamTagsAtomicWriteTests(unittest.TestCase):
    """Task 4: 출력은 tmp+replace로 원자적으로 쓴다 - 부분 파일이 남지 않는다."""

    def test_atomic_write_leaves_no_tmp_file_and_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(
                tmp, [_fact_row(str(i), kam_raw=f"원문 {i}") for i in range(5)]
            )
            tags_path = _write_tags_raw(tmp, [_tag_row_json("0", ["수익인식"])])
            output_path = os.path.join(tmp, "enriched.jsonl")

            merge_kam_tags(facts_path, tags_path, output_path)

            self.assertTrue(os.path.exists(output_path))
            self.assertFalse(os.path.exists(output_path + ".merge.tmp"))
            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 5)


class MergeKamTagsCliCommandTests(unittest.TestCase):
    """Task 4: `dart merge-kam-tags` CLI 배선."""

    def test_cli_merge_kam_tags_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = _write_facts(tmp, [_fact_row("1", kam_raw="원문 A"), _fact_row("2", kam_raw="원문 B")])
            tags_path = _write_tags_raw(tmp, [_tag_row_json("1", ["수익인식"])])
            output_path = os.path.join(tmp, "enriched.jsonl")

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["merge-kam-tags", "--facts", facts_path, "--tags", tags_path, "-o", output_path],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("병합 완료", result.output)
            rows = _read_jsonl(output_path)
            by_rcept = {r["rcept_no"]: r for r in rows}
            self.assertEqual(by_rcept["1"]["kam_tags"], ["수익인식"])
            self.assertEqual(by_rcept["2"]["kam_tags"], [])

    def test_cli_missing_facts_file_exits_with_code_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tags_path = _write_tags_raw(tmp, [_tag_row_json("1", ["수익인식"])])
            output_path = os.path.join(tmp, "enriched.jsonl")

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["merge-kam-tags", "--facts", "/no/such/facts.jsonl", "--tags", tags_path, "-o", output_path],
            )

            self.assertEqual(result.exit_code, 1)
            self.assertIn("오류", result.output)


class TagKamEnvVarOverrideTests(unittest.TestCase):
    """`--base-url`/`--model`이 env `KAM_LLM_BASE_URL`/`KAM_LLM_MODEL`로도
    오버라이드 가능해야 한다(문서화된 동작 - 명시 플래그가 항상 env보다
    우선하고, env가 하드코딩된 기본값보다 우선한다)."""

    def test_env_vars_override_defaults_when_flags_absent(self) -> None:
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
                    env={"KAM_LLM_BASE_URL": "http://env-endpoint/v1", "KAM_LLM_MODEL": "env-model"},
                )

                self.assertEqual(result.exit_code, 0, result.output)
                rows = _read_jsonl(output_path)
                self.assertEqual(rows[0]["base_url"], "http://env-endpoint/v1")
                self.assertEqual(rows[0]["model"], "env-model")
        finally:
            httpx.Client = original_client  # type: ignore[assignment]

    def test_explicit_flag_overrides_env_var(self) -> None:
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
                    ["tag-kam", "--facts", facts_path, "-o", output_path, "--model", "flag-model"],
                    env={"KAM_LLM_MODEL": "env-model"},
                )

                self.assertEqual(result.exit_code, 0, result.output)
                rows = _read_jsonl(output_path)
                self.assertEqual(rows[0]["model"], "flag-model")
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
