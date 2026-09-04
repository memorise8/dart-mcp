"""원문 XML → 회계정책 선택 사실(policy_stance.jsonl).

KAM 분할(split_kam_cli.py)과 같은 인계 방식: 수집기가 해석하고 TEMIS 는 rcept_no 로 조인해 적재한다.
한 (rcept_no, topic, subject) 에 행은 하나 — 같은 기준서 블록이 여러 개면 확신 높은 값을 남긴다.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dart_search_mcp.audit_xml_parser import _strip_tags_to_text
from dart_search_mcp.policy_blocks import split_policy_blocks
from dart_search_mcp.policy_llm import judge_with_llm
from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED, Judgement, judge
from dart_search_mcp.tools.extract_facts import select_xml_path
from dart_search_mcp.tools.kam_tagger import CallFn, call_llm

PARSER_VERSION = "ps-v1"
_CONFIDENCE = {"rule": 1.0, "llm_verified": 0.9, "llm": 0.6, "none": 0.0}
_RANK = {"rule": 3, "llm_verified": 2, "llm": 1, "none": 0}


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


def _record(fact: dict[str, Any], j: Judgement, block: PolicyBlock) -> dict[str, Any]:
    """UNDETERMINED 도 행으로 남긴다 — 분포 고지에 '판단불가 N' 을 보이기 위해. 근거 자리엔 블록 첫 120자."""
    fy = fact.get("fiscal_year")
    evidence = j.evidence if j.value != UNDETERMINED else "판정 불가: " + block.text[:120]
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
) -> dict[str, Any]:
    facts_path, docs_dir, output_path = Path(facts_path), Path(docs_dir), Path(output_path)
    if output_path.resolve() == facts_path.resolve():
        raise PolicyStanceSourceError("출력 경로가 입력 facts 와 같다")
    specs = [TOPICS[t] for t in topics]
    facts = _read_facts(facts_path)[: limit or None]
    by_key: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    by_topic: dict[str, Counter[str]] = defaultdict(Counter)
    capped: dict[str, list[int]] = defaultdict(list)
    llm_calls = 0
    reports = 0
    started = time.time()
    for fact in facts:
        xml_path = select_xml_path(docs_dir, fact["rcept_no"])
        if xml_path is None:
            continue
        reports += 1
        text = _strip_tags_to_text(xml_path.read_bytes())
        for block in split_policy_blocks(text):
            for spec in specs:
                if not spec.applies(block):
                    continue
                j = judge(spec, block)
                if j.value == UNDETERMINED and use_llm:
                    llm_calls += 1
                    j = judge_with_llm(spec, block, call_fn=call_fn)
                capped[spec.code].append(1 if block.capped else 0)
                key = (fact["rcept_no"], spec.code, j.subject)
                prev = by_key.get(key)
                if prev is None or _RANK[j.method] > _RANK[prev["method"]]:
                    by_key[key] = _record(fact, j, block)
    for rec in by_key.values():
        by_topic[rec["topic"]][rec["value"]] += 1
    tmp = output_path.with_name(output_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in by_key.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)
    summary = {
        "reports": reports,
        "rows": len(by_key),
        "by_topic": {t: dict(c) for t, c in by_topic.items()},
        "capped_ratio": {t: (sum(v) / len(v) if v else 0.0) for t, v in capped.items()},
        "llm_calls": llm_calls,
        "parser_version": PARSER_VERSION,
        "elapsed_sec": round(time.time() - started, 1),
    }
    output_path.with_name(output_path.name + ".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return summary
