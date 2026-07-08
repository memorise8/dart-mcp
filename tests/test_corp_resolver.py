"""
`dart_search_mcp.corp`의 구조화된 회사코드 리졸버(`resolve_corp_code`)에 대한 테스트.

Task 3: `search_corp_code`(공개 MCP 도구, 사람이 읽는 문자열 반환)에서 exact/prefix/
contains 매치를 레코드로 반환하는 구조화된 리졸버를 분리한다. `search_corp_code`는
이 리졸버 위의 얇은 포매팅 래퍼로 남아야 하며, 사람이 읽는 출력과 리댁션 동작은
변경되지 않아야 한다.

corpCode.xml은 실제 OpenDART 응답과 동일하게 ZIP으로 감싼 XML을 메모리에서 만들어
`httpx.MockTransport`로 주입한다. 실제 OpenDART로는 어떤 요청도 나가지 않는다.
"""

import io
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import AsyncMock, patch

import httpx

# `server`를 먼저 import해서 `dart_search_mcp.tools.*` -> `dart_search_mcp.corp` 순으로
# `@mcp.tool()` 등록이 이루어지는 정식 순서를 보존한다 (tests/test_public_surface.py의
# 도구 목록 순서 검증이 깨지지 않도록).
import server  # noqa: F401
from dart_search_mcp import corp
from dart_search_mcp.corp import CorpMatches, CorpRecord, CorpValidationError

_DUMMY_API_KEY = "test-dummy-crtfc-key-corp-resolver"

_RealAsyncClient = httpx.AsyncClient


def _build_corp_code_zip(records: list[dict[str, str]]) -> bytes:
    """실제 OpenDART corpCode.xml ZIP 응답과 동일한 형태(zip 안에 XML 하나)를
    메모리에서 만든다."""
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


def _patched_async_client(transport: httpx.MockTransport):
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


@contextmanager
def mocked_corp_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """corp.py 전용 OpenDART 호출을 MockTransport로 가로채고, 실제 크리덴셜 대신
    더미 키를 사용하게 한다. 실제 OpenDART로는 어떤 요청도 나가지 않는다."""
    transport = httpx.MockTransport(handler)
    with patch("dart_search_mcp.corp.httpx.AsyncClient", _patched_async_client(transport)), \
            patch("dart_search_mcp.corp.API_KEY", _DUMMY_API_KEY):
        yield


# "삼성전자"를 정확히 일치(exact), 접두(prefix), 부분(contains) 매치로 각각
# 대표하는 픽스처. 일부러 XML 상에서는 정확 일치 항목을 마지막에 두어, 리졸버가
# 소스 순서가 아니라 매치 종류에 따라 정렬함을 검증한다.
_FIXTURE_RECORDS = [
    {
        "corp_code": "00999999",
        "corp_name": "한국삼성전자판매",
        "stock_code": "",
        "modify_date": "20240101",
    },
    {
        "corp_code": "00777777",
        "corp_name": "카카오",
        "stock_code": "035720",
        "modify_date": "20240101",
    },
    {
        "corp_code": "00888888",
        "corp_name": "삼성전자우선주",
        "stock_code": "005935",
        "modify_date": "20240101",
    },
    {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "modify_date": "20240101",
    },
]


class ResolveCorpCodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        corp._corp_code_cache = None

    def tearDown(self) -> None:
        corp._corp_code_cache = None

    async def test_exact_match_ranks_before_prefix_and_contains(self) -> None:
        zip_bytes = _build_corp_code_zip(_FIXTURE_RECORDS)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        with mocked_corp_transport(handler):
            result = await corp.resolve_corp_code("삼성전자")

        self.assertIsInstance(result, CorpMatches)
        assert isinstance(result, CorpMatches)

        self.assertEqual([r.corp_name for r in result.exact], ["삼성전자"])
        self.assertEqual([r.corp_name for r in result.prefix], ["삼성전자우선주"])
        self.assertEqual([r.corp_name for r in result.contains], ["한국삼성전자판매"])

        # 카카오는 어떤 버킷에도 없어야 한다.
        all_names = [r.corp_name for r in result.all]
        self.assertNotIn("카카오", all_names)

        # exact가 prefix/contains보다 먼저 오는 순서를 명시적으로 검증한다.
        self.assertEqual(
            all_names,
            ["삼성전자", "삼성전자우선주", "한국삼성전자판매"],
        )

        exact_record = result.exact[0]
        self.assertIsInstance(exact_record, CorpRecord)
        self.assertEqual(exact_record.corp_code, "00126380")
        self.assertEqual(exact_record.stock_code, "005930")

    async def test_empty_company_name_returns_validation_error_without_network_call(self) -> None:
        with patch.object(corp, "_load_corp_codes", new_callable=AsyncMock) as mock_load:
            result = await corp.resolve_corp_code("   ")

        self.assertIsInstance(result, CorpValidationError)
        assert isinstance(result, CorpValidationError)
        self.assertEqual(result.message, "오류: 회사명(corp_name)을 입력해주세요.")
        mock_load.assert_not_called()

    async def test_no_matches_returns_empty_corp_matches(self) -> None:
        zip_bytes = _build_corp_code_zip(_FIXTURE_RECORDS)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        with mocked_corp_transport(handler):
            result = await corp.resolve_corp_code("존재하지않는회사이름")

        self.assertIsInstance(result, CorpMatches)
        assert isinstance(result, CorpMatches)
        self.assertEqual(result.all, [])


class SearchCorpCodeFormatterTests(unittest.IsolatedAsyncioTestCase):
    """`search_corp_code`(공개 MCP 도구)가 `resolve_corp_code` 위의 얇은 래퍼로서
    기존과 동일한 사람이 읽는 출력을 만드는지 확인한다."""

    def setUp(self) -> None:
        corp._corp_code_cache = None

    def tearDown(self) -> None:
        corp._corp_code_cache = None

    async def test_search_corp_code_output_stays_human_readable_and_ranked(self) -> None:
        zip_bytes = _build_corp_code_zip(_FIXTURE_RECORDS)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        with mocked_corp_transport(handler):
            output = await corp.search_corp_code("삼성전자")

        self.assertIn('"삼성전자" 회사 검색 결과', output)
        self.assertIn("고유번호: 00126380", output)
        self.assertIn("종목코드: 005930", output)
        self.assertIn("[기업정보: get_company_info('00126380')]", output)

        # exact 매치("삼성전자")가 prefix 매치("삼성전자우선주")보다 먼저 나와야 한다.
        self.assertLess(
            output.index("1. 삼성전자\n"),
            output.index("삼성전자우선주"),
        )

    async def test_search_corp_code_no_matches_message_unchanged(self) -> None:
        zip_bytes = _build_corp_code_zip(_FIXTURE_RECORDS)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=zip_bytes)

        with mocked_corp_transport(handler):
            output = await corp.search_corp_code("존재하지않는회사이름")

        self.assertEqual(output, "검색 결과가 없습니다.\n검색어: 존재하지않는회사이름")

    async def test_search_corp_code_empty_name_error_unchanged(self) -> None:
        with patch.object(corp, "_load_corp_codes", new_callable=AsyncMock) as mock_load:
            output = await corp.search_corp_code("")

        self.assertEqual(output, "오류: 회사명(corp_name)을 입력해주세요.")
        mock_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
