from dart_search_mcp.app import mcp

from dart_search_mcp.client import _fetch_dart

from dart_search_mcp.formatting import _format_generic_response

@mcp.tool()
async def get_xbrl_taxonomy(sj_div: str = "BS1") -> str:
    """
    XBRL 택사노미 재무제표 양식(표준계정과목체계)을 조회합니다.

    Parameters:
        sj_div: 재무제표구분
            BS1=재무상태표(일반), BS2=재무상태표(특수), BS3=재무상태표(은행),
            BS4=재무상태표(보험), IS1=손익계산서(일반), IS2=손익계산서(특수),
            IS3=손익계산서(은행), IS4=손익계산서(보험),
            DCIS=포괄손익계산서, CF1~CF4=현금흐름표, SCE=자본변동표

    Returns:
        표준계정과목 목록 (계정ID, 계정명, 표시순서 등)
    """
    if not sj_div or not sj_div.strip():
        return "오류: 재무제표구분(sj_div)을 입력해주세요."

    data = await _fetch_dart("xbrlTaxonomy.json", {"sj_div": sj_div.strip()})
    if isinstance(data, str):
        return data

    try:
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

        title = f"XBRL 택사노미 - {sj_div} (표준계정과목체계)"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
