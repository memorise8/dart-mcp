from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dart_search_mcp.app import mcp

from dart_search_mcp.client import _fetch_dart, _fetch_dart_result

from dart_search_mcp.formatting import _format_date
from dart_search_mcp.results import DartError, DartNoData, DartSuccess
from dart_search_mcp.types import DartRecord, QueryParams, records_from

if TYPE_CHECKING:
    # `dart_search_mcp.corp`는 여기서 런타임에 import하지 않는다 (아래 함수
    # 안에서 지연 import). corp.py는 모듈 최상단에서 `@mcp.tool()`로
    # `search_corp_code`를 등록하는데, 만약 이 모듈이 corp.py를 최상단에서
    # import하면 `server.py`가 이 모듈을 먼저 import할 때 `search_corp_code`가
    # `search_disclosures`/`get_company_info`보다 먼저 등록되어
    # tests/test_public_surface.py의 MCP 도구 등록 순서 검증이 깨진다.
    from dart_search_mcp.corp import CorpRecord

_SOURCE_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


@dataclass(frozen=True, slots=True)
class DisclosureRecord:
    """OpenDART `list.json` 응답 한 건에 대응하는 구조화된 공시 레코드."""

    report_name: str
    rcept_no: str
    rcept_dt: str
    corp_code: str
    corp_name: str
    stock_code: str
    filing_type: str
    source_url: str
    flr_nm: str = ""
    remark: str = ""


@dataclass(frozen=True, slots=True)
class DisclosureSearchResult:
    """`search_disclosures_structured`가 정상 조회에 성공했을 때의 결과."""

    records: list[DisclosureRecord]
    total_count: int
    total_page: int
    page_no: int


@dataclass(frozen=True, slots=True)
class DisclosureSearchError:
    """corp_name 검증 실패, corp_name 조회 결과 없음, DART API 오류 등."""

    message: str


@dataclass(frozen=True, slots=True)
class DisclosureAmbiguousCompanyError:
    """corp_name이 둘 이상의 회사와 매치되어 어떤 corp_code를 쓸지 결정할 수
    없는 경우. `list.json`은 호출하지 않고 후보 목록만 담아 반환한다."""

    message: str
    candidates: list["CorpRecord"] = field(default_factory=list)


type DisclosureSearchOutcome = DisclosureSearchResult | DisclosureSearchError | DisclosureAmbiguousCompanyError


def _to_disclosure_record(item: DartRecord) -> DisclosureRecord:
    rcept_no = item.get("rcept_no", "")
    return DisclosureRecord(
        report_name=item.get("report_nm", ""),
        rcept_no=rcept_no,
        rcept_dt=item.get("rcept_dt", ""),
        corp_code=item.get("corp_code", ""),
        corp_name=item.get("corp_name", ""),
        stock_code=item.get("stock_code", ""),
        filing_type=item.get("corp_cls", ""),
        source_url=_SOURCE_URL_TEMPLATE.format(rcept_no=rcept_no) if rcept_no else "",
        flr_nm=item.get("flr_nm", ""),
        remark=item.get("rm", ""),
    )


async def _resolve_single_corp_code(
    corp_name: str,
) -> str | DisclosureSearchError | DisclosureAmbiguousCompanyError:
    """회사명을 정확히 하나의 corp_code로 해석한다.

    exact 매치가 정확히 하나면 그것을 사용한다. exact 매치가 없고
    prefix+contains를 합친 후보가 정확히 하나면 그것을 사용한다. 그 외
    (후보가 여럿이거나, exact가 여럿인 경우)에는 `list.json`을 전혀
    호출하지 않고 결정적인 "선택하세요" 오류(`DisclosureAmbiguousCompanyError`)를
    반환한다. 후보가 하나도 없으면 `DisclosureSearchError`를 반환한다.
    """
    # 지연 import: 모듈 최상단에서 import하면 `dart_search_mcp.corp`의
    # `@mcp.tool()` 등록이 이 모듈보다 먼저 실행되어 MCP 도구 등록 순서가
    # 바뀐다 (파일 상단 TYPE_CHECKING 블록의 설명 참고).
    from dart_search_mcp.corp import CorpLoadError, CorpMatches, CorpValidationError, resolve_corp_code

    resolution = await resolve_corp_code(corp_name)

    if isinstance(resolution, (CorpValidationError, CorpLoadError)):
        return DisclosureSearchError(message=resolution.message)

    assert isinstance(resolution, CorpMatches)

    if len(resolution.exact) == 1:
        return resolution.exact[0].corp_code

    candidates = resolution.exact if resolution.exact else [*resolution.prefix, *resolution.contains]

    if not candidates:
        return DisclosureSearchError(message=f"검색 결과가 없습니다.\n검색어: {corp_name}")

    if len(candidates) == 1:
        return candidates[0].corp_code

    lines = [
        f'오류: 회사명 "{corp_name}"에 해당하는 회사가 여러 건입니다. '
        "corp_code를 지정해 다시 조회해주세요.",
    ]
    for candidate in candidates:
        lines.append(f"  - {candidate.corp_name} (corp_code={candidate.corp_code})")

    return DisclosureAmbiguousCompanyError(message="\n".join(lines), candidates=candidates)


