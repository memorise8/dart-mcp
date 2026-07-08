import re
from dataclasses import dataclass
from typing import Final

from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart, _fetch_dart_result
from dart_search_mcp.formatting import _default_date_range, _format_amount, _format_date, _format_generic_response
from dart_search_mcp.registries import MAJOR_EVENT_REGISTRY, PERIODIC_REPORT_REGISTRY, SECURITIES_REGISTRATION_REGISTRY
from dart_search_mcp.results import DartError, DartNoData, DartSuccess
from dart_search_mcp.types import DartRecord, QueryParams, records_from
from dart_search_mcp.urls import SOURCE_URL_TEMPLATE

# 정기보고서 유형(reprt_code) -> 사람이 읽는 라벨. `get_periodic_report`의 제목과
# `get_audit_report_structured`의 `business_year_label` 계산에서 함께 사용한다.
_REPRT_CODE_LABELS: Final[dict[str, str]] = {
    "11013": "1분기보고서",
    "11012": "반기보고서",
    "11014": "3분기보고서",
    "11011": "사업보고서",
}

# `PERIODIC_REPORT_REGISTRY`의 회계감사인 항목 키. `get_audit_report_structured`가
# 항상 이 항목만 조회하므로 registries.py의 문자열을 그대로 하드코딩하지 않고
# 이름으로 참조한다.
_AUDIT_REPORT_TYPE = "회계감사인"


@dataclass(frozen=True, slots=True)
class AuditReportRecord:
    """OpenDART 정기보고서 `회계감사인`(accnutAdtorNmNdAdtOpinion) 응답 한 건에
    대응하는 구조화된 감사보고서 사실(fact) 레코드.

    OpenDART 필드 매핑: adtor(감사인), adt_opinion(감사의견),
    adt_reprt_spcmnt_matter(감사보고서 특기사항), emphs_matter(강조사항등),
    core_adt_matter(핵심감사사항), stlm_dt(결산일).
    `reprt_code`/`business_year_label`은 응답 데이터가 아니라 조회에 사용한
    입력 파라미터(reprt_code, bsns_year)로부터 이 모듈이 채워 넣는다.
    """

    corp_code: str
    corp_name: str
    corp_cls: str
    bsns_year: str
    reprt_code: str
    business_year_label: str
    rcept_no: str
    auditor: str
    audit_opinion: str
    special_matter: str
    emphasis_matter: str
    core_audit_matter: str
    settlement_date: str
    source_url: str


@dataclass(frozen=True, slots=True)
class AuditReportResult:
    """`get_audit_report_structured`가 정상 조회에 성공했을 때의 결과.

    `no_data_message`는 DART가 status 013(`DartNoData`, "조회된 데이터 없음")으로
    응답한 경우에만 채워진다."""

    records: list[AuditReportRecord]
    no_data_message: str | None = None


@dataclass(frozen=True, slots=True)
class AuditReportError:
    """corp_code/bsns_year 누락 등 입력 검증 실패, 또는 DART API 오류.

    공개 문자열 도구(`get_periodic_report`)가 반환하는 오류 문자열과 달리,
    구조화 계층 호출자는 `isinstance` 체크만으로 성공/실패를 구분할 수 있다."""

    message: str


def _normalize_text(value: str) -> str:
    """OpenDART 응답 자유서술형 텍스트 값에 섞여 들어오는 개행/탭/중복 공백
    잡음을 단일 공백으로 정리한다 (예: "한영\n회계법인" -> "한영 회계법인").

    내부 공백을 전부 지우는 것이 아니라 하나의 공백으로 축약(collapse)하므로
    감사의견/핵심감사사항처럼 여러 단어로 구성된 문장의 단어 사이 공백은 그대로
    보존된다. 앞뒤 공백은 제거한다."""

    return re.sub(r"\s+", " ", value).strip()


