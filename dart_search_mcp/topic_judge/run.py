"""WS8 주제 판정 실행기: 후보 고르기 → 병렬 판정 → progress 이어 쓰기 → 16칸 행.

판정 함수는 주입받은 `complete_json(prompt) -> dict` 하나에만 기댄다(스펙 §3).
실제 luna 호출은 `scripts/ws8_run_in_backend.py`가 백엔드 이미지 안에서 주입한다.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .judge import CompleteJson, judge_window
from .postcheck import UNDETERMINED, Verdict
from .topics import TOPIC_DEFS
from .window import Window, doc_text, pick_window

METHOD = "llm_luna"
PARSER_VERSION = "ws8-v1"
ROW_SUBJECT = "회사"
CONFIDENCE_DETERMINED = 0.8
CONFIDENCE_UNDETERMINED = 0.0
UNDETERMINED_PREFIX = "판정 불가: "
HELD_CONFLICT = "conflict"
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


def build_row(fact: dict[str, Any], topic: str, verdict: Verdict, *, window_text: str,
              block_offset: int, anchor: str) -> dict[str, Any]:
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
        "method": METHOD,
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


def _verdict_of(rec: dict[str, Any]) -> Verdict:
    return Verdict(rec["value"], rec.get("evidence", ""), rec.get("subject", ""), rec.get("held_reason"), None)


def rows_from_progress(progress: list[dict[str, Any]], facts_by_rc: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in progress:
        groups[(rec["rcept_no"], rec["topic"])].append(rec)
    rows = []
    for (rc, topic), recs in sorted(groups.items()):
        fact = facts_by_rc.get(rc) or {"rcept_no": rc, "corp_code": recs[0].get("corp_code"),
                                       "fiscal_year": recs[0].get("fiscal_year")}
        v = combine([_verdict_of(r) for r in recs])
        first = recs[0]
        rows.append(build_row(fact, topic, v, window_text=first.get("window_head", ""),
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


def with_retries(fn: CompleteJson, *, max_retries: int = 3, base_delay: float = 2.0,
                 is_retryable: Callable[[BaseException], bool] = is_rate_limited,
                 sleep: Callable[[float], None] = time.sleep) -> CompleteJson:
    """429에서 지수 백오프(2·4·8초)로 최대 `max_retries`번 다시 부른다. 그 뒤엔 예외를 그대로 올린다
    (judge_window가 llm_error로 남긴다)."""
    def wrapped(prompt: str) -> dict:
        for attempt in range(max_retries + 1):
            try:
                return fn(prompt)
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_retries or not is_retryable(exc):
                    raise
                sleep(base_delay * (2 ** attempt))
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
) -> dict[str, Any]:
    """후보를 판정해 progress에 이어 쓰고, 끝나면 progress 전체로 행·요약을 원자적으로 쓴다.

    - `limit`: 이번 실행에서 판정할 (공시, 주제) 수의 상한(이어 하기로 건너뛴 것은 세지 않는다).
    - `usage_getter`: 판정 직후 같은 스레드에서 불러 그 호출의 토큰 사용량을 얻는다(선택)."""
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
    done = {(r["rcept_no"], r["topic"]) for r in read_progress(progress_path)}

    stats: Counter = Counter()
    candidates = []
    for cand in iter_candidates(facts, docs_dir, topics, skip=done, stats=stats):
        candidates.append(cand)
        if limit is not None and len(candidates) >= limit:
            break

    lock = threading.Lock()
    calls = 0
    run_held: Counter = Counter()
    tokens: Counter = Counter()

    def one(cand: tuple[dict[str, Any], str, Window], fh) -> None:
        nonlocal calls
        fact, topic, w = cand
        t0 = time.time()
        v = judge_window(TOPIC_DEFS[topic], w, corp_name=fact.get("corp_name", ""), complete_json=complete_json)
        usage = usage_getter() if usage_getter else None
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
            fh.write(line)
            fh.flush()

    with open(progress_path, "a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in [ex.submit(one, c, fh) for c in candidates]:
            fut.result()

    progress = read_progress(progress_path)
    progress = [r for r in progress if r["topic"] in topics and (rcept_filter is None or r["rcept_no"] in rcept_filter)]
    rows = rows_from_progress(progress, facts_by_rc)
    _write_jsonl_atomic(out_path, rows)

    by_topic: dict[str, Counter] = defaultdict(Counter)
    held: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
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
        "workers": workers,
        "limit": limit,
        "method": METHOD,
        "parser_version": PARSER_VERSION,
        "elapsed_sec": round(time.time() - started, 1),
    }
    _write_json_atomic(summary_path_for(out_path), summary)
    return summary


def dry_judge(prompt: str) -> dict:
    """네트워크 없는 가짜 판정기(--dry-judge): 언제나 UNDETERMINED."""
    return {"value": UNDETERMINED, "evidence": "", "subject": "none"}
