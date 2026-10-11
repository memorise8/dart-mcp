"""완료 기준 측정 — 1층 표 비율(스펙 §4.8)과, 입력이 있으면 정답 묶음 층별 채점·v1/v2 비교(스펙 §7.1·§7.2).

1층만:
    python scripts/ledger_measure.py --out-dir TABLES --sample sample300.txt [--gold-dir DIR] [--json OUT]

전체 층(판정 실행 포함):
    python scripts/ledger_measure.py --out-dir TABLES [--sample LIST] --facts FACTS.jsonl --marks MARKS.jsonl \
        --instruments RUN1.jsonl --instruments2 RUN2.jsonl --v1-stance V1.jsonl [--classified classified.jsonl] \
        --work-dir DIR [--facts-audit audit_facts.jsonl]
  → DIR/ledger/(표본으로 좁힌 facts·policy_marks), DIR/topic_stance.v2.jsonl(scripts/ledger_judge.py),
    DIR/ledger.measure.json, DIR/ledger.measure.md
  판정이 이미 있으면 --v2-stance V2.jsonl 로 주고 판정을 건너뛴다(--md 로 md 경로를 정할 수 있다).

비율 정의(분모):
- period_found_ratio, unit_found_ratio: 값 열이 있는 표(검사 값이 true/false인 표)
- sum_ok_ratio: 합계·소계 행이 있어 합계를 검사한 표
- read_failed_ratio: 전체 표

--sample 이 있으면 모든 층(판정 입력·채점·비교)을 그 접수번호로 좁힌다. 정답 항목 중 표본 밖은 skipped 로 센다.
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


def _filter_jsonl(src: Path, dst: Path, keep: set[str] | None) -> int:
    """keep 이 없으면 심볼릭 링크(전체), 있으면 그 접수번호 줄만 쓴다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if keep is None:
        dst.symlink_to(Path(src).resolve())
        return -1
    n = 0
    with Path(src).open(encoding="utf-8") as fi, dst.open("w", encoding="utf-8") as fo:
        for line in fi:
            if line.strip() and json.loads(line).get("rcept_no") in keep:
                fo.write(line if line.endswith("\n") else line + "\n")
                n += 1
    return n


def _judge_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ledger_judge_cli", Path(__file__).with_name("ledger_judge.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ledger_judge_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_judge(args, keep: set[str] | None) -> Path:
    work: Path = args.work_dir
    ledger = work / "ledger"
    if ledger.exists():
        raise SystemExit(f"이미 있음(덮지 않음): {ledger}")
    _filter_jsonl(args.facts, ledger / "facts.jsonl", keep)
    _filter_jsonl(args.marks, ledger / "policy_marks.jsonl", keep)
    out = work / "topic_stance.v2.jsonl"
    argv = ["--ledger", str(ledger), "--instruments", str(args.instruments), "--instruments2", str(args.instruments2),
            "--tables", str(args.out_dir), "--out", str(out), "--workers", str(args.workers)]
    if args.facts_audit:
        argv += ["--facts-audit", str(args.facts_audit)]
    if args.v1_stance:
        argv += ["--v1-stance", str(args.v1_stance)]
    if keep is not None:
        lst = work / "rcepts.txt"
        lst.write_text("".join(f"{r}\n" for r in sorted(keep)), encoding="utf-8")
        argv += ["--rcept-list", str(lst)]
    rc = _judge_module().main(argv)
    if rc != 0:
        raise SystemExit(f"판정 실행 실패(rc={rc})")
    return out


