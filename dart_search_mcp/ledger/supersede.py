"""정정 계보 — 같은 회사·같은 보고서의 옛 접수본 판정 행을 뺀다(K2-1, 10-27).

DART 정정 공시(「[기재정정]」 「[첨부정정]」 「[첨부추가]」 사업보고서 …)는 새 접수번호로 나온다. 옛 접수본과 정정본을
둘 다 판정하면 같은 회사·연도에 판정 행이 둘 남는다. BE 는 둘 다 최신(is_latest)으로 보아 조건에 함께 건다
(예: 팅크웨어 20260320001267 은 2024년 감사보고서를 잘못 붙였고 20260323001094 [첨부정정]이 2025년분이다).

규칙: 묶음 = (corp_code, 정정 표시를 뗀 보고서명). 묶음에서 판정 행이 있는 가장 늦은 접수번호가 최신본이다. 옛
접수본의 행은 최신본에 같은 (fs_scope, 주제) 행이 있을 때만 뺀다 — 첨부추가처럼 최신본에 그 범위·주제가 없으면
옛 행이 그 회사의 유일한 근거라 남긴다. 회사·보고서명을 모르는 접수번호는 손대지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict

_CORRECTION_PREFIX = re.compile(r"^(\s*\[[^\]]*\]\s*)+")


def base_report_name(name: str | None) -> str:
    """「[첨부정정]사업보고서 (2025.12)」 → 「사업보고서 (2025.12)」."""
    return _CORRECTION_PREFIX.sub("", name or "").strip()


def supersede(rows: list[dict], audit: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """판정 행 → (남길 행, 뺀 행). 뺀 행에는 superseded_by(최신 접수번호)를 붙여 돌려준다."""
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    key_of: dict[str, tuple[str, str]] = {}
    for r in rows:
        rc = r["rcept_no"]
        a = audit.get(rc) or {}
        corp, name = a.get("corp_code") or r.get("corp_code"), base_report_name(a.get("report_name"))
        if not corp or not name:
            continue
        key_of[rc] = (corp, name)
        groups[(corp, name)].add(rc)
    latest = {k: max(v) for k, v in groups.items() if len(v) > 1}
    have = {(r["rcept_no"], r["fs_scope"], r["topic"]) for r in rows}
    kept, dropped = [], []
    for r in rows:
        k = key_of.get(r["rcept_no"])
        new = latest.get(k) if k else None
        if new and new != r["rcept_no"] and (new, r["fs_scope"], r["topic"]) in have:
            dropped.append({**r, "superseded_by": new})
        else:
            kept.append(r)
    return kept, dropped
