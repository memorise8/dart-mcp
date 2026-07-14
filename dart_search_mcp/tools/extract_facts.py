"""`dart_collected/manifest.json`(Step 1 수집 매니페스트)을 순회하며 각
필링(rcept_no)의 로컬 감사보고서 XML을 Task 1 순수 파서
(`dart_search_mcp.audit_xml_parser.parse_audit_xml`)로 파싱해, **1행/1공시**
JSONL(`audit_facts.jsonl`)과 요약 JSON(`<output>.summary.json`)을 만드는
대량(bulk) CLI 전용 모듈.

이 모듈은 **순수 동기**다(async 없음) - `parse_audit_xml`도 순수 동기이고,
파일 읽기/쓰기 외에는 어떤 네트워크 호출도 하지 않으므로 asyncio가 필요
없다. `dart_search_mcp.tools.bulk_audit`(감사서류 ZIP 추출의 bulk 버전)의
아래 관례를 그대로 따른다(단, 함수 시그니처 자체에 `resume`을 받아 이
모듈이 재개 여부를 스스로 판단한다는 점만 다르다):

- **입력 소스:** Step 1 수집기(`dart_search_mcp.collect.collect_disclosures`)가
  쓴 매니페스트 JSON(`records[]`)을 읽어 대상 필링 목록을 만든다.
  `corp_cls` 필터, `limit`을 이 순서로 적용한다.
- **XML 선택:** 각 rcept의 `docs_dir/<rcept_no>/` 폴더에서 연결감사
  `<rcept>_00761.xml` > 감사 `<rcept>_00760.xml` > 사업보고서 임베드
  (`<rcept>.xml` 또는 그 폴더의 유일한 xml) 순으로 고른다. 실측 데이터
  48,537건 중 감사/연결감사 XML은 항상 `_00760.xml`/`_00761.xml`로만
  존재했고(임베드 사례는 관찰되지 않음), 폴더 자체가 없는 경우가 소수
  (166건) 있었다 - 두 우선순위 파일이 모두 없고 폴더에 xml이 정확히
  하나도 아니면 `no_xml`로 격리한다(폴더 없음도 동일하게 `no_xml`).
- **예외 격리:** rcept 한 건에서 어떤 예외가 나든(XML 읽기 실패,
  `parse_audit_xml` 내부 오류 등 - 파서 자체는 필드별 실패 시 예외를
  던지지 않도록 설계돼 있지만, 방어적으로 여기서도 잡는다) 그 rcept만
  `failed`(kind=예외 클래스명)로 집계하고 전체 실행은 계속한다 - 절대
  중단하지 않는다. `no_xml`도 같은 실패 집계(`by_error_kind`)에 들어간다.
- **체크포인트/재개:** `resume=True`면 `checkpoint` 경로의 기존 상태를
  읽어 이미 처리된 rcept는 건너뛰고 누적 집계(`counts`)를 이어간다.
  `resume=False`(기본값)면 기존 체크포인트 내용을 무시하고 항상 새로
  시작한다(디스크의 체크포인트 파일은 새 상태로 덮어쓴다). run-params
  가드는 `bulk_audit._ensure_run_params_match_checkpoint`와 동형이다 -
  `--resume`으로 이어 쓸 때 manifest/docs-dir/corp-cls/output이 바뀌면
  명확한 오류를 낸다.
- **원자적 쓰기:** 체크포인트와 summary JSON은 tmp 파일에 쓴 뒤
  `Path.replace`로 원자적으로 교체한다(`dart_search_mcp.collect`의 원자적
  쓰기 원본 패턴과 동일). **JSONL 출력 자체는** 매 rcept 처리 시 한 줄씩
  append한다 - 48k건 규모에서 매 줄마다 전체 파일을 tmp+replace로 다시
  쓰는 것은 비현실적이므로(브리프가 명시한 원자적 쓰기 대상은 체크포인트/
  summary이지 계속 자라나는 JSONL 로그 파일이 아니다), 대신
  "체크포인트가 진실원천"이라는 규칙으로 중복 행을 막는다: `resume=False`
  로 시작하면(또는 `resume=True`인데 체크포인트가 비어 있으면) 출력
  파일을 새로 쓰기 모드로 열어 이전 내용을 버리고, `resume=True`이고
  체크포인트에 이미 처리된 rcept가 있으면 append 모드로 열어 그 rcept들은
  다시 쓰지 않는다.

이 모듈은 어떤 MCP 도구도 등록하지 않는다(대량 처리는 블로킹 MCP 도구
호출로 적합하지 않다) - `cli.py`의 `dart extract-audit-facts` 명령을
통해서만 제공한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dart_search_mcp.audit_xml_parser import ParsedAuditReport, parse_audit_xml

logger = logging.getLogger(__name__)

_MAX_ERROR_SAMPLES = 10

_OPINION_KEYS: tuple[str, ...] = ("적정", "한정", "부적정", "의견거절", "unknown")


class ExtractFactsSourceError(Exception):
    """입력 소스(매니페스트 JSON)를 읽거나 해석할 수 없을 때."""


# ---------------------------------------------------------------------------
# 매니페스트 로딩
# ---------------------------------------------------------------------------


def load_records_from_manifest(path: str | Path) -> list[dict[str, Any]]:
    """Step 1 수집 매니페스트 JSON(`records[]`)을 그대로 읽어 반환한다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractFactsSourceError(f"매니페스트를 읽을 수 없습니다 ({path}): {exc}") from exc

    records = data.get("records") if isinstance(data, dict) else None
    if records is None:
        raise ExtractFactsSourceError(f"매니페스트 형식이 올바르지 않습니다(records 없음): {path}")
    if not isinstance(records, list):
        raise ExtractFactsSourceError(f"매니페스트 형식이 올바르지 않습니다(records가 배열이 아님): {path}")
    return records


