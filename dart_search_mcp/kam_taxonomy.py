"""KAM(핵심감사사항) 원문 LLM 태깅을 위한 고정 태소노미 + 프롬프트 빌더 + 응답 파서.

Phase ②(`.omo/plans/dart-kam-llm-tagging.md` Task 1)의 **순수 부분**만 담는다.
이 모듈은 LLM/네트워크/시계를 전혀 호출하지 않는다 — 전부 입력만으로 결정되는
순수 함수다(실제 엔드포인트 호출은 후속 태스크의 `dart tag-kam` CLI 책임).

`KAM_TAXONOMY`는 기존 `dart_search_mcp.temis_export.TOPIC_KEYWORDS`(14개
결정론 키워드 매칭 사전)와는 별개의, LLM 보조 분류를 위한 더 넓은 통제
어휘다. 두 사전은 서로 다른 목적(결정론 키워드 매칭 vs LLM 판단 보조)을
가지므로 태그 표현이 완전히 1:1로 대응하지 않을 수 있다(예:
`TOPIC_KEYWORDS`의 "풋옵션"/"금융부채"는 여기서 "금융부채·풋옵션" 한
태그로 합쳐진다).
"""

from __future__ import annotations

import json
import re

# (태그, 정의 1줄) 쌍의 통제 어휘. 순서가 곧 `parse_tag_response`가 반환하는
# `valid` 목록의 정렬 순서다(결정론·집계 일관성을 위해 텍스트 내 등장 순서나
# LLM 응답 순서가 아니라 이 사전 순서를 따른다).
KAM_TAXONOMY: tuple[tuple[str, str], ...] = (
    ("수익인식", "수익 인식 시점·방법(진행률, 투입법 등)의 판단과 추정의 불확실성"),
    ("손상", "영업권·유형자산·무형자산 등 자산손상 징후 판단 및 손상차손 측정"),
    ("공정가치평가", "관측 불가능한 투입변수에 기초한 금융상품 등의 공정가치 측정"),
    ("계속기업", "계속기업으로서의 존속능력에 대한 중요한 불확실성 평가"),
    ("우발부채·소송", "소송·분쟁 등 우발상황에 따른 부채 인식 여부 및 금액 추정"),
    ("재고자산평가", "재고자산의 순실현가능가치 평가 및 평가충당금 산정"),
    ("대손충당금", "채권의 회수가능성 평가 및 대손(기대신용손실) 충당금 추정"),
    ("특수관계자거래", "특수관계자와의 거래 식별, 조건의 적정성 및 공시 완전성"),
    ("리스", "리스이용자·제공자 회계처리, 사용권자산·리스부채 측정"),
    ("금융부채·풋옵션", "비지배지분 풋옵션 등 금융부채의 인식 및 측정"),
    ("계약부채", "선수금 등 계약부채의 인식 시점과 금액 측정"),
    ("보증·충당부채", "제품보증 등 충당부채의 인식요건 및 금액 추정"),
    ("이연법인세", "이연법인세자산의 실현가능성 및 세무 불확실성 평가"),
    ("사업결합·PPA", "사업결합 시 취득자산·부채의 공정가치 배분(PPA) 및 영업권 산정"),
    ("매출채권", "매출채권의 존재·발생 및 평가(회수가능성)에 대한 감사이슈"),
    ("개발비 자산화", "연구개발비의 자산화 요건 충족 여부 및 무형자산 인식"),
    ("종속·관계기업 투자평가", "종속기업·관계기업 투자주식의 손상 및 평가방법 적정성"),
    ("건설계약·진행률", "건설계약 등 진행기준 수익인식의 진행률 산정 근거"),
    ("기타", "위 목록 어디에도 명확히 대응하지 않으나 핵심감사사항으로 다루어진 사항"),
)

# 태그 문자열만 모은 집합. `KAM_TAXONOMY`와 항상 정합되어야 한다(테스트가 검증).
VALID_TAGS: frozenset[str] = frozenset(tag for tag, _ in KAM_TAXONOMY)

_SYSTEM_INSTRUCTION = (
    "다음은 회계감사보고서의 핵심감사사항(KAM) 원문입니다. 이 원문을 읽고, "
    "아래 목록에 있는 태그만 골라 JSON 배열로만 답하십시오. 해당하는 태그가 "
    "없으면 빈 배열 []을 반환하십시오. 목록 밖의 용어나 설명, 코드펜스 없이 "
    "순수 JSON 배열만 출력하십시오. 실제로 다루어진 주제만 태그로 고르고 "
    "과도하게 태그를 남발하지 마십시오.\n\n"
    "태그 목록:"
)

