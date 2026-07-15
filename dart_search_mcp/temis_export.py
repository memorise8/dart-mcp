"""
TEMIS 호환 `DartTopicCase` JSON 생성기.

`dart_search_mcp.tools.reports.AuditReportRecord`(Task 5의 회계감사인 구조화
사실)을 temis `DART_TOPIC_CASES_PATH`가 그대로 읽어들이는 `DartTopicCase`
JSON 배열로 변환한다.

설계 원칙:
  - 이 모듈은 순수/결정적이다. 네트워크 호출도, `datetime.now()` 같은 시계
    부수 효과도 없다. `freshness_timestamp`는 호출자가 주입한 문자열을 그대로
    쓴다 (실제 "지금" 시각을 주입하는 일은 Task 8 CLI 계층의 책임이다).
  - temis의 `DartTopicCase` pydantic 스키마(`app/schemas/dart_topic_search.py`)를
    이 레포에서 직접 import하지 않는다 — 레포 간 결합과 (`sys.path` 조작 등)
    불안정한 의존을 피하기 위해서다. 대신 `DartTopicCaseRecord`가 그 스키마와
    필드명·타입을 정확히 미러링하고, 테스트가 엄격한 타입으로 그 계약을
    검증한다.
  - 토픽 태그는 작은 결정적 한국어 키워드 사전(`TOPIC_KEYWORDS`)에서 텍스트에
    나타나는 용어를 찾아 부여한다. 짧은 키워드가 더 긴 다른 용어의 일부로만
    나타나는 경우(예: "리스크" 안의 "리스", "보증금" 안의 "보증")는 오탐으로
    간주해 매칭하지 않는다 — `_TOPIC_KEYWORD_EXCLUSIONS`에 등록된 예외 목록이
    이를 판정한다(`_term_matches` 참고). 전수적인(exhaustive) 회계 토픽
    커버리지를 주장하지 않으며, 사전에 없는 주제는 태그가 비게 된다
    (`topic_tags == []`, `case_id`는 `general` slug를 쓴다).
  - `extraction_confidence`는 핵심 감사 필드(auditor, audit_opinion,
    core_audit_matter, emphasis_matter) 중 값이 채워진 필드의 비율이다
    (`_confidence_for` 참고). 빈 문자열과 "-"(값 없음 표시)는 "없음"으로
    취급한다. 이 값은 사실(fact)이 얼마나 완전하게 추출되었는지(필드
    완전성)를 나타낼 뿐, 실제 모델 기반 신뢰도나 태그 매칭 확신도가 아니다.
  - `case_id`는 오직 소스 값에서만 계산되는 결정적 문자열이다:
    `dart-<corp_code>-<fiscal_year>-<rcept_no>-<topic_slug>-<discriminator>`.
    `corp_code`와 `rcept_no`를 그대로 포함하므로 회사가 달라지면 같은
    토픽/연도라도 절대 충돌하지 않고(전역 고유), 배치 내 다른 사실이나
    입력 순서에 의존하지 않는다(순서 무관, 재생성해도 항상 동일). 같은
    보고서에서 같은 토픽으로 매칭되는 사실이 둘 이상일 수 있으므로,
    그 사실의 구별 내용(auditor/audit_opinion/special_matter/
    emphasis_matter/core_audit_matter)에 대한 sha256 해시 앞 8 hex 문자를
    `discriminator`로 덧붙여 여전히 고유하게 만든다(`_case_id_for` 참고).

접수번호(`rcept_no`)가 비어 있으면 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=`
같은 가짜 원문 링크를 만들 수 있으므로 해당 사실(fact)은 건너뛴다
(`TopicCaseSkipped`). 마찬가지로 `corp_code`가 비어 있거나 `bsns_year`를
정수(`fiscal_year`)로 해석할 수 없는 경우도 건너뛴다 — 스키마의 두 필드 모두
빈 값이 허용되지 않는 `str`/`int`이기 때문이다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from dart_search_mcp.tools.reports import AuditReportRecord
from dart_search_mcp.urls import SOURCE_URL_TEMPLATE

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

# 짧은 키워드가 실제로는 더 긴 다른 용어의 일부로만 나타날 때의 오탐(false
# positive)을 막기 위한 예외 목록. 키: 짧은 키워드, 값: 그 키워드가 접두어로
# 나타나는 더 긴 용어들의 튜플. 텍스트 안에서 짧은 키워드가 나타난 위치에
# 이 더 긴 용어 중 하나가 같은 위치에서 시작하면, 그 등장은 "포함된 것"으로
# 간주해 매칭에서 제외한다 — 짧은 키워드가 텍스트의 다른 위치에서 독립적으로
# 나타나면 그 등장은 여전히 매칭된다. 일반적인 가드 메커니즘이며, 재현된
# 두 쌍(리스/리스크, 보증/보증금) 외에도 항목을 추가할 수 있다.
_TOPIC_KEYWORD_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "리스": ("리스크",),
    "보증": ("보증금",),
}


@dataclass(frozen=True, slots=True)
class DartTopicCaseRecord:
    """temis `DartTopicCase` pydantic 스키마와 필드명/타입이 1:1로 대응하는
    이 레포의 불변 로컬 표현. temis 스키마를 직접 import하지 않기 위해
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


