"""OpenAI 호환 엔드포인트를 통한 KAM(핵심감사사항) 원문 LLM 태깅.

Phase ②(`.omo/plans/dart-kam-llm-tagging.md` Task 2): Task 1의 순수
프롬프트 빌더(`build_tagging_prompt`)/응답 파서(`parse_tag_response`) 위에
**실제 LLM 호출**(`call_llm`, httpx 기반)과 **content-hash 캐시**
(`load_cache`/`save_cache`), 그리고 **단건 태깅**(`tag_one_kam`)을 얹는다.

이 모듈은 다음을 지킨다:
- **import-time 네트워크 없음**: `httpx`를 import만 하고, 실제 소켓은 함수
  실행 시점(`call_llm` 호출)에만 연다.
- **격리**: 코어 모듈(`audit_xml_parser`, `audit_facts_adapter`,
  `extract_facts` 등)은 이 모듈을 import하지 않는다 — LLM/네트워크 의존은
  이 모듈과 그 상위(CLI, 다음 태스크)에만 국한된다.
- **시계 미접근**: `tag_one_kam`은 `tagged_at` 같은 시각 값을 만들지 않는다
  — CLI(다음 태스크)가 결과에 시각을 주입한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import httpx

from dart_search_mcp.kam_taxonomy import build_tagging_prompt, parse_tag_response

# 실측 동작 확인된 기본값. 하드코딩 강제가 아니라 기본값일 뿐 — 호출부(CLI 등)가
# `model`/`base_url`을 자유롭게 오버라이드할 수 있다.
DEFAULT_BASE_URL = "http://192.168.0.4:10532/v1"
DEFAULT_MODEL = "gpt-5.4-mini"

# `call_llm`에 주입 가능한 콜러블의 타입 별칭(테스트에서 fake 함수로 대체).
CallFn = Callable[..., str]


class KamLlmError(RuntimeError):
    """LLM 호출(연결/타임아웃/비200/응답 형식) 실패 시 발생하는 예외.

    응답 body 전체는 절대 메시지에 담지 않는다(상태 코드/요지만).
    """


def _post_chat_completion(client: httpx.Client, base_url: str, payload: dict[str, Any]) -> str:
    try:
        response = client.post(f"{base_url}/chat/completions", json=payload)
    except httpx.TimeoutException as exc:
        raise KamLlmError(f"LLM 요청 시간이 초과되었습니다: {base_url}") from exc
    except httpx.RequestError as exc:
        raise KamLlmError(
            f"LLM 엔드포인트에 연결할 수 없습니다: {base_url} ({type(exc).__name__})"
        ) from exc

    if response.status_code != 200:
        raise KamLlmError(f"LLM 요청이 실패했습니다: 상태 코드 {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise KamLlmError("LLM 응답이 유효한 JSON이 아닙니다") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise KamLlmError(
            "LLM 응답 형식이 예상과 다릅니다 (choices[0].message.content 없음)"
        ) from exc

    if not isinstance(content, str):
        raise KamLlmError("LLM 응답의 content가 문자열이 아닙니다")

    return content


def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
) -> str:
    """OpenAI 호환 `POST {base_url}/chat/completions`를 호출해 응답 텍스트를 반환한다.

    `client`(httpx.Client)를 주입하면 그 클라이언트로 호출한다(테스트에서
    실제 소켓 없이 mock하기 위함). 없으면 이 함수가 임시 클라이언트를
    만들어 쓰고 닫는다. 요청 body는 `{model, messages, temperature: 0}`.

    실패(연결 오류/타임아웃/비200 상태/형식 오류)는 `KamLlmError`로
    변환한다 — 응답 body 전체는 노출하지 않는다.
    """
    payload = {"model": model, "messages": messages, "temperature": 0}

    if client is not None:
        return _post_chat_completion(client, base_url, payload)

    with httpx.Client(timeout=timeout) as owned_client:
        return _post_chat_completion(owned_client, base_url, payload)


def _cache_key(model: str, kam_raw: str) -> str:
    """`sha256(model + "\\n" + kam_raw)` hexdigest. 모델이 바뀌면 키도 바뀐다."""
    return hashlib.sha256(f"{model}\n{kam_raw}".encode("utf-8")).hexdigest()


def load_cache(path: Path | str) -> dict[str, Any]:
    """캐시 파일을 읽어 dict로 반환한다. 없거나 비어 있으면 빈 dict."""
    cache_path = Path(path)
    if not cache_path.exists():
        return {}

    text = cache_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}

    return json.loads(text)


def save_cache(path: Path | str, cache: dict[str, Any]) -> None:
    """캐시 dict를 `path`에 원자적으로(tmp 파일 작성 후 `Path.replace`) 저장한다."""
    cache_path = Path(path)
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(cache_path)


def tag_one_kam(
    kam_raw: str,
    *,
    model: str,
    base_url: str,
    cache: dict[str, Any],
    call_fn: CallFn = call_llm,
) -> dict[str, Any]:
    """KAM 원문 한 건을 태깅한다. 캐시 히트면 `call_fn`을 호출하지 않는다.

    반환값: `{tags, dropped, kam_hash, model, base_url}`. 캐시 미스일 때만
    `build_tagging_prompt` -> `call_fn` -> `parse_tag_response` 순으로
    실행하고, 결과를 `cache`(호출부가 전달한 dict, in-memory)에 저장한다.
    시각(`tagged_at`) 등은 이 함수의 책임이 아니다(CLI가 주입).
    """
    kam_hash = _cache_key(model, kam_raw)

    cached = cache.get(kam_hash)
    if cached is not None:
        return cached

    messages = build_tagging_prompt(kam_raw)
    content = call_fn(messages, model=model, base_url=base_url)
    tags, dropped = parse_tag_response(content)

    result: dict[str, Any] = {
        "tags": tags,
        "dropped": dropped,
        "kam_hash": kam_hash,
        "model": model,
        "base_url": base_url,
    }
    cache[kam_hash] = result
    return result
