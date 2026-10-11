"""2층 ② 사채·우선주 기록 — 프롬프트(i2)·호출·캐시·공시 단위 실행(스펙 §5.3, 브리프 T9).

LLM 은 주입된 `complete_json(messages, schema) -> dict` 로 부른다(백엔드 이미지 안에서는
scripts/ledger_instruments_in_backend.py 가 luna LLMClient 를 감싸 넘긴다). 표준 라이브러리와
instrument_input·instrument_validate 만 쓴다(1층 tables.py·dart-agent 를 import 하지 않는다).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

from .instrument_input import build_input, input_scopes, needs_instruments
from .instrument_validate import validate

PROMPT_VERSION = "i2"  # i1 → i2: 2026-10-11 100건 측정(근거 통과 43.7%) 뒤 개정, 보고서 T9 측정 1회

CompleteJson = Callable[[list, dict], dict]

_AMOUNTS = {"face": None, "host_carrying": None, "derivative_liability": None,
            "derivative_asset": None, "equity_component": None}

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["instruments"],
    "properties": {"instruments": {"type": "array", "items": {
        "type": "object",
        "required": ["instrument", "series_key", "kind", "balance", "movement", "option_class",
                     "embedded_separation", "separated_targets", "evidence"],
        "properties": {
            "instrument": {"type": "string"},
            "series_key": {"type": "string"},
            "kind": {"enum": ["CB", "BW", "EB", "RCPS"]},
            "balance": {"type": "object", "properties": {
                p: {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in _AMOUNTS}}
                for p in ("current_end", "prior_end")}},
            "movement": {"type": "object", "properties": {p: {"type": "array", "items": {"enum": [
                "issued", "converted", "redeemed", "matured", "extinguished", "modified", "none"]}}
                for p in ("current", "prior")}},
            "option_class": {"enum": ["equity", "derivative_liability", "unconfirmed"]},
            "embedded_separation": {"enum": ["separated", "not_separated", "whole_fvpl", "unconfirmed"]},
            "separated_targets": {"type": "array", "items": {"enum": ["conversion", "redemption"]}},
            "evidence": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        }}}},
}

_SYSTEM_I2 = (
    "너는 감사보고서 주석에서 전환사채·신주인수권부사채·교환사채·상환전환우선주를 회차별로 기록한다. "
    "표 칸 id와 문장 id로만 근거를 댄다. 보이지 않는 값은 null 또는 unconfirmed로 둔다. "
    "사채의 부채요소(주계약)와 전환권 파생상품부채는 다른 칸이다. JSON 객체 하나만 답한다."
)

_RULES_I2 = """규칙:
1. 회차(상품)마다 한 줄. kind 는 CB(전환사채)·BW(신주인수권부사채)·EB(교환사채)·RCPS(상환전환우선주) 중 하나.
   series_key 는 「제3회」 같은 회차 번호와 종류로 만든다(예: "CB-3"). 회차 번호가 보이지 않으면 series_key 는
   빈 문자열 "" 로 두고, 발행일·액면으로 묶고, 못 묶으면 따로 둔다. 같은 회차의 당기말·전기말 잔액은 한 줄에 담는다.
2. 금액은 칸에 인쇄된 숫자 그대로 적는다. 단위를 바꾸지 않는다: 표 단위가 천원이면 천원 숫자를 그대로 쓴다
   (「(단위: 천원)」 표의 1,499,943 → 1499943. 1499943000 이 아니다). 쉼표는 빼고, 괄호 (1,234) 는 -1234.
   머리글 줄의 [current_end]·[prior_end] 는 그 열의 기간이다. current_end 금액은 당기말 열, prior_end 는 전기말 열에서.
3. null 이 아닌 필드는 모두 evidence 에 근거 id 를 댄다(금액마다, option_class·embedded_separation 도).
   evidence 키는 필드 경로다: "balance.current_end.face", "balance.prior_end.equity_component" 처럼.
   댈 근거가 없으면 금액은 null, 분류는 "unconfirmed" 로 둔다.