# ---------- ledger.measure.md ----------
def _md_table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def render_markdown(result: dict, gold_index: dict[str, dict]) -> str:
    L: list[str] = ["# WS8 v2 측정 (ledger.measure)", ""]
    inp = result.get("inputs") or {}
    if inp:
        L += ["## 입력", ""] + [f"- {k}: `{v}`" for k, v in inp.items() if v is not None] + [""]
    if "tables" in result:
        adj = result.get("adjusted") or {}
        L += ["## 1층 표 비율", "",
              f"- 공시 {result['filings']}(없음 {len(result['filings_missing'])}), 표 {result['tables']}",
              f"- 기간 판별 {adj.get('period_found_ratio')} · 단위 판별 {adj.get('unit_found_ratio')} · "
              f"합계 검산 {adj.get('sum_ok_ratio')} · 읽기 실패 {adj.get('read_failed_ratio')} (보정 비율)", ""]
    gs = result.get("goldset")
    if gs:
        g3 = gs["3"]
        L += ["## 정답 묶음", "",
              f"- 1층: 맞음 {gs['1']['passed']}, 틀림 {len(gs['1']['failed'])}, 표본 밖 {gs['1']['skipped']}",
              (f"- 2층: 맞음 {gs['2']['passed']}, 틀림 {len(gs['2']['failed'])}, "
               f"건너뜀 {gs['2']['skipped']}(그중 공시 없음 {len(gs['2'].get('no_data') or [])})"
               if gs["2"].get("scored", True) else "- 2층: 채점 안 함(--facts·--instruments 없음)"),
              f"- 3층: 맞음 {g3['passed']}, **틀린 판정 {len(g3['failed'])}**, 판단불가로 놓침 {len(g3['missed'])}, "
              f"표본 밖 {g3['skipped']} — 관문(스펙 §7.2)은 틀린 판정 0", ""]
        for layer in ("1", "2"):
            if gs[layer]["items"]:
                L += [f"### {layer}층 항목", ""]
                L += _md_table(["id", "결과", "내용"],
                               [[r["id"], {True: "맞음", False: "**틀림**", None: "건너뜀"}[r["ok"]], r["detail"][:160]]
                                for r in gs[layer]["items"]]) + [""]
        if g3["items"]:
            L += ["### 3층 항목", ""]
            names = {"pass": "맞음", "wrong": "**틀림**", "miss": "놓침"}
            rows = []
            for r in g3["items"]:
                g = gold_index.get(r["id"], {})
                rows.append([r["id"], g.get("corp", ""), g.get("topic", "")[:12], "/".join(r["expect"]),
                             names[r["status"]] + (" (회계사 확인)" if r.get("needs_accountant_confirm") else ""),
                             r["detail"][:120]])
            L += _md_table(["id", "회사", "주제", "기대", "결과", "v2"], rows) + [""]
    cmp_ = result.get("compare")
    if cmp_:
        L += ["## v1·v2 비교 (접수번호 × 주제)", ""]
        rows = []
        for topic, t in sorted(cmp_["topics"].items()):
            d = t["directions"]
            rows.append([topic, t["n_v1"], t["n_v2"], t["changed"], d.get("same", 0), d.get("value→UNDETERMINED", 0),
                         d.get("value→other", 0), d.get("UNDETERMINED→value", 0), d.get("scope_split", 0),
                         d.get("v1_only", 0), d.get("v2_only", 0)])
        L += _md_table(["주제", "v1", "v2", "바뀜", "같음", "값→판단불가", "값→다른 값", "판단불가→값", "범위 갈림",
                        "v1만", "v2만"], rows) + [""]
        for topic, t in sorted(cmp_["topics"].items()):
            cols = sorted({c for row in t["matrix"].values() for c in row})
            L += [f"### {topic} 방향 행렬 (행 v1, 열 v2)", ""]
            L += _md_table(["v1 \\ v2"] + cols,
                           [[r] + [t["matrix"][r].get(c, 0) for c in cols] for r in sorted(t["matrix"])]) + [""]
        for cls, m in (cmp_.get("measure") or {}).items():
            L += [f"### 측정 {cls} {m['n']}건의 v2 행방", ""]
            cols = sorted(m["totals"])
            L += _md_table(["주제"] + cols + ["계"],
                           [[t] + [c.get(k, 0) for k in cols] + [sum(c.values())] for t, c in sorted(m["by_topic"].items())]
                           + [["합계"] + [m["totals"].get(k, 0) for k in cols] + [m["n"]]]) + [""]
    return "\n".join(L) + "\n"


