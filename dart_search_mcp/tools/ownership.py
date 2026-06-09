from dart_search_mcp.app import mcp

from dart_search_mcp.client import _fetch_dart

from dart_search_mcp.formatting import _format_amount, _format_date
from dart_search_mcp.types import records_from

@mcp.tool()
async def get_major_shareholders_report(
    corp_code: str,
) -> str:
    """
    DART에서 대량보유 상황보고 정보를 조회합니다.

    특정 기업의 주식 등을 대량보유(5% 이상)한 자의 보유 현황 및 변동 내역을 반환합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")

    Returns:
        대량보유 상황보고 목록
        - 접수번호, 접수일자
        - 보고서구분 (보유/변동)
        - 보고자명
        - 보유주식수 및 증감
        - 보유비율 및 증감
        - 변동사유
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."

    params = {"corp_code": corp_code.strip()}

    data = await _fetch_dart("majorstock.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        if not items:
            return f"대량보유 상황보고 데이터가 없습니다.\n고유번호: {corp_code}"

        corp_name = items[0].get("corp_name", "") if items else ""

        lines = [
            f"대량보유 상황보고 ({corp_name})",
            f"고유번호: {corp_code}",
            "=" * 60,
        ]

        for i, item in enumerate(items, start=1):
            rcept_no = item.get("rcept_no", "")
            rcept_dt = item.get("rcept_dt", "")
            report_tp = item.get("report_tp", "")
            repror = item.get("repror", "")
            stkqy = item.get("stkqy", "")
            stkqy_irds = item.get("stkqy_irds", "")
            stkrt = item.get("stkrt", "")
            stkrt_irds = item.get("stkrt_irds", "")
            ctr_stkqy = item.get("ctr_stkqy", "")
            ctr_stkrt = item.get("ctr_stkrt", "")
            report_resn = item.get("report_resn", "")

            lines.append(f"\n{i}. {repror}")
            lines.append(f"   접수일자: {_format_date(rcept_dt)}")
            if report_tp:
                lines.append(f"   보고서구분: {report_tp}")
            if stkqy:
                lines.append(f"   보유주식수: {_format_amount(stkqy)} (증감: {_format_amount(stkqy_irds)})")
            if stkrt:
                lines.append(f"   보유비율: {stkrt}% (증감: {stkrt_irds}%)")
            if ctr_stkqy:
                lines.append(f"   계약등 주식수: {_format_amount(ctr_stkqy)} ({ctr_stkrt}%)")
            if report_resn:
                lines.append(f"   변동사유: {report_resn}")
            if rcept_no:
                lines.append(f"   접수번호: {rcept_no}")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"

@mcp.tool()
async def get_executive_stock_report(
    corp_code: str,
) -> str:
    """
    DART에서 임원 및 주요주주의 주식 소유보고 정보를 조회합니다.

    특정 기업의 임원 및 주요주주(10% 이상)의 특정증권등 소유 현황 및 변동 내역을 반환합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")

    Returns:
        임원·주요주주 소유보고 목록
        - 접수번호, 접수일자
        - 보고자명
        - 임원여부, 직위
        - 주요주주여부
        - 특정증권등 소유수 및 증감
        - 특정증권등 소유비율 및 증감
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."

    params = {"corp_code": corp_code.strip()}

    data = await _fetch_dart("elestock.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        if not items:
            return f"임원·주요주주 소유보고 데이터가 없습니다.\n고유번호: {corp_code}"

        corp_name = items[0].get("corp_name", "") if items else ""

        lines = [
            f"임원·주요주주 소유보고 ({corp_name})",
            f"고유번호: {corp_code}",
            "=" * 60,
        ]

        for i, item in enumerate(items, start=1):
            rcept_no = item.get("rcept_no", "")
            rcept_dt = item.get("rcept_dt", "")
            repror = item.get("repror", "")
            isu_exctv_rgist_at = item.get("isu_exctv_rgist_at", "")
            isu_exctv_ofcps = item.get("isu_exctv_ofcps", "")
            isu_main_shrholdr = item.get("isu_main_shrholdr", "")
            sp_stock_lmp_cnt = item.get("sp_stock_lmp_cnt", "")
            sp_stock_lmp_irds_cnt = item.get("sp_stock_lmp_irds_cnt", "")
            sp_stock_lmp_rate = item.get("sp_stock_lmp_rate", "")
            sp_stock_lmp_irds_rate = item.get("sp_stock_lmp_irds_rate", "")

            lines.append(f"\n{i}. {repror}")
            lines.append(f"   접수일자: {_format_date(rcept_dt)}")
            if isu_exctv_rgist_at:
                lines.append(f"   임원등록여부: {isu_exctv_rgist_at}")
            if isu_exctv_ofcps:
                lines.append(f"   임원직위: {isu_exctv_ofcps}")
            if isu_main_shrholdr:
                lines.append(f"   주요주주여부: {isu_main_shrholdr}")
            if sp_stock_lmp_cnt:
                lines.append(f"   소유수: {_format_amount(sp_stock_lmp_cnt)} (증감: {_format_amount(sp_stock_lmp_irds_cnt)})")
            if sp_stock_lmp_rate:
                lines.append(f"   소유비율: {sp_stock_lmp_rate}% (증감: {sp_stock_lmp_irds_rate}%)")
            if rcept_no:
                lines.append(f"   접수번호: {rcept_no}")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