4. 근거 id 는 입력에 인쇄된 그대로만 쓴다: 칸 "<표 id>:r<행>:c<열>", 글 표의 금액 "<표 id>:r<행>:v<번호>",
   문장 "s<번호>"(예: "s494" — "s:494"·"<접수번호>:s494" 아님), 각주 "f:<표 id>:<번호>".
   표 id 만("<표 id>")이나 r0, 입력에 없는 열 번호는 쓰지 않는다. 만들어 내지 않는다.
5. 필드 뜻:
   - face: 액면(발행가액) 잔액. 그 금액이 적힌 칸(잔액 표의 「액면가액」·「전환사채」 행, 또는 발행조건 글 표의
     「액면가액 | v1=…원」 금액)의 id 를 댄다.
   - host_carrying: 사채의 부채요소(주계약) 장부금액. 인쇄된 장부금액·합계 칸 하나를 댄다. 그런 칸이 없고
     같은 표·같은 열에 구성 칸(액면·상환할증금·전환권조정·사채할인발행차금)만 있으면 그 칸들을 모두 대고
     인쇄된 값(차감은 음수)의 합을 적는다. 다른 열·다른 표 칸을 섞지 않는다. 우선주부채(주계약)도 여기.
     상품 전체를 당기손익-공정가치로 측정하면 그 전체 장부금액도 여기에 둔다.
   - derivative_liability: 전환권·상환권 등 옵션(내재파생상품)이 파생상품부채로 따로 잡힌 금액. 이런 칸만:
     「파생상품부채」·「전환권부채」 행, 「…내재파생상품」·「전환권 및 조기상환권」 같은 옵션 행, 파생상품부채
     변동표의 「기말」 행, 제목이 「파생상품부채의 내역」인 표의 회차 행. 아닌 것: 「전환권조정」·「전환권가치」 같은
     차감 칸(괄호 음수), 「사채할인발행차금」·「상환할증금」·「합계」 행, 옵션 낱말 없이 상품 이름만 있는 행의
     금액(상품 전체 장부금액), 「전환사채 및 조기상환권」처럼 상품과 옵션을 함께 적은 행.
   - derivative_asset: 매도청구권 등이 파생상품자산으로 잡힌 금액(「파생상품자산」 행, 매도청구권 행, 파생상품자산
     변동표의 「기말」 행).
   - equity_component: 전환권대가·신주인수권대가 등 자본요소(「전환사채의 자본요소」·「전환권대가」 행).
6. option_class: 전환권(신주인수권·교환권)이 자본이면 "equity". 전환권 자체가 (파생상품)부채로 잡힌 근거
   (위 derivative_liability 칸이나 「전환권을 금융부채로 분류」 같은 문장)가 있을 때만 "derivative_liability".
   사채 부채요소나 전환권조정 칸만 있으면 derivative_liability 가 아니다. 「…경우 부채로 분류한다」 같은 조건부
   회계정책 문장만으로는 정하지 않는다. 상품 전체를 당기손익-공정가치로 지정했다는 문장만 있으면 "unconfirmed".
   모르면 "unconfirmed".
7. embedded_separation: 내재파생상품을 주계약과 분리했으면 "separated"(separated_targets 에 분리한 것:
   "conversion"·"redemption"), 분리하지 않았으면 "not_separated", 상품 전체를 당기손익-공정가치로 측정하면
   "whole_fvpl", 모르면 "unconfirmed". 근거는 그렇게 말하는 문장 id 가 가장 좋다.
8. movement: 그 기간에 있었던 일 — issued(발행)·converted(전환)·redeemed(상환)·matured(만기)·
   extinguished(소멸)·modified(조건 변경)·none(변동 없음). 모르면 빈 목록.
