"""정정 계보(공시 단위) — 원본·정정본을 묶어 BE `dart_filing_lineage` 와 접수번호별 is_latest 를 만든다(10-28).

판정 행 단위 규칙(supersede.py, K2-1)과 같은 묶음 열쇠를 쓴다: (corp_code, 앞의 「[…]」 표시를 모두 뗀 보고서명).
묶음 안에서 접수번호(날짜 + 그날 일련번호) 순서가 늦을수록 최신이다. 접수본이 둘 이상인 묶음만 계보 행이 된다 —
정정 표시만 있고 원본을 수집하지 않은 혼자 묶음은 그 자체가 최신이라 BE 결과가 같다(계보 밖 = is_latest true).

출력 형식(dart3 10-28 합의):
  lineage 행  — corp_code, base_report_nm, first/last_rcept_no, first/last_rcept_dt(YYYYMMDD), revision_count(정정 표시가
                붙은 접수본 수), revision_types(정정 표시를 처음 나온 순서로 중복 없이 「|」로 이은 문자열, 없으면 "")
  members 행 — rcept_no, corp_code, base_report_nm, first_rcept_no, last_rcept_no, is_latest, revision_type(원본은 null,
                겹친 표시는 「|」로 이음)
"""
from __future__ import annotations

import re
from collections import defaultdict

from dart_search_mcp.ledger.supersede import base_report_name

_TAG = re.compile(r"\[\s*([^\]]*?)\s*\]")
_PREFIX = re.compile(r"^(\s*\[[^\]]*\]\s*)+")
_RCEPT = re.compile(r"\d{14}")
_CORP = re.compile(r"\d{8}")
_MAX_NAME = 256  # BE String(256) — base_report_nm·revision_types


def revision_tags(report_name: str | None) -> list[str]:
    """「[기재정정][첨부추가]사업보고서 (2025.12)」 → ["기재정정", "첨부추가"]. 앞머리 표시만 본다."""
    m = _PREFIX.match(report_name or "")
    return [t for t in _TAG.findall(m.group(0)) if t] if m else []


def build_lineage(filings: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """공시 목록(rcept_no·corp_code·report_name·rcept_dt) → (lineage 행, members 행, 통계).
    통계의 lone_rcepts 는 원본 없이 혼자인 정정본 접수번호 목록(확인 목록용)."""
    groups: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    stats = {"filings": 0, "skipped_no_key": 0, "skipped_bad_shape": 0, "duplicate_rcept": 0, "lone_correction": 0}
    lone: list[str] = []
    seen: set[str] = set()
    for f in filings:
        rc, corp = str(f.get("rcept_no") or ""), str(f.get("corp_code") or "")
        name = base_report_name(f.get("report_name"))
        stats["filings"] += 1
        if not rc or not corp or not name:
            stats["skipped_no_key"] += 1
            continue
        if not _RCEPT.fullmatch(rc) or not _CORP.fullmatch(corp) or len(name) > _MAX_NAME:
            stats["skipped_bad_shape"] += 1  # BE 칸(rcept 14·corp 8·이름 256)에 안 맞으면 싣지 않는다 — 접수번호 정렬도 14자리 숫자일 때만 시간순
            continue
        if rc in seen:
            stats["duplicate_rcept"] += 1
            continue
        seen.add(rc)
        groups[(corp, name)][rc] = f
    lineage, members = [], []
    for (corp, name), by_rc in sorted(groups.items()):
        order = sorted(by_rc)
        if len(order) < 2:
            if revision_tags(by_rc[order[0]].get("report_name")):
                lone.append(order[0])
            continue
        first, last = order[0], order[-1]
        types: list[str] = []
        count = 0
        for rc in order:
            tags = revision_tags(by_rc[rc].get("report_name"))
            if tags:
                count += 1
            types += [t for t in tags if t not in types]
            members.append({"rcept_no": rc, "corp_code": corp, "base_report_nm": name, "first_rcept_no": first,
                            "last_rcept_no": last, "is_latest": rc == last,
                            "revision_type": "|".join(tags) if tags else None})
        lineage.append({"corp_code": corp, "base_report_nm": name, "first_rcept_no": first, "last_rcept_no": last,
                        "first_rcept_dt": _dt(by_rc[first]), "last_rcept_dt": _dt(by_rc[last]),
                        "revision_count": count, "revision_types": "|".join(types)[:_MAX_NAME]})
    stats.update(lineage=len(lineage), members=len(members), lone_correction=len(lone), lone_rcepts=lone,
                 not_latest=sum(1 for m in members if not m["is_latest"]))
    return lineage, members, stats


def _dt(f: dict) -> str | None:
    """rcept_dt 를 YYYYMMDD 8자로. 없으면 접수번호 앞 8자리(접수일)."""
    d = re.sub(r"\D", "", str(f.get("rcept_dt") or ""))[:8]
    return d if len(d) == 8 else (str(f.get("rcept_no") or "")[:8] or None)