def _filter_by_corp_cls(records: list[dict[str, Any]], corp_cls: set[str] | frozenset[str] | None) -> list[dict[str, Any]]:
    if not corp_cls:
        return records
    allowed = {c.strip() for c in corp_cls if c and c.strip()}
    if not allowed:
        return records
    return [r for r in records if str(r.get("corp_cls", "") or "") in allowed]


# ---------------------------------------------------------------------------
# XML 선택 (우선순위: 연결감사 00761 > 감사 00760 > 사업보고서 임베드)
# ---------------------------------------------------------------------------


def select_xml_path(docs_dir: str | Path, rcept_no: str) -> Path | None:
    """`docs_dir/<rcept_no>/` 폴더에서 파싱할 XML 하나를 고른다.

    우선순위: 연결감사 `<rcept>_00761.xml` > 감사 `<rcept>_00760.xml` >
    사업보고서 임베드(`<rcept>.xml` 또는 그 폴더의 유일한 xml). 폴더가
    없거나, 위 우선순위 파일이 모두 없고 폴더의 xml 파일이 정확히 하나가
    아니면(0개 또는 모호하게 여러 개) `None`을 반환한다(호출자가 `no_xml`로
    격리한다)."""
    folder = Path(docs_dir) / rcept_no
    if not folder.is_dir():
        return None

    consolidated = folder / f"{rcept_no}_00761.xml"
    if consolidated.is_file():
        return consolidated

    audit = folder / f"{rcept_no}_00760.xml"
    if audit.is_file():
        return audit

    embedded = folder / f"{rcept_no}.xml"
    if embedded.is_file():
        return embedded

    xmls = sorted(p for p in folder.glob("*.xml") if p.is_file())
    if len(xmls) == 1:
        return xmls[0]
    return None


# ---------------------------------------------------------------------------
# JSONL 직렬화
# ---------------------------------------------------------------------------


def serialize_parsed_report(parsed: ParsedAuditReport) -> dict[str, Any]:
    """`ParsedAuditReport` -> JSON 직렬화 가능한 dict.

    ⚠️ `parse_flags`(frozenset)는 그대로 두면 JSON으로 직렬화할 수 없으므로
    **정렬된 list**로, `kam_tags`(tuple)는 **list**로 변환한다. 그 외 필드는
    그대로 옮긴다(`None`도 그대로)."""
    return {
        "rcept_no": parsed.rcept_no,
        "corp_code": parsed.corp_code,
        "corp_name": parsed.corp_name,
        "corp_cls": parsed.corp_cls,
        "stock_code": parsed.stock_code,
        "report_name": parsed.report_name,
        "rcept_dt": parsed.rcept_dt,
        "category": parsed.category,
        "fiscal_year": parsed.fiscal_year,
        "settlement_month": parsed.settlement_month,
        "auditor": parsed.auditor,
        "audit_opinion": parsed.audit_opinion,
        "opinion_snippet": parsed.opinion_snippet,
        "going_concern": parsed.going_concern,
        "going_concern_snippet": parsed.going_concern_snippet,
        "kam_present": parsed.kam_present,
        "kam_raw": parsed.kam_raw,
        "emphasis_raw": parsed.emphasis_raw,
        "kam_tags": list(parsed.kam_tags),
        "parse_flags": sorted(parsed.parse_flags),
        "source_url": parsed.source_url,
        "doc_path": parsed.doc_path,
    }


