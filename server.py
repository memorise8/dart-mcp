"""
전자공시검색 MCP 서버

한국 금융감독원 전자공시시스템(DART) Open API를 통해
기업 공시 정보, 재무제표, 지분공시, 정기보고서, 주요사항보고서 등
70여개 API를 검색하는 종합 MCP 서버입니다.
"""

import datetime
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("전자공시검색")

API_KEY = os.environ.get("DART_API_KEY", "") or os.environ.get("dart_api", "")

_corp_code_cache: list[dict] | None = None
BASE_URL = "https://opendart.fss.or.kr/api"


# ============================================================
# 레지스트리: 정기보고서 (DS002) - 27개 보고서 유형
# ============================================================

PERIODIC_REPORT_REGISTRY: dict[str, str] = {
    "증자감자현황": "irdsSttus",
    "배당": "alotMatter",
    "자기주식취득처분": "tesstkAcqsDspsSttus",
    "최대주주현황": "hyslrSttus",
    "최대주주변동": "hyslrChgSttus",
    "소액주주": "mrhlSttus",
    "임원현황": "exctvSttus",
    "직원현황": "empSttus",
    "이사감사개인별보수": "hmvAuditIndvdlBySttus",
    "이사감사전체보수": "hmvAuditAllSttus",
    "개인별보수지급": "indvdlByPay",
    "타법인출자": "otrCprInvstmntSttus",
    "채무증권발행": "detScritsIsuAcmslt",
    "기업어음미상환": "entrprsBilScritsNrdmpBlce",
    "단기사채미상환": "srtpdPsndbtNrdmpBlce",
    "회사채미상환": "cprndNrdmpBlce",
    "신종자본증권미상환": "newCaplScritsNrdmpBlce",
    "조건부자본증권미상환": "cndlCaplScritsNrdmpBlce",
    "회계감사인": "accnutAdtorNmNdAdtOpinion",
    "감사용역체결": "adtServcCnclsSttus",
    "비감사용역계약": "accnutAdtorNonAdtServcCnclsSttus",
    "사외이사변동": "outcmpnyDrctrNdChangeSttus",
    "미등기임원보수": "unrstExctvMendngSttus",
    "이사감사보수승인금액": "drctrAdtAllMendngSttusGmtsckConfmAmount",
    "이사감사보수유형별": "drctrAdtAllMendngSttusMendngPymntamtTyCl",
    "공모자금사용": "pssrpCptalUseDtls",
    "사모자금사용": "prvsrpCptalUseDtls",
    "주식총수현황": "stockTotqySttus",
}


# ============================================================
# 레지스트리: 주요사항보고서 (DS005) - 36개 이벤트 유형
# ============================================================

MAJOR_EVENT_REGISTRY: dict[str, str] = {
    "자산양수도": "astInhtrfEtcPtbkOpt",
    "부도발생": "dfOcr",
    "영업정지": "bsnSp",
    "회생절차": "ctrcvsBgrq",
    "해산사유": "dsRsOcr",
    "유상증자결정": "piicDecsn",
    "무상증자결정": "fricDecsn",
    "유무상증자결정": "pifricDecsn",
    "감자결정": "crDecsn",
    "관리절차개시": "bnkMngtPcbg",
    "소송": "lwstLg",
    "해외상장결정": "ovLstDecsn",
    "해외상장폐지결정": "ovDlstDecsn",
    "해외상장": "ovLst",
    "해외상장폐지": "ovDlst",
    "전환사채발행": "cvbdIsDecsn",
    "신주인수권부사채발행": "bdwtIsDecsn",
    "교환사채발행": "exbdIsDecsn",
    "관리절차중단": "bnkMngtPcsp",
    "상각형조건부자본증권발행": "wdCocobdIsDecsn",
    "자기주식취득결정": "tsstkAqDecsn",
    "자기주식처분결정": "tsstkDpDecsn",
    "자기주식신탁체결": "tsstkAqTrctrCnsDecsn",
    "자기주식신탁해지": "tsstkAqTrctrCcDecsn",
    "영업양수결정": "bsnInhDecsn",
    "영업양도결정": "bsnTrfDecsn",
    "유형자산양수": "tgastInhDecsn",
    "유형자산양도": "tgastTrfDecsn",
    "타법인주식양수": "otcprStkInvscrInhDecsn",
    "타법인주식양도": "otcprStkInvscrTrfDecsn",
    "사채권양수": "stkrtbdInhDecsn",
    "사채권양도": "stkrtbdTrfDecsn",
    "회사합병": "cmpMgDecsn",
    "회사분할": "cmpDvDecsn",
    "회사분할합병": "cmpDvmgDecsn",
    "주식교환이전": "stkExtrDecsn",
}


