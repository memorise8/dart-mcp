"""audit_facts 패치 생성 — 감사인 복구 (의견 unknown 은 분석 리포트만).

원본 audit_facts.jsonl 은 절대 수정하지 않는다 (정책문서 A §6.1 원본 보존).
감사인이 실제로 복구된 행만 audit_facts_patch.jsonl 로 쓴다.

회계사 지시(컨트롤러 재정, 2026-08-17): 의견 자동해소(fulltext_fallback)는
아직 프로덕션 데이터에 적용하지 않는다. 따라서 이 스크립트는:
  - 패치 파일에는 감사인 필드(rcept_no·auditor_raw·auditor_resolved·
    auditor_source) 만 쓴다. audit_opinion_* / opinion_method 는 어떤
    행에도 넣지 않는다.
  - 의견 unknown 은 여전히 분석하되, 결과는 별도 산출물로만 낸다:
      dart_collected/opinion_unknown_analysis.jsonl  (행별 원자료)
      reports/opinion_unknown_examples.md             (회계사 검토용 요약)

합격선 (착수 전 표본 실측 §13.4·§14.1 에서 유도):
  - 감사인 오염군 복구율  >= 95%   (표본 실측 99%)  — HARD 게이트, 미달 시 exit 1
  - 의견 unknown 해소율   (표본 실측 78%)            — 정보용 지표. 게이트 아님,
                                                        패치에도 반영하지 않는다.

사용:
    python scripts/build_audit_facts_patch.py \
        --facts dart_collected/audit_facts.jsonl \
        --out dart_collected/audit_facts_patch.jsonl \
        [--limit N]   # 스모크 테스트: 처리 대상 행을 앞에서 N건만
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditor_resolver import is_firm, resolve_auditor
from opinion_fallback import classify_opinion_fulltext

GATE_AUDITOR_RECOVERY = 0.95

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_FAIR_PHRASE_RE = re.compile(r"공정하게\s*표시하고\s*있습니다")
_OPINION_HEADING_RE = re.compile(r"감사의견")

PROGRESS_EVERY = 1000
EXCERPT_CHARS = 400
EXAMPLES_N = 10


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("euc-kr", "replace")


def _plain(xml_text: str) -> str:
    """태그 제거 + 공백 정규화한 평문."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", xml_text)).strip()


def _excerpt_from_heading(plain_text: str, length: int = EXCERPT_CHARS) -> str:
    """'감사의견' 표제부터 length 글자만큼 잘라낸다. 없으면 문서 처음부터."""
    m = _OPINION_HEADING_RE.search(plain_text)
    start = m.start() if m else 0
    return plain_text[start : start + length]