def _to_audit_report_record(
    item: DartRecord, *, bsns_year: str, reprt_code: str, business_year_label: str
) -> AuditReportRecord:
    rcept_no = item.get("rcept_no", "")
    return AuditReportRecord(
        corp_code=item.get("corp_code", ""),
        corp_name=item.get("corp_name", ""),
        corp_cls=item.get("corp_cls", ""),
        bsns_year=bsns_year,
        reprt_code=reprt_code,
        business_year_label=business_year_label,
        rcept_no=rcept_no,
        auditor=_normalize_text(item.get("adtor", "")),
        audit_opinion=_normalize_text(item.get("adt_opinion", "")),
        special_matter=_normalize_text(item.get("adt_reprt_spcmnt_matter", "")),
        emphasis_matter=_normalize_text(item.get("emphs_matter", "")),
        core_audit_matter=_normalize_text(item.get("core_adt_matter", "")),
        settlement_date=_normalize_text(item.get("stlm_dt", "")),
        source_url=SOURCE_URL_TEMPLATE.format(rcept_no=rcept_no) if rcept_no else "",
    )


async def get_audit_report_structured(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> AuditReportResult | AuditReportError:
    """DART 정기보고서 `회계감사인`(accnutAdtorNmNdAdtOpinion) 항목을 구조화된
    감사보고서 사실(fact) 목록으로 조회한다.

    `get_periodic_report`와 달리 report_type을 입력받지 않는다 (항상
    회계감사인 항목만 조회하는 전용 추출기). corp_code/bsns_year가 비어 있으면
    DART를 호출하지 않고 `AuditReportError`를 반환한다. 응답에 완전히 동일한
    (모든 필드가 같은) 행이 있으면 첫 번째 항목만 남긴다. `AuditReportRecord`는
    frozen dataclass이므로 레코드 전체 동등성으로 비교한다 — 식별 필드만 보는
    얕은 키는 연결/별도 재무제표처럼 같은 rcept_no 아래 내용이 다른
    사실(fact)이 함께 보고되는 경우 그중 하나를 잘못 없애버릴 수 있다.
    """
    if not corp_code or not corp_code.strip():
        return AuditReportError(message="오류: 고유번호(corp_code)를 입력해주세요.")
    if not bsns_year or not bsns_year.strip():
        return AuditReportError(message="오류: 사업연도(bsns_year)를 입력해주세요.")

    endpoint = PERIODIC_REPORT_REGISTRY[_AUDIT_REPORT_TYPE]
    params: QueryParams = {
        "corp_code": corp_code.strip(),
        "bsns_year": bsns_year.strip(),
        "reprt_code": reprt_code,
    }

    try:
        result = await _fetch_dart_result(f"{endpoint}.json", params)

        if isinstance(result, DartError):
            return AuditReportError(message=result.message)
        if isinstance(result, DartNoData):
            return AuditReportResult(records=[], no_data_message=result.message)

        assert isinstance(result, DartSuccess)
        items = records_from(result.data.get("list", []))

        reprt_nm = _REPRT_CODE_LABELS.get(reprt_code, reprt_code)
        business_year_label = f"{bsns_year.strip()}년 {reprt_nm}"

        seen: set[AuditReportRecord] = set()
        deduped: list[AuditReportRecord] = []
        for item in items:
            record = _to_audit_report_record(
                item,
                bsns_year=bsns_year.strip(),
                reprt_code=reprt_code,
                business_year_label=business_year_label,
            )
            if record in seen:
                continue
            seen.add(record)
            deduped.append(record)

        return AuditReportResult(records=deduped)
    except Exception as e:
        return AuditReportError(message=f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}")


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

    params: QueryParams = {
        "corp_code": corp_code.strip(),
        "bsns_year": bsns_year.strip(),
        "reprt_code": reprt_code,
    }

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        reprt_nm = _REPRT_CODE_LABELS.get(reprt_code, reprt_code)
        title = f"정기보고서 - {report_type} ({bsns_year}년 {reprt_nm})\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"

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
    params: QueryParams = {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn_de,
        "end_de": end_de,
    }

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        items = records_from(data.get("list", []))

        date_range = ""
        if bgn_de or end_de:
            date_range = f" ({_format_date(bgn_de) if bgn_de else '~'} ~ {_format_date(end_de) if end_de else '~'})"

        title = f"주요사항보고서 - {event_type}{date_range}\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
