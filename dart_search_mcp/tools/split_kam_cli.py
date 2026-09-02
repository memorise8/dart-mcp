"""`audit_facts.jsonl`(수집기 산출물)에서 `kam_present=true` 공시를 골라
`dart_search_mcp.kam_split.split_kam_items`로 KAM 원문을 항목 단위로 쪼개고,
항목마다 `item_kind`를 붙여 `kam_items.jsonl`(한 줄 = 한 항목)을 내는 자립
CLI 전용 모듈.

`dart_search_mcp.tools.tag_kam_cli`와 달리 LLM/네트워크/캐시/체크포인트가
전혀 필요 없다 — 정규식 분할 + 규칙 판정뿐인 순수 계산이라 전량이 몇 초에
끝난다. 그 복잡도를 흉내내지 않는다(finov2 `.superpowers/sdd/
2026-09-02-dart-chat-ui/task-A-kam-split-collector.md` 참조).

## (회사, 회계연도) 당 1건만 남기는 선택 규칙 — temis-backend 에서 이관

`audit_facts.jsonl`에는 같은 공시가 정정([기재정정]/[첨부추가])으로 여러 번
등장하거나 12월 결산이 아닌 회사도 섞여 있다. 지금까지 이 선택은
temis-backend `app/services/dart_mvp_import.py::_select_deduped_rows`가
해 왔다(감사의견 적재와 동일 규칙 재사용). `dart_mvp_import.py`가 앞으로
`kam_items.jsonl`을 **그대로** 적재하게 되면(finov2 `docs/ops/
37_dart_temis_boundary_20260902.md` 단계 2) 그 선택 로직이 temis 쪽에 더
남아있지 않으므로, item_kind 판정과 별개로 이 선택도 여기로 옮기지 않으면
산출되는 (rcept_no, seq) 집합이 운영 3,543행과 달라진다 — 실측으로 확인:
이 선택 없이 `audit_facts.jsonl`의 `kam_present=true` 행을 그대로 돌리면
정정본·비12월결산이 섞여 항목 수가 어긋난다.

규칙(원본과 동일, 값만 옮김):
1. `settlement_month == 12`인 행만 본다.
2. `(corp_code, fiscal_year)`당 하나만 남긴다 — [기재정정]/[첨부추가]
   표시가 있는 보고서가 우선이고, 동순위면 `rcept_no`가 더 큰(문자열
   비교) 쪽이 이긴다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from dart_search_mcp.kam_split import split_kam_items

_REQUIRED_SETTLEMENT_MONTH: Final[int] = 12
_CORRECTION_MARKERS: Final[tuple[str, ...]] = ("[기재정정]", "[첨부추가]")


class SplitKamSourceError(Exception):
    """입력 소스(facts JSONL)를 읽거나 해석할 수 없을 때."""


def _is_correction(report_name: str) -> bool:
    return any(marker in report_name for marker in _CORRECTION_MARKERS)


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """`path`를 읽어 JSON 객체 행만 원래 등장 순서대로 반환한다. 파싱 실패
    줄과 dict가 아닌 줄은 조용히 건너뛴다(전체 로딩을 중단하지 않는다)."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            rows.append(row)
    return rows


def select_canonical_disclosures(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    """`(corp_code, fiscal_year)`당 정본 공시 1건을 고른다.

    `temis-backend`의 `_select_deduped_rows`와 동일한 규칙이다 — 12월 결산만
    보고, 정정본([기재정정]/[첨부추가])을 우선하며, 동순위면 `rcept_no`가
    더 큰 쪽이 이긴다.
    """
    best: dict[tuple[str, int], tuple[tuple[int, str], dict[str, Any]]] = {}
    for row in rows:
        if row.get("settlement_month") != _REQUIRED_SETTLEMENT_MONTH:
            continue
        corp_code = str(row.get("corp_code") or "")
        fiscal_year = row.get("fiscal_year")
        rcept_no = str(row.get("rcept_no") or "")
        if not corp_code or not isinstance(fiscal_year, int) or not rcept_no:
            continue
        key = (corp_code, fiscal_year)
        priority = (1 if _is_correction(str(row.get("report_name") or "")) else 0, rcept_no)
        current = best.get(key)
        if current is None or priority > current[0]:
            best[key] = (priority, row)
    return {key: value[1] for key, value in best.items()}


def build_kam_item_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """정본 공시 각각에서 `kam_present=true`인 것만 골라 항목으로 쪼개고,
    `kam_items.jsonl`에 쓸 dict 목록을 반환한다. 회사·회계연도(오름차순)
    순서로 결정론적으로 정렬한다."""
    canonical = select_canonical_disclosures(rows)
    records: list[dict[str, Any]] = []
    for key in sorted(canonical):
        row = canonical[key]
        if not row.get("kam_present"):
            continue
        rcept_no = str(row.get("rcept_no") or "")
        for seq, item in enumerate(split_kam_items(str(row.get("kam_raw") or ""))):
            records.append(
                {
                    "rcept_no": rcept_no,
                    "seq": seq,
                    "item_no": item.item_no,
                    "title": item.title,
                    "body": item.body,
                    "item_kind": item.kind.value,
                }
            )
    return records


def run_split_kam(facts_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """`facts_path`(`audit_facts.jsonl`)를 읽어 `output_path`
    (`kam_items.jsonl`)를 원자적으로(tmp+`Path.replace`) 쓴다.

    시계/네트워크를 전혀 쓰지 않는 순수 계산이라 결정론적이다: 같은
    `facts_path` 내용이면 항상 같은 출력 바이트를 만든다.

    `output_path`가 `facts_path`와 같은 경로를 가리키면(resolve 후 동일)
    입력을 덮어쓰는 것을 막기 위해 즉시 `SplitKamSourceError`를 던진다.
    """
    facts_path = Path(facts_path)
    output_path = Path(output_path)

    if not facts_path.exists():
        raise SplitKamSourceError(f"facts 파일을 찾을 수 없습니다: {facts_path}")

    if output_path.resolve() == facts_path.resolve():
        raise SplitKamSourceError(
            f"출력 경로가 입력 facts 경로와 같습니다 ({output_path}) - "
            "output을 facts와 다른 경로로 지정하세요(입력을 덮어쓰는 것을 방지)."
        )

    try:
        rows = _read_jsonl_rows(facts_path)
    except OSError as exc:
        raise SplitKamSourceError(f"facts 파일을 읽을 수 없습니다 ({facts_path}): {exc}") from exc

    records = build_kam_item_records(rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".split.tmp")
    with open(tmp_path, "w", encoding="utf-8") as tmp_f:
        for record in records:
            tmp_f.write(json.dumps(record, ensure_ascii=False))
            tmp_f.write("\n")
    tmp_path.replace(output_path)

    kind_counts: dict[str, int] = {}
    disclosures: set[str] = set()
    for record in records:
        kind_counts[record["item_kind"]] = kind_counts.get(record["item_kind"], 0) + 1
        disclosures.add(record["rcept_no"])

    return {
        "items": len(records),
        "disclosures": len(disclosures),
        "kind_counts": kind_counts,
    }
