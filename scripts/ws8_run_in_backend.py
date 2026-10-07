"""WS8 주제 판정을 TEMIS 백엔드 이미지 안에서 luna로 돌린다.

백엔드 소스(/w)와 수집기 소스(/c)를 PYTHONPATH에 두고 실행한다. env는 `--env-file`로만
넘긴다(이 스크립트는 env 값을 읽어 출력하지 않는다). 실행 모양은 task-C4C5-report.md 참고.

    python scripts/ws8_run_in_backend.py --facts /data/audit_facts.jsonl --docs /data/docs \\
        --out /out/topic_stance.jsonl [--topics A,B] [--limit N] [--workers 4] [--dry-judge]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # 수집기 루트(/c)


def _load_topic_judge():
    """`dart_search_mcp/__init__.py`는 MCP 서버를 띄우는 의존성(fastmcp 등)을 import한다.
    백엔드 이미지에는 그 패키지가 없으므로, 패키지 껍데기만 등록하고 하위 모듈을 바로 읽는다.
    topic_judge·audit_xml_parser·tools.extract_facts는 표준 라이브러리만 쓴다."""
    if "dart_search_mcp" not in sys.modules:
        pkg = types.ModuleType("dart_search_mcp")
        pkg.__path__ = [str(HERE / "dart_search_mcp")]
        sys.modules["dart_search_mcp"] = pkg
    from dart_search_mcp.topic_judge import run as run_mod
    from dart_search_mcp.topic_judge.topics import TOPIC_DEFS
    return run_mod, TOPIC_DEFS


def _backend_complete_json():
    """LLMClient 하나를 스레드들이 함께 쓴다(spike run_luna.py와 같은 방식 — OpenAI 클라이언트는
    스레드 안전하고, 동시성은 백엔드의 llm_rate_limiter 슬롯이 조절한다). 호출마다 토큰 사용량을
    스레드 지역 변수에 남긴다."""
    from app.core.config import get_config
    from app.llm.client import LLMClient

    config = get_config()
    model = config.dart_extraction_model
    client = LLMClient()
    tl = threading.local()
    orig = client._create_with_rate_limit

    def wrapped(payload):
        resp = orig(payload)
        u = getattr(resp, "usage", None)
        if u is not None:
            details = getattr(u, "completion_tokens_details", None)
            tl.usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "reasoning_tokens": getattr(details, "reasoning_tokens", 0) or 0,
            }
        return resp

    client._create_with_rate_limit = wrapped

    def complete_json(prompt: str) -> dict:
        tl.usage = None
        tl.model_used = None
        completion = client.complete_json(prompt=prompt, model=model)
        tl.model_used = completion.model_used  # 폴백 모델로 답했는지 행마다 남긴다
        return completion.payload

    def usage_getter():
        usage = dict(getattr(tl, "usage", None) or {})
        model_used = getattr(tl, "model_used", None)
        if model_used:
            usage["model_used"] = model_used
        return usage or None

    effort = (getattr(config, "llm_reasoning_efforts", {}) or {}).get(model)
    return complete_json, usage_getter, {"model": model, "reasoning_effort": effort}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--facts", required=True)
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--topics", default=None, help="콤마 구분. 기본: 8개 전부")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 판정할 (공시, 주제) 수 상한")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rcept-file", default=None, help="이 파일의 rcept_no(줄마다)만 판정")
    ap.add_argument("--retry-errors", action="store_true", help="progress의 llm_error·parse 기록을 다시 판정")
    ap.add_argument("--dry-judge", action="store_true", help="LLM 없이 가짜 판정기(UNDETERMINED)로 배선만 확인")
    args = ap.parse_args(argv)

    run_mod, topic_defs = _load_topic_judge()
    topics = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else list(topic_defs)
    rcept_filter = None
    if args.rcept_file:
        rcept_filter = {l.strip() for l in Path(args.rcept_file).read_text(encoding="utf-8").splitlines() if l.strip()}

    if args.dry_judge:
        complete_json, usage_getter, info = run_mod.dry_judge, None, {"model": "dry-judge", "reasoning_effort": None}
    else:
        complete_json, usage_getter, info = _backend_complete_json()
        complete_json = run_mod.with_retries(complete_json)  # 429: Retry-After·5초부터 지수 백오프, 5번
    print(json.dumps({"start": info, "topics": topics, "limit": args.limit, "workers": args.workers},
                     ensure_ascii=False), flush=True)

    summary = run_mod.run_topics(
        facts_path=Path(args.facts), docs_dir=Path(args.docs), topics=topics, out_path=Path(args.out),
        complete_json=complete_json, workers=args.workers, limit=args.limit, rcept_filter=rcept_filter,
        usage_getter=usage_getter, retry_errors=args.retry_errors,
        log=lambda m: print(m, flush=True),
    )
    summary["judge"] = info
    run_mod._write_json_atomic(run_mod.summary_path_for(Path(args.out)), summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