9. 대상 상품이 없으면 {"instruments": []}."""

_EXAMPLE_I2 = """예시 입력(일동홀딩스, 천원 표):
[T7] (consolidated) / 제목: 당기말과 전기말 현재 전환사채의 세부내역 / 단위: (단위: 천원)
머리글 | c1=당기말 [current_end] | c2=전기말 [prior_end]
r2 전환사채의 부채요소
r3 전환사채 | c1=1,000,000 | c2=82,915,000
r4 전환사채상환할증금 | c1=- | c2=7,511,222
r5 전환권조정 | c1=(103,911) | c2=(30,905,828)
r6 합 계 | c1=896,089 | c2=59,520,394
r7 전환사채의 자본요소 | c1=121,352 | c2=519,263
예시 출력:
{"instruments": [{"instrument": "전환사채", "series_key": "", "kind": "CB",
 "balance": {"current_end": {"face": 1000000, "host_carrying": 896089, "derivative_liability": null,
                             "derivative_asset": null, "equity_component": 121352},
             "prior_end": {"face": 82915000, "host_carrying": 59520394, "derivative_liability": null,
                           "derivative_asset": null, "equity_component": 519263}},
 "movement": {"current": [], "prior": []},
 "option_class": "equity", "embedded_separation": "unconfirmed", "separated_targets": [],
 "evidence": {"option_class": ["T7:r7:c1"],
              "balance.current_end.face": ["T7:r3:c1"], "balance.current_end.host_carrying": ["T7:r6:c1"],
              "balance.current_end.equity_component": ["T7:r7:c1"],
              "balance.prior_end.face": ["T7:r3:c2"], "balance.prior_end.host_carrying": ["T7:r6:c2"],
              "balance.prior_end.equity_component": ["T7:r7:c2"]}}]}
(표에 회차 번호가 없으므로 series_key 는 "". 자본요소 행이 있으므로 option_class 는 equity.
 파생상품부채 칸이 없으므로 derivative_liability 는 null. 천원 숫자를 그대로 적었다.)

예시 입력 2(행사가격 조정 공시 표, 원 단위가 아닌 천원 표):
[T9] (separate) / 주석: 상환전환우선주 / 제목: 발행자의 주가 변동에 따라 행사가격이 조정되는 조건으로 인해 부채로 분류된 금융부채의 장부금액 / 단위: (단위: 천원)
머리글 | c1=당기말 [current_end] | c2=전기말 [prior_end]
r2 제7회 상환전환우선주 내재파생상품 | c1=2,500,419 | c2=4,084,843
s12: 상환전환우선주의 전환권 및 조기상환권은 자본요소에 해당하지 않으므로 주계약에서 분리하여 파생상품부채로 인식하였습니다.
예시 출력 2(그 회차 줄의 일부):
 "series_key": "RCPS-7", "kind": "RCPS", "balance": {"current_end": {"derivative_liability": 2500419, ...},
 "prior_end": {"derivative_liability": 4084843, ...}}, "option_class": "derivative_liability",
 "embedded_separation": "separated", "separated_targets": ["conversion", "redemption"],
 "evidence": {"balance.current_end.derivative_liability": ["T9:r2:c1"],
              "balance.prior_end.derivative_liability": ["T9:r2:c2"], "option_class": ["T9:r2:c1", "s12"],
              "embedded_separation": ["s12"]}"""

_USER_I2 = """{rules}

출력 형식(JSON):
{schema}

{example}

---
입력 표(칸 id = <표 id>:r<행>:c<열>):
{tables_text}

입력 문장(id: 글):
{sentences}"""

PROMPT_I2 = {"version": PROMPT_VERSION, "system": _SYSTEM_I2, "rules": _RULES_I2, "example": _EXAMPLE_I2,
             "user": _USER_I2}


def build_messages(inp: dict) -> list[dict]:
    sentences = "\n".join(f"{sid}: {text}" for sid, text in (inp.get("sentences") or {}).items()) or "(없음)"
    user = _USER_I2.format(rules=_RULES_I2, schema=json.dumps(OUTPUT_SCHEMA, ensure_ascii=False),
                           example=_EXAMPLE_I2, tables_text=inp.get("tables_text") or "(없음)",
                           sentences=sentences)
    return [{"role": "system", "content": _SYSTEM_I2}, {"role": "user", "content": user}]


def cache_key(inp: dict) -> str:
    raw = PROMPT_VERSION + json.dumps(inp, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Cache:
    """키 → 원 응답. jsonl append(한 줄 {"key", "response", "model_used"}). 스레드 안전."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, object] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # 끊긴 마지막 줄
                    if isinstance(row, dict) and "key" in row:
                        self._data[row["key"]] = {"response": row.get("response"),
                                                  "model_used": row.get("model_used")}

    def get(self, key: str):
        """원 응답(없으면 None)."""
        with self._lock:
            return (self._data.get(key) or {}).get("response")

    def entry(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def put(self, key: str, response, model_used: str | None = None) -> None:
        with self._lock:
            self._data[key] = {"response": response, "model_used": model_used}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "response": response, "model_used": model_used},
                                    ensure_ascii=False) + "\n")


