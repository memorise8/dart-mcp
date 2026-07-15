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
- **크래시 후 resume 시 JSONL 자가복구(멱등화):** JSONL append는 그 자체로
  멱등이 아니다 - 한 rcept 처리 후 줄을 flush했지만 호출부가 체크포인트를
  저장하기 **전에** 크래시하면, resume 시 그 rcept가 checkpoint의
  `processed`엔 없어 재처리될 수 있어 중복행이 생길 수 있고, 줄을 쓰는
  도중 크래시하면 torn(부분) 마지막 줄이 남을 수도 있다. 그래서 "정상
  종료 시 무중복"만 보장하는 게 아니라, "체크포인트가 진실원천"이라고
  단정하지도 않는다 - 대신 `resume=True`로 시작할 때마다(신규 실행에선
  스캔이 불필요하므로 건너뛴다) 기존 output JSONL을 한 번 스캔해
  `json.loads` 성공 + `rcept_no` 존재하는 유효 라인만 남기고(torn 마지막
  라인은 버려진다, 같은 rcept_no가 중복으로 있었다면 하나로 합친다) 그
  복구된 내용으로 파일을 tmp+replace로 원자적으로 재작성한다. 이 스캔에서
  얻은 rcept_no 집합을 checkpoint의 `processed`/`counts`에 보강해(JSONL을
  보조 진실원천으로 삼는다) 물리적으로 이미 있는 rcept는 재기록/재계상하지
  않는다. 결과적으로 크래시 후 resume해도 중복행 0, torn 라인 제거가
  보장된다(정상 종료 경로는 원래대로 동작한다).
- **원자적 쓰기:** 체크포인트와 summary JSON은 tmp 파일에 쓴 뒤
  `Path.replace`로 원자적으로 교체한다(`dart_search_mcp.collect`의 원자적
  쓰기 원본 패턴과 동일). **JSONL 출력 자체는** 매 rcept 처리 시 한 줄씩
  append한다 - 48k건 규모에서 매 줄마다 전체 파일을 tmp+replace로 다시
  쓰는 것은 비현실적이다. 대신 위 자가복구 단계가 resume 시 한 번만 전체를
  재작성해 클린 상태로 만들고, 그 이후 append는 그 클린 상태 위에
  이어붙는다.

이 모듈은 어떤 MCP 도구도 등록하지 않는다(대량 처리는 블로킹 MCP 도구
호출로 적합하지 않다) - `cli.py`의 `dart extract-audit-facts` 명령을
통해서만 제공한다.

## finalize: facts.jsonl -> temis DartTopicCase JSON

`deserialize_parsed_report`는 `serialize_parsed_report`의 역함수로, JSONL 한
행(dict)을 `ParsedAuditReport`로 복원한다. `emit_topic_cases_from_facts`는
완결된 `audit_facts.jsonl`을 (in-memory 파싱 결과가 아니라) **다시 읽어**
기존 어댑터(`dart_search_mcp.audit_facts_adapter.parsed_reports_to_topic_cases`,
Task 2)와 변환기(`dart_search_mcp.temis_export.topic_cases_to_json`, Task 6)를
재사용해 temis `DartTopicCase` JSON 배열을 만든다 - `cli.py`의
`--emit-topic-cases` 옵션이 이 함수를 호출한다.
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


def _none_or_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def deserialize_parsed_report(row: dict[str, Any]) -> ParsedAuditReport:
    """`serialize_parsed_report`의 역함수: JSONL 한 행(dict) -> `ParsedAuditReport`.

    - `parse_flags`: list -> frozenset. `kam_tags`: list -> tuple.
    - `fiscal_year`/`settlement_month`: null -> None, 그 외 값은 int로 강제
      변환한다(변환 불가 시 `ValueError`/`TypeError`를 그대로 전파한다 -
      호출자인 `emit_topic_cases_from_facts`가 그 라인 전체를 손상 라인으로
      다뤄 건너뛴다).
    - 나머지 필드는 그대로 옮기되, 누락된 키에는 방어적 기본값(빈 문자열/
      False/빈 컬렉션)을 쓴다 - 이 모듈이 직접 만든 손상 없는 JSONL에는 전
      필드가 항상 존재하므로 실전에서는 기본값이 쓰일 일이 거의 없다."""
    return ParsedAuditReport(
        rcept_no=str(row.get("rcept_no", "") or ""),
        corp_code=str(row.get("corp_code", "") or ""),
        corp_name=str(row.get("corp_name", "") or ""),
        corp_cls=str(row.get("corp_cls", "") or ""),
        stock_code=str(row.get("stock_code", "") or ""),
        report_name=str(row.get("report_name", "") or ""),
        rcept_dt=str(row.get("rcept_dt", "") or ""),
        category=str(row.get("category", "") or ""),
        fiscal_year=_none_or_int(row.get("fiscal_year")),
        settlement_month=_none_or_int(row.get("settlement_month")),
        auditor=str(row.get("auditor", "") or ""),
        audit_opinion=str(row.get("audit_opinion", "") or "unknown"),
        opinion_snippet=str(row.get("opinion_snippet", "") or ""),
        going_concern=bool(row.get("going_concern", False)),
        going_concern_snippet=str(row.get("going_concern_snippet", "") or ""),
        kam_present=bool(row.get("kam_present", False)),
        kam_raw=str(row.get("kam_raw", "") or ""),
        emphasis_raw=str(row.get("emphasis_raw", "") or ""),
        kam_tags=tuple(row.get("kam_tags") or ()),
        parse_flags=frozenset(row.get("parse_flags") or ()),
        source_url=str(row.get("source_url", "") or ""),
        doc_path=str(row.get("doc_path", "") or ""),
    )


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
# resume 시 JSONL 자가복구(멱등화)
# ---------------------------------------------------------------------------


