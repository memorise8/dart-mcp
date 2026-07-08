"""
OpenDART API 키(`crtfc_key`) 리댁션 유틸리티.

URL, 쿼리 파라미터 dict, 예외 메시지 문자열 등 어디에서도 `crtfc_key`가
평문으로 노출되지 않도록 하는 단일 진입점(`redact`)을 제공한다.
`dart_search_mcp.client`의 OpenDART HTTP 경계와 `cli.py`의 진단 출력이
이 헬퍼를 공유해서 사용한다.

이 모듈을 import하는 부수 효과로 `httpx` 로거의 레벨을 WARNING으로 올린다.
httpx는 기본적으로 요청 URL(크리덴셜 쿼리 파라미터 포함)을 INFO 레벨로
로깅하므로, 이 모듈이 import되는 즉시 그 INFO 로그 자체가 발생하지 않도록
막는다. `dart_search_mcp.client`가 이 모듈을 import하므로, CLI/MCP 서버가
기동되는 모든 경로에서 이 억제가 적용된다.
"""

from __future__ import annotations

import logging
import re
from typing import Any, overload

from dart_search_mcp.config import API_KEY

REDACTED = "<redacted>"

logging.getLogger("httpx").setLevel(logging.WARNING)

_CRTFC_KEY_QUERY_PATTERN = re.compile(r"crtfc_key=[^&\s]*")


def _redact_str(text: str) -> str:
    redacted = _CRTFC_KEY_QUERY_PATTERN.sub(f"crtfc_key={REDACTED}", text)
    if API_KEY:
        redacted = redacted.replace(API_KEY, REDACTED)
    return redacted


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: (REDACTED if key == "crtfc_key" else value) for key, value in params.items()}


@overload
def redact(value: str) -> str: ...
@overload
def redact(value: dict[str, Any]) -> dict[str, Any]: ...


def redact(value: str | dict[str, Any]) -> str | dict[str, Any]:
    """URL/예외 메시지 문자열 또는 쿼리 파라미터 dict에서 `crtfc_key` 값을
    가린 사본을 반환한다. 입력은 변경하지 않는다."""
    if isinstance(value, dict):
        return _redact_params(value)
    return _redact_str(value)
