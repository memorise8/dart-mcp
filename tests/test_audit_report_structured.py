"""
`dart_search_mcp.tools.reports.get_audit_report_structured`에 대한 테스트.

Task 5: 정기보고서 유형 `회계감사인`(accnutAdtorNmNdAdtOpinion)에 대한 구조화된
감사보고서 사실(fact) 추출.

- DART가 동일한 rcept_no/사업연도/보고서코드 조합에 대해 완전히 동일한 행을
  중복 반환하는 사례가 실제 운영 데이터에서 관찰되어, 안정적인 키
  (corp_code, bsns_year, reprt_code, rcept_no) 기준으로 중복을 제거한다.
- corp_code/bsns_year 누락 등 구조화 계층의 입력 검증 실패는 문자열이 아니라
  타입이 있는 `AuditReportError`로 반환된다.
- 기존 공개 문자열 도구 `get_periodic_report`는 이 작업으로 영향받지 않으며,
  잘못된 report_type에 대해 기존 문자열 오류 메시지를 그대로 반환한다.

실제 OpenDART로는 어떤 요청도 나가지 않는다 (`httpx.MockTransport`).
"""

import unittest
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import httpx

# `server`를 먼저 import해서 `dart_search_mcp.tools.*` 모듈들의 `@mcp.tool()`
# 등록이 tests/test_public_surface.py가 기대하는 정식 순서로 이루어지도록 한다.
import server  # noqa: F401
from dart_search_mcp.tools.reports import (
    AuditReportError,
    AuditReportRecord,
    AuditReportResult,
    get_audit_report_structured,
    get_periodic_report,
)

_DUMMY_API_KEY = "test-dummy-crtfc-key-audit-report"

_RealAsyncClient = httpx.AsyncClient


def _patched_async_client(transport: httpx.MockTransport):
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_dart_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_API_KEY):
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
        "adt_reprt_spcmnt_matter": "해당사항 없음",
        "emphs_matter": "",
        "core_adt_matter": "재고자산 평가",
        "stlm_dt": "2023-12-31",
    }


_DUPLICATE_AUDIT_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [
        _samsung_audit_row(),
        _samsung_audit_row(),
        _samsung_audit_row(),
    ],
}


class AuditReportStructuredDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_rows_collapse_to_one_source_fact(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("accnutAdtorNmNdAdtOpinion.json", str(request.url))
            return httpx.Response(200, json=_DUPLICATE_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="2023")

        self.assertIsInstance(outcome, AuditReportResult)
        assert isinstance(outcome, AuditReportResult)
        self.assertEqual(len(outcome.records), 1)

        record = outcome.records[0]
        self.assertIsInstance(record, AuditReportRecord)
        self.assertEqual(record.corp_code, "00126380")
        self.assertEqual(record.corp_name, "삼성전자")
        self.assertEqual(record.corp_cls, "Y")
        self.assertEqual(record.bsns_year, "2023")
        self.assertEqual(record.reprt_code, "11011")
        self.assertEqual(record.rcept_no, "20240312000001")
        self.assertEqual(record.auditor, "삼정회계법인")
        self.assertEqual(record.audit_opinion, "적정")
        self.assertEqual(record.special_matter, "해당사항 없음")
        self.assertEqual(record.emphasis_matter, "")
        self.assertEqual(record.core_audit_matter, "재고자산 평가")
        self.assertEqual(record.settlement_date, "2023-12-31")
        self.assertEqual(record.business_year_label, "2023년 사업보고서")
        self.assertEqual(record.source_url, "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240312000001")

    async def test_same_key_different_content_are_both_preserved(self) -> None:
        """동일한 (corp_code, bsns_year, reprt_code, rcept_no) 조합이라도 내용
        필드(예: 감사의견, 감사인)가 다르면 서로 다른 사실(fact)이므로 둘 다
        보존되어야 한다 (예: 연결/별도 재무제표를 하나의 rcept_no 아래 함께
        보고하는 경우). 4개 필드만 보는 옛 키는 이를 하나로 뭉개버렸다."""

        consolidated_row = _samsung_audit_row()
        separate_row = {
            **_samsung_audit_row(),
            "adtor": "한영회계법인",
            "adt_opinion": "한정",
        }
        response = {
            "status": "000",
            "message": "정상",
            "list": [consolidated_row, separate_row],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="2023")

        self.assertIsInstance(outcome, AuditReportResult)
        assert isinstance(outcome, AuditReportResult)
        self.assertEqual(len(outcome.records), 2)
        self.assertEqual(
            {r.auditor for r in outcome.records},
            {"삼정회계법인", "한영회계법인"},
        )
        self.assertEqual(
            {r.audit_opinion for r in outcome.records},
            {"적정", "한정"},
        )

    async def test_distinct_rcept_no_are_not_collapsed(self) -> None:
        response = {
            "status": "000",
            "message": "정상",
            "list": [
                _samsung_audit_row(rcept_no="20240312000001"),
                _samsung_audit_row(rcept_no="20240312000002"),
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="2023")

        self.assertIsInstance(outcome, AuditReportResult)
        assert isinstance(outcome, AuditReportResult)
        self.assertEqual(len(outcome.records), 2)
        self.assertEqual(
            {r.rcept_no for r in outcome.records},
            {"20240312000001", "20240312000002"},
        )


class AuditReportStructuredValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_corp_code_returns_typed_validation_error_without_network_call(self) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json=_DUPLICATE_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="", bsns_year="2023")

        self.assertFalse(called, "corp_code 누락 시 DART API를 호출해서는 안 된다")
        self.assertIsInstance(outcome, AuditReportError)
        assert isinstance(outcome, AuditReportError)
        self.assertIn("고유번호", outcome.message)

    async def test_missing_bsns_year_returns_typed_validation_error_without_network_call(self) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json=_DUPLICATE_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="")

        self.assertFalse(called, "bsns_year 누락 시 DART API를 호출해서는 안 된다")
        self.assertIsInstance(outcome, AuditReportError)
        assert isinstance(outcome, AuditReportError)
        self.assertIn("사업연도", outcome.message)


class AuditReportNoDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_013_returns_empty_records_with_no_data_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="2023")

        self.assertIsInstance(outcome, AuditReportResult)
        assert isinstance(outcome, AuditReportResult)
        self.assertEqual(outcome.records, [])
        self.assertIsNotNone(outcome.no_data_message)


class AuditReportFetchErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_dart_error_status_returns_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})

        with mocked_dart_transport(handler):
            outcome = await get_audit_report_structured(corp_code="00126380", bsns_year="2023")

        self.assertIsInstance(outcome, AuditReportError)
        assert isinstance(outcome, AuditReportError)
        self.assertIn("020", outcome.message)


class GetPeriodicReportUnaffectedTests(unittest.IsolatedAsyncioTestCase):
    """`get_periodic_report`(기존 공개 문자열 도구)는 이 작업으로 영향받지 않는다."""

    async def test_invalid_report_type_still_returns_existing_public_error_text(self) -> None:
        output = await get_periodic_report("00126380", "2023", report_type="존재하지않는유형")

        self.assertIn('오류: 유효하지 않은 report_type입니다: "존재하지않는유형"', output)
        self.assertIn("회계감사인", output)  # 사용 가능한 목록에 여전히 포함되어 있어야 함

    async def test_string_tool_still_returns_duplicated_rows_for_audit_report_type(self) -> None:
        """`get_periodic_report`는 여전히 얇은 래퍼로 남아 구조화 계층의 중복
        제거 로직을 공유하지 않는다 (기존 동작 보존, 회귀 방지)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_DUPLICATE_AUDIT_RESPONSE)

        with mocked_dart_transport(handler):
            output = await get_periodic_report("00126380", "2023", report_type="회계감사인")

        self.assertEqual(output.count("삼정회계법인"), 3)


if __name__ == "__main__":
    unittest.main()
