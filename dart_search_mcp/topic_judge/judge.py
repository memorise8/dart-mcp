"""WS8 판정 호출: 프롬프트 → complete_json → 사후 검사."""

from __future__ import annotations

from typing import Callable

from .postcheck import UNDETERMINED, Verdict, postcheck
from .prompt import render_prompt
from .topics import TopicDef
from .window import Window

CompleteJson = Callable[[str], dict]


def judge_window(defn: TopicDef, window: Window, *, corp_name: str,
                 complete_json: CompleteJson) -> Verdict:
    """한 공시 × 한 주제를 판정한다. 호출 실패·형식 오류는 예외 없이 UNDETERMINED로 남긴다."""
    prompt = render_prompt(defn, corp_name=corp_name, window=window.text)
    try:
        raw = complete_json(prompt)
    except Exception:  # noqa: BLE001 — 실행은 계속돼야 한다(Review Focus 3)
        return Verdict(UNDETERMINED, "", "", "llm_error", None)
    if not isinstance(raw, dict) or "value" not in raw:
        return Verdict(UNDETERMINED, "", "", "parse", raw if isinstance(raw, dict) else None)
    return postcheck(defn, raw, window)
