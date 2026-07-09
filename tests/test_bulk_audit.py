"""`dart_search_mcp.tools.bulk_audit`(체크포인트 가능한 대량 감사서류 추출)에
대한 테스트.

두 종류의 테스트로 나뉜다:

- `extract_audit_documents_core`(단일 필링 추출기)를 `unittest.mock.patch`로
  직접 대체해, bulk 루프 자체(예외 격리/체크포인트-재개/`--limit`/run-params
  가드)만 검증하는 테스트. 이 경우 대체 함수가 반환하는 `AuditDocsError`에
  `kind`를 직접 지정한다.
- `_fetch_dart_binary`만 모킹하고 `extract_audit_documents_core`는 실제
  코드를 그대로 태워, bulk 상태 분류가 실제 `AuditDocsError.kind`(메시지
  문구가 아니라)로부터 나온다는 것을 end-to-end로 검증하는 테스트
  (`KindClassificationTests`).

실제 OpenDART로는 어떤 요청도 나가지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

import cli
from dart_search_mcp.tools.audit_docs import AuditDocsError, AuditDocsOutcome
from dart_search_mcp.tools.bulk_audit import (
    BulkAuditSourceError,
    FilingInput,
    bulk_extract_audit_documents,
    load_filings_from_manifest,
    load_filings_from_rcept_json,
)

_NO_CONSOLIDATED_MESSAGE = "오류: 연결감사보고서를 찾을 수 없습니다 (rcept_no=NOCONSOL1)."


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


def _fake_success(rcept_no: str, output_dir: str) -> AuditDocsOutcome:
    target_dir = os.path.join(output_dir, rcept_no)
    os.makedirs(target_dir, exist_ok=True)
    manifest_path = os.path.join(target_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"rcept_no": rcept_no}, f)
    return AuditDocsOutcome(
        rcept_no=rcept_no,
        corp_code="00126380",
        output_dir=target_dir,
        manifest_path=manifest_path,
        written=[],
        audit_found=True,
        consolidated_found=True,
        message=f"추출 완료: {rcept_no}",
    )


class MixedOutcomesTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_outcomes_run_completes_and_records_every_status(self) -> None:
        """SUCCESS1(성공), RAISES1(예외 발생), UNCLASSIFIED1(kind 없는/방어적
        오류 - corp_name 해석 실패류처럼 실제 bulk 경로에서는 나올 수 없는
        오류를 시뮬레이션), NOCONSOL1(연결감사보고서 없음, kind="no_consolidated")
        네 건을 한 번에 처리한다 - 하나의 실패/예외도 다른 필링 처리를 막지
        않아야 한다.

        UNCLASSIFIED1은 `AuditDocsError`가 `kind`를 지정하지 않은(기본값
        `"error"`) 경우를 시뮬레이션한다 - 이런 오류는 절대 skip으로
        오분류되지 않고 `failed`로 귀속되어야 한다."""
        calls: list[str] = []

        async def fake_extract(*, rcept_no, output_dir, include, require_consolidated):
            calls.append(rcept_no)
            if rcept_no == "SUCCESS1":
                return _fake_success(rcept_no, output_dir)
            if rcept_no == "RAISES1":
                # 파손/암호화된 ZIP 등에서 실제로 나올 수 있는 raise를 시뮬레이션한다.
                raise RuntimeError("암호화된 ZIP을 열 수 없습니다 (시뮬레이션)")
            if rcept_no == "UNCLASSIFIED1":
                return AuditDocsError(message="오류: 분류되지 않은 오류입니다 (시뮬레이션)")
            if rcept_no == "NOCONSOL1":
                return AuditDocsError(message=_NO_CONSOLIDATED_MESSAGE, kind="no_consolidated")
            raise AssertionError(f"예상치 못한 rcept_no: {rcept_no}")

        filings = [
            FilingInput(rcept_no="SUCCESS1", corp_code="00126380", corp_name="삼성전자"),
            FilingInput(rcept_no="RAISES1"),
            FilingInput(rcept_no="UNCLASSIFIED1"),
            FilingInput(rcept_no="NOCONSOL1"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_extract):
                manifest = await bulk_extract_audit_documents(
                    filings=filings,
                    output_dir=output_dir,
                    require_consolidated=True,
                    sleep_seconds=0,
                    generated_at="2026-07-09T00:00:00+00:00",
                    source="test",
                )

            # RAISES1 다음에 오는 필링들도 모두 호출되어야 한다(중단 없이 계속).
            self.assertEqual(calls, ["SUCCESS1", "RAISES1", "UNCLASSIFIED1", "NOCONSOL1"])

            statuses = {result.rcept_no: result.status for result in manifest.results}
            self.assertEqual(
                statuses,
                {
                    "SUCCESS1": "succeeded",
                    "RAISES1": "failed",
                    "UNCLASSIFIED1": "failed",
                    "NOCONSOL1": "skipped_no_consolidated",
                },
            )
            self.assertEqual(manifest.total, 4)
            self.assertEqual(
                manifest.counts_by_status,
                {"succeeded": 1, "failed": 2, "skipped_no_consolidated": 1},
            )

            # 실패/예외 메시지가 매니페스트에 기록되되, 비어있지 않아야 한다.
            raises_result = next(r for r in manifest.results if r.rcept_no == "RAISES1")
            self.assertTrue(raises_result.message)
            self.assertIsNone(raises_result.output_path)

            success_result = next(r for r in manifest.results if r.rcept_no == "SUCCESS1")
            self.assertTrue(success_result.output_path)
            self.assertTrue(os.path.isdir(success_result.output_path))

            # bulk-manifest.json이 실제로 쓰여졌고 JSON으로 라운드트립되어야 한다.
            manifest_path = os.path.join(output_dir, "bulk-manifest.json")
            self.assertTrue(os.path.isfile(manifest_path))
            with open(manifest_path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["total"], 4)
            self.assertEqual(len(payload["results"]), 4)


_RCEPT_NO = "20260323001689"
_AUDIT_ONLY_ZIP = _build_zip({f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서")})
_CONSOLIDATED_ONLY_ZIP = _build_zip({f"{_RCEPT_NO}_00761.xml": _document_xml("00761", "연결감사보고서")})


class KindClassificationTests(unittest.IsolatedAsyncioTestCase):
    """`extract_audit_documents_core`를 실제 코드 그대로 태우고(`_fetch_dart_binary`만
    모킹) bulk 상태가 `AuditDocsError.kind`/성공 outcome의
    `audit_found`/`consolidated_found`로부터 나온다는 것을 확인한다 - 이
    `audit_docs.py`의 오류 메시지 문구를 리워딩해도 이 분류는 바뀌지 않는다."""

    async def test_audit_only_zip_with_include_audit_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = _AUDIT_ONLY_ZIP
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    include="audit",
                    sleep_seconds=0,
                )

        self.assertEqual(manifest.results[0].status, "succeeded")
        self.assertEqual(manifest.counts_by_status, {"succeeded": 1})

    async def test_zip_without_audit_entry_and_include_audit_is_skipped_no_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = _CONSOLIDATED_ONLY_ZIP
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    include="audit",
                    sleep_seconds=0,
                )

        self.assertEqual(manifest.results[0].status, "skipped_no_audit")
        self.assertEqual(manifest.counts_by_status, {"skipped_no_audit": 1})

    async def test_fetch_error_string_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = "오류: 요청 시간이 초과되었습니다."
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    sleep_seconds=0,
                )

        self.assertEqual(manifest.results[0].status, "failed")
        self.assertIn("오류", manifest.results[0].message or "")

    async def test_corrupt_zip_bytes_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = b"this is definitely not a zip file"
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    sleep_seconds=0,
                )

        self.assertEqual(manifest.results[0].status, "failed")

    async def test_require_consolidated_missing_is_skipped_no_consolidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = _AUDIT_ONLY_ZIP
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    require_consolidated=True,
                    sleep_seconds=0,
                )

        self.assertEqual(manifest.results[0].status, "skipped_no_consolidated")

    async def test_raised_exception_from_fetch_is_failed_and_run_continues(self) -> None:
        """`_fetch_dart_binary`가 (오류 문자열이 아니라) 실제로 예외를 던지는
        경우도 `_process_filing`이 잡아 `failed`로 격리하고, 다른 필링 처리를
        막지 않아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch(
                "dart_search_mcp.tools.audit_docs._fetch_dart_binary", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.side_effect = [RuntimeError("네트워크 오류 (시뮬레이션)"), _AUDIT_ONLY_ZIP]
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no="RAISER1"), FilingInput(rcept_no=_RCEPT_NO)],
                    output_dir=output_dir,
                    include="audit",
                    sleep_seconds=0,
                )

        statuses = {r.rcept_no: r.status for r in manifest.results}
        self.assertEqual(statuses, {"RAISER1": "failed", _RCEPT_NO: "succeeded"})


class RunParamsGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_with_different_params_raises_value_error(self) -> None:
        """같은 체크포인트를 다른 필링 목록/옵션으로 재사용하려 하면(예:
        include를 바꿔서 --resume) 이전 실행의 진행 상황과 이번 실행이
        뒤섞이지 않도록 명확한 오류를 내야 한다(Minor 리뷰 지적)."""

        async def fake_extract(*, rcept_no, output_dir, include, require_consolidated):
            return _fake_success(rcept_no, output_dir)

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            checkpoint_path = Path(tmp) / "bulk-manifest.json.checkpoint.json"

            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_extract):
                await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no="A1")],
                    output_dir=output_dir,
                    include="both",
                    checkpoint=checkpoint_path,
                    sleep_seconds=0,
                )

                with self.assertRaises(ValueError):
                    await bulk_extract_audit_documents(
                        filings=[FilingInput(rcept_no="A1")],
                        output_dir=output_dir,
                        include="audit",
                        checkpoint=checkpoint_path,
                        sleep_seconds=0,
                    )

                with self.assertRaises(ValueError):
                    await bulk_extract_audit_documents(
                        filings=[FilingInput(rcept_no="A1"), FilingInput(rcept_no="B1")],
                        output_dir=output_dir,
                        include="both",
                        checkpoint=checkpoint_path,
                        sleep_seconds=0,
                    )


class RedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_secret_in_raised_exception_message_is_redacted_in_manifest(self) -> None:
        """필링 처리 중 raise된 예외 메시지에 API 키가 들어있어도(예: httpx가
        요청 URL을 그대로 예외 메시지에 담는 경우), 매니페스트에 기록되기
        전에 리댁션되어야 한다."""
        secret = "totally-secret-crtfc-key-value"

        async def fake_extract(*, rcept_no, output_dir, include, require_consolidated):
            raise RuntimeError(f"다운로드 실패: https://opendart.fss.or.kr/api/document.xml?crtfc_key={secret}")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_extract):
                manifest = await bulk_extract_audit_documents(
                    filings=[FilingInput(rcept_no="SECRET1")],
                    output_dir=output_dir,
                    sleep_seconds=0,
                )

            result = manifest.results[0]
            self.assertEqual(result.status, "failed")
            self.assertIsNotNone(result.message)
            assert result.message is not None
            self.assertNotIn(secret, result.message)
            self.assertIn("crtfc_key=<redacted>", result.message)

            manifest_path = os.path.join(output_dir, "bulk-manifest.json")
            with open(manifest_path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_caps_processed_filings(self) -> None:
        calls: list[str] = []

        async def fake_extract(*, rcept_no, output_dir, include, require_consolidated):
            calls.append(rcept_no)
            return _fake_success(rcept_no, output_dir)

        filings = [FilingInput(rcept_no=f"R{i}") for i in range(5)]

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_extract):
                manifest = await bulk_extract_audit_documents(
                    filings=filings, output_dir=output_dir, limit=2, sleep_seconds=0
                )

        self.assertEqual(calls, ["R0", "R1"])
        self.assertEqual(manifest.total, 2)
        self.assertEqual(manifest.counts_by_status, {"succeeded": 2})


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_skips_succeeded_and_retries_failed(self) -> None:
        calls: list[str] = []

        async def fake_first(*, rcept_no, output_dir, include, require_consolidated):
            calls.append(rcept_no)
            if rcept_no == "S1":
                return _fake_success(rcept_no, output_dir)
            if rcept_no == "F1":
                raise RuntimeError("일시적 오류(시뮬레이션)")
            raise AssertionError(rcept_no)

        filings = [FilingInput(rcept_no="S1"), FilingInput(rcept_no="F1")]

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            checkpoint_path = Path(tmp) / "bulk-manifest.json.checkpoint.json"

            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_first):
                first_manifest = await bulk_extract_audit_documents(
                    filings=filings,
                    output_dir=output_dir,
                    checkpoint=checkpoint_path,
                    sleep_seconds=0,
                )

            self.assertEqual(calls, ["S1", "F1"])
            self.assertEqual(
                {r.rcept_no: r.status for r in first_manifest.results},
                {"S1": "succeeded", "F1": "failed"},
            )
            self.assertTrue(checkpoint_path.exists())

            calls_before_resume = list(calls)

            async def fake_second(*, rcept_no, output_dir, include, require_consolidated):
                calls.append(rcept_no)
                self.assertNotEqual(rcept_no, "S1", "이미 성공한 필링을 재처리하면 안 된다")
                return _fake_success(rcept_no, output_dir)

            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_second):
                second_manifest = await bulk_extract_audit_documents(
                    filings=filings,
                    output_dir=output_dir,
                    checkpoint=checkpoint_path,
                    sleep_seconds=0,
                )

            # S1(성공)은 재호출되지 않고, F1(실패)만 재시도되어야 한다.
            self.assertEqual(calls, calls_before_resume + ["F1"])
            self.assertEqual(
                {r.rcept_no: r.status for r in second_manifest.results},
                {"S1": "succeeded", "F1": "succeeded"},
            )
            self.assertEqual(second_manifest.counts_by_status, {"succeeded": 2})


