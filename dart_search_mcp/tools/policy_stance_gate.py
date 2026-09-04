"""주제별 게이트 리포트 — 어휘 §3.6. 통과한 주제만 TEMIS 화면에 노출한다."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED

GATES: dict[str, Any] = {
    "positive_recall_min": 0.95,
    "hardneg_fp_max": 0,
    "undetermined_max": {"STD_EARLY_ADOPTION": 0.10, "PPE_MEASUREMENT": 0.10, "REVENUE_METHOD": 0.30, "IP_MEASUREMENT": 0.10},
}


def _rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _key(r: dict[str, Any]) -> tuple[str, str, str | None]:
    return (r["rcept_no"], r["topic"], r.get("subject"))


def build_gate_report(*, stances_path: Path, positives_path: Path | None, hardneg_path: Path | None, report_path: Path) -> dict[str, Any]:
    stances = {_key(r): r for r in _rows(stances_path)}
    dist: dict[str, Counter[str]] = defaultdict(Counter)
    for r in stances.values():
        dist[r["topic"]][r["value"]] += 1
    pos_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _rows(positives_path):
        pos_by_topic[r["topic"]].append(r)
    neg_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _rows(hardneg_path):
        neg_by_topic[r["topic"]].append(r)
    topics: dict[str, Any] = {}
    lines = ["# 회계정책 선택 사실 게이트 리포트", ""]
    for code in sorted(dist):
        spec = TOPICS.get(code)
        total = sum(dist[code].values())
        undet = dist[code].get(UNDETERMINED, 0) / total if total else 0.0
        pos = pos_by_topic.get(code, [])
        hit = sum(1 for p in pos if stances.get(_key(p), {}).get("value") == p["expected"])
        recall = hit / len(pos) if pos else None
        neg = neg_by_topic.get(code, [])
        fp = sum(1 for n in neg if spec and stances.get(_key(n), {}).get("value") in spec.positive_values)
        passed = (
            (recall is None or recall >= GATES["positive_recall_min"])
            and fp <= GATES["hardneg_fp_max"]
            and undet <= GATES["undetermined_max"].get(code, 0.10)
        )
        topics[code] = {
            "distribution": dict(dist[code]), "rows": total, "positive_recall": recall, "positives": len(pos),
            "hardneg_false_positive": fp, "hardnegs": len(neg), "undetermined_ratio": round(undet, 4), "passed": passed,
        }
        lines += [f"## {code} — {'통과' if passed else '미달'}", "", "| 값 | 건수 |", "|---|---|"]
        lines += [f"| {v} | {n} |" for v, n in sorted(dist[code].items())]
        lines += ["", f"- 양성 재현: {hit}/{len(pos)}" + (f" ({recall:.0%})" if recall is not None else " (대조군 없음)"),
                  f"- 하드 네거티브 오탐: {fp}/{len(neg)}", f"- 판단불가 비율: {undet:.1%} (상한 {GATES['undetermined_max'].get(code, 0.10):.0%})", ""]
    report = {"passed": all(t["passed"] for t in topics.values()) if topics else False, "topics": topics}
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    return report
