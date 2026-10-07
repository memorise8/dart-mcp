"""WS8 주제 판정 실행기: 후보 고르기 → 병렬 판정 → progress 이어 쓰기 → 16칸 행.

판정 함수는 주입받은 `complete_json(prompt) -> dict` 하나에만 기댄다(스펙 §3).
실제 luna 호출은 `scripts/ws8_run_in_backend.py`가 백엔드 이미지 안에서 주입한다.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .judge import CompleteJson, judge_window
from .postcheck import UNDETERMINED, Verdict, postcheck
from .topics import TOPIC_DEFS
from .window import Window, doc_text, pick_window

METHOD = "llm_luna"
METHOD_FALLBACK = "llm_fallback"  # 백엔드가 luna 대신 폴백 모델로 답한 기록(progress usage.model_used)
PARSER_VERSION = "ws8-v1"
ROW_SUBJECT = "회사"
CONFIDENCE_DETERMINED = 0.8
CONFIDENCE_UNDETERMINED = 0.0
UNDETERMINED_PREFIX = "판정 불가: "
HELD_CONFLICT = "conflict"
RETRYABLE_HELD = frozenset({"llm_error", "parse"})  # --retry-errors가 다시 판정하는 보류 사유
EXCERPT_CHARS = 400

ROW_COLUMNS = (
    "rcept_no", "corp_code", "fiscal_year", "topic", "subject", "value", "evidence", "method",
    "confidence", "block_offset", "sentence_kind", "sentence_subject", "held_reason",
    "evidence_heading_path", "is_conditional", "parser_version",
)

# 모델 subject → (sentence_kind, sentence_subject). 값 어휘는 policy_sentences.KIND_*와 같다.
_SENTENCE_META = {
    "self": ("company_statement", "회사"),
    "standard_text": ("standard_description", "기준서"),
    "conditional": ("conditional", "회사"),
}
_SENTENCE_META_DEFAULT = ("", "불명")


# --- 입력 ----------------------------------------------------------------------

def load_facts(facts_path: Path) -> list[dict[str, Any]]:
    """audit_facts.jsonl을 읽는다. 깨진 줄·corp_code 없는 줄은 건너뛰고, rcept_no는 첫 줄만 쓴다."""
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    with open(facts_path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rc = rec.get("rcept_no")
            if not rc or not rec.get("corp_code") or rc in seen:
                continue
            seen.add(rc)
            facts.append(rec)
    return facts


def iter_candidates(
    facts: Iterable[dict[str, Any]], docs_dir: Path, topics: list[str],
    *, skip: set[tuple[str, str]] = frozenset(), stats: Counter | None = None,
) -> Iterator[tuple[dict[str, Any], str, Window]]:
    """(공시, 주제, 창). 공시마다 원문을 한 번만 읽는다. 키워드가 맞고 창이 있는 것만 낸다."""
    stats = stats if stats is not None else Counter()
    for fact in facts:
        rc = fact["rcept_no"]
        todo = [t for t in topics if (rc, t) not in skip]
        if not todo:
            continue
        text = doc_text(docs_dir, rc)
        if text is None:
            stats["no_xml"] += 1
            continue
        for t in todo:
            defn = TOPIC_DEFS[t]
            if not defn.keyword_re.search(text):
                continue
            w = pick_window(defn, text)
            if w is None:
                stats[f"no_window:{t}"] += 1
                continue
            yield fact, t, w


# --- 묶기와 행 ---------------------------------------------------------------------

def combine(verdicts: list[Verdict]) -> Verdict:
    """같은 (공시, 주제)의 판정을 하나로 묶는다(스펙 §4.4).

    판정값(UNDETERMINED 아님)이 둘 이상으로 엇갈리면 UNDETERMINED(held_reason `conflict`).
    판정값이 하나뿐이면 그 첫 판정, 모두 UNDETERMINED면 첫 판정을 낸다."""
    if not verdicts:
        raise ValueError("판정이 없다")
    determined = [v for v in verdicts if v.value != UNDETERMINED]
    if len({v.value for v in determined}) > 1:
        return Verdict(UNDETERMINED, "", determined[0].subject, HELD_CONFLICT, None)
    return determined[0] if determined else verdicts[0]


def method_for(model_used: str | None) -> str:
    """model_used가 있고 luna가 아니면 llm_fallback, 아니면 llm_luna(기록이 없으면 luna로 본다)."""
    return METHOD_FALLBACK if model_used and "luna" not in model_used.lower() else METHOD


def model_used_of(rec: dict[str, Any]) -> str | None:
    usage = rec.get("usage")
    return (usage.get("model_used") if isinstance(usage, dict) else None) or rec.get("model_used")


def build_row(fact: dict[str, Any], topic: str, verdict: Verdict, *, window_text: str,
              block_offset: int, anchor: str, model_used: str | None = None) -> dict[str, Any]:
    fy = fact.get("fiscal_year")
    determined = verdict.value != UNDETERMINED
    kind, sent_subject = _SENTENCE_META.get(verdict.subject, _SENTENCE_META_DEFAULT)
    return {
        "rcept_no": fact["rcept_no"],
        "corp_code": fact.get("corp_code"),
        "fiscal_year": int(fy) if fy not in (None, "") else None,
        "topic": topic,
        "subject": ROW_SUBJECT,
        "value": verdict.value,
        "evidence": verdict.evidence if determined else UNDETERMINED_PREFIX + window_text[:120],
        "method": method_for(model_used),
        "confidence": CONFIDENCE_DETERMINED if determined else CONFIDENCE_UNDETERMINED,
        "block_offset": block_offset,
        "sentence_kind": kind,
        "sentence_subject": sent_subject,
        "held_reason": verdict.held_reason or "",
        "evidence_heading_path": anchor,
        "is_conditional": verdict.held_reason == "conditional",
        "parser_version": PARSER_VERSION,
    }


def evidence_excerpt(window_text: str, evidence: str, limit: int = EXCERPT_CHARS) -> str:
    """회계사 확인용: 창에서 근거 둘레 `limit`자. 근거를 못 찾으면 창 앞 `limit`자."""
    i = window_text.find(evidence) if evidence else -1
    if i < 0:
        return window_text[:limit]
    pad = max(0, (limit - len(evidence)) // 2)
    start = max(0, min(i - pad, len(window_text) - limit))
    return window_text[start: start + limit]


# --- 이어 하기 -----------------------------------------------------------------------

def progress_path_for(out_path: Path) -> Path:
    return out_path.parent / f"{out_path.stem}.progress.jsonl"


def records_path_for(rows_path: Path) -> Path:
    """--repostcheck가 다시 검사한 progress 기록을 쓰는 곳: <rows stem>.records.jsonl."""
    return rows_path.parent / f"{rows_path.stem}.records.jsonl"


def summary_path_for(out_path: Path) -> Path:
    return out_path.with_suffix(".summary.json")


def read_progress(path: Path) -> list[dict[str, Any]]:
    """progress를 읽는다. 중간에 끊겨 잘린 마지막 줄은 버린다."""
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _ensure_trailing_newline(path: Path) -> None:
    """끊긴 실행이 남긴 잘린 마지막 줄 뒤에 새 기록이 붙어 함께 깨지지 않게 줄바꿈을 보탠다."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with open(path, "rb") as f:
        f.seek(-1, 2)
        last = f.read(1)
    if last != b"\n":
        with open(path, "ab") as f:
            f.write(b"\n")