def _parse(response) -> list | None:
    if isinstance(response, (str, bytes)):
        try:
            response = json.loads(response)
        except ValueError:
            return None
    if not isinstance(response, dict):
        return None
    items = response.get("instruments")
    return items if isinstance(items, list) else None


def extract_instruments(inp: dict, complete_json: CompleteJson, cache: Cache | None = None, *,
                        model: str = "", usage_getter: Callable[[], dict | None] | None = None) -> dict:
    """입력 묶음 하나 → {"rcept_no", "records", "dropped", "prompt_version", "model", "model_used", "usage",
    "cached"}.

    records 는 검증을 거친 기록(필드별 사유는 record["issues"]), dropped 는 버린 기록·호출의 사유다.
    호출이 실패하면 dropped=["llm_error"], 응답 JSON 이 깨지면 ["bad_json"]. 실패 응답은 캐시하지 않는다.
    기록 하나의 검증이 예외를 내면 그 기록만 "bad_record" 로 버린다(캐시된 응답으로 이어 해도 멈추지 않게).
    model_used 는 실제로 답한 모델(usage_getter 의 model_used, 없으면 model)이고 캐시에도 남는다.
    """
    out = {"rcept_no": inp.get("rcept_no"), "records": [], "dropped": [], "prompt_version": PROMPT_VERSION,
           "model": model, "model_used": None, "usage": None, "cached": False}
    if inp.get("fs_scope") is not None:
        out["fs_scope"] = inp["fs_scope"]
    key = cache_key(inp)
    entry = cache.entry(key) if cache is not None else None
    if entry is not None:
        response = entry["response"]
        out["cached"] = True
        out["model_used"] = entry.get("model_used")
    else:
        try:
            response = complete_json(build_messages(inp), OUTPUT_SCHEMA)
        except Exception as exc:  # noqa: BLE001 — 공시 하나가 실행 전체를 멈추지 않게
            out["dropped"] = ["llm_error"]
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out["usage"] = usage_getter() if usage_getter else None
            return out
        out["usage"] = usage_getter() if usage_getter else None
        out["model_used"] = (out["usage"] or {}).get("model_used") or model or None
    items = _parse(response)
    if items is None:
        out["dropped"] = ["bad_json"]
        return out
    if cache is not None and not out["cached"]:
        cache.put(key, response, out["model_used"])
    for seq, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            out["dropped"].append("bad_record")
            continue
        try:
            rec, reasons = validate(item, inp, seq=seq)
        except Exception:  # noqa: BLE001
            out["dropped"].append("bad_record")
            continue
        if rec is None:
            out["dropped"].extend(reasons)
            continue
        rec["issues"] = reasons
        out["records"].append(rec)
    return out


def dry_complete_json(messages: list, schema: dict) -> dict:
    """LLM 없는 가짜(--dry): 언제나 빈 목록."""
    return {"instruments": []}


# ---------- 공시 단위 실행 ----------