last_result: dict | None = None  # 마지막 main() 결과(테스트용)


def main(argv: list[str] | None = None) -> int:
    from dart_search_mcp.ledger.compare import compare
    from dart_search_mcp.ledger.goldset import GOLDSET_PATH, load_goldset, score, score_layer1

    ap = argparse.ArgumentParser(description="완료 기준 측정(1층 비율, 정답 묶음 층별 채점, v1·v2 비교)")
    ap.add_argument("--out-dir", type=Path, required=True, help="1층 표 기록 디렉터리")
    ap.add_argument("--sample", type=Path, help="접수번호 목록(있으면 모든 층을 이 공시로 좁힌다)")
    ap.add_argument("--gold-dir", type=Path, help="goldset 공시 표 기록 디렉터리(기본: --out-dir)")
    ap.add_argument("--gold", type=Path, default=GOLDSET_PATH)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--facts", type=Path, help="2층 ① facts.jsonl")
    ap.add_argument("--marks", type=Path, help="2층 ③ policy_marks.jsonl")
    ap.add_argument("--facts-audit", type=Path)
    ap.add_argument("--instruments", type=Path)
    ap.add_argument("--instruments2", type=Path)
    ap.add_argument("--v1-stance", type=Path)
    ap.add_argument("--v2-stance", type=Path, help="이미 만든 v2 판정(주면 판정을 실행하지 않는다)")
    ap.add_argument("--classified", type=Path, help="10-11 정책만 측정 classified.jsonl")
    ap.add_argument("--work-dir", type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    sample = None
    if args.sample:
        sample = [ln.strip() for ln in args.sample.read_text(encoding="utf-8").splitlines() if ln.strip()]
    keep = set(sample) if sample is not None else None
    gold = load_goldset(args.gold)

    result: dict = {}
    if sample is not None:
        result = measure_tables(args.out_dir, sample)
        result["period_unknown_by_type"] = period_unknown_breakdown(args.out_dir, sample)
    gl1 = score_layer1(gold, args.gold_dir or args.out_dir, keep)
    result["goldset_layer1"] = {"ok": sum(g["ok"] for g in gl1), "n": len(gl1), "items": gl1}

    full = args.v2_stance or (args.facts and args.marks and args.instruments and args.instruments2)
    if full:
        if args.work_dir:
            args.work_dir.mkdir(parents=True, exist_ok=True)
        v2 = args.v2_stance
        if v2 is None:
            if not args.work_dir:
                ap.error("판정을 실행하려면 --work-dir 가 필요하다")
            v2 = run_judge(args, keep)
        facts_for_gold = args.facts
        runs = [p for p in (args.instruments, args.instruments2) if p]
        result["inputs"] = {"tables": str(args.out_dir), "sample": str(args.sample) if args.sample else None,
                            "facts": str(args.facts) if args.facts else None,
                            "marks": str(args.marks) if args.marks else None,
                            "instruments": str(args.instruments) if args.instruments else None,
                            "instruments2": str(args.instruments2) if args.instruments2 else None,
                            "v1_stance": str(args.v1_stance) if args.v1_stance else None, "v2_stance": str(v2),
                            "classified": str(args.classified) if args.classified else None}
        sc = score(gold, args.gold_dir or args.out_dir, facts_for_gold, runs or None, v2, rcepts=keep)
        result["goldset"] = {str(k): v for k, v in sc.items()}
        if args.v1_stance:
            result["compare"] = compare(args.v1_stance, v2, args.classified, rcepts=keep)
        md_path = args.md or (args.work_dir / "ledger.measure.md" if args.work_dir else None)
        if md_path:
            md_path.write_text(render_markdown(result, {g["id"]: g for g in gold}), encoding="utf-8")
        if args.work_dir and not args.json:
            args.json = args.work_dir / "ledger.measure.json"

    global last_result
    last_result = result
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text if not full else json.dumps({"goldset": {k: {kk: vv for kk, vv in v.items() if kk != "items"}
                                                        for k, v in result["goldset"].items()}},
                                           ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
