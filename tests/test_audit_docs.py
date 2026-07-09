"""`dart_search_mcp.tools.audit_docs`에 대한 테스트.

Step 2b: Step 2a(`document_zip.inspect_document_zip`)로 분류한 ZIP 엔트리 중
감사보고서/연결감사보고서 XML을 실제로 디스크에 추출하고 매니페스트를 쓰는
계층. 실제 OpenDART 호출은 전혀 하지 않는다 — `_fetch_dart_binary`(다운로드
경계)와 `_load_corp_codes`(회사명 해석 경계)를 모킹하고, ZIP은
`zipfile.ZipFile(BytesIO(), "w")`로 만든 인메모리 fixture만 사용한다.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

import cli
import server  # noqa: F401
from dart_search_mcp import corp
from dart_search_mcp.corp import CorpNameAmbiguous, CorpRecord
from dart_search_mcp.tools.audit_docs import (
    AuditDocsError,
    AuditDocsOutcome,
    extract_audit_documents_core,
)

_RCEPT_NO = "20260323001689"


def _document_xml(acode: str, name: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<DOCUMENT xmlns="http://dart.fss.or.kr/dsae">'
        f'<DOCUMENT-NAME ACODE="{acode}">{name}</DOCUMENT-NAME>'
        "</DOCUMENT>"
    )
    return body.encode("utf-8")


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for filename, content in entries.items():
            zf.writestr(filename, content)
    return buf.getvalue()


_AUDIT_ONLY_ZIP = _build_zip({f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서")})

_BOTH_ZIP = _build_zip(
    {
        f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서"),
        f"{_RCEPT_NO}_00761.xml": _document_xml("00761", "연결감사보고서"),
    }
)


class _TempDirMixin:
    def _make_output_dir(self) -> str:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)  # type: ignore[attr-defined]
        return tmpdir.name


class RceptNoDirectTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_rcept_no_direct_audit_only_zip_writes_file_and_manifest(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _AUDIT_ONLY_ZIP
            outcome = await extract_audit_documents_core(rcept_no=_RCEPT_NO, output_dir=output_dir)

        self.assertIsInstance(outcome, AuditDocsOutcome)
        assert isinstance(outcome, AuditDocsOutcome)
        self.assertTrue(outcome.audit_found)
        self.assertFalse(outcome.consolidated_found)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        written_file = os.path.join(target_dir, f"{_RCEPT_NO}_00760.xml")
        self.assertTrue(os.path.isfile(written_file))

        with open(written_file, "rb") as f:
            written_bytes = f.read()
        with zipfile.ZipFile(BytesIO(_AUDIT_ONLY_ZIP)) as zf:
            source_bytes = zf.read(f"{_RCEPT_NO}_00760.xml")
        self.assertEqual(written_bytes, source_bytes, "쓴 파일 바이트는 ZIP 엔트리 원본과 동일해야 한다")

        manifest_path = os.path.join(target_dir, "manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["rcept_no"], _RCEPT_NO)
        self.assertTrue(manifest["audit"]["found"])
        self.assertFalse(manifest["consolidated_audit"]["found"])
        self.assertEqual(len(manifest["files"]), 1)


class IncludeBothTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_include_both_writes_both_files_and_manifest(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _BOTH_ZIP
            outcome = await extract_audit_documents_core(
                rcept_no=_RCEPT_NO, output_dir=output_dir, include="both"
            )

        self.assertIsInstance(outcome, AuditDocsOutcome)
        assert isinstance(outcome, AuditDocsOutcome)
        self.assertTrue(outcome.audit_found)
        self.assertTrue(outcome.consolidated_found)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        self.assertTrue(os.path.isfile(os.path.join(target_dir, f"{_RCEPT_NO}_00760.xml")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, f"{_RCEPT_NO}_00761.xml")))

        with open(os.path.join(target_dir, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertTrue(manifest["audit"]["found"])
        self.assertTrue(manifest["consolidated_audit"]["found"])
        self.assertEqual(len(manifest["files"]), 2)


class RequireConsolidatedTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_require_consolidated_missing_errors_and_leaves_no_partial_output(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _AUDIT_ONLY_ZIP
            outcome = await extract_audit_documents_core(
                rcept_no=_RCEPT_NO,
                output_dir=output_dir,
                include="consolidated",
                require_consolidated=True,
            )

        self.assertIsInstance(outcome, AuditDocsError)
        assert isinstance(outcome, AuditDocsError)
        self.assertTrue(outcome.message)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        self.assertFalse(os.path.exists(target_dir), "실패 시 대상 디렉토리가 전혀 생성되면 안 된다")

    async def test_without_require_consolidated_missing_is_recorded_not_found_and_succeeds(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _AUDIT_ONLY_ZIP
            outcome = await extract_audit_documents_core(
                rcept_no=_RCEPT_NO,
                output_dir=output_dir,
                include="consolidated",
                require_consolidated=False,
            )

        self.assertIsInstance(outcome, AuditDocsOutcome)
        assert isinstance(outcome, AuditDocsOutcome)
        self.assertFalse(outcome.consolidated_found)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        self.assertFalse(
            os.path.isfile(os.path.join(target_dir, f"{_RCEPT_NO}_00761.xml")),
            "연결감사보고서 파일이 없어도 되지만 실제로 존재하면 안 된다",
        )

        with open(os.path.join(target_dir, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertFalse(manifest["consolidated_audit"]["found"])
        self.assertEqual(len(manifest["files"]), 0)

    async def test_include_audit_require_consolidated_both_present_succeeds_and_writes_consolidated(
        self,
    ) -> None:
        """include="audit"만 요청해도 require_consolidated=True면 ZIP에 실재하는
        연결감사보고서를 검증하고 함께 써야 한다 (Finding 1)."""
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _BOTH_ZIP
            outcome = await extract_audit_documents_core(
                rcept_no=_RCEPT_NO,
                output_dir=output_dir,
                include="audit",
                require_consolidated=True,
            )

        self.assertIsInstance(outcome, AuditDocsOutcome)
        assert isinstance(outcome, AuditDocsOutcome)
        self.assertTrue(outcome.audit_found)
        self.assertTrue(outcome.consolidated_found)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        self.assertTrue(os.path.isfile(os.path.join(target_dir, f"{_RCEPT_NO}_00760.xml")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, f"{_RCEPT_NO}_00761.xml")))

        with open(os.path.join(target_dir, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertTrue(manifest["consolidated_audit"]["found"])
        self.assertEqual(len(manifest["files"]), 2)

    async def test_include_audit_require_consolidated_missing_errors_and_leaves_no_partial_output(
        self,
    ) -> None:
        """include="audit"이고 ZIP에 연결감사보고서가 실제로 없으면 여전히
        오류이며 부분 출력도 없어야 한다 (Finding 1의 반대 경로)."""
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = _AUDIT_ONLY_ZIP
            outcome = await extract_audit_documents_core(
                rcept_no=_RCEPT_NO,
                output_dir=output_dir,
                include="audit",
                require_consolidated=True,
            )

        self.assertIsInstance(outcome, AuditDocsError)
        assert isinstance(outcome, AuditDocsError)
        self.assertTrue(outcome.message)

        target_dir = os.path.join(output_dir, _RCEPT_NO)
        self.assertFalse(os.path.exists(target_dir), "실패 시 대상 디렉토리가 전혀 생성되면 안 된다")


class CorpNameResolutionTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        corp._corp_code_cache = None

    def tearDown(self) -> None:
        corp._corp_code_cache = None

    async def test_unique_corp_name_resolves_then_extracts(self) -> None:
        output_dir = self._make_output_dir()

        with patch.object(corp, "_load_corp_codes", new_callable=AsyncMock) as mock_load, patch(
            "dart_search_mcp.tools.audit_docs._resolve_rcept_no", new_callable=AsyncMock
        ) as mock_resolve_rcept, patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_load.return_value = [
                {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "modify_date": ""}
            ]
            mock_resolve_rcept.return_value = _RCEPT_NO
            mock_fetch.return_value = _AUDIT_ONLY_ZIP

            outcome = await extract_audit_documents_core(
                corp_name="삼성전자", bsns_year="2024", output_dir=output_dir
            )

        self.assertIsInstance(outcome, AuditDocsOutcome)
        assert isinstance(outcome, AuditDocsOutcome)
        self.assertEqual(outcome.rcept_no, _RCEPT_NO)
        self.assertEqual(outcome.corp_code, "00126380")
        mock_resolve_rcept.assert_awaited_once_with("00126380", "2024", "11011")

    async def test_ambiguous_corp_name_errors_without_calling_download(self) -> None:
        output_dir = self._make_output_dir()

        candidates = [
            CorpRecord(corp_code="00126380", corp_name="삼성전자A", stock_code="", modify_date=""),
            CorpRecord(corp_code="00999999", corp_name="삼성전자B", stock_code="", modify_date=""),
        ]

        with patch.object(corp, "_load_corp_codes", new_callable=AsyncMock) as mock_load, patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch, patch(
            "dart_search_mcp.tools.audit_docs.resolve_single_corp_code", new_callable=AsyncMock
        ) as mock_resolve_single:
            mock_resolve_single.return_value = CorpNameAmbiguous(corp_name="삼성전자", candidates=candidates)

            outcome = await extract_audit_documents_core(
                corp_name="삼성전자", bsns_year="2024", output_dir=output_dir
            )

        self.assertIsInstance(outcome, AuditDocsError)
        assert isinstance(outcome, AuditDocsError)
        mock_fetch.assert_not_awaited()
        mock_load.assert_not_awaited()
        self.assertFalse(os.path.exists(os.path.join(output_dir, _RCEPT_NO)))


class DownloadErrorTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_download_error_returns_typed_error_and_writes_no_files(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = "오류: 요청 시간이 초과되었습니다."
            outcome = await extract_audit_documents_core(rcept_no=_RCEPT_NO, output_dir=output_dir)

        self.assertIsInstance(outcome, AuditDocsError)
        assert isinstance(outcome, AuditDocsError)
        self.assertIn("오류", outcome.message)
        self.assertEqual(os.listdir(output_dir), [])


class CorruptZipTests(_TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_corrupt_zip_returns_typed_error_and_writes_no_files(self) -> None:
        output_dir = self._make_output_dir()

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = b"this is definitely not a zip file"
            outcome = await extract_audit_documents_core(rcept_no=_RCEPT_NO, output_dir=output_dir)

        self.assertIsInstance(outcome, AuditDocsError)
        assert isinstance(outcome, AuditDocsError)
        self.assertEqual(os.listdir(output_dir), [])


class CliAndToolSurfaceTests(unittest.TestCase):
    def test_audit_documents_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["audit-documents", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--rcept-no", result.output)
        self.assertIn("--require-consolidated", result.output)

    def test_list_tools_includes_new_tool_and_all_priors(self) -> None:
        import asyncio

        async def list_tool_names() -> list[str]:
            tools = await server.mcp.list_tools()
            return [tool.name for tool in tools]

        names = asyncio.run(list_tool_names())
        self.assertIn("extract_audit_documents", names)
        self.assertEqual(len(names), 18)


class CliIntegrationTests(unittest.TestCase):
    def test_cli_download_error_exits_nonzero_without_writing_output(self) -> None:
        output_dir = tempfile.TemporaryDirectory()
        self.addCleanup(output_dir.cleanup)

        with patch(
            "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = "오류: 요청 시간이 초과되었습니다."
            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["audit-documents", "--rcept-no", _RCEPT_NO, "-o", output_dir.name],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(os.listdir(output_dir.name), [], "실패 시 출력 디렉토리에 아무 파일도 없어야 한다")


if __name__ == "__main__":
    unittest.main()
