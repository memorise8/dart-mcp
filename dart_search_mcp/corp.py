from __future__ import annotations

import io

import xml.etree.ElementTree as ET

import zipfile

from dataclasses import dataclass, field



import httpx

from dart_search_mcp.app import mcp

from dart_search_mcp.config import API_KEY

from dart_search_mcp.redact import redact



_corp_code_cache: list[dict[str, str]] | None = None

async def _load_corp_codes() -> list[dict[str, str]]:
    """corpCode.xml ZIP을 다운로드하여 파싱, 모듈 수준 캐시에 저장합니다."""
    global _corp_code_cache
    if _corp_code_cache is not None:
        return _corp_code_cache

    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_filename = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        xml_bytes = zf.read(xml_filename)

    root = ET.fromstring(xml_bytes.decode("utf-8"))
    corps = []
    for elem in root.findall("list"):
        corps.append(
            {
                "corp_code": elem.findtext("corp_code", ""),
                "corp_name": elem.findtext("corp_name", ""),
                "stock_code": elem.findtext("stock_code", ""),
                "modify_date": elem.findtext("modify_date", ""),
            }
        )

    _corp_code_cache = corps
    return corps


@dataclass(frozen=True, slots=True)
class CorpRecord:
    """corpCode.xml의 회사 한 건에 대응하는 구조화된 레코드."""

    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str


@dataclass(frozen=True, slots=True)
class CorpMatches:
    """회사명 검색의 구조화된 결과.

    `exact`(완전 일치), `prefix`(접두 일치), `contains`(부분 일치) 세 버킷으로
    나뉘며, `all`은 이 우선순위(exact -> prefix -> contains) 그대로 이어붙인
    목록이다.
    """

    query: str
    exact: list[CorpRecord] = field(default_factory=list)
    prefix: list[CorpRecord] = field(default_factory=list)
    contains: list[CorpRecord] = field(default_factory=list)

    @property
    def all(self) -> list[CorpRecord]:
        return [*self.exact, *self.prefix, *self.contains]


@dataclass(frozen=True, slots=True)
class CorpValidationError:
    """회사명 입력값 검증에 실패한 경우. `_load_corp_codes()`를 호출하지 않는다."""

    message: str


@dataclass(frozen=True, slots=True)
class CorpLoadError:
    """corpCode.xml 로드(다운로드/파싱) 중 오류가 발생한 경우."""

    message: str


type CorpResolution = CorpMatches | CorpValidationError | CorpLoadError


def _to_corp_record(item: dict[str, str]) -> CorpRecord:
    return CorpRecord(
        corp_code=item.get("corp_code", ""),
        corp_name=item.get("corp_name", ""),
        stock_code=item.get("stock_code", ""),
        modify_date=item.get("modify_date", ""),
    )


async def resolve_corp_code(corp_name: str) -> CorpResolution:
    """회사명으로 corpCode.xml 목록을 검색해 구조화된 결과를 반환합니다.

    `search_corp_code`(공개 MCP 도구)가 사람이 읽는 문자열로 포매팅하기 전
    단계로, exact/prefix/contains 매치를 각각 `CorpRecord` 목록으로 구분해
    돌려준다. 입력 검증 실패 시에는 `_load_corp_codes()`를 호출하지 않는다
    (네트워크 호출 없이 즉시 `CorpValidationError`를 반환).

    Parameters:
        corp_name: 검색할 회사명 (예: "삼성전자", "카카오", "네이버")

    Returns:
        `CorpMatches`(정상), `CorpValidationError`(입력값 오류) 또는
        `CorpLoadError`(corpCode.xml 로드 실패) 중 하나.
    """
    if not corp_name or not corp_name.strip():
        return CorpValidationError("오류: 회사명(corp_name)을 입력해주세요.")

    query = corp_name.strip().lower()

    try:
        corps = await _load_corp_codes()
    except Exception as e:
        return CorpLoadError(f"오류: 회사 코드 목록 로드 중 오류가 발생했습니다. {redact(str(e))}")

    exact: list[CorpRecord] = []
    prefix: list[CorpRecord] = []
    contains: list[CorpRecord] = []

    for item in corps:
        name = item.get("corp_name", "").lower()
        if name == query:
            exact.append(_to_corp_record(item))
        elif name.startswith(query):
            prefix.append(_to_corp_record(item))
        elif query in name:
            contains.append(_to_corp_record(item))

    return CorpMatches(query=corp_name, exact=exact, prefix=prefix, contains=contains)


@mcp.tool()
async def search_corp_code(
    corp_name: str,
) -> str:
    """
    회사명으로 DART 고유번호(corp_code)를 검색합니다.

    get_company_info(), get_financial_statements() 등 대부분의 API 호출에 필요한
    고유번호를 회사명으로 찾을 때 사용합니다. corpCode.xml 전체 목록을 다운로드하여
    정확한 회사명 매칭을 수행합니다 (최초 호출 시 다운로드 후 메모리 캐시).

    Parameters:
        corp_name: 검색할 회사명 (예: "삼성전자", "카카오", "네이버")

    Returns:
        검색된 회사 목록과 각 회사의 고유번호(corp_code)
    """
    result = await resolve_corp_code(corp_name)

    if isinstance(result, (CorpValidationError, CorpLoadError)):
        return result.message

    matches = result.all

    if not matches:
        return f"검색 결과가 없습니다.\n검색어: {corp_name}"

    lines = [
        f'"{corp_name}" 회사 검색 결과',
        "=" * 60,
    ]

    for i, item in enumerate(matches[:20], start=1):
        corp_nm = item.corp_name
        c_code = item.corp_code
        stock_code = item.stock_code.strip()

        lines.append(f"\n{i}. {corp_nm}")
        if stock_code:
            lines.append(f"   종목코드: {stock_code}")
        if c_code:
            lines.append(f"   고유번호: {c_code}")
            lines.append(f"   [기업정보: get_company_info('{c_code}')]")

    if len(matches) > 20:
        lines.append(f"\n... 외 {len(matches) - 20}개 결과 (검색어를 더 구체적으로 입력하세요)")

    lines.append("\n" + "=" * 60)

    return "\n".join(lines)
