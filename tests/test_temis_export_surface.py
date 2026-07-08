"""
Task 8: opt-in TEMIS export CLI(`dart temis-topic-cases`) + MCP tool
(`export_temis_topic_cases`) 표면 테스트.

이 모듈은 Task 6(`dart_search_mcp.temis_export`)과 Task 5
(`dart_search_mcp.tools.reports.get_audit_report_structured`)를 얇게 감싸는
운영 adapter 경계(`dart_search_mcp.tools.temis`)를 검증한다:

- `uv run dart temis-topic-cases --help`가 0으로 종료한다.
- MCP `list_tools()`에 `export_temis_topic_cases`가 추가되고 기존 16개
  도구 이름이 모두 그대로 남는다 (`tests/test_public_surface.py`에서 별도 검증).
- 모킹된 CLI/MCP 실행이 임시 파일에 유효한 JSON을 쓴다.
- `corp_code`/`corp_name` 둘 다 없거나, `corp_name`이 모호하거나 해석되지
  않으면 CLI는 0이 아닌 종료 코드를 반환하고 출력 파일을 만들지 않는다.
- 출력 경로는 항상 덮어쓴다(overwrite) — 기존 내용을 유지한 채 append하지 않는다.
- `freshness_timestamp`는 이 계층(Task 8)에서 주입되며 ISO-8601(`...Z`) 형식이다.

레포 관례에 따라 `dart_search_mcp.tools.*`를 import하기 전에 `server`를 먼저
import해서 `@mcp.tool()` 등록 순서를 보존한다 (`tests/test_public_surface.py`가
기대하는 정식 순서). 실제 OpenDART로는 어떤 요청도 나가지 않는다
(`httpx.MockTransport`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import httpx
from click.testing import CliRunner

import server  # noqa: F401
import cli
from dart_search_mcp.tools.temis import (
    TemisExportError,
    TemisExportOutcome,
    export_temis_topic_cases,
    export_temis_topic_cases_core,
)

_ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_DUMMY_CLIENT_API_KEY = "test-dummy-crtfc-key-temis-export"
_DUMMY_CORP_API_KEY = "test-dummy-crtfc-key-temis-export-corp"

_RealAsyncClient = httpx.AsyncClient


def _patched_async_client(transport: httpx.MockTransport):
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_dart_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_CLIENT_API_KEY):
        yield


@contextmanager
def mocked_corp_and_list_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Iterator[None]:
    """corpCode.xml(corp.py)과 accnutAdtorNmNdAdtOpinion.json(client.py) 호출을
    하나의 MockTransport로 가로챈다 (같은 `httpx.AsyncClient` 참조를 공유하므로
    같은 transport 인스턴스로 함께 패치해야 한다)."""
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.corp.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.corp.API_KEY", _DUMMY_CORP_API_KEY), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_CLIENT_API_KEY):
        yield


def _samsung_audit_row(rcept_no: str = "20240312000001") -> dict[str, str]:
    return {
        "rcept_no": rcept_no,
        "corp_cls": "Y",
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "bsns_year": "2023",
        "adtor": "삼정회계법인",
        "adt_opinion": "적정",
        "adt_reprt_spcmnt_matter": "풋옵션 약정으로 발생한 금융부채 검토",
        "emphs_matter": "",
        "core_adt_matter": "",
        "stlm_dt": "2023-12-31",
    }


_AUDIT_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [_samsung_audit_row()],
}


def _build_corp_code_zip(records: list[dict[str, str]]) -> bytes:
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    root = ET.Element("result")
    for record in records:
        list_elem = ET.SubElement(root, "list")
        for key in ("corp_code", "corp_name", "stock_code", "modify_date"):
            child = ET.SubElement(list_elem, key)
            child.text = record.get(key, "")

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("CORPCODE.xml", xml_bytes)
    return buffer.getvalue()


_CORP_FIXTURE = [
    {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "modify_date": "20240101"},
    {"corp_code": "00164742", "corp_name": "삼성물산", "stock_code": "028260", "modify_date": "20240101"},
    {"corp_code": "00401731", "corp_name": "삼성SDI", "stock_code": "006400", "modify_date": "20240101"},
]


class CliHelpTests(unittest.TestCase):
    def test_temis_topic_cases_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["temis-topic-cases", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("temis-topic-cases", cli.cli.commands)

    def test_top_level_help_lists_new_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("temis-topic-cases", result.output)


class CoreExportSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_writes_valid_json_array_with_injected_freshness_timestamp(self) -> None:
        called_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called_urls.append(str(request.url))
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with self._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    corp_code="00126380",
                    bsns_year="2023",
                    output_path=output_path,
                )

                self.assertIsInstance(outcome, TemisExportOutcome)
                assert isinstance(outcome, TemisExportOutcome)
                self.assertEqual(outcome.record_count, 1)

                with open(output_path, encoding="utf-8") as f:
                    raw = f.read()

        parsed = json.loads(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 1)
        record = parsed[0]
        self.assertEqual(record["company_identifier"], "00126380")
        self.assertEqual(record["company_name"], "삼성전자")
        self.assertEqual(record["fiscal_year"], 2023)
        self.assertRegex(record["freshness_timestamp"], _ISO_8601_RE)
        self.assertTrue(any("accnutAdtorNmNdAdtOpinion.json" in url for url in called_urls))

    async def test_explicit_freshness_timestamp_is_used_verbatim(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with self._temp_path() as output_path:
                await export_temis_topic_cases_core(
                    corp_code="00126380",
                    bsns_year="2023",
                    output_path=output_path,
                    freshness_timestamp="2026-01-01T00:00:00Z",
                )
                with open(output_path, encoding="utf-8") as f:
                    parsed = json.load(f)

        self.assertEqual(parsed[0]["freshness_timestamp"], "2026-01-01T00:00:00Z")

    async def test_overwrite_semantics_replace_prior_contents(self) -> None:
        """Output path 재사용 시 기존 내용에 append하지 않고 전체를 덮어써야
        Task 6 변환기의 case_id 유일성이 파일 단위로 유지된다."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with self._temp_path() as output_path:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write('[{"stale": "data-from-a-previous-run"}]')

                await export_temis_topic_cases_core(
                    corp_code="00126380",
                    bsns_year="2023",
                    output_path=output_path,
                )

                with open(output_path, encoding="utf-8") as f:
                    parsed = json.load(f)

        self.assertEqual(len(parsed), 1)
        self.assertNotIn("stale", parsed[0])
        case_ids = [r["case_id"] for r in parsed]
        self.assertEqual(len(case_ids), len(set(case_ids)))

    async def test_corp_name_resolves_to_unique_exact_match(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)

        def dispatch(request: httpx.Request) -> httpx.Response:
            if "corpCode.xml" in str(request.url):
                return httpx.Response(200, content=zip_bytes)
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_corp_and_list_transport(dispatch):
            with self._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    corp_name="삼성전자",
                    bsns_year="2023",
                    output_path=output_path,
                )

        self.assertIsInstance(outcome, TemisExportOutcome)
        assert isinstance(outcome, TemisExportOutcome)
        self.assertEqual(outcome.record_count, 1)

    @staticmethod
    @contextmanager
    def _temp_path() -> Iterator[str]:
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        try:
            yield path
        finally:
            if os.path.exists(path):
                os.remove(path)


class CoreExportFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_corp_code_and_corp_name_returns_error_without_writing_file(self) -> None:
        network_called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal network_called
            network_called = True
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with CoreExportSuccessTests._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    bsns_year="2023",
                    output_path=output_path,
                )

                self.assertIsInstance(outcome, TemisExportError)
                self.assertFalse(network_called)
                self.assertFalse(os.path.exists(output_path))

    async def test_ambiguous_corp_name_returns_error_without_writing_file(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)
        list_json_called = False

        def dispatch(request: httpx.Request) -> httpx.Response:
            nonlocal list_json_called
            if "corpCode.xml" in str(request.url):
                return httpx.Response(200, content=zip_bytes)
            list_json_called = True
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_corp_and_list_transport(dispatch):
            with CoreExportSuccessTests._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    corp_name="삼성",
                    bsns_year="2023",
                    output_path=output_path,
                )

                self.assertIsInstance(outcome, TemisExportError)
                assert isinstance(outcome, TemisExportError)
                self.assertIn("삼성전자", outcome.message)
                self.assertFalse(list_json_called, "ambiguous name must not call accnutAdtorNmNdAdtOpinion.json")
                self.assertFalse(os.path.exists(output_path))

    async def test_unresolved_corp_name_returns_error_without_writing_file(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)

        def dispatch(request: httpx.Request) -> httpx.Response:
            if "corpCode.xml" in str(request.url):
                return httpx.Response(200, content=zip_bytes)
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_corp_and_list_transport(dispatch):
            with CoreExportSuccessTests._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    corp_name="존재하지않는회사이름",
                    bsns_year="2023",
                    output_path=output_path,
                )

                self.assertIsInstance(outcome, TemisExportError)
                self.assertFalse(os.path.exists(output_path))

    async def test_missing_bsns_year_returns_error_without_writing_file(self) -> None:
        with CoreExportSuccessTests._temp_path() as output_path:
            outcome = await export_temis_topic_cases_core(
                corp_code="00126380",
                bsns_year="",
                output_path=output_path,
            )

            self.assertIsInstance(outcome, TemisExportError)
            self.assertFalse(os.path.exists(output_path))

    async def test_dart_api_error_returns_error_without_writing_file(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})

        with mocked_dart_transport(handler):
            with CoreExportSuccessTests._temp_path() as output_path:
                outcome = await export_temis_topic_cases_core(
                    corp_code="00126380",
                    bsns_year="2023",
                    output_path=output_path,
                )

                self.assertIsInstance(outcome, TemisExportError)
                self.assertFalse(os.path.exists(output_path))

    async def test_malformed_extra_keywords_returns_error_without_writing_file(self) -> None:
        with CoreExportSuccessTests._temp_path() as output_path:
            outcome = await export_temis_topic_cases_core(
                corp_code="00126380",
                bsns_year="2023",
                output_path=output_path,
                extra_keywords="not-a-valid-pair",
            )

            self.assertIsInstance(outcome, TemisExportError)
            self.assertFalse(os.path.exists(output_path))


class McpToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_temis_topic_cases_tool_is_registered(self) -> None:
        tools = await server.mcp.list_tools()
        names = [tool.name for tool in tools]
        self.assertIn("export_temis_topic_cases", names)

    async def test_tool_writes_file_and_returns_success_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with CoreExportSuccessTests._temp_path() as output_path:
                message = await export_temis_topic_cases(
                    bsns_year="2023",
                    output_path=output_path,
                    corp_code="00126380",
                )
                with open(output_path, encoding="utf-8") as f:
                    parsed = json.load(f)

        self.assertIsInstance(message, str)
        self.assertEqual(len(parsed), 1)

    async def test_tool_returns_error_message_without_writing_file_when_corp_missing(self) -> None:
        with CoreExportSuccessTests._temp_path() as output_path:
            message = await export_temis_topic_cases(
                bsns_year="2023",
                output_path=output_path,
            )

            self.assertIsInstance(message, str)
            self.assertIn("오류", message)
            self.assertFalse(os.path.exists(output_path))


class CliIntegrationTests(unittest.TestCase):
    def test_cli_missing_corp_exits_nonzero_without_writing_file(self) -> None:
        with CoreExportSuccessTests._temp_path() as output_path:
            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["temis-topic-cases", "2023", "-o", output_path],
            )

            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(os.path.exists(output_path))

    def test_cli_mocked_run_writes_valid_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            with CoreExportSuccessTests._temp_path() as output_path:
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    ["temis-topic-cases", "2023", "--code", "00126380", "-o", output_path],
                )

                self.assertEqual(result.exit_code, 0, result.output)
                with open(output_path, encoding="utf-8") as f:
                    parsed = json.load(f)

        self.assertEqual(len(parsed), 1)

    def test_cli_ambiguous_corp_name_exits_nonzero_without_writing_file(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)

        def dispatch(request: httpx.Request) -> httpx.Response:
            if "corpCode.xml" in str(request.url):
                return httpx.Response(200, content=zip_bytes)
            return httpx.Response(200, json=_AUDIT_RESPONSE)

        with mocked_corp_and_list_transport(dispatch):
            with CoreExportSuccessTests._temp_path() as output_path:
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    ["temis-topic-cases", "2023", "--corp", "삼성", "-o", output_path],
                )

                self.assertNotEqual(result.exit_code, 0)
                self.assertFalse(os.path.exists(output_path))


if __name__ == "__main__":
    unittest.main()
