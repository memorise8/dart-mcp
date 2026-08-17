"""감사의견 전문 폴백 — 검사 순서가 정확성의 전부다."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from opinion_fallback import classify_opinion_fulltext


def test_적정_표준문구():
    assert classify_opinion_fulltext(
        "중요성의 관점에서 공정하게 표시하고 있습니다"
    ) == "적정"
    assert classify_opinion_fulltext(
        "모든 중요한 점에서 공정하게  표시하고 있습니다"
    ) == "적정"


def test_한정이_적정문구보다_먼저다():
    # 한정 보고서의 '한정의견의 근거' 단락에는 적정 문구가 함께 나온다
    text = (
        "한정의견의 근거 ... 위 사항을 제외하고는 중요성의 관점에서 "
        "공정하게 표시하고 있습니다"
    )
    assert classify_opinion_fulltext(text) == "한정"


def test_의견거절이_한정보다_먼저다():
    text = "우리는 위 재무제표에 대하여 의견을 표명하지 않습니다. 한정의견의 근거가 아니라"
    assert classify_opinion_fulltext(text) == "의견거절"


def test_부적정이_가장_먼저다():
    text = "재무제표는 공정하게 표시하고 있지 않습니다"
    assert classify_opinion_fulltext(text) == "부적정"


def test_아무_문구도_없으면_unknown():
    assert classify_opinion_fulltext("감사보고서 표지와 목차만 있는 문서") == "unknown"
    assert classify_opinion_fulltext("") == "unknown"
