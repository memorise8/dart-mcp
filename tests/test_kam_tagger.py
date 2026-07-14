"""`dart_search_mcp.tools.kam_tagger`에 대한 테스트.

Task 2(`.omo/plans/dart-kam-llm-tagging.md`): OpenAI 호환 클라이언트(httpx)
호출 + content-hash 캐시 + 단건 태깅. **실제 네트워크 호출은 절대 하지
않는다** — `call_llm`은 fake httpx.Client로, `tag_one_kam`은 fake `call_fn`
주입으로 검증한다.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from dart_search_mcp.tools.kam_tagger import (
    KamLlmError,
    call_llm,
    load_cache,
    save_cache,
    tag_one_kam,
)


def _expected_hash(model: str, kam_raw: str) -> str:
    return hashlib.sha256(f"{model}\n{kam_raw}".encode("utf-8")).hexdigest()


class _FakeResponse:
    def __init__(self, status_code: int, json_data: Any = "no-json", text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> Any:
        if self._json_data == "no-json":
            raise ValueError("no json body")
        return self._json_data


class _FakeClient:
    """httpx.Client와 동일한 `.post(url, json=...)` 인터페이스를 흉내내는 mock."""

    def __init__(self, response: _FakeResponse | None = None, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response


class CallLlmTests(unittest.TestCase):
    def test_posts_expected_body_and_extracts_content(self) -> None:
        fake_response = _FakeResponse(
            200, json_data={"choices": [{"message": {"content": '["수익인식"]'}}]}
        )
        client = _FakeClient(fake_response)

        result = call_llm(
            [{"role": "user", "content": "원문"}],
            model="gpt-5.4-mini",
            base_url="http://example.test/v1",
            client=client,
        )

        self.assertEqual(result, '["수익인식"]')
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["url"], "http://example.test/v1/chat/completions")
        self.assertEqual(call["json"]["model"], "gpt-5.4-mini")
        self.assertEqual(call["json"]["messages"], [{"role": "user", "content": "원문"}])
        self.assertEqual(call["json"]["temperature"], 0)

    def test_non_200_raises_clear_error_without_dumping_body(self) -> None:
        sensitive_body = "매우 민감할 수 있는 응답 본문 내용" * 20
        fake_response = _FakeResponse(500, json_data="no-json", text=sensitive_body)
        client = _FakeClient(fake_response)

        with self.assertRaises(KamLlmError) as ctx:
            call_llm([], model="m", base_url="http://x", client=client)

        message = str(ctx.exception)
        self.assertIn("500", message)
        self.assertNotIn(sensitive_body, message)
        self.assertLess(len(message), len(sensitive_body))

    def test_malformed_json_response_raises_clear_error(self) -> None:
        fake_response = _FakeResponse(200, json_data="no-json")
        client = _FakeClient(fake_response)

        with self.assertRaises(KamLlmError):
            call_llm([], model="m", base_url="http://x", client=client)

    def test_missing_choices_shape_raises_clear_error(self) -> None:
        fake_response = _FakeResponse(200, json_data={"unexpected": True})
        client = _FakeClient(fake_response)

        with self.assertRaises(KamLlmError):
            call_llm([], model="m", base_url="http://x", client=client)

    def test_connection_error_raises_clear_error(self) -> None:
        client = _FakeClient(raise_exc=httpx.ConnectError("boom"))

        with self.assertRaises(KamLlmError) as ctx:
            call_llm([], model="m", base_url="http://x", client=client)

        self.assertIn("http://x", str(ctx.exception))

    def test_timeout_raises_clear_error(self) -> None:
        client = _FakeClient(raise_exc=httpx.TimeoutException("timed out"))

        with self.assertRaises(KamLlmError) as ctx:
            call_llm([], model="m", base_url="http://x", client=client)

        self.assertIn("시간", str(ctx.exception))

    def test_no_network_when_client_injected(self) -> None:
        # client가 주입되면 이 함수는 자체 httpx.Client를 만들지 않는다(소켓 미생성 근거:
        # fake client가 예외 없이 응답을 그대로 돌려주는지로 간접 확인).
        fake_response = _FakeResponse(200, json_data={"choices": [{"message": {"content": "[]"}}]})
        client = _FakeClient(fake_response)
        result = call_llm([], model="m", base_url="http://x", client=client)
        self.assertEqual(result, "[]")


class CacheAtomicRoundTripTests(unittest.TestCase):
    def test_save_then_load_round_trip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "kam_tags.cache.json"
            data = {
                "abc123": {
                    "tags": ["수익인식"],
                    "dropped": [],
                    "kam_hash": "abc123",
                    "model": "m",
                    "base_url": "http://x",
                }
            }

            save_cache(path, data)
            loaded = load_cache(path)

            self.assertEqual(loaded, data)

    def test_save_leaves_no_tmp_file_behind(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "kam_tags.cache.json"
            save_cache(path, {"k": {"tags": [], "dropped": [], "kam_hash": "k", "model": "m", "base_url": "b"}})

            tmp_path = path.with_name(path.name + ".tmp")
            self.assertTrue(path.exists())
            self.assertFalse(tmp_path.exists())

    def test_saved_file_is_valid_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            save_cache(path, {"k": {"tags": [], "dropped": [], "kam_hash": "k", "model": "m", "base_url": "b"}})

            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            self.assertIn("k", parsed)

    def test_load_missing_file_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing.json"
            self.assertEqual(load_cache(path), {})

    def test_load_empty_file_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.json"
            path.write_text("", encoding="utf-8")
            self.assertEqual(load_cache(path), {})


class TagOneKamTests(unittest.TestCase):
    def test_cache_hit_does_not_call_llm(self) -> None:
        kam_raw = "동일 원문"
        model = "gpt-5.4-mini"
        base_url = "http://example.test/v1"
        cache_key = _expected_hash(model, kam_raw)
        cached_value = {
            "tags": ["수익인식"],
            "dropped": [],
            "kam_hash": cache_key,
            "model": model,
            "base_url": base_url,
        }
        cache: dict[str, Any] = {cache_key: cached_value}
        call_count = 0

        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            nonlocal call_count
            call_count += 1
            return '["손상"]'

        result = tag_one_kam(
            kam_raw, model=model, base_url=base_url, cache=cache, call_fn=fake_call_fn
        )

        self.assertEqual(call_count, 0)
        self.assertEqual(result, cached_value)

    def test_cache_miss_calls_llm_and_stores_result(self) -> None:
        received_messages: list[list[dict[str, str]]] = []

        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            received_messages.append(messages)
            return '["수익인식"]'

        cache: dict[str, Any] = {}
        result = tag_one_kam(
            "원문 A", model="gpt-5.4-mini", base_url="http://x", cache=cache, call_fn=fake_call_fn
        )

        self.assertEqual(len(received_messages), 1)
        self.assertEqual(result["tags"], ["수익인식"])
        self.assertEqual(result["dropped"], [])
        self.assertEqual(result["model"], "gpt-5.4-mini")
        self.assertEqual(result["base_url"], "http://x")
        self.assertIn(result["kam_hash"], cache)
        self.assertEqual(cache[result["kam_hash"]], result)

    def test_out_of_taxonomy_tags_are_dropped_via_parse_integration(self) -> None:
        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            return '["수익인식","환율변동"]'

        cache: dict[str, Any] = {}
        result = tag_one_kam(
            "원문 B", model="gpt-5.4-mini", base_url="http://x", cache=cache, call_fn=fake_call_fn
        )

        self.assertEqual(result["tags"], ["수익인식"])
        self.assertEqual(result["dropped"], ["환율변동"])

    def test_cache_key_includes_model_so_different_model_misses(self) -> None:
        kam_raw = "동일 원문"
        seen_models: list[str] = []

        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            seen_models.append(model)
            return '["손상"]'

        cache: dict[str, Any] = {}
        tag_one_kam(kam_raw, model="model-a", base_url="http://x", cache=cache, call_fn=fake_call_fn)
        tag_one_kam(kam_raw, model="model-b", base_url="http://x", cache=cache, call_fn=fake_call_fn)

        self.assertEqual(seen_models, ["model-a", "model-b"])
        self.assertEqual(len(cache), 2)

    def test_second_call_with_same_model_and_kam_is_cache_hit(self) -> None:
        kam_raw = "반복 원문"
        call_count = 0

        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            nonlocal call_count
            call_count += 1
            return '["손상"]'

        cache: dict[str, Any] = {}
        first = tag_one_kam(kam_raw, model="m", base_url="http://x", cache=cache, call_fn=fake_call_fn)
        second = tag_one_kam(kam_raw, model="m", base_url="http://x", cache=cache, call_fn=fake_call_fn)

        self.assertEqual(call_count, 1)
        self.assertEqual(first, second)

    def test_result_has_no_clock_derived_fields(self) -> None:
        def fake_call_fn(messages: list[dict[str, str]], *, model: str, base_url: str) -> str:
            return "[]"

        result = tag_one_kam("원문", model="m", base_url="http://x", cache={}, call_fn=fake_call_fn)

        self.assertNotIn("tagged_at", result)
        self.assertEqual(set(result.keys()), {"tags", "dropped", "kam_hash", "model", "base_url"})

    def test_default_call_fn_is_call_llm(self) -> None:
        import inspect

        from dart_search_mcp.tools.kam_tagger import call_llm as real_call_llm

        signature = inspect.signature(tag_one_kam)
        self.assertIs(signature.parameters["call_fn"].default, real_call_llm)


class CoreModulesDoNotImportKamTaggerTests(unittest.TestCase):
    """코어(parser/adapter/extract_facts)가 이 LLM 모듈을 import하지 않는지 정적으로 확인한다."""

    def test_core_modules_have_no_kam_tagger_reference(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        core_files = [
            repo_root / "dart_search_mcp" / "audit_xml_parser.py",
            repo_root / "dart_search_mcp" / "audit_facts_adapter.py",
            repo_root / "dart_search_mcp" / "tools" / "extract_facts.py",
            repo_root / "dart_search_mcp" / "kam_taxonomy.py",
        ]
        for file_path in core_files:
            text = file_path.read_text(encoding="utf-8")
            self.assertNotIn("kam_tagger", text, f"{file_path} must not reference kam_tagger")


if __name__ == "__main__":
    unittest.main()
