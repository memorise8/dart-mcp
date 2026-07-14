"""``ParsedAuditReport``(Task 1의 순수 XML 파서 산출물)를 기존
``dart_search_mcp.temis_export.convert_audit_reports_to_topic_cases``가 이미
소비하는 ``AuditReportRecord``(원래 OpenDART API용, Task 5)로 변환하는 어댑터.

이 모듈은 파서(``audit_xml_parser.py``)가 아니라 통합 글루 계층이다 —
``AuditReportRecord``를 얻기 위해 ``dart_search_mcp.tools.reports``를
import하며, 그 모듈이 전이적으로 import하는 ``config.py``의 import-time
``load_dotenv()`` 파일 접근을 허용한다(``audit_xml_parser.py``는 이 의존을
피하려고 로직을 인라인 복제했지만, 이 어댑터는 그럴 필요가 없다 — 애초에
``tools.reports``를 직접 참조하는 통합 계층이기 때문이다).

``to_audit_report_record``는 그 자체로는 순수 함수다(네트워크/파일시스템
접근 없음, 부수효과 없음) — 입력 dataclass의 필드만 읽어 새 dataclass를
만든다."""

from __future__ import annotations

from dart_search_mcp.audit_xml_parser import ParsedAuditReport
from dart_search_mcp.temis_export import (
    DartTopicCaseRecord,
    TopicCaseSkipped,
    convert_audit_reports_to_topic_cases,
)
from dart_search_mcp.tools.reports import AuditReportRecord


def to_audit_report_record(parsed: ParsedAuditReport) -> AuditReportRecord:
    """``ParsedAuditReport``(로컬 XML 사실) -> ``AuditReportRecord``(OpenDART
    API 형태와 동일한 구조).

    매핑 규칙:
      - corp_code/corp_name/corp_cls/rcept_no/source_url: 동일 이름 그대로.
      - bsns_year: ``str(parsed.fiscal_year)`` (fiscal_year가 None이면 "").
        빈 문자열이면 하류 ``_validate_fact``(temis_export.py)가 그 사실을
        정직하게 skip한다 — 의도된 동작이다(사업연도를 모르는 사실을
        조작된 값으로 채워넣지 않는다).
      - auditor: parsed.auditor 그대로.
      - audit_opinion: parsed.audit_opinion 그대로(한글 enum 값 또는
        "unknown").
      - core_audit_matter: parsed.kam_raw.
      - emphasis_matter: parsed.emphasis_raw.
      - special_matter: "" (파서가 특기사항을 별도 추출하지 않는다).
      - reprt_code: "" (F형 감사보고서엔 정기보고서코드가 없다. case_id
        계산에 쓰이지 않으므로 안전하다).
      - business_year_label: ``f"{fiscal_year}년 {category}"``
        (fiscal_year가 None이면 category만).
      - settlement_date: fiscal_year와 settlement_month가 모두 있으면
        ``f"{fiscal_year}.{settlement_month:02d}"``, 아니면 "".
    """

    fiscal_year = parsed.fiscal_year
    bsns_year = str(fiscal_year) if fiscal_year is not None else ""

    if fiscal_year is not None:
        business_year_label = f"{fiscal_year}년 {parsed.category}"
    else:
        business_year_label = parsed.category

    if fiscal_year is not None and parsed.settlement_month is not None:
        settlement_date = f"{fiscal_year}.{parsed.settlement_month:02d}"
    else:
        settlement_date = ""

    return AuditReportRecord(
        corp_code=parsed.corp_code,
        corp_name=parsed.corp_name,
        corp_cls=parsed.corp_cls,
        bsns_year=bsns_year,
        reprt_code="",
        business_year_label=business_year_label,
        rcept_no=parsed.rcept_no,
        auditor=parsed.auditor,
        audit_opinion=parsed.audit_opinion,
        special_matter="",
        emphasis_matter=parsed.emphasis_raw,
        core_audit_matter=parsed.kam_raw,
        settlement_date=settlement_date,
        source_url=parsed.source_url,
    )


def parsed_reports_to_topic_cases(
    parsed_list: list[ParsedAuditReport],
    *,
    freshness_timestamp: str,
) -> tuple[list[DartTopicCaseRecord], list[TopicCaseSkipped]]:
    """``to_audit_report_record``로 각 사실을 어댑팅한 뒤 기존
    ``convert_audit_reports_to_topic_cases``에 그대로 넘기는 편의 함수.

    Task 3/4(대량 CLI)가 파서 출력 목록을 finov2 ``DartTopicCase`` 레코드
    목록으로 한 번에 변환할 때 쓴다. 어댑팅 자체와 마찬가지로 순수/결정적이다
    — ``freshness_timestamp``는 호출자가 주입한 값을 그대로 전달할 뿐,
    이 함수가 직접 시계를 읽지 않는다."""

    facts = [to_audit_report_record(parsed) for parsed in parsed_list]
    return convert_audit_reports_to_topic_cases(facts, freshness_timestamp=freshness_timestamp)
