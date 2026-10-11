"""3층 규칙표 판정 실행기 — 사실·정책 표시·사채 기록 두 실행 → topic_stance.v2.jsonl (스펙 §6)

    python scripts/ledger_judge.py --ledger dart_collected/ledger --facts-audit dart_collected/audit_facts.jsonl \\
        --instruments RUN1.jsonl --instruments2 RUN2.jsonl --v1-stance ws8-out/.../topic_stance.jsonl \\
        --out topic_stance.v2.jsonl [--tables dart_collected/tables] [--rcept-list FILE] [--workers N]

입력:
  - <ledger>/facts.jsonl, <ledger>/policy_marks.jsonl (2층 ①③)
  - --instruments / --instruments2: 2층 ② 독립 두 실행. (rcept_no, fs_scope)마다 마지막 줄을 쓰고,
    model_used 가 --model(기본 gpt-5.6-luna)이 아닌 줄은 그 실행에 없는 것으로 친다(R19).
  - --v1-stance: 옛 topic_judge 결과. PPA_MEASUREMENT_PERIOD 값만 옮겨 쓴다(Q7, R3).
  - --tables: 1층 표 파일. 근거 줄의 주석 번호·제목, 무형자산 변동표·금융상품 범주표 여부, 읽기 실패 주석에 쓴다.
    디렉터리가 없으면 표 정보 없이 판정한다(근거 줄에 주석이 빠지고 NOT_APPLICABLE 은 나오지 않는다).

출력(이미 있으면 멈춘다 — 기존 파일을 덮지 않는다):
  - <out>                              판정 한 줄에 하나(주제 × fs_scope)
  - <out 폴더>/records.v2.jsonl         판정 + 근거 사실 전체·사채 기록·정책 표시(표본 페이지용)
  - <out 이름>.summary.json             주제별 값·held_reason 수, 버전, 입력 통계
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT.parent / "dart-search-mcp" / "dart_collected"
ACCEPTED_MODEL = "gpt-5.6-luna"
_RUN_TOPICS = ("CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION")


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_runs(path: Path, model: str, keep: set[str] | None) -> tuple[dict, Counter]:
    """(rcept_no, fs_scope) → 기록 목록(마지막 줄). 다른 모델 줄은 그 실행에 없는 것으로 친다."""
    last: dict = {}
    stats: Counter = Counter()
    for r in _jsonl(path):
        if keep is not None and r.get("rcept_no") not in keep:
            continue
        stats["lines"] += 1
        last[(r.get("rcept_no"), r.get("fs_scope") or "separate")] = r
    out = {}
    for key, r in last.items():
        if r.get("model_used") != model:
            stats["dropped_model"] += 1
            continue
        out[key] = list(r.get("records") or [])
    stats["keys"] = len(out)
    return out, stats


def _group(path: Path, keep: set[str] | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if path.exists():
        for r in _jsonl(path):
            if keep is None or r.get("rcept_no") in keep:
                out[r["rcept_no"]].append(r)
    return out


def process(job: dict) -> dict:
    from dart_search_mcp.ledger.judge_rules import judge_filing, load_rules, scope_meta

    groups = load_rules()["concept_groups"]
    rcept = job["rcept_no"]
    tables, err = [], None
    tdir = job.get("tables_dir")
    if tdir:
        p = Path(tdir) / f"{rcept}.json.gz"
        if p.exists():
            try:
                from dart_search_mcp.ledger.tables import load_filing
                tables = load_filing(p).get("tables") or []
            except Exception as exc:  # 표 파일 하나가 실행 전체를 멈추지 않게
                err = f"{type(exc).__name__}: {exc}"[:200]
    scopes = set(job["scopes"]) | {t.get("fs_scope") for t in tables if t.get("fs_scope")}
    scopes = sorted(scopes) or ["separate"]
    # PPA: v1 행은 범위가 없어 한 범위(연결이 있으면 연결)에만 내고, 사업결합 표 확인은 공시 단위로 한다
    bc_concepts = ("BC_CONSIDERATION", "BC_NET_ASSETS_FV", "BC_RELATED_COST")
    filing_meta = {"ppa_scope": "consolidated" if "consolidated" in scopes else scopes[0],
                   "bc_filing": any(f.get("concept") in bc_concepts and
                                    f.get("period") in ("current_end", "current_period", "prior_end", "prior_period")
                                    for f in job["facts"])}
    rows, records = [], []
    for scope in scopes:
        facts = [f for f in job["facts"] if (f.get("fs_scope") or "separate") == scope]
        meta = dict(job["meta"])
        meta.update(filing_meta)
        if tables:
            meta.update(scope_meta(tables, scope))
        runs = {"run1": job["run1"].get(scope), "run2": job["run2"].get(scope)}
        for row in judge_filing(rcept, scope, facts, runs, job["marks"], meta):
            rows.append(row)
            group = set(groups[row["topic"]])
            rec = dict(row)
            rec["facts"] = [f for f in facts if f.get("concept") in group]
            rec["marks"] = [m for m in job["marks"] if m.get("topic") == row["topic"]]
            if row["topic"] in _RUN_TOPICS:
                rec["instruments"] = runs
            records.append(rec)
    return {"rcept_no": rcept, "rows": rows, "records": records, "error": err}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="3층 규칙표 판정 실행기")
    ap.add_argument("--ledger", type=Path, default=DATA / "ledger")
    ap.add_argument("--facts-audit", type=Path, default=DATA / "audit_facts.jsonl")
    ap.add_argument("--instruments", type=Path, required=True, help="2층 ② 1차 실행")
    ap.add_argument("--instruments2", type=Path, required=True, help="2층 ② 2차 실행(독립)")
    ap.add_argument("--v1-stance", type=Path, help="옛 topic_judge topic_stance.jsonl(PPA 값 옮김)")
    ap.add_argument("--tables", type=Path, default=DATA / "tables")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rcept-list", type=Path, help="접수번호 한 줄에 하나(이 공시만 판정)")
    ap.add_argument("--model", default=ACCEPTED_MODEL)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = ap.parse_args(argv)

    out = args.out
    rec_path = out.parent / "records.v2.jsonl"
    sum_path = out.with_name(out.name.removesuffix(".jsonl") + ".summary.json")
    existing = [str(p) for p in (out, rec_path, sum_path) if p.exists()]
    if existing:
        print(f"이미 있음(덮지 않음): {', '.join(existing)}", file=sys.stderr)
        return 2
    t0 = time.time()
    keep = None
    if args.rcept_list:
        keep = {ln.strip() for ln in args.rcept_list.read_text(encoding="utf-8").splitlines() if ln.strip()}

    facts = _group(args.ledger / "facts.jsonl", keep)
    marks = _group(args.ledger / "policy_marks.jsonl", keep)
    run1, s1 = load_runs(args.instruments, args.model, keep)
    run2, s2 = load_runs(args.instruments2, args.model, keep)
    audit = {r["rcept_no"]: r for r in _jsonl(args.facts_audit) if keep is None or r.get("rcept_no") in keep}
    v1 = {}
    if args.v1_stance:
        for r in _jsonl(args.v1_stance):
            if r.get("topic") == "PPA_MEASUREMENT_PERIOD" and (keep is None or r.get("rcept_no") in keep):
                v1[r["rcept_no"]] = r

    rcepts = set(facts) | set(marks) | {k[0] for k in run1} | {k[0] for k in run2} | set(v1)
    if keep is not None:
        rcepts &= keep
    by_rcept_runs = defaultdict(lambda: ({}, {}))
    for k, v in run1.items():
        by_rcept_runs[k[0]][0][k[1]] = v
    for k, v in run2.items():
        by_rcept_runs[k[0]][1][k[1]] = v
    tables_dir = str(args.tables) if args.tables and args.tables.is_dir() else None

    def jobs():
        for rc in sorted(rcepts):
            a = audit.get(rc) or v1.get(rc) or {}
            r1, r2 = by_rcept_runs.get(rc, ({}, {}))
            scopes = {f.get("fs_scope") or "separate" for f in facts.get(rc, [])} | set(r1) | set(r2)
            yield {"rcept_no": rc, "facts": facts.get(rc, []), "marks": marks.get(rc, []), "run1": r1, "run2": r2,
                   "scopes": sorted(scopes), "tables_dir": tables_dir,
                   "meta": {"corp_code": a.get("corp_code"), "fiscal_year": a.get("fiscal_year"),
                            "v1_ppa": v1.get(rc)}}

    out.parent.mkdir(parents=True, exist_ok=True)
    values: dict[str, Counter] = defaultdict(Counter)
    held: Counter = Counter()
    held_by_topic: dict[str, Counter] = defaultdict(Counter)
    errors: dict[str, str] = {}
    n_rows = 0
    with out.open("w", encoding="utf-8") as fo, rec_path.open("w", encoding="utf-8") as fr:
        if args.workers > 1:
            pool = Pool(args.workers)
            it = pool.imap(process, jobs(), chunksize=16)
        else:
            pool, it = None, map(process, jobs())
        try:
            for res in it:
                if res["error"]:
                    errors[res["rcept_no"]] = res["error"]
                for row in res["rows"]:
                    fo.write(json.dumps(row, ensure_ascii=False) + "\n")
                    values[row["topic"]][row["value"]] += 1
                    if row["held_reason"]:
                        held[row["held_reason"]] += 1
                        held_by_topic[row["topic"]][row["held_reason"]] += 1
                    n_rows += 1
                for rec in res["records"]:
                    fr.write(json.dumps(rec, ensure_ascii=False) + "\n")
        finally:
            if pool:
                pool.close()
                pool.join()

    from dart_search_mcp.ledger.judge_rules import PARSER_VERSION, load_rules
    concepts_versions = Counter(f.get("concepts_version") for fs in facts.values() for f in fs)
    summary = {
        "parser_version": PARSER_VERSION, "rules_version": load_rules()["rules_version"],
        "concepts_versions": dict(concepts_versions),
        "generated_at": datetime.now(timezone.utc).isoformat(), "elapsed_sec": round(time.time() - t0, 1),
        "filings": len(rcepts), "rows": n_rows,
        "values_by_topic": {t: dict(c) for t, c in sorted(values.items())},
        "held_reason": dict(held), "held_reason_by_topic": {t: dict(c) for t, c in sorted(held_by_topic.items())},
        "instruments": {"model": args.model, "run1": dict(s1), "run2": dict(s2)},
        "tables_dir": tables_dir, "table_errors": errors,
        "inputs": {"ledger": str(args.ledger), "facts_audit": str(args.facts_audit),
                   "instruments": str(args.instruments), "instruments2": str(args.instruments2),
                   "v1_stance": str(args.v1_stance) if args.v1_stance else None},
    }
    sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("filings", "rows", "held_reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
