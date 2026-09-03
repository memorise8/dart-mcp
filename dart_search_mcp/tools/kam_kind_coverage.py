"""`kam_items.jsonl`(수집기 산출물, 3,543행)에서 분류 규칙이 정형 문구를
얼마나 놓치는지 **측정**하는 자립 스크립트.

finov2 `.superpowers/sdd/2026-09-02-dart-chat-ui/BRIEF-A-kam-quality.md`
작업② — 고치는 게 아니라 재는 것이다. `classify_item_kind`(`kam_split.py`)
에 패턴을 추가하지 않는다. 이미 분류된 `item_kind`를 그대로 두고, 제목이
여러 공시에서 반복되는지만 센다. 실제 KAM 도 반복된다(「수익인식」·「재고
자산의 평가」 등)는 게 브리프의 경고라 자동으로 정형 문구라고 판정하지
않는다 — 사람이 볼 목록을 내는 것이 이 스크립트의 전부다.

## 측정 방법

(가) `title` 앞 40자를 공백 제거해 정규화한 값을 그룹 키로 쓴다. 2개 이상의
서로 다른 공시(`rcept_no`)에서 나타나는 그룹만 "반복 제목"으로 본다.
그룹의 항목 수(빈도) 내림차순으로 상위 40개를 뽑는다.

(나) 상위 40개 각각에 대해, 그 그룹에 속한 항목들의 `body`(title 은 40자로
잘려 있어 "별도의 의견을 제공하지 않습니다"처럼 긴 정형 어구가 다 담기지
않는다 — 그래서 body 전체를 본다) 안에 아래 어휘 중 하나라도 있으면
"정형 문구 의심"으로 표시한다(자동 판정이 아니라 표시만):

    우리는 / 핵심감사사항은 / 커뮤니케이션 / 전문가적 판단 /
    별도의 의견을 제공하지 않습니다

공백 유무 변형(`kam_split.py`가 이미 겪은 문제)에 대비해 어휘·본문 모두
공백을 모두 제거한 뒤 부분 문자열로 비교한다.

## 게이트 ② 숫자의 범위 — 지시서와 다르게 정한 것

- "반복 제목(2개 이상 공시) 종류 수" · "그중 item_kind='kam'인 종류 수"는
  **전체 3,543행 기준**이다(상위 40개로 자르지 않은 전체 반복 제목 종류
  수). "item_kind='kam'인 종류"는 그룹 내 모든 항목의 item_kind 가 전부
  `kam`인 경우로 정의한다(실측 결과 혼합 종류 그룹은 0개였다 — 아래 리포트
  참조).
- "정형 문구 어휘를 포함한 종류 수" · "그 종류들이 덮는 항목 수 합계"는
  브리프 (나)가 명시한 대로 **상위 40개 안에서만** 센다. 상위 40개 밖에도
  정형 문구 의심 종류가 더 있을 수 있으나, 이 스크립트는 지시서 (나) 문구
  ("(가)의 상위 40개 중")를 그대로 따른다.

사용:
    uv run python -m dart_search_mcp.tools.kam_kind_coverage \
        --items dart_collected/kam_items.jsonl \
        --out dart_collected/kam_kind_coverage.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

_TITLE_PREFIX_LEN: Final[int] = 40
_TOP_N: Final[int] = 40
_MIN_DISCLOSURES: Final[int] = 2

# 정형 문구 특징 어휘(브리프 작업②(나) 그대로 — 새 패턴 추가 금지 대상 아님,
# 측정에만 쓴다).
_SUSPECT_MARKERS: Final[tuple[str, ...]] = (
    "우리는",
    "핵심감사사항은",
    "커뮤니케이션",
    "전문가적 판단",
    "별도의 의견을 제공하지 않습니다",
)


def _strip_all_whitespace(text: str) -> str:
    return "".join(text.split())


def _normalize_title_key(title: str) -> str:
    return _strip_all_whitespace(title)[:_TITLE_PREFIX_LEN]


@dataclass
class TitleGroup:
    """정규화된 제목(앞 40자, 공백 제거)이 같은 항목들의 묶음."""

    key: str
    sample_title: str
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def frequency(self) -> int:
        return len(self.items)

    @property
    def disclosures(self) -> set[str]:
        return {str(item.get("rcept_no", "")) for item in self.items}

    @property
    def item_kinds(self) -> set[str]:
        return {str(item.get("item_kind", "")) for item in self.items}

    @property
    def is_suspect(self) -> bool:
        """그룹 내 아무 항목이든 본문에 정형 문구 어휘가 있으면 의심으로 본다."""
        normalized_markers = [_strip_all_whitespace(marker) for marker in _SUSPECT_MARKERS]
        for item in self.items:
            body_normalized = _strip_all_whitespace(str(item.get("body", "")))
            if any(marker in body_normalized for marker in normalized_markers):
                return True
        return False


def _read_items(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
    return rows


def build_title_groups(items: list[dict[str, Any]]) -> dict[str, TitleGroup]:
    groups: dict[str, TitleGroup] = {}
    for item in items:
        key = _normalize_title_key(str(item.get("title", "")))
        group = groups.get(key)
        if group is None:
            group = TitleGroup(key=key, sample_title=str(item.get("title", "")))
            groups[key] = group
        group.items.append(item)
    return groups


def repeated_groups(groups: dict[str, TitleGroup]) -> list[TitleGroup]:
    """2개 이상의 서로 다른 공시(`rcept_no`)에서 나타나는 그룹만 남긴다."""
    return [group for group in groups.values() if len(group.disclosures) >= _MIN_DISCLOSURES]


def top_n_by_frequency(groups: list[TitleGroup], n: int = _TOP_N) -> list[TitleGroup]:
    """빈도(항목 수) 내림차순, 동순위는 그룹 키 오름차순으로 정렬해
    결정론적으로 상위 n개를 뽑는다."""
    return sorted(groups, key=lambda group: (-group.frequency, group.key))[:n]


@dataclass
class CoverageResult:
    kind_counts: dict[str, int]
    total_items: int
    repeated_group_count: int
    repeated_all_kam_group_count: int
    top40: list[TitleGroup]
    top40_suspect_group_count: int
    top40_suspect_kam_group_count: int
    top40_suspect_kam_item_count: int


def compute_coverage(items: list[dict[str, Any]]) -> CoverageResult:
    kind_counts = Counter(str(item.get("item_kind", "")) for item in items)
    groups = build_title_groups(items)
    repeated = repeated_groups(groups)
    all_kam_repeated = [group for group in repeated if group.item_kinds == {"kam"}]
    top40 = top_n_by_frequency(repeated)
    suspect_top40 = [group for group in top40 if group.is_suspect]
    # 브리프 (다) 요약 줄 대상: "정형 문구로 의심되나 kam 으로 분류된 것"
    # — 이미 preamble/going_concern_notice/audit_communication 으로 옳게
    # 분류된 그룹은 이 요약의 대상이 아니다. item_kind 가 전부 kam 인
    # 그룹만 센다.
    suspect_kam_top40 = [group for group in suspect_top40 if group.item_kinds == {"kam"}]
    return CoverageResult(
        kind_counts=dict(kind_counts),
        total_items=len(items),
        repeated_group_count=len(repeated),
        repeated_all_kam_group_count=len(all_kam_repeated),
        top40=top40,
        top40_suspect_group_count=len(suspect_top40),
        top40_suspect_kam_group_count=len(suspect_kam_top40),
        top40_suspect_kam_item_count=sum(group.frequency for group in suspect_kam_top40),
    )


def render_markdown(result: CoverageResult) -> str:
    lines: list[str] = []
    lines.append("# KAM 분류 규칙 커버리지 측정")
    lines.append("")
    lines.append(
        "finov2 `BRIEF-A-kam-quality.md` 작업② — 분류 규칙을 고치지 않고 "
        "얼마나 놓치는지만 쟀다."
    )
    lines.append("")
    lines.append("## 전체 분포 (4종)")
    lines.append("")
    lines.append("| item_kind | 건수 |")
    lines.append("|---|---|")
    for kind in ("kam", "preamble", "going_concern_notice", "audit_communication"):
        lines.append(f"| {kind} | {result.kind_counts.get(kind, 0)} |")
    lines.append(f"| **합계** | **{result.total_items}** |")
    lines.append("")
    lines.append(
        f"반복 제목(2개 이상 공시) 종류 수(전체): **{result.repeated_group_count}**  "
        f"· 그중 item_kind 가 전부 `kam`인 종류 수: **{result.repeated_all_kam_group_count}**"
    )
    lines.append("")
    lines.append("## 반복 제목 상위 40개")
    lines.append("")
    lines.append(
        "제목 앞 40자를 공백 제거해 정규화 → 2개 이상 공시에서 반복되는 것 → "
        "빈도(항목 수)순 상위 40개. `정형 문구 의심`은 그룹 내 항목의 본문에 "
        "「우리는·핵심감사사항은·커뮤니케이션·전문가적 판단·별도의 의견을 "
        "제공하지 않습니다」 중 하나라도 있으면 표시한다(자동 판정 아님 — "
        "사람이 볼 목록)."
    )
    lines.append("")
    lines.append("| 순위 | 제목(앞 40자) | 빈도 | 공시 수 | item_kind | 정형 문구 의심 |")
    lines.append("|---|---|---|---|---|---|")
    for rank, group in enumerate(result.top40, start=1):
        kinds = ",".join(sorted(group.item_kinds))
        suspect = "예" if group.is_suspect else "-"
        title_display = group.sample_title[:_TITLE_PREFIX_LEN].replace("|", "\\|")
        lines.append(
            f"| {rank} | {title_display} | {group.frequency} | "
            f"{len(group.disclosures)} | {kinds} | {suspect} |"
        )
    lines.append("")
    lines.append(
        "요약: 정형 문구로 의심되나 kam 으로 분류된 것 약 "
        f"{result.top40_suspect_kam_item_count}건 "
        f"(상위 40개 안에서, item_kind 가 전부 kam 인 "
        f"{result.top40_suspect_kam_group_count}개 제목 종류. "
        f"참고: preamble·going_concern_notice·audit_communication 로 이미 옳게 "
        f"분류된 그룹까지 포함하면 정형 문구 의심 종류는 "
        f"{result.top40_suspect_group_count}개다)."
    )
    lines.append("")
    return "\n".join(lines)


def run(items_path: str | Path, output_path: str | Path) -> CoverageResult:
    items = _read_items(Path(items_path))
    result = compute_coverage(items)
    Path(output_path).write_text(render_markdown(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="KAM 분류 규칙 커버리지 측정 (재기만 한다 — 규칙을 고치지 않는다)"
    )
    parser.add_argument("--items", default="dart_collected/kam_items.jsonl")
    parser.add_argument("--out", default="dart_collected/kam_kind_coverage.md")
    args = parser.parse_args()
    result = run(args.items, args.out)
    print(f"분포: {result.kind_counts} 합계={result.total_items}")
    print(f"반복 제목(전체) 종류 수: {result.repeated_group_count}")
    print(f"그중 item_kind 전부 kam 인 종류 수: {result.repeated_all_kam_group_count}")
    print(f"상위 40개 중 정형 문구 의심 종류 수(전체 kind 포함): {result.top40_suspect_group_count}")
    print(
        f"상위 40개 중 정형 문구 의심 + item_kind 전부 kam 인 종류 수: "
        f"{result.top40_suspect_kam_group_count}"
    )
    print(f"그 종류들이 덮는 항목 수 합계: {result.top40_suspect_kam_item_count}")


if __name__ == "__main__":
    main()
