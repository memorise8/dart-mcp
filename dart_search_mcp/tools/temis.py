"""
Task 8: opt-in TEMIS export CLI/MCP 표면.

Task 5(`get_audit_report_structured`)로 감사보고서 사실을 조회하고, Task 6
(`dart_search_mcp.temis_export`)로 finov2 `DartTopicCase` JSON 배열로 변환한
뒤, 이 모듈이 실제 출력 파일에 쓴다. finov2는 이 모듈이 만든 파일을
`DART_TOPIC_CASES_PATH`로 읽는다 — OpenDART를 직접 호출하지 않는다. 이 모듈이
그 운영 adapter 경계다.

설계 원칙:
  - Task 6 변환기(`convert_audit_reports_to_topic_cases`)는 순수/결정적이며
    시계를 직접 읽지 않는다. 실제 "지금" 시각(`freshness_timestamp`)을
    주입하는 책임은 이 모듈에 있다 (`_utc_now_iso`). 테스트에서만
    `freshness_timestamp`를 명시적으로 오버라이드할 수 있다.
  - `corp_code`가 canonical 키다(전역 제약). `corp_name`은 별도의 resolver
    단계(`dart_search_mcp.corp.resolve_corp_code`)를 거쳐 정확히 하나의
    `corp_code`로 해석된 뒤에만 사용된다. `corp_name`을 OpenDART 파라미터로
    직접 보내지 않는다. 모호하거나(둘 이상 매치) 해석 불가(매치 없음)면
    OpenDART를 호출하지 않고 오류를 반환한다 (패턴은
    `dart_search_mcp.tools.disclosures._resolve_single_corp_code`와 동일).
  - 파일 쓰기 시맨틱은 항상 OVERWRITE다: `output_path`를 매 호출마다 새
    JSON 배열로 완전히 교체한다(append하지 않음). Task 6 변환기가 보장하는
    `case_id` 유일성은 "한 번의 변환 호출" 범위에서만 유효하므로, 파일
    전체를 매번 새로 쓰는 것이 그 유일성 보장을 파일 단위로 지키는
    유일한 방법이다. 실패(입력 검증 실패, resolver 실패, DART API 오류) 시
    출력 파일은 전혀 건드리지 않는다 — 기존 파일이 있어도 그대로 남는다.
  - `corp.py`는 이 모듈보다 먼저 `server.py`에서 import되어 `search_corp_code`
    MCP tool을 이미 등록한 뒤이므로, 여기서 최상단에서 import해도
    `tests/test_public_surface.py`의 tool 등록 순서에 영향을 주지 않는다
    (`dart_search_mcp.tools.disclosures`와 달리 지연 import가 필요 없다 —
    이 모듈은 `server.py`에서 가장 마지막에 import되기 때문).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from dart_search_mcp.app import mcp
from dart_search_mcp.corp import (
    CorpLoadError,
    CorpNameAmbiguous,
    CorpNameNotFound,
    CorpValidationError,
    resolve_single_corp_code,
)
from dart_search_mcp.temis_export import TOPIC_KEYWORDS, convert_audit_reports_to_topic_cases, topic_cases_to_json
from dart_search_mcp.tools.reports import AuditReportError, get_audit_report_structured


@dataclass(frozen=True, slots=True)
class TemisExportError:
    """입력 검증 실패, corp_name 해석 실패(미해결/모호), 또는 DART API 오류.

    이 경우 출력 파일은 전혀 쓰지 않는다(기존 파일이 있어도 손대지 않는다)."""

    message: str


@dataclass(frozen=True, slots=True)
class TemisExportOutcome:
    """`export_temis_topic_cases_core`가 output_path에 성공적으로 쓴 결과."""

    output_path: str
    record_count: int
    skipped_count: int
    message: str


def _utc_now_iso() -> str:
    """UTC now를 ISO-8601(`...Z`) 문자열로 반환한다.

    Task 6 변환기는 순수 함수라 시계를 직접 읽지 않으므로, 실제 "지금" 시각을
    주입하는 책임은 이 CLI/MCP adapter 계층에 있다."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_extra_topic_keywords(raw: str) -> tuple[tuple[str, str], ...] | TemisExportError:
    """`"slug:용어,slug2:용어2"` 형식의 문자열을 (slug, 용어) 튜플 목록으로
    파싱한다. 빈 문자열이면 빈 튜플(기본 TOPIC_KEYWORDS만 사용)을 반환한다.

    각 항목은 반드시 `:`로 slug와 용어를 구분해야 하며, 둘 다 비어 있지
    않아야 한다. 형식이 올바르지 않으면 `TemisExportError`를 반환한다
    (DART 호출 전에 검증되므로 출력 파일은 쓰이지 않는다)."""
    raw = (raw or "").strip()
    if not raw:
        return ()

    pairs: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            return TemisExportError(
                message=f'오류: 추가 토픽 키워드 형식이 올바르지 않습니다 ("slug:용어" 형식 필요): {chunk!r}'
            )
        slug, _, term = chunk.partition(":")
        slug = slug.strip()
        term = term.strip()
        if not slug or not term:
            return TemisExportError(
                message=f'오류: 추가 토픽 키워드 형식이 올바르지 않습니다 ("slug:용어" 형식 필요): {chunk!r}'
            )
        pairs.append((slug, term))

    return tuple(pairs)