# 코드펜스(```json ... ``` 등)에서 내용을 추출하기 위한 패턴.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# 단일 JSON 객체가 태그 배열을 감싸고 있을 때 방어적으로 확인할 키 이름들.
_WRAPPER_KEYS = ("tags", "kam_tags", "labels", "result")

# 파싱 완전 실패 시 dropped에 담기는 마커의 최대 길이(원문 과다 노출 방지).
_FAILURE_MARKER_MAX_LEN = 200


def _taxonomy_listing() -> str:
    return "\n".join(f"- {tag}: {definition}" for tag, definition in KAM_TAXONOMY)


def build_tagging_prompt(kam_raw: str) -> list[dict[str, str]]:
    """`kam_raw`에 대한 OpenAI 호환 chat messages 목록을 만든다.

    순수 함수다 — 네트워크나 시계를 호출하지 않고, 입력(`kam_raw`)과
    `KAM_TAXONOMY`만으로 결정된다. system 메시지에 지시문과 태그 목록(태그 +
    정의)을 담고, user 메시지에 KAM 원문을 그대로 담는다.
    """
    system_content = f"{_SYSTEM_INSTRUCTION}\n{_taxonomy_listing()}"
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": kam_raw},
    ]


def _coerce_to_list(parsed: object) -> list[object] | None:
    """파싱된 JSON 값에서 태그 배열을 방어적으로 뽑아낸다.

    이미 리스트면 그대로, 단일 객체(dict)면 `_WRAPPER_KEYS` 중 하나에 담긴
    리스트를 찾아본다(예: `{"tags": [...]}`). 그 외(단일 문자열 등)는 원소
    하나짜리 리스트로 취급한다. 그래도 리스트를 뽑을 수 없으면 `None`."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in _WRAPPER_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        return None
    if isinstance(parsed, str):
        return [parsed]
    return None


def _extract_json_array(text: str) -> list[object] | None:
    """`text`에서 태그 배열로 해석 가능한 JSON을 방어적으로 추출한다.

    다음을 순서대로 시도한다: (1) 전체 텍스트를 그대로 JSON으로 파싱,
    (2) 코드펜스(```json ... ```) 내부, (3) 텍스트 내 첫 `[`부터 마지막 `]`
    까지의 부분 문자열. 무엇도 유효한 배열로 이어지지 않으면 `None`을
    반환한다(예외를 던지지 않는다)."""
    candidates: list[str] = []

    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fence_match = _FENCE_RE.search(text)
    if fence_match:
        fenced = fence_match.group(1).strip()
        if fenced:
            candidates.append(fenced)

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001 - 이 함수의 계약은 "never raise"다. 정상적인
            # 파싱 실패(JSONDecodeError)뿐 아니라 병리적 입력(예: 매우 깊게 중첩된
            # 대괄호 문자열)이 유발하는 RecursionError 등도 여기서 흡수해야
            # `parse_tag_response`가 어떤 입력에도 예외를 던지지 않는다.
            continue
        result = _coerce_to_list(parsed)
        if result is not None:
            return result

    return None


def parse_tag_response(text: str) -> tuple[list[str], list[str]]:
    """LLM 응답 텍스트를 `(valid_tags, dropped_tags)`로 파싱한다.

    - `VALID_TAGS`에 있는 원소는 `valid`로, 없는 원소는 `dropped`로 분리한다.
    - `valid`는 태소노미 정의 순서(`KAM_TAXONOMY` 순서)로 정렬되고 중복이
      제거된다. `dropped`는 처음 등장한 순서를 보존하며 중복이 제거된다.
    - JSON 배열을 전혀 찾을 수 없으면(완전 파싱 실패) `([], [marker])`를
      반환한다. `marker`는 원문 일부(최대 `_FAILURE_MARKER_MAX_LEN`자) 또는
      빈 응답을 나타내는 고정 문자열이다.
    - 예외를 던지지 않는다(방어적 파싱).
    """
    parsed = _extract_json_array(text)

    if parsed is None:
        marker = text.strip()[:_FAILURE_MARKER_MAX_LEN]
        if not marker:
            marker = "<empty response>"
        return [], [marker]

    valid_set: set[str] = set()
    dropped: list[str] = []
    dropped_seen: set[str] = set()

    for item in parsed:
        tag = item if isinstance(item, str) else str(item)
        tag = tag.strip()
        if not tag:
            continue
        if tag in VALID_TAGS:
            valid_set.add(tag)
        elif tag not in dropped_seen:
            dropped_seen.add(tag)
            dropped.append(tag)

    valid = [tag for tag, _ in KAM_TAXONOMY if tag in valid_set]
    return valid, dropped
