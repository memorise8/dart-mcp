#!/usr/bin/env python3
"""Phase 1 재추출 산출물 회귀 — 회계사 확인 표본을 기대값으로 고정한다.

`uv run python scripts/phase1_regression.py <out_dir>` 로 돌린다. `<out_dir>` 안의
`audit_facts.jsonl`·`kam_items.jsonl`·`policy_stance.jsonl` 세 파일을 읽어 아래 표와
대조하고, `<out_dir>/regression-report.md` 에 PASS/FAIL 표를 쓴다. **FAIL 이 하나라도
있으면 종료코드 1** 이다(호출자가 파이프라인을 세우기 위한 것).

기대값의 출처는 두 가지다.

1. 회계사 확인(2026-09-08, analysis §2 A1~A4·P1~P5) — Task 6 브리프 Step 1 이 옮겨 적은 표.
2. 컨트롤러 정정(브리프 「컨트롤러 추가」) — STX 20251219000526 은 `disclosed`,
   20260413000920 은 `not_disclosed`, HC홈센타는 연결감사(`_00761`) 기준, 온타이드·라닉스는
   자산군 행 + 회사 수준 MIXED.

이 스크립트는 **판정하지 않는다**. 산출물이 기대와 다르면 그대로 FAIL 로 적을 뿐이고,
파서를 고칠지 기대값을 고칠지는 사람이 정한다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# 기대 대상 행이 산출물에 없을 때 `Check.actual` 에 쓰는 표시. 값 불일치(FAIL)와 구분해
# 보고서에서 바로 눈에 띄게 한다.
MISSING = "행 없음"


@dataclass(frozen=True)
class Check:
    """대조 한 건. `section` 은 보고서의 표 묶음, `field` 는 그 표의 행 이름이다."""

    section: str
    target: str
    field: str
    expected: str
    actual: str
    passed: bool
    note: str = ""


@dataclass(frozen=True)
class AuditExpectation:
    """감사 메타 한 공시. `fields` 는 (열 이름, 기대 문자열) 쌍, `kam_items` 는
    `kam_items.jsonl` 에서 `item_kind == "kam"` 인 행 수(None 이면 안 본다)."""

    rcept_no: str
    label: str
    fields: tuple[tuple[str, str], ...] = ()
    kam_items: int | None = None
    note: str = ""


@dataclass(frozen=True)
class PolicyExpectation:
    """정책 한 (공시, 주제[, 대상]).

    - `value`/`evidence_contains`: (rcept, topic, subject) 행 하나를 지목해 본다.
      `evidence_contains` 에 튜플을 주면 **그중 하나만 들어도 통과**다 — 회계사가 확인한 축이
      "근거가 회사 주어 문장인가"이고, 한 블록 안에 그런 문장이 둘 이상일 때 어느 쪽이 뽑히는지는
      위치(먼저 나온 문장이 이긴다)로 정해지기 때문이다. 특정 문장 하나를 못 박으면 축이 아니라
      순서를 시험하게 된다.
    - `present_values`/`absent_values`: 같은 (rcept, topic) 의 **행 집합**에 그 값이
      있어야/없어야 한다(디와이디형 기대: 「REVALUATION_MODEL 행이 없어야 한다」).
    """

    rcept_no: str
    label: str
    topic: str
    subject: str | None = None
    value: str | None = None
    evidence_contains: str | tuple[str, ...] | None = None
    present_values: tuple[str, ...] = ()
    absent_values: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class VersionExpectation:
    """산출물 전 행이 같은 파서/분할기 판이어야 한다."""

    filename: str
    column: str
    value: str


# --------------------------------------------------------------------------------------
# 기대값 표 (회계사 확인 + 컨트롤러 정정)
# --------------------------------------------------------------------------------------

AUDIT_EXPECTATIONS: tuple[AuditExpectation, ...] = (
    AuditExpectation(
        "20260407002635",
        "동성제약",
        (("audit_opinion", "적정"), ("opinion_status", "confirmed"),
         ("icfr_opinion", "부적정"), ("emphasis_status", "present")),
        kam_items=1,
        note="재무제표 적정 + 내부회계 부적정(축 분리). KAM 은 환불부채 측정 1건",
    ),
    AuditExpectation(
        "20260323001089",
        "HC홈센타",
        (("audit_opinion", "적정"), ("opinion_status", "confirmed")),
        note="정본은 연결감사(_00761)",
    ),
    AuditExpectation("20260323001719", "팡스카이", (("gc_material_uncertainty", "disclosed"),),
                     note="의견거절근거 안의 계속기업 불확실성"),
    AuditExpectation(
        "20251219000526",
        "STX마린서비스(2024.12 연결감사)",
        (("gc_material_uncertainty", "disclosed"), ("gc_location", "within_basis_for_opinion")),
        note="컨트롤러 정정: 이 보고서 **자체**가 계속기업 불확실성을 기재한다",
    ),
    AuditExpectation("20260413000920", "STX마린서비스(2025.12 감사)", (("gc_material_uncertainty", "not_disclosed"),),
                     note="전기 보고서 의견을 옮겨 적은 기타사항 문장뿐"),
    AuditExpectation("20260407003361", "삼보산업", (("gc_material_uncertainty", "disclosed"),), kam_items=1),
    AuditExpectation("20260323001579", "코이즈", kam_items=2, note="현금창출단위 손상검사·계속기업가정"),
    AuditExpectation("20260323000861", "동일스틸럭스", kam_items=1),
    AuditExpectation("20260319001215", "비나텍", kam_items=1),
    AuditExpectation("20260318001001", "효성 ITX", kam_items=1),
)

POLICY_EXPECTATIONS: tuple[PolicyExpectation, ...] = (
    PolicyExpectation("20260316001522", "네오팜", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED",
                      evidence_contains="검토 중"),
    PolicyExpectation(
        "20260320001388", "나이벡", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED",
        evidence_contains=("검토 중", "채택하지 않"),
        note="회계사가 확인한 축은 「근거가 회사 주어 문장인가」다. 이 블록에는 그런 문장이 둘 "
             "(「…아직 채택하지 않았으며 …」·「…검토 중에 있으며 …」)이고 먼저 나온 쪽이 이긴다 "
             "— 어느 쪽이든 통과(컨트롤러 판정 2026-09-08, Task 4 리뷰가 받아들인 규칙)",
    ),
    PolicyExpectation("20260318001024", "이마트", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED"),
    PolicyExpectation("20260316001663", "에스텍", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED"),
    PolicyExpectation("20260310002820", "삼성전자", "STD_EARLY_ADOPTION", "1118", value="NOT_ADOPTED"),
    PolicyExpectation("20260327000721", "올래디오", "STD_EARLY_ADOPTION", "1117", value="ADOPTED",
                      note="ps-v2 신규 값(신규 적용 목록에 실린 기준서)"),
    PolicyExpectation("20260316000929", "고려아연", "STD_EARLY_ADOPTION", "1109", value="EARLY_ADOPTED"),
    PolicyExpectation("20260313001168", "유니셈", "STD_EARLY_ADOPTION", "1109", value="PERMITTED_ONLY"),
    PolicyExpectation("20260430001895", "디와이디", "PPE_MEASUREMENT",
                      present_values=("COST_MODEL",), absent_values=("REVALUATION_MODEL",),
                      note="2.2.11 손상 조건절을 재평가로 읽지 않는다"),
    PolicyExpectation("20260319001171", "온타이드", "PPE_MEASUREMENT", "토지", value="REVALUATION_MODEL"),
    PolicyExpectation("20260319001171", "온타이드", "PPE_MEASUREMENT", "건물", value="REVALUATION_MODEL"),
    PolicyExpectation("20260319001171", "온타이드", "PPE_MEASUREMENT", "토지와 건물을 제외한 유형자산",
                      value="COST_MODEL"),
    PolicyExpectation("20260319001171", "온타이드", "PPE_MEASUREMENT", None, value="MIXED",
                      note="회사 수준 행은 자산군이 갈리므로 MIXED"),
    PolicyExpectation("20260318000717", "라닉스", "PPE_MEASUREMENT", "토지", value="REVALUATION_MODEL"),
    PolicyExpectation("20260318000717", "라닉스", "PPE_MEASUREMENT", "토지를 제외한 유형자산", value="COST_MODEL"),
    PolicyExpectation("20260318000717", "라닉스", "PPE_MEASUREMENT", None, value="MIXED"),
    PolicyExpectation("20260319000739", "성광벤드", "PPE_MEASUREMENT", None, value="COST_MODEL"),
    PolicyExpectation("20260318000447", "엠앤씨솔루션", "REVENUE_METHOD", None, value="BOTH",
                      note="「일부의 수익은 진행기준」 = 병행"),
    PolicyExpectation("20260401000853", "엑시나", "REVENUE_METHOD", None, value="BOTH"),
    PolicyExpectation("20260323001053", "HC보광산업", "REVENUE_METHOD", None, value="BOTH"),
    PolicyExpectation("20260318000745", "흥아해운", "IP_MEASUREMENT", None, value="FAIR_VALUE_MODEL"),
    PolicyExpectation("20260319000921", "레드캡투어", "IP_MEASUREMENT", None, value="COST_MODEL"),
)

VERSION_EXPECTATIONS: tuple[VersionExpectation, ...] = (
    VersionExpectation("audit_facts.jsonl", "parser_version", "af-v2"),
    VersionExpectation("policy_stance.jsonl", "parser_version", "ps-v2"),
)


# --------------------------------------------------------------------------------------
# 산출물 읽기
# --------------------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """빈 줄은 건너뛴다. 깨진 줄은 그대로 예외를 올린다 — 회귀 판정의 입력이 손상된 채로
    PASS 가 나오는 것이 더 나쁘다."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_audit_facts(path: Path, wanted: set[str] | None = None) -> dict[str, dict[str, Any]]:
    return {r["rcept_no"]: r for r in _iter_jsonl(path) if wanted is None or r["rcept_no"] in wanted}


