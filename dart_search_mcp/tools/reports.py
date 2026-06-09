from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart
from dart_search_mcp.formatting import _default_date_range, _format_amount, _format_date, _format_generic_response
from dart_search_mcp.registries import MAJOR_EVENT_REGISTRY, PERIODIC_REPORT_REGISTRY, SECURITIES_REGISTRATION_REGISTRY
from dart_search_mcp.types import QueryParams, records_from


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

        reprt_nm = reprt_code_map.get(reprt_code, reprt_code)
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