def _scan_and_repair_output(output_path: Path) -> dict[str, dict[str, Any]]:
    """resume 시작 시 기존 output JSONL을 한 번 스캔해 자가복구한다.

    라인 단위로 읽어 `json.loads` 성공 + `rcept_no` 필드가 있는 **유효
    라인만** 남긴다(마지막 라인이 torn/파싱 불가면 다른 무효 라인과 함께
    버려진다 - 잘려나간다). 같은 rcept_no가 여러 번 나오면(과거 크래시로
    이미 중복행이 생겼던 경우) 가장 나중에 나온 라인 값으로 병합해 한 번만
    남긴다. 복구된 내용으로 파일을 tmp+replace로 원자적으로 재작성한 뒤
    (그 이후 append가 이어질 클린 상태를 만든다), `rcept_no -> row` 매핑을
    반환한다 - 호출자가 체크포인트의 `processed`/`counts`를 이 매핑으로
    보강해, 물리적으로 이미 JSONL에 있는 rcept의 재기록/재계상을 막는다.

    출력 파일이 아직 없으면(신규 실행 등) 아무 것도 하지 않고 빈 dict를
    반환한다."""
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
                # torn(부분) 라인 등 파싱 불가 - 버린다(잘라낸다).
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


def _record_success(
    counts: dict[str, Any],
    *,
    audit_opinion: str | None,
    going_concern: bool,
    kam_present: bool,
    corp_cls: str | None,
) -> None:
    """성공 집계 1건을 반영한다.

    개별 필드(전체 `ParsedAuditReport`가 아니라)를 받는 이유: 정상 처리
    경로(`_process_one`)뿐 아니라, resume 시 JSONL 자가복구로 이미
    직렬화돼 있던 row(dict)로부터도 동일하게 재계상할 수 있어야 하기
    때문이다(둘 다 이 함수를 통해 같은 로직을 공유한다)."""
    counts["parsed_ok"] += 1
    opinion = audit_opinion if audit_opinion in counts["opinion_distribution"] else "unknown"
    counts["opinion_distribution"][opinion] = counts["opinion_distribution"].get(opinion, 0) + 1
    if going_concern:
        counts["going_concern_true"] += 1
    if kam_present:
        counts["kam_present"] += 1
    resolved_corp_cls = corp_cls or "unknown"
    counts["by_corp_cls"][resolved_corp_cls] = counts["by_corp_cls"].get(resolved_corp_cls, 0) + 1


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

    _record_success(
        counts,
        audit_opinion=parsed.audit_opinion,
        going_concern=parsed.going_concern,
        kam_present=parsed.kam_present,
        corp_cls=parsed.corp_cls,
    )
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
    집합과 누적 집계를 저장한다. `resume=True`면 이어 쓰기 전에 먼저 기존
    output JSONL을 스캔해 중복/torn(부분) 마지막 라인을 제거하고 원자적으로
    재작성한다(크래시 후 resume에도 자가 복구) - 그렇게 확인된 rcept는
    checkpoint에 없더라도 재기록/재계상하지 않는다. `resume=False`
    (기본값)면 기존 체크포인트/output 내용을 무시하고 항상 새로 시작한다.
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

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if resume:
        # 크래시 후 resume: JSONL을 스캔·복구하고, 복구된(=물리적으로 이미
        # 있는) rcept 중 checkpoint의 processed에는 아직 없는 것들을 여기서
        # 보강한다 - 이렇게 하면 아래 루프의 기존 `processed` 체크만으로도
        # 재기록/재처리가 자동으로 건너뛰어진다.
        recovered = _scan_and_repair_output(output_path)
        for rcept_no, row in recovered.items():
            if rcept_no in state["processed"]:
                continue
            state["processed"][rcept_no] = "ok"
            _record_success(
                state["counts"],
                audit_opinion=row.get("audit_opinion"),
                going_concern=bool(row.get("going_concern")),
                kam_present=bool(row.get("kam_present")),
                corp_cls=row.get("corp_cls"),
            )
        _save_checkpoint(checkpoint_path, state)

    mode = "a" if resume else "w"

    with open(output_path, mode, encoding="utf-8") as out_f:
        for i, record in enumerate(records, start=1):
            rcept_no = str(record.get("rcept_no", "") or "").strip()
            if not rcept_no:
                # 인덱스 기반 합성 키로 한 번만 집계한다(resume마다 재계상 방지).
                synthetic_key = f"__norcept_{i}"
                if synthetic_key in state["processed"]:
                    continue
                _record_failure(state["counts"], "missing_rcept_no", "<unknown>")
                state["processed"][synthetic_key] = "missing_rcept_no"
                _save_checkpoint(checkpoint_path, state)
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


