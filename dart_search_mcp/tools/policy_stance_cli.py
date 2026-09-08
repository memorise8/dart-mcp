"""원문 XML → 회계정책 선택 사실(policy_stance.jsonl).

KAM 분할(split_kam_cli.py)과 같은 인계 방식: 수집기가 해석하고 TEMIS 는 rcept_no 로 조인해 적재한다.
한 (rcept_no, topic, subject) 에 행은 하나 — 같은 기준서 블록이 여러 개면 확신 높은 값을 남긴다.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dart_search_mcp.audit_xml_parser import _strip_tags_to_text
from dart_search_mcp.policy_blocks import PolicyBlock, split_policy_blocks
from dart_search_mcp.policy_llm import DEFAULT_BASE_URL, DEFAULT_MODEL, judge_with_llm
from dart_search_mcp.policy_rules import HELD_CONFLICT, TOPICS, UNDETERMINED, Judgement, judge_all
from dart_search_mcp.tools.extract_facts import select_xml_path
from dart_search_mcp.tools.kam_tagger import CallFn, call_llm

PARSER_VERSION = "ps-v2"
_CONFIDENCE = {"rule": 1.0, "llm_verified": 0.9, "llm": 0.6, "none": 0.0}
_RANK = {"rule": 3, "llm_verified": 2, "llm": 1, "none": 0}
# 판단 보류 행의 근거 접두어 — `_record` 와 `policy_stance_audit._NONE_PREFIX` 가 쓰는 것과 같다.
_NONE_PREFIX = "판정 불가: "


class PolicyStanceSourceError(Exception):
    pass


def _read_facts(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_atomic(output_path: Path, records: Any) -> None:
    tmp = output_path.with_name(output_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in sorted(records, key=lambda r: (r["rcept_no"], r["topic"], r["subject"] or "")):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)


def _more_informative(j: Judgement, prev: dict[str, Any]) -> bool:
    """같은 `_RANK` 안에서의 동점 처리 — 값은 그대로 두고 **보류 사유가 있는 쪽**을 남긴다.

    T5 실측: 한 보고서에 유형자산 블록이 여러 개일 때(재무상태표 줄·주석 표·정책 절), 판단
    보류 행은 모두 method="none"(rank 0) 이라 **먼저 나온 블록**이 이겼다 — 그 결과 R1 이
    붙인 CONDITIONAL_ONLY 가 앞쪽 표 블록의 빈 보류 사유에 덮여 출력에서 0건이 됐다
    (1,486 보고서 표본: 블록 단위 5건 → 행 단위 0건). 값 우선순위는 건드리지 않는다."""
    return (
        _RANK[j.method] == _RANK[prev["method"]]
        and bool(j.held_reason)
        and not prev.get("held_reason")
    )


def resolve_cross_block_conflicts(
    by_key: dict[tuple[str, str, str | None], dict[str, Any]],
    seen_values: dict[tuple[str, str], set[str]],
) -> tuple[int, int]:
    """한 보고서 안에서 값이 갈릴 때 회사 수준 행(subject None)을 정리한다. (조합 승격, 충돌 보류).

    Fix round 1(리뷰 I1): 블록 단위 조합 승격(`policy_rules.judge_all`)만으로는 **블록이 여럿인**
    보고서를 못 다룬다 — 「(4) 유형자산 …그 외의 모든 유형자산은 취득원가에서 …」(원가)와
    「(5) 유형자산 …토지에 대하여 재평가모형을 …」(재평가)이 다른 블록에 있으면 dedup 이 먼저
    나온 블록을 남겨 COST_MODEL 로 확정됐다(실측 6건). 더 나쁜 것은 같은 파일 안에서 회사 수준
    행이 자기 자산군 행과 모순되는 경우다(20260403000370: {None: COST, 토지: REVALUATION}).

    처리는 주제 어휘에 조합값이 있는지로 갈린다.
    - 조합값이 있으면(PPE=MIXED, REVENUE=BOTH) 회사 수준 행을 그 값으로 올린다. Fix round 2
      (리뷰 b): 값만 바꾸면 안 된다 — 감사(`policy_stance_audit._is_combo_row`)가 `method=="rule"`
      로 콤보 행을 갈라내므로, 블록 안 승격 경로(`policy_rules.judge_all`)와 똑같이
      method·confidence·held_reason 까지 맞춘다.
    - 조합값이 없으면(IP — 어휘가 COST/FAIR_VALUE/UNDETERMINED 로 닫혀 있다) 없는 값을 지어내지
      않는다. Fix round 2(리뷰 c, 컨트롤러 판정): 판단 보류로 내리고 사유를 CONFLICT 로 남긴다.

    근거는 두 경로 모두 먼저 나온 블록의 것을 그대로 둔다(보류 행은 `_record` 와 같은 접두어를
    붙인다) — "어느 문장에서 시작된 판단인가"를 잃지 않기 위해서다. 자산군 행은 손대지 않는다.
    """
    promoted = 0
    conflicts = 0
    for (rcept_no, code), values in seen_values.items():
        spec = TOPICS.get(code)
        if spec is None or len(values) < 2:
            continue
        rec = by_key.get((rcept_no, code, None))
        if rec is None or rec["value"] == UNDETERMINED:
            continue
        if spec.combo_value:
            if rec["value"] == spec.combo_value:
                continue
            rec["value"] = spec.combo_value
            rec["method"] = "rule"
            rec["confidence"] = _CONFIDENCE["rule"]
            rec["held_reason"] = ""
            promoted += 1
        else:
            rec["evidence"] = _NONE_PREFIX + rec["evidence"]
            rec["value"] = UNDETERMINED
            rec["method"] = "none"
            rec["confidence"] = _CONFIDENCE["none"]
            rec["held_reason"] = HELD_CONFLICT
            conflicts += 1
    return promoted, conflicts


def _record(fact: dict[str, Any], j: Judgement, block: PolicyBlock) -> dict[str, Any]:
    """UNDETERMINED 도 행으로 남긴다 — 분포 고지에 '판단불가 N' 을 보이기 위해. 근거 자리엔 블록 첫 120자.

    ps-v2(T4): 근거 메타 5열을 덧붙인다. 기존 열의 이름·의미·값 코드는 그대로다 — 소비자(TEMIS
    적재)는 새 열을 몰라도 되고, 알면 "이 근거가 누구의 어떤 문장인가"와 "왜 보류인가"를 읽을
    수 있다."""
    fy = fact.get("fiscal_year")
    evidence = j.evidence if j.value != UNDETERMINED else _NONE_PREFIX + block.text[:120]
    return {
        "rcept_no": fact["rcept_no"],
        "corp_code": fact.get("corp_code"),
        "fiscal_year": int(fy) if fy not in (None, "") else None,
        "topic": j.topic,
        "subject": j.subject,
        "value": j.value,
        "evidence": evidence,
        "method": j.method,
        "confidence": _CONFIDENCE[j.method],
        "block_offset": block.start,
        "sentence_kind": j.sentence_kind,
        "sentence_subject": j.sentence_subject,
        "held_reason": j.held_reason,
        "evidence_heading_path": j.evidence_heading_path,
        "is_conditional": j.is_conditional,
        "parser_version": PARSER_VERSION,
    }


def run_extract_policy_stances(
    *,
    facts_path: Path,
    docs_dir: Path,
    output_path: Path,
    topics: tuple[str, ...],
    use_llm: bool,
    limit: int | None = None,
    call_fn: CallFn = call_llm,
    llm_subjects: frozenset[str] | None = None,
    llm_workers: int = 1,
    llm_model: str = DEFAULT_MODEL,
    llm_base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    """`llm_subjects` 가 주어지면 UNDETERMINED 블록 중 `j.subject` 가 그 집합에 속한 것만 LLM 에 보낸다.
    subject 가 없는 주제(PPE_MEASUREMENT·REVENUE_METHOD·IP_MEASUREMENT — subject_of 가 항상 None) 는
    `j.subject`(=None) 가 그 집합에 속할 수 없으므로, `llm_subjects` 를 지정하면 이 주제들은 LLM 판정에서
    통째로 제외된다. 의도된 동작이다 — 이 주제들은 UNDETERMINED 가 수만 건 규모라 전량 LLM 판정이
    비현실적이고, subject 단위로 좁힐 대상 축도 없다."""
    facts_path, docs_dir, output_path = Path(facts_path), Path(docs_dir), Path(output_path)
    if output_path.resolve() == facts_path.resolve():
        raise PolicyStanceSourceError("출력 경로가 입력 facts 와 같다")
    specs = [TOPICS[t] for t in topics]
    facts = _read_facts(facts_path)
    if limit is not None:
        facts = facts[:limit]
    by_key: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    by_topic: dict[str, Counter[str]] = defaultdict(Counter)
    capped: dict[str, list[int]] = defaultdict(list)
    # 규칙 판정은 먼저 순차로 전부 끝낸다. UNDETERMINED 이고 LLM 대상(llm_subjects)인 블록만
    # (fact, spec, block, key) 로 모아 두었다가 아래에서 한 번에(순차 또는 병렬) 판정한다.
    pending: list[tuple[dict[str, Any], Any, PolicyBlock, tuple[str, str, str | None]]] = []
    # I1: (rcept_no, topic) 별로 이 보고서에서 나온 확정값 전부(회사 수준 행 + 자산군 행).
    seen_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    reports = 0
    skipped_no_xml = 0
    started = time.time()
    for fact in facts:
        xml_path = select_xml_path(docs_dir, fact["rcept_no"])
        if xml_path is None:
            skipped_no_xml += 1
            continue
        reports += 1
        text = _strip_tags_to_text(xml_path.read_bytes())
        for block in split_policy_blocks(text):
            for spec in specs:
                if not spec.applies(block):
                    continue
                # T5: 블록 하나가 여러 행을 낼 수 있다(유형자산 자산군별 행). 상한 도달률은
                # 여전히 블록당 한 번만 센다 — 행 수가 아니라 블록 수의 비율이기 때문이다.
                capped[spec.code].append(1 if block.capped else 0)
                # I1: 회사 수준 행(subject None)이 있는 주제만 모은다 — STD_EARLY_ADOPTION 은
                # subject 가 기준서 번호라 회사 수준 행이 없고, 기준서끼리 값이 다른 것은 충돌이
                # 아니다.
                has_company_row = spec.subject_of(block) is None
                for j in judge_all(spec, block):
                    key = (fact["rcept_no"], spec.code, j.subject)
                    if has_company_row and j.value != UNDETERMINED:
                        # 블록·자산군을 가로질러 어떤 확정값들이 나왔는지 모은다(dedup 이 한 행만
                        # 남기므로 여기서 기록해 두지 않으면 나중에 알 수 없다).
                        seen_values[(fact["rcept_no"], spec.code)].add(j.value)
                    if j.value == UNDETERMINED and use_llm and (llm_subjects is None or j.subject in llm_subjects):
                        pending.append((fact, spec, block, key))
                    prev = by_key.get(key)
                    if prev is None or _RANK[j.method] > _RANK[prev["method"]] or _more_informative(j, prev):
                        by_key[key] = _record(fact, j, block)
    # V6 R5: 블록 루프가 끝나 by_key 가 전부 확정된 뒤 pending 을 필터링한다. 같은 키에 블록이
    # 여러 개일 때, 다른 블록이 이미 규칙으로 판정해 by_key[key]["value"] != UNDETERMINED 이면
    # (rank 비교상 그 값이 최종값이므로) 이 UNDETERMINED 블록을 LLM 에 보내도 결과에 반영되지
    # 않는다 — 제거한다. 같은 키에 UNDETERMINED 블록이 여럿 남으면(다른 블록도 전부 규칙 판정
    # 실패) 가장 긴 블록 하나만 남긴다 — 문맥이 더 많은 블록이 LLM 판정에 유리하고, 나머지는
    # 중복 호출일 뿐이다.
    llm_pending_raw = len(pending)
    if pending:
        best: dict[
            tuple[str, str, str | None], tuple[dict[str, Any], Any, PolicyBlock, tuple[str, str, str | None]]
        ] = {}
        for item in pending:
            _fact, _spec, block, key = item
            if by_key[key]["value"] != UNDETERMINED:
                continue
            cur = best.get(key)
            if cur is None or len(block.text) > len(cur[2].text):
                best[key] = item
        pending = list(best.values())
    # I1: 보고서 단위 조합값 승격은 dedup 이 끝난 뒤에 한 번 한다(블록 루프 안에서는 아직 어느
    # 행이 최종인지 모른다). LLM 판정 뒤에 한 번 더 부른다 — 그때 값이 바뀔 수 있기 때문이다.
    combo_promoted, combo_conflicts = resolve_cross_block_conflicts(by_key, seen_values)
    # LLM 단계 진입 전, 규칙만으로 낸 결과를 먼저 원자적으로 써 둔다 — LLM 단계(게이트웨이 호출)가
    # 프로세스를 통째로 죽이는 사고(OOM·강제종료 등)를 내더라도 이 파일은 남는다. 최종 결과가 덮어쓴다.
    _write_atomic(output_path, by_key.values())
    llm_failures = 0
    llm_gateway = 0
    llm_parse = 0
    llm_unanchored = 0
    llm_unverified = 0
    llm_unsupported = 0
    if pending:
        def _judge_one(
            item: tuple[dict[str, Any], Any, PolicyBlock, tuple[str, str, str | None]]
        ) -> tuple[Judgement, bool, str | None]:
            """개별 LLM 호출 실패(타임아웃·게이트웨이 오류 등 예상 밖 예외 포함)가 전체 실행을
            멈추지 않게 격리한다 — UNDETERMINED/"none" 으로 낮추고 실패로 센다. 예외 없이 끝난
            호출은 judge_with_llm 이 trace 에 남긴 사유("gateway"/"parse"/"unanchored"/
            "unverified"/"ok")를 그대로 돌려준다(S-I1/Q-I1 — 다섯 실패 경로를 구분해서 센다)."""
            _, spec, block, _ = item
            tr: dict[str, str] = {}
            try:
                j = judge_with_llm(spec, block, model=llm_model, base_url=llm_base_url, call_fn=call_fn, trace=tr)
                return j, False, tr.get("reason")
            except Exception:
                subject = spec.subject_of(block)
                return Judgement(spec.code, subject, UNDETERMINED, "", "none"), True, None

        if llm_workers <= 1:
            results = [_judge_one(item) for item in pending]
        else:
            with ThreadPoolExecutor(max_workers=llm_workers) as executor:
                results = list(executor.map(_judge_one, pending))
        llm_failures = sum(1 for _, failed, _reason in results if failed)
        reason_counts = Counter(reason for _, failed, reason in results if not failed and reason is not None)
        llm_gateway = reason_counts.get("gateway", 0)
        llm_parse = reason_counts.get("parse", 0)
        llm_unanchored = reason_counts.get("unanchored", 0)
        llm_unverified = reason_counts.get("unverified", 0)
        llm_unsupported = reason_counts.get("unsupported", 0)
        for (fact, spec, block, key), (j, _failed, _reason) in zip(pending, results):
            prev = by_key.get(key)
            if prev is None or _RANK[j.method] > _RANK[prev["method"]]:
                by_key[key] = _record(fact, j, block)
                if spec.subject_of(block) is None and j.value != UNDETERMINED:
                    seen_values[(fact["rcept_no"], spec.code)].add(j.value)
        more_promoted, more_conflicts = resolve_cross_block_conflicts(by_key, seen_values)
        combo_promoted += more_promoted
        combo_conflicts += more_conflicts
    llm_calls = len(pending)
    for rec in by_key.values():
        by_topic[rec["topic"]][rec["value"]] += 1
    _write_atomic(output_path, by_key.values())
    summary = {
        "facts": len(facts),
        "reports": reports,
        "skipped_no_xml": skipped_no_xml,
        "rows": len(by_key),
        "cross_block_combo_promoted": combo_promoted,
        "cross_block_conflict_held": combo_conflicts,
        "by_topic": {t: dict(c) for t, c in by_topic.items()},
        "capped_ratio": {t: (sum(v) / len(v) if v else 0.0) for t, v in capped.items()},
        "llm_pending_raw": llm_pending_raw,
        "llm_calls": llm_calls,
        "llm_failures": llm_failures,
        "llm_gateway": llm_gateway,
        "llm_parse": llm_parse,
        "llm_unanchored": llm_unanchored,
        "llm_unverified": llm_unverified,
        "llm_unsupported": llm_unsupported,
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
        "llm_subjects": sorted(llm_subjects) if llm_subjects is not None else None,
        "llm_subjects_note": (
            "subject 없는 주제(PPE_MEASUREMENT 등)는 --llm-subjects 지정 시 LLM 판정에서 제외된다"
            if llm_subjects is not None
            else None
        ),
        "llm_workers": llm_workers,
        "parser_version": PARSER_VERSION,
        "elapsed_sec": round(time.time() - started, 1),
    }
    summary_path = output_path.with_name(output_path.name + ".summary.json")
    summary_tmp = summary_path.with_name(summary_path.name + ".tmp")
    summary_tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary_tmp.replace(summary_path)
    return summary
