"""정답 묶음(goldset.jsonl)과 층별 채점(스펙 §7.1).

1층 항목: 회계사가 짚은 근거 칸 하나. 그 공시의 표 기록에서 행 레이블(label·label_path)에 `label_contains`가 있고,
열 기간이 `period`이며 칸의 원 환산값이 `value_krw`인 칸이 있으면 맞다.
고르기 조건(선택): `title_contains`(표 제목), `header_contains`(열 머리글 경로), `fs_scope`, `region_kind`.
read_failed 표는 세지 않는다(행·칸을 잡지 못한 표다).

2층 항목(`source`):
- `fact`: facts.jsonl 에 `concept`(문자열 또는 목록)·`period`(선택)·`fs_scope`(선택)가 맞고 `value_krw`가
  amount_krw 의 절댓값과 같은 사실이 있으면 맞다. `absent: true`면 그런 개념의 0 아닌 사실이 **없어야** 맞다
  (`period`는 문자열이나 목록, 없으면 모든 기간).
  금액은 있음·없음 검사 모두 같은 칸을 본다: unit_kind 가 krw 면 amount_krw, 그 밖(주식 수·% 등)이면 amount.
  `absent` 항목인데 facts 에 그 공시 줄이 아예 없으면 맞음이 아니라 건너뜀(skipped, 「사실에 공시 없음」)이다.
- `instrument`: 사채 기록 실행마다 그 공시(`fs_scope` 선택)의 기록 중 `field` 값이 `equals`인 것이 있어야 맞다.
  `absent: true`면 어느 실행에도 없어야 맞다. 실행이 여럿이면 모두 맞아야 맞다(두 실행 합의, R16).
  `absent` 항목인데 어느 실행에 그 공시 기록이 아예 없으면 건너뜀(skipped, 「실행에 공시 없음」)이다.
결과 항목: {id, ok(True/False, 건너뜀이면 None), skipped, detail}.

3층 항목: `topic`과 `expect`(받아들이는 값 목록, 판단불가는 "UNDETERMINED"), 선택 `fs_scope`·`held_reason`.
그 공시·주제의 v2 행(범위별)을 모아 상태를 정한다.
- wrong: 판정 값(판단불가 아님) 가운데 expect 에 없는 값이 하나라도 있다. 스펙 §7.2 관문은 이것이 0 이다.
- pass: expect 에 든 판정 값이 있거나, 모두 판단불가이고 expect 에 UNDETERMINED 가 있다(행이 없어도 같다).
- miss: 모두 판단불가(또는 행 없음)인데 expect 에 판정 값만 있다 — 재현율 놓침, 틀림이 아니다(R14).
기대 held_reason 과 실제 사유가 다르면 pass 로 두고 detail 에 적는다.
`needs_accountant_confirm: true`(R26) 항목은 결과에 같은 표시를 넘긴다(회계사 확인 페이지에 올릴 것).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .tables import load_filing

UND = "UNDETERMINED"

GOLDSET_PATH = Path(__file__).with_name("goldset.jsonl")
_WS = re.compile(r"\s+")


def _sq(s: str | None) -> str:
    return _WS.sub("", s or "")


def _jsonl(path: Path):
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _in_scope(item: dict, rcepts) -> bool:
    return rcepts is None or item["rcept_no"] in rcepts


def load_goldset(path: Path = GOLDSET_PATH) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _tables(item: dict, filing: dict):
    for t in filing.get("tables", []):
        if t.get("status") == "read_failed":
            continue
        if item.get("title_contains") and _sq(item["title_contains"]) not in _sq(t.get("title")):
            continue
        if item.get("fs_scope") and t.get("fs_scope") != item["fs_scope"]:
            continue
        if item.get("region_kind") and (t.get("region") or {}).get("kind") != item["region_kind"]:
            continue
        yield t


def _label_hit(row: dict, needle: str) -> bool:
    return any(needle in _sq(x) for x in [row.get("label")] + list(row.get("label_path") or []))


def score_layer1(gold: list[dict], out_dir: Path, rcepts=None) -> list[dict]:
    """1층 정답 항목마다 {id, ok, detail}. detail은 맞으면 찾은 칸(table_id rN cN), 틀리면 가까운 후보.
    rcepts 가 주어지면 그 밖의 공시 항목은 건너뛴다(결과에 없음)."""
    results = []
    for item in gold:
        if item.get("layer") != 1 or not _in_scope(item, rcepts):
            continue
        path = Path(out_dir) / f"{item['rcept_no']}.json.gz"
        if not path.exists():
            results.append({"id": item["id"], "ok": False, "detail": f"표 기록 파일 없음: {path.name}"})
            continue
        filing = load_filing(path)
        needle = _sq(item["label_contains"])
        hits, near = [], []
        for t in _tables(item, filing):
            cols = {c["col"]: c for c in t.get("columns", []) if c.get("role") == "value"}
            for row in t.get("rows", []):
                if not _label_hit(row, needle):
                    continue
                for cell in row.get("cells", []):
                    col = cols.get(cell["col"])
                    if col is None:
                        continue
                    if item.get("header_contains") and _sq(item["header_contains"]) not in _sq("/".join(col["header_path"])):
                        continue
                    where = f"{t['table_id']} r{row['row']} c{cell['col']}"
                    if col.get("period") == item["period"] and cell.get("value_krw") == item["value_krw"]:
                        hits.append(where)
                    else:
                        near.append(f"{where} {col.get('period')}={cell.get('value_krw')}")
        if hits:
            results.append({"id": item["id"], "ok": True, "detail": "; ".join(hits)})
        else:
            results.append({"id": item["id"], "ok": False,
                            "detail": "맞는 칸 없음" + (" — 후보: " + "; ".join(near[:5]) if near else " — 레이블 행 없음")})
    return results


# ---------- 2층 ----------
def _as_list(x) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple, set)) else [x]


def _fact_desc(f: dict) -> str:
    return f"{f.get('concept')} {f.get('fs_scope')} {f.get('period')}={f.get('amount_krw')}"


def _amount(f: dict):
    """비교에 쓰는 금액: 원 단위(unit_kind krw)는 amount_krw, 그 밖은 amount."""
    v = f.get("amount_krw") if (f.get("unit_kind") or "krw") == "krw" else f.get("amount")
    return v if isinstance(v, (int, float)) else None


def _skip(detail: str) -> dict:
    return {"ok": None, "skipped": True, "detail": detail}


def _score_fact(item: dict, facts: list[dict]) -> dict:
    concepts = set(_as_list(item["concept"]))
    periods = set(_as_list(item.get("period")))
    cand = [f for f in facts if f.get("concept") in concepts
            and (not item.get("fs_scope") or (f.get("fs_scope") or "separate") == item["fs_scope"])]
    if item.get("absent"):
        if not facts:
            return _skip("사실에 공시 없음")
        bad = [f for f in cand if (not periods or f.get("period") in periods) and _amount(f)]
        if bad:
            return {"ok": False, "detail": "있어선 안 되는 사실: " + "; ".join(_fact_desc(f) for f in bad[:5])}
        return {"ok": True, "detail": "해당 사실 없음"}
    hits = [f for f in cand if (not periods or f.get("period") in periods)
            and _amount(f) is not None and abs(_amount(f)) == item["value_krw"]]
    if hits:
        src = hits[0].get("source") or {}
        return {"ok": True, "detail": f"{_fact_desc(hits[0])} {src.get('table_id', '')}".strip()}
    near = "; ".join(_fact_desc(f) for f in cand[:5])
    return {"ok": False, "detail": "맞는 사실 없음" + (f" — 후보: {near}" if near else " — 그 개념 사실 없음")}


def _load_runs(paths, rcepts: set[str]) -> list[tuple[str, dict]]:
    """실행마다 (이름, {(rcept, scope): 기록 목록}). (rcept, scope)마다 마지막 줄."""
    runs = []
    for i, p in enumerate(_as_list(paths), 1):
        last: dict = {}
        for r in _jsonl(Path(p)):
            if r.get("rcept_no") in rcepts:
                last[(r["rcept_no"], r.get("fs_scope") or "separate")] = list(r.get("records") or [])
        runs.append((f"run{i}", last))
    return runs


def _score_instrument(item: dict, runs: list[tuple[str, dict]]) -> dict:
    if not runs:
        return {"ok": False, "detail": "사채 기록 실행 없음"}
    notes, ok, missing = [], True, []
    for name, recs_by_key in runs:
        if not any(rc == item["rcept_no"] for rc, _scope in recs_by_key):
            missing.append(name)
        recs = [rec for (rc, scope), recs in recs_by_key.items()
                if rc == item["rcept_no"] and (not item.get("fs_scope") or scope == item["fs_scope"])
                for rec in recs]
        vals = sorted({str(r.get(item["field"])) for r in recs})
        hit = item["equals"] in vals
        good = (not hit) if item.get("absent") else hit
        ok = ok and good
        notes.append(f"{name}: {item['field']}={'/'.join(vals) if vals else '기록 없음'}")
    if item.get("absent") and missing:
        return _skip(f"실행에 공시 없음({', '.join(missing)})")
    return {"ok": ok, "detail": "; ".join(notes)}


def score_layer2(gold: list[dict], facts_path, instruments_paths, rcepts=None) -> list[dict]:
    """2층 정답 항목마다 {id, ok, detail}. instruments_paths 는 경로 하나나 목록(실행별)."""
    items = [g for g in gold if g.get("layer") == 2 and _in_scope(g, rcepts)]
    if not items:
        return []
    want = {g["rcept_no"] for g in items}
    facts: dict[str, list[dict]] = {}
    if facts_path and any(g.get("source", "fact") == "fact" for g in items):
        for f in _jsonl(Path(facts_path)):
            if f.get("rcept_no") in want:
                facts.setdefault(f["rcept_no"], []).append(f)
    runs = _load_runs(instruments_paths, want) if any(g.get("source") == "instrument" for g in items) else []
    out = []
    for g in items:
        if g.get("source") == "instrument":
            res = _score_instrument(g, runs)
        else:
            res = _score_fact(g, facts.get(g["rcept_no"], []))
        out.append({"id": g["id"], "skipped": False, **res})
    return out


# ---------- 3층 ----------
def load_stance(path, rcepts=None) -> dict[tuple[str, str], list[dict]]:
    """(rcept_no, topic) → v2 행 목록(범위별)."""
    out: dict[tuple[str, str], list[dict]] = {}
    for r in _jsonl(Path(path)):
        if rcepts is None or r.get("rcept_no") in rcepts:
            out.setdefault((r["rcept_no"], r["topic"]), []).append(r)
    return out


def _row_desc(r: dict) -> str:
    return f"{r.get('fs_scope')}:{r['value']}" + (f"/{r['held_reason']}" if r.get("held_reason") else "")


def judge_item(item: dict, rows: list[dict]) -> dict:
    expect = set(item["expect"])
    if item.get("fs_scope"):
        rows = [r for r in rows if (r.get("fs_scope") or "separate") == item["fs_scope"]]
    got = sorted({r["value"] for r in rows if r["value"] != UND})
    desc = "; ".join(_row_desc(r) for r in rows) or "v2 행 없음"
    wrong = [v for v in got if v not in expect]
    if wrong:
        status = "wrong"
    elif got:
        status = "pass"
    elif UND in expect:
        status = "pass"
        reasons = sorted({r.get("held_reason") or "" for r in rows})
        if item.get("held_reason") and rows and item["held_reason"] not in reasons:
            desc += f" (기대 사유 {item['held_reason']}, 실제 {'/'.join(reasons)})"
    else:
        status = "miss"
    return {"id": item["id"], "status": status, "ok": status == "pass", "got": got,
            "expect": sorted(expect), "detail": desc,
            "needs_accountant_confirm": bool(item.get("needs_accountant_confirm"))}


def score_layer3(gold: list[dict], stance_path, rcepts=None) -> list[dict]:
    items = [g for g in gold if g.get("layer") == 3 and _in_scope(g, rcepts)]
    if not items:
        return []
    stance = load_stance(stance_path, {g["rcept_no"] for g in items})
    return [judge_item(g, stance.get((g["rcept_no"], g["topic"]), [])) for g in items]


def score(gold: list[dict], tables_dir, facts_path, instruments_path, stance_path, *, rcepts=None) -> dict:
    """층별 {passed, failed: [ids], skipped, items}. 3층은 failed = 틀린 판정(관문), missed = 재현율 놓침.
    rcepts 가 주어지면 그 밖의 공시 항목은 skipped 로만 센다. 입력 경로가 None 이면 그 층은 채점하지 않는다."""
    def scoped(layer):
        return [g for g in gold if g.get("layer") == layer]

    def skipped(layer):
        return sum(1 for g in scoped(layer) if not _in_scope(g, rcepts))

    out: dict = {}
    l1 = score_layer1(gold, tables_dir, rcepts) if tables_dir else []
    out[1] = {"passed": sum(r["ok"] for r in l1), "failed": [r["id"] for r in l1 if not r["ok"]],
              "skipped": skipped(1), "items": l1}
    scored2 = bool(facts_path or instruments_path)
    l2 = score_layer2(gold, facts_path, instruments_path, rcepts) if scored2 else []
    no_data = [r["id"] for r in l2 if r.get("skipped")]
    out[2] = {"scored": scored2, "passed": sum(r["ok"] is True for r in l2),
              "failed": [r["id"] for r in l2 if r["ok"] is False],
              "skipped": skipped(2) + len(no_data), "no_data": no_data, "items": l2}
    l3 = score_layer3(gold, stance_path, rcepts) if stance_path else []
    out[3] = {"passed": sum(r["status"] == "pass" for r in l3),
              "failed": [r["id"] for r in l3 if r["status"] == "wrong"],
              "missed": [r["id"] for r in l3 if r["status"] == "miss"],
              "skipped": skipped(3), "items": l3}
    return out