def _term_occurrence_indices(text: str, term: str) -> list[int]:
    """텍스트에서 `term`이 나타나는 모든 시작 인덱스(왼쪽에서 오른쪽 스캔,
    겹치는 등장도 각각 포함)."""
    indices: list[int] = []
    start = 0
    while True:
        idx = text.find(term, start)
        if idx == -1:
            break
        indices.append(idx)
        start = idx + 1
    return indices


def _term_matches(text: str, term: str) -> bool:
    """`term`이 텍스트에 "독립적으로" 나타나는지 판정한다(longest-match 가드).

    `term`의 모든 등장 위치를 훑어, `_TOPIC_KEYWORD_EXCLUSIONS`에 등록된 더 긴
    용어가 같은 위치에서 시작해 `term`을 포함하는 경우는 그 등장을 "더 긴
    용어의 일부"로 간주해 무시한다(예: "리스크" 안의 "리스", "보증금" 안의
    "보증"). 무시되지 않는 등장이 하나라도 있으면 매칭으로 판단한다."""
    excluding_terms = _TOPIC_KEYWORD_EXCLUSIONS.get(term, ())
    if not excluding_terms:
        return term in text
    for idx in _term_occurrence_indices(text, term):
        if not any(text.startswith(longer, idx) for longer in excluding_terms):
            return True
    return False


