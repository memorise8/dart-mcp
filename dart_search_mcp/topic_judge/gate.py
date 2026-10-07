"""WS8 회계사 결과 관문: 주제마다 표본 20건 이상, wrong 1건 이하면 통과."""

from __future__ import annotations

from typing import Any, Iterable

from .topics import TOPIC_DEFS

ANSWERS = ("ok", "partial", "wrong")
MIN_SAMPLES = 20
MAX_WRONG = 1


def gate(samples: list[dict[str, Any]], answers: dict[str, str], *,
         expected_topics: Iterable[str] | None = None) -> dict[str, Any]:
    """answers: {sample_id: ok|partial|wrong}. 답이 없는 표본은 n에 들지 않는다(unanswered).

    주제마다 {n, ok, partial, wrong, unanswered, pass}와, 고칠 목록(partial 표본)을 낸다.
    `expected_topics`(기본: 8개 주제 전부) 중 표본이 하나도 없는 주제도 n=0·불통과로 보고한다."""
    narrowed = expected_topics is not None
    expected = list(expected_topics) if narrowed else list(TOPIC_DEFS)
    by_id = {s["sample_id"]: s for s in samples}
    unknown = sorted(set(answers) - set(by_id))
    if unknown:
        raise ValueError(f"표본에 없는 sample_id: {unknown[:5]}")
    bad = sorted({a for a in answers.values() if a not in ANSWERS})
    if bad:
        raise ValueError(f"답 값은 {'|'.join(ANSWERS)} 중 하나여야 한다: {bad}")

    topics: dict[str, dict[str, Any]] = {
        t: {"n": 0, "ok": 0, "partial": 0, "wrong": 0, "unanswered": 0} for t in expected
    }
    fix_list: list[dict[str, Any]] = []
    for s in samples:
        if narrowed and s["topic"] not in topics:
            continue  # --topics로 좁히면 그 밖의 주제 표본은 보지 않는다
        t = topics.setdefault(s["topic"], {"n": 0, "ok": 0, "partial": 0, "wrong": 0, "unanswered": 0})
        a = answers.get(s["sample_id"])
        if a is None:
            t["unanswered"] += 1
            continue
        t["n"] += 1
        t[a] += 1
        if a == "partial":
            fix_list.append({**s, "answer": a})
    for t in topics.values():
        t["pass"] = t["wrong"] <= MAX_WRONG and t["n"] >= MIN_SAMPLES
    return {"topics": topics, "fix_list": fix_list, "passed": bool(topics) and all(t["pass"] for t in topics.values())}