async def search_disclosures_structured(
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
) -> DisclosureSearchOutcome:
    """`corp_code`를 1차 키로 하는 구조화된 공시 검색.

    `corp_code`가 주어지면 그대로 사용해 `list.json`을 호출한다. `corp_code`
    없이 `corp_name`만 주어지면 `resolve_corp_code`로 먼저 정확히 하나의
    `corp_code`로 해석한 뒤 그 `corp_code`로만 `list.json`을 호출한다.
    `corp_name`은 어떤 경우에도 `list.json` 요청 파라미터로 전송하지 않는다.
    `corp_name`이 둘 이상의 회사와 매치되면(모호하면) `list.json`을 전혀
    호출하지 않고 `DisclosureAmbiguousCompanyError`를 반환한다.
    `corp_name`/`corp_code`를 둘 다 생략하면 기존과 동일하게 필터 없이
    전체 공시를 조회한다.
    """
    resolved_corp_code = corp_code.strip()

    if not resolved_corp_code and corp_name.strip():
        resolution = await _resolve_single_corp_code(corp_name)
        if isinstance(resolution, (DisclosureSearchError, DisclosureAmbiguousCompanyError)):
            return resolution
        resolved_corp_code = resolution

    params: QueryParams = {
        "page_no": str(page_no),
        "page_count": str(min(page_count, 100)),
        "sort": sort,
        "sort_mth": sort_mth,
    }
    if resolved_corp_code:
        params["corp_code"] = resolved_corp_code
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

    try:
        result = await _fetch_dart_result("list.json", params)

        if isinstance(result, DartError):
            return DisclosureSearchError(message=result.message)
        if isinstance(result, DartNoData):
            return DisclosureSearchResult(records=[], total_count=0, total_page=1, page_no=page_no)

        assert isinstance(result, DartSuccess)
        data = result.data
        items = records_from(data.get("list", []))
        records = [_to_disclosure_record(item) for item in items]
        total_count = int(str(data.get("total_count", 0)) or "0")
        total_page = int(str(data.get("total_page", 1)) or "1")

        return DisclosureSearchResult(
            records=records, total_count=total_count, total_page=total_page, page_no=page_no
        )
    except Exception as e:
        return DisclosureSearchError(message=f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}")


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
        corp_name: 회사명 (예: "삼성전자", "카카오"). corp_code가 없으면
            먼저 고유번호로 해석한 뒤 그 고유번호로만 조회합니다
            (OpenDART에는 회사명으로 직접 필터링하는 파라미터가 없습니다).
            회사명이 여러 회사와 매치되면 후보 목록을 보여주는 오류를
            반환하니, corp_code를 지정해 다시 호출하세요.
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
    outcome = await search_disclosures_structured(
        corp_name=corp_name,
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        last_reprt_at=last_reprt_at,
        pblntf_ty=pblntf_ty,
        pblntf_detail_ty=pblntf_detail_ty,
        corp_cls=corp_cls,
        sort=sort,
        sort_mth=sort_mth,
        page_no=page_no,
        page_count=page_count,
    )

    if isinstance(outcome, (DisclosureSearchError, DisclosureAmbiguousCompanyError)):
        return outcome.message

    search_desc = corp_name if corp_name else "전체"

    if not outcome.records:
        return f"검색 결과가 없습니다.\n검색 조건: {search_desc}"

    lines = [
        f"공시 검색 결과 (검색: \"{search_desc}\", {page_no}/{outcome.total_page}페이지, 총 {outcome.total_count}건)",
        "=" * 60,
    ]

    for i, item in enumerate(outcome.records, start=1):
        lines.append(f"\n{i}. {item.report_name}")
        corp_parts = [item.corp_name]
        if item.stock_code:
            corp_parts.append(f"({item.stock_code})")
        lines.append(f"   회사: {' '.join(corp_parts)}")
        if item.flr_nm and item.flr_nm != item.corp_name:
            lines.append(f"   제출인: {item.flr_nm}")
        lines.append(f"   접수일자: {_format_date(item.rcept_dt)}")
        if item.corp_code:
            lines.append(f"   고유번호: {item.corp_code}")
        if item.rcept_no:
            lines.append(f"   접수번호: {item.rcept_no}")
        if item.remark:
            lines.append(f"   비고: {item.remark}")

    lines.append("\n" + "=" * 60)
    if page_no < outcome.total_page:
        lines.append(f"다음 페이지: search_disclosures(corp_name=\"{corp_name}\", page_no={page_no + 1})")

    return "\n".join(lines)

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
