"""상품·회차·권리별 기록(c15, 김현명 C1) — 2층 ② 사채 기록 두 실행 → instrument_rights.v1.jsonl 행.

회사 판정 행(전환권·내재파생)은 요약값이다. 회계사 C1: 상품·발행회차·권리별 분류를 따로 기록하고, 상품을 지정한 질문에는
그 상품만 쓴다. 두 실행(R16)이 같은 상품에 같은 분류를 낼 때만 행을 쓴다 — 한 실행에만 있거나 분류가 다르면 쓰지 않는다.

상품 키(product_key) 규칙 — 정정본·재실행에도 같은 상품이 같은 키를 갖게:
  1. 상품 이름(instrument)이나 series_key 에 「제N회」·「N회차」·「N차」 번호가 있으면 「<kind>-<N>」(예 CB-3).
  2. 없으면 series_key 의 마지막 숫자(「CB-?1」 → CB-1).
  3. 숫자가 없으면 「<kind>:<공백 뺀 상품 이름>」.
같은 범위에서 키가 겹치면(두 상품이 같은 번호) 그 키의 행은 쓰지 않는다 — 어느 상품인지 모른다. 잔액이 당기·전기 어디에도 없어
기준시점을 모르는 상품도 쓰지 않는다.
"""
from __future__ import annotations

import re

_ROUND = re.compile(r"제\s*(\d+)\s*회|(\d+)\s*회\s*차?|(\d+)\s*차")
_LAST_NUM = re.compile(r"(\d+)(?!.*\d)")
_CONV = {"equity": "EQUITY", "derivative_liability": "LIABILITY"}
_EMB = {"separated": "SEPARATED", "not_separated": "NOT_SEPARATED", "whole_fvpl": "WHOLE_FVPL"}
_CONV_FIELD = {"equity": "equity_component", "derivative_liability": "derivative_liability"}


def product_key(rec: dict) -> str | None:
    kind = (rec.get("kind") or "").strip() or "OTHER"
    for text in (rec.get("instrument") or "", rec.get("series_key") or ""):
        m = _ROUND.search(text)
        if m:
            return f"{kind}-{int(next(g for g in m.groups() if g))}"
    m = _LAST_NUM.search(rec.get("series_key") or "")
    if m:
        return f"{kind}-{int(m.group(1))}"
    name = re.sub(r"\s+", "", rec.get("instrument") or "")
    return f"{kind}:{name}" if name else None


def _by_key(recs: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    dup: set[str] = set()
    for r in recs or []:
        k = product_key(r)
        if not k:
            continue
        if k in out:
            dup.add(k)
        out[k] = r
    return {k: v for k, v in out.items() if k not in dup}


def _side(rec: dict, field: str | None) -> str | None:
    bal = rec.get("balance") or {}
    fields = [field] if field else ["host_carrying", "derivative_liability", "derivative_asset", "equity_component", "face"]
    for side in ("current", "prior"):
        b = bal.get(f"{side}_end") or {}
        if any(b.get(f) not in (None, 0) for f in fields):
            return side
    return None


def _refs(recs: tuple[dict, ...], *keys: str) -> list[str]:
    """두 실행 기록의 근거 칸(앞 키부터, 처음 나오는 키의 칸들)."""
    for key in keys:
        out = list(dict.fromkeys(r for rec in recs for r in (rec.get("evidence") or {}).get(key) or [] if r))
        if out:
            return out
    return []


def instrument_rights(rcept_no: str, fs_scope: str, run1: list[dict] | None, run2: list[dict] | None,
                      meta: dict | None = None) -> list[dict]:
    """한 공시·범위의 두 실행 기록 → 상품·권리 행. 두 실행이 같은 상품 키에 같은 분류를 낸 것만."""
    meta = meta or {}
    if run1 is None or run2 is None:
        return []
    a, b = _by_key(run1), _by_key(run2)
    out = []
    for key in sorted(set(a) & set(b)):
        r1, r2 = a[key], b[key]
        base = {"rcept_no": rcept_no, "corp_code": meta.get("corp_code"), "fiscal_year": meta.get("fiscal_year"),
                "fs_scope": fs_scope, "kind": r1.get("kind"), "product_key": key,
                "series_key": r1.get("series_key"), "instrument": (r1.get("instrument") or "").strip()}
        oc = r1.get("option_class")
        if oc in _CONV and oc == r2.get("option_class"):
            field = _CONV_FIELD[oc]
            side = _side(r1, field) or _side(r1, None)
            if side:  # 기간 근거(잔액)가 없는 상품은 쓰지 않는다
                out.append({**base, "right": "conversion", "class": _CONV[oc], "basis_period": side,
                            "evidence": f"{base['instrument'] or key} 전환권 {'자본' if oc == 'equity' else '파생상품부채'} 분류",
                            "source_refs": _refs((r1, r2), "option_class", f"balance.{side}_end.{field}")})
        es = r1.get("embedded_separation")
        if es in _EMB and es == r2.get("embedded_separation"):
            side = _side(r1, None)
            if not side:
                continue
            what = {"separated": "주계약과 분리", "not_separated": "주계약과 분리하지 않음",
                    "whole_fvpl": "상품 전체를 당기손익-공정가치로 지정"}[es]
            targets = r1.get("separated_targets") or []
            out.append({**base, "right": "embedded", "class": _EMB[es], "basis_period": side,
                        "targets": targets,
                        "evidence": f"{base['instrument'] or key} 내재파생상품 {what}",
                        "source_refs": _refs((r1, r2), "embedded_separation")})
    return out
