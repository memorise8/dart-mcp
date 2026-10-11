"""2층 ② 사채·우선주 기록을 TEMIS 백엔드 이미지 안에서 luna로 돌린다(프롬프트 버전은 instruments.PROMPT_VERSION).

백엔드 소스(/w)와 수집기 소스(/c)를 PYTHONPATH에 두고 실행한다. env는 `--env-file`로만 넘긴다
(이 스크립트는 env 값을 읽어 출력하지 않는다). 1층 결과 파일만 읽으므로 dart-agent는 필요 없다.

    python scripts/ledger_instruments_in_backend.py --tables /data/tables --ledger /data/ledger \\
        --out /out/instruments.jsonl [--rcept-list FILE] [--limit N] [--workers 4] [--no-cache] [--dry]

- 대상: --ledger/facts.jsonl 의 사채·우선주 개념 사실이나 주석 제목으로 needs_instruments 인 공시.
- 출력: 공시·fs_scope 마다 한 줄 {rcept_no, fs_scope, records, dropped, prompt_version, model, usage, ...}.
  이어 하기 — 이미 있는 (rcept_no, fs_scope)는 건너뛴다(llm_error 줄은 다시 부른다).
- 캐시: 기본 <out>.cache.jsonl(키 = sha256(PROMPT_VERSION + 입력 묶음), 값 = 원 응답). --no-cache 면 쓰지 않는다
  (같은 100건 재현율 측정용). --dry 는 LLM 없이 {"instruments": []} 를 돌려주는 가짜로 배선만 확인하고
  캐시를 쓰지 않는다.
- 요약: <out>.summary.json
- --needs-out FILE: LLM 없이 대상 공시를 세어 「접수번호<TAB>범위,범위」 줄로 쓰고 끝낸다(무작위 표본 뽑기용,
  범위가 빈 줄 = 표 0개). 수집기 venv 에서도 돈다. missing·unreadable 수도 알린다.
- --summarize: --out 만 읽어 (rcept_no, fs_scope)마다 마지막 줄로 측정 요약(근거 통과율·issue·dropped·모델)을
  계산해 출력하고 <out>.measure.json 에 쓴다(LLM 없음, 이어 하기 뒤 측정은 이것을 쓴다).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import types
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # 수집기 루트(/c)


def _load_instruments():
    """`dart_search_mcp/__init__.py`는 MCP 서버 의존성(fastmcp 등)을 import한다. 백엔드 이미지에는 그 패키지가
    없으므로 패키지 껍데기만 등록하고 ledger 하위 모듈만 읽는다(ws8_run_in_backend._load_topic_judge 와 같은 방식).
    ledger/__init__.py 는 비어 있고 instruments·instrument_input·instrument_validate 는 표준 라이브러리만 쓴다."""
    if "dart_search_mcp" not in sys.modules:
        pkg = types.ModuleType("dart_search_mcp")
        pkg.__path__ = [str(HERE / "dart_search_mcp")]
        sys.modules["dart_search_mcp"] = pkg
    from dart_search_mcp.ledger import instruments
    return instruments


def _backend_complete_json():
    """백엔드 LLMClient 하나를 스레드들이 함께 쓴다(ws8_run_in_backend._backend_complete_json 과 같은 방식).
    messages 의 마지막 user 글을 prompt 로, 그 앞(system)을 history_messages 로 넘긴다. 응답 형식은 백엔드
    complete_json 의 JSON 모드에 맡기고 schema 는 프롬프트 안에 글로 들어 있다. 호출마다 토큰 사용량·실제
    응답 모델을 스레드 지역 변수에 남긴다. 429는 topic_judge.run.with_retries 로 지수 백오프한다."""
    from app.core.config import get_config
    from app.llm.client import LLMClient

    from dart_search_mcp.topic_judge.run import with_retries

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

    def call(pair) -> dict:
        messages, _schema = pair
        tl.usage = None
        tl.model_used = None
        completion = client.complete_json(prompt=messages[-1]["content"], model=model,
                                          history_messages=list(messages[:-1]))
        tl.model_used = completion.model_used  # 폴백 모델로 답했는지 줄마다 남긴다
        return completion.payload

    retrying = with_retries(call)

    def complete_json(messages: list, schema: dict) -> dict:
        return retrying((messages, schema))

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
    ap.add_argument("--tables", type=Path, help="1층 표 파일 디렉터리(<접수번호>.json.gz)")
    ap.add_argument("--ledger", type=Path, help="2층 ① 출력 디렉터리(facts.jsonl)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rcept-list", type=Path, help="접수번호 한 줄에 하나(이 순서로)")
    ap.add_argument("--limit", type=int, help="이번 실행에서 새로 처리할 대상 공시 수 상한")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cache", type=Path, help="기본 <out>.cache.jsonl")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry", action="store_true", help="LLM 없이 가짜(빈 목록)로 배선만 확인")
    ap.add_argument("--needs-out", type=Path, help="대상 공시 목록만 쓰고 끝낸다(LLM 없음)")
    ap.add_argument("--summarize", action="store_true", help="--out 의 마지막 줄 기준 측정 요약만(LLM 없음)")
    args = ap.parse_args(argv)

    mod = _load_instruments()
    if args.summarize:
        summary = mod.summarize_out(args.out)
        path = args.out.with_name(args.out.name + ".measure.json")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    if args.tables is None or args.ledger is None:
        ap.error("--tables 와 --ledger 가 필요하다(--summarize 제외)")
    if args.rcept_list:
        rcepts = [ln.strip() for ln in args.rcept_list.read_text(encoding="utf-8").splitlines() if ln.strip()]
    else:
        rcepts = sorted(p.name[: -len(".json.gz")] for p in args.tables.glob("*.json.gz"))

    if args.needs_out:
        stats = Counter()
        needs = mod.list_needs(args.tables, mod.load_instrument_facts(args.ledger / "facts.jsonl"), rcepts, stats)
        args.needs_out.parent.mkdir(parents=True, exist_ok=True)
        args.needs_out.write_text("".join(f"{r}\t{','.join(s)}\n" for r, s in needs), encoding="utf-8")
        print(json.dumps({"scanned": len(rcepts), "needs": len(needs),
                          "no_tables": sum(1 for _r, s in needs if not s),
                          "two_scopes": sum(1 for _r, s in needs if len(s) > 1),
                          "missing": stats["missing"], "unreadable": stats["unreadable"]}), flush=True)
        return 0
    if args.dry:
        complete_json, usage_getter, info = mod.dry_complete_json, None, {"model": "dry", "reasoning_effort": None}
        cache = None
    else:
        complete_json, usage_getter, info = _backend_complete_json()
        cache = None if args.no_cache else mod.Cache(args.cache or args.out.with_name(args.out.name + ".cache.jsonl"))
    print(json.dumps({"start": info, "prompt_version": mod.PROMPT_VERSION, "filings": len(rcepts),
                      "limit": args.limit, "workers": args.workers, "cache": bool(cache)}, ensure_ascii=False),
          flush=True)

    facts = mod.load_instrument_facts(args.ledger / "facts.jsonl")
    summary = mod.run_filings(
        tables_dir=args.tables, facts=facts, out_path=args.out, rcepts=rcepts, complete_json=complete_json,
        model=info["model"], cache=cache, workers=args.workers, limit=args.limit, usage_getter=usage_getter,
        log=lambda m: print(m, flush=True))
    summary["judge"] = info
    summary_path = args.out.with_name(args.out.name + ".summary.json")
    tmp = summary_path.with_name(summary_path.name + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(summary_path)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