def _verdict_of(rec: dict[str, Any]) -> Verdict:
    return Verdict(rec["value"], rec.get("evidence", ""), rec.get("subject", ""), rec.get("held_reason"), None)


def rows_from_progress(progress: list[dict[str, Any]], facts_by_rc: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in progress:
        groups[(rec["rcept_no"], rec["topic"])].append(rec)
    rows = []
    for (rc, topic), recs in sorted(groups.items()):
        # 다시 판정한 결과가 있으면 그 전의 호출 실패(llm_error·parse) 기록은 버린다.
        retried = [r for r in recs if r.get("held_reason") not in RETRYABLE_HELD]
        if retried and len(retried) < len(recs):
            recs = retried
        fact = facts_by_rc.get(rc) or {"rcept_no": rc, "corp_code": recs[0].get("corp_code"),
                                       "fiscal_year": recs[0].get("fiscal_year")}
        verdicts = [_verdict_of(r) for r in recs]
        v = combine(verdicts)
        chosen = next((r for r, x in zip(recs, verdicts) if x is v), recs[0])  # 고른 판정의 기록(충돌이면 첫 기록)
        first = recs[0]
        rows.append(build_row(fact, topic, v, model_used=model_used_of(chosen),
                              window_text=first.get("window_head", ""),
                              block_offset=first.get("block_offset", 0), anchor=first.get("anchor", "")))
    return rows


def _write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# --- 재시도 ------------------------------------------------------------------------

def is_rate_limited(exc: BaseException) -> bool:
    """429(요청 한도) 오류인가. AppError.status_code·openai RateLimitError 모두 본다."""
    if getattr(exc, "status_code", None) == 429 or getattr(exc, "status", None) == 429:
        return True
    return "RateLimit" in type(exc).__name__


def retry_after_seconds(exc: BaseException) -> float | None:
    """오류에 실린 Retry-After(초)를 찾는다. openai 오류는 `response.headers`, 그 밖엔 `headers`."""
    for holder in (getattr(exc, "response", None), exc):
        headers = getattr(holder, "headers", None)
        if not headers:
            continue
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            raw = None
        if raw is None:
            continue
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None
    cause = exc.__cause__
    return retry_after_seconds(cause) if cause is not None and cause is not exc else None


def with_retries(fn: CompleteJson, *, max_retries: int = 5, base_delay: float = 5.0,
                 is_retryable: Callable[[BaseException], bool] = is_rate_limited,
                 sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = lambda: random.uniform(0.0, 1.0)) -> CompleteJson:
    """429에서 max(Retry-After, 지수 백오프 5·10·20·40·80초) + 지터(0~1초)만큼 쉬고 최대 `max_retries`번
    다시 부른다. 그 뒤엔 예외를 그대로 올린다(judge_window가 llm_error로 남긴다)."""
    def wrapped(prompt: str) -> dict:
        for attempt in range(max_retries + 1):
            try:
                return fn(prompt)
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_retries or not is_retryable(exc):
                    raise
                delay = base_delay * (2 ** attempt)
                ra = retry_after_seconds(exc)
                if ra is not None:
                    delay = max(delay, ra)
                sleep(delay + jitter())
        raise AssertionError("unreachable")
    return wrapped


# --- 실행 ------------------------------------------------------------------------

def run_topics(
    *,
    facts_path: Path,
    docs_dir: Path,
    topics: list[str],
    out_path: Path,
    complete_json: CompleteJson,
    workers: int = 4,
    limit: int | None = None,
    rcept_filter: set[str] | None = None,
    usage_getter: Callable[[], dict | None] | None = None,
    retry_errors: bool = False,
    log: Callable[[str], None] | None = None,
    log_every: int = 2000,
) -> dict[str, Any]:
    """후보를 판정해 progress에 이어 쓰고, 끝나면 progress 전체로 행·요약을 원자적으로 쓴다.

    - `limit`: 이번 실행에서 판정할 (공시, 주제) 수의 상한(이어 하기로 건너뛴 것은 세지 않는다).
    - `usage_getter`: 판정 직후 같은 스레드에서 불러 그 호출의 토큰 사용량·model_used를 얻는다(선택).
    - `retry_errors`: progress의 llm_error·parse 기록을 끝난 것으로 치지 않고 다시 판정한다.
    - `log`: 진행 상황 한 줄(후보 훑기는 `log_every`건마다, 판정은 100건마다)."""
    unknown = [t for t in topics if t not in TOPIC_DEFS]
    if unknown:
        raise ValueError(f"알 수 없는 주제: {unknown}")
    started = time.time()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path = progress_path_for(out_path)

    facts = load_facts(facts_path)
    if rcept_filter is not None:
        facts = [f for f in facts if f["rcept_no"] in rcept_filter]
    facts_by_rc = {f["rcept_no"]: f for f in facts}
    prior = read_progress(progress_path)
    if retry_errors:
        ok_keys = {(r["rcept_no"], r["topic"]) for r in prior if r.get("held_reason") not in RETRYABLE_HELD}
        done = ok_keys
    else:
        done = {(r["rcept_no"], r["topic"]) for r in prior}

    stats: Counter = Counter()
    candidates = []

    def _scanned(facts_iter):
        for i, f in enumerate(facts_iter, 1):
            if log and i % log_every == 0:
                log(f"scan {i}/{len(facts)} docs · candidates {len(candidates)}")
            yield f

    for cand in iter_candidates(_scanned(facts), docs_dir, topics, skip=done, stats=stats):
        candidates.append(cand)
        if limit is not None and len(candidates) >= limit:
            break
    if log:
        log(f"scan done · candidates {len(candidates)} · skipped(done) {len(done)}")

    lock = threading.Lock()
    calls = 0
    run_held: Counter = Counter()
    tokens: Counter = Counter()
    models: Counter = Counter()

    def one(cand: tuple[dict[str, Any], str, Window], fh) -> None:
        nonlocal calls
        fact, topic, w = cand
        t0 = time.time()
        v = judge_window(TOPIC_DEFS[topic], w, corp_name=fact.get("corp_name", ""), complete_json=complete_json)
        try:
            usage = usage_getter() if usage_getter else None
        except Exception:  # noqa: BLE001 — 사용량 기록 실패가 판정 기록을 막으면 안 된다
            usage = None
        rec = {
            "rcept_no": fact["rcept_no"], "topic": topic,
            "corp_code": fact.get("corp_code"), "fiscal_year": fact.get("fiscal_year"),
            "corp_name": fact.get("corp_name", ""),
            "value": v.value, "evidence": v.evidence, "subject": v.subject, "held_reason": v.held_reason,
            "anchor": w.anchor, "block_offset": w.spans[0][0], "window_head": w.text[:120],
            "excerpt": evidence_excerpt(w.text, v.evidence),
            "raw": v.raw, "usage": usage, "latency": round(time.time() - t0, 2),
        }
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with lock:
            calls += 1
            if v.held_reason:
                run_held[v.held_reason] += 1
            if usage:
                for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
                    tokens[k] += int(usage.get(k) or 0)
                if usage.get("model_used"):
                    models[usage["model_used"]] += 1
            fh.write(line)
            fh.flush()
            if log and calls % 100 == 0:
                log(f"judged {calls}/{len(candidates)} · llm_error {run_held['llm_error']} · parse {run_held['parse']}")

    _ensure_trailing_newline(progress_path)
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        with open(progress_path, "a", encoding="utf-8") as fh:
            for fut in [ex.submit(one, c, fh) for c in candidates]:
                fut.result()
    except BaseException:
        # 중단(Ctrl-C·예외)되면 아직 시작하지 않은 판정은 버린다. 끝난 것은 progress에 있다.
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    ex.shutdown(wait=True)

    progress = read_progress(progress_path)
    progress = [r for r in progress if r["topic"] in topics and (rcept_filter is None or r["rcept_no"] in rcept_filter)]
    rows = rows_from_progress(progress, facts_by_rc)
    _write_jsonl_atomic(out_path, rows)

    by_topic: dict[str, Counter] = defaultdict(Counter)
    held: dict[str, Counter] = defaultdict(Counter)
    by_method: Counter = Counter()
    for r in rows:
        by_method[r["method"]] += 1
        by_topic[r["topic"]][r["value"]] += 1
        if r["held_reason"]:
            held[r["topic"]][r["held_reason"]] += 1
    summary = {
        "topics": topics,
        "facts": len(facts),
        "rows": len(rows),
        "calls_this_run": calls,
        "resumed_skipped": len(done),
        "errors_this_run": {"llm_error": run_held["llm_error"], "parse": run_held["parse"]},
        "errors_total": {"llm_error": sum(c["llm_error"] for c in held.values()),
                         "parse": sum(c["parse"] for c in held.values())},
        "no_xml": stats["no_xml"],
        "no_window": {t: stats[f"no_window:{t}"] for t in topics},
        "by_topic": {t: dict(by_topic[t]) for t in topics},
        "held_reason": {t: dict(held[t]) for t in topics},
        "tokens_this_run": dict(tokens),
        "models_this_run": dict(models),
        "retry_errors": retry_errors,
        "workers": workers,
        "limit": limit,
        "method": METHOD,
        "by_method": dict(by_method),
        "parser_version": PARSER_VERSION,
        "elapsed_sec": round(time.time() - started, 1),
    }
    _write_json_atomic(summary_path_for(out_path), summary)
    return summary


def repostcheck_progress(*, progress_path: Path, docs_dir: Path, out_path: Path) -> dict[str, Any]:
    """LLM 없이 progress의 모델 답(raw)에 지금의 사후 검사를 다시 걸어 행을 새로 쓴다.

    기록마다 원문으로 창을 다시 고른다. 창의 anchor·block_offset이 기록과 같으면 raw에 postcheck를
    다시 걸고, 다르면(창 규칙이 바뀜) 기록된 판정을 그대로 두고 `window_changed`로 센다. raw가 없거나
    value가 없는 기록(llm_error·parse)도 그대로 둔다. 행은 `out_path`에 원자적으로, 바뀐 내역 요약은
    `<out stem>.repostcheck.json`에 쓴다. progress 파일은 읽기만 한다."""
    started = time.time()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = read_progress(progress_path)
    by_rc: dict[str, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        by_rc[rec["rcept_no"]].append(i)

    counts: Counter = Counter()
    value_changes: dict[str, Counter] = defaultdict(Counter)
    held_changes: dict[str, Counter] = defaultdict(Counter)
    updated: list[dict[str, Any]] = list(records)
    for rc in sorted(by_rc):
        idxs = by_rc[rc]
        text: str | None = None
        loaded = False
        for i in idxs:
            rec = records[i]
            raw = rec.get("raw")
            topic = rec["topic"]
            if topic not in TOPIC_DEFS or not isinstance(raw, dict) or "value" not in raw:
                counts["kept_no_raw"] += 1
                continue
            if not loaded:
                text, loaded = doc_text(docs_dir, rc), True
            defn = TOPIC_DEFS[topic]
            w = pick_window(defn, text) if text is not None else None
            if w is None or w.anchor != rec.get("anchor") or w.spans[0][0] != rec.get("block_offset"):
                counts["window_changed"] += 1
                continue
            v = postcheck(defn, raw, w)
            counts["rechecked"] += 1
            if (v.value, v.held_reason) != (rec.get("value"), rec.get("held_reason")):
                counts["changed"] += 1
                value_changes[topic][f"{rec.get('value')}->{v.value}"] += 1
                held_changes[topic][f"{rec.get('held_reason')}->{v.held_reason}"] += 1
            updated[i] = {**rec, "value": v.value, "evidence": v.evidence, "subject": v.subject,
                          "held_reason": v.held_reason, "excerpt": evidence_excerpt(w.text, v.evidence)}

    rows = rows_from_progress(updated, {})
    _write_jsonl_atomic(out_path, rows)
    # 다시 검사한 기록(근거·발췌 포함) — 표본 뽑기가 낡은 progress 발췌 대신 읽는다.
    records_path = records_path_for(out_path)
    _write_jsonl_atomic(records_path, updated)
    by_topic: dict[str, Counter] = defaultdict(Counter)
    by_method: Counter = Counter()
    for r in rows:
        by_method[r["method"]] += 1
        by_topic[r["topic"]][r["value"]] += 1
    summary = {
        "progress": str(progress_path),
        "records": len(records),
        "rows": len(rows),
        "rechecked": counts["rechecked"],
        "changed": counts["changed"],
        "window_changed": counts["window_changed"],
        "kept_no_raw": counts["kept_no_raw"],
        "value_changes": {t: dict(c) for t, c in value_changes.items()},
        "held_reason_changes": {t: dict(c) for t, c in held_changes.items()},
        "by_topic": {t: dict(c) for t, c in by_topic.items()},
        "by_method": dict(by_method),
        "records_path": str(records_path),
        "parser_version": PARSER_VERSION,
        "elapsed_sec": round(time.time() - started, 1),
    }
    _write_json_atomic(out_path.with_suffix(".repostcheck.json"), summary)
    return summary


def dry_judge(prompt: str) -> dict:
    """네트워크 없는 가짜 판정기(--dry-judge): 언제나 UNDETERMINED."""
    return {"value": UNDETERMINED, "evidence": "", "subject": "none"}
