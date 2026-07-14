"""`audit_facts.jsonl`(Task 4 산출물)에서 `kam_present=true` 행만 골라 KAM
원문을 배치로 태깅하고, 사이드카 `kam_tags.jsonl`을 내는 자립 CLI 전용 모듈.

Phase ②(`.omo/plans/dart-kam-llm-tagging.md` Task 3): Task 1(순수 태소노미/
프롬프트 빌더/응답 파서, `dart_search_mcp.kam_taxonomy`)과 Task 2(httpx
래퍼/content-hash 캐시/단건 태깅, `dart_search_mcp.tools.kam_tagger`)를 묶어
대량(bulk) 처리 루프를 얹는다. `dart_search_mcp.tools.extract_facts`(Task 4)의
아래 관례를 그대로 재사용한다(체크포인트/run-params 가드/JSONL 자가복구
구조는 그 모듈과 동형이지만, 이 저장소 관례대로 - `bulk_audit.py`/
`extract_facts.py`가 서로 그렇듯 - 이 모듈에서 독립적으로 재구현한다.
private 헬퍼를 모듈 간에 직접 import하지 않는다):

- **체크포인트/재개:** `resume=True`면 기존 체크포인트를 읽어 이미 처리된
  rcept는 건너뛰고 누적 집계를 이어간다. `resume=False`(기본값)면 항상
  새로 시작한다. run-params 가드(`facts_path`/`output_path`/`model`/
  `base_url`)는 `--resume`으로 다른 입력/모델을 섞어 쓰는 것을 막는다.
- **JSONL 자가복구:** `resume=True`로 시작할 때마다 기존 `kam_tags.jsonl`을
  한 번 스캔해 torn(부분) 마지막 라인을 버리고 중복 rcept_no를 병합한 뒤
  원자적으로 재작성한다 - 그 rcept 집합을 체크포인트에 보강해 재기록을
  막는다.
- **예외 격리:** rcept 한 건의 태깅 실패(`KamLlmError` 등 어떤 예외든)는 그
  rcept만 실패로 집계하고 전체 실행은 계속한다.
- **원자적 쓰기:** 체크포인트/캐시/summary는 tmp+`Path.replace`로 원자적으로
  쓴다. JSONL 출력 자체는 매 rcept 처리 시 한 줄씩 append한다.

## 동시성

httpx 호출은 I/O-bound이므로 `ThreadPoolExecutor`로 태깅(네트워크 호출)만
병렬화한다. 다만 **공유 가변 상태(캐시 dict/체크포인트/출력 JSONL)는 항상
메인 스레드에서만 건드린다** - Task 2 리뷰가 지적한 `save_cache` 동시-라이터
충돌을 피하기 위해서다. 구체적으로:

1. 캐시 히트는 메인 스레드에서 즉시 처리한다(디스크 I/O도, 네트워크 호출도
   필요 없다).
2. 캐시 미스만 워커 스레드 풀에 제출한다. 워커(`_tag_worker`)는
   `build_tagging_prompt` -> `call_fn` -> `parse_tag_response`만 수행하는
   순수 계산 + 네트워크 호출이며, 캐시 dict/체크포인트/파일을 전혀
   건드리지 않는다.
3. 완료되는 대로(`as_completed`) 메인 스레드가 캐시 갱신 + JSONL append +
   체크포인트 저장을 수행한다.

## `tagged_at` 시계 미접근

`tag_kam_batch`(core)는 `tagged_at: str | None` 파라미터로 시각 문자열을
그대로 받아서 쓸 뿐, 스스로 시계를 읽지 않는다(`dry_run=True`가 아니면 필수
- 없으면 `ValueError`). 실제 "지금" 시각(`_utc_now_iso()`)을 만들어 주입하는
책임은 `run_tag_kam`(이 모듈의 얇은 CLI 어댑터, `cli.py`의 `dart tag-kam`
명령이 호출)에 있다. `extract_facts.emit_topic_cases_from_facts`의
`freshness_timestamp` 주입과 동일한 설계 원칙이다 - 한 번의 실행(run) 안에서
처리되는 모든 rcept는 같은 `tagged_at`을 공유한다(재개된 다음 실행의 새
rcept들은 그 실행 시점의 새 `tagged_at`을 받는다).
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dart_search_mcp.kam_taxonomy import KAM_TAXONOMY, build_tagging_prompt, parse_tag_response
from dart_search_mcp.tools.kam_tagger import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    CallFn,
    call_llm,
    load_cache,
    save_cache,
)
from dart_search_mcp.tools.temis import _utc_now_iso

logger = logging.getLogger(__name__)

_MAX_ERROR_SAMPLES = 10
_DEFAULT_CACHE_SAVE_EVERY = 20
_DEFAULT_PROGRESS_EVERY = 25
_DEFAULT_CONCURRENCY = 4


class TagKamSourceError(Exception):
    """입력 소스(facts JSONL)를 읽거나 해석할 수 없을 때."""


# ---------------------------------------------------------------------------
# facts.jsonl 로딩 + 대상 필터 (kam_present=true && kam_raw 비어있지 않음)
# ---------------------------------------------------------------------------


def load_kam_targets(facts_path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """`facts_path`(`audit_facts.jsonl`)에서 `kam_present=true`이고 `kam_raw`가
    비어있지 않은 행만 원래 등장 순서대로 골라 반환한다. `limit`은 이 필터를
    적용한 **이후**의 목록에 적용한다(`extract_facts`의 corp_cls -> limit
    순서와 동일한 관례)."""
    path = Path(facts_path)
    if not path.exists():
        raise TagKamSourceError(f"facts 파일을 찾을 수 없습니다: {path}")

    targets: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if not row.get("kam_present"):
                    continue
                kam_raw = row.get("kam_raw")
                if not isinstance(kam_raw, str) or not kam_raw.strip():
                    continue
                targets.append(row)
    except OSError as exc:
        raise TagKamSourceError(f"facts 파일을 읽을 수 없습니다 ({path}): {exc}") from exc

    if limit is not None:
        targets = targets[: max(0, limit)]
    return targets


# ---------------------------------------------------------------------------
# content-hash (이 모듈 전용 공식 - base_url까지 포함해 kam_tagger._cache_key와
# 의도적으로 다르다. 아래 docstring 참조)
# ---------------------------------------------------------------------------


def _kam_cache_key(model: str, base_url: str, kam_raw: str) -> str:
    """`sha256(model + "\\n" + base_url + "\\n" + kam_raw)` hexdigest.

    캐시 히트 여부를 워커 풀에 제출하기 **전에** 메인 스레드에서 미리
    판별하기 위해 이 모듈에서 자체 계산한다(공유 캐시 dict를 읽기 위해
    `tag_one_kam`을 부르면 미스일 때 그 자리에서 동기 호출까지 해버려
    병렬화 의미가 없어진다).

    `kam_tagger._cache_key`(`sha256(model + "\\n" + kam_raw)`, base_url
    미포함)와는 **의도적으로 다른 공식**이다: base_url이 키에 없으면
    같은 model·다른 base_url(예: 엔드포인트 교체)로 실행했을 때 캐시가
    히트해버려, 이전 엔드포인트로 계산된 태그를 새 base_url로 스탬프하는
    provenance 불일치가 생긴다. 이 모듈은 `tag_one_kam`을 호출하지 않고
    캐시를 직접 관리하므로(`tag_kam_batch`의 `_finalize_success`), 이
    자체 공식만 내부적으로 일관되면 되고 `kam_tagger`쪽 회귀 위험 없이
    바꿀 수 있다."""
    return hashlib.sha256(f"{model}\n{base_url}\n{kam_raw}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 체크포인트 (extract_facts._load_checkpoint 등과 동형 - 이 모듈 전용 재구현)
# ---------------------------------------------------------------------------


def _empty_counts() -> dict[str, Any]:
    return {
        "tagged_ok": 0,
        "failed": 0,
        "by_error_kind": {},
        "dropped_total": 0,
        "cache_hits": 0,
        "tag_distribution": {},
        "error_samples": {},
    }


def _empty_state() -> dict[str, Any]:
    return {"processed": {}, "run_params": None, "counts": _empty_counts()}


def _load_checkpoint(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("processed", {})
    data.setdefault("run_params", None)
    counts = data.setdefault("counts", _empty_counts())
    for key, default in _empty_counts().items():
        counts.setdefault(key, default)
    return data


def _save_checkpoint(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _run_params_header(*, facts_path: str | Path, output_path: str | Path, model: str, base_url: str) -> dict[str, Any]:
    return {
        "facts_path": str(facts_path),
        "output_path": str(output_path),
        "model": model,
        "base_url": base_url,
    }


def _ensure_run_params_match_checkpoint(state: dict[str, Any], run_params: dict[str, Any]) -> None:
    """`--resume`으로 체크포인트를 이어 쓸 때, 이번 호출의 facts/output/model/
    base_url이 체크포인트에 기록된 이전 실행과 다르면 명확한 오류를 낸다."""
    existing = state.get("run_params")
    if existing is not None and existing != run_params:
        raise ValueError(
            "체크포인트의 실행 파라미터가 현재 호출과 다릅니다 - 다른 facts/output/model/"
            "base-url로 같은 체크포인트를 재사용하면 이전 실행의 진행 상황이 이번 실행과 맞지 "
            f"않아 결과가 뒤섞일 수 있습니다. 체크포인트: {existing}, 현재 호출: {run_params}. "
            "--resume 없이 새로 시작하거나 다른 체크포인트 경로를 사용하세요."
        )
    state["run_params"] = run_params


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# resume 시 JSONL 자가복구 (extract_facts._scan_and_repair_output과 동형)
# ---------------------------------------------------------------------------


def _scan_and_repair_output(output_path: Path) -> dict[str, dict[str, Any]]:
    """resume 시작 시 기존 `kam_tags.jsonl`을 한 번 스캔해 자가복구한다.

    `json.loads` 성공 + `rcept_no` 필드가 있는 유효 라인만 남긴다(torn 마지막
    라인은 버려진다). 같은 rcept_no가 여러 번 나오면 가장 나중 값으로
    병합한다. 복구된 내용으로 파일을 tmp+replace로 원자적으로 재작성하고,
    `rcept_no -> row` 매핑을 반환한다."""
    if not output_path.exists():
        return {}

    order: list[str] = []
    rows_by_rcept: dict[str, dict[str, Any]] = {}
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            rcept_no = row.get("rcept_no")
            if not isinstance(rcept_no, str) or not rcept_no:
                continue
            if rcept_no not in rows_by_rcept:
                order.append(rcept_no)
            rows_by_rcept[rcept_no] = row

    tmp_path = output_path.with_name(output_path.name + ".repair.tmp")
    with open(tmp_path, "w", encoding="utf-8") as tmp_f:
        for rcept_no in order:
            tmp_f.write(json.dumps(rows_by_rcept[rcept_no], ensure_ascii=False))
            tmp_f.write("\n")
    tmp_path.replace(output_path)

    return rows_by_rcept


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------


def _record_failure(counts: dict[str, Any], kind: str, rcept_no: str) -> None:
    counts["failed"] += 1
    counts["by_error_kind"][kind] = counts["by_error_kind"].get(kind, 0) + 1
    samples: list[str] = counts["error_samples"].setdefault(kind, [])
    if len(samples) < _MAX_ERROR_SAMPLES:
        samples.append(rcept_no)


def _record_success(counts: dict[str, Any], *, tags: list[str], dropped: list[str], cache_hit: bool = False) -> None:
    counts["tagged_ok"] += 1
    counts["dropped_total"] += len(dropped)
    for tag in tags:
        counts["tag_distribution"][tag] = counts["tag_distribution"].get(tag, 0) + 1
    if cache_hit:
        counts["cache_hits"] += 1


def _build_summary(targets_total: int, counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "dry_run": False,
        "targets": targets_total,
        "tagged_ok": counts["tagged_ok"],
        "failed": counts["failed"],
        "by_error_kind": dict(counts["by_error_kind"]),
        "dropped_total": counts["dropped_total"],
        "cache_hits": counts["cache_hits"],
        "tag_distribution": dict(counts["tag_distribution"]),
        "error_samples": {kind: list(samples) for kind, samples in counts["error_samples"].items()},
    }


# ---------------------------------------------------------------------------
# 워커 (스레드 풀에서 실행 - 공유 가변 상태 미접근)
# ---------------------------------------------------------------------------


def _tag_worker(kam_raw: str, *, model: str, base_url: str, call_fn: CallFn) -> tuple[list[str], list[str]]:
    """워커 스레드에서 실행된다. `build_tagging_prompt` -> `call_fn` ->
    `parse_tag_response`만 수행하는 순수 계산 + 네트워크 호출이며, 캐시
    dict/체크포인트/출력 파일을 전혀 건드리지 않는다(전부 메인 스레드
    책임). 예외는 그대로 전파한다 - 메인 스레드의 `future.result()` 호출부가
    그 rcept 하나에만 격리해서 집계한다."""
    messages = build_tagging_prompt(kam_raw)
    content = call_fn(messages, model=model, base_url=base_url)
    return parse_tag_response(content)


# ---------------------------------------------------------------------------
# 메인 진입점 (core - 시계 미접근)
# ---------------------------------------------------------------------------


def tag_kam_batch(
    facts_path: str | Path,
    output_path: str | Path,
    *,
    tagged_at: str | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    cache_path: str | Path | None = None,
    resume: bool = False,
    limit: int | None = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    checkpoint: str | Path | None = None,
    summary_path: str | Path | None = None,
    dry_run: bool = False,
    call_fn: CallFn = call_llm,
    cache_save_every: int = _DEFAULT_CACHE_SAVE_EVERY,
    progress_every: int = _DEFAULT_PROGRESS_EVERY,
) -> dict[str, Any]:
    """`facts_path`의 `kam_present=true` 행을 `tag_one_kam`과 동등한 로직으로
    배치 태깅해 `output_path`에 JSONL(1행/1건)로 append하고, 완료 후
    `summary_path`(기본값: `<output_path>.summary.json`)에 요약 통계를
    원자적으로 쓴다. 반환값은 그 요약 dict과 동일하다.

    `dry_run=True`면 엔드포인트를 전혀 호출하지 않고 `{"dry_run": True,
    "targets": N, "taxonomy": [...]}`만 반환한다 - 출력/캐시/체크포인트/
    summary 파일을 전혀 만들지 않는다.

    `tagged_at`은 `dry_run=False`일 때 필수다(core는 시계를 직접 읽지 않는다
    - 실제 "지금" 시각을 만들어 주입하는 책임은 `run_tag_kam`에 있다).

    체크포인트(기본값: `<output_path>.checkpoint.json`)에 처리한 rcept
    집합과 누적 집계를 저장한다. `resume=True`면 이어 쓰기 전에 기존 JSONL을
    스캔해 자가복구한다(크래시 후 resume에도 중복행 0).
    """
    targets = load_kam_targets(facts_path, limit=limit)

    if dry_run:
        return {
            "dry_run": True,
            "targets": len(targets),
            "taxonomy": [tag for tag, _ in KAM_TAXONOMY],
        }

    if tagged_at is None:
        raise ValueError("tagged_at은 dry_run=False일 때 필수입니다 (호출부가 _utc_now_iso()로 주입)")

    facts_path = Path(facts_path)
    output_path = Path(output_path)
    cache_path_resolved = Path(cache_path) if cache_path else output_path.with_name(output_path.name + ".cache.json")
    checkpoint_path = Path(checkpoint) if checkpoint else output_path.with_name(output_path.name + ".checkpoint.json")
    summary_path_resolved = Path(summary_path) if summary_path else output_path.with_name(output_path.name + ".summary.json")

    total_selected = len(targets)

    state = _load_checkpoint(checkpoint_path) if resume else _empty_state()
    run_params = _run_params_header(facts_path=facts_path, output_path=output_path, model=model, base_url=base_url)
    _ensure_run_params_match_checkpoint(state, run_params)
    _save_checkpoint(checkpoint_path, state)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if resume:
        recovered = _scan_and_repair_output(output_path)
        for rcept_no, row in recovered.items():
            if rcept_no in state["processed"]:
                continue
            state["processed"][rcept_no] = "ok"
            _record_success(
                state["counts"],
                tags=list(row.get("tags") or []),
                dropped=list(row.get("dropped") or []),
            )
        _save_checkpoint(checkpoint_path, state)

    cache = load_cache(cache_path_resolved)
    cache_dirty_count = 0

    def _maybe_save_cache(force: bool = False) -> None:
        nonlocal cache_dirty_count
        if force or (cache_dirty_count and cache_dirty_count % cache_save_every == 0):
            save_cache(cache_path_resolved, cache)

    processed_count = 0

    def _log_progress() -> None:
        if progress_every and processed_count % progress_every == 0:
            logger.info(
                "진행률: %d/%d (성공 %d, 실패 %d, 캐시히트 %d)",
                processed_count,
                total_selected,
                state["counts"]["tagged_ok"],
                state["counts"]["failed"],
                state["counts"]["cache_hits"],
            )

    mode = "a" if resume else "w"

    with open(output_path, mode, encoding="utf-8") as out_f:

        def _finalize_success(*, rcept_no: str, tags: list[str], dropped: list[str], kam_hash: str, is_cache_hit: bool) -> None:
            nonlocal cache_dirty_count, processed_count
            if not is_cache_hit:
                cache[kam_hash] = {
                    "tags": list(tags),
                    "dropped": list(dropped),
                    "kam_hash": kam_hash,
                    "model": model,
                    "base_url": base_url,
                }
                cache_dirty_count += 1
            row = {
                "rcept_no": rcept_no,
                "tags": list(tags),
                "dropped": list(dropped),
                "kam_hash": kam_hash,
                "model": model,
                "base_url": base_url,
                "tagged_at": tagged_at,
            }
            out_f.write(json.dumps(row, ensure_ascii=False))
            out_f.write("\n")
            out_f.flush()
            _record_success(state["counts"], tags=tags, dropped=dropped, cache_hit=is_cache_hit)
            state["processed"][rcept_no] = "ok"
            _save_checkpoint(checkpoint_path, state)
            processed_count += 1
            _log_progress()
            _maybe_save_cache()

        def _finalize_failure(*, rcept_no: str, kind: str) -> None:
            nonlocal processed_count
            _record_failure(state["counts"], kind, rcept_no)
            state["processed"][rcept_no] = kind
            _save_checkpoint(checkpoint_path, state)
            processed_count += 1
            _log_progress()

        max_workers = max(1, int(concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pending_futures: dict[Any, tuple[str, str]] = {}
            # 같은 실행(run) 안에서 입력 facts.jsonl에 같은 rcept_no가 두 번 이상
            # 나올 수 있다. `state["processed"]`는 제출된 rcept가 완료(성공/실패
            # finalize)된 뒤에야 갱신되므로, 캐시가 콜드인 상태에서 같은 rcept_no가
            # 완료 전에 다시 나오면 풀에 중복 제출되어 출력 JSONL에 같은 rcept_no가
            # 두 행 생긴다("무중복" 설계 위반). 제출 시점에 바로 표시하는
            # `submitted`로 이를 막는다.
            submitted: set[str] = set()

            for i, target in enumerate(targets, start=1):
                rcept_no = str(target.get("rcept_no", "") or "").strip()
                if not rcept_no:
                    synthetic_key = f"__norcept_{i}"
                    if synthetic_key in state["processed"]:
                        continue
                    _record_failure(state["counts"], "missing_rcept_no", "<unknown>")
                    state["processed"][synthetic_key] = "missing_rcept_no"
                    _save_checkpoint(checkpoint_path, state)
                    continue
                if rcept_no in state["processed"] or rcept_no in submitted:
                    continue

                kam_raw = str(target.get("kam_raw", "") or "")
                kam_hash = _kam_cache_key(model, base_url, kam_raw)
                cached = cache.get(kam_hash)
                if cached is not None:
                    submitted.add(rcept_no)
                    _finalize_success(
                        rcept_no=rcept_no,
                        tags=list(cached.get("tags") or []),
                        dropped=list(cached.get("dropped") or []),
                        kam_hash=kam_hash,
                        is_cache_hit=True,
                    )
                    continue

                submitted.add(rcept_no)
                future = pool.submit(_tag_worker, kam_raw, model=model, base_url=base_url, call_fn=call_fn)
                pending_futures[future] = (rcept_no, kam_hash)

            for future in as_completed(pending_futures):
                rcept_no, kam_hash = pending_futures[future]
                try:
                    tags, dropped = future.result()
                except Exception as exc:  # noqa: BLE001 - 이 rcept 하나에만 예외를 격리한다.
                    _finalize_failure(rcept_no=rcept_no, kind=type(exc).__name__)
                    continue

                _finalize_success(
                    rcept_no=rcept_no,
                    tags=list(tags),
                    dropped=list(dropped),
                    kam_hash=kam_hash,
                    is_cache_hit=False,
                )

    _maybe_save_cache(force=True)

    summary = _build_summary(total_selected, state["counts"])
    _write_json_atomic(summary_path_resolved, summary)
    return summary


# ---------------------------------------------------------------------------
# 얇은 CLI 어댑터 (실제 시계 - 이 함수만 `_utc_now_iso()`를 호출한다)
# ---------------------------------------------------------------------------


def run_tag_kam(
    facts_path: str | Path,
    output_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    cache_path: str | Path | None = None,
    resume: bool = False,
    limit: int | None = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    checkpoint: str | Path | None = None,
    summary_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """`cli.py`의 `dart tag-kam` 명령이 호출하는 얇은 어댑터.

    `tag_kam_batch`(core, 시계 미접근)에 실제 `tagged_at`(`_utc_now_iso()`)을
    주입해 호출한다 - 이 모듈에서 실제 "지금" 시각을 만드는 곳은 여기뿐이다.
    `call_fn`은 `tag_kam_batch`의 기본값(`call_llm`, 실제 네트워크 호출)을
    그대로 쓴다."""
    return tag_kam_batch(
        facts_path,
        output_path,
        tagged_at=None if dry_run else _utc_now_iso(),
        model=model,
        base_url=base_url,
        cache_path=cache_path,
        resume=resume,
        limit=limit,
        concurrency=concurrency,
        checkpoint=checkpoint,
        summary_path=summary_path,
        dry_run=dry_run,
    )