def _matched_topics(
    text: str, topic_keywords: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    """텍스트에 독립적으로 나타나는 (slug, 한국어 키워드) 쌍을 사전 순서
    그대로 반환한다. 더 긴 용어의 일부로만 나타나는 경우는 `_term_matches`가
    걸러낸다."""
    return [(slug, term) for slug, term in topic_keywords if _term_matches(text, term)]


def _bounded_snippet(text: str, keyword: str | None, *, radius: int = _SNIPPET_RADIUS) -> str:
    if keyword:
        idx = text.find(keyword)
        if idx != -1:
            start = max(0, idx - radius)
            end = min(len(text), idx + len(keyword) + radius)
            return text[start:end].strip()
    return text[:_FALLBACK_SNIPPET_LENGTH].strip()


def _is_field_present(value: str) -> bool:
    """빈 문자열과 "-"(값 없음을 뜻하는 자리표시자)를 "없음"으로 취급한다."""
    stripped = value.strip()
    return stripped != "" and stripped != "-"


def _confidence_for(fact: AuditReportRecord) -> float:
    """핵심 감사 필드 중 값이 채워진 필드의 비율을 반환한다.

    확인하는 필드는 `auditor`, `audit_opinion`, `core_audit_matter`,
    `emphasis_matter` 4개다. 각 필드는 빈 문자열이거나 "-"(값 없음 표시)면
    "없음"으로 취급된다(`_is_field_present`). `audit_opinion`은 추가로
    `"unknown"`(파서가 감사의견을 분류하지 못했다는 센티넬 — 실제 의견
    유형이 아니다)이면 마찬가지로 "없음"으로 취급한다: 미분류를 추출로
    과대계상하지 않기 위함이며, 다른 필드나 `"unknown"` 이외의 의견값에는
    영향이 없다. 반환값은 채워진 필드 수를 전체 필드 수로 나눈 비율이며,
    항상 [0.0, 1.0] 범위다(4개 모두 채워지면 1.0, 하나도 채워지지 않으면
    0.0). 이 값이 나타내는 것은 오직 "이 사실(fact)이 얼마나 완전하게
    추출되었는가"(필드 완전성)이며, 매칭된 토픽 키워드 개수나 실제 모델
    기반 신뢰도와는 무관하다."""
    opinion_present = _is_field_present(fact.audit_opinion) and fact.audit_opinion != "unknown"
    fields_present = (
        _is_field_present(fact.auditor),
        opinion_present,
        _is_field_present(fact.core_audit_matter),
        _is_field_present(fact.emphasis_matter),
    )
    present = sum(1 for value in fields_present if value)
    return present / len(fields_present)


def _case_id_discriminator(fact: AuditReportRecord) -> str:
    """fact의 구별 내용에 대한 sha256 해시의 앞 8 hex 문자.

    해시 입력은 `auditor|audit_opinion|special_matter|emphasis_matter|
    core_audit_matter`를 그 순서로 이어붙인 문자열이다. 같은 보고서
    (`rcept_no`)에서 같은 토픽으로 매칭되는 사실이 둘 이상일 때도 `case_id`가
    여전히 고유하도록 만드는 역할이다. 오직 이 fact 자신의 필드값에서만
    계산되므로 배치 내 다른 fact나 처리 순서와 무관하다(순서 무관,
    재생성해도 항상 동일)."""
    source = "|".join(
        (
            fact.auditor,
            fact.audit_opinion,
            fact.special_matter,
            fact.emphasis_matter,
            fact.core_audit_matter,
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]


def _case_id_for(corp_code: str, fiscal_year: int, rcept_no: str, topic_slug: str, fact: AuditReportRecord) -> str:
    """`dart-<corp_code>-<fiscal_year>-<rcept_no>-<topic_slug>-<discriminator>`.

    회사(`corp_code`)와 원본 보고서(`rcept_no`)를 그대로 포함하므로 다른
    회사가 같은 토픽/연도를 가져도 절대 충돌하지 않는다(전역 고유). 오직
    소스 값에서만 계산되므로 배치 내 처리 순서와 무관하며, 같은 입력으로
    재생성해도 항상 동일한 값을 낸다. `discriminator`는
    `_case_id_discriminator`가 계산한다."""
    discriminator = _case_id_discriminator(fact)
    return f"dart-{corp_code}-{fiscal_year}-{rcept_no}-{topic_slug}-{discriminator}"


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

    `case_id`는 오직 각 사실의 소스 값에서만 계산된다(`_case_id_for` 참고)
    — `corp_code`/`rcept_no`를 포함해 전역적으로 고유하고, 배치 내 다른
    사실이나 처리 순서에 의존하지 않으며, 재생성해도 항상 동일하다.
    반환되는 `records`는 유효한 사실만 담고 입력 순서를 보존한다.
    유효하지 않은 사실(빈 접수번호/고유번호, 정수로 해석되지 않는 사업연도)은
    `skipped`에 사유와 함께 담기고 `records`에는 나타나지 않는다.

    `freshness_timestamp`는 호출자가 주입하는 값이다 — 이 함수는 시계를
    직접 읽지 않는다(순수/결정적 코어를 유지하기 위함, Task 8 CLI가 실제
    "지금" 시각을 주입한다).

    `topic_keywords`는 기본값(`TOPIC_KEYWORDS`)을 오버라이드할 수 있게
    해주는 선택적 파라미터다 — 호출자가 추가/커스텀 토픽 키워드를 쓰고 싶을
    때를 위한 것이며, 기본 동작은 바뀌지 않는다.
    """
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

        primary_keyword = matched[0][1] if matched else None
        disclosure_snippet = _bounded_snippet(text, primary_keyword)

        records.append(
            DartTopicCaseRecord(
                case_id=_case_id_for(corp_code, fiscal_year, rcept_no, topic_slug, fact),
                company_identifier=corp_code,
                company_name=fact.corp_name,
                fiscal_year=fiscal_year,
                report_id=rcept_no,
                auditor=fact.auditor,
                topic_tags=topic_tags,
                disclosure_snippet=disclosure_snippet,
                source_url=SOURCE_URL_TEMPLATE.format(rcept_no=rcept_no),
                document_id=rcept_no,
                extraction_confidence=_confidence_for(fact),
                freshness_timestamp=freshness_timestamp,
            )
        )

    return records, skipped


def topic_cases_to_json(records: list[DartTopicCaseRecord]) -> str:
    """`DartTopicCaseRecord` 목록을 temis `DART_TOPIC_CASES_PATH`가 바로
    읽을 수 있는 JSON 배열 문자열로 직렬화한다."""
    return json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2)