# ============================================================
# 레지스트리: 증권신고서 (DS003) - 6개 유형
# ============================================================

SECURITIES_REGISTRATION_REGISTRY: dict[str, str] = {
    "지분증권": "estkRs",
    "채무증권": "bdRs",
    "증권예탁증권": "stkdpRs",
    "합병": "mgRs",
    "주식의포괄적교환이전": "extrRs",
    "분할": "dvRs",
}


# ============================================================
# 헬퍼 함수
# ============================================================


async def _fetch_dart(endpoint: str, params: dict) -> dict | str:
    """
    공통 DART API 호출 함수.
    성공 시 파싱된 dict를 반환하고, 실패 시 오류 메시지 문자열을 반환합니다.
    """
    params["crtfc_key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        return "오류: 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
    except httpx.HTTPStatusError as e:
        return f"오류: HTTP 오류가 발생했습니다. 상태 코드: {e.response.status_code}"
    except httpx.RequestError as e:
        return f"오류: 네트워크 오류가 발생했습니다. {str(e)}"
    except Exception as e:
        return f"오류: 예상치 못한 오류가 발생했습니다. {str(e)}"

    # DART API 상태 코드 확인
    status = data.get("status", "")
    message = data.get("message", "")
    # status 013은 오류가 아니라 "조회된 데이터 없음" 신호
    if status == "013":
        return "조회된 데이터가 없습니다. (해당 조건에 맞는 공시/보고 내역이 없습니다)"
    if status and status != "000":
        return f"오류: {message} (status: {status})"

    return data


async def _fetch_dart_binary(endpoint: str, params: dict) -> bytes | str:
    """DART API에서 바이너리(ZIP) 파일을 다운로드합니다."""
    params["crtfc_key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            # JSON 응답이면 오류 메시지
            if "application/json" in content_type:
                data = response.json()
                status = data.get("status", "")
                message = data.get("message", "")
                return f"오류: {message} (status: {status})"

            return response.content
    except httpx.TimeoutException:
        return "오류: 요청 시간이 초과되었습니다."
    except httpx.HTTPStatusError as e:
        return f"오류: HTTP 오류 상태 코드: {e.response.status_code}"
    except Exception as e:
        return f"오류: {str(e)}"


def _format_date(date_str: str) -> str:
    """YYYYMMDD 형식의 날짜를 YYYY-MM-DD로 변환합니다."""
    if date_str and len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return date_str


def _default_date_range(bgn_de: str, end_de: str) -> tuple[str, str]:
    """
    bgn_de/end_de 미지정 시 기본 범위(최근 약 10년 ~ 오늘)를 채웁니다.
    주요사항보고서·증권신고서 API는 bgn_de/end_de가 필수이므로 생략 시 기본값을 적용.
    """
    today = datetime.date.today()
    if not end_de:
        end_de = today.strftime("%Y%m%d")
    if not bgn_de:
        bgn_de = f"{today.year - 10}0101"
    return bgn_de, end_de


def _format_amount(val: str) -> str:
    """숫자 문자열에 천 단위 콤마를 추가합니다."""
    if not val or val == "-":
        return "-"
    # 공백 제거
    cleaned = val.strip().replace(",", "")
    if not cleaned:
        return "-"
    try:
        return f"{int(cleaned):,}"
    except ValueError:
        try:
            return f"{float(cleaned):,.2f}"
        except ValueError:
            return val


def _format_generic_response(
    title: str,
    data: dict | list,
    key_descriptions: dict | None = None,
) -> str:
    """
    DART API 응답을 범용적으로 포맷팅합니다.
    정기보고서, 주요사항보고서 등 다양한 응답 구조에 대응합니다.

    Args:
        title: 출력 제목
        data: API 응답의 list 또는 단일 dict
        key_descriptions: 키에 대한 한글 설명 매핑 (선택)
    """
    lines = [
        "=" * 60,
        title,
        "=" * 60,
    ]

    if isinstance(data, dict):
        data = [data]

    if not data:
        lines.append("데이터가 없습니다.")
        lines.append("=" * 60)
        return "\n".join(lines)

    descs = key_descriptions or {}

    for idx, item in enumerate(data):
        if len(data) > 1:
            lines.append(f"\n--- [{idx + 1}] ---")

        if isinstance(item, dict):
            for key, value in item.items():
                # status, message 등 메타 필드 제외
                if key in ("status", "message", "crtfc_key"):
                    continue
                label = descs.get(key, key)
                str_val = str(value) if value is not None else "-"

                # 날짜 필드 자동 포맷
                if (key.endswith("_de") or key.endswith("_dt") or key == "est_dt") and len(str_val) == 8:
                    str_val = _format_date(str_val)

                # 금액 필드 자동 포맷 (amount, cnt, qy 등 숫자성 필드)
                if any(
                    kw in key
                    for kw in ("amount", "amt", "_cnt", "_qy", "stkqy", "stkrt", "lmp_cnt", "lmp_rate")
                ):
                    str_val = _format_amount(str_val)

                lines.append(f"  {label}: {str_val}")
        else:
            lines.append(f"  {item}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ============================================================
# Tool 1: 공시검색
# ============================================================


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
    params: dict = {
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
        total_count = data.get("total_count", 0)
        total_page = data.get("total_page", 1)
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

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


# ============================================================
# Tool 2: 기업개황
# ============================================================


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

        corp_name = data.get("corp_name", "")
        corp_name_eng = data.get("corp_name_eng", "")
        stock_name = data.get("stock_name", "")
        stock_code = data.get("stock_code", "")
        ceo_nm = data.get("ceo_nm", "")
        c_cls = data.get("corp_cls", "")
        jurir_no = data.get("jurir_no", "")
        bizr_no = data.get("bizr_no", "")
        adres = data.get("adres", "")
        hm_url = data.get("hm_url", "")
        ir_url = data.get("ir_url", "")
        phn_no = data.get("phn_no", "")
        fax_no = data.get("fax_no", "")
        induty_code = data.get("induty_code", "")
        est_dt = data.get("est_dt", "")
        acc_mt = data.get("acc_mt", "")

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


# ============================================================
# Tool 3: 회사명으로 고유번호 검색
# ============================================================


async def _load_corp_codes() -> list[dict]:
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

    exact: list[dict] = []
    starts: list[dict] = []
    contains: list[dict] = []

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


# ============================================================
# Tool 4: 단일회사 주요계정
# ============================================================


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
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

        if not items:
            return f"재무제표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"단일회사 주요계정 ({bsns_year}년 {reprt_nm})",
            f"고유번호: {corp_code}",
            "=" * 70,
        ]

        # fs_div(CFS/OFS) + sj_div(BS/IS) 별로 그룹핑
        groups: dict[str, list] = {}
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


# ============================================================
# Tool 5: 단일회사 전체 재무제표
# ============================================================


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
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

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
        sj_groups: dict[str, list] = {}
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


# ============================================================
# Tool 6: 다중회사 주요계정
# ============================================================


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
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

        if not items:
            return f"재무제표 데이터가 없습니다.\n고유번호: {corp_code}, 사업연도: {bsns_year}"

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)

        lines = [
            f"다중회사 주요계정 ({bsns_year}년 {reprt_nm})",
            f"조회 고유번호: {corp_code}",
            "=" * 70,
        ]

        # 회사별 + sj_div별 그룹핑
        corp_groups: dict[str, dict[str, list]] = {}
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


# ============================================================
# Tool 7: 단일회사 주요 재무지표
# ============================================================


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

    items: list = []
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
        part = data.get("list", [])
        if isinstance(part, dict):
            part = [part]
        items.extend(part)

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
        cl_groups: dict[str, list] = {}
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


# ============================================================
# Tool 8: 다중회사 주요 재무지표
# ============================================================


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

    items: list = []
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
        part = data.get("list", [])
        if isinstance(part, dict):
            part = [part]
        items.extend(part)

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
        corp_groups: dict[str, dict[str, list]] = {}
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


# ============================================================
# Tool 9: 대량보유 상황보고
# ============================================================


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
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

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


# ============================================================
# Tool 10: 임원·주요주주 소유보고
# ============================================================


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
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

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


# ============================================================
# Tool 11: 정기보고서 주요정보 (27개 보고서 유형 - 레지스트리/디스패치)
# ============================================================


@mcp.tool()
async def get_periodic_report(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    report_type: str = "",
) -> str:
    """
    DART 정기보고서의 주요정보를 유형별로 조회합니다.

    27가지 보고서 유형 중 하나를 선택하여 해당 정보를 조회합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        bsns_year: 사업연도 (예: "2024", "2023")
        reprt_code: 보고서코드
            11013=1분기보고서, 11012=반기보고서,
            11014=3분기보고서, 11011=사업보고서(기본값)
        report_type: 보고서 유형 (아래 목록에서 선택, 필수)
            "증자감자현황" - 증자(감자) 현황
            "배당" - 배당에 관한 사항
            "자기주식취득처분" - 자기주식 취득 및 처분 현황
            "최대주주현황" - 최대주주 현황
            "최대주주변동" - 최대주주 변동 현황
            "소액주주" - 소액주주 현황
            "임원현황" - 임원 현황
            "직원현황" - 직원 현황
            "이사감사개인별보수" - 이사·감사의 개인별 보수 현황
            "이사감사전체보수" - 이사·감사 전체의 보수 현황
            "개인별보수지급" - 개인별 보수지급 금액(5억 이상 상위 5인)
            "타법인출자" - 타법인 출자 현황
            "채무증권발행" - 채무증권 발행실적
            "기업어음미상환" - 기업어음증권 미상환 잔액
            "단기사채미상환" - 단기사채 미상환 잔액
            "회사채미상환" - 회사채 미상환 잔액
            "신종자본증권미상환" - 신종자본증권 미상환 잔액
            "조건부자본증권미상환" - 조건부자본증권 미상환 잔액
            "회계감사인" - 회계감사인의 명칭 및 감사의견
            "감사용역체결" - 감사용역 체결 현황
            "비감사용역계약" - 회계감사인과의 비감사용역 계약체결 현황
            "사외이사변동" - 사외이사 및 그 변동 현황
            "미등기임원보수" - 미등기임원 보수 현황
            "이사감사보수승인금액" - 이사·감사의 보수현황(주총 승인금액)
            "이사감사보수유형별" - 이사·감사의 보수현황(보수지급금액 유형별)
            "공모자금사용" - 공모자금의 사용내역
            "사모자금사용" - 사모자금의 사용내역

    Returns:
        선택한 유형의 정기보고서 주요정보 (유형에 따라 반환 필드가 다름)
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."
    if not bsns_year or not bsns_year.strip():
        return "오류: 사업연도(bsns_year)를 입력해주세요."
    if not report_type or not report_type.strip():
        available = "\n".join(f"  - {k}" for k in PERIODIC_REPORT_REGISTRY)
        return f"오류: report_type을 입력해주세요.\n\n사용 가능한 report_type:\n{available}"

    report_type = report_type.strip()
    if report_type not in PERIODIC_REPORT_REGISTRY:
        available = "\n".join(f"  - {k}" for k in PERIODIC_REPORT_REGISTRY)
        return f"오류: 유효하지 않은 report_type입니다: \"{report_type}\"\n\n사용 가능한 report_type:\n{available}"

    endpoint = PERIODIC_REPORT_REGISTRY[report_type]
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

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)
        title = f"정기보고서 - {report_type} ({bsns_year}년 {reprt_nm})\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"


# ============================================================
# Tool 12: 주요사항보고서 (36개 이벤트 유형 - 레지스트리/디스패치)
# ============================================================


@mcp.tool()
async def get_major_event_report(
    corp_code: str,
    event_type: str = "",
    bgn_de: str = "",
    end_de: str = "",
) -> str:
    """
    DART 주요사항보고서를 이벤트 유형별로 조회합니다.

    36가지 이벤트 유형 중 하나를 선택하여 해당 보고서 내용을 조회합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리, 예: "00126380")
        event_type: 이벤트 유형 (아래 목록에서 선택, 필수)
            "자산양수도" - 자산양수도(주요자산 양수도 등)
            "부도발생" - 부도 발생
            "영업정지" - 영업 정지
            "회생절차" - 회생절차 개시신청
            "해산사유" - 해산사유 발생
            "유상증자결정" - 유상증자 결정
            "무상증자결정" - 무상증자 결정
            "유무상증자결정" - 유무상증자 결정
            "감자결정" - 감자 결정
            "관리절차개시" - 관리절차 개시
            "소송" - 소송 등
            "해외상장결정" - 해외 상장 결정
            "해외상장폐지결정" - 해외 상장폐지 결정
            "해외상장" - 해외 상장
            "해외상장폐지" - 해외 상장폐지
            "전환사채발행" - 전환사채권 발행 결정
            "신주인수권부사채발행" - 신주인수권부사채권 발행 결정
            "교환사채발행" - 교환사채권 발행 결정
            "관리절차중단" - 관리절차 중단
            "상각형조건부자본증권발행" - 상각형 조건부자본증권 발행 결정
            "자기주식취득결정" - 자기주식 취득 결정
            "자기주식처분결정" - 자기주식 처분 결정
            "자기주식신탁체결" - 자기주식취득 신탁계약 체결 결정
            "자기주식신탁해지" - 자기주식취득 신탁계약 해지 결정
            "영업양수결정" - 영업양수 결정
            "영업양도결정" - 영업양도 결정
            "유형자산양수" - 유형자산 양수 결정
            "유형자산양도" - 유형자산 양도 결정
            "타법인주식양수" - 타법인 주식 및 출자증권 양수 결정
            "타법인주식양도" - 타법인 주식 및 출자증권 양도 결정
            "사채권양수" - 사채권 양수 결정
            "사채권양도" - 사채권 양도 결정
            "회사합병" - 회사 합병 결정
            "회사분할" - 회사 분할 결정
            "회사분할합병" - 회사 분할합병 결정
            "주식교환이전" - 주식의 포괄적 교환·이전 결정
        bgn_de: 검색 시작일 YYYYMMDD (예: "20240101")
        end_de: 검색 종료일 YYYYMMDD (예: "20241231")

    Returns:
        선택한 유형의 주요사항보고서 정보 (유형에 따라 반환 필드가 다름)
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."
    if not event_type or not event_type.strip():
        available = "\n".join(f"  - {k}" for k in MAJOR_EVENT_REGISTRY)
        return f"오류: event_type을 입력해주세요.\n\n사용 가능한 event_type:\n{available}"

    event_type = event_type.strip()
    if event_type not in MAJOR_EVENT_REGISTRY:
        available = "\n".join(f"  - {k}" for k in MAJOR_EVENT_REGISTRY)
        return f"오류: 유효하지 않은 event_type입니다: \"{event_type}\"\n\n사용 가능한 event_type:\n{available}"

    endpoint = MAJOR_EVENT_REGISTRY[event_type]

    # bgn_de/end_de는 DART 필수값 → 생략 시 기본 범위 적용
    bgn_de, end_de = _default_date_range(bgn_de, end_de)
    params: dict = {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn_de,
        "end_de": end_de,
    }

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        items = data.get("list", [])

        if isinstance(items, dict):
            items = [items]

        date_range = ""
        if bgn_de or end_de:
            date_range = f" ({_format_date(bgn_de) if bgn_de else '~'} ~ {_format_date(end_de) if end_de else '~'})"

        title = f"주요사항보고서 - {event_type}{date_range}\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"


# ============================================================
# Tool 13: 공시서류 원본파일 다운로드
# ============================================================


@mcp.tool()
async def download_document(rcept_no: str, output_dir: str = ".") -> str:
    """
    공시서류 원본파일을 다운로드합니다.

    Parameters:
        rcept_no: 접수번호 (14자리, search_disclosures 결과에서 확인 가능)
        output_dir: 저장 디렉토리 (기본값: 현재 디렉토리)

    Returns:
        다운로드 결과 메시지 (파일 경로, 포함된 파일 목록)
    """
    if not rcept_no or not rcept_no.strip():
        return "오류: 접수번호(rcept_no)를 입력해주세요."

    data = await _fetch_dart_binary("document.xml", {"rcept_no": rcept_no.strip()})
    if isinstance(data, str):
        return data

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"{rcept_no.strip()}.zip")
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with zipfile.ZipFile(filepath) as z:
            file_list = z.namelist()
            lines = [
                "공시서류 원본파일 다운로드 완료",
                f"저장 경로: {os.path.abspath(filepath)}",
                f"파일 크기: {len(data):,} bytes",
                f"포함 파일 ({len(file_list)}개):",
            ]
            for fname in file_list:
                lines.append(f"  - {fname}")
            return "\n".join(lines)
    except Exception:
        return f"다운로드 완료: {os.path.abspath(filepath)} ({len(data):,} bytes)"


