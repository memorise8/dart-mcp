"""2층 정책·예외 문장 표시 실행기 — 1층 표 파일(<tables>/<접수번호>.json.gz) → <out-dir>/policy_marks.*

    python scripts/ledger_policy_marks.py --tables dart_collected/tables --out-dir dart_collected/ledger \\
        [--rcept-list FILE] [--workers N] [--limit N]

출력(이미 있으면 멈춘다 — 기존 파일을 덮지 않는다):
  - policy_marks.jsonl         표시 한 줄에 하나
  - policy_marks.summary.json  공시 수, 주제×종류 수, 예외 종류별 수
  - policy_marks.failed.txt    처리에 실패한 접수번호(없으면 빈 파일)
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
OUTPUTS = ("policy_marks.jsonl", "policy_marks.summary.json", "policy_marks.failed.txt")


def process(job: tuple[str, str]) -> dict:
    rcept, tables_dir = job
    from dart_search_mcp.ledger.policy_marks import mark_policies
    from dart_search_mcp.ledger.tables import load_filing

    try:
        filing = load_filing(Path(tables_dir) / f"{rcept}.json.gz")
        marks = mark_policies(rcept, filing.get("paragraphs", []), filing.get("tables", []))
        return {"rcept_no": rcept, "error": None, "marks": marks}
    except Exception as exc:  # 공시 하나가 실행 전체를 멈추지 않게
        return {"rcept_no": rcept, "error": f"{type(exc).__name__}: {exc}"[:200], "marks": []}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="2층 정책·예외 문장 표시 실행기")
    ap.add_argument("--tables", type=Path, default=DATA / "tables")
    ap.add_argument("--out-dir", type=Path, default=DATA / "ledger")
    ap.add_argument("--rcept-list", type=Path, help="접수번호 한 줄에 하나")
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

    by_topic_kind: dict[str, Counter] = {}
    by_exception: Counter = Counter()
    filings_with: Counter = Counter()
    errors: Counter = Counter()
    failed = 0
    failed_rcepts: list[str] = []
    t0 = time.time()
    path = args.out_dir / OUTPUTS[0]
    tmp = path.with_suffix(".jsonl.tmp")
    with Pool(args.workers) as pool, tmp.open("w", encoding="utf-8") as fh:
        for i, res in enumerate(pool.imap_unordered(process, [(r, str(args.tables)) for r in rcepts], chunksize=8), 1):
            if res["error"]:
                failed += 1
                failed_rcepts.append(res["rcept_no"])
                errors[res["error"].split(":")[0]] += 1
                continue
            seen = set()
            for m in res["marks"]:
                fh.write(json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n")
                by_topic_kind.setdefault(m["topic"], Counter())[m["kind"]] += 1
                if m["exception"]:
                    by_exception[f'{m["topic"]}:{m["exception"]}'] += 1
                seen.add((m["topic"], m["kind"]))
            filings_with.update(f"{t}:{k}" for t, k in seen)
            if i % 2000 == 0:
                print(f"{i}/{len(rcepts)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    tmp.replace(path)

    summary = {
        "marks_version": "p1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(time.time() - t0, 1),
        "filings": len(rcepts), "filings_missing": missing, "filings_failed": failed,
        "filing_errors": dict(errors.most_common(20)),
        "marks_by_topic_kind": {t: dict(c) for t, c in sorted(by_topic_kind.items())},
        "filings_with_topic_kind": dict(sorted(filings_with.items())),
        "marks_by_exception": dict(sorted(by_exception.items())),
    }
    failed_path = args.out_dir / OUTPUTS[2]
    ftmp = failed_path.with_suffix(".txt.tmp")
    ftmp.write_text("".join(r + "\n" for r in sorted(failed_rcepts)), encoding="utf-8")
    ftmp.replace(failed_path)
    spath = args.out_dir / OUTPUTS[1]
    stmp = spath.with_suffix(".json.tmp")
    stmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    stmp.replace(spath)
    print(json.dumps({k: summary[k] for k in ("filings", "filings_missing", "filings_failed")}, ensure_ascii=False))
    print(json.dumps(summary["marks_by_topic_kind"], ensure_ascii=False))
    print(json.dumps(summary["marks_by_exception"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
