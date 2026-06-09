from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart
from dart_search_mcp.formatting import _format_amount
from dart_search_mcp.types import DartRecord, records_from


@mcp.tool()
async def get_multi_company_financials(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """
    DART에서 여러 회사의 주요계정 재무제표를 한 번에 조회합니다.

    최대 여러 회사의 매출액, 영업이익, 당기순이익, 자산총계 등
    주요 재무항목을 비교할 수 있습니다.

    Parameters:
        corp_code: DART 고유번호 (쉼표로 구분하여 복수 입력, 예: "00126380,00164779,00258801")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)

    Returns:
        다중회사 주요계정 재무제표 (회사별/재무제표 구분별 주요 계정 비교)
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

    params = {
        "corp_code": corp_code.strip(),
        "bsns_year": bsns_year.strip(),
        "reprt_code": reprt_code,
    }

    data = await _fetch_dart("fnlttMultiAcnt.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        if not items:
            return f"재무제표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"다중회사 주요계정 ({bsns_year}년 {reprt_nm})",
            f"조회 고유번호: {corp_code}",
            "=" * 70,
        ]

        # 회사별 + sj_div별 그룹핑
        corp_groups: dict[str, dict[str, list[DartRecord]]] = {}
        for item in items:
            stock_code = item.get("stock_code", "")
            corp_name = item.get("corp_name", stock_code)
            fs_div = item.get("fs_div", "")
            fs_nm = item.get("fs_nm", fs_div)
            sj_div = item.get("sj_div", "")
            sj_nm = item.get("sj_nm", sj_div)

            corp_key = f"{corp_name} ({fs_nm})"
            sj_key = f"{sj_div}:{sj_nm}"
            if corp_key not in corp_groups:
                corp_groups[corp_key] = {}
            if sj_key not in corp_groups[corp_key]:
                corp_groups[corp_key][sj_key] = []
            corp_groups[corp_key][sj_key].append(item)

        for corp_key, sj_groups in corp_groups.items():
            lines.append(f"\n{'=' * 40}")
            lines.append(f"  {corp_key}")
            lines.append(f"{'=' * 40}")

            for sj_key, sj_items in sj_groups.items():
                sj_nm = sj_key.split(":", 1)[1]
                lines.append(f"\n  [{sj_nm}]")

                bfefrmtrm_nm = ""
                if sj_items:
                    first = sj_items[0]
                    thstrm_nm = first.get("thstrm_nm", "당기")
                    frmtrm_nm = first.get("frmtrm_nm", "전기")
                    bfefrmtrm_nm = first.get("bfefrmtrm_nm", "")
                    if bfefrmtrm_nm:
                        lines.append(f"    {'계정명':<25} {thstrm_nm:>18} {frmtrm_nm:>18} {bfefrmtrm_nm:>18}")
                        lines.append("    " + "-" * 81)
                    else:
                        lines.append(f"    {'계정명':<25} {thstrm_nm:>18} {frmtrm_nm:>18}")
                        lines.append("    " + "-" * 63)

                for item in sj_items:
                    account_nm = item.get("account_nm", "")
                    thstrm_amount = item.get("thstrm_amount", "")
                    frmtrm_amount = item.get("frmtrm_amount", "")
                    bfefrmtrm_amount = item.get("bfefrmtrm_amount", "")

                    if bfefrmtrm_nm:
                        lines.append(
                            f"    {account_nm:<25} {_format_amount(thstrm_amount):>18} "
                            f"{_format_amount(frmtrm_amount):>18} {_format_amount(bfefrmtrm_amount):>18}"
                        )
                    else:
                        lines.append(
                            f"    {account_nm:<25} {_format_amount(thstrm_amount):>18} "
                            f"{_format_amount(frmtrm_amount):>18}"
                        )

        lines.append("\n" + "=" * 70)
        lines.append("단위: 원")

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
