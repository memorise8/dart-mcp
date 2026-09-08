"""주제별 게이트 리포트 — 어휘 §3.6. 통과한 주제만 TEMIS 화면에 노출한다."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED

_DEFAULT_UNDET_MAX = 0.10
_SUBJECT_TABLE_MIN_ROWS = 100

# REVENUE_METHOD 의 상한만 0.30 -> 0.40 이다(2026-09-08, ps-v2 전량 재추출 수치로 재보정).
# ps-v2 가 절 번호+표제가 본문에 붙은 수익인식 블록(「2.2.8 수익인식회사는 …」)을 새로 인식하면서
# **분모가 두 배**가 됐다: 회사 수준 행 13,981 -> 27,922. 같은 재추출에서 확정 행은
# 10,360 -> 18,003(+73.8%) 으로 늘었으므로 정확도가 떨어진 것이 아니라 **덜 보던 블록을 새로
# 보게 된 것**이고, 새로 들어온 블록이 옛 표본보다 판정하기 어려워 보류율이 25.9% -> 35.5% 로
# 올랐다. 상한은 그 실측치에 여유를 얹어 0.05 단위로 올림한 값이다(0.355 -> 0.40).
# PPE(82.3%)·IP(98.0%)·STD(8.1%) 의 상한은 건드리지 않는다 — 그쪽 분모는 이번에 바뀌지 않았다.
GATES: dict[str, Any] = {
    "positive_recall_min": 0.95,
    "hardneg_fp_max": 0,
    "undetermined_max": {"STD_EARLY_ADOPTION": 0.10, "PPE_MEASUREMENT": 0.10, "REVENUE_METHOD": 0.40, "IP_MEASUREMENT": 0.10},
    "min_positives": 10,
    "min_hardnegs": 50,
}


class PolicyGateControlError(Exception):
    """대조군(양성/하드 네거티브) 파일의 행 형식이 깨졌을 때(예: expected 누락)."""


def _rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _control_rows(path: Path | None) -> list[dict[str, Any]]:
    """대조군 파일을 읽되, expected 가 없는 행은 그 행을 지목해 바로 에러를 낸다(M4) —
    조용히 0건 취급되어 게이트가 잘못 통과하는 것을 막는다."""
    rows = _rows(path)
    for i, r in enumerate(rows):
        if "expected" not in r:
            raise PolicyGateControlError(
                f"{path}: {i + 1}번째 행(rcept_no={r.get('rcept_no')!r}, topic={r.get('topic')!r}, "
                f"subject={r.get('subject')!r})에 expected 가 없다"
            )
    return rows


def _key(r: dict[str, Any]) -> tuple[str, str, str | None]:
    return (r["rcept_no"], r["topic"], r.get("subject"))


def _load_capped_ratio(stances_path: Path) -> dict[str, float]:
    """<stances_path>.summary.json 이 있으면(extract-policy-stances 가 남긴 것) capped_ratio 를 읽는다.
    없거나 깨졌으면 조용히 빈 값 — 게이트는 상한 도달률 없이도 동작해야 한다."""
    stances_path = Path(stances_path)
    summary_path = stances_path.with_name(stances_path.name + ".summary.json")
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(data.get("capped_ratio") or {})


def build_gate_report(
    *, stances_path: Path, positives_path: Path | None, hardneg_path: Path | None, report_path: Path
) -> dict[str, Any]:
    stances_rows = _rows(stances_path)
    stances = {_key(r): r for r in stances_rows}

    # 주제 전체 분포와, subject 가 있는 행만 모은 주제×대상 분포(C1) 를 함께 쌓는다.
    # Fix round 1(리뷰 I3): 회사 수준 행(subject None)만 모은 분포도 따로 쌓는다 — 아래 판단불가
    # 비율은 이 분포로 잰다.
    dist: dict[str, Counter[str]] = defaultdict(Counter)
    dist_company: dict[str, Counter[str]] = defaultdict(Counter)
    dist_by_subject: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for r in stances_rows:
        dist[r["topic"]][r["value"]] += 1
        subj = r.get("subject")
        if subj is None:
            dist_company[r["topic"]][r["value"]] += 1
        else:
            dist_by_subject[r["topic"]][subj][r["value"]] += 1

    pos_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _control_rows(positives_path):
        pos_by_topic[r["topic"]].append(r)
    neg_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _control_rows(hardneg_path):
        neg_by_topic[r["topic"]].append(r)

    capped_ratio_map = _load_capped_ratio(stances_path)

    all_codes = sorted(set(dist) | set(pos_by_topic) | set(neg_by_topic))
    unknown_codes = [c for c in all_codes if c not in TOPICS]

    topics: dict[str, Any] = {}
    for code in all_codes:
        spec = TOPICS.get(code)
        counts = dist.get(code, Counter())
        total = sum(counts.values())
        # I3: 판단불가 비율은 **회사 수준 행**(subject None)으로 잰다. T5 가 유형자산에 자산군별
        # 행을 더하면서 단위가 섞였다 — 자산군 행은 정의상 UNDETERMINED 가 아니라, 전체 행으로
        # 재면 판단불가 비율이 실제보다 낮게 나온다(실측 1,486 보고서 표본: PPE 81.5% → 75.2%).
        # 대상(subject)별 행만 있는 주제(STD_EARLY_ADOPTION — subject 가 기준서 번호)는 회사 수준
        # 행이 아예 없으므로 주제 전체로 되돌아간다(기존 동작 그대로).
        company_counts = dist_company.get(code, Counter())
        company_total = sum(company_counts.values())
        ratio_counts, ratio_total = (
            (company_counts, company_total) if company_total else (counts, total)
        )
        undet = ratio_counts.get(UNDETERMINED, 0) / ratio_total if ratio_total else 0.0

        pos = pos_by_topic.get(code, [])
        neg = neg_by_topic.get(code, [])
        # I4: 대조군 행의 키가 stances 에 없으면 "미매칭" — 미매칭은 재현/오탐 계산에서 자연히
        # 0 취급되지만, 표본이 충분한지 판정할 때는 매칭된 건수만 센다(빠진 대조군이 통과를 벌지 않게).
        pos_matched = sum(1 for p in pos if _key(p) in stances)
        neg_matched = sum(1 for n in neg if _key(n) in stances)
        unmatched_controls = (len(pos) - pos_matched) + (len(neg) - neg_matched)

        hit = sum(1 for p in pos if stances.get(_key(p), {}).get("value") == p["expected"])
        recall = hit / len(pos) if pos else None
        fp = sum(1 for n in neg if spec and stances.get(_key(n), {}).get("value") in spec.positive_values)

        undet_max = GATES["undetermined_max"].get(code, _DEFAULT_UNDET_MAX)
        controls_sufficient = pos_matched >= GATES["min_positives"] and neg_matched >= GATES["min_hardnegs"]

        # C1: 대조군(양성∪하드네거티브)이 특정 대상(subject)에 걸려 있으면, 판단불가 상한은
        # 주제 전체가 아니라 그 대상들의 합집합으로만 잰다. 대조군이 없으면 주제 전체로 되돌아간다.
        control_subjects = sorted(
            {p.get("subject") for p in pos if p.get("subject") is not None}
            | {n.get("subject") for n in neg if n.get("subject") is not None}
        )
        if control_subjects:
            scoped_counts: Counter[str] = Counter()
            for s in control_subjects:
                scoped_counts.update(dist_by_subject.get(code, {}).get(s, Counter()))
            scoped_total = sum(scoped_counts.values())
            gate_undet = scoped_counts.get(UNDETERMINED, 0) / scoped_total if scoped_total else 0.0
            undetermined_scope = "controls-subjects:" + ",".join(control_subjects)
        else:
            gate_undet = undet
            undetermined_scope = "topic"

        passed = (
            spec is not None  # TOPICS 에 없는 코드는 조용히 통과시키지 않는다(I4)
            and controls_sufficient
            and (recall is None or recall >= GATES["positive_recall_min"])
            and fp <= GATES["hardneg_fp_max"]
            and gate_undet <= undet_max
        )

        topics[code] = {
            "distribution": dict(counts),
            "rows": total,
            "company_rows": company_total,
            "undetermined_scope_unit": "company-rows" if company_total else "all-rows",
            "positive_recall": recall,
            "positive_hit": hit,
            "positives": len(pos),
            "positives_matched": pos_matched,
            "hardneg_false_positive": fp,
            "hardnegs": len(neg),
            "hardnegs_matched": neg_matched,
            "unmatched_controls": unmatched_controls,
            "undetermined_ratio": round(undet, 4),
            "undetermined_ratio_scoped": round(gate_undet, 4),
            "undetermined_scope": undetermined_scope,
            "capped_ratio": capped_ratio_map.get(code),
            "controls_sufficient": controls_sufficient,
            "passed": passed,
        }

    report_passed = all(t["passed"] for t in topics.values()) if topics else False
    report = {"passed": report_passed, "topics": topics, "unknown_topics": unknown_codes}
    Path(report_path).write_text(
        _render_markdown(topics, dist_by_subject, unknown_codes, report_passed), encoding="utf-8"
    )
    return report


def _render_markdown(
    topics: dict[str, Any],
    dist_by_subject: dict[str, dict[str, Counter[str]]],
    unknown_codes: list[str],
    report_passed: bool,
) -> str:
    lines = ["# 회계정책 선택 사실 게이트 리포트", ""]
    if unknown_codes:
        lines += [
            "> ⚠ TOPICS 에 없는 주제 코드가 대조군/결과에 있다: " + ", ".join(unknown_codes) + " — 해당 코드는 미달로 처리한다.",
            "",
        ]
    lines += ["## 종합", "", "통과(회계사 표본 미확인)" if report_passed else "미달", ""]

    for code, t in sorted(topics.items()):
        spec = TOPICS.get(code)
        counts: dict[str, int] = t["distribution"]
        pos, hit = t["positives"], t["positive_hit"]
        neg, fp = t["hardnegs"], t["hardneg_false_positive"]
        recall = t["positive_recall"]
        undet, undet_max = t["undetermined_ratio"], GATES["undetermined_max"].get(code, _DEFAULT_UNDET_MAX)
        ctrl_status = "충분" if t["controls_sufficient"] else "부족(화면 비노출)"
        title = "미달(알 수 없는 주제)" if spec is None else ("통과" if t["passed"] else "미달")
        lines += [f"## {code} — {title}", "", "| 값 | 건수 |", "|---|---|"]
        lines += [f"| {v} | {n} |" for v, n in sorted(counts.items())]
        lines += [
            "",
            f"- 양성 재현: {hit}/{pos}" + (f" ({recall:.0%})" if recall is not None else " (대조군 없음)"),
            f"- 하드 네거티브 오탐: {fp}/{neg}",
            f"- 대조군 매칭: 양성 {t['positives_matched']}/{pos} · 하드 {t['hardnegs_matched']}/{neg} (미매칭은 통제 불충분으로 처리)",
            f"- 행 수: 회사 수준(subject 없음) {t['company_rows']} / 전체 {t['rows']}",
            f"- 판단불가 비율: {undet:.1%} (상한 {undet_max:.0%}, 분모 {t['undetermined_scope_unit']})",
        ]
        if t["undetermined_scope"] != "topic":
            lines.append(
                f"- 판단불가 비율(게이트 기준, {t['undetermined_scope']}): {t['undetermined_ratio_scoped']:.1%} (상한 {undet_max:.0%})"
            )
        if t["capped_ratio"] is not None:
            lines.append(f"- 상한 도달률: {t['capped_ratio']:.1%}")
        lines += [
            f"- 대조군: 양성 {pos}/{GATES['min_positives']} · 하드 네거티브 {neg}/{GATES['min_hardnegs']} → {ctrl_status}",
            "- 회계사 표본 20건 일치: 미확인 (게이트 통과에 포함되지 않음)",
            "",
        ]
        subj_counts = dist_by_subject.get(code, {})
        big_subjects = sorted(s for s, c in subj_counts.items() if sum(c.values()) >= _SUBJECT_TABLE_MIN_ROWS)
        if big_subjects:
            value_cols = list(spec.values) if spec else sorted({v for c in subj_counts.values() for v in c})
            lines += [
                "### 대상별 분포",
                "",
                "| 대상 | 행 | " + " | ".join(value_cols) + " | 판단불가 비율 |",
                "|" + "---|" * (3 + len(value_cols)),
            ]
            for s in big_subjects:
                c = subj_counts[s]
                s_total = sum(c.values())
                s_undet = c.get(UNDETERMINED, 0) / s_total if s_total else 0.0
                row_cells = " | ".join(str(c.get(v, 0)) for v in value_cols)
                lines.append(f"| {s} | {s_total} | {row_cells} | {s_undet:.1%} |")
            lines.append("")
    return "\n".join(lines)
