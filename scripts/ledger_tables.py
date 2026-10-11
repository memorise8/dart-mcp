"""1층 표 장부 실행기 — 공시마다 <out-dir>/<접수번호>.json.gz 를 쓰고 <out-dir>.summary.json 에 요약한다.

    python scripts/ledger_tables.py --docs dart_collected/docs --out-dir dart_collected/tables \\
        [--rcept-list FILE] [--workers N] [--dart-agent PATH] [--limit N]

- 공시당 하나씩 multiprocessing.Pool 로 처리한다.
- 결과 파일이 이미 있고 parser_version 이 같으면 건너뛴다(이어하기). 기존 파일을 다른 버전으로 덮지 않으려면
  --out-dir 를 새로 준다.
- 요약: 공시 수, 표 수, 상태별 수·비율, 검사별 통과 비율, 실패 사유 상위 20.
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
CHECKS = ("period_found", "unit_found", "sum_ok", "identity_ok")


def _configure(dart_agent: str | None) -> None:
    # _dart_agent._mod 는 lru_cache 라 파서 import 전에 경로를 정해야 한다
    from dart_search_mcp.ledger import _dart_agent
    _dart_agent.configure(dart_agent)


def _files(folder: Path) -> tuple[list[tuple[str, bytes]], dict]:
    manifest: dict = {}
    mp = folder / "manifest.json"
    if mp.exists():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    names = [f["filename"] for f in manifest.get("files", []) if isinstance(f, dict) and f.get("filename")]
    paths = [folder / n for n in names if (folder / n).exists()] or sorted(folder.glob("*.xml"))
    return [(p.name, p.read_bytes()) for p in paths], manifest


def _stats(rcept: str, filing: dict, skipped: bool) -> dict:
    status: Counter = Counter()
    reasons: Counter = Counter()
    checks = {k: Counter() for k in CHECKS}
    for t in filing.get("tables", []):
        status[t.get("status", "?")] += 1
        if t.get("status") != "verified":
            reasons.update(t.get("issues", []))
        for k in CHECKS:
            v = (t.get("checks") or {}).get(k)
            if v is not None:
                checks[k][str(bool(v)).lower()] += 1
    return {"rcept_no": rcept, "skipped": skipped, "error": None, "tables": sum(status.values()),
            "status": dict(status), "reasons": dict(reasons), "checks": {k: dict(v) for k, v in checks.items()}}


def process(job: tuple[str, str, str]) -> dict:
    rcept, docs, out_dir = job
    from dart_search_mcp.ledger.tables import PARSER_VERSION, build_filing, load_filing, write_filing

    out = Path(out_dir) / f"{rcept}.json.gz"
    try:
        if out.exists():
            existing = load_filing(out)
            if existing.get("parser_version") == PARSER_VERSION:
                return _stats(rcept, existing, skipped=True)
        files, manifest = _files(Path(docs) / rcept)
        if not files:
            raise FileNotFoundError("xml 없음")
        filing = build_filing(rcept, files, manifest)
        write_filing(Path(out_dir), rcept, filing)
        return _stats(rcept, filing, skipped=False)
    except Exception as exc:  # 공시 하나가 실행 전체를 멈추지 않게
        return {"rcept_no": rcept, "skipped": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "tables": 0, "status": {}, "reasons": {}, "checks": {}}


def summarize(results: list[dict], parser_version: str, elapsed: float) -> dict:
    status: Counter = Counter()
    reasons: Counter = Counter()
    checks = {k: Counter() for k in CHECKS}
    errors: Counter = Counter()
    for r in results:
        status.update(r["status"])
        reasons.update(r["reasons"])
        for k, v in r["checks"].items():
            checks[k].update(v)
        if r["error"]:
            errors[r["error"].split(":")[0]] += 1
    n_tables = sum(status.values())
    return {
        "parser_version": parser_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "filings": len(results),
        "filings_written": sum(1 for r in results if not r["skipped"] and not r["error"]),
        "filings_skipped": sum(1 for r in results if r["skipped"]),
        "filings_failed": sum(1 for r in results if r["error"]),
        "filing_errors": dict(errors.most_common(20)),
        "tables": n_tables,
        "status": dict(status),
        "status_ratio": {k: round(v / n_tables, 4) for k, v in status.items()} if n_tables else {},
        "check_pass_ratio": {
            k: round(c.get("true", 0) / (c.get("true", 0) + c.get("false", 0)), 4) if sum(c.values()) else None
            for k, c in checks.items()
        },
        "check_counts": {k: dict(c) for k, c in checks.items()},
        "top_failure_reasons": reasons.most_common(20),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="1층 표 장부 실행기")
    ap.add_argument("--docs", type=Path, default=DATA / "docs")
    ap.add_argument("--out-dir", type=Path, default=DATA / "tables")
    ap.add_argument("--rcept-list", type=Path, help="접수번호 한 줄에 하나")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--dart-agent", help="dart-agent 저장소 경로(기본: DART_AGENT_ROOT 또는 기본 경로)")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    _configure(args.dart_agent)
    from dart_search_mcp.ledger.tables import PARSER_VERSION

    if args.rcept_list:
        rcepts = [ln.strip() for ln in args.rcept_list.read_text(encoding="utf-8").splitlines() if ln.strip()]
    else:
        rcepts = sorted(p.name for p in args.docs.iterdir() if p.is_dir())
    if args.limit:
        rcepts = rcepts[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [(r, str(args.docs), str(args.out_dir)) for r in rcepts]
    results: list[dict] = []
    t0 = time.time()
    with Pool(args.workers, initializer=_configure, initargs=(args.dart_agent,)) as pool:
        for i, res in enumerate(pool.imap_unordered(process, jobs, chunksize=4), 1):
            results.append(res)
            if i % 500 == 0:
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)

    summary = summarize(results, PARSER_VERSION, time.time() - t0)
    summary_path = Path(str(args.out_dir).rstrip("/") + ".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("filings", "filings_failed", "tables", "status")}, ensure_ascii=False))
    print(f"summary → {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