async def _resolve_export_corp_code(corp_code: str, corp_name: str) -> str | TemisExportError:
    """corp_code 또는 corp_name 중 하나를 정확히 하나의 corp_code로 해석한다.

    corp_code가 주어지면 그대로 사용한다(공백 제거). corp_code가 없고
    corp_name만 주어지면 `resolve_corp_code`로 exact -> (prefix+contains)
    순서로 정확히 하나의 후보를 찾는다. 후보가 없으면(unresolved) 또는
    둘 이상이면(ambiguous) `TemisExportError`를 반환하고 OpenDART
    `list.json`/감사보고서 조회를 전혀 호출하지 않는다. 둘 다 비어 있으면
    바로 `TemisExportError`를 반환한다(네트워크 호출 없음)."""
    corp_code = (corp_code or "").strip()
    corp_name = (corp_name or "").strip()

    if corp_code:
        return corp_code

    if not corp_name:
        return TemisExportError(message="오류: corp_code 또는 회사명(corp_name/--corp)을 입력해주세요.")

    resolution = await resolve_single_corp_code(corp_name)

    if isinstance(resolution, str):
        return resolution

    if isinstance(resolution, (CorpValidationError, CorpLoadError)):
        return TemisExportError(message=resolution.message)

    if isinstance(resolution, CorpNameNotFound):
        return TemisExportError(message=f"오류: 검색 결과가 없습니다.\n검색어: {resolution.corp_name}")

    assert isinstance(resolution, CorpNameAmbiguous)

    lines = [
        f'오류: 회사명 "{resolution.corp_name}"에 해당하는 회사가 여러 건입니다. '
        "corp_code(--code)를 지정해 다시 시도해주세요.",
    ]
    for candidate in resolution.candidates:
        lines.append(f"  - {candidate.corp_name} (corp_code={candidate.corp_code})")

    return TemisExportError(message="\n".join(lines))


async def export_temis_topic_cases_core(
    *,
    bsns_year: str,
    output_path: str,
    corp_code: str = "",
    corp_name: str = "",
    reprt_code: str = "11011",
    extra_keywords: str = "",
    freshness_timestamp: str | None = None,
) -> TemisExportOutcome | TemisExportError:
    """감사보고서 사실(회계감사인)을 TEMIS `DartTopicCase` JSON 배열로 변환해
    `output_path`에 쓴다. CLI(`temis-topic-cases`)와 MCP tool
    (`export_temis_topic_cases`)이 공유하는 core 구현이다.

    실패 시(corp_code/corp_name 둘 다 없음, corp_name 미해결/모호,
    bsns_year 누락, 잘못된 extra_keywords 형식, DART API 오류) `output_path`는
    전혀 쓰지 않는다.

    성공 시 `output_path`는 항상 덮어쓴다(overwrite) — 기존 내용에
    append하지 않는다.
    """
    resolved_corp_code = await _resolve_export_corp_code(corp_code, corp_name)
    if isinstance(resolved_corp_code, TemisExportError):
        return resolved_corp_code

    if not bsns_year or not bsns_year.strip():
        return TemisExportError(message="오류: 사업연도(bsns_year)를 입력해주세요.")

    if not output_path or not output_path.strip():
        return TemisExportError(message="오류: 출력 경로(output_path)를 입력해주세요.")

    parsed_extra_keywords = _parse_extra_topic_keywords(extra_keywords)
    if isinstance(parsed_extra_keywords, TemisExportError):
        return parsed_extra_keywords

    topic_keywords = TOPIC_KEYWORDS + parsed_extra_keywords if parsed_extra_keywords else TOPIC_KEYWORDS

    audit_outcome = await get_audit_report_structured(resolved_corp_code, bsns_year, reprt_code)
    if isinstance(audit_outcome, AuditReportError):
        return TemisExportError(message=audit_outcome.message)

    resolved_freshness_timestamp = freshness_timestamp or _utc_now_iso()

    records, skipped = convert_audit_reports_to_topic_cases(
        audit_outcome.records,
        freshness_timestamp=resolved_freshness_timestamp,
        topic_keywords=topic_keywords,
    )

    raw_json = topic_cases_to_json(records)

    # OVERWRITE 시맨틱: "w" 모드는 기존 파일 내용을 완전히 교체한다(append 아님).
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(raw_json)

    message = f"TEMIS 토픽 케이스 {len(records)}건을 {output_path}에 저장했습니다."
    if skipped:
        message += f" ({len(skipped)}건은 유효하지 않아 건너뛰었습니다.)"

    return TemisExportOutcome(
        output_path=output_path,
        record_count=len(records),
        skipped_count=len(skipped),
        message=message,
    )