# ---------------------------------------------------------------------------
# topic_cases 산출 (facts.jsonl -> temis DartTopicCase JSON, finalize 단계)
# ---------------------------------------------------------------------------


def emit_topic_cases_from_facts(
    facts_path: str | Path,
    output_path: str | Path,
    *,
    freshness_timestamp: str,
) -> dict[str, Any]:
    """완결된 사실 JSONL(`extract_audit_facts`의 output)을 다시 읽어 temis
    `DartTopicCase` JSON 배열(`dart_search_mcp.temis_export.topic_cases_to_json`)로
    변환해 `output_path`에 원자적으로(tmp+replace) 쓴다.

    설계 원칙: in-memory로 만들어진 파싱 결과가 아니라 디스크의 **완결된
    JSONL을 다시 읽어** 생성한다 - resume/크래시로 여러 번 실행돼도 항상
    호출 시점의 전체 facts 기준으로 정확히 재생성된다(누적 스트림 상태에
    의존하지 않는다).

    - 각 라인을 `json.loads` -> `deserialize_parsed_report`로 복원한다.
      dict가 아니거나 복원에 실패하는 라인(JSON 손상, 숫자로 변환할 수
      없는 fiscal_year 등)은 그 라인만 건너뛰고 `corrupt_lines`에 집계한다
      - 전체 실행을 멈추지 않는다.
    - 유효하게 복원된 사실들은 `dart_search_mcp.audit_facts_adapter.
      parsed_reports_to_topic_cases`(Task 2 어댑터)에 그대로 넘긴다. 빈
      rcept_no/corp_code이거나 fiscal_year를 정수로 해석할 수 없는 사실
      (예: fiscal_year가 null인 행)은 그 안에서 `TopicCaseSkipped`로
      집계되어 반환되는 `skipped`/`skipped_reasons_count`에 반영된다. KAM이
      없는 사실도(핵심감사사항 섹션이 아예 없는 비상장 "단순 적정의견"
      다수) topic 키워드 매칭 없이 `topic_slug="general"`인 케이스 1건으로
      정상 생성된다(어댑터가 이미 보장하는 동작).
    - 순수성: 이 함수는 시계를 직접 읽지 않는다 - `freshness_timestamp`는
      호출자(CLI)가 주입한 값을 그대로 전달할 뿐이다.

    반환값: `{"facts_rows": ..., "topic_cases": ..., "skipped": ...,
    "skipped_reasons_count": {...}, "corrupt_lines": ...}`.
    """
    # 지연 import: 모듈 최상단에서 import하면 `dart_search_mcp.tools.reports`의
    # `@mcp.tool()` 등록이 `server.py`가 정한 순서보다 먼저 실행될 위험이
    # 있어 MCP 도구 등록 순서(`tests/test_public_surface.py`)가 바뀔 수
    # 있다(`dart_search_mcp.tools.disclosures._resolve_single_corp_code`와
    # 동일한 이유). 이 모듈(`extract_facts.py`)은 어떤 MCP 도구도 등록하지
    # 않는 순수 동기 CLI 전용 모듈이므로, 이 함수가 실제로 호출될 때만
    # 어댑터/변환기를 가져온다.
    from dart_search_mcp.audit_facts_adapter import parsed_reports_to_topic_cases
    from dart_search_mcp.temis_export import topic_cases_to_json

    facts_path = Path(facts_path)
    output_path = Path(output_path)

    parsed_list: list[ParsedAuditReport] = []
    corrupt_lines = 0

    with open(facts_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
                if not isinstance(row, dict):
                    raise ValueError("JSONL 행이 객체(dict)가 아닙니다")
                parsed_list.append(deserialize_parsed_report(row))
            except (json.JSONDecodeError, ValueError, TypeError):
                # 손상된(파싱 불가/필드 변환 불가) 라인 - 건너뛰고 집계만 한다.
                corrupt_lines += 1

    records, skipped = parsed_reports_to_topic_cases(parsed_list, freshness_timestamp=freshness_timestamp)

    raw_json = topic_cases_to_json(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    tmp_path.write_text(raw_json, encoding="utf-8")
    tmp_path.replace(output_path)

    skipped_reasons_count: dict[str, int] = {}
    for item in skipped:
        skipped_reasons_count[item.reason] = skipped_reasons_count.get(item.reason, 0) + 1

    return {
        "facts_rows": len(parsed_list),
        "topic_cases": len(records),
        "skipped": len(skipped),
        "skipped_reasons_count": skipped_reasons_count,
        "corrupt_lines": corrupt_lines,
    }
