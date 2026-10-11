"""정답 묶음(goldset.jsonl)과 층별 채점.

1층 항목: 회계사가 짚은 근거 칸 하나. 그 공시의 표 기록에서 행 레이블(label·label_path)에 `label_contains`가 있고,
열 기간이 `period`이며 칸의 원 환산값이 `value_krw`인 칸이 있으면 맞다.
고르기 조건(선택): `title_contains`(표 제목), `header_contains`(열 머리글 경로), `fs_scope`, `region_kind`.
read_failed 표는 세지 않는다(행·칸을 잡지 못한 표다).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .tables import load_filing

GOLDSET_PATH = Path(__file__).with_name("goldset.jsonl")
_WS = re.compile(r"\s+")


def _sq(s: str | None) -> str:
    return _WS.sub("", s or "")


def load_goldset(path: Path = GOLDSET_PATH) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _tables(item: dict, filing: dict):
    for t in filing.get("tables", []):
        if t.get("status") == "read_failed":
            continue
        if item.get("title_contains") and _sq(item["title_contains"]) not in _sq(t.get("title")):
            continue
        if item.get("fs_scope") and t.get("fs_scope") != item["fs_scope"]:
            continue
        if item.get("region_kind") and (t.get("region") or {}).get("kind") != item["region_kind"]:
            continue
        yield t


def _label_hit(row: dict, needle: str) -> bool:
    return any(needle in _sq(x) for x in [row.get("label")] + list(row.get("label_path") or []))


def score_layer1(gold: list[dict], out_dir: Path) -> list[dict]:
    """1층 정답 항목마다 {id, ok, detail}. detail은 맞으면 찾은 칸(table_id rN cN), 틀리면 가까운 후보."""
    results = []
    for item in gold:
        if item.get("layer") != 1:
            continue
        path = Path(out_dir) / f"{item['rcept_no']}.json.gz"
        if not path.exists():
            results.append({"id": item["id"], "ok": False, "detail": f"표 기록 파일 없음: {path.name}"})
            continue
        filing = load_filing(path)
        needle = _sq(item["label_contains"])
        hits, near = [], []
        for t in _tables(item, filing):
            cols = {c["col"]: c for c in t.get("columns", []) if c.get("role") == "value"}
            for row in t.get("rows", []):
                if not _label_hit(row, needle):
                    continue
                for cell in row.get("cells", []):
                    col = cols.get(cell["col"])
                    if col is None:
                        continue
                    if item.get("header_contains") and _sq(item["header_contains"]) not in _sq("/".join(col["header_path"])):
                        continue
                    where = f"{t['table_id']} r{row['row']} c{cell['col']}"
                    if col.get("period") == item["period"] and cell.get("value_krw") == item["value_krw"]:
                        hits.append(where)
                    else:
                        near.append(f"{where} {col.get('period')}={cell.get('value_krw')}")
        if hits:
            results.append({"id": item["id"], "ok": True, "detail": "; ".join(hits)})
        else:
            results.append({"id": item["id"], "ok": False,
                            "detail": "맞는 칸 없음" + (" — 후보: " + "; ".join(near[:5]) if near else " — 레이블 행 없음")})
    return results
