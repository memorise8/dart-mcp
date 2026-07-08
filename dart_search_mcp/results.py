"""
DART OpenAPI 호출 결과를 위한 구조화된 결과 타입.

`dart_search_mcp.client._fetch_dart_result`가 반환하는 내부 표현으로,
성공(DartSuccess) / 조회된 데이터 없음(DartNoData) / 오류(DartError) 세 가지
경우를 명시적으로 구분한다.

이 모듈은 내부 전용 계층이다: 기존 공개 MCP 도구/CLI 명령은 여전히
`dart_search_mcp.client._fetch_dart`를 통해 `JsonObject | str`을 그대로
반환받으며, 이 계층은 그 뒤에서만 사용된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from dart_search_mcp.types import JsonObject

NO_DATA_MESSAGE = "조회된 데이터가 없습니다. (해당 조건에 맞는 공시/보고 내역이 없습니다)"


@dataclass(frozen=True, slots=True)
class DartSuccess:
    """DART API가 정상(status 000)으로 응답한 결과."""

    data: JsonObject


@dataclass(frozen=True, slots=True)
class DartNoData:
    """DART API가 '조회된 데이터 없음'(status 013)으로 응답한 결과."""

    message: str = NO_DATA_MESSAGE


@dataclass(frozen=True, slots=True)
class DartError:
    """DART API 호출이 실패했거나 오류 상태를 반환한 결과.

    `status`는 DART가 반환한 상태 코드가 있을 때만 채워지며,
    타임아웃/네트워크 오류처럼 DART 응답 자체가 없는 경우에는 빈 문자열이다.
    """

    message: str
    status: str = ""


type DartResult = DartSuccess | DartNoData | DartError