def _write_examples_md(
    path: Path,
    unknown_opinion: int,
    label_dist: collections.Counter,
    fair_phrase_count: int,
    examples: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# 감사의견 unknown 분석 리포트")
    lines.append("")
    lines.append(
        "회계사 지시에 따라 의견 자동해소는 패치에 반영하지 않았습니다. "
        "아래는 검토용 분석 결과입니다."
    )
    lines.append("")
    lines.append(f"- 총 unknown 건수: **{unknown_opinion}건**")
    dist_str = ", ".join(f"{label} {n}건" for label, n in label_dist.most_common())
    lines.append(f"- fallback 판정 분포: {dist_str if dist_str else '(없음)'}")
    lines.append(
        f"- 표준 적정 문구(`공정하게 표시하고 있습니다`) 보유: **{fair_phrase_count}건**"
    )
    lines.append("")
    lines.append(f"## 예시 {len(examples)}건")
    lines.append("")
    for i, ex in enumerate(examples, 1):
        lines.append(
            f"### {i}. {ex['corp_name']} "
            f"(corp_cls={ex['corp_cls']}, 결산월={ex['settlement_month']})"
        )
        lines.append("")
        lines.append(f"> {ex['excerpt']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="audit_facts 감사인 패치 생성 (+ 의견 분석 리포트)")
    parser.add_argument("--facts", default="dart_collected/audit_facts.jsonl")
    parser.add_argument("--out", default="dart_collected/audit_facts_patch.jsonl")
    parser.add_argument("--opinion-out", default="dart_collected/opinion_unknown_analysis.jsonl")
    parser.add_argument("--examples-out", default="reports/opinion_unknown_examples.md")
    parser.add_argument(
        "--limit", type=int, default=None, help="스모크 테스트용 — 처리 대상 행을 앞에서 N건만 본다"
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    facts_path = root / args.facts
    out_path = root / args.out
    opinion_out_path = root / args.opinion_out
    examples_out_path = root / args.examples_out

    dirty_auditor = recovered = 0
    unknown_opinion = resolved_opinion = 0
    missing_doc = 0
    processed = 0
    patches: list[dict] = []
    opinion_rows: list[dict] = []
    label_dist: collections.Counter = collections.Counter()
    fair_phrase_count = 0
    examples: list[dict] = []

    with open(facts_path, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            flr_nm = str(row.get("auditor") or "")
            opinion = str(row.get("audit_opinion") or "unknown")
            need_auditor = not is_firm(flr_nm)
            need_opinion = opinion == "unknown"
            if not (need_auditor or need_opinion):
                continue

            if args.limit is not None and processed >= args.limit:
                break
            processed += 1

            doc_path = row.get("doc_path")
            xml_text = ""
            if doc_path and (root / doc_path).exists():
                xml_text = _decode((root / doc_path).read_bytes())
            else:
                missing_doc += 1

            if need_auditor:
                dirty_auditor += 1
                resolved, source = resolve_auditor(xml_text, flr_nm)
                if resolved:
                    recovered += 1
                    patches.append(
                        {
                            "rcept_no": row["rcept_no"],
                            "auditor_raw": flr_nm,
                            "auditor_resolved": resolved,
                            "auditor_source": source,
                        }
                    )

            if need_opinion:
                unknown_opinion += 1
                plain = _plain(xml_text) if xml_text else ""
                label = classify_opinion_fulltext(plain)
                if label != "unknown":
                    resolved_opinion += 1
                label_dist[label] += 1
                has_fair = bool(_FAIR_PHRASE_RE.search(plain))
                if has_fair:
                    fair_phrase_count += 1
                opinion_rows.append(
                    {
                        "rcept_no": row["rcept_no"],
                        "corp_name": row.get("corp_name", ""),
                        "corp_cls": row.get("corp_cls", ""),
                        "settlement_month": row.get("settlement_month"),
                        "fallback_label": label,
                        "has_standard_fair_phrase": has_fair,
                    }
                )
                if plain and len(examples) < EXAMPLES_N:
                    examples.append(
                        {
                            "corp_name": row.get("corp_name", ""),
                            "corp_cls": row.get("corp_cls", ""),
                            "settlement_month": row.get("settlement_month"),
                            "excerpt": _excerpt_from_heading(plain),
                        }
                    )

            if processed % PROGRESS_EVERY == 0:
                print(
                    f"진행: {processed}행 처리 "
                    f"(감사인 복구 {recovered}/{dirty_auditor}, 의견 해소 {resolved_opinion}/{unknown_opinion})",
                    file=sys.stderr,
                )

    with open(out_path, "w", encoding="utf-8") as fh:
        for patch in patches:
            fh.write(json.dumps(patch, ensure_ascii=False) + "\n")

    with open(opinion_out_path, "w", encoding="utf-8") as fh:
        for row in opinion_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    _write_examples_md(examples_out_path, unknown_opinion, label_dist, fair_phrase_count, examples)

    auditor_rate = recovered / dirty_auditor if dirty_auditor else 1.0
    opinion_rate = resolved_opinion / unknown_opinion if unknown_opinion else 1.0
    summary = {
        "auditor_dirty": dirty_auditor,
        "auditor_recovered": recovered,
        "auditor_recovery_rate": round(auditor_rate, 4),
        "opinion_unknown": unknown_opinion,
        "opinion_resolved_informational": resolved_opinion,
        "opinion_resolve_rate_informational": round(opinion_rate, 4),
        "opinion_applied": False,
        "missing_doc": missing_doc,
        "patch_rows": len(patches),
    }
    out_path.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if auditor_rate < GATE_AUDITOR_RECOVERY:
        print(f"합격선 미달: 감사인 복구율 {auditor_rate:.1%} (>=95% 필요)")
        return 1
    print(
        f"합격선 통과: 감사인 복구율 {auditor_rate:.1%}. "
        f"의견 해소율은 정보용({opinion_rate:.1%})으로만 기록 — 회계사 지시에 따라 패치 미적용."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
