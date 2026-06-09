from dart_search_mcp.app import mcp

from dart_search_mcp.client import _fetch_dart

from dart_search_mcp.formatting import _format_date
from dart_search_mcp.types import QueryParams, records_from

@mcp.tool()
async def search_disclosures(
    corp_name: str = "",
    corp_code: str = "",
    bgn_de: str = "",
    end_de: str = "",
    last_reprt_at: str = "",
    pblntf_ty: str = "",
    pblntf_detail_ty: str = "",
    corp_cls: str = "",
    sort: str = "date",
    sort_mth: str = "desc",
    page_no: int = 1,
    page_count: int = 20,
) -> str:
    """
    DART 전자공시시스템에서 공시 목록을 검색합니다.

    Parameters:
        corp_name: 회사명 (예: "삼성전자", "카카오")
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        bgn_de: 검색 시작일 YYYYMMDD (예: "20240101")
        end_de: 검색 종료일 YYYYMMDD (예: "20241231")
        last_reprt_at: 최종보고서만 검색 (Y/N, 기본값: 빈문자열=전체)
        pblntf_ty: 공시유형
            A=정기공시, B=주요사항보고, C=발행공시, D=지분공시,
            E=기타공시, F=외부감사관련, G=펀드공시, H=자산유동화,
            I=거래소공시, J=공정위공시
        pblntf_detail_ty: 공시상세유형 (공시유형 하위 세부 유형코드)
        corp_cls: 법인구분 (Y=유가증권, K=코스닥, N=코넥스, E=기타)
        sort: 정렬 기준 (date=접수일자, crp=회사명, rpt=보고서명)
        sort_mth: 정렬방식 (asc=오름차순, desc=내림차순)
        page_no: 페이지번호 (기본값: 1)
        page_count: 페이지당 건수 (기본값: 20, 최대 100)

    Returns:
        공시 목록 (회사명, 보고서명, 접수일자, 고유번호, 접수번호 포함)
        corp_code를 사용하여 get_company_info() 또는 get_financial_statements()로 상세 조회 가능
    """
    params: QueryParams = {
        "page_no": str(page_no),
        "page_count": str(min(page_count, 100)),
        "sort": sort,
        "sort_mth": sort_mth,
    }
    if corp_name:
        params["corp_name"] = corp_name
    if corp_code:
        params["corp_code"] = corp_code.strip()
    if bgn_de:
        params["bgn_de"] = bgn_de
    if end_de:
        params["end_de"] = end_de
    if last_reprt_at:
        params["last_reprt_at"] = last_reprt_at
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty
    if pblntf_detail_ty:
        params["pblntf_detail_ty"] = pblntf_detail_ty
    if corp_cls:
        params["corp_cls"] = corp_cls

    data = await _fetch_dart("list.json", params)
    if isinstance(data, str):
        return data

    try:
        total_count = str(data.get("total_count", 0))
        total_page = int(str(data.get("total_page", 1)) or "1")
        items = records_from(data.get("list", []))

        if not items:
            search_desc = corp_name if corp_name else "전체"
            return f"검색 결과가 없습니다.\n검색 조건: {search_desc}"

        search_desc = corp_name if corp_name else "전체"
        lines = [
            f"공시 검색 결과 (검색: \"{search_desc}\", {page_no}/{total_page}페이지, 총 {total_count}건)",
            "=" * 60,
        ]

        for i, item in enumerate(items, start=1):
            corp_nm = item.get("corp_name", "")
            c_code = item.get("corp_code", "")
            stock_code = item.get("stock_code", "")
            report_nm = item.get("report_nm", "")
            rcept_no = item.get("rcept_no", "")
            flr_nm = item.get("flr_nm", "")
            rcept_dt = item.get("rcept_dt", "")
            rm = item.get("rm", "")

            lines.append(f"\n{i}. {report_nm}")
            corp_parts = [corp_nm]
            if stock_code:
                corp_parts.append(f"({stock_code})")
            lines.append(f"   회사: {' '.join(corp_parts)}")
            if flr_nm and flr_nm != corp_nm:
                lines.append(f"   제출인: {flr_nm}")
            lines.append(f"   접수일자: {_format_date(rcept_dt)}")
            if c_code:
                lines.append(f"   고유번호: {c_code}")
            if rcept_no:
                lines.append(f"   접수번호: {rcept_no}")
            if rm:
                lines.append(f"   비고: {rm}")

        lines.append("\n" + "=" * 60)
        if page_no < int(total_page):
            lines.append(f"다음 페이지: search_disclosures(corp_name=\"{corp_name}\", page_no={page_no + 1})")

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"

@mcp.tool()
async def get_company_info(
    corp_code: str,
) -> str:
    """
    DART 고유번호로 기업 개황 정보를 조회합니다.

    고유번호(corp_code)는 search_disclosures() 또는 search_corp_code() 결과에서 확인할 수 있습니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")

    Returns:
        기업 개황 정보 (회사명, 대표이사, 주소, 업종, 설립일, 결산월 등)
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."

    data = await _fetch_dart("company.json", {"corp_code": corp_code.strip()})
    if isinstance(data, str):
        return data

    try:
        corp_cls_map = {"Y": "유가증권시장", "K": "코스닥", "N": "코넥스", "E": "기타"}

        corp_name = str(data.get("corp_name") or "")
        corp_name_eng = str(data.get("corp_name_eng") or "")
        stock_name = str(data.get("stock_name") or "")
        stock_code = str(data.get("stock_code") or "")
        ceo_nm = str(data.get("ceo_nm") or "")
        c_cls = str(data.get("corp_cls") or "")
        jurir_no = str(data.get("jurir_no") or "")
        bizr_no = str(data.get("bizr_no") or "")
        adres = str(data.get("adres") or "")
        hm_url = str(data.get("hm_url") or "")
        ir_url = str(data.get("ir_url") or "")
        phn_no = str(data.get("phn_no") or "")
        fax_no = str(data.get("fax_no") or "")
        induty_code = str(data.get("induty_code") or "")
        est_dt = str(data.get("est_dt") or "")
        acc_mt = str(data.get("acc_mt") or "")

        lines = [
            "=" * 60,
            "기업 개황",
            "=" * 60,
        ]

        if corp_name:
            name_line = corp_name
            if corp_name_eng:
                name_line += f" ({corp_name_eng})"
            lines.append(f"회사명:       {name_line}")
        if stock_name or stock_code:
            lines.append(f"종목명/코드:  {stock_name} / {stock_code}")
        if c_cls:
            lines.append(f"법인구분:     {corp_cls_map.get(c_cls, c_cls)}")
        if ceo_nm:
            lines.append(f"대표이사:     {ceo_nm}")
        lines.append("")

        if adres:
            lines.append(f"주소:         {adres}")
        if phn_no:
            lines.append(f"전화번호:     {phn_no}")
        if fax_no:
            lines.append(f"팩스번호:     {fax_no}")
        if hm_url:
            lines.append(f"홈페이지:     {hm_url}")
        if ir_url:
            lines.append(f"IR:           {ir_url}")
        lines.append("")

        if bizr_no:
            lines.append(f"사업자번호:   {bizr_no}")
        if jurir_no:
            lines.append(f"법인등록번호: {jurir_no}")
        if induty_code:
            lines.append(f"업종코드:     {induty_code}")
        if est_dt:
            lines.append(f"설립일:       {_format_date(est_dt)}")
        if acc_mt:
            lines.append(f"결산월:       {acc_mt}월")

        lines.append("=" * 60)

        return "\n".join(lines)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
