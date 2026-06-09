from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart
from dart_search_mcp.formatting import _format_amount
from dart_search_mcp.types import DartRecord, records_from


@mcp.tool()
async def get_financial_statements(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """
    DART에서 단일회사의 주요계정 재무제표를 조회합니다.

    매출액, 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계 등
    주요 재무항목을 당기/전기/전전기 비교 형식으로 반환합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)

    Returns:
        주요계정 재무제표 (재무상태표/손익계산서 구분, 당기/전기/전전기 금액)
        연결재무제표(CFS)와 개별재무제표(OFS) 모두 포함
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

    params = {
        "corp_code": corp_code.strip(),
        "bsns_year": bsns_year.strip(),
        "reprt_code": reprt_code,
    }

    data = await _fetch_dart("fnlttSinglAcnt.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        if not items:
            return f"재무제표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"단일회사 주요계정 ({bsns_year}년 {reprt_nm})",
            f"고유번호: {corp_code}",
            "=" * 70,
        ]

        # fs_div(CFS/OFS) + sj_div(BS/IS) 별로 그룹핑
        groups: dict[str, list[DartRecord]] = {}
        for item in items:
            fs_div = item.get("fs_div", "")
            fs_nm = item.get("fs_nm", fs_div)
            sj_div = item.get("sj_div", "")
            sj_nm = item.get("sj_nm", sj_div)
            key = f"{fs_nm} - {sj_nm}"
            if key not in groups:
                groups[key] = []
            groups[key].append(item)

        for group_key, group_items in groups.items():
            lines.append(f"\n[{group_key}]")

            bfefrmtrm_nm = ""
            if group_items:
                first = group_items[0]
                thstrm_nm = first.get("thstrm_nm", "당기")
                frmtrm_nm = first.get("frmtrm_nm", "전기")
                bfefrmtrm_nm = first.get("bfefrmtrm_nm", "")
                if bfefrmtrm_nm:
                    lines.append(f"  {'계정명':<25} {thstrm_nm:>20} {frmtrm_nm:>20} {bfefrmtrm_nm:>20}")
                    lines.append("  " + "-" * 87)
                else:
                    lines.append(f"  {'계정명':<25} {thstrm_nm:>20} {frmtrm_nm:>20}")
                    lines.append("  " + "-" * 67)

            for item in group_items:
                account_nm = item.get("account_nm", "")
                thstrm_amount = item.get("thstrm_amount", "")
                frmtrm_amount = item.get("frmtrm_amount", "")
                bfefrmtrm_amount = item.get("bfefrmtrm_amount", "")

                if bfefrmtrm_nm:
                    lines.append(
                        f"  {account_nm:<25} {_format_amount(thstrm_amount):>20} "
                        f"{_format_amount(frmtrm_amount):>20} {_format_amount(bfefrmtrm_amount):>20}"
                    )
                else:
                    lines.append(
                        f"  {account_nm:<25} {_format_amount(thstrm_amount):>20} "
                        f"{_format_amount(frmtrm_amount):>20}"
                    )

        lines.append("\n" + "=" * 70)
        lines.append("단위: 원")

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"

@mcp.tool()
async def get_financial_statements_full(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
) -> str:
    """
    DART에서 단일회사의 전체 재무제표를 조회합니다.

    재무상태표(BS), 손익계산서(IS), 포괄손익계산서(CIS), 현금흐름표(CF),
    자본변동표(SCE) 등 전체 재무제표 항목을 상세하게 반환합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)
        fs_div: 재무제표 구분
            CFS=연결재무제표(기본값), OFS=개별재무제표

    Returns:
        전체 재무제표 (BS=재무상태표, IS=손익계산서, CIS=포괄손익계산서,
        CF=현금흐름표, SCE=자본변동표 구분별 상세 항목)
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
    fs_div_map = {"CFS": "연결재무제표", "OFS": "개별재무제표"}
    sj_div_map = {
        "BS": "재무상태표",
        "IS": "손익계산서",
        "CIS": "포괄손익계산서",
        "CF": "현금흐름표",
        "SCE": "자본변동표",
    }

    params = {
        "corp_code": corp_code.strip(),
        "bsns_year": bsns_year.strip(),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }

    data = await _fetch_dart("fnlttSinglAcntAll.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        if not items:
            return f"재무제표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)
        fs_nm = fs_div_map.get(fs_div, fs_div)

        lines = [
            f"전체 재무제표 ({bsns_year}년 {reprt_nm} / {fs_nm})",
            f"고유번호: {corp_code}",
            "=" * 70,
        ]

        # sj_div 별로 그룹핑
        sj_groups: dict[str, list[DartRecord]] = {}
        for item in items:
            sj_div = item.get("sj_div", "")
            sj_nm = item.get("sj_nm", sj_div_map.get(sj_div, sj_div))
            key = f"{sj_div}:{sj_nm}"
            if key not in sj_groups:
                sj_groups[key] = []
            sj_groups[key].append(item)

        for group_key, group_items in sj_groups.items():
            sj_nm = group_key.split(":", 1)[1]
            lines.append(f"\n[{sj_nm}]")

            bfefrmtrm_nm = ""
            if group_items:
                first = group_items[0]
                thstrm_nm = first.get("thstrm_nm", "당기")
                frmtrm_nm = first.get("frmtrm_nm", "전기")
                bfefrmtrm_nm = first.get("bfefrmtrm_nm", "")
                if bfefrmtrm_nm:
                    lines.append(f"  {'계정명':<25} {thstrm_nm:>20} {frmtrm_nm:>20} {bfefrmtrm_nm:>20}")
                    lines.append("  " + "-" * 87)
                else:
                    lines.append(f"  {'계정명':<25} {thstrm_nm:>20} {frmtrm_nm:>20}")
                    lines.append("  " + "-" * 67)

            for item in group_items:
                account_nm = item.get("account_nm", "")
                account_detail = item.get("account_detail", "")
                thstrm_amount = item.get("thstrm_amount", "")
                frmtrm_amount = item.get("frmtrm_amount", "")
                bfefrmtrm_amount = item.get("bfefrmtrm_amount", "")

                display_nm = account_nm
                if account_detail and account_detail != "-":
                    display_nm = f"  {account_nm} ({account_detail})"

                if bfefrmtrm_nm:
                    lines.append(
                        f"  {display_nm:<25} {_format_amount(thstrm_amount):>20} "
                        f"{_format_amount(frmtrm_amount):>20} {_format_amount(bfefrmtrm_amount):>20}"
                    )
                else:
                    lines.append(
                        f"  {display_nm:<25} {_format_amount(thstrm_amount):>20} "
                        f"{_format_amount(frmtrm_amount):>20}"
                    )

        lines.append("\n" + "=" * 70)
        lines.append("단위: 원")

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
