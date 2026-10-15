"""2층 ① 항목 금액 사실 실행기 — 1층 표 파일(<tables>/<접수번호>.json.gz) → <out-dir>/facts.*

    python scripts/ledger_facts.py --tables dart_collected/tables --out-dir dart_collected/ledger \\
        [--rcept-list FILE] [--workers N] [--concepts PATH] [--limit N]

출력(이미 있으면 멈춘다 — 기존 파일을 덮지 않는다):
  - facts.jsonl            사실 한 줄에 하나(스펙 §5.2). c11부터 문장 사실(투자부동산 공정가치, source.sentence_id)도 담는다
  - facts.unmatched.tsv    관련 주석 안 미매칭 금액 행 이름 빈도 상위 500(count, label)
  - facts.summary.json     공시·표·사실 수, 개념별·상태별 수, 충돌 경고, 주제별 매칭률(정의 A·B), 주제별 미매칭 상위
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT.parent / "dart-search-mcp" / "dart_collected"
OUTPUTS = ("facts.jsonl", "facts.unmatched.tsv", "facts.summary.json")
UNMATCHED_TOP = 500
_STATE: dict = {}


def _init(concepts_path: str | None) -> None:
    from dart_search_mcp.ledger.facts import load_concepts, load_topics
    p = Path(concepts_path) if concepts_path else None
    _STATE["concepts"] = load_concepts(p)
    _STATE["topics"] = load_topics(p)
    _STATE["concepts_path"] = p


def process(job: tuple[str, str]) -> dict:
    rcept, tables_dir = job
    from dart_search_mcp.ledger.facts import coverage, extract_facts, sentence_facts, unmatched_rows
    from dart_search_mcp.ledger.tables import load_filing

    try:
        filing = load_filing(Path(tables_dir) / f"{rcept}.json.gz")
        tables = filing.get("tables", [])
        stats: Counter = Counter()
        facts = extract_facts(tables, _STATE["concepts"], stats=stats)
        facts += sentence_facts(filing, concepts_path=_STATE.get("concepts_path"))  # c11: 투자부동산 공정가치 문장
        topic_unmatched: dict = {}
        cov = coverage(tables, _STATE["concepts"], _STATE["topics"], unmatched=topic_unmatched)
        return {"rcept_no": rcept, "error": None, "tables": len(tables),
                "table_status": dict(Counter(t.get("status") for t in tables)), "facts": facts,
                "stats": dict(stats), "unmatched": dict(unmatched_rows(tables, _STATE["concepts"])),
                "coverage": cov, "topic_unmatched": {k: dict(v) for k, v in topic_unmatched.items()},
                "parser_version": filing.get("parser_version")}
    except Exception as exc:  # 공시 하나가 실행 전체를 멈추지 않게
        return {"rcept_no": rcept, "error": f"{type(exc).__name__}: {exc}"[:200], "tables": 0, "table_status": {},
                "facts": [], "stats": {}, "unmatched": {}, "coverage": {}, "topic_unmatched": {},
                "parser_version": None}


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else None


def summarize(results: list[dict], concepts_version: str, elapsed: float, missing: int = 0) -> tuple[dict, Counter]:
    stats: Counter = Counter()
    unmatched: Counter = Counter()
    table_status: Counter = Counter()
    errors: Counter = Counter()
    by_concept: Counter = Counter()
    by_status: Counter = Counter()
    by_period: Counter = Counter()
    filings_with: Counter = Counter()
    cov: dict = {}
    topic_unmatched: dict = {}
    versions: Counter = Counter()
    for r in results:
        if r["error"]:
            errors[r["error"].split(":")[0]] += 1
            continue
        versions[r["parser_version"]] += 1
        stats.update(r["stats"])
        unmatched.update(r["unmatched"])
        table_status.update(r["table_status"])
        for f in r["facts"]:
            by_concept[f["concept"]] += 1
            by_status[f["source_status"]] += 1
            by_period[f["period"]] += 1
        filings_with.update({f["concept"] for f in r["facts"]})
        for t, s in r["coverage"].items():
            agg = cov.setdefault(t, {"counts": Counter(), "excluded_by_rule": Counter()})
            agg["counts"].update({k: v for k, v in s.items() if k != "excluded_by_rule"})
            agg["excluded_by_rule"].update(s.get("excluded_by_rule") or {})
        for t, c in r["topic_unmatched"].items():
            topic_unmatched.setdefault(t, Counter()).update(c)
    coverage = {}
    for t, a in sorted(cov.items()):
        s = a["counts"]
        coverage[t] = {**dict(s), "excluded_by_rule": dict(a["excluded_by_rule"].most_common()),
                       "ratio_all_rows": _ratio(s["matched"], s["rows"]),
                       "ratio_keyword_rows": _ratio(s["kw_matched"], s["kw_rows"]),
                       "ratio_keyword_restored": _ratio(s["kw_all_matched"], s["kw_all_rows"])}
    summary = {
        "concepts_version": concepts_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "filings": len(results),
        "filings_missing": missing,
        "filings_failed": sum(1 for r in results if r["error"]),
        "filing_errors": dict(errors.most_common(20)),
        "table_parser_versions": dict(versions),
        "tables": sum(table_status.values()),
        "table_status": dict(table_status),
        "facts": sum(by_concept.values()),
        "facts_by_concept": dict(sorted(by_concept.items())),
        "filings_with_concept": dict(sorted(filings_with.items())),
        "facts_by_source_status": dict(by_status),
        "facts_by_period": dict(by_period),
        "conflict_rows": stats.get("conflict_rows", 0),
        "conflicts": dict(sorted((k, v) for k, v in stats.items() if k.startswith("conflict:"))),
        "tables_read_failed_skipped": stats.get("tables_read_failed_skipped", 0),
        "facts_region_title_only": stats.get("facts_region_title_only", 0),
        "coverage": coverage,
        "coverage_definition": {
            "ratio_all_rows": "주제 개념 note: 구간 주석 표의 금액 item 행 중 주제 개념에 걸린 비율(정의 A)",
            "ratio_keyword_rows": "그중 주제 낱말 행(topics.keywords) 중 제외 규칙(topics.exclusions)에 안 걸린 행만(정의 B, 판정 기준)",
            "ratio_keyword_restored": "정의 B에서 제외 규칙으로 뺀 행을 되돌린 값(주제 낱말 행 전부)",
        },
        "topic_unmatched_top": {t: c.most_common(30) for t, c in sorted(topic_unmatched.items())},
    }
    return summary, unmatched


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="2층 ① 항목 금액 사실 실행기")
    ap.add_argument("--tables", type=Path, default=DATA / "tables")
    ap.add_argument("--out-dir", type=Path, default=DATA / "ledger")
    ap.add_argument("--rcept-list", type=Path, help="접수번호 한 줄에 하나")
    ap.add_argument("--concepts", help="개념 사전 경로(기본: ledger/concepts.json)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    existing = [n for n in OUTPUTS if (args.out_dir / n).exists()]
    if existing:
        print(f"이미 있음(덮지 않음): {', '.join(existing)} in {args.out_dir}", file=sys.stderr)
        return 2
    if args.rcept_list:
        listed = [ln.strip() for ln in args.rcept_list.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rcepts = [r for r in listed if (args.tables / f"{r}.json.gz").exists()]
        missing = len(listed) - len(rcepts)
    else:
        rcepts = sorted(p.name[: -len(".json.gz")] for p in args.tables.glob("*.json.gz"))
        missing = 0
    if args.limit:
        rcepts = rcepts[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    from dart_search_mcp.ledger.facts import _load_raw
    version = _load_raw(Path(args.concepts) if args.concepts else None).get("concepts_version", "")

    jobs = [(r, str(args.tables)) for r in rcepts]
    results: list[dict] = []
    t0 = time.time()
    facts_path = args.out_dir / "facts.jsonl"
    tmp = facts_path.with_suffix(".jsonl.tmp")
    with Pool(args.workers, initializer=_init, initargs=(args.concepts,)) as pool, \
            tmp.open("w", encoding="utf-8") as fh:
        for i, res in enumerate(pool.imap_unordered(process, jobs, chunksize=8), 1):
            for f in res["facts"]:
                fh.write(json.dumps(f, ensure_ascii=False, separators=(",", ":")) + "\n")
            res["facts"] = [{"concept": f["concept"], "source_status": f["source_status"], "period": f["period"]}
                            for f in res["facts"]]
            results.append(res)
            if i % 2000 == 0:
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    tmp.replace(facts_path)

    summary, unmatched = summarize(results, version, time.time() - t0, missing)
    with (args.out_dir / "facts.unmatched.tsv").open("w", encoding="utf-8") as fh:
        fh.write("count\tlabel\n")
        for label, n in unmatched.most_common(UNMATCHED_TOP):
            fh.write(f"{n}\t{label}\n")
    (args.out_dir / "facts.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("concepts_version", "filings", "filings_missing", "filings_failed", "facts")},
                     ensure_ascii=False))
    print(json.dumps({t: (c["ratio_all_rows"], c["ratio_keyword_rows"], c["ratio_keyword_restored"])
                      for t, c in summary["coverage"].items()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