# ============================================================
# Tool 14: XBRL 재무제표 원본파일(DSD) 다운로드
# ============================================================


async def _resolve_rcept_no(corp_code: str, bsns_year: str, reprt_code: str) -> str:
    """
    고유번호+사업연도+보고서코드로 해당 정기보고서의 접수번호(rcept_no)를 조회합니다.
    fnlttXbrl.xml은 rcept_no를 요구하므로, 사용자 편의를 위해 list.json에서 역산합니다.

    보고서명의 기간표기 "(YYYY.MM)"를 파싱해 결산월과 무관하게 매칭합니다.
    - 사업보고서: 결산연도가 bsns_year인 보고서(12월 결산)를 우선하되, 없으면
      차년도 상반기 결산 보고서(비12월 결산, 예: 1월 결산 REIT의 "(YYYY+1.01)")도 포함.
    - 분기보고서: 회계연도 내 두 건(1분기/3분기)을 기간 순서로 구분(이른=1분기, 늦은=3분기).
    동일 기간의 정정본이 있으면 최신 접수본(정정본)을 선택합니다.
    성공 시 14자리 접수번호, 오류 시 "오류:..." 문자열, 미발견 시 "" 반환.
    """
    keyword_map = {
        "11011": "사업보고서",
        "11012": "반기보고서",
        "11013": "분기보고서",
        "11014": "분기보고서",
    }
    keyword = keyword_map.get(reprt_code, "보고서")
    try:
        by = int(bsns_year.strip())
    except (ValueError, AttributeError):
        return ""
    next_year = by + 1

    # 사업보고서는 결산 후(주로 다음 해) 제출 → 조회창을 차년도까지. 분기/반기는 당해 연도.
    if reprt_code == "11011":
        bgn, end = f"{by}0101", f"{next_year}1231"
    else:
        bgn, end = f"{by}0101", f"{by}1231"

    data = await _fetch_dart("list.json", {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn,
        "end_de": end,
        "pblntf_ty": "A",
        "page_count": "100",
        "sort": "date",
        "sort_mth": "desc",
    })
    if isinstance(data, str):
        # "오류:..."는 그대로 전달, "조회된 데이터 없음"류는 미발견("")으로 처리
        return data if data.startswith("오류") else ""

    items = data.get("list", [])
    if isinstance(items, dict):
        items = [items]

    # (결산연도, 결산월, 접수일자, 접수번호) 후보 수집
    cands: list[tuple[int, int, str, str]] = []
    for item in items:
        nm = item.get("report_nm", "")
        if keyword not in nm:
            continue
        m = re.search(r"\((\d{4})[.\-/](\d{2})\)", nm)
        if not m:
            continue
        py, pm = int(m.group(1)), int(m.group(2))
        if reprt_code == "11011":
            in_scope = (py == by) or (py == next_year and pm <= 6)
        else:
            in_scope = (py == by)
        if in_scope:
            cands.append((py, pm, item.get("rcept_dt", ""), item.get("rcept_no", "")))

    if not cands:
        return ""

    if reprt_code in ("11011", "11012"):
        # 결산기간이 가장 최근 + 최신 접수(정정본 우선)
        cands.sort(key=lambda x: (x[0], x[1], x[2]))
        return cands[-1][3]

    # 분기: 기간(년,월)별로 정정본 정리 후 1분기=이른 기간 / 3분기=늦은 기간
    by_period: dict[tuple[int, int], tuple[str, str]] = {}
    for py, pm, dt, rno in cands:
        key = (py, pm)
        if key not in by_period or dt > by_period[key][0]:
            by_period[key] = (dt, rno)
    ordered = [by_period[k][1] for k in sorted(by_period)]
    if reprt_code == "11013":
        return ordered[0]
    return ordered[-1]


