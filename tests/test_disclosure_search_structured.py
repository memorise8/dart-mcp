"""
`dart_search_mcp.tools.disclosures.search_disclosures_structured`에 대한 테스트.

Task 4: 공시 검색을 corp_code 우선/구조화된 방식으로 바꾼다.

- `corp_code`가 주어지면 그대로 `list.json`을 호출한다.
- `corp_name`만 주어지면 `resolve_corp_code`로 먼저 해석한 뒤, 해석된
  `corp_code`로만 `list.json`을 호출한다. `corp_name`은 절대
  OpenDART `list.json` 요청 파라미터로 전송하지 않는다.
- 회사명이 여러 후보와 매치되어 모호하면(exact 매치가 없고 prefix/contains
  후보가 2개 이상) `list.json`을 전혀 호출하지 않고 결정적인 "선택하세요"
  오류를 반환한다.

corpCode.xml(corp.py)과 list.json(client.py) 호출 모두 같은 `httpx.AsyncClient`
객체를 참조하므로(`import httpx`), 두 호출을 함께 모킹해야 하는 테스트는 URL로
라우팅하는 단일 `httpx.MockTransport`(`mocked_corp_and_list_transport`)를 쓴다.
실제 OpenDART로는 어떤 요청도 나가지 않는다.
"""

import io
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import httpx

# `server`를 먼저 import해서 `dart_search_mcp.tools.*` -> `dart_search_mcp.corp` 순으로
# `@mcp.tool()` 등록이 이루어지는 정식 순서를 보존한다 (tests/test_public_surface.py의
# 도구 목록 순서 검증이 깨지지 않도록).
import server  # noqa: F401
from dart_search_mcp.tools.disclosures import (
    DisclosureAmbiguousCompanyError,
    DisclosureSearchError,
    DisclosureSearchResult,
    search_disclosures,
    search_disclosures_structured,
)

_DUMMY_CLIENT_API_KEY = "test-dummy-crtfc-key-disclosures-client"
_DUMMY_CORP_API_KEY = "test-dummy-crtfc-key-disclosures-corp"

_RealAsyncClient = httpx.AsyncClient


def _patched_async_client(transport: httpx.MockTransport):
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_list_json_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """`list.json` 호출(client.py)을 MockTransport로 가로챈다.

    주의: `dart_search_mcp.corp`와 `dart_search_mcp.client`는 둘 다 같은
    `httpx` 모듈 객체를 참조하므로(`import httpx`), `dart_search_mcp.corp.httpx.AsyncClient`와
    `dart_search_mcp.client.httpx.AsyncClient`를 각각 다른 transport로 동시에
    패치하면 나중에 적용된 패치가 먼저 것을 덮어써 둘 다 같은 transport를
    쓰게 된다. corp_code 해석(corpCode.xml)과 list.json 호출을 한 테스트
    안에서 함께 모킹해야 하면 `mocked_corp_and_list_transport`를 사용한다.
    """
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_CLIENT_API_KEY):
        yield


@contextmanager
def mocked_corp_and_list_transport(
    corp_handler: Callable[[httpx.Request], httpx.Response],
    list_handler: Callable[[httpx.Request], httpx.Response],
) -> Iterator[None]:
    """`corpCode.xml`(corp.py)과 `list.json`(client.py) 호출을 하나의
    MockTransport로 가로채, 요청 URL에 따라 각각 다른 핸들러로 라우팅한다.

    `dart_search_mcp.corp.httpx.AsyncClient`와 `dart_search_mcp.client.httpx.AsyncClient`는
    같은 `httpx.AsyncClient` 객체를 가리키므로 반드시 같은 transport 인스턴스로
    함께 패치해야 한다(둘을 다른 transport로 따로 패치하면 나중 패치가 이긴다).
    """

    def dispatch(request: httpx.Request) -> httpx.Response:
        if "corpCode.xml" in str(request.url):
            return corp_handler(request)
        return list_handler(request)

    transport = httpx.MockTransport(dispatch)
    with patch("dart_search_mcp.corp.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.client.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.corp.API_KEY", _DUMMY_CORP_API_KEY), \
            patch("dart_search_mcp.client.API_KEY", _DUMMY_CLIENT_API_KEY):
        yield


def _build_corp_code_zip(records: list[dict[str, str]]) -> bytes:
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

_LIST_JSON_RESPONSE = {
    "status": "000",
    "message": "정상",
    "page_no": "1",
    "page_count": "20",
    "total_count": "1",
    "total_page": "1",
    "list": [
        {
            "corp_cls": "Y",
            "corp_name": "삼성전자",
            "corp_code": "00126380",
            "stock_code": "005930",
            "report_nm": "사업보고서",
            "rcept_no": "20240101000123",
            "flr_nm": "삼성전자",
            "rcept_dt": "20240101",
            "rm": "",
        }
    ],
}


class SearchDisclosuresStructuredByCorpCodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_corp_code_is_used_and_corp_name_is_never_sent_to_list_json(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_list_json_transport(handler):
            result = await search_disclosures_structured(corp_code="00126380")

        self.assertEqual(len(captured), 1)
        query = captured[0].url.params
        self.assertEqual(query.get("corp_code"), "00126380")
        self.assertNotIn("corp_name", query)

        self.assertIsInstance(result, DisclosureSearchResult)
        assert isinstance(result, DisclosureSearchResult)
        self.assertEqual(result.total_count, 1)
        self.assertEqual(len(result.records), 1)

        record = result.records[0]
        self.assertEqual(record.report_name, "사업보고서")
        self.assertEqual(record.rcept_no, "20240101000123")
        self.assertEqual(record.rcept_dt, "20240101")
        self.assertEqual(record.corp_code, "00126380")
        self.assertEqual(record.corp_name, "삼성전자")
        self.assertEqual(record.stock_code, "005930")
        self.assertEqual(record.filing_type, "Y")
        self.assertEqual(record.source_url, "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240101000123")

    async def test_public_string_tool_formats_corp_code_search(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_list_json_transport(handler):
            output = await search_disclosures(corp_code="00126380")

        self.assertIn("사업보고서", output)
        self.assertIn("20240101000123", output)
        self.assertIn("00126380", output)
        self.assertIn("삼성전자", output)


class SearchDisclosuresStructuredByCorpNameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from dart_search_mcp import corp

        corp._corp_code_cache = None

    def tearDown(self) -> None:
        from dart_search_mcp import corp

        corp._corp_code_cache = None

    async def test_unique_exact_name_match_resolves_then_calls_list_json_with_corp_code_only(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)
        captured: list[httpx.Request] = []

        def corp_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        def list_handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_corp_and_list_transport(corp_handler, list_handler):
            result = await search_disclosures_structured(corp_name="삼성전자")

        self.assertEqual(len(captured), 1)
        query = captured[0].url.params
        self.assertEqual(query.get("corp_code"), "00126380")
        self.assertNotIn("corp_name", query)

        self.assertIsInstance(result, DisclosureSearchResult)

    async def test_ambiguous_name_returns_deterministic_error_without_calling_list_json(self) -> None:
        """"삼성"은 exact 매치가 없고 prefix 후보가 3개(삼성전자/삼성물산/삼성SDI)이므로
        모호하다. 어떤 회사를 조회할지 결정적으로 사용자에게 물어야 하며, 전체 회사를
        순회 조회해서는 안 된다 (list.json이 전혀 호출되지 않아야 한다)."""
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)
        list_json_called = False

        def corp_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        def list_handler(request: httpx.Request) -> httpx.Response:
            nonlocal list_json_called
            list_json_called = True
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_corp_and_list_transport(corp_handler, list_handler):
            result = await search_disclosures_structured(corp_name="삼성")

        self.assertFalse(list_json_called, "ambiguous company name must not trigger a list.json call")
        self.assertIsInstance(result, DisclosureAmbiguousCompanyError)
        assert isinstance(result, DisclosureAmbiguousCompanyError)
        self.assertIn("삼성전자", result.message)
        self.assertIn("삼성물산", result.message)
        self.assertIn("삼성SDI", result.message)
        self.assertEqual({c.corp_name for c in result.candidates}, {"삼성전자", "삼성물산", "삼성SDI"})

    async def test_public_string_tool_returns_deterministic_ambiguous_error(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)
        list_json_called = False

        def corp_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        def list_handler(request: httpx.Request) -> httpx.Response:
            nonlocal list_json_called
            list_json_called = True
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_corp_and_list_transport(corp_handler, list_handler):
            output = await search_disclosures(corp_name="삼성")

        self.assertFalse(list_json_called)
        self.assertIsInstance(output, str)
        self.assertIn("삼성전자", output)
        self.assertIn("삼성물산", output)

    async def test_unresolvable_name_returns_error_without_calling_list_json(self) -> None:
        zip_bytes = _build_corp_code_zip(_CORP_FIXTURE)
        list_json_called = False

        def corp_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        def list_handler(request: httpx.Request) -> httpx.Response:
            nonlocal list_json_called
            list_json_called = True
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_corp_and_list_transport(corp_handler, list_handler):
            result = await search_disclosures_structured(corp_name="존재하지않는회사이름")

        self.assertFalse(list_json_called)
        self.assertIsInstance(result, DisclosureSearchError)


class SearchDisclosuresStructuredNoFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_corp_filter_still_searches_all_like_before(self) -> None:
        """corp_name/corp_code를 둘 다 생략하면 여전히 corp_code 필터 없이
        전체 공시를 조회할 수 있어야 한다 (기존 '전체 조회' 동작 보존)."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_LIST_JSON_RESPONSE)

        with mocked_list_json_transport(handler):
            result = await search_disclosures_structured(bgn_de="20240101", end_de="20240131")

        self.assertEqual(len(captured), 1)
        query = captured[0].url.params
        self.assertNotIn("corp_code", query)
        self.assertNotIn("corp_name", query)
        self.assertIsInstance(result, DisclosureSearchResult)


if __name__ == "__main__":
    unittest.main()