def load_kam_counts(path: Path) -> Counter[str]:
    """`item_kind == "kam"` 인 항목만 공시별로 센다(preamble·감사소통·계속기업 안내 제외)."""
    counts: Counter[str] = Counter()
    for r in _iter_jsonl(path):
        if r.get("item_kind") == "kam":
            counts[r["rcept_no"]] += 1
    return counts


def load_policy_rows(
    path: Path, wanted: set[str] | None = None
) -> tuple[dict[tuple[str, str, str | None], dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    """(rcept, topic, subject) -> 행, 그리고 (rcept, topic) -> 행 목록 을 함께 돌려준다."""
    by_key: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    by_topic: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in _iter_jsonl(path):
        if wanted is not None and r["rcept_no"] not in wanted:
            continue
        by_key[(r["rcept_no"], r["topic"], r.get("subject"))] = r
        by_topic.setdefault((r["rcept_no"], r["topic"]), []).append(r)
    return by_key, by_topic


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


# --------------------------------------------------------------------------------------
# 대조
# --------------------------------------------------------------------------------------


def check_audit(
    facts: dict[str, dict[str, Any]],
    kam_counts: Counter[str],
    expectations: Sequence[AuditExpectation],
) -> list[Check]:
    checks: list[Check] = []
    for exp in expectations:
        target = f"{exp.rcept_no} {exp.label}"
        row = facts.get(exp.rcept_no)
        for column, expected in exp.fields:
            actual = MISSING if row is None else _norm(row.get(column))
            checks.append(Check("감사", target, column, expected, actual, actual == expected, exp.note))
        if exp.kam_items is not None:
            # 감사 행이 없으면 KAM 항목 수도 신뢰할 수 없다 — 행 없음으로 적는다.
            actual = MISSING if row is None else str(kam_counts.get(exp.rcept_no, 0))
            checks.append(
                Check("KAM", target, "kam 항목 수", str(exp.kam_items), actual,
                      actual == str(exp.kam_items), exp.note)
            )
    return checks


def check_policy(
    by_key: dict[tuple[str, str, str | None], dict[str, Any]],
    by_topic: dict[tuple[str, str], list[dict[str, Any]]],
    expectations: Sequence[PolicyExpectation],
) -> list[Check]:
    checks: list[Check] = []
    for exp in expectations:
        subject_label = exp.subject if exp.subject is not None else "회사 수준"
        target = f"{exp.rcept_no} {exp.label} / {exp.topic} / {subject_label}"
        row = by_key.get((exp.rcept_no, exp.topic, exp.subject))
        rows = by_topic.get((exp.rcept_no, exp.topic), [])
        if exp.value is not None:
            actual = MISSING if row is None else _norm(row.get("value"))
            checks.append(Check("정책", target, "value", exp.value, actual, actual == exp.value, exp.note))
        if exp.evidence_contains is not None:
            wanted = (
                (exp.evidence_contains,)
                if isinstance(exp.evidence_contains, str)
                else tuple(exp.evidence_contains)
            )
            evidence = None if row is None else _norm(row.get("evidence"))
            actual = MISSING if evidence is None else evidence[:120]
            passed = evidence is not None and any(w in evidence for w in wanted)
            checks.append(
                Check("정책", target, "evidence ⊃", " 또는 ".join(wanted), actual, passed, exp.note)
            )
        values = {_norm(r.get("value")) for r in rows}
        for value in exp.present_values:
            actual = ", ".join(sorted(values)) or MISSING
            checks.append(Check("정책", target, f"값 {value} 존재", "있음", actual, value in values, exp.note))
        for value in exp.absent_values:
            actual = ", ".join(sorted(values)) or MISSING
            checks.append(Check("정책", target, f"값 {value} 없음", "없음", actual, value not in values, exp.note))
    return checks


def check_versions(out_dir: Path, expectations: Sequence[VersionExpectation]) -> list[Check]:
    """판 표시(af-v2/ps-v2)가 전 행에 같은 값으로 실렸는지 — 옛 산출물이 섞여 들어오는 사고 방지."""
    checks: list[Check] = []
    for exp in expectations:
        path = Path(out_dir) / exp.filename
        if not path.exists():
            checks.append(Check("산출물", exp.filename, exp.column, exp.value, MISSING, False))
            continue
        seen = Counter(_norm(r.get(exp.column)) for r in _iter_jsonl(path))
        actual = ", ".join(f"{v or '(없음)'}×{n}" for v, n in seen.most_common(3))
        checks.append(
            Check("산출물", exp.filename, exp.column, f"전 행 {exp.value}", actual,
                  set(seen) == {exp.value})
        )
    return checks


def run_checks(
    out_dir: Path | str,
    *,
    audit_expectations: Sequence[AuditExpectation] = AUDIT_EXPECTATIONS,
    policy_expectations: Sequence[PolicyExpectation] = POLICY_EXPECTATIONS,
    version_expectations: Sequence[VersionExpectation] = (),
) -> list[Check]:
    """산출물 폴더를 읽어 대조 결과를 돌려준다. 기대 대상 공시만 메모리에 남긴다."""
    out = Path(out_dir)
    audit_rcepts = {e.rcept_no for e in audit_expectations}
    policy_rcepts = {e.rcept_no for e in policy_expectations}
    facts = load_audit_facts(out / "audit_facts.jsonl", audit_rcepts) if audit_rcepts else {}
    kam_counts = (
        load_kam_counts(out / "kam_items.jsonl")
        if any(e.kam_items is not None for e in audit_expectations)
        else Counter()
    )
    by_key, by_topic = (
        load_policy_rows(out / "policy_stance.jsonl", policy_rcepts) if policy_rcepts else ({}, {})
    )
    return (
        check_versions(out, version_expectations)
        + check_audit(facts, kam_counts, audit_expectations)
        + check_policy(by_key, by_topic, policy_expectations)
    )


# --------------------------------------------------------------------------------------
# 보고서
# --------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(checks: Sequence[Check], *, out_dir: Path | str = "") -> str:
    passed = sum(1 for c in checks if c.passed)
    failed = len(checks) - passed
    lines = [
        "# Phase 1 회귀 — 회계사 확인 표본",
        "",
        f"- 산출물: `{out_dir}`",
        f"- 대조 {len(checks)}건 · PASS {passed} · **FAIL {failed}**",
        "",
    ]
    for section in ("산출물", "감사", "KAM", "정책"):
        rows = [c for c in checks if c.section == section]
        if not rows:
            continue
        n_fail = sum(1 for c in rows if not c.passed)
        lines += [
            f"## {section} ({len(rows)}건, FAIL {n_fail})",
            "",
            "| 결과 | 대상 | 항목 | 기대 | 실제 | 비고 |",
            "|---|---|---|---|---|---|",
        ]
        for c in rows:
            mark = "PASS" if c.passed else "**FAIL**"
            lines.append(
                f"| {mark} | {_cell(c.target)} | {_cell(c.field)} | {_cell(c.expected)} | "
                f"{_cell(c.actual)} | {_cell(c.note)} |"
            )
        lines.append("")
    if failed:
        lines += [
            "## FAIL 목록",
            "",
            "> 이 스크립트는 원인을 판단하지 않는다. 아래 각 행에 대해 파서를 고칠지 기대값을 고칠지는 사람이 정한다.",
            "",
        ]
        lines += [
            f"- `{c.target}` / {c.field}: 기대 `{c.expected}` ≠ 실제 `{c.actual}`"
            + (f" — {c.note}" if c.note else "")
            for c in checks
            if not c.passed
        ]
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1 재추출 산출물 회귀 대조")
    parser.add_argument("out_dir", help="audit_facts.jsonl·kam_items.jsonl·policy_stance.jsonl 이 있는 폴더")
    parser.add_argument("--report", default=None, help="보고서 경로 (기본: <out_dir>/regression-report.md)")
    parser.add_argument("--only", default=None, help="쉼표 구분 rcept_no 만 대조 (디버깅용)")
    parser.add_argument("--check-versions", action="store_true",
                        help="parser_version 이 전 행에서 af-v2/ps-v2 인지도 본다 (전량 재읽기)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    audit = AUDIT_EXPECTATIONS
    policy = POLICY_EXPECTATIONS
    if args.only:
        only = {s.strip() for s in args.only.split(",") if s.strip()}
        audit = tuple(e for e in audit if e.rcept_no in only)
        policy = tuple(e for e in policy if e.rcept_no in only)
    checks = run_checks(
        out_dir,
        audit_expectations=audit,
        policy_expectations=policy,
        version_expectations=VERSION_EXPECTATIONS if args.check_versions else (),
    )
    report_path = Path(args.report) if args.report else out_dir / "regression-report.md"
    report_path.write_text(render_report(checks, out_dir=out_dir), encoding="utf-8")
    failed = [c for c in checks if not c.passed]
    print(f"회귀 대조 {len(checks)}건 · PASS {len(checks) - len(failed)} · FAIL {len(failed)} -> {report_path}")
    for c in failed:
        print(f"  FAIL {c.target} / {c.field}: 기대 {c.expected!r} ≠ 실제 {c.actual!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
