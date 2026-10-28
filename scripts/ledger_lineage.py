"""정정 계보 파일 만들기 — 수집 공시 목록 → BE dart_filing_lineage 적재용 JSONL (ledger/lineage.py)

    python scripts/ledger_lineage.py --audit dart_collected/audit_facts.jsonl --out-dir OUT \\
        [--superseded judge-full-N/superseded.v2.jsonl]

출력(폴더가 이미 있으면 멈춘다 — 덮지 않는다):
  - filing_lineage.v1.jsonl          계보 한 묶음에 한 줄(BE 표 칸 그대로)
  - filing_lineage_members.v1.jsonl  계보에 든 접수번호 한 줄씩(is_latest)
  - review.jsonl                     확인 목록: 최신본 감사의견 못 읽음·의견 바뀜·원본 미수집 혼자 정정
  - summary.json                     행 수·false 수·수집기 sha·판정 행 계보(--superseded)와의 대조
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dart_search_mcp.ledger.lineage import build_lineage  # noqa: E402
from dart_search_mcp.ledger.supersede import base_report_name  # noqa: E402

_KNOWN = (None, "", "unknown")


def _review(filings: list[dict], members: list[dict], lone: list[str]) -> list[dict]:
    by_rc: dict[str, dict] = {}
    for f in filings:  # build_lineage 처럼 같은 접수번호는 처음 것
        by_rc.setdefault(str(f.get("rcept_no") or ""), f)
    chains: dict[str, list[str]] = defaultdict(list)
    for m in members:
        chains[m["last_rcept_no"]].append(m["rcept_no"])
    out = []
    for last, rcs in sorted(chains.items()):
        new, old = by_rc[last], [by_rc[r] for r in rcs if r != last]
        new_op = new.get("audit_opinion")
        old_ops = sorted({o.get("audit_opinion") for o in old} - set(_KNOWN))
        if new_op in _KNOWN and old_ops:
            out.append({"kind": "latest_opinion_unread", "last_rcept_no": last, "old_opinions": old_ops})
        elif new_op not in _KNOWN and [o for o in old_ops if o != new_op]:
            out.append({"kind": "opinion_changed", "last_rcept_no": last, "latest_opinion": new_op, "old_opinions": old_ops})
    for rc in lone:
        f = by_rc[rc]
        out.append({"kind": "lone_correction", "rcept_no": rc, "corp_code": f.get("corp_code"),
                    "base_report_nm": base_report_name(f.get("report_name"))})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--superseded", type=Path, help="판정기 superseded.v2.jsonl — 옛 접수본이 members false 에 드는지 대조")
    args = ap.parse_args(argv)
    if args.out_dir.exists():
        print(f"{args.out_dir} 이미 있음 — 새 폴더를 주세요", file=sys.stderr)
        return 2
    filings = [json.loads(line) for line in args.audit.open(encoding="utf-8") if line.strip()]
    lineage, members, stats = build_lineage(filings)
    review = _review(filings, members, stats.pop("lone_rcepts"))
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "audit": str(args.audit), **stats,
               "review": {k: sum(1 for r in review if r["kind"] == k)
                          for k in ("latest_opinion_unread", "opinion_changed", "lone_correction")}}
    try:
        summary["collector_sha"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                                  text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        summary["collector_sha"] = None
    mismatch = []
    if args.superseded:
        false_rc = {m["rcept_no"] for m in members if not m["is_latest"]}
        latest_of = {m["rcept_no"]: m["last_rcept_no"] for m in members}
        sup = {}
        for line in args.superseded.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                sup.setdefault(r["rcept_no"], r.get("superseded_by"))
        judged = set(sup.values())  # 판정기의 최신 = 판정 행이 있는 가장 늦은 접수본
        for rc, by in sorted(sup.items()):
            if rc not in false_rc:
                mismatch.append({"rcept_no": rc, "superseded_by": by, "why": "not_in_members_false"})
            elif latest_of.get(rc) != by:
                # 계보 최신본에 판정 행이 없으면 판정기는 그 앞 정정본을 최신으로 본다 — 진짜 어긋남과 나눈다
                why = "different_latest" if latest_of.get(rc) in judged else "latest_unjudged"
                mismatch.append({"rcept_no": rc, "superseded_by": by, "lineage_last": latest_of.get(rc), "why": why})
        summary["superseded_check"] = {"file": str(args.superseded), "superseded_rcept": len(sup),
                                       "in_members_false": sum(1 for rc in sup if rc in false_rc),
                                       "mismatch": len(mismatch)}
    args.out_dir.mkdir(parents=True)
    for name, rows in (("filing_lineage.v1.jsonl", lineage), ("filing_lineage_members.v1.jsonl", members),
                       ("review.jsonl", review), ("superseded_mismatch.jsonl", mismatch)):
        with (args.out_dir / name).open("w", encoding="utf-8") as fo:
            for r in rows:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
