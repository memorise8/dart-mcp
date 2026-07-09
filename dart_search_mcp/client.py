from typing import cast

import httpx

from dart_search_mcp.config import API_KEY, BASE_URL
from dart_search_mcp.redact import redact
from dart_search_mcp.results import DartError, DartNoData, DartResult, DartSuccess
from dart_search_mcp.types import JsonObject, QueryParams

async def _fetch_dart_result(endpoint: str, params: QueryParams) -> DartResult:
    """
    공통 DART API 호출 함수.
    성공(DartSuccess) / 조회된 데이터 없음(DartNoData) / 오류(DartError)를
    구분하는 구조화된 결과(DartResult)를 반환합니다.
    """
    params["crtfc_key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()
            data = cast(dict[str, object], response.json())
    except httpx.TimeoutException:
        return DartError(message="오류: 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.")
    except httpx.HTTPStatusError as e:
        return DartError(message=f"오류: HTTP 오류가 발생했습니다. 상태 코드: {e.response.status_code}")
    except httpx.RequestError as e:
        return DartError(message=f"오류: 네트워크 오류가 발생했습니다. {redact(str(e))}")
    except Exception as e:
        return DartError(message=f"오류: 예상치 못한 오류가 발생했습니다. {redact(str(e))}")

    # DART API 상태 코드 확인
    status = data.get("status", "")
    message = data.get("message", "")
    # status 013은 오류가 아니라 "조회된 데이터 없음" 신호
    if status == "013":
        return DartNoData()
    if status and status != "000":
        return DartError(message=f"오류: {message} (status: {status})", status=str(status))

    return DartSuccess(data=data)

async def _fetch_dart(endpoint: str, params: QueryParams) -> JsonObject | str:
    """
    공통 DART API 호출 함수.
    성공 시 파싱된 dict를 반환하고, 실패 시 오류 메시지 문자열을 반환합니다.

    내부적으로는 `_fetch_dart_result`가 만드는 구조화된 결과(DartResult)를
    기존 공개 반환 형태(JsonObject | str)로 변환하기만 하는 얇은 래퍼입니다.
    """
    result = await _fetch_dart_result(endpoint, params)
    if isinstance(result, DartSuccess):
        return result.data
    return result.message

async def _fetch_dart_binary(endpoint: str, params: QueryParams) -> bytes | str:
    """DART API에서 바이너리(ZIP) 파일을 다운로드합니다."""
    params["crtfc_key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            # JSON 응답이면 오류 메시지
            if "application/json" in content_type:
                data = response.json()
                status = data.get("status", "")
                message = data.get("message", "")
                return f"오류: {message} (status: {status})"

            return response.content
    except httpx.TimeoutException:
        return "오류: 요청 시간이 초과되었습니다."
    except httpx.HTTPStatusError as e:
        return f"오류: HTTP 오류 상태 코드: {e.response.status_code}"
    except Exception as e:
        return f"오류: {redact(str(e))}"
