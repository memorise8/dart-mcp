"""
`dart_search_mcp.corp.search_corp_code`에 대한 리댁션 회귀 테스트.

`corp.py`의 `_load_corp_codes()`는 `client.py`와 완전히 독립적으로 자체
`httpx.AsyncClient`를 사용해 OpenDART `corpCode.xml`을 다운로드한다.
Task 2 리댁션 수정이 `client.py`의 두 호출부에만 적용되고 이 경로는 놓쳤던
Critical 취약점(비-2xx 응답 시 `crtfc_key` 평문 노출)에 대한 회귀 테스트다.
"""

import unittest
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import httpx

# `server`를 먼저 import해서 `dart_search_mcp.tools.*` -> `dart_search_mcp.corp` 순으로
# `@mcp.tool()` 등록이 이루어지는 정식 순서를 보존한다. 이 모듈이 `dart_search_mcp.corp`를
# `server`보다 먼저 import해버리면 `search_corp_code`가 다른 도구들보다 먼저 등록되어
# `tests/test_public_surface.py`의 도구 목록 순서 검증이 (테스트 실행 순서에 따라
# 우연히) 깨질 수 있다.
import server  # noqa: F401
from dart_search_mcp import corp
from dart_search_mcp.redact import REDACTED

_DUMMY_API_KEY = "test-dummy-crtfc-key-corp"

_RealAsyncClient = httpx.AsyncClient


def _patched_async_client(transport: httpx.MockTransport):
    """corp.py의 `httpx.AsyncClient`에 MockTransport를 주입하는 팩토리.

    `httpx.AsyncClient` 자체를 패치하므로, 패치된 이름을 다시 참조하지 않도록
    패치 전에 저장해둔 실제 클래스(`_RealAsyncClient`)를 사용한다.
    """

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_corp_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """corp.py 전용 OpenDART 호출을 MockTransport로 가로채고, 실제 크리덴셜
    대신 더미 키를 사용하게 한다. 실제 OpenDART로는 어떤 요청도 나가지 않는다."""
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.corp.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.corp.API_KEY", _DUMMY_API_KEY):
        yield


class SearchCorpCodeRedactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 다른 테스트나 이전 실행에서 모듈 수준 캐시가 채워져 있으면 네트워크
        # 호출 자체가 발생하지 않아 이 테스트가 무의미해지므로 매번 초기화한다.
        corp._corp_code_cache = None

    def tearDown(self) -> None:
        corp._corp_code_cache = None

    async def test_non_2xx_response_error_is_redacted(self) -> None:
        """QA 실패 시나리오(리뷰어 재현): OpenDART corpCode.xml 호출이 403을
        반환하면 httpx의 raise_for_status()가 crtfc_key를 포함한 전체 요청
        URL을 예외 메시지에 담는다. search_corp_code가 반환하는 문자열에는
        실제 키 값이 절대 노출되지 않고 `<redacted>`만 남아야 한다."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden")

        with mocked_corp_transport(handler):
            result = await corp.search_corp_code("삼성전자")

        self.assertNotIn(_DUMMY_API_KEY, result)
        self.assertIn(REDACTED, result)

    async def test_request_error_message_is_redacted(self) -> None:
        """네트워크 예외(RequestError 등)에도 동일하게 리댁션이 적용되는지
        확인한다. 예외 메시지 자체에 URL(및 키)을 직접 담아 재현한다."""
        leaking_url = (
            f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={_DUMMY_API_KEY}"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError(f"connection reset while calling {leaking_url}")

        with mocked_corp_transport(handler):
            result = await corp.search_corp_code("삼성전자")

        self.assertNotIn(_DUMMY_API_KEY, result)
        self.assertIn(REDACTED, result)


if __name__ == "__main__":
    unittest.main()