@mcp.tool()
async def export_temis_topic_cases(
    bsns_year: str,
    output_path: str,
    corp_code: str = "",
    corp_name: str = "",
    reprt_code: str = "11011",
    extra_keywords: str = "",
) -> str:
    """
    감사보고서 사실(회계감사인)을 TEMIS(finov2) `DartTopicCase` JSON 배열로 변환해 output_path 파일에 씁니다.

    opt-in 운영 adapter 경계입니다. finov2는 OpenDART를 직접 호출하지 않습니다.

    이 도구의 산출물은 항상 **회사 1건** 단위입니다. finov2 쪽 소비 방식은
    두 단계입니다: (1) 현재는 `DART_TOPIC_CASES_PATH` 환경변수로 이 파일을
    직접 읽는 파일 기반 모드이며, 파일 하나에는 회사 1건만 담깁니다. (2)
    finov2 쪽에서 별도로 진행 중인 목표 모드는 각 회사별 산출물을
    `case_id`(corp_code/rcept_no를 포함해 전역 고유·결정적인 upsert 키)로
    DB에 import해 여러 회사를 누적하는 방식이며, 이 경우
    `DART_TOPIC_CASES_PATH`는 finov2가 읽는 런타임 파일이 아니라 import
    배치의 입력 파일이 됩니다 — 이 DB import는 finov2 쪽 작업으로, 이
    도구는 구현하거나 완료를 보장하지 않습니다.

    ⚠️ output_path는 항상 덮어씁니다(overwrite) — 기존 파일에 append하지
    않습니다. 실패 시(corp_code/corp_name 둘 다 없음, corp_name이 여러 회사와
    매치되거나 해석되지 않음, bsns_year 누락, DART API 오류) output_path는
    전혀 쓰지 않습니다.

    Parameters:
        bsns_year: 사업연도 (예: "2024")
        output_path: TEMIS DartTopicCase JSON 배열을 쓸 출력 파일 경로
        corp_code: DART 고유번호 (8자리). corp_name 대신 사용합니다
            (corp_code/corp_name 둘 다 비어 있으면 오류이며 파일을 쓰지
            않습니다)
        corp_name: 회사명. corp_code가 없을 때만 사용되며 정확히 하나의
            회사로 해석되어야 합니다 (여러 회사와 매치되거나(모호) 결과가
            없으면(미해결) 오류이며 파일을 쓰지 않습니다)
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기,
            11014=3분기, 기본값 11011)
        extra_keywords: 기본 토픽 키워드 사전에 추가할 항목,
            `"slug:용어,slug2:용어2"` 형식 (선택, 생략 시 기본 사전만 사용)

    Returns:
        저장 결과 메시지 (성공 시 저장된 건수와 경로, 실패 시 "오류: ..." 메시지)
    """
    outcome = await export_temis_topic_cases_core(
        bsns_year=bsns_year,
        output_path=output_path,
        corp_code=corp_code,
        corp_name=corp_name,
        reprt_code=reprt_code,
        extra_keywords=extra_keywords,
    )
    return outcome.message
