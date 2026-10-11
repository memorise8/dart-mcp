"""1층 완료 기준 측정(스펙 §4.8) — 표 기록 디렉터리와 표본 목록 → 비율, 기간 미판별 표 유형별 분해, goldset 1층 채점.

    python scripts/ledger_measure.py --out-dir DIR --sample sample300.txt [--gold-dir DIR] [--json OUT]

비율 정의(분모):
- period_found_ratio, unit_found_ratio: 값 열이 있는 표(검사 값이 true/false인 표)
- sum_ok_ratio: 합계·소계 행이 있어 합계를 검사한 표
- read_failed_ratio: 전체 표
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dart_search_mcp.ledger.tables import load_filing  # noqa: E402

CHECKS = ("period_found", "unit_found", "sum_ok", "identity_ok")

# R8: 완료 기준(기간·단위)은 재무 금액 표에서 잰다. 아래 구간의 표는 보정 비율의 분모에서 뺀다(표 기록은 그대로).
#   auditor_report·other — 감사인 보고서·외부감사 실시내용 서식(감사참여자·주요 감사실시내용·커뮤니케이션)·표지·목차
#   statement_ce — 자본변동표 본문(기간이 행으로 내려가 열 기간이 없다)
EXCLUDED_REGION_KINDS = ("auditor_report", "other", "statement_ce")


def _ratio(c: Counter) -> float | None:
    n = c.get("true", 0) + c.get("false", 0)
    return round(c.get("true", 0) / n, 4) if n else None


def _iter_tables(out_dir: Path, sample: list[str]):
    for rcept in sample:
        path = Path(out_dir) / f"{rcept}.json.gz"
        if path.exists():
            yield rcept, load_filing(path)
        else:
            yield rcept, None


def measure_tables(out_dir: Path, sample: list[str]) -> dict:
    """원 정의 비율과, EXCLUDED_REGION_KINDS 표를 뺀 보정 비율(adjusted)을 함께 낸다. 기준은 보정 비율에 건다."""
    checks = {k: Counter() for k in CHECKS}
    adj_checks = {k: Counter() for k in CHECKS}
    adj_status: Counter = Counter()
    status: Counter = Counter()
    missing: list[str] = []
    filings = 0
    for rcept, filing in _iter_tables(out_dir, sample):
        if filing is None:
            missing.append(rcept)
            continue
        filings += 1
        for t in filing.get("tables", []):
            status[t.get("status", "?")] += 1
            kept = (t.get("region") or {}).get("kind") not in EXCLUDED_REGION_KINDS
            if kept:
                adj_status[t.get("status", "?")] += 1
            for k in CHECKS:
                v = (t.get("checks") or {}).get(k)
                if v is not None:
                    checks[k][str(bool(v)).lower()] += 1
                    if kept:
                        adj_checks[k][str(bool(v)).lower()] += 1
    n = sum(status.values())
    n_adj = sum(adj_status.values())
    return {
        "filings": filings,
        "filings_missing": missing,
        "tables": n,
        "status": dict(status),
        "period_found_ratio": _ratio(checks["period_found"]),
        "unit_found_ratio": _ratio(checks["unit_found"]),
        "sum_ok_ratio": _ratio(checks["sum_ok"]),
        "identity_ok_ratio": _ratio(checks["identity_ok"]),
        "read_failed_ratio": round(status.get("read_failed", 0) / n, 4) if n else None,
        "counts": {k: dict(v) for k, v in checks.items()},
        "adjusted": {
            "excluded_region_kinds": list(EXCLUDED_REGION_KINDS),
            "tables": n_adj,
            "excluded_tables": n - n_adj,
            "period_found_ratio": _ratio(adj_checks["period_found"]),
            "unit_found_ratio": _ratio(adj_checks["unit_found"]),
            "sum_ok_ratio": _ratio(adj_checks["sum_ok"]),
            "identity_ok_ratio": _ratio(adj_checks["identity_ok"]),
            "read_failed_ratio": round(adj_status.get("read_failed", 0) / n_adj, 4) if n_adj else None,
            "counts": {k: dict(v) for k, v in adj_checks.items()},
        },
    }


# ---------- 기간 미판별 표 유형 ----------
_TYPE_RULES = (
    ("equity_statement", ("자본변동표",)),
    ("movement", ("변동", "증감", "기초", "기말", "취득", "처분", "대체", "상각", "증가", "감소")),
    ("maturity", ("만기", "1년", "1년이내", "1년이하", "1년초과", "5년", "이내", "초과", "잔존")),
    ("category_matrix", ("상각후원가", "공정가치측정", "FVPL", "FVOCI", "당기손익-공정가치", "기타포괄손익-공정가치")),
    ("fair_value_level", ("수준1", "수준2", "수준3", "수준 1")),
    ("related_party", ("특수관계",)),
    ("sensitivity", ("민감도", "상승", "하락", "변동시")),
    ("aging", ("연체", "손상", "경과")),
    ("shares_capital", ("주식수", "발행주식", "보통주", "우선주")),
    ("interest_rate_terms", ("이자율", "연이자율", "만기일", "발행일", "차입처")),
    ("rate_or_ratio", ("%", "비율", "지분율")),
)


def table_type(t: dict) -> str:
    region = (t.get("region") or {}).get("kind", "")
    if region == "statement_ce":
        return "equity_statement"
    if region.startswith("statement_"):
        return region
    hdr = " ".join("/".join(c.get("header_path") or []) for c in t.get("columns", []))
    text = re.sub(r"\s+", "", f"{t.get('title') or ''} {hdr}")
    for name, words in _TYPE_RULES:
        if any(re.sub(r"\s+", "", w) in text for w in words):
            return name
    if not hdr.strip("/ "):
        return "no_header"
    return "other"


def period_unknown_breakdown(out_dir: Path, sample: list[str], examples: int = 3) -> list[dict]:
    by: Counter = Counter()
    ex: dict[str, list[str]] = {}
    for _rcept, filing in _iter_tables(out_dir, sample):
        for t in (filing or {}).get("tables", []):
            if (t.get("checks") or {}).get("period_found") is False:
                k = table_type(t)
                by[k] += 1
                if len(ex.setdefault(k, [])) < examples:
                    ex[k].append(f"{t['table_id']} | {(t.get('title') or '')[:60]}")
    total = sum(by.values())
    return [{"type": k, "n": v, "share": round(v / total, 4), "examples": ex[k]} for k, v in by.most_common()]


def main(argv: list[str] | None = None) -> int:
    from dart_search_mcp.ledger.goldset import GOLDSET_PATH, load_goldset, score_layer1

    ap = argparse.ArgumentParser(description="1층 완료 기준 측정")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--sample", type=Path, required=True)
    ap.add_argument("--gold-dir", type=Path, help="goldset 공시 표 기록 디렉터리(기본: --out-dir)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    sample = [ln.strip() for ln in args.sample.read_text(encoding="utf-8").splitlines() if ln.strip()]
    result = measure_tables(args.out_dir, sample)
    result["period_unknown_by_type"] = period_unknown_breakdown(args.out_dir, sample)
    gold = score_layer1(load_goldset(GOLDSET_PATH), args.gold_dir or args.out_dir)
    result["goldset_layer1"] = {"ok": sum(g["ok"] for g in gold), "n": len(gold), "items": gold}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
