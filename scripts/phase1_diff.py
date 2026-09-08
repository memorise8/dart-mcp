#!/usr/bin/env python3
"""Phase 1 재추출 차등 보고 — 옛 산출물(ps-v1/af-v1/kam-v1) 대비 무엇이 몇 건 바뀌었나.

`uv run python scripts/phase1_diff.py <old_dir> <new_dir> [--output diff-report.md]`.
두 폴더 모두 `audit_facts.jsonl`·`kam_items.jsonl`·`policy_stance.jsonl` 을 갖고 있어야 한다.
옛 폴더는 **읽기만** 한다.

주의: 옛 `audit_facts.jsonl`(2026-07-14)은 옛 `policy_stance.jsonl`(2026-09-05)보다 앞서 만들어졌다.
그래서 정책 차등은 공시 목록이 아니라 `(rcept_no, topic, subject)` 키로 맞춘다 — 한쪽에만 있는 키는
「신규/소멸」로 따로 센다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_EXAMPLES = 5
_ABSENT = "—"


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


# --------------------------------------------------------------------------------------
# (a) 감사 메타
# --------------------------------------------------------------------------------------


def diff_audit(old_path: Path, new_path: Path) -> dict[str, Any]:
    old: dict[str, tuple[str, bool, bool]] = {}
    for r in _iter_jsonl(old_path):
        # 옛 판에는 opinion_status/icfr_opinion/gc_material_uncertainty/emphasis_status 가 없다.
        # 있는 열(audit_opinion·going_concern bool·emphasis_raw)로만 옛 상태를 만든다.
        old[r["rcept_no"]] = (_norm(r.get("audit_opinion")), bool(r.get("going_concern")),
                              bool(_norm(r.get("emphasis_raw")).strip()))
    opinion_x: Counter[tuple[str, str]] = Counter()
    gc_x: Counter[tuple[bool, str]] = Counter()
    emphasis_x: Counter[tuple[bool, str]] = Counter()
    dist: dict[str, Counter[str]] = defaultdict(Counter)
    gc_true_to_not: list[str] = []
    gc_false_to_disc: list[str] = []
    new_rcepts: set[str] = set()
    for r in _iter_jsonl(new_path):
        rcept = r["rcept_no"]
        new_rcepts.add(rcept)
        for column in ("audit_opinion", "opinion_status", "opinion_source", "icfr_opinion",
                       "gc_material_uncertainty", "gc_location", "emphasis_status",
                       "other_matter_status", "audit_report_count", "parser_version"):
            dist[column][_norm(r.get(column))] += 1
        prev = old.get(rcept)
        if prev is None:
            continue
        old_op, old_gc, old_emph = prev
        opinion_x[(old_op, _norm(r.get("audit_opinion")))] += 1
        gc_new = _norm(r.get("gc_material_uncertainty"))
        gc_x[(old_gc, gc_new)] += 1
        if old_gc and gc_new != "disclosed":
            gc_true_to_not.append(rcept)
        if (not old_gc) and gc_new == "disclosed":
            gc_false_to_disc.append(rcept)
        emphasis_x[(old_emph, _norm(r.get("emphasis_status")))] += 1
    return {
        "counts": {"old": len(old), "new": len(new_rcepts)},
        "only_old": len(set(old) - new_rcepts),
        "only_new": len(new_rcepts - set(old)),
        "opinion_crosstab": opinion_x,
        "gc_crosstab": gc_x,
        "gc_true_to_not_disclosed": sorted(gc_true_to_not)[:_EXAMPLES],
        "gc_true_to_not_disclosed_n": len(gc_true_to_not),
        "gc_false_to_disclosed": sorted(gc_false_to_disc)[:_EXAMPLES],
        "gc_false_to_disclosed_n": len(gc_false_to_disc),
        "emphasis_crosstab": emphasis_x,
        "gc_location": dict(dist["gc_location"]),
        "dist": {k: dict(v) for k, v in dist.items()},
    }


# --------------------------------------------------------------------------------------
# (b) KAM
# --------------------------------------------------------------------------------------


def _kam_counts(path: Path) -> tuple[Counter[str], Counter[str]]:
    """(공시별 kam 항목 수, item_kind 분포). 옛 판에는 kam 아닌 item_kind 가 없을 수도 있다."""
    per_rcept: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    for r in _iter_jsonl(path):
        kind = _norm(r.get("item_kind")) or "kam"
        kinds[kind] += 1
        if kind == "kam":
            per_rcept[r["rcept_no"]] += 1
    return per_rcept, kinds


def diff_kam(old_path: Path, new_path: Path) -> dict[str, Any]:
    old, kinds_old = _kam_counts(old_path)
    new, kinds_new = _kam_counts(new_path)
    changed = [(r, old.get(r, 0), new.get(r, 0)) for r in sorted(set(old) | set(new))
               if old.get(r, 0) != new.get(r, 0)]
    decreased = sorted((c for c in changed if c[2] < c[1]), key=lambda c: (c[1] - c[2], c[0]), reverse=True)
    increased = sorted((c for c in changed if c[2] > c[1]), key=lambda c: (c[2] - c[1], c[0]), reverse=True)
    return {
        "rcepts": {"old": len(old), "new": len(new)},
        "items": {"old": sum(old.values()), "new": sum(new.values())},
        "hist_old": Counter(old.values()),
        "hist_new": Counter(new.values()),
        "kind_old": kinds_old,
        "kind_new": kinds_new,
        "changed_n": len(changed),
        "decreased_n": len(decreased),
        "increased_n": len(increased),
        "decreased": decreased[:_EXAMPLES],
        "increased": increased[:_EXAMPLES],
    }


# --------------------------------------------------------------------------------------
# (c) 회계정책
# --------------------------------------------------------------------------------------


def _policy_index(path: Path) -> tuple[dict[tuple[str, str, str | None], str], dict[str, Any]]:
    index: dict[tuple[str, str, str | None], str] = {}
    meta: dict[str, Any] = {
        "held_reason": Counter(),
        "sentence_kind": Counter(),
        "sentence_subject": Counter(),
        "method": Counter(),
        "is_conditional": Counter(),
        "asset_class_rows": Counter(),
        "company_rows": defaultdict(Counter),
        "dist": defaultdict(Counter),
    }
    for r in _iter_jsonl(path):
        topic, subject, value = r["topic"], r.get("subject"), _norm(r.get("value"))
        index[(r["rcept_no"], topic, subject)] = value
        meta["held_reason"][_norm(r.get("held_reason"))] += 1
        meta["sentence_kind"][_norm(r.get("sentence_kind"))] += 1
        meta["sentence_subject"][_norm(r.get("sentence_subject"))] += 1
        meta["method"][_norm(r.get("method"))] += 1
        meta["is_conditional"][_norm(r.get("is_conditional"))] += 1
        meta["dist"][topic][value] += 1
        if subject is None:
            meta["company_rows"][topic][value] += 1
        elif topic != "STD_EARLY_ADOPTION":
            # STD_EARLY_ADOPTION 의 subject 는 기준서 번호라 자산군 행이 아니다.
            meta["asset_class_rows"][topic] += 1
    return index, meta


def diff_policy(old_path: Path, new_path: Path) -> dict[str, Any]:
    old, old_meta = _policy_index(old_path)
    new, new_meta = _policy_index(new_path)
    transitions: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    new_only: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    old_only: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for key, value in new.items():
        prev = old.get(key)
        if prev is None:
            new_only[key[1]][(_ABSENT, value)] += 1
        else:
            transitions[key[1]][(prev, value)] += 1
    for key, value in old.items():
        if key not in new:
            old_only[key[1]][(value, _ABSENT)] += 1
    return {
        "rows": {"old": len(old), "new": len(new)},
        "transitions": transitions,
        "new_only": new_only,
        "old_only": old_only,
        "dist_old": old_meta["dist"],
        "dist_new": new_meta["dist"],
        "company_rows_old": old_meta["company_rows"],
        "company_rows_new": new_meta["company_rows"],
        "ppe_asset_class_rows": {"old": old_meta["asset_class_rows"].get("PPE_MEASUREMENT", 0),
                                 "new": new_meta["asset_class_rows"].get("PPE_MEASUREMENT", 0)},
        "asset_class_rows_new": dict(new_meta["asset_class_rows"]),
        "held_reason": new_meta["held_reason"],
        "held_reason_old": old_meta["held_reason"],
        "sentence_kind": new_meta["sentence_kind"],
        "method_old": old_meta["method"],
        "method_new": new_meta["method"],
        "is_conditional": new_meta["is_conditional"],
    }


def _summary(path: Path) -> dict[str, Any]:
    p = Path(str(path) + ".summary.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------------------
# 보고서
# --------------------------------------------------------------------------------------


def _dist_table(title: str, counters: dict[str, Counter[str] | dict[str, int]], columns: list[str]) -> list[str]:
    lines = [f"### {title}", "", "| 값 | " + " | ".join(columns) + " |", "|" + "---|" * (len(columns) + 1)]
    keys = sorted({k for c in counters.values() for k in c})
    for k in keys:
        cells = " | ".join(str(counters[c].get(k, 0)) for c in columns)
        lines.append(f"| {k or '(빈값)'} | {cells} |")
    return lines + [""]


def _crosstab_table(title: str, table: Counter, old_label: str, new_label: str) -> list[str]:
    rows = sorted(table.items(), key=lambda kv: (-kv[1], str(kv[0])))
    lines = [f"### {title}", "", f"| {old_label} | {new_label} | 건수 |", "|---|---|---|"]
    lines += [f"| {_norm(a) or '(빈값)'} | {_norm(b) or '(빈값)'} | {n} |" for (a, b), n in rows]
    return lines + [""]


def render_report(audit: dict[str, Any], kam: dict[str, Any], policy: dict[str, Any],
                  *, old_dir: Path, new_dir: Path, summaries: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Phase 1 재추출 차등 보고 (af-v1/kam-v1/ps-v1 → af-v2/kam-v2/ps-v2)",
        "",
        f"- 옛 산출물: `{old_dir}` (읽기 전용)",
        f"- 새 산출물: `{new_dir}`",
        "",
        "## 1. 감사 메타 (audit_facts.jsonl)",
        "",
        f"- 공시 수: 옛 {audit['counts']['old']} → 새 {audit['counts']['new']} "
        f"(옛에만 {audit['only_old']}건 · 새에만 {audit['only_new']}건)",
        "",
    ]
    lines += _crosstab_table("감사의견 옛→새", audit["opinion_crosstab"], "옛 audit_opinion", "새 audit_opinion")
    lines += _dist_table("새 열 분포", {k: audit["dist"].get(k, {}) for k in
                                    ("opinion_status", "opinion_source", "icfr_opinion")},
                         ["opinion_status", "opinion_source", "icfr_opinion"])
    lines += _crosstab_table("계속기업 옛 bool → 새 gc_material_uncertainty", audit["gc_crosstab"],
                             "옛 going_concern", "새 gc_material_uncertainty")
    lines += [
        f"- True → not_disclosed: **{audit['gc_true_to_not_disclosed_n']}건** "
        f"(예: {', '.join(audit['gc_true_to_not_disclosed']) or '없음'})",
        f"- False → disclosed: **{audit['gc_false_to_disclosed_n']}건** "
        f"(예: {', '.join(audit['gc_false_to_disclosed']) or '없음'})",
        "",
    ]
    lines += _dist_table("gc_location / 강조·기타사항 상태", {k: audit["dist"].get(k, {}) for k in
                                                      ("gc_location", "emphasis_status", "other_matter_status")},
                         ["gc_location", "emphasis_status", "other_matter_status"])
    lines += _crosstab_table("강조사항 옛(emphasis_raw 있음) → 새 emphasis_status", audit["emphasis_crosstab"],
                             "옛 강조사항 원문", "새 emphasis_status")

    lines += [
        "## 2. KAM (kam_items.jsonl)",
        "",
        f"- 공시 수: 옛 {kam['rcepts']['old']} → 새 {kam['rcepts']['new']}",
        f"- kam 항목 수: 옛 {kam['items']['old']} → 새 {kam['items']['new']}",
        f"- 항목 수가 달라진 공시: {kam['changed_n']}건 (감소 {kam['decreased_n']} · 증가 {kam['increased_n']})",
        "",
        "### 공시별 kam 항목 수 히스토그램",
        "",
        "| 항목 수 | 옛 공시 수 | 새 공시 수 |",
        "|---|---|---|",
    ]
    for n in sorted(set(kam["hist_old"]) | set(kam["hist_new"])):
        lines.append(f"| {n} | {kam['hist_old'].get(n, 0)} | {kam['hist_new'].get(n, 0)} |")
    lines += ["", "### item_kind 분포", "", "| item_kind | 옛 | 새 |", "|---|---|---|"]
    for k in sorted(set(kam["kind_old"]) | set(kam["kind_new"])):
        lines.append(f"| {k} | {kam['kind_old'].get(k, 0)} | {kam['kind_new'].get(k, 0)} |")
    lines += ["", "### 변화 예시", "", "| 방향 | rcept_no | 옛 | 새 |", "|---|---|---|---|"]
    lines += [f"| 감소 | {r} | {o} | {n} |" for r, o, n in kam["decreased"]]
    lines += [f"| 증가 | {r} | {o} | {n} |" for r, o, n in kam["increased"]]
    lines.append("")

    lines += [
        "## 3. 회계정책 (policy_stance.jsonl)",
        "",
        f"- 행 수: 옛 {policy['rows']['old']} → 새 {policy['rows']['new']}",
        f"- 유형자산 자산군 행(subject 있음): 옛 {policy['ppe_asset_class_rows']['old']} → "
        f"새 {policy['ppe_asset_class_rows']['new']}",
        "",
    ]
    for topic in sorted(set(policy["dist_old"]) | set(policy["dist_new"])):
        lines += _dist_table(f"{topic} 값 분포", {"옛": policy["dist_old"].get(topic, Counter()),
                                              "새": policy["dist_new"].get(topic, Counter())}, ["옛", "새"])
        lines += _crosstab_table(f"{topic} 옛→새 (양쪽에 있는 키)", policy["transitions"].get(topic, Counter()),
                                 "옛 value", "새 value")
        only_new = policy["new_only"].get(topic, Counter())
        only_old = policy["old_only"].get(topic, Counter())
        if only_new or only_old:
            lines += _crosstab_table(f"{topic} 한쪽에만 있는 키", only_new + only_old, "옛 value", "새 value")

    lines += _dist_table("근거 메타 분포(새 열)", {"held_reason": policy["held_reason"],
                                          "sentence_kind": policy["sentence_kind"]},
                         ["held_reason", "sentence_kind"])
    lines += _dist_table("판정 방법(method)", {"옛": policy["method_old"], "새": policy["method_new"]}, ["옛", "새"])

    old_rev = policy["company_rows_old"].get("REVENUE_METHOD", Counter())
    new_rev = policy["company_rows_new"].get("REVENUE_METHOD", Counter())

    def _ratio(c: Counter) -> str:
        total = sum(c.values())
        return f"{(c.get('UNDETERMINED', 0) / total):.1%}" if total else "—"

    lines += [
        "### REVENUE_METHOD 보류율 분모 변화 (게이트 상수 재보정 근거)",
        "",
        "| 판 | 회사 수준 행 | UNDETERMINED | 보류율 |",
        "|---|---|---|---|",
        f"| ps-v1 | {sum(old_rev.values())} | {old_rev.get('UNDETERMINED', 0)} | {_ratio(old_rev)} |",
        f"| ps-v2 | {sum(new_rev.values())} | {new_rev.get('UNDETERMINED', 0)} | {_ratio(new_rev)} |",
        "",
    ]
    old_s, new_s = summaries.get("old", {}), summaries.get("new", {})
    lines += [
        "### LLM 호출",
        "",
        "| 판 | llm_pending_raw | llm_calls | llm_failures | 모델 | 소요(초) |",
        "|---|---|---|---|---|---|",
        f"| ps-v1 | {old_s.get('llm_pending_raw', '—')} | {old_s.get('llm_calls', '—')} | "
        f"{old_s.get('llm_failures', '—')} | {old_s.get('llm_model', '—')} | {old_s.get('elapsed_sec', '—')} |",
        f"| ps-v2 | {new_s.get('llm_pending_raw', '—')} | {new_s.get('llm_calls', '—')} | "
        f"{new_s.get('llm_failures', '—')} | {new_s.get('llm_model', '—')} | {new_s.get('elapsed_sec', '—')} |",
        "",
        f"- 보고서 단위 조합 승격(cross_block_combo_promoted): {new_s.get('cross_block_combo_promoted', '—')}",
        f"- 블록 간 값 상이로 보류(cross_block_conflict_held): {new_s.get('cross_block_conflict_held', '—')}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1 재추출 차등 보고")
    parser.add_argument("old_dir")
    parser.add_argument("new_dir")
    parser.add_argument("--output", default=None, help="기본: <new_dir>/diff-report.md")
    args = parser.parse_args(argv)
    old_dir, new_dir = Path(args.old_dir), Path(args.new_dir)
    audit = diff_audit(old_dir / "audit_facts.jsonl", new_dir / "audit_facts.jsonl")
    kam = diff_kam(old_dir / "kam_items.jsonl", new_dir / "kam_items.jsonl")
    policy = diff_policy(old_dir / "policy_stance.jsonl", new_dir / "policy_stance.jsonl")
    summaries = {"old": _summary(old_dir / "policy_stance.jsonl"),
                 "new": _summary(new_dir / "policy_stance.jsonl")}
    out = Path(args.output) if args.output else new_dir / "diff-report.md"
    out.write_text(render_report(audit, kam, policy, old_dir=old_dir, new_dir=new_dir, summaries=summaries),
                   encoding="utf-8")
    print(f"차등 보고 -> {out}")
    print(f"  감사 공시 {audit['counts']['old']} -> {audit['counts']['new']} · "
          f"계속기업 True→not_disclosed {audit['gc_true_to_not_disclosed_n']} · "
          f"False→disclosed {audit['gc_false_to_disclosed_n']}")
    print(f"  KAM 항목 {kam['items']['old']} -> {kam['items']['new']} (변화 공시 {kam['changed_n']})")
    print(f"  정책 행 {policy['rows']['old']} -> {policy['rows']['new']} · "
          f"PPE 자산군 행 {policy['ppe_asset_class_rows']['new']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
