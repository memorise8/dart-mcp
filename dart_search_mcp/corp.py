import io

import xml.etree.ElementTree as ET

import zipfile



import httpx

from dart_search_mcp.app import mcp

from dart_search_mcp.config import API_KEY



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
    if not corp_name or not corp_name.strip():
        return "오류: 회사명(corp_name)을 입력해주세요."

    query = corp_name.strip().lower()

    try:
        corps = await _load_corp_codes()
    except Exception as e:
        return f"오류: 회사 코드 목록 로드 중 오류가 발생했습니다. {str(e)}"

    exact: list[dict[str, str]] = []
    starts: list[dict[str, str]] = []
    contains: list[dict[str, str]] = []

    for item in corps:
        name = item.get("corp_name", "").lower()
        if name == query:
            exact.append(item)
        elif name.startswith(query):
            starts.append(item)
        elif query in name:
            contains.append(item)

    matches = exact + starts + contains

    if not matches:
        return f"검색 결과가 없습니다.\n검색어: {corp_name}"

    lines = [
        f'"{corp_name}" 회사 검색 결과',
        "=" * 60,
    ]

    for i, item in enumerate(matches[:20], start=1):
        corp_nm = item.get("corp_name", "")
        c_code = item.get("corp_code", "")
        stock_code = item.get("stock_code", "").strip()

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
