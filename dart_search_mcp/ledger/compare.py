"""v1(ws8-v1 topic_judge)·v2(ws8-v2 규칙표) 판정 비교 (스펙 §7.2).

비교 단위는 접수번호 × 주제다. v1 은 범위 구분이 없고 v2 는 범위(별도·연결)마다 행이 있으므로 v2 를 공시 단위로 모은다.
- 판정 값이 하나면 그 값, 범위끼리 판정 값이 다르면 "A|B"(방향 scope_split),
- 모두 판단불가면 "UNDETERMINED/<사유>"(사유는 needs_review > read_failed > policy_only > no_facts > 그 밖 순으로 하나).

방향(directions): same, value→UNDETERMINED, value→other, UNDETERMINED→value, UNDETERMINED→UNDETERMINED,
scope_split, v1_only(v2 행 없음), v2_only(v1 행 없음).

측정 파일(classified.jsonl, 10-11 정책만 측정)의 `cls_final`이 policy+no-holding(정책만 1,945건)·policy+contradicted
(반대 금액 167건)인 행이 v2 에서 어디로 갔는지 주제별로 센다: same / UNDETERMINED/<사유> / other:<값> / v2 행 없음 / split:<값>.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

UND = "UNDETERMINED"
NONE_LABEL = "(없음)"
MEASURE_CLASSES = ("policy+no-holding", "policy+contradicted")
_REASON_ORDER = ("needs_review", "read_failed", "policy_only", "no_facts")


def _jsonl(path: Path):
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _reason(reasons: set[str]) -> str:
    for r in _REASON_ORDER:
        if r in reasons:
            return r
    rest = sorted(r for r in reasons if r)
    return rest[0] if rest else ""


def aggregate_v2(rows: list[dict]) -> tuple[str, str]:
    """범위별 v2 행 → (값 종류, 표시 이름). 값 종류는 value | und | split."""
    det = sorted({r["value"] for r in rows if r["value"] != UND})
    if len(det) == 1:
        return "value", det[0]
    if det:
        return "split", "|".join(det)
    reason = _reason({r.get("held_reason") or "" for r in rows})
    return "und", UND + (f"/{reason}" if reason else "")


def load_v1(path, rcepts=None) -> dict[tuple[str, str], str]:
    out = {}
    for r in _jsonl(Path(path)):
        if rcepts is None or r.get("rcept_no") in rcepts:
            out[(r["rcept_no"], r["topic"])] = r["value"]
    return out


def load_v2(path, rcepts=None) -> dict[tuple[str, str], tuple[str, str]]:
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in _jsonl(Path(path)):
        if rcepts is None or r.get("rcept_no") in rcepts:
            rows[(r["rcept_no"], r["topic"])].append(r)
    return {k: aggregate_v2(v) for k, v in rows.items()}


def _direction(v1: str | None, v2: tuple[str, str] | None) -> str:
    if v2 is None:
        return "v1_only"
    if v1 is None:
        return "v2_only"
    kind, label = v2
    if kind == "split":
        return "scope_split"
    if v1 == UND:
        return "UNDETERMINED→UNDETERMINED" if kind == "und" else "UNDETERMINED→value"
    if kind == "und":
        return "value→UNDETERMINED"
    return "same" if label == v1 else "value→other"


def _measure_bucket(v1: str, v2: tuple[str, str] | None) -> str:
    if v2 is None:
        return "v2 행 없음"
    kind, label = v2
    if kind == "und":
        return label
    if kind == "split":
        return f"split:{label}"
    return "same" if label == v1 else f"other:{label}"


def compare(v1_path, v2_path, classified_path=None, rcepts=None) -> dict:
    """주제별 {n_v1, n_v2, changed, directions, matrix(v1 값 → v2 표시 → 수)}와 측정 행의 행방(measure)."""
    v1 = load_v1(v1_path, rcepts)
    v2 = load_v2(v2_path, rcepts)
    topics: dict[str, dict] = {}
    for key in sorted(set(v1) | set(v2)):
        topic = key[1]
        t = topics.setdefault(topic, {"n_v1": 0, "n_v2": 0, "changed": 0, "directions": Counter(),
                                      "matrix": defaultdict(Counter)})
        a, b = v1.get(key), v2.get(key)
        t["n_v1"] += a is not None
        t["n_v2"] += b is not None
        d = _direction(a, b)
        t["directions"][d] += 1
        if d not in ("same", "UNDETERMINED→UNDETERMINED"):
            t["changed"] += 1
        t["matrix"][a if a is not None else NONE_LABEL][b[1] if b is not None else NONE_LABEL] += 1
    out: dict = {"topics": {k: {"n_v1": t["n_v1"], "n_v2": t["n_v2"], "changed": t["changed"],
                                "directions": dict(t["directions"]),
                                "matrix": {r: dict(c) for r, c in t["matrix"].items()}}
                            for k, t in topics.items()}}
    if classified_path:
        measure: dict = {}
        for r in _jsonl(Path(classified_path)):
            cls = r.get("cls_final")
            if cls not in MEASURE_CLASSES or (rcepts is not None and r.get("rcept_no") not in rcepts):
                continue
            m = measure.setdefault(cls, {"n": 0, "totals": Counter(), "by_topic": defaultdict(Counter)})
            bucket = _measure_bucket(r["value"], v2.get((r["rcept_no"], r["topic"])))
            m["n"] += 1
            m["totals"][bucket] += 1
            m["by_topic"][r["topic"]][bucket] += 1
        out["measure"] = {cls: {"n": m["n"], "totals": dict(m["totals"]),
                                "by_topic": {t: dict(c) for t, c in m["by_topic"].items()}}
                          for cls, m in measure.items()}
    return out