# ---------------------------------------------------------------------------
# 체크포인트 (bulk_audit._load_checkpoint/_save_checkpoint/run-params 가드와 동형)
# ---------------------------------------------------------------------------


def _empty_counts() -> dict[str, Any]:
    return {
        "parsed_ok": 0,
        "failed": 0,
        "by_error_kind": {},
        "opinion_distribution": dict.fromkeys(_OPINION_KEYS, 0),
        "going_concern_true": 0,
        "kam_present": 0,
        "by_corp_cls": {},
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


def _run_params_header(
    *,
    manifest_path: str | Path,
    docs_dir: str | Path,
    corp_cls: set[str] | frozenset[str] | None,
    output_path: str | Path,
) -> dict[str, Any]:
    """체크포인트 헤더에 저장할, JSON 직렬화 가능한 실행 파라미터 스냅샷."""
    return {
        "manifest_path": str(manifest_path),
        "docs_dir": str(docs_dir),
        "corp_cls": sorted(corp_cls) if corp_cls else None,
        "output_path": str(output_path),
    }


def _ensure_run_params_match_checkpoint(state: dict[str, Any], run_params: dict[str, Any]) -> None:
    """`--resume`으로 체크포인트를 이어 쓸 때, 이번 호출의 manifest/docs-dir/
    corp-cls/output이 체크포인트에 기록된 이전 실행과 다르면 명확한 오류를
    낸다(다른 입력/옵션의 진행 상황이 이번 실행과 뒤섞이는 것을 막는다)."""
    existing = state.get("run_params")
    if existing is not None and existing != run_params:
        raise ValueError(
            "체크포인트의 실행 파라미터가 현재 호출과 다릅니다 - 다른 매니페스트/docs-dir/"
            "corp-cls/output으로 같은 체크포인트를 재사용하면 이전 실행의 진행 상황이 이번 "
            f"실행과 맞지 않아 결과가 뒤섞일 수 있습니다. 체크포인트: {existing}, "
            f"현재 호출: {run_params}. --resume 없이 새로 시작하거나 다른 체크포인트 경로를 "
            "사용하세요."
        )
    state["run_params"] = run_params


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------


def _record_failure(counts: dict[str, Any], kind: str, rcept_no: str) -> None:
    counts["failed"] += 1
    counts["by_error_kind"][kind] = counts["by_error_kind"].get(kind, 0) + 1
    samples: list[str] = counts["error_samples"].setdefault(kind, [])
    if len(samples) < _MAX_ERROR_SAMPLES:
        samples.append(rcept_no)


def _record_success(counts: dict[str, Any], parsed: ParsedAuditReport) -> None:
    counts["parsed_ok"] += 1
    opinion = parsed.audit_opinion if parsed.audit_opinion in counts["opinion_distribution"] else "unknown"
    counts["opinion_distribution"][opinion] = counts["opinion_distribution"].get(opinion, 0) + 1
    if parsed.going_concern:
        counts["going_concern_true"] += 1
    if parsed.kam_present:
        counts["kam_present"] += 1
    corp_cls = parsed.corp_cls or "unknown"
    counts["by_corp_cls"][corp_cls] = counts["by_corp_cls"].get(corp_cls, 0) + 1


def _build_summary(total_selected: int, counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_selected": total_selected,
        "parsed_ok": counts["parsed_ok"],
        "failed": counts["failed"],
        "by_error_kind": dict(counts["by_error_kind"]),
        "opinion_distribution": dict(counts["opinion_distribution"]),
        "going_concern_true": counts["going_concern_true"],
        "kam_present": counts["kam_present"],
        "by_corp_cls": dict(counts["by_corp_cls"]),
        "error_samples": {kind: list(samples) for kind, samples in counts["error_samples"].items()},
    }


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------


def _process_one(
    rcept_no: str,
    record: dict[str, Any],
    docs_dir: Path,
    out_f: Any,
    counts: dict[str, Any],
) -> str:
    """rcept 한 건을 처리한다. 어떤 예외가 발생하든(XML 읽기 실패 포함) 여기서
    잡아 `failed`로 집계하고 상태 문자열을 반환한다 - 절대 raise하지
    않는다(호출자가 전체 실행을 계속할 수 있도록)."""
    xml_path = select_xml_path(docs_dir, rcept_no)
    if xml_path is None:
        _record_failure(counts, "no_xml", rcept_no)
        return "no_xml"

    try:
        xml_bytes = xml_path.read_bytes()
        parsed = parse_audit_xml(xml_bytes, meta=record, doc_path=str(xml_path))
        row = serialize_parsed_report(parsed)
        out_f.write(json.dumps(row, ensure_ascii=False))
        out_f.write("\n")
        out_f.flush()
    except Exception as exc:  # noqa: BLE001 - 의도적으로 이 rcept 하나에만 예외를 격리한다.
        kind = type(exc).__name__
        _record_failure(counts, kind, rcept_no)
        return kind

    _record_success(counts, parsed)
    return "ok"


def extract_audit_facts(
    manifest_path: str | Path,
    docs_dir: str | Path,
    output_path: str | Path,
    *,
    resume: bool = False,
    limit: int | None = None,
    corp_cls: set[str] | frozenset[str] | None = None,
    checkpoint: str | Path | None = None,
    summary_path: str | Path | None = None,
    progress_every: int = 25,
) -> dict[str, Any]:
    """`manifest_path`의 각 레코드에 대해 `docs_dir/<rcept_no>/`의 로컬 감사
    XML을 파싱해 `output_path`에 JSONL(1행/1공시)로 append하고, 완료 후
    `summary_path`(기본값: `<output_path>.summary.json`)에 요약 통계를
    원자적으로 쓴다. 반환값은 그 요약 dict과 동일하다.

    체크포인트(기본값: `<output_path>.checkpoint.json`)에 처리한 rcept
    집합과 누적 집계를 저장한다. `resume=True`면 기존 체크포인트를 이어
    쓰고(이미 처리된 rcept는 건너뛰며 output에도 재기록하지 않는다),
    `resume=False`(기본값)면 기존 체크포인트/output 내용을 무시하고 항상
    새로 시작한다.
    """
    manifest_path = Path(manifest_path)
    docs_dir = Path(docs_dir)
    output_path = Path(output_path)
    checkpoint_path = Path(checkpoint) if checkpoint else output_path.with_name(output_path.name + ".checkpoint.json")
    summary_path_resolved = Path(summary_path) if summary_path else output_path.with_name(output_path.name + ".summary.json")

    records = load_records_from_manifest(manifest_path)
    records = _filter_by_corp_cls(records, corp_cls)
    if limit is not None:
        records = records[: max(0, limit)]
    total_selected = len(records)

    state = _load_checkpoint(checkpoint_path) if resume else _empty_state()
    run_params = _run_params_header(
        manifest_path=manifest_path, docs_dir=docs_dir, corp_cls=corp_cls, output_path=output_path
    )
    _ensure_run_params_match_checkpoint(state, run_params)
    _save_checkpoint(checkpoint_path, state)

    mode = "a" if state["processed"] else "w"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, mode, encoding="utf-8") as out_f:
        for i, record in enumerate(records, start=1):
            rcept_no = str(record.get("rcept_no", "") or "").strip()
            if not rcept_no:
                _record_failure(state["counts"], "missing_rcept_no", "<unknown>")
                continue
            if rcept_no in state["processed"]:
                continue

            status = _process_one(rcept_no, record, docs_dir, out_f, state["counts"])
            state["processed"][rcept_no] = status
            _save_checkpoint(checkpoint_path, state)

            if progress_every and i % progress_every == 0:
                logger.info(
                    "진행률: %d/%d (성공 %d, 실패 %d)",
                    i,
                    total_selected,
                    state["counts"]["parsed_ok"],
                    state["counts"]["failed"],
                )

    summary = _build_summary(total_selected, state["counts"])
    _write_json_atomic(summary_path_resolved, summary)
    return summary