@mcp.tool()
async def download_xbrl(
    corp_code: str = "",
    bsns_year: str = "",
    reprt_code: str = "11011",
    output_dir: str = ".",
    rcept_no: str = "",
) -> str:
    """
    XBRL 재무제표 원본파일(DSD)을 다운로드합니다.

    DART의 fnlttXbrl.xml API는 접수번호(rcept_no)를 필요로 합니다.
    rcept_no를 직접 지정하거나, corp_code+bsns_year+reprt_code로 자동 조회할 수 있습니다.

    Parameters:
        corp_code: DART 고유번호 (8자리) — rcept_no 미지정 시 필수
        bsns_year: 사업연도 (예: "2024") — rcept_no 미지정 시 필수
        reprt_code: 보고서코드 (11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서)
        output_dir: 저장 디렉토리 (기본값: 현재 디렉토리)
        rcept_no: 접수번호 (14자리). 지정 시 corp_code/bsns_year 대신 직접 사용.

    Returns:
        다운로드 결과 메시지 (파일 경로, 포함된 파일 목록)
    """
    rcept_no = rcept_no.strip() if rcept_no else ""
    if not rcept_no:
        if not corp_code or not corp_code.strip() or not bsns_year or not bsns_year.strip():
            return "오류: rcept_no, 또는 corp_code와 bsns_year를 입력해주세요."
        resolved = await _resolve_rcept_no(corp_code, bsns_year, reprt_code)
        if isinstance(resolved, str) and resolved.startswith("오류"):
            return resolved
        if not resolved:
            return (
                f"오류: 해당 보고서의 접수번호를 찾지 못했습니다 "
                f"(corp_code={corp_code}, bsns_year={bsns_year}, reprt_code={reprt_code}).\n"
                f"search_disclosures로 접수번호를 확인한 뒤 rcept_no로 직접 지정하세요."
            )
        rcept_no = resolved

    params = {
        "rcept_no": rcept_no,
        "reprt_code": reprt_code,
    }

    data = await _fetch_dart_binary("fnlttXbrl.xml", params)
    if isinstance(data, str):
        return data

    os.makedirs(output_dir, exist_ok=True)
    filename = f"xbrl_{rcept_no}.zip"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with zipfile.ZipFile(filepath) as z:
            file_list = z.namelist()
            lines = [
                "XBRL 재무제표 원본파일 다운로드 완료",
                f"저장 경로: {os.path.abspath(filepath)}",
                f"파일 크기: {len(data):,} bytes",
                f"포함 파일 ({len(file_list)}개):",
            ]
            for fname in file_list:
                lines.append(f"  - {fname}")
            return "\n".join(lines)
    except Exception:
        return f"다운로드 완료: {os.path.abspath(filepath)} ({len(data):,} bytes)"


