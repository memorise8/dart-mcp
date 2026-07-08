import unittest
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import httpx

from dart_search_mcp import client
from dart_search_mcp.results import DartError, DartNoData, DartResult, DartSuccess, NO_DATA_MESSAGE

# `dart_search_mcp.client`가 `dart_search_mcp.redact`를 import하는 부수 효과로
# httpx 요청 로깅(크리덴셜 쿼리 파라미터가 담긴 INFO 레벨 "HTTP Request: ..." 로그)이
# 이미 WARNING 레벨로 억제되어 있다. 별도의 테스트 전용 워크어라운드는 필요 없다.

_RealAsyncClient = httpx.AsyncClient

# 테스트에서 실제 .env의 DART_API_KEY가 절대 사용/로깅되지 않도록 더미 값으로 치환한다.
_DUMMY_API_KEY = "test-dummy-crtfc-key"


def _patched_async_client(transport: httpx.MockTransport):
    """dart_search_mcp.client.httpx.AsyncClient에 MockTransport를 주입하는 팩토리.

    `httpx.AsyncClient` 자체를 패치하므로, 패치된 이름을 다시 참조하지 않도록
    패치 전에 저장해둔 실제 클래스(`_RealAsyncClient`)를 사용한다.
    """

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_dart_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """DART 호출을 MockTransport로 가로채고, 실제 크리덴셜 대신 더미 키를 사용하게 한다."""
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_API_KEY):
        yield


class StructuredResultDataclassTests(unittest.TestCase):
    def test_dart_success_holds_response_data(self) -> None:
        result: DartResult = DartSuccess(data={"status": "000", "corp_name": "삼성전자"})

        self.assertIsInstance(result, DartSuccess)
        self.assertEqual(result.data["corp_name"], "삼성전자")

    def test_dart_no_data_has_default_korean_message(self) -> None:
        result: DartResult = DartNoData()

        self.assertEqual(result.message, NO_DATA_MESSAGE)
        self.assertEqual(result, DartNoData())

    def test_dart_error_holds_message_and_optional_status(self) -> None:
        result: DartResult = DartError(message="오류: 필드 누락 (status: 020)", status="020")

        self.assertEqual(result.status, "020")
        self.assertIn("020", result.message)

    def test_dart_error_status_defaults_to_empty_for_network_failures(self) -> None:
        result = DartError(message="오류: 네트워크 오류가 발생했습니다. boom")

        self.assertEqual(result.status, "")

    def test_results_are_frozen(self) -> None:
        result = DartSuccess(data={})

        with self.assertRaises(AttributeError):
            result.data = {"x": 1}  # type: ignore[misc]


class FetchDartResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_success_on_status_000(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "000", "message": "정상", "corp_name": "삼성전자"})

        with mocked_dart_transport(handler):
            result = await client._fetch_dart_result("company.json", {"corp_code": "00126380"})

        self.assertIsInstance(result, DartSuccess)
        assert isinstance(result, DartSuccess)
        self.assertEqual(result.data["corp_name"], "삼성전자")

    async def test_returns_no_data_on_status_013(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

        with mocked_dart_transport(handler):
            result = await client._fetch_dart_result("list.json", {})

        self.assertEqual(result, DartNoData())

    async def test_returns_typed_error_on_non_000_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})

        with mocked_dart_transport(handler):
            result = await client._fetch_dart_result("list.json", {"crtfc_key": "should-not-leak"})

        self.assertIsInstance(result, DartError)
        assert isinstance(result, DartError)
        self.assertEqual(result.status, "020")
        self.assertEqual(result.message, "오류: 요청 제한을 초과하였습니다. (status: 020)")
        self.assertNotIn("should-not-leak", result.message)
        self.assertNotIn(_DUMMY_API_KEY, result.message)

    async def test_returns_typed_error_on_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        with mocked_dart_transport(handler):
            result = await client._fetch_dart_result("list.json", {})

        self.assertIsInstance(result, DartError)
        assert isinstance(result, DartError)
        self.assertEqual(result.status, "")
        self.assertEqual(result.message, "오류: 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.")

    async def test_legacy_fetch_dart_still_returns_plain_string_for_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})

        with mocked_dart_transport(handler):
            data = await client._fetch_dart("list.json", {})

        self.assertEqual(data, "오류: 요청 제한을 초과하였습니다. (status: 020)")

    async def test_legacy_fetch_dart_still_returns_message_for_no_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

        with mocked_dart_transport(handler):
            data = await client._fetch_dart("list.json", {})

        self.assertEqual(data, NO_DATA_MESSAGE)

    async def test_legacy_fetch_dart_still_returns_dict_for_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "000", "message": "정상", "corp_name": "삼성전자"})

        with mocked_dart_transport(handler):
            data = await client._fetch_dart("company.json", {"corp_code": "00126380"})

        self.assertIsInstance(data, dict)
        assert isinstance(data, dict)
        self.assertEqual(data["corp_name"], "삼성전자")

    async def test_unexpected_error_message_containing_request_url_is_redacted(self) -> None:
        """QA 실패 시나리오: 요청 URL(crtfc_key 포함)을 담은 예기치 못한 예외가 발생해도
        DartError 메시지에는 `crtfc_key=<redacted>`만 남고 실제 키 값은 노출되지 않는다."""
        leaking_url = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={_DUMMY_API_KEY}&corp_code=00126380"

        def handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError(f"connection reset while calling {leaking_url}")

        with mocked_dart_transport(handler):
            result = await client._fetch_dart_result("list.json", {})

        self.assertIsInstance(result, DartError)
        assert isinstance(result, DartError)
        self.assertIn("crtfc_key=<redacted>", result.message)
        self.assertNotIn(_DUMMY_API_KEY, result.message)


class PublicFormatterUnaffectedByStructuredErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_company_info_still_emits_existing_korean_error_style(self) -> None:
        """QA 실패 시나리오: 000이 아닌 status를 모킹하면 내부적으로는 DartError가 반환되지만,
        공개 MCP 도구(server.get_company_info)의 출력 문자열은 리팩터링 이전과 동일해야 한다."""
        import server

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})

        with mocked_dart_transport(handler):
            internal_result = await client._fetch_dart_result("company.json", {"corp_code": "00126380"})
            output = await server.get_company_info("00126380")

        self.assertIsInstance(internal_result, DartError)
        self.assertEqual(output, "오류: 요청 제한을 초과하였습니다. (status: 020)")


if __name__ == "__main__":
    unittest.main()
