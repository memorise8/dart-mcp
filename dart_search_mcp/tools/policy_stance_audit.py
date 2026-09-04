"""policy_stance.jsonl 의 근거 품질을 기계적으로 감사한다.

policy_stance_gate.py 는 값(value)의 분포·재현·오탐만 본다 — 이 모듈은 evidence 가
① 실제로 원문(rcept_no XML)에 있는지, ② 그 값을 내는 판정 패턴과 맞는지, ③ 표 조각·타
기준서 번호·상한 도달 같은 품질 신호를 갖고 있는지를 행마다 검사한다. 이 모듈은
policy_rules(TOPICS·UNDETERMINED)·extract_facts.select_xml_path·
audit_xml_parser._strip_tags_to_text 외에는 다른 policy_* 모듈에 기대지 않는다(자기완결).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from dart_search_mcp.audit_xml_parser import _strip_tags_to_text
from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED  # noqa: F401 — UNDETERMINED 는 외부에서 참조할 수 있게 재노출
from dart_search_mcp.tools.extract_facts import select_xml_path

# BLOCK_CAP(policy_blocks.py) + evidence_sentence 의 400자 상한(policy_rules.py) — 감사가 자기완결
# 되도록 값만 여기 다시 적고 그 모듈들을 import 하지는 않는다.
_WINDOW = 3000 + 400
_NONE_PREFIX = "판정 불가: "

_WS_RE = re.compile(r"\s+")
_TABLE_FRAG_RE = re.compile(r"\d{1,3}(?:,\d{3}){2,}")
_FOREIGN_STD_RE = re.compile(r"제\s*(\d{4})\s*호")

# 주제 핵심어 정규식 — llm/llm_verified 행은 규칙 패턴이 아니라 이 느슨한 키워드로 근거의
# 관련성만 확인한다(브리프 §R4 지정값 그대로).
_AUDIT_KEYWORDS: dict[str, re.Pattern[str]] = {
    "STD_EARLY_ADOPTION": re.compile(r"조기|적용|도입|채택|시행|영향|검토|평가"),
    "PPE_MEASUREMENT": re.compile(r"(?:원가|재평가)\s*(?:모형|모델)"),
    "REVENUE_METHOD": re.compile(r"진행|투입법|기간에\s*걸쳐|인도|통제|시점"),
    "IP_MEASUREMENT": re.compile(r"(?:원가|공정가치)\s*(?:모형|모델)"),
}

FLAGS: tuple[str, ...] = ("evidence_in_block", "pattern_in_evidence", "foreign_standard", "table_fragment", "capped")
# evidence_in_block·pattern_in_evidence 는 "확인됨"이 정상이라 False 일 때 위반이고,
# foreign_standard·table_fragment·capped 는 그 자체(True)가 이상 징후라 True 일 때 위반이다.
_VIOLATION_WHEN_FALSE = frozenset({"evidence_in_block", "pattern_in_evidence"})


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _evidence_in_block(evidence: str, method: str, text: str | None, block_offset: Any) -> bool | None:
    """text 가 None(=rcept_no 의 XML 을 못 읽음)이면 확인 자체가 불가능하므로 None(검사 제외) —
    호출자가 missing_xml 로 별도 집계하고 위반·종료코드 판정에서 뺀다(Q-I4: `--docs` 오지정이나
    XML 결손을 "근거의 100% 가 블록 밖"으로 잘못 읽는 사고를 막는다). block_offset 이 없거나
    원문 길이를 벗어나면(문서는 있는데 오프셋이 이상함) 위반으로 본다."""
    if text is None:
        return None
    if not isinstance(block_offset, int) or block_offset < 0 or block_offset >= len(text):
        return False
    check = evidence
    if method == "none" and check.startswith(_NONE_PREFIX):
        check = check[len(_NONE_PREFIX) :]
    norm_evidence = _normalize(check)
    if not norm_evidence:
        return True  # 빈 근거는 비교 대상이 없다 — 위반으로 잡지 않는다
    window = text[block_offset : block_offset + _WINDOW]
    return norm_evidence in _normalize(window)


def _value_patterns(spec: Any, value: str) -> list[re.Pattern[str]]:
    return [pat for pat, v in (*spec.negative, *spec.positive, *spec.fallback) if v == value]


def _pattern_in_evidence_rule(topic: str, value: str, evidence: str) -> bool | None:
    """rule 행(콤보값 제외 — 콤보는 _pattern_in_evidence_combo 가 블록 창 전체를 본다): 그 value 를
    내는 패턴(negative∪positive∪fallback) 중 하나라도 evidence 에 매치되면 확인됨."""
    spec = TOPICS.get(topic)
    if spec is None:
        return None
    pats = _value_patterns(spec, value)
    if not pats:
        return None
    return any(p.search(evidence) for p in pats)


def _pattern_in_evidence_combo(topic: str, text: str | None, block_offset: Any) -> bool | None:
    """콤보값(MIXED/BOTH) 행 전용(Q-I3): `policy_rules.judge` 는 블록 전체(최대 3,000자)에서
    서로 다른 긍정값 2개가 잡히면 콤보로 승격하지만, 저장되는 evidence 는 그 중 한 값 주변의
    문장(≤400자)뿐이다 — 그 문장 안에서만 positive 패턴 2개를 요구하면 두 모형 언급이 400자
    이상 떨어져 있을 때 구조적으로 위반이 난다. 그래서 콤보 행은 evidence_in_block 과 같은 창
    (text[block_offset:block_offset+_WINDOW])에서, "패턴"이 아니라 서로 다른 "값"이 2개 이상
    매치되는지를 본다 — judge 의 승격 조건(서로 다른 값 2개)과 의미를 맞춘다."""
    spec = TOPICS.get(topic)
    if spec is None or not spec.combo_value:
        return None
    if text is None:
        return None  # XML 없음 — missing_xml 로 이미 별도 집계, 여기서는 검사 제외
    if not isinstance(block_offset, int) or block_offset < 0 or block_offset >= len(text):
        return False
    window = text[block_offset : block_offset + _WINDOW]
    matched_values = {v for pat, v in spec.positive if pat.search(window)}
    return len(matched_values) >= 2


def _pattern_in_evidence_llm(topic: str, evidence: str) -> bool | None:
    kw = _AUDIT_KEYWORDS.get(topic)
    return bool(kw.search(evidence)) if kw is not None else None


def _foreign_standard(topic: str, subject: str | None, evidence: str) -> bool | None:
    """T1(STD_EARLY_ADOPTION) 행에만 적용 — evidence 에 다른 기준서 번호가 있으면 귀속 오류 후보."""
    if topic != "STD_EARLY_ADOPTION":
        return None
    nums = _FOREIGN_STD_RE.findall(evidence)
    if not nums:
        return False
    return any(n != subject for n in nums)


def _table_fragment(evidence: str) -> bool:
    return len(_TABLE_FRAG_RE.findall(evidence)) >= 2


def _capped(evidence: str) -> bool:
    return len(evidence) >= 400


def _is_combo_row(row: dict[str, Any]) -> bool:
    spec = TOPICS.get(row.get("topic"))
    return bool(spec and spec.combo_value and row.get("method") == "rule" and row.get("value") == spec.combo_value)


def _effective_method(row: dict[str, Any]) -> str:
    """리포트 그룹핑용(Q-I3) — 콤보값 rule 행은 "combo" 로 따로 묶는다. 판정 근거(블록 전체)가
    보통 rule 행(문장 하나)과 다르므로, 같은 (topic, "rule") 그룹에 섞으면 pattern_in_evidence
    위반율을 잘못 읽는다(콤보 비중만큼 그대로 찍혀 "근거가 부실하다"는 잘못된 결론을 낸다)."""
    return "combo" if _is_combo_row(row) else (row.get("method") or "")


def _row_flags(row: dict[str, Any], text: str | None) -> dict[str, bool]:
    evidence = row.get("evidence") or ""
    method = row.get("method") or ""
    topic = row.get("topic")
    subject = row.get("subject")
    value = row.get("value")
    flags: dict[str, bool] = {}
    eib = _evidence_in_block(evidence, method, text, row.get("block_offset"))
    if eib is not None:
        flags["evidence_in_block"] = eib
    if method == "none":
        return flags  # 판단 불가 행은 evidence_in_block 만 본다 — 나머지는 판정 자체가 없어 무의미
    if _is_combo_row(row):
        pie = _pattern_in_evidence_combo(topic, text, row.get("block_offset"))
    elif method == "rule":
        pie = _pattern_in_evidence_rule(topic, value, evidence)
    elif method in ("llm", "llm_verified"):
        pie = _pattern_in_evidence_llm(topic, evidence)
    else:
        pie = None
    if pie is not None:
        flags["pattern_in_evidence"] = pie
    fs = _foreign_standard(topic, subject, evidence)
    if fs is not None:
        flags["foreign_standard"] = fs
    flags["table_fragment"] = _table_fragment(evidence)
    flags["capped"] = _capped(evidence)
    return flags


def run_policy_stance_audit(
    *, stances_path: Path, docs_dir: Path, report_path: Path, sample_per_flag: int = 20
) -> dict[str, Any]:
    stances_path, docs_dir, report_path = Path(stances_path), Path(docs_dir), Path(report_path)
    rows = [json.loads(line) for line in stances_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    text_cache: dict[str, str | None] = {}

    def _doc_text(rcept_no: str) -> str | None:
        if rcept_no not in text_cache:
            xml_path = select_xml_path(docs_dir, rcept_no)
            text_cache[rcept_no] = _strip_tags_to_text(xml_path.read_bytes()) if xml_path is not None else None
        return text_cache[rcept_no]

    group_rows: dict[tuple[str, str], int] = defaultdict(int)
    group_flags: dict[tuple[str, str], dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"checked": 0, "violations": 0}))
    violations: dict[str, list[list[Any]]] = defaultdict(list)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_xml = 0

    for row in rows:
        rcept_no = row["rcept_no"]
        topic = row["topic"]
        text = _doc_text(rcept_no)
        if text is None:
            missing_xml += 1
        flags = _row_flags(row, text)
        key = (topic, _effective_method(row))
        group_rows[key] += 1
        for flag, ok in flags.items():
            is_violation = (not ok) if flag in _VIOLATION_WHEN_FALSE else ok
            fstat = group_flags[key][flag]
            fstat["checked"] += 1
            if is_violation:
                fstat["violations"] += 1
                subject = row.get("subject")
                violations[flag].append([rcept_no, topic, subject])
                if len(samples[flag]) < sample_per_flag:
                    samples[flag].append(
                        {
                            "rcept_no": rcept_no,
                            "topic": topic,
                            "subject": subject,
                            "value": row.get("value"),
                            "evidence_head": (row.get("evidence") or "")[:120],
                        }
                    )

    by_group: dict[str, Any] = {}
    for key in sorted(group_rows):
        topic, method = key
        flags_map = group_flags[key]
        by_group[f"{topic}|{method}"] = {
            "topic": topic,
            "method": method,
            "rows": group_rows[key],
            "flags": {
                flag: {
                    "checked": fs["checked"],
                    "violations": fs["violations"],
                    "ratio": (fs["violations"] / fs["checked"]) if fs["checked"] else 0.0,
                }
                for flag, fs in flags_map.items()
            },
        }

    totals: dict[str, dict[str, Any]] = {}
    for flag in FLAGS:
        checked = sum(g["flags"].get(flag, {}).get("checked", 0) for g in by_group.values())
        viol = sum(g["flags"].get(flag, {}).get("violations", 0) for g in by_group.values())
        totals[flag] = {"checked": checked, "violations": viol, "ratio": (viol / checked) if checked else 0.0}

    result: dict[str, Any] = {
        "rows": len(rows),
        "missing_xml": missing_xml,
        "by_group": by_group,
        "totals": totals,
        "violations": {flag: violations.get(flag, []) for flag in FLAGS},
    }

    _write_report(report_path, result, samples)
    json_path = report_path.with_suffix(".json")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _write_report(report_path: Path, result: dict[str, Any], samples: dict[str, list[dict[str, Any]]]) -> None:
    lines = ["# 회계정책 선택 사실 근거 품질 감사", "", f"행 {result['rows']}건."]
    if result["missing_xml"]:
        lines.append(
            f"XML 없음(rcept_no 의 문서를 못 읽음): {result['missing_xml']}건 — "
            "evidence_in_block 검사·종료코드 판정에서 제외했다(Q-I4)."
        )
    lines += ["", "## 전체 플래그 요약", ""]
    lines += ["| 플래그 | 검사 | 위반 | 비율 |", "|---|---|---|---|"]
    for flag in FLAGS:
        t = result["totals"][flag]
        lines.append(f"| {flag} | {t['checked']} | {t['violations']} | {t['ratio']:.1%} |")

    lines += ["", "## (주제, 판정방식) 별", ""]
    for key, g in sorted(result["by_group"].items()):
        lines += [f"### {g['topic']} / {g['method']} — 행 {g['rows']}건", "", "| 플래그 | 검사 | 위반 | 비율 |", "|---|---|---|---|"]
        for flag in FLAGS:
            fs = g["flags"].get(flag)
            if fs is None:
                continue
            lines.append(f"| {flag} | {fs['checked']} | {fs['violations']} | {fs['ratio']:.1%} |")
        lines.append("")

    lines += ["## 플래그별 위반 예시", ""]
    for flag in FLAGS:
        total_viol = result["totals"][flag]["violations"]
        ex = samples.get(flag, [])
        lines.append(f"### {flag} ({len(ex)}건 표시 / 전체 위반 {total_viol}건)")
        if not ex:
            lines += ["", "- (위반 없음)", ""]
            continue
        lines += ["", "| rcept_no | topic | subject | value | evidence(앞 120자) |", "|---|---|---|---|---|"]
        for s in ex:
            lines.append(f"| {s['rcept_no']} | {s['topic']} | {s['subject']} | {s['value']} | {s['evidence_head']} |")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