# ============================================================
# Tool 15: XBRL 택사노미 재무제표 양식
# ============================================================


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


# ============================================================
# Tool 16: 증권신고서 주요정보 (6개 유형 - 레지스트리/디스패치)
# ============================================================


@mcp.tool()
async def get_securities_report(
    corp_code: str,
    report_type: str,
    bgn_de: str = "",
    end_de: str = "",
) -> str:
    """
    증권신고서 주요정보를 유형별로 조회합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리)
        report_type: 신고서 유형
            "지분증권" - 지분증권
            "채무증권" - 채무증권
            "증권예탁증권" - 증권예탁증권
            "합병" - 합병
            "주식의포괄적교환이전" - 주식의 포괄적 교환·이전
            "분할" - 분할
        bgn_de: 검색 시작일 YYYYMMDD (기본값: 빈문자열)
        end_de: 검색 종료일 YYYYMMDD (기본값: 빈문자열)

    Returns:
        증권신고서 주요정보
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."

    if not report_type or not report_type.strip():
        available = "\n".join(f"  - {k}" for k in SECURITIES_REGISTRATION_REGISTRY)
        return f"오류: report_type을 입력해주세요.\n\n사용 가능한 report_type:\n{available}"

    report_type = report_type.strip()
    if report_type not in SECURITIES_REGISTRATION_REGISTRY:
        available = "\n".join(f"  - {k}" for k in SECURITIES_REGISTRATION_REGISTRY)
        return f"오류: 올바른 신고서 유형을 입력해주세요.\n사용 가능한 유형: {available}"

    endpoint = SECURITIES_REGISTRATION_REGISTRY[report_type]
    # bgn_de/end_de는 DART 필수값 → 생략 시 기본 범위 적용
    bgn_de, end_de = _default_date_range(bgn_de, end_de)
    params: dict = {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn_de,
        "end_de": end_de,
    }

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        # group과 list 두 가지 응답 형식 처리
        groups = data.get("group", None)
        items = data.get("list", [])

        if groups:
            # group 형식: 각 그룹별로 제목과 항목 포맷팅
            lines = [
                "=" * 60,
                f"증권신고서 - {report_type}",
                f"고유번호: {corp_code}",
                "=" * 60,
            ]

            if isinstance(groups, dict):
                groups = [groups]

            for grp in groups:
                grp_title = grp.get("title", "")
                grp_items = grp.get("list", [])

                if grp_title:
                    lines.append(f"\n{'─' * 40}")
                    lines.append(f"  {grp_title}")
                    lines.append(f"{'─' * 40}")

                if isinstance(grp_items, dict):
                    grp_items = [grp_items]

                for idx, item in enumerate(grp_items):
                    if len(grp_items) > 1:
                        lines.append(f"\n  --- [{idx + 1}] ---")
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if key in ("status", "message", "crtfc_key"):
                                continue
                            str_val = str(value) if value is not None else "-"
                            if (key.endswith("_de") or key.endswith("_dt")) and len(str_val) == 8:
                                str_val = _format_date(str_val)
                            if any(kw in key for kw in ("amount", "amt", "_cnt", "_qy", "stkqy", "stkrt")):
                                str_val = _format_amount(str_val)
                            lines.append(f"    {key}: {str_val}")

            lines.append("\n" + "=" * 60)
            return "\n".join(lines)

        # list 형식: 기존 범용 포맷터 사용
        if isinstance(items, dict):
            items = [items]

        date_range = ""
        if bgn_de or end_de:
            date_range = f" ({_format_date(bgn_de) if bgn_de else '~'} ~ {_format_date(end_de) if end_de else '~'})"

        title = f"증권신고서 - {report_type}{date_range}\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"


# ============================================================
# 서버 실행
# ============================================================


if __name__ == "__main__":
    mcp.run()