class SourceLoadingTests(unittest.TestCase):
    def _write_manifest(self, tmp: str, records: list[dict]) -> str:
        path = os.path.join(tmp, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"records": records}, f, ensure_ascii=False)
        return path

    def test_load_filings_from_manifest_reads_rcept_no_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_manifest(
                tmp,
                [
                    {
                        "category": "감사보고서",
                        "report_name": "감사보고서",
                        "rcept_no": "1",
                        "corp_code": "00126380",
                        "corp_name": "삼성전자",
                    },
                    {
                        "category": "감사보고서",
                        "report_name": "감사보고서[기재정정]",
                        "rcept_no": "2",
                        "corp_code": "00164779",
                        "corp_name": "테스트기업",
                    },
                ],
            )
            filings = load_filings_from_manifest(path)

        self.assertEqual([f.rcept_no for f in filings], ["1", "2"])
        self.assertEqual(filings[0].corp_name, "삼성전자")

    def test_load_filings_from_manifest_exclude_corrections_filters_marked_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_manifest(
                tmp,
                [
                    {"report_name": "감사보고서", "rcept_no": "1"},
                    {"report_name": "감사보고서[기재정정]", "rcept_no": "2"},
                ],
            )
            filings = load_filings_from_manifest(path, exclude_corrections=True)

        self.assertEqual([f.rcept_no for f in filings], ["1"])

    def test_load_filings_from_manifest_missing_records_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"not_records": []}, f)

            with self.assertRaises(BulkAuditSourceError):
                load_filings_from_manifest(path)

    def test_load_filings_from_rcept_json_reads_plain_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rcepts.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(["1", "2", "3"], f)

            filings = load_filings_from_rcept_json(path)

        self.assertEqual([f.rcept_no for f in filings], ["1", "2", "3"])

    def test_load_filings_from_rcept_json_non_array_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"not": "an array"}, f)

            with self.assertRaises(BulkAuditSourceError):
                load_filings_from_rcept_json(path)


class CliSurfaceTests(unittest.TestCase):
    def test_bulk_audit_documents_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["bulk-audit-documents", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--manifest", result.output)
        self.assertIn("--rcept-json", result.output)
        self.assertIn("--resume", result.output)
        self.assertIn("--limit", result.output)

    def test_cli_requires_exactly_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            runner = CliRunner()

            neither = runner.invoke(cli.cli, ["bulk-audit-documents", "-o", output_dir])
            self.assertNotEqual(neither.exit_code, 0)

            rcept_json_path = os.path.join(tmp, "rcepts.json")
            with open(rcept_json_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            manifest_path = os.path.join(tmp, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"records": []}, f)

            both = runner.invoke(
                cli.cli,
                [
                    "bulk-audit-documents",
                    "--manifest",
                    manifest_path,
                    "--rcept-json",
                    rcept_json_path,
                    "-o",
                    output_dir,
                ],
            )
            self.assertNotEqual(both.exit_code, 0)

    def test_cli_end_to_end_with_rcept_json_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rcept_json_path = os.path.join(tmp, "rcepts.json")
            with open(rcept_json_path, "w", encoding="utf-8") as f:
                json.dump(["A1"], f)
            output_dir = os.path.join(tmp, "out")

            async def fake_extract(*, rcept_no, output_dir, include, require_consolidated):
                return _fake_success(rcept_no, output_dir)

            with patch("dart_search_mcp.tools.bulk_audit.extract_audit_documents_core", fake_extract):
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    ["bulk-audit-documents", "--rcept-json", rcept_json_path, "-o", output_dir, "--sleep-seconds", "0"],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.isfile(os.path.join(output_dir, "bulk-manifest.json")))


if __name__ == "__main__":
    unittest.main()
