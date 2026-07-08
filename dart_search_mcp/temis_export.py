"""
TEMIS(finov2) 호환 `DartTopicCase` JSON 생성기.

`dart_search_mcp.tools.reports.AuditReportRecord`(Task 5의 회계감사인 구조화
사실)을 finov2 `DART_TOPIC_CASES_PATH`가 그대로 읽어들이는 `DartTopicCase`
JSON 배열로 변환한다.

설계 원칙:
  - 이 모듈은 순수/결정적이다. 네트워크 호출도, `datetime.now()` 같은 시계
    부수 효과도 없다. `freshness_timestamp`는 호출자가 주입한 문자열을 그대로
    쓴다 (실제 "지금" 시각을 주입하는 일은 Task 8 CLI 계층의 책임이다).
  - finov2의 `DartTopicCase` pydantic 스키마(`app/schemas/dart_topic_search.py`)를
    이 레포에서 직접 import하지 않는다 — 레포 간 결합과 (`sys.path` 조작 등)
    불안정한 의존을 피하기 위해서다. 대신 `DartTopicCaseRecord`가 그 스키마와
    필드명·타입을 정확히 미러링하고, 테스트가 엄격한 타입으로 그 계약을
    검증한다.
  - 토픽 태그는 작은 결정적 한국어 키워드 사전(`TOPIC_KEYWORDS`)에서 텍스트에
    나타나는 용어를 찾아 부여한다. 전수적인(exhaustive) 회계 토픽 커버리지를
    주장하지 않으며, 사전에 없는 주제는 태그가 비게 된다(`topic_tags == []`,
    `case_id`는 `general` slug를 쓴다).
  - `extraction_confidence`는 매칭된 태그 개수에 기반한 단순 휴리스틱이다.
    실제 모델 기반 신뢰도 점수가 아니다.
  - `dart_search_mcp.document_extraction.extract_snippets`(Task 7)는 ZIP
    바이트에서 스니펫을 뽑는 별도 책임(원문 파일 처리)을 갖는다. 이 모듈이
    다루는 `AuditReportRecord`는 이미 구조화된 문자열 필드이므로 ZIP을 열
    필요가 없어 그 함수를 재사용하지 않지만, 동일한 주의사항을 따른다:
    키워드는 앞뒤 공백이 없어야 substring 매칭이 정확하다
    (`TOPIC_KEYWORDS`의 모든 한국어 용어는 리터럴이라 이미 그렇다).

접수번호(`rcept_no`)가 비어 있으면 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=`
같은 가짜 원문 링크를 만들 수 있으므로 해당 사실(fact)은 건너뛴다
(`TopicCaseSkipped`). 마찬가지로 `corp_code`가 비어 있거나 `bsns_year`를
정수(`fiscal_year`)로 해석할 수 없는 경우도 건너뛴다 — 스키마의 두 필드 모두
빈 값이 허용되지 않는 `str`/`int`이기 때문이다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from dart_search_mcp.tools.reports import AuditReportRecord

_SOURCE_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# (topic slug, 한국어 키워드) 순서쌍의 튜플. 순서가 두 가지를 결정한다:
#   1. `case_id`에 쓰이는 slug — 텍스트에서 매칭된 항목 중 사전 순서상 가장
#      앞선 것의 slug를 쓴다.
#   2. `topic_tags`에 담기는 한국어 용어들의 순서 — 매칭된 항목을 이 사전
#      순서 그대로 나열한다(텍스트 내 등장 순서가 아니다).
# 작고 결정적인 예시 사전이며, 실무에서 자주 보이는 감사 이슈 일부만 다룬다.
# 전수적인 회계 토픽 커버리지를 주장하지 않는다.
TOPIC_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("put-option", "풋옵션"),
    ("financial-liability", "금융부채"),
    ("nci", "비지배지분"),
    ("revenue", "수익인식"),
    ("warranty", "보증"),
    ("contract-liability", "계약부채"),
    ("going-concern", "계속기업"),
    ("contingent-liability", "우발부채"),
    ("related-party", "특수관계자"),
    ("impairment", "손상"),
    ("fair-value", "공정가치"),
    ("lease", "리스"),
    ("inventory", "재고자산"),
    ("bad-debt", "대손"),
)

_DEFAULT_TOPIC_SLUG = "general"
_SNIPPET_RADIUS = 80
_FALLBACK_SNIPPET_LENGTH = 240


@dataclass(frozen=True, slots=True)
class DartTopicCaseRecord:
    """finov2 `DartTopicCase` pydantic 스키마와 필드명/타입이 1:1로 대응하는
    이 레포의 불변 로컬 표현. finov2 스키마를 직접 import하지 않기 위해
    이 모듈이 자체적으로 미러링한다 — 필드가 바뀌면 양쪽을 함께 갱신해야
    한다."""

    case_id: str
    company_identifier: str
    company_name: str
    fiscal_year: int
    report_id: str
    auditor: str
    topic_tags: list[str]
    disclosure_snippet: str
    source_url: str
    document_id: str
    extraction_confidence: float
    freshness_timestamp: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TopicCaseSkipped:
    """사실(fact) 하나가 유효한 `DartTopicCaseRecord`로 변환될 수 없을 때의
    이유. 접수번호가 비어 있으면 가짜 DART 원문 링크를 생성할 수 없으므로
    반드시 건너뛴다."""

    reason: str


def _matched_topics(
    text: str, topic_keywords: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    """텍스트에 나타나는 (slug, 한국어 키워드) 쌍을 사전 순서 그대로 반환한다."""
    return [(slug, term) for slug, term in topic_keywords if term in text]


def _bounded_snippet(text: str, keyword: str | None, *, radius: int = _SNIPPET_RADIUS) -> str:
    if keyword:
        idx = text.find(keyword)
        if idx != -1:
            start = max(0, idx - radius)
            end = min(len(text), idx + len(keyword) + radius)
            return text[start:end].strip()
    return text[:_FALLBACK_SNIPPET_LENGTH].strip()


def _confidence_for(matched: list[tuple[str, str]]) -> float:
    """매칭된 태그 개수에 기반한 단순/결정적 휴리스틱.

    실제 모델 기반 신뢰도가 아니다 — 매칭이 많을수록 신뢰도를 높게 두되
    1.0을 넘지 않는다. 매칭이 하나도 없으면 낮은 고정값을 반환한다."""
    if not matched:
        return 0.3
    return min(1.0, 0.5 + 0.15 * len(matched))


def _fact_text(fact: AuditReportRecord) -> str:
    return " ".join(
        part
        for part in (
            fact.audit_opinion,
            fact.special_matter,
            fact.emphasis_matter,
            fact.core_audit_matter,
        )
        if part
    )


def _validate_fact(fact: AuditReportRecord) -> tuple[str, str, int] | TopicCaseSkipped:
    """(rcept_no, corp_code, fiscal_year)를 반환하거나, 유효하지 않으면
    사유가 담긴 `TopicCaseSkipped`를 반환한다. 셋 다 앞뒤 공백을 제거한
    값으로 정규화된다."""
    rcept_no = (fact.rcept_no or "").strip()
    if not rcept_no:
        return TopicCaseSkipped(
            reason="빈 접수번호(rcept_no)로는 DART 원문 링크를 생성할 수 없어 건너뜁니다."
        )

    corp_code = (fact.corp_code or "").strip()
    if not corp_code:
        return TopicCaseSkipped(reason="빈 고유번호(corp_code)여서 건너뜁니다.")

    bsns_year = (fact.bsns_year or "").strip()
    try:
        fiscal_year = int(bsns_year)
    except ValueError:
        return TopicCaseSkipped(
            reason=f"사업연도(bsns_year)를 정수로 해석할 수 없어 건너뜁니다: {fact.bsns_year!r}"
        )

    return rcept_no, corp_code, fiscal_year


def convert_audit_reports_to_topic_cases(
    facts: list[AuditReportRecord],
    *,
    freshness_timestamp: str,
    topic_keywords: tuple[tuple[str, str], ...] = TOPIC_KEYWORDS,
) -> tuple[list[DartTopicCaseRecord], list[TopicCaseSkipped]]:
    """감사보고서 사실(fact) 목록을 `DartTopicCaseRecord` 목록으로 일괄 변환한다.

    `case_id`의 채번(sequence)은 (topic slug, fiscal_year) 조합별로 1부터
    독립적으로 매겨져, 반환된 배열 전체에서 `case_id`가 항상 고유하도록
    보장한다. 반환되는 `records`는 유효한 사실만 담고 입력 순서를 보존한다.
    유효하지 않은 사실(빈 접수번호/고유번호, 정수로 해석되지 않는 사업연도)은
    `skipped`에 사유와 함께 담기고 `records`에는 나타나지 않는다.

    `freshness_timestamp`는 호출자가 주입하는 값이다 — 이 함수는 시계를
    직접 읽지 않는다(순수/결정적 코어를 유지하기 위함, Task 8 CLI가 실제
    "지금" 시각을 주입한다).

    `topic_keywords`는 기본값(`TOPIC_KEYWORDS`)을 오버라이드할 수 있게
    해주는 선택적 파라미터다 — 호출자가 추가/커스텀 토픽 키워드를 쓰고 싶을
    때를 위한 것이며, 기본 동작은 바뀌지 않는다.
    """
    sequence_counters: dict[tuple[str, int], int] = {}
    records: list[DartTopicCaseRecord] = []
    skipped: list[TopicCaseSkipped] = []

    for fact in facts:
        validated = _validate_fact(fact)
        if isinstance(validated, TopicCaseSkipped):
            skipped.append(validated)
            continue

        rcept_no, corp_code, fiscal_year = validated

        text = _fact_text(fact)
        matched = _matched_topics(text, topic_keywords)
        topic_slug = matched[0][0] if matched else _DEFAULT_TOPIC_SLUG
        topic_tags = [term for _, term in matched]

        counter_key = (topic_slug, fiscal_year)
        sequence_counters[counter_key] = sequence_counters.get(counter_key, 0) + 1
        sequence = sequence_counters[counter_key]

        primary_keyword = matched[0][1] if matched else None
        disclosure_snippet = _bounded_snippet(text, primary_keyword)

        records.append(
            DartTopicCaseRecord(
                case_id=f"dart-{topic_slug}-{fiscal_year}-{sequence:03d}",
                company_identifier=corp_code,
                company_name=fact.corp_name,
                fiscal_year=fiscal_year,
                report_id=rcept_no,
                auditor=fact.auditor,
                topic_tags=topic_tags,
                disclosure_snippet=disclosure_snippet,
                source_url=_SOURCE_URL_TEMPLATE.format(rcept_no=rcept_no),
                document_id=rcept_no,
                extraction_confidence=_confidence_for(matched),
                freshness_timestamp=freshness_timestamp,
            )
        )

    return records, skipped


def topic_cases_to_json(records: list[DartTopicCaseRecord]) -> str:
    """`DartTopicCaseRecord` 목록을 finov2 `DART_TOPIC_CASES_PATH`가 바로
    읽을 수 있는 JSON 배열 문자열로 직렬화한다."""
    return json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2)
