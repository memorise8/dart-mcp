from typing import cast

import httpx

from dart_search_mcp.config import API_KEY, BASE_URL
from dart_search_mcp.types import JsonObject, QueryParams

async def _fetch_dart(endpoint: str, params: QueryParams) -> JsonObject | str:
    """
    공통 DART API 호출 함수.
    성공 시 파싱된 dict를 반환하고, 실패 시 오류 메시지 문자열을 반환합니다.
    """
    params["crtfc_key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()
            data = cast(dict[str, object], response.json())
    except httpx.TimeoutException:
        return "오류: 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
    except httpx.HTTPStatusError as e:
        return f"오류: HTTP 오류가 발생했습니다. 상태 코드: {e.response.status_code}"
    except httpx.RequestError as e:
        return f"오류: 네트워크 오류가 발생했습니다. {str(e)}"
    except Exception as e:
        return f"오류: 예상치 못한 오류가 발생했습니다. {str(e)}"

    # DART API 상태 코드 확인
    status = data.get("status", "")
    message = data.get("message", "")
    # status 013은 오류가 아니라 "조회된 데이터 없음" 신호
    if status == "013":
        return "조회된 데이터가 없습니다. (해당 조건에 맞는 공시/보고 내역이 없습니다)"
    if status and status != "000":
        return f"오류: {message} (status: {status})"

    return data

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
        return f"오류: {str(e)}"
