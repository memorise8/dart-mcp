"""감사의견 전문 폴백 분류.

의견 단락 추출이 실패해 unknown 이 된 보고서도, 본문에는 표준 의견 문구가
그대로 있다(표본 250건 중 78% 자동 판정 — §14.1). 검사 순서가 핵심이다:
'적정' 문구는 한정·부적정 보고서의 근거 단락에도 나오므로
부적정 → 의견거절 → 한정 → 적정 순으로 먼저 잡히는 것이 이긴다.
"""

from __future__ import annotations

import re

# (라벨, 패턴) — 목록 순서가 곧 검사 순서다. 순서를 바꾸면 오분류된다.
OPINION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("부적정", re.compile(r"공정하게\s*표시하고\s*있지\s*않습니다|부적정의견")),
    ("의견거절", re.compile(r"의견을?\s*표명하지\s*않습니다|의견거절")),
    ("한정", re.compile(r"한정의견|한정\s*의견의\s*근거")),
    ("적정", re.compile(
        r"중요성의?\s*관점에서\s*공정하게\s*표시하고\s*있습니다"
        r"|모든\s*중요한\s*점에서\s*공정하게\s*표시하고\s*있습니다"
    )),
)


def classify_opinion_fulltext(full_text: str) -> str:
    """전문에서 표준 의견 문구를 우선순위대로 찾는다. 없으면 unknown."""
    if not full_text:
        return "unknown"
    for label, pattern in OPINION_PATTERNS:
        if pattern.search(full_text):
            return label
    return "unknown"
