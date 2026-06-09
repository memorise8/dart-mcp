from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart
from dart_search_mcp.formatting import _format_amount
from dart_search_mcp.types import DartRecord, records_from


@mcp.tool()
async def get_financial_indicators(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    idx_cl_code: str = "",
) -> str:
    """
    DART에서 단일회사의 주요 재무지표를 조회합니다.

    수익성, 안정성, 성장성, 활동성 등의 재무비율 지표를 반환합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)
        idx_cl_code: 지표분류코드 (빈 문자열이면 전체)
            M210000=수익성지표 (매출총이익률, 영업이익률, 순이익률, ROE, ROA 등)
            M220000=안정성지표 (부채비율, 유동비율, 자기자본비율, 이자보상배율 등)
            M230000=성장성지표 (매출액증가율, 영업이익증가율, 총자산증가율 등)
            M240000=활동성지표 (총자산회전율, 재고자산회전율, 매출채권회전율 등)

    Returns:
        재무지표 목록 (지표분류, 지표명, 지표값)
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."
    if not bsns_year or not bsns_year.strip():
        return "오류: 사업연도(bsns_year)를 입력해주세요."

    reprt_code_map = {
        "11013": "1분기보고서",
        "11012": "반기보고서",
        "11014": "3분기보고서",
        "11011": "사업보고서",
    }

    # idx_cl_code는 DART API 필수값. 미지정 시 4개 분류를 모두 조회하여 병합.
    codes = [idx_cl_code] if idx_cl_code else ["M210000", "M220000", "M230000", "M240000"]

    items: list[DartRecord] = []
    last_err = None
    for code in codes:
        params = {
            "corp_code": corp_code.strip(),
            "bsns_year": bsns_year.strip(),
            "reprt_code": reprt_code,
            "idx_cl_code": code,
        }
        data = await _fetch_dart("fnlttSinglIndx.json", params)
        if isinstance(data, str):
            last_err = data
            continue
        items.extend(records_from(data.get("list", [])))

    if not items:
        return last_err or f"재무지표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

    try:
        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"단일회사 주요 재무지표 ({bsns_year}년 {reprt_nm})",
            f"고유번호: {corp_code}",
            "=" * 60,
        ]

        # 지표분류별 그룹핑
        cl_groups: dict[str, list[DartRecord]] = {}
        for item in items:
            idx_cl_nm = item.get("idx_cl_nm", "기타")
            if idx_cl_nm not in cl_groups:
                cl_groups[idx_cl_nm] = []
            cl_groups[idx_cl_nm].append(item)

        for cl_nm, cl_items in cl_groups.items():
            lines.append(f"\n[{cl_nm}]")
            lines.append(f"  {'지표명':<30} {'지표값':>15}")
            lines.append("  " + "-" * 47)

            for item in cl_items:
                idx_nm = item.get("idx_nm", "")
                idx_val = item.get("idx_val", "-")
                lines.append(f"  {idx_nm:<30} {idx_val:>15}")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"

@mcp.tool()
async def get_multi_company_indicators(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    idx_cl_code: str = "",
) -> str:
    """
    DART에서 여러 회사의 주요 재무지표를 한 번에 조회합니다.

    여러 회사의 수익성, 안정성, 성장성, 활동성 지표를 비교할 수 있습니다.

    Parameters:
        corp_code: DART 고유번호 (쉼표로 구분하여 복수 입력, 예: "00126380,00164779")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)
        idx_cl_code: 지표분류코드 (빈 문자열이면 전체)
            M210000=수익성지표 (매출총이익률, 영업이익률, 순이익률, ROE, ROA 등)
            M220000=안정성지표 (부채비율, 유동비율, 자기자본비율, 이자보상배율 등)
            M230000=성장성지표 (매출액증가율, 영업이익증가율, 총자산증가율 등)
            M240000=활동성지표 (총자산회전율, 재고자산회전율, 매출채권회전율 등)

    Returns:
        다중회사 재무지표 비교 (회사별/지표분류별 지표명, 지표값)
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요. 쉼표로 구분하여 복수 입력 가능합니다."
    if not bsns_year or not bsns_year.strip():
        return "오류: 사업연도(bsns_year)를 입력해주세요."

    reprt_code_map = {
        "11013": "1분기보고서",
        "11012": "반기보고서",
        "11014": "3분기보고서",
        "11011": "사업보고서",
    }

    # idx_cl_code는 DART API 필수값. 미지정 시 4개 분류를 모두 조회하여 병합.
    codes = [idx_cl_code] if idx_cl_code else ["M210000", "M220000", "M230000", "M240000"]

    items: list[DartRecord] = []
    last_err = None
    for code in codes:
        params = {
            "corp_code": corp_code.strip(),
            "bsns_year": bsns_year.strip(),
            "reprt_code": reprt_code,
            "idx_cl_code": code,
        }
        data = await _fetch_dart("fnlttCmpnyIndx.json", params)
        if isinstance(data, str):
            last_err = data
            continue
        items.extend(records_from(data.get("list", [])))

    if not items:
        return last_err or f"재무지표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

    try:
        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"다중회사 주요 재무지표 ({bsns_year}년 {reprt_nm})",
            f"조회 고유번호: {corp_code}",
            "=" * 60,
        ]

        # 회사별 + 지표분류별 그룹핑
        corp_groups: dict[str, dict[str, list[DartRecord]]] = {}
        for item in items:
            corp_name = item.get("corp_name", "")
            idx_cl_nm = item.get("idx_cl_nm", "기타")

            if corp_name not in corp_groups:
                corp_groups[corp_name] = {}
            if idx_cl_nm not in corp_groups[corp_name]:
                corp_groups[corp_name][idx_cl_nm] = []
            corp_groups[corp_name][idx_cl_nm].append(item)

        for corp_name, cl_groups in corp_groups.items():
            lines.append(f"\n{'=' * 40}")
            lines.append(f"  {corp_name}")
            lines.append(f"{'=' * 40}")

            for cl_nm, cl_items in cl_groups.items():
                lines.append(f"\n  [{cl_nm}]")
                lines.append(f"    {'지표명':<30} {'지표값':>15}")
                lines.append("    " + "-" * 47)

                for item in cl_items:
                    idx_nm = item.get("idx_nm", "")
                    idx_val = item.get("idx_val", "-")
                    lines.append(f"    {idx_nm:<30} {idx_val:>15}")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