def load_instrument_facts(facts_path: Path) -> dict[str, list[dict]]:
    """facts.jsonl → {rcept_no: [{"concept", "fs_scope"}]} (needs_instruments 에 필요한 칸만)."""
    from .instrument_input import INSTRUMENT_FACT_CONCEPTS
    out: dict[str, list[dict]] = {}
    if not Path(facts_path).exists():
        return out
    with Path(facts_path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            f = json.loads(line)
            if f.get("concept") in INSTRUMENT_FACT_CONCEPTS:
                out.setdefault(f["rcept_no"], []).append({"concept": f["concept"], "fs_scope": f.get("fs_scope")})
    return out


def _load_filing(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def list_needs(tables_dir: Path, facts: dict[str, list[dict]], rcepts: Iterable[str],
               stats: Counter | None = None) -> list[tuple[str, list[str]]]:
    """LLM 없이 대상 공시와 입력 범위(fs_scope) 목록만 센다. 범위가 빈 공시는 표 0개(no_tables)다.
    stats 를 주면 missing(파일 없음)·unreadable(읽기 실패) 수를 센다."""
    stats = stats if stats is not None else Counter()
    out = []
    for rcept in rcepts:
        path = Path(tables_dir) / f"{rcept}.json.gz"
        if not path.exists():
            stats["missing"] += 1
            continue
        try:
            tables = _load_filing(path).get("tables") or []
        except Exception:  # noqa: BLE001
            stats["unreadable"] += 1
            continue
        if needs_instruments(facts.get(rcept, []), tables):
            out.append((rcept, input_scopes(tables)))
    return out


_RETRY_DROPS = {"llm_error", "bad_json", "input_error"}


def read_done(out_path: Path) -> set[tuple[str, str | None]]:
    """이미 끝낸 (rcept_no, fs_scope). 끊긴 마지막 줄과 llm_error·bad_json·input_error 줄은 빼서 다음 실행이 다시 부르게 한다
    (그래서 같은 키가 여러 줄일 수 있다 — 읽는 쪽은 마지막 줄을 쓴다)."""
    done: set[tuple[str, str | None]] = set()
    if not out_path.exists():
        return done
    with out_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if _RETRY_DROPS & set(row.get("dropped") or []):
                continue
            done.add((row.get("rcept_no"), row.get("fs_scope")))
    return done


def _ensure_trailing_newline(path: Path) -> None:
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            last = fh.read(1)
        if last != b"\n":
            with path.open("ab") as fh:
                fh.write(b"\n")


def run_filings(*, tables_dir: Path, facts: dict[str, list[dict]], out_path: Path, rcepts: Iterable[str],
                complete_json: CompleteJson, model: str, cache: Cache | None = None, workers: int = 4,
                limit: int | None = None, usage_getter: Callable[[], dict | None] | None = None,
                log: Callable[[str], None] = lambda m: None) -> dict:
    """공시마다 needs_instruments → fs_scope 마다 입력 묶음 → 호출·검증 → out_path 에 한 줄씩 append.

    이어 하기: out_path 에 이미 있는 (rcept_no, fs_scope) 는 건너뛴다. 대상인데 넣을 표가 0개인 공시는
    {"skipped": "no_tables"} 줄을 남긴다. limit 은 이번 실행에서 새로 처리할 대상 공시 수 상한이다.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_trailing_newline(out_path)
    done = read_done(out_path)
    done_rcepts = {r for r, _s in done}
    counts: Counter = Counter()
    lock = threading.Lock()
    t0 = time.time()

    def inc(key: str, n: int = 1) -> None:
        with lock:
            counts[key] += n

    def jobs():
        taken = 0
        for rcept in rcepts:
            if limit is not None and taken >= limit:
                return
            path = Path(tables_dir) / f"{rcept}.json.gz"
            if not path.exists():
                inc("missing")
                continue
            inc("scanned")
            try:
                filing = _load_filing(path)
            except Exception:  # noqa: BLE001
                inc("read_error")
                continue
            tables = filing.get("tables") or []
            if not needs_instruments(facts.get(rcept, []), tables):
                continue
            inc("needs")
            scopes = input_scopes(tables)
            todo = [s for s in scopes if (rcept, s) not in done]
            if not scopes:
                if rcept in done_rcepts:
                    inc("already_done")
                    continue
                taken += 1
                yield rcept, None, None, filing
                continue
            if not todo:
                inc("already_done")
                continue
            taken += 1
            for s in todo:
                yield rcept, s, tables, filing

    fh = out_path.open("a", encoding="utf-8")

    def write(row: dict) -> None:
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()

    def one(job) -> None:
        rcept, scope, tables, filing = job
        if scope is None:
            write({"rcept_no": rcept, "fs_scope": None, "skipped": "no_tables", "records": [], "dropped": [],
                   "prompt_version": PROMPT_VERSION, "model": model, "usage": None})
            with lock:
                counts["skipped_no_tables"] += 1
            return
        start = time.time()
        try:
            inp = build_input(rcept, tables, filing.get("paragraphs") or [], fs_scope=scope)
        except Exception as exc:  # noqa: BLE001
            write({"rcept_no": rcept, "fs_scope": scope, "records": [], "dropped": ["input_error"],
                   "error": f"{type(exc).__name__}: {exc}"[:200], "prompt_version": PROMPT_VERSION,
                   "model": model, "usage": None})
            with lock:
                counts["dropped:input_error"] += 1
            return
        res = extract_instruments(inp, complete_json, cache, model=model, usage_getter=usage_getter)
        res.update({"n_cells": len(inp["cells"]), "n_sentences": len(inp["sentences"]),
                    "elapsed_sec": round(time.time() - start, 2)})
        write(res)
        with lock:
            counts["done"] += 1
            counts["records"] += len(res["records"])
            counts["cached"] += int(res["cached"])
            for d in res["dropped"]:
                counts[f"dropped:{d.split(':')[0]}"] += 1
            for r in res["records"]:
                for reason in r.get("issues") or []:
                    counts[f"issue:{reason.split(':')[0]}"] += 1
            if counts["done"] % 20 == 0:
                log(f"{counts['done']} done {time.time() - t0:.0f}s")

    try:
        if workers <= 1:
            for job in jobs():
                one(job)
        else:
            # 공시를 한꺼번에 메모리에 올리지 않게 진행 중 작업을 workers*2 개로 묶는다.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending: list = []
                for job in jobs():
                    pending.append(pool.submit(one, job))
                    if len(pending) >= workers * 2:
                        pending.pop(0).result()
                for fut in pending:
                    fut.result()
    finally:
        fh.close()
    summary = dict(sorted(counts.items()))
    summary.update({"prompt_version": PROMPT_VERSION, "model": model, "elapsed_sec": round(time.time() - t0, 1)})
    return summary


def summarize_out(out_path: Path) -> dict:
    """출력 jsonl 을 (rcept_no, fs_scope) 마다 마지막 줄만으로 다시 센다(이어 하기 뒤 측정용).

    evidence_pass_rate = 필드 사유(issues)가 하나도 없는 기록 / 전체 기록.
    """
    last: dict[tuple, dict] = {}
    lines = 0
    with Path(out_path).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            lines += 1
            last[(row.get("rcept_no"), row.get("fs_scope"))] = row
    issues: Counter = Counter()
    issue_fields: Counter = Counter()
    dropped: Counter = Counter()
    models: Counter = Counter()
    usage: Counter = Counter()
    records = all_pass = skipped = cached = 0
    for row in last.values():
        if row.get("skipped"):
            skipped += 1
            continue
        cached += int(bool(row.get("cached")))
        if row.get("model_used"):
            models[row["model_used"]] += 1
        for k, v in (row.get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                usage[k] += v
        for d in row.get("dropped") or []:
            dropped[str(d).split(":")[0]] += 1
        for rec in row.get("records") or []:
            records += 1
            rs = rec.get("issues") or []
            all_pass += int(not rs)
            for r in rs:
                issues[str(r).split(":")[0]] += 1
                issue_fields[str(r)] += 1
    return {
        "lines": lines, "keys": len(last), "filings": len({k[0] for k in last}),
        "skipped_no_tables": skipped, "cached": cached, "records": records, "records_all_pass": all_pass,
        "evidence_pass_rate": round(all_pass / records, 4) if records else None,
        "issues": dict(sorted(issues.items())), "issue_fields": dict(issue_fields.most_common(30)),
        "dropped": dict(sorted(dropped.items())), "model_used": dict(models), "usage": dict(usage),
        "prompt_version": PROMPT_VERSION,
    }
