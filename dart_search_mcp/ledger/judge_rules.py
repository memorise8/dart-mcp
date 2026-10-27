"""3층 규칙표 판정 (스펙 §6) — 사실(2층 ①)·사채 기록(2층 ②)·정책 표시(2층 ③) → 주제별 판정 행.

판정 단위는 공시 × fs_scope × 주제다. 기간은 「당기 또는 전기」(d6)이고 당기를 우선해 basis_period 를 정한다.
정밀도가 먼저다(R14): 근거가 모자라거나 서로 어긋나면 UNDETERMINED 와 held_reason 을 남긴다.

사채 기록은 독립된 두 번의 LLM 실행에서 온다(R16). 전환권 분류·내재파생 분리는 두 실행이 같은 회사 단위 값을 낼
때만 확정하고, 다르면 needs_review 다. `instruments` 인자는 기록 목록(두 실행이 같다고 봄) 또는
{"run1": 목록|None, "run2": 목록|None}(None = 그 실행에 그 공시·범위 줄이 없음)이다.

meta 키(모두 선택):
  corp_code, fiscal_year      audit_facts 에서
  tables                      {table_id: {"note_no","note_title","kind","multiplier"}} — 근거 줄의 주석·단위
  movement_table              무형자산 주석에 변동표가 있는가(None 이면 증가·상각 사실로 짐작)
  category_table              금융상품 범주표가 있는가(내재파생 NOT_APPLICABLE 의 조건)
  read_failed_topics          관련 잔액표·범주표(또는 제목 없는 표)를 읽지 못한 주제 집합(r5)
  read_failed_any_topics      관련 주석 표를 하나라도 읽지 못한 주제 집합 — 「없음」 값만 막는다(r5)
  instruments_targeted        이 공시·범위가 2층 ② 사채·우선주 대상이었는가(어느 실행이든 skipped 아닌 줄, R35)
  v1_ppa                      옛 topic_judge 의 PPA_MEASUREMENT_PERIOD 행(Q7 유지)
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
PARSER_VERSION = "ws8-v2"
METHOD = "ledger_rules"
UND = "UNDETERMINED"
TOPICS = ("CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION", "FVOCI_DESIGNATION",
          "DEV_COST_CAPITALIZATION", "LEASE_EXEMPTION", "IP_FAIR_VALUE", "PPA_MEASUREMENT_PERIOD",
          "BUSINESS_COMBINATION_COST_CLASS")

_SIDE = {"current_end": "current", "current_period": "current", "prior_end": "prior", "prior_period": "prior"}
_PERIOD_LABEL = {"current_end": "당기말", "prior_end": "전기말", "current_period": "당기", "prior_period": "전기"}
_UNIT_LABEL = {1: "원", 1000: "천원", 1000000: "백만원", 100000000: "억원"}
_STATEMENT_LABEL = {"statement_bs": "재무상태표", "statement_is": "손익계산서", "statement_ci": "포괄손익계산서",
                    "statement_ce": "자본변동표", "statement_cf": "현금흐름표"}
_FIELD_LABEL = {"equity_component": "자본요소", "derivative_liability": "파생상품부채",
                "derivative_asset": "파생상품자산", "host_carrying": "부채요소", "face": "액면"}
_SENT_SPLIT_RE = re.compile(r"(?<=다\.)\s*")
_COMPONENTS_SHOWN = 4
_WS_RE = re.compile(r"\s+")
_CELL_RE = re.compile(r"^(?P<tid>.+):r(?P<row>\d+):c(?P<col>\d+)$")


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    j = dict(raw["judge"])
    j["rules_version"] = raw.get("rules_version", "")
    j["exc_patterns"] = {n: d["pattern"] for n, d in raw.get("exception_patterns", {}).items()}
    rx = {}
    d = j["dev_expensed_statement"]
    rx["dev_exp"] = tuple(re.compile(d[k]) for k in ("pattern", "requires", "exclude"))
    rx["emb_not_sep"] = re.compile(j["emb_not_separated_statement"])
    rx["whole_fvpl"] = re.compile(j["whole_fvpl_designation_statement"]["pattern"])
    rx["ip_fv_sentence"] = re.compile(j["ip_fair_value_in_sentence"])
    rx["bc_accounts"] = {v: re.compile(p) for v, p in j["bc_cost_accounts"].items()}
    pp = j["ppa_provisional_statement"]
    rx["ppa_prov"] = tuple(re.compile(pp[k]) for k in ("pattern", "requires", "exclude"))
    rb = j["read_failed_blocking"]
    rx["rf_nonbalance"], rx["rf_balance"] = re.compile(rb["nonbalance"]), re.compile(rb["balance"])
    j["rx"] = rx
    return j


def load_rules(path: Path | None = None) -> dict:
    return _load(str(path or RULES_PATH))


# ---------------------------------------------------------------- 보조 함수

def _side(f: dict) -> str | None:
    return _SIDE.get(f.get("period") or "")


def _num(x) -> float:
    return x if isinstance(x, (int, float)) else 0


def _usable(facts: list[dict], concept: str) -> list[dict]:
    """기간을 아는(당기·전기) 그 개념 사실. 기간 unknown 은 판정에 쓰지 않는다(R14)."""
    return [f for f in facts if f.get("concept") == concept and _side(f)]


def _pos(facts: list[dict], *concepts: str) -> list[dict]:
    return [f for c in concepts for f in _usable(facts, c) if _num(f.get("amount")) > 0]


def _pos_abs(facts: list[dict], *concepts: str) -> list[dict]:
    """부호와 무관하게 금액이 있는 사실 — 괄호(음수)로 표시한 금액: 부채(EMB_DERIV_*)와 리스료(LEASE_*_EXP, r4)."""
    return [f for c in concepts for f in _usable(facts, c) if abs(_num(f.get("amount"))) > 0]


def _first_with(recs: list[dict], *fields: str) -> tuple[dict, str]:
    """잔액이 0 이 아닌 첫 기록과 그 칸(없으면 첫 기록과 첫 칸)."""
    for r in recs:
        for fld in fields:
            if _record_side(r, fld):
                return r, fld
    return recs[0], fields[0]


def _nonzero(facts: list[dict], *concepts: str) -> list[dict]:
    return [f for c in concepts for f in _usable(facts, c) if _num(f.get("amount")) != 0]


def host(facts: list[dict]) -> bool:
    """HOST_LIABILITY 사실이 당기·전기 중 하나라도 > 0."""
    return bool(_pos(facts, "HOST_LIABILITY"))


def has_policy(marks: list[dict], topic: str) -> bool:
    return any(m.get("topic") == topic and m.get("kind") == "policy" for m in marks)


def undetermined(policy_only: bool, reason_if_held: str | None, *, rules: dict | None = None) -> dict | None:
    """판단불가 결과. 사실은 있으나 근거가 모자라면(reason_if_held) no_facts 와 그 사유,
    사실·기록이 모두 없고 정책 문장만 있으면 policy_only, 둘 다 아니면 None(행 없음)."""
    if reason_if_held:
        return _res(UND, held="no_facts", evidence=reason_if_held)
    if policy_only:
        rules = rules or load_rules()
        return _res(UND, held="policy_only", evidence=rules["reasons"]["policy_only"], kind="policy")
    return None


def _res(value, *, held="", evidence="", basis=None, refs=None, note=None, kind=None, heading="") -> dict:
    return {"value": value, "held_reason": held, "evidence": evidence, "basis_period": basis,
            "source_refs": list(refs or []), "review_note": note, "sentence_kind": kind, "heading": heading}


def _review(note: str, evidence: str | None = None) -> dict:
    return _res(UND, held="needs_review", evidence=evidence or f"확인 필요: {note}", note=note)


def _fact_ref(f: dict) -> str:
    s = f.get("source") or {}
    if s.get("sentence_id") and not s.get("table_id"):
        return s["sentence_id"]  # 문장 사실(c11)
    return f"{s.get('table_id')}:r{s.get('row')}:c{s.get('col')}"


def _fmt_amount(x) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.2f}"
    return f"{int(x):,}"


def _fact_unit(f: dict) -> str:
    if f.get("unit_kind") == "foreign":
        return f.get("currency") or ""
    a, k = f.get("amount"), f.get("amount_krw")
    if isinstance(a, (int, float)) and isinstance(k, (int, float)) and a:
        return _UNIT_LABEL.get(round(k / a), "")
    return ""


def _heading(tid: str | None, tables: dict) -> str:
    t = (tables or {}).get(tid or "") or {}
    kind = t.get("kind") or ""
    if kind == "note" or t.get("note_title"):
        return " ".join(x for x in ("주석", str(t.get("note_no") or "").strip(), (t.get("note_title") or "").strip()) if x)
    return _STATEMENT_LABEL.get(kind, "")


def evidence_line(fact: dict, tables: dict | None = None) -> str:
    """「<행 이름> <금액(원래 단위·단위 표기)> (<당기말|전기말|당기|전기>, 주석 <번호> <제목>)」."""
    amount = fact.get("amount")
    if fact.get("match_basis") == "sentence":  # 문장 사실: 문장 그대로(금액·단위가 문장에 있다)
        return f"{(fact.get('text') or '').strip()} ({_PERIOD_LABEL.get(fact.get('period') or '', '')}, 문장)"
    amt = ("-" if amount is None else _fmt_amount(amount)) + _fact_unit(fact)
    where = [_PERIOD_LABEL.get(fact.get("period") or "", "")]
    head = _heading((fact.get("source") or {}).get("table_id"), tables or {})
    if head:
        where.append(head)
    where = [w for w in where if w]
    label = (fact.get("label") or fact.get("concept") or "").strip()
    path = [p.strip() for p in fact.get("label_path") or [] if p and p.strip()]
    if fact.get("concept") in ("FVOCI_EQUITY", "RND_EXPENSE", "DEV_COST_ADDITION") and len(path) > 1 and not fact.get("components"):
        # c12: 경로(「금융자산 / 비상장 주식 / 수준 3」)로 걸린 지분 행은 경로를 보인다(행 이름만으로는 무엇인지 모름)
        label = " / ".join(dict.fromkeys(path))
    comps = fact.get("components") or []
    if comps:  # c12: 합계 행은 무엇의 합계인지(구획·더한 행 이름) 보인다(회계사 두 명, s055·S053)
        n = max(fact.get("components_total") or 0, len(comps))
        shown = "·".join(comps[:_COMPONENTS_SHOWN]) + (f" 외 {n - _COMPONENTS_SHOWN}건" if n > _COMPONENTS_SHOWN else "")
        label = " ".join(x for x in (fact.get("section"), f"{label}(구성: {shown})") if x)
    return f"{label} {amt}" + (f" ({', '.join(where)})" if where else "")


def _pick(facts: list[dict]) -> dict | None:
    """대표 사실: 당기 우선, 그 안에서 금액 절댓값이 큰 것."""
    if not facts:
        return None
    return sorted(facts, key=lambda f: (_side(f) != "current", -abs(_num(f.get("amount")))))[0]


def _basis(facts: list[dict]) -> str | None:
    sides = {_side(f) for f in facts}
    return "current" if "current" in sides else ("prior" if "prior" in sides else None)


def _from_facts(value: str, facts: list[dict], tables: dict, *, extra_refs=()) -> dict:
    """사실 근거 결정 결과 — 근거 줄은 대표 사실, source_refs 는 근거 사실 전부(당기 먼저)."""
    best = _pick(facts)
    ordered = sorted(facts, key=lambda f: (_side(f) != "current",))
    refs = list(dict.fromkeys([_fact_ref(f) for f in ordered] + list(extra_refs)))
    return _res(value, evidence=evidence_line(best, tables), basis=_basis(facts), refs=refs, kind="ledger_fact",
                heading=_heading((best.get("source") or {}).get("table_id"), tables))


def _topic_facts(ctx: "Ctx", topic: str) -> list[dict]:
    group = set(ctx.rules["concept_groups"][topic])
    return [f for f in ctx.facts if f.get("concept") in group and _side(f)]


def _topic_marks(marks: list[dict], topic: str) -> list[dict]:
    return [m for m in marks if m.get("topic") == topic]


class Ctx:
    def __init__(self, facts, marks, meta, rules, fs_scope=None):
        self.facts, self.marks, self.meta, self.rules = facts, marks, meta, rules
        self.fs_scope = fs_scope
        self.targeted_no_records = False  # R35: 사채·우선주 대상인데 두 실행 모두 기록 0 (judge_filing 이 정함)
        self.tables = meta.get("tables") or {}
        self.reasons = rules["reasons"]
        self.rx = rules["rx"]

    def und(self, topic: str, reason_key: str | None) -> dict | None:
        """사유가 있으면 no_facts, 그 주제의 사실이 하나도 없고 정책 문장이 있으면 policy_only."""
        reason = self.reasons[reason_key] if reason_key else None
        facts = [f for f in _topic_facts(self, topic) if _num(f.get("amount")) != 0]
        return undetermined(has_policy(self.marks, topic) and not facts, reason, rules=self.rules)


# ---------------------------------------------------------------- 사채 기록

def _cell_tid(ref: str) -> str | None:
    m = _CELL_RE.match(ref or "")
    return m.group("tid") if m else None


def _record_line(rec: dict, field: str, side: str | None, tables: dict,
                 ev_key: str = "option_class") -> tuple[str, list[str], str]:
    """기록 근거 줄: 「<상품> <칸 이름> <금액><단위> (<당기말|전기말>, 주석 …)」."""
    name = (rec.get("instrument") or rec.get("series_key") or "사채").strip()
    ev = rec.get("evidence") or {}
    if side:
        key = f"balance.{side}_end.{field}"
        amount = ((rec.get("balance") or {}).get(f"{side}_end") or {}).get(field)
        refs = list(ev.get(key) or [])
        tid = next((t for t in map(_cell_tid, refs) if t), None)
        unit = _UNIT_LABEL.get((tables.get(tid or "") or {}).get("multiplier"), "") if tid else ""
        where = ["당기말" if side == "current" else "전기말"]
        head = _heading(tid, tables)
        if head:
            where.append(head)
        return f"{name} {_FIELD_LABEL[field]} {_fmt_amount(amount)}{unit} ({', '.join(where)})", refs, head
    refs = list(dict.fromkeys(ev.get(ev_key) or []))
    tid = next((t for t in map(_cell_tid, refs) if t), None)
    return f"{name} {_FIELD_LABEL[field]} 분류 (근거 {', '.join(refs) or '기록'})", refs, _heading(tid, tables)


def _record_side(rec: dict, field: str) -> str | None:
    bal = rec.get("balance") or {}
    for side in ("current", "prior"):
        if _num((bal.get(f"{side}_end") or {}).get(field)) != 0:
            return side
    return None


def _rec_sides(recs: list[dict]) -> set[str]:
    """기록이 가리키는 기간 — 잔액 칸(어느 칸이든) 0 이 아닌 기간, 없으면 변동(당기·전기)이 적힌 기간."""
    bal = {s for r in recs for s in ("current", "prior")
           if any(_num(v) != 0 for v in ((r.get("balance") or {}).get(f"{s}_end") or {}).values())}
    if bal:
        return bal
    return {s for r in recs for s in ("current", "prior") if ((r.get("movement") or {}).get(s))}


def _period(recs: list[dict], facts: list[dict]) -> str | None:
    """기록으로 정한 값의 basis_period(I2): 기록 잔액 → 기록 변동 → 같은 범위의 기간 아는 사실 순, 당기 우선."""
    sides = _rec_sides(recs) or {_side(f) for f in facts}
    return "current" if "current" in sides else ("prior" if "prior" in sides else None)


def _from_records(value: str, picks: list[tuple[dict, str]], tables: dict, extra_ev: str = "",
                  facts: list[dict] = (), recs: list[dict] = ()) -> dict:
    """기록 근거 결과. basis_period 는 근거 줄 칸의 기간(당기 우선), 그 칸이 비면 _period(근거 기록 + 값을 정한
    기록 전체 `recs`, facts) — 근거 줄이 잔액 없는 첫 기록으로 돌아가도 다른 기록의 기간을 쓴다."""
    lines, refs, sides, head = [], [], set(), ""
    for rec, field in picks:
        side = _record_side(rec, field)
        line, r, h = _record_line(rec, field, side, tables)
        lines.append((line, side))
        refs.extend(r)
        sides.add(side)
        head = head or h
    basis = "current" if "current" in sides else ("prior" if "prior" in sides else None)
    basis = basis or _period([rec for rec, _ in picks] + list(recs), facts)
    ev = " / ".join([_join_sides(lines)] + ([extra_ev] if extra_ev else []))
    return _res(value, evidence=ev, basis=basis, refs=list(dict.fromkeys(refs)), kind="instrument_record",
                heading=head)


def _join_sides(items: list[tuple[str, str | None]]) -> str:
    """c13(합의 규칙 3·25, v2q Q2): 근거 줄을 기간별로 나눠 보인다 — 당기 먼저, 전기는 「전기 기준」. 한 기간뿐이면 그대로."""
    items = list(dict.fromkeys(items))  # 같은 문장(한 문장에 단기·소액 금액)은 한 번만
    sides = {sd for _, sd in items if sd}
    if len(sides) < 2:
        return " / ".join(line for line, _ in items)
    cur = [line for line, sd in items if sd != "prior"]
    pri = [line for line, sd in items if sd == "prior"]
    return f"당기: {' / '.join(cur)} / 전기 기준: {' / '.join(pri)}"


def _non_conversion(f: dict) -> bool:
    """내재파생 사실의 대상이 전환권이 아님이 분명한가 — 상환권(조기상환청구권 등)이거나, 행·경로·문맥이 정한
    「기타」(신종자본증권 등). 대상 낱말이 없어 붙은 other/default 와 대상 모름(None)은 분명하지 않다."""
    t = f.get("target")
    return t == "redemption" or (t == "other" and f.get("target_basis") not in (None, "default"))


def _beside_equity_component(ctx: Ctx, f: dict) -> bool:
    """같은 표·같은 열·같은 기간에 0 보다 큰 전환권대가(CONV_OPTION_EQUITY) 사실이 있는가 — 회차별 사채 표(S016 모양).
    열까지 같아야 한다: 회차를 열로 나눈 표에서 다른 회차의 전환권대가는 이 파생상품부채가 다른 권리라는 근거가 아니다."""
    src = f.get("source") or {}
    tid = src.get("table_id")
    return bool(tid) and any((e.get("source") or {}).get("table_id") == tid and (e.get("source") or {}).get("col") == src.get("col")
                             and _side(e) == _side(f) for e in _pos(ctx.facts, "CONV_OPTION_EQUITY"))


def _equity_guard(ctx: Ctx, res: dict) -> dict:
    """EQUITY 판정에 같은 범위·기간 안 전환권 파생상품부채로 볼 금액이 있으면 확인 필요(d10, T11b W3·W4).
    CONV_OPTION_LIABILITY 금액(부호 무관), 또는 대상이 전환권이 아님이 분명하지 않은 EMB_DERIV_LIABILITY 금액이다.
    파생상품부채 기록(option_class=derivative_liability)이 없으면 부채·둘 다로 증명된 것은 아니어서 값을 정하지 않는다."""
    if res.get("value") != "EQUITY":
        return res
    emb = [f for f in _pos_abs(ctx.facts, "EMB_DERIV_LIABILITY") if not _non_conversion(f)]
    other = [f for f in emb if _beside_equity_component(ctx, f)]
    li = [f for f in _usable(ctx.facts, "CONV_OPTION_LIABILITY") if _num(f.get("amount")) != 0] + \
        [f for f in emb if f not in other]
    aside = []
    if res.get("basis_period"):  # c14: 「같은 기간 안」 — 다른 기준시점의 부채 금액은 이 판정을 막지 않고 따로 보인다(C1)
        aside = [f for f in li if _side(f) != res["basis_period"]]
        li = [f for f in li if _side(f) == res["basis_period"]]
        other = [f for f in other if _side(f) == res["basis_period"]]
    if aside and not li:
        res = {**res, "evidence": f"{res['evidence']} / 다른 기준시점: 파생상품부채 {evidence_line(_pick(aside), ctx.tables)}"
                                  f"(기준시점이 달라 이 판정에 넣지 않음)"}
        res["flags"] = list(res.get("flags") or []) + ["conv_other_period_shown"]
    if other and not li:
        # c14(김현명 C1, S016 마이크로디지탈): 전환권대가(자본)와 같은 표·같은 기간의 대상 없는 파생상품부채는 그 사채의
        # 다른 권리(조기상환권 등)다 — 한 상품의 전환권은 자본이면서 부채일 수 없다. 별도 정보로 보이고 합산하지 않는다
        res = {**res, "evidence": f"{res['evidence']} / 별도: 다른 권리 파생상품부채 "
                                  f"{evidence_line(_pick(other), ctx.tables)}(전환권 판정에 합산 안 함)"}
        res["flags"] = list(res.get("flags") or []) + ["conv_other_right_shown"]
        return res
    if not li:
        return res
    return _review(f"자본 분류 근거({res['evidence']})인데 전환권 파생상품부채로 볼 금액이 있음: "
                   f"{evidence_line(_pick(li), ctx.tables)}")


def _conv_run(ctx: Ctx, ins: list[dict]) -> dict | None:
    topic = "CONVERSION_OPTION_CLASS"
    eq = [i for i in ins if i.get("option_class") == "equity"]
    li = [i for i in ins if i.get("option_class") == "derivative_liability"]
    feq, fli = _pos(ctx.facts, "CONV_OPTION_EQUITY"), _pos(ctx.facts, "CONV_OPTION_LIABILITY")
    if not ins:
        if feq and fli and not {_side(f) for f in feq} & {_side(f) for f in fli}:
            # c14(김현명 C1): 기준시점이 다른 자본·부채 금액은 「둘 다」로 묶지 않고 한쪽을 고르지도 않는다(위 기록 길과 같음)
            return _review("기준시점이 다른 자본·부채 기록", "확인 필요: 기준시점이 다른 자본·부채 금액(상품별 확인 필요): " +
                           _join_sides([(evidence_line(_pick(x), ctx.tables), _side(_pick(x))) for x in (feq, fli)]))
        cls = ("BOTH" if feq and fli else "EQUITY" if feq else "LIABILITY" if fli else None)
        if cls:
            res = _from_facts(cls, feq + fli, ctx.tables)
            if cls == "BOTH":
                both = {_side(f) for f in feq} & {_side(f) for f in fli}
                pick = "current" if "current" in both else "prior"  # c14: 두 분류가 함께 있는 기간이 기준시점
                feq, fli = [f for f in feq if _side(f) == pick], [f for f in fli if _side(f) == pick]
                res = _from_facts(cls, feq + fli, ctx.tables)
                res["evidence"] = _join_sides([(evidence_line(_pick(x), ctx.tables), _side(_pick(x))) for x in (feq, fli)])
            return _equity_guard(ctx, res)
        return ctx.und(topic, "conv_no_class" if host(ctx.facts) else None)
    if not (eq or li):
        return ctx.und(topic, "conv_no_class")
    value = "BOTH" if eq and li else ("EQUITY" if eq else "LIABILITY")
    # 기록이 가리키지 않은 분류의 금액 사실이 있으면 확인 필요(R14)
    missing = [f for f in (fli if not li else []) + (feq if not eq else [])]
    if missing:
        return _review(f"사채 기록은 {value}인데 다른 분류 금액이 있음: {evidence_line(_pick(missing), ctx.tables)}")
    # R34(d6): 쓴 기록에 당기·전기 잔액·변동이 없고 같은 범위에 기간 아는 사채 사실(전환권·부채요소)도 없으면
    # 판정 기간 안에 그 사채를 가졌는지 모른다 → 판단불가. 전기에 상환·전환한 변동도 근거다.
    period_facts = _nonzero(ctx.facts, "CONV_OPTION_EQUITY", "CONV_OPTION_LIABILITY", "HOST_LIABILITY")
    if not _rec_sides(eq + li) and not period_facts:
        return undetermined(False, ctx.reasons["conv_no_period"])
    picks = [_first_with(eq, "equity_component")] if eq else []
    if li:
        picks.append(_first_with(li, "derivative_liability"))
    se, sl = _rec_sides(eq), _rec_sides(li)
    if eq and li and se and sl and not se & sl:
        # c14(김현명 C1): 「둘 다」는 같은 기준시점에서 자본·부채가 각각 확인될 때만. 시점이 다르면 대개 다른 상품(EB 교환권대가와
        # CB 파생부채 등, s002·s013)이라 한쪽을 골라 정하지 않는다 — 상품별 기록(2단계) 전까지 확인 필요
        return _review("기준시점이 다른 자본·부채 기록", "확인 필요: 기준시점이 다른 자본·부채 기록(상품별 확인 필요): " +
                       _from_records(value, picks, ctx.tables)["evidence"])
    res = _from_records(value, picks, ctx.tables, facts=period_facts, recs=eq + li)
    if value == "BOTH" and se and sl:  # c14: 두 분류가 함께 있는 기간이 기준시점
        res["basis_period"] = "current" if "current" in se & sl else "prior"
    return _equity_guard(ctx, res)


def _held(ctx: Ctx, recs: list[dict]) -> bool:
    """분리 문장·기록이 가리키는 복합상품을 판정 기간(당기말·전기말) 안에 가지고 있는가(d6, T11b W1) — 그 기록의
    잔액 칸 하나라도 0 이 아니거나, 같은 범위에 분리된 파생상품 금액 사실(EMB_*·CONV_OPTION_LIABILITY, 기간 앎)이 0 이
    아님. 다른 사채의 부채요소·자본요소(HOST_LIABILITY·CONV_OPTION_EQUITY)는 그 상품의 보유 근거가 아니다(수정 1차)."""
    for r in recs:
        bal = r.get("balance") or {}
        if any(_num(v) != 0 for side in ("current_end", "prior_end") for v in (bal.get(side) or {}).values()):
            return True
    return bool(_nonzero(ctx.facts, "EMB_DERIV_LIABILITY", "EMB_DERIV_ASSET", "CONV_OPTION_LIABILITY"))


def _whole_fvpl_mark(ctx: Ctx) -> dict | None:
    """같은 공시의 문장(주제·종류 무관) 중 복합계약(상품) 전체를 당기손익-공정가치로 지정했다는 문장(d8)."""
    rx = ctx.rx["whole_fvpl"]
    return next((m for m in ctx.marks if rx.search(m.get("text") or "")), None)


def _emb_run(ctx: Ctx, ins: list[dict]) -> dict | None:
    topic = "EMBEDDED_DERIVATIVE_SEPARATION"
    rules = ctx.rules
    sep = [i for i in ins if i.get("embedded_separation") == "separated"]
    nots = [i for i in ins if i.get("embedded_separation") in ("not_separated", "whole_fvpl")]
    emb = _pos_abs(ctx.facts, "EMB_DERIV_LIABILITY", "EMB_DERIV_ASSET")
    strong = [f for f in emb if f.get("target") in rules["emb_strong_targets"]
              and f.get("target_basis") in rules["emb_strong_bases"]]
    weak = [f for f in emb if f not in strong]
    stmt = [m for m in _topic_marks(ctx.marks, topic)
            if m.get("kind") == "company_statement" and ctx.rx["emb_not_sep"].search(m.get("text") or "")]
    if sep and not _held(ctx, sep):
        sep = []  # 분리 문장·기록이 가리키는 상품이 당기·전기 모두 없음 → 해당 없음·판단불가 규칙으로(§6.2)
    if sep or strong:
        if stmt:
            return _review(f"회사 문장은 분리하지 않음({stmt[0].get('sentence_id')})인데 분리 금액·기록이 있음")
        whole = _whole_fvpl_mark(ctx)
        if whole:
            if sep:
                rec = sep[0]
                refs = (rec.get("evidence") or {}).get("embedded_separation") or []
                what = f"{(rec.get('instrument') or rec.get('series_key') or '사채').strip()} 분리 기록" + \
                    (f"({', '.join(dict.fromkeys(refs))})" if refs else "")
            else:
                f = _pick(strong)
                what = f"분리 금액 {evidence_line(f, ctx.tables)}({_fact_ref(f)})"
            return _review(f"{what}과 상품 전체를 당기손익-공정가치로 지정한 문장({whole.get('sentence_id')})이 함께 있음")
        if strong and nots and not sep:
            return _review(f"사채 기록은 {nots[0].get('embedded_separation')}인데 분리 금액이 있음: "
                           f"{evidence_line(_pick(strong), ctx.tables)}")
        if sep:
            rec, field = _first_with(sep, "derivative_liability", "derivative_asset")
            if _record_side(rec, field):
                return _from_records("SEPARATED", [(rec, field)], ctx.tables)
            line, refs, head = _record_line(rec, field, None, ctx.tables, "embedded_separation")
            # 분리 문장 기록: 기간은 _held 가 본 기록 잔액(·변동) 또는 분리된 파생상품 금액 사실에서
            held_facts = _nonzero(ctx.facts, "EMB_DERIV_LIABILITY", "EMB_DERIV_ASSET", "CONV_OPTION_LIABILITY")
            return _res("SEPARATED", evidence=line.replace(f"{_FIELD_LABEL[field]} 분류", "주계약과 분리"),
                        basis=_period(sep, held_facts), refs=refs, kind="instrument_record", heading=head)
        return _from_facts("SEPARATED", strong, ctx.tables)
    if nots:
        if weak:
            return _review(f"사채 기록은 {nots[0].get('embedded_separation')}인데 파생상품 금액이 있음: "
                           f"{evidence_line(_pick(weak), ctx.tables)}")
        # 분리 안 함도 판정 기간 안의 상품이어야 한다(d6, I2) — 기록 잔액·변동, 없으면 같은 범위의 복합상품 사실
        basis = _period(nots, _nonzero(ctx.facts, *rules["concept_groups"][topic]))
        if not basis:
            return undetermined(False, ctx.reasons["emb_no_period"])
        rec = nots[0]
        refs = list((rec.get("evidence") or {}).get("embedded_separation") or [])
        what = "상품 전체를 당기손익-공정가치로 지정" if rec.get("embedded_separation") == "whole_fvpl" else "주계약과 분리하지 않음"
        name = (rec.get("instrument") or rec.get("series_key") or "사채").strip()
        tid = next((t for t in map(_cell_tid, refs) if t), None)
        return _res("NOT_SEPARATED", evidence=f"{name} {what} (근거 {', '.join(refs) or '기록'})", basis=basis,
                    refs=refs, kind="instrument_record", heading=_heading(tid, ctx.tables))
    compound = _nonzero(ctx.facts, *ctx.rules["concept_groups"][topic])
    unknown_period = [f for f in ctx.facts if f.get("concept") in ctx.rules["concept_groups"][topic]
                      and not _side(f) and _num(f.get("amount")) != 0]
    if not compound and not unknown_period and not ins and ctx.meta.get("category_table") \
            and _topic_marks(ctx.marks, topic):
        if ctx.targeted_no_records:
            # R35: 사채·우선주 대상 공시인데 회차 기록이 0 — 개념 재현율이 낮아 없음은 약한 근거다
            return undetermined(False, ctx.reasons["emb_targeted_no_records"])
        # 당기·전기 둘 다 보고 없음을 확인한 값 — basis_period 는 이 보고서의 당기(I2)
        return _res("NOT_APPLICABLE", evidence="당기·전기 모두 복합상품(사채·전환권·내재파생상품) 잔액 없음, 금융상품 범주표 있음",
                    basis="current", kind="ledger_fact")
    if weak:
        return ctx.und(topic, "emb_weak_only")
    if ins or host(ctx.facts) or _pos(ctx.facts, "CONV_OPTION_EQUITY", "CONV_OPTION_LIABILITY"):
        return ctx.und(topic, "emb_no_evidence")
    return ctx.und(topic, None)


_CONFIRMED = {"CONVERSION_OPTION_CLASS": ("option_class", {"equity", "derivative_liability"}),
              "EMBEDDED_DERIVATIVE_SEPARATION": ("embedded_separation", {"separated", "not_separated", "whole_fvpl"})}


def _consensus(ctx: Ctx, topic: str, runs: tuple, fn) -> dict | None:
    a, b = runs
    field, ok = _CONFIRMED[topic]
    confirmed = lambda rs: [r for r in rs or [] if r.get(field) in ok]  # noqa: E731
    if (a is None) != (b is None):
        present = a if a is not None else b
        if confirmed(present):
            which = "2차" if b is None else "1차"
            return _review(f"{which} 실행에 기록 없음, 다른 실행은 확인된 기록 있음")
        a = b = present
    ra, rb = fn(ctx, a or []), fn(ctx, b or [])
    va, vb = (ra or {}).get("value"), (rb or {}).get("value")
    if va == vb:
        # 값이 같아도 한 실행이 확인 필요면 확인 필요가 이긴다
        return rb if (rb or {}).get("held_reason") == "needs_review" else ra
    if {va, vb} == {None, UND}:  # 한 실행은 행 없음, 다른 실행은 판단불가 — 둘 다 확정 값이 없다
        return ra or rb
    desc = lambda r: "행 없음" if r is None else (r["value"] + (f"/{r['held_reason']}" if r["held_reason"] else ""))  # noqa: E731
    return _review(f"두 실행 값이 다름: 1차 {desc(ra)} / 2차 {desc(rb)}")


# ---------------------------------------------------------------- 주제별 사실 판정

def _fvoci_stmt(ctx: Ctx) -> list[dict]:
    """회사가 보유 지분상품을 FVOCI 로 지정했다는 직접 문장(c12 2층 문장 사실, 보유 내역 주석의 문장만)."""
    return [f for f in ctx.facts if f.get("concept") == "FVOCI_DESIGNATION_STMT"]


def _fvoci(ctx: Ctx) -> dict | None:
    topic = "FVOCI_DESIGNATION"
    eq = _pos(ctx.facts, "FVOCI_EQUITY")
    reserve = _nonzero(ctx.facts, "FVOCI_EQUITY_RESERVE")
    stmt = _fvoci_stmt(ctx)
    cur = [f for f in eq + reserve if _side(f) == "current"]
    # c14(김현명 C3·합의 Q2, K2): 기말 잔액이 없다는 이유만으로 지정 사례에서 빼지 않는다. 전기에만 FVOCI 지분 잔액이
    # 있으면 c11 옛 사실이든 c12 새 사실이든 한 규칙 — DESIGNATED(전기 기준) + 「당기말 잔액 없음(기중 처분 여부 미확인)」
    if eq or reserve:
        res = _from_facts("DESIGNATED", cur or eq + reserve, ctx.tables)
        if stmt:  # r5: 지정 문장도 근거로 붙인다
            res["evidence"] = f"{res['evidence']} / 지정 문장: {(stmt[0].get('text') or '').strip()[:200]}"
            res["source_refs"].append(_fact_ref(stmt[0]))
        if not cur:
            _fvoci_no_current(res)
        return res
    total = _pos(ctx.facts, "FVOCI_FA_TOTAL") + _nonzero(ctx.facts, "FVOCI_FA_RESERVE")
    held = _pos(ctx.facts, "FVOCI_FA_TOTAL")
    if stmt and held and not [f for f in held if _side(f) == "current"]:
        # 수정 1차: 문장 길도 당기 잔액. 전기 총액은 채무상품이 섞였을 수 있어 K2(지분 잔액) 규칙을 넓히지 않는다
        return _review("전기만 FVOCI 잔액", f"확인 필요: 전기만 FVOCI 잔액(지정 문장 있음, 당기 잔액 없음): "
                                       f"{evidence_line(_pick(held), ctx.tables)}")
    held = [f for f in held if _side(f) == "current"]
    if stmt and held:
        # r5(회계사 두 명 s056·s058): 「…지정하는 취소불가능한 선택권을 적용하였습니다」 「…기타포괄손익으로 인식하도록
        # 지정하였습니다」 — 지분상품 지정을 회사가 직접 밝힌 문장 + FVOCI 금융자산 잔액(0 초과). 총액만 있어 지분인지
        # 모르던 것을 정한다. 문장만으로는 정하지 않는다: 지정 후 전량 처분해 기말 잔액이 없을 수 있다(s052 우리벤처파트너스).
        # basis_period 는 잔액의 기간(당기 우선)
        best = _pick(held)
        return _res("DESIGNATED", evidence=f"{(stmt[0].get('text') or '').strip()[:300]} / {evidence_line(best, ctx.tables)}",
                    basis=_basis(held), refs=[_fact_ref(stmt[0])] + [_fact_ref(f) for f in held], kind="company_statement",
                    heading=_heading((best.get("source") or {}).get("table_id"), ctx.tables))
    fvpl = _pos(ctx.facts, "FVPL_EQUITY")
    if fvpl and not total:
        res = _from_facts("NOT_DESIGNATED", fvpl, ctx.tables)
        _fvoci_none_note(ctx, res)
        return res
    if total:
        return ctx.und(topic, "fvoci_total_only")
    if _pos(ctx.facts, "FVOCI_DEBT") or _nonzero(ctx.facts, "FVOCI_DEBT_RESERVE"):
        return ctx.und(topic, "fvoci_debt_only")
    if [f for f in _topic_facts(ctx, topic) if f.get("concept") != "FVOCI_DESIGNATION_STMT"]:
        return ctx.und(topic, "fvoci_no_equity")  # 지정 문장(금액 없음)만으로는 사유를 바꾸지 않는다(r5)
    return ctx.und(topic, None)


def _fvoci_no_current(res: dict) -> None:
    res["evidence"] = f"{res['evidence']} / 당기말 잔액 없음(기중 처분 여부 미확인)"
    res["flags"] = list(res.get("flags") or []) + ["fvoci_no_current_balance"]


def _fvoci_none_note(ctx: Ctx, res: dict) -> None:
    """c13(합의 규칙 12·13, s042·s044·s048·s050): 범주별 금융상품 표에 FVOCI 가 없으면 근거 앞에 그 표를 먼저 보인다 — 표시만."""
    tid = ctx.meta.get("category_table_id")
    if not (ctx.meta.get("category_table") and ctx.meta.get("category_fvoci") is False):
        return
    if not tid:  # 수정 1차: 금액 있는 범주표가 없으면 표를 대지 않고 문구만
        res["evidence"] = f"FVOCI 없음(FVTPL만) / {res['evidence']}"
        return
    head = _heading(tid, ctx.tables)
    res["evidence"] = f"FVOCI 없음(FVTPL만): 범주별 금융상품 표({head or tid}) / {res['evidence']}"
    res["source_refs"] = list(res["source_refs"]) + [tid]


def _dev_expensed(ctx: Ctx) -> dict | None:
    pat, req, exc = ctx.rx["dev_exp"]
    for m in _topic_marks(ctx.marks, "DEV_COST_CAPITALIZATION"):
        for sent in _SENT_SPLIT_RE.split(m.get("text") or ""):  # 문단 안 문장 하나 안에서만 본다
            if pat.search(sent) and req.search(sent) and not exc.search(sent):
                return {**m, "text": sent.strip()}
    return None


def _top(facts: list[dict], n: int = 2) -> list[dict]:
    return sorted(facts, key=lambda f: -abs(_num(f.get("amount"))))[:n]


_DEV_PAST_BALANCE_NOTE = "올해 자산화 없음(과거 개발비 잔액 있음)"


def _dev_expensed_by_amount(ctx: Ctx, add: list[dict], asset: list[dict], rnd_cur: list[dict]) -> dict | None:
    """c14(김현명 D3·b6): 당기 연구개발 관련 비용 금액 + 무형자산 변동표에서 당기 개발비 증가 없음 → EXPENSED.
    개발비 잔액이 있으면(과거 자산화) 당기 증가 칸이 0·「-」로 **읽혀야** 한다 — 못 찾은 것과 0 을 나눈다. 그때는
    「올해 자산화 없음(과거 개발비 잔액 있음)」과 표지 expensed_current_year_only 로 전액 비용 처리와 구분한다.
    개발비 잔액도 증가 사실도 없으면 변동표에 개발비가 없는 것 — 표지 dev_expensed_by_amount. 비용 줄은 원문 명칭 그대로."""
    if not rnd_cur or not ctx.meta.get("movement_table"):
        return None
    if [f for f in add if _side(f) == "current"] or (not asset and _dev_expensed(ctx)):
        return None  # 당기 증가가 있거나, 회사의 비용 처리 문장이 있으면 그 길(문장 근거)이 먼저
    zero_cur = [f for f in _usable(ctx.facts, "DEV_COST_ADDITION") if _side(f) == "current" and _num(f.get("amount")) == 0]
    rnd_line = " / ".join(evidence_line(f, ctx.tables) for f in _top(rnd_cur))
    refs = [_fact_ref(f) for f in _top(rnd_cur)]
    if asset or add:
        if not zero_cur:
            return None  # 잔액(또는 전기 증가)은 있는데 당기 증가 칸을 못 읽음 — 정하지 않는다
        z = zero_cur[0]
        head = _heading((z.get("source") or {}).get("table_id"), ctx.tables)
        ev = (f"{_DEV_PAST_BALANCE_NOTE}: {evidence_line(z, ctx.tables)} / 비용 금액: {rnd_line}")
        res = _res("EXPENSED", evidence=ev, basis="current", refs=[_fact_ref(z)] + refs, kind="ledger_fact", heading=head)
        res["flags"] = ["expensed_current_year_only"]
        return res
    if ctx.meta.get("dev_asset_label"):
        return None  # 무형자산 주석 표에 개발비 행·열이 있는데 금액을 못 읽음(열 방향 변동표 등) — 「못 찾음」은 「없음」이 아니다
    ev = f"비용 금액: {rnd_line} / 무형자산 변동표에 당기 개발비 증가 없음"
    res = _res("EXPENSED", evidence=ev, basis="current", refs=refs, kind="ledger_fact",
               heading=_heading((rnd_cur[0].get("source") or {}).get("table_id"), ctx.tables))
    res["flags"] = ["dev_expensed_by_amount"]
    return res


def _dev(ctx: Ctx) -> dict | None:
    topic = "DEV_COST_CAPITALIZATION"
    add = _pos(ctx.facts, "DEV_COST_ADDITION")
    rnd_cur = [f for f in _pos_abs(ctx.facts, "RND_EXPENSE") if _side(f) == "current"]
    asset = _pos(ctx.facts, "DEV_COST_ASSET")
    expensed = _dev_expensed_by_amount(ctx, add, asset, rnd_cur)
    if expensed:
        return expensed
    if add:
        res = _from_facts("CAPITALIZED", add, ctx.tables)
        if rnd_cur and [f for f in add if _side(f) == "current"]:
            # c14(김현명 D3): 자산화와 비용 처리가 함께 확인되면 둘 다 보인다 — 표시만
            res["evidence"] += " / 비용 처리도 확인: " + " / ".join(evidence_line(f, ctx.tables) for f in _top(rnd_cur))
        return res
    stmt = _dev_expensed(ctx)
    if stmt and not asset:
        # 문장 근거라 기간 사실이 없다 — 이 보고서의 문장이므로 basis_period 는 당기(I2)
        ev = (stmt.get("text") or "").strip()[:300]
        rnd = _pos(ctx.facts, "RND_EXPENSE")
        cur = [f for f in rnd if _side(f) == "current"] or rnd
        refs = [stmt.get("sentence_id")]
        if cur:  # c13(합의 규칙 17, s067·s069): 비용 처리면 그 금액을 보인다 — 표시만
            shown = sorted(cur, key=lambda f: -abs(_num(f.get("amount"))))[:2]
            ev += " / 비용 금액: " + " / ".join(evidence_line(f, ctx.tables) for f in shown)
            refs += [_fact_ref(f) for f in shown]
        return _res("EXPENSED", evidence=ev, basis="current", refs=refs, kind="company_statement")
    exc = [m for m in _topic_marks(ctx.marks, topic) if m.get("exception") == "dev_amortization"]
    movement = ctx.meta.get("movement_table")
    if movement is None:
        movement = bool(_usable(ctx.facts, "DEV_COST_ADDITION") or _usable(ctx.facts, "DEV_COST_AMORT"))
    if exc:
        if asset and not movement:
            # 증가액을 확인할 변동표가 없을 때만 「○년 상각」 예외를 자산화 근거로 쓴다(Q5·스펙 §6.2 해석)
            res = _from_facts("CAPITALIZED", asset, ctx.tables, extra_refs=[exc[0].get("sentence_id")])
            res["evidence"] = f"{res['evidence']} / {(exc[0].get('text') or '').strip()[:200]}"
            return res
        return undetermined(False, ctx.reasons["dev_amort_no_addition"])
    if asset or _pos(ctx.facts, "DEV_COST_AMORT"):
        return ctx.und(topic, "dev_balance_only")
    if _pos(ctx.facts, "RND_EXPENSE"):
        return ctx.und(topic, "dev_rnd_only")
    return ctx.und(topic, None)


def _lease_value(short: bool, low: bool) -> str | None:
    return "BOTH" if short and low else "SHORT_TERM" if short else "LOW_VALUE" if low else None


_LEASE_CONCEPTS = ("LEASE_SHORT_TERM_EXP", "LEASE_LOW_VALUE_EXP", "LEASE_SHORT_LOW_EXP")
_LEASE_POLICY_RE = re.compile(r"(단기|소액)[^.]{0,80}(면제|제외|정액|비용)|(면제|제외)[^.]{0,60}(단기|소액)")
_COMBINED_NOTE = "단기·소액 합산 공시, 개별 금액 구분 불가"


def _lease_policy(ctx: Ctx) -> dict | None:
    """리스 정책 문장(단기·소액 인식면제) 표시 하나 — 현금유출액 문장은 빼고."""
    return next((m for m in _topic_marks(ctx.marks, "LEASE_EXEMPTION") if m.get("kind") == "policy"
                 and "현금유출" not in (m.get("text") or "") and _LEASE_POLICY_RE.search(m.get("text") or "")), None)


def _lease(ctx: Ctx) -> dict | None:
    res = _lease_core(ctx)
    pol = _lease_policy(ctx)
    if res and res["value"] != UND and pol:  # c13: 정책 문장을 보조 근거로 보인다(두 분 요청) — 표시만
        res["evidence"] = f"{res['evidence']} / 정책: {(pol.get('text') or '').strip()[:200]}"
        res["source_refs"] = list(res["source_refs"]) + [pol.get("sentence_id")]
        missing = {"SHORT_TERM": "소액", "LOW_VALUE": "단기"}.get(res["value"])
        if missing and _lease_policy_both(pol.get("text") or ""):
            # c14(김현명 D4): 값은 금액대로. 정책은 둘 다인데 한쪽 금액만 있으면 다른 쪽은 「미적용」이 아니라 「미확인」
            res["evidence"] += f" / 정책은 단기·소액 모두 면제 / {missing}: 실제 적용 내역 미확인"
            res["flags"] = list(res.get("flags") or []) + ["lease_policy_both"]
    if res and res["value"] != UND and res.get("cash_outflow"):
        # c14(김현명 C2): 현금유출액 문장 속 지급액으로 정한 값 — 비용 인식액으로 쓰지 않는다
        res["evidence"] += " / 정책+지급액(현금유출) 근거 · 비용 인식액 미확인" if pol else " / 현금유출액 근거·비용 인식액 미확인"
        res["flags"] = list(res.get("flags") or []) + ["lease_cash_outflow"]
    return res


# 정책 문장에서 그 유형에 면제를 적용한다는 말(「단기리스에는 적용하지 않고」 같은 부정은 아님, 리뷰 c14 #7)
_LEASE_SHORT_RE = re.compile(r"단기(?![^.,]{0,30}(적용하지|제외하지)\s*않)[^.]{0,80}(면제|제외|정액|비용)|(면제|제외)[^.]{0,60}단기")
_LEASE_LOW_RE = re.compile(r"소액(?![^.,]{0,30}(적용하지|제외하지)\s*않)[^.]{0,80}(면제|제외|정액|비용)|(면제|제외)[^.]{0,60}소액")


def _lease_policy_both(text: str) -> bool:
    """정책 문장이 단기리스와 소액자산리스를 둘 다 말하는가."""
    return bool(_LEASE_SHORT_RE.search(text) and _LEASE_LOW_RE.search(text))


def _lease_core(ctx: Ctx) -> dict | None:
    topic = "LEASE_EXEMPTION"
    # c13(합의 규칙 28·29): 현금유출액 문장 속 단기·소액 리스료(문장 사실)는 표 사실이 하나도 없을 때만 쓴다.
    # c14(김현명 C2): 정책 문장이 없어도 보조 근거로 쓴다(_lease 가 「비용 인식액 미확인」을 붙인다). 총 현금유출액은 2층에서 읽지 않는다
    lease = [f for f in ctx.facts if f.get("concept") in _LEASE_CONCEPTS]
    sent = [f for f in lease if f.get("match_basis") == "sentence"]
    table = [f for f in lease if f.get("match_basis") != "sentence"]
    keep = table if [f for f in table if _side(f)] else sent
    res = _lease_value_res(ctx, [f for f in ctx.facts if f.get("concept") not in _LEASE_CONCEPTS] + keep)
    if res and keep is sent and sent:
        res["cash_outflow"] = True
    return res


def _lease_value_res(ctx: Ctx, facts: list[dict]) -> dict | None:
    topic = "LEASE_EXEMPTION"
    s_all, l_all = _usable(facts, "LEASE_SHORT_TERM_EXP"), _usable(facts, "LEASE_LOW_VALUE_EXP")
    c_all = _usable(facts, "LEASE_SHORT_LOW_EXP")
    # 리스료는 비용이라 괄호(음수)로 적는 회사가 많다 — 부호와 무관하게 금액이 있으면 면제 적용(회계사 v2 s083 등)
    s, lo, c = _pos_abs(facts, "LEASE_SHORT_TERM_EXP"), _pos_abs(facts, "LEASE_LOW_VALUE_EXP"), \
        _pos_abs(facts, "LEASE_SHORT_LOW_EXP")
    value = _lease_value(bool(s), bool(lo))
    if value:
        res = _from_facts(value, s + lo, ctx.tables)
        if value == "BOTH":
            res["evidence"] = _join_sides([(evidence_line(_pick(x), ctx.tables), _side(_pick(x))) for x in (s, lo)])
            # 두 칸이 같은 기간에 함께 있을 때만 그 기간을 쓴다
            sides = {_side(f) for f in s} & {_side(f) for f in lo}
            res["basis_period"] = "current" if "current" in sides else ("prior" if "prior" in sides else
                                                                         res["basis_period"])
        return res
    if c:
        if s_all or l_all:
            return _review(f"단기·소액 합산 행은 있으나 개별 행은 0: {evidence_line(_pick(c), ctx.tables)}")
        # c13(합의 규칙 27, v2q Q5): 합친 한 줄만 — 면제 적용, 개별 금액 구분 불가. 값 이름은 C2 답을 기다리며 지금 값
        # (BOTH)을 두고, 나중에 이름을 바꿀 수 있게 표지 lease_combined_row 를 단다
        res = _from_facts("BOTH", c, ctx.tables)
        res["evidence"] = f"{res['evidence']} — {_COMBINED_NOTE}"
        res["flags"] = ["lease_combined_row"]
        return res
    if ((s_all and l_all) or c_all) and not all(f.get("match_basis") == "sentence" for f in s_all + l_all + c_all):
        # 「적용 안 함」은 표의 0·「-」 칸으로만 — 현금유출액 문장은 면제 적용의 보조 근거일 뿐이다(C2, 리뷰 c14 #6)
        res = _from_facts("NONE", s_all + l_all + c_all, ctx.tables)
        res["evidence"] = "단기·소액리스료 칸이 0 또는 「-」: " + " / ".join(
            evidence_line(f, ctx.tables) for f in ([_pick(s_all), _pick(l_all)] if s_all and l_all else [_pick(c_all)]))
        return res
    if s_all or l_all:
        return ctx.und(topic, "lease_partial_zero")
    return ctx.und(topic, None)


def _ip_pairs(car: list[dict], fv: list[dict]) -> list[tuple[dict, dict]]:
    """같은 표·같은 행(행×열 표)의 장부금액·공정가치 짝, 없으면 같은 기간의 가장 큰 값끼리."""
    pairs = []
    for c in car:
        cs = c.get("source") or {}
        for f in fv:
            fs = f.get("source") or {}
            if (cs.get("table_id"), cs.get("row"), _side(c)) == (fs.get("table_id"), fs.get("row"), _side(f)):
                pairs.append((c, f))
    if pairs:
        return pairs
    for side in ("current", "prior"):
        cc = [x for x in car if _side(x) == side]
        ff = [x for x in fv if _side(x) == side]
        if cc and ff:
            pairs.append((max(cc, key=lambda x: _num(x.get("amount_krw") or x.get("amount"))),
                          max(ff, key=lambda x: _num(x.get("amount_krw") or x.get("amount")))))
    return pairs


def _ip_key(x: dict):
    return x.get("amount_krw") if x.get("amount_krw") is not None else x.get("amount")


def _ip_same(c: dict, f: dict) -> bool | None:
    """장부금액·공정가치가 같은가. 문장 공정가치(「15,857백만원」)는 문장 단위 한 칸 안이면 같다(반올림·절사 표기).
    문장 쪽인데 장부금액 원화 금액을 모르면 None(비교 불가)."""
    if f.get("match_basis") != "sentence":
        return _ip_key(c) == _ip_key(f)
    if c.get("amount_krw") is None or not f.get("amount") or f.get("amount_krw") is None:
        return None
    unit = f["amount_krw"] / f["amount"]
    return abs(c["amount_krw"] - f["amount_krw"]) < unit


def _ip_model(ctx: Ctx) -> list[dict]:
    """공정가치 모형 문장 사실 IP_FV_MODEL(c11, 2층 문장 사실) — 그 회사(범위)는 장부금액이 곧 공정가치다(r4)."""
    return [f for f in ctx.facts if f.get("concept") == "IP_FV_MODEL"]


_FV_APPROX_NOTE = "장부금액과 유사(근사) — 회사가 중요한 차이가 없다고 판단함 · 공정가치 금액은 별도로 제시하지 않음"


def _ip(ctx: Ctx) -> dict | None:
    topic = "IP_FAIR_VALUE"
    car, fv = _pos(ctx.facts, "IP_CARRYING"), _pos(ctx.facts, "IP_FAIR_VALUE")
    if not car:
        return ctx.und(topic, "ip_no_carrying" if fv else None)
    model = _ip_model(ctx)
    pairs = _ip_pairs(car, fv)
    if pairs:
        pairs.sort(key=lambda p: (_side(p[0]) != "current", -_num(p[0].get("amount_krw") or p[0].get("amount"))))
        c, f = pairs[0]
        same = _ip_same(c, f)
        line = f"{evidence_line(c, ctx.tables)} / {evidence_line(f, ctx.tables)}"
        if same is None:
            return _review(f"장부금액 단위를 몰라 문장 공정가치와 비교 못 함: {line}")
        value = "FV_EQUALS" if same else "FV_DIFFERS"
        if value == "FV_DIFFERS" and model:
            return _review(f"공정가치 모형({_fact_ref(model[0])})인데 장부금액과 공정가치가 다름: {line}")
        res = _from_facts(value, [c, f], ctx.tables)
        res["evidence"] = line
        res["basis_period"] = _side(c)
        return res
    cur_car = [x for x in car if _side(x) == "current"]
    if model and cur_car:  # 공정가치 모형: 당기말 장부금액이 공정가치이자 장부금액(회계사 v2 s120). 당기말 없으면 안 씀(수정 1차)
        best = _pick(cur_car)
        res = _from_facts("FV_EQUALS", [best], ctx.tables, extra_refs=[_fact_ref(model[0])])
        res["evidence"] = f"{evidence_line(best, ctx.tables)} / 공정가치 모형: {(model[0].get('text') or '').strip()[:200]}"
        return res
    nodiff = [f for f in ctx.facts if f.get("concept") == "IP_FV_NO_MATERIAL_DIFF"]
    if nodiff and cur_car:
        # c14(김현명 D5 b): 「차이가 중요하지 않다」 공시 → 장부금액과 유사(근사). 공정가치 금액은 없으니 쓰지 않고
        # 차액 0 으로도 보지 않는다 — 표지 fv_approx 로 직접 공시한 같음과 나눈다
        best = _pick(cur_car)
        res = _from_facts("FV_EQUALS", [best], ctx.tables, extra_refs=[_fact_ref(nodiff[0])])
        res["evidence"] = (f"{_FV_APPROX_NOTE}: {evidence_line(best, ctx.tables)} / "
                           f"{(nodiff[0].get('text') or '').strip()[:200]}")
        res["flags"] = ["fv_approx"]
        return res
    if nodiff:  # 당기말 장부금액이 없으면 근사로도 정하지 않는다
        return _review(f"공정가치 금액 없이 차이 없음 문장만 있음({_fact_ref(nodiff[0])}): "
                       f"{(nodiff[0].get('text') or '').strip()[:150]}")
    leadin = [f for f in ctx.facts if f.get("concept") == "IP_FV_LEADIN"]
    if leadin:  # 공정가치 표 머리 문장은 있는데 금액을 못 읽었다 — 공시 안 함이 아니다
        return _review(f"공정가치 공시 표의 금액을 읽지 못함({_fact_ref(leadin[0])}): {(leadin[0].get('text') or '').strip()[:150]}")
    blocked = [f for f in ctx.facts if f.get("concept") == "IP_FV_MODEL_BLOCKED"]
    if blocked:  # 모형 문장이 원가모형 근거와 충돌 — 공시 안 함으로 정하지 않는다(수정 4차 안전망)
        return _review(f"공정가치 모형 문장({_fact_ref(blocked[0])})이 원가모형 근거와 충돌: "
                       f"{(blocked[0].get('text') or '').strip()[:150]}")
    sent = [m for m in _topic_marks(ctx.marks, topic) if ctx.rx["ip_fv_sentence"].search(m.get("text") or "")]
    if sent:
        return _review(f"공정가치 금액이 표에 없고 문장({sent[0].get('sentence_id')})에만 있음")
    if fv:  # 공정가치는 있으나 장부금액과 같은 기간이 아님
        return ctx.und(topic, "ip_period_mismatch")
    return _from_facts("FV_NOT_DISCLOSED", car, ctx.tables)


# 완료·확정 공시 낱말. 아닌 것: 「미확정」 「완료되지 않아/아니하여」 「확정될 예정」 「확정되기 전」 「완료 여부」 「확정에 따라 변동될 수」
# 「확정급여」(리뷰 c14 #4)
_PPA_DONE_WORD = re.compile(r"(?<!미)(완료|확정)(?!\s*(되|하)지(는)?\s*(않|못|아니))(?!\s*될)(?!되기\s*전)(?!\s*전)(?!\s*여부)"
                            r"(?!\s*급여)(?!에\s*따라)")


def _ppa(ctx: Ctx) -> dict | None:
    topic = "PPA_MEASUREMENT_PERIOD"
    # v1 행은 범위(별도·연결)가 없다 — 실행기가 정한 한 범위(연결이 있으면 연결)에만 낸다
    if ctx.meta.get("ppa_scope") and ctx.meta["ppa_scope"] != ctx.fs_scope:
        return None
    v1 = ctx.meta.get("v1_ppa")
    if not v1 or (v1.get("value") or UND) == UND:
        prov = _ppa_provisional(ctx)
        done = _ppa_completed(ctx)
        if prov and done:
            return _review("잠정 문장과 완료 문장이 함께 있음",
                           f"확인 필요: 잠정 문장({prov['source_refs'][0]})과 완료 문장({done['source_refs'][0]})이 함께 있음")
        if prov or done:
            return prov or done
    if not v1:
        return ctx.und(topic, None)
    value = v1.get("value") or UND
    bc = _usable(ctx.facts, "BC_CONSIDERATION") + _usable(ctx.facts, "BC_NET_ASSETS_FV") + \
        _usable(ctx.facts, "BC_RELATED_COST")
    # 사업결합 표 확인은 공시 단위다(연결에만 있는 공시가 많다)
    if value in ("PROVISIONAL", "FINAL") and not bc and not ctx.meta.get("bc_filing"):
        return _review(f"옛 판정 {value}이나 {ctx.reasons['ppa_no_bc_table']}")
    v1_ev = v1.get("evidence") or ""
    if value == "FINAL":
        # c14(김현명 D6): 잠정금액 소급조정 사실만으로는 확정이 아니다 — 완료·확정 공시가 있어야 「확정—직접 확인」
        if not _PPA_DONE_WORD.search(v1_ev):
            return _review("소급조정 사실만 있고 완료·확정 공시 없음", f"확인 필요: 소급조정 사실만 있고 완료·확정 공시 없음: {v1_ev[:200]}")
        v1_ev = f"확정—직접 확인: {v1_ev}"
    res = _res(value, held=v1.get("held_reason") or "", evidence=v1_ev,
               kind=v1.get("sentence_kind"), heading=v1.get("evidence_heading_path") or "")
    if value == "FINAL":
        res["flags"] = ["ppa_final_direct"]
    res["evidence_period"] = None  # c13 수정 1차: 근거는 v1 문장 — 사업결합 표 기간(basis)으로 「전기 기준」을 붙이지 않는다
    if bc and value in ("PROVISIONAL", "FINAL"):
        res["source_refs"] = [_fact_ref(f) for f in bc]
        res["basis_period"] = _basis(bc)
    if value != UND and not res["basis_period"]:
        res["basis_period"] = "current"  # v1 문장은 이 보고서의 문장 — 당기(I2)
    return res


def _ppa_provisional(ctx: Ctx) -> dict | None:
    """r5(회계사 두 명 s136 코웨이): 「연결회사의 사업결합 회계처리는 당기말 현재 잠정금액을 보고한 것으로 …」 — 회사 문장
    (정책 표시로 잡혀도)이 당기말 잠정을 직접 말하고, 공시에 사업결합 표(이전대가·순자산 공정가치, 기간 무관)가 있으면
    PROVISIONAL. 측정기간 정책 형태(「완료되지 못한다면」)·확정으로 조정된 문장·관계기업 지분 문장은 아니다.
    v1 이 정한 값(FINAL·PROVISIONAL 등)은 바꾸지 않는다(_ppa 가 v1 UND·없음일 때만 부른다)."""
    pat, req, exc = ctx.rx["ppa_prov"]
    bc = [f for f in ctx.facts if f.get("concept") in ("BC_CONSIDERATION", "BC_NET_ASSETS_FV")
          and _num(f.get("amount")) != 0]
    if not (bc or ctx.meta.get("bc_table_filing")):
        return None
    for m in _topic_marks(ctx.marks, "PPA_MEASUREMENT_PERIOD"):
        for sent in _SENT_SPLIT_RE.split(m.get("text") or ""):
            if pat.search(sent) and req.search(sent) and not exc.search(sent):
                return _res("PROVISIONAL", evidence=sent.strip()[:300], basis="current",
                            refs=[m.get("sentence_id")] + [_fact_ref(f) for f in bc], kind="company_statement")
    return None


def _ppa_completed(ctx: Ctx) -> dict | None:
    """c13(두 분 규칙 23·31, s137 지란지교시큐리티): 「측정기간 내에 사업결합 회계처리를 완료하였습니다」 「매수가격배분이 완료」
    「… 확정되었습니다」 회사 문장(2층 PPA_COMPLETED_STMT) + 공시에 사업결합 표 → FINAL. 측정기간 1년 경과만으로는 정하지
    않는다(D6). v1 UND·없음일 때만."""
    done = [f for f in ctx.facts if f.get("concept") == "PPA_COMPLETED_STMT"]
    bc = [f for f in ctx.facts if f.get("concept") in ("BC_CONSIDERATION", "BC_NET_ASSETS_FV") and _num(f.get("amount")) != 0]
    if not done or not (bc or ctx.meta.get("bc_table_filing")):
        return None
    res = _res("FINAL", evidence=f"확정—직접 확인: {(done[0].get('text') or '').strip()[:300]}", basis="current",
               refs=[_fact_ref(done[0])] + [_fact_ref(f) for f in bc], kind="company_statement")
    res["flags"] = ["ppa_final_direct"]  # c14(김현명 D6): 측정기간 종료로 판단한 확정과 근거 유형을 나눈다
    return res


def _bc_cost(ctx: Ctx) -> dict | None:
    topic = "BUSINESS_COMBINATION_COST_CLASS"
    cost = _pos(ctx.facts, "BC_RELATED_COST")
    if not cost:
        return ctx.und(topic, None)
    found = {}
    for f in cost:
        text = " ".join([f.get("label") or "", *(f.get("label_path") or []), f.get("column") or ""])
        for v, rx in ctx.rx["bc_accounts"].items():
            if rx.search(text):
                found.setdefault(v, []).append(f)
    if len(found) == 1:
        (v, fs), = found.items()
        return _from_facts(v, fs, ctx.tables)
    if len(found) > 1:
        return _review("사업결합 관련 원가 계정이 영업·영업외 양쪽으로 나옴")
    return ctx.und(topic, "bc_cost_no_account")


# ---------------------------------------------------------------- 1층 표에서 읽는 meta

_TOKEN_NOISE_RE = re.compile(r"\s+|\([^)]*\)|\*\d*|^[\d.가-하]{0,3}[.)]")


def _header_token(text: str) -> str:
    return _TOKEN_NOISE_RE.sub("", text or "")


def _table_head(t: dict) -> str:
    region = t.get("region") or {}
    return " ".join([region.get("note_title") or "", t.get("title") or ""])


def _rf_blocks(t: dict, rules: dict) -> bool:
    """r5: 읽지 못한 표가 주제를 막는가 — 잔액표·범주표(제목에 장부금액·내역·범주…)만. 발행조건·계약 내용·변동·손익
    표는 막지 않는다(회계사 두 명 s039: 발행조건 표 하나로 판단불가). 제목이 없는 표는 무슨 표인지 몰라 막는다.
    막지 않는 표도 read_failed_any_topics 에 남아 「없음」 값(해당 없음·지정 안 함 등)은 계속 막는다."""
    title = _WS_RE.sub("", t.get("title") or "")
    if not title:
        return True
    if rules["rx"]["rf_nonbalance"].search(title):
        return False
    return bool(rules["rx"]["rf_balance"].search(title))


# 개발비 자산 행·열 이름(「경상연구개발비」 「개발비(판관비)」 같은 비용 이름은 빼고)
_INTANGIBLE_CIP = re.compile(r"건설중인")
_EXPENSE_NOTE = re.compile(r"판매비|관리비|영업비용|비용의성격|제조원가|매출원가|손익|수익|세무조정")
_DEV_ASSET_WORD = re.compile(r"^(?!.*(경상|연구|비용|판매|판관|제조|상각비))(.*개발비|.*개발중인)")
_FVOCI_WORD = re.compile(r"기타포괄손익(.?공정가치|인식|으로인식)|FVOCI")


_PL_TITLE = re.compile(r"(?<!당기)(?<!포괄)손익|수익|비용")


def _category_amount_table(t: dict) -> bool:
    """범주표로 근거에 댈 수 있는가: 숫자 칸이 있고, 주석·표 제목이 손익(금융손익·범주별 순손익) 표가 아니다."""
    head = _WS_RE.sub("", _table_head(t))
    if _PL_TITLE.search(head) and not re.search(r"장부|잔액", head):  # 「장부금액과 순손익」 표(s050 t032)는 잔액 표다
        return False
    return any(isinstance(c.get("value"), (int, float)) for r in t.get("rows") or [] for c in r.get("cells") or [])


def _category_has_fvoci(t: dict) -> bool:
    fv_cols = {c.get("col") for c in t.get("columns") or [] if c.get("role", "value") == "value"
               and any(_FVOCI_WORD.search(_WS_RE.sub("", h or "")) for h in c.get("header_path") or [])}
    for r in t.get("rows") or []:
        fv_row = any(_FVOCI_WORD.search(_WS_RE.sub("", x or "")) for x in [r.get("label"), *(r.get("label_path") or [])])
        if any(_num(c.get("value")) != 0 and (fv_row or c.get("col") in fv_cols) for c in r.get("cells") or []):
            return True
    return False


def scope_meta(tables: list[dict], fs_scope: str, *, rules_path: Path | None = None) -> dict:
    """1층 표 기록(한 공시) → judge_filing meta 의 표 정보(그 fs_scope 표만).

    tables: {table_id: 주석 번호·제목·종류·단위 배수}, movement_table: 무형자산 주석에 변동표(기초·기말·취득·상각 낱말
    중 min_words 개 이상)가 있는가, category_table: 금융상품 범주표가 있는가, read_failed_topics: 관련 주석
    표를 읽지 못한 주제.
    """
    rules = load_rules(rules_path)
    mv, cat = rules["movement_table"], rules["category_table"]
    mv_note = re.compile(mv["note"])
    mv_tokens = [re.compile(w) for w in mv["tokens"]]
    cat_note, cat_head, cat_row = re.compile(cat["note"]), re.compile(cat["head"]), re.compile(cat["row"])
    topic_notes = {t: re.compile(p) for t, p in rules["topic_notes"].items()}
    out = {"tables": {}, "movement_table": False, "category_table": False, "read_failed_topics": set(),
           "read_failed_any_topics": set(), "category_fvoci": False, "category_table_id": None, "dev_asset_label": False}
    for t in tables:
        if (t.get("fs_scope") or "") != fs_scope:
            continue
        region = t.get("region") or {}
        out["tables"][t.get("table_id")] = {"note_no": region.get("note_no"), "note_title": region.get("note_title"),
                                            "kind": region.get("kind"),
                                            "multiplier": (t.get("unit") or {}).get("multiplier")}
        note = region.get("note_title") or ""
        head = _table_head(t)
        if t.get("status") == "read_failed":
            hit = {k for k, rx in topic_notes.items() if rx.search(head)}
            out["read_failed_any_topics"].update(hit)
            if _rf_blocks(t, rules):
                out["read_failed_topics"].update(hit)
            continue
        labels = [r.get("label") or "" for r in t.get("rows") or []]
        headers = [h for c in t.get("columns") or [] for h in c.get("header_path") or []]
        if region.get("kind") == "note" and not _EXPENSE_NOTE.search(_WS_RE.sub("", note)) and \
                any(_DEV_ASSET_WORD.search(_WS_RE.sub("", x or "")) for x in labels + headers):
            out["dev_asset_label"] = True  # c14: 주석 표(비용 주석 밖)에 개발비(자산) 행·열이 있다 — 개발비 별도 주석도(리뷰 #5)
        if mv_note.search(note) and any(_INTANGIBLE_CIP.search(_WS_RE.sub("", x or "")) for x in labels + headers):
            out["dev_asset_label"] = True  # c14: 무형자산 주석의 건설중인(무형)자산 — 개발 진행 중 자산화일 수 있다(손 점검 30건 중 3건)
        if mv_note.search(note):
            # 머리글 낱말 통째로 맞춘다(「당기말」의 「기말」, 「상각누계액」의 「상각」 같은 부분 일치는 안 됨)
            tokens = {_header_token(x) for x in labels} | {_header_token(h) for h in headers}
            if all(any(rx.match(tok) for tok in tokens) for rx in mv_tokens):
                out["movement_table"] = True
        is_cat = cat_note.search(head) and (cat_head.search(t.get("title") or "") or any(cat_row.search(lb) for lb in labels))
        if is_cat:
            out["category_table"] = True  # 판정 조건(내재파생 해당 없음) — 수정 1차에서도 그대로
        # 근거 인용·「FVOCI 없음」 표시용(값에 안 씀): 판정 조건 범주표, 또는 주석·표 제목에 「범주」가 있거나 띄어 쓴
        # 「상각후원가 금융자산」 행이 있는 금융상품 주석 표. 금액 있는 범주 잔액 표만 인용한다(수정 1차)
        cite = is_cat or (cat_note.search(head) and ("범주" in _WS_RE.sub("", head)
                                                     or any(cat_row.search(_WS_RE.sub("", lb)) for lb in labels)))
        if cite and _category_amount_table(t):
            out["category_table_id"] = out["category_table_id"] or t.get("table_id")
            if _category_has_fvoci(t):  # c13: 범주표에 0 아닌 FVOCI 열·행이 있는가(열만 있고 모두 0이면 없음)
                out["category_fvoci"] = True
    return out


# ---------------------------------------------------------------- 공개 함수

def _runs(instruments) -> tuple:
    if isinstance(instruments, dict):
        return instruments.get("run1"), instruments.get("run2")
    return list(instruments or []), list(instruments or [])


def _row(rcept_no: str, fs_scope: str, topic: str, res: dict, meta: dict) -> dict:
    determined = res["value"] != UND
    return {
        "rcept_no": rcept_no, "corp_code": meta.get("corp_code"), "fiscal_year": meta.get("fiscal_year"),
        "topic": topic, "subject": "회사", "value": res["value"], "evidence": res["evidence"],
        "method": METHOD, "confidence": 1.0 if determined else 0.0, "block_offset": None,
        "sentence_kind": res.get("sentence_kind"), "sentence_subject": "회사",
        "held_reason": "" if determined else (res.get("held_reason") or ""),
        "evidence_heading_path": res.get("heading") or "", "is_conditional": False,
        "parser_version": PARSER_VERSION, "fs_scope": fs_scope, "basis_period": res.get("basis_period"),
        "source_refs": [r for r in res.get("source_refs") or [] if r], "review_note": res.get("review_note"),
        **({"value_flags": list(res["flags"])} if res.get("flags") else {}),
    }


_CONV_FROZEN_NOTE = "c12 근거 변경 — 전환권 규칙 결정 대기"


def _conv_frozen(ctx: Ctx, runs: tuple, facts: list[dict], marks, meta: dict, rules: dict, fs_scope: str) -> dict | None:
    """r5 수정 1차: 전환권은 회계사 의견이 갈린 주제라 이번 판에서 값을 c11 에 묶는다. c12 가 손익 열 칸을 판정에서 뺀
    (DERIV_PL_COLUMN 대리 사실) 것 때문에 값·사유가 c11 과 달라지면 확인 필요로 둔다."""
    res = _consensus(ctx, "CONVERSION_OPTION_CLASS", runs, _conv_run)
    proxy = [f for f in facts if f.get("concept") == "DERIV_PL_COLUMN" and f.get("orig_concept")]
    if not proxy:
        return res
    restored = [f for f in facts if f.get("concept") != "DERIV_PL_COLUMN"] + \
        [{**f, "concept": f["orig_concept"]} for f in proxy]
    ctx11 = Ctx(restored, ctx.marks, meta, rules, fs_scope)
    ctx11.targeted_no_records = ctx.targeted_no_records
    old = _consensus(ctx11, "CONVERSION_OPTION_CLASS", runs, _conv_run)
    key = lambda r: None if r is None else (r["value"], r["held_reason"])  # noqa: E731
    if key(old) == key(res):
        return res
    was = "행 없음" if old is None else old["value"] + (f"/{old['held_reason']}" if old["held_reason"] else "")
    return _review(_CONV_FROZEN_NOTE, f"확인 필요: {_CONV_FROZEN_NOTE} (c11 손익 열 사실 포함 시 {was})")


def judge_filing(rcept_no: str, fs_scope: str, facts: list[dict], instruments, marks: list[dict],
                 meta: dict | None = None, *, rules_path: Path | None = None) -> list[dict]:
    """공시 하나 × fs_scope → 주제마다 한 행(근거가 전혀 없고 정책 문장도 없는 주제는 행 없음)."""
    meta = meta or {}
    rules = load_rules(rules_path)
    facts = [f for f in facts if (f.get("fs_scope") or fs_scope) == fs_scope]
    ctx = Ctx(facts, list(marks or []), meta, rules, fs_scope)
    runs = _runs(instruments)
    ctx.targeted_no_records = bool(meta.get("instruments_targeted")) and not (runs[0] or runs[1])
    judged = {
        "CONVERSION_OPTION_CLASS": _conv_frozen(ctx, runs, facts, marks, meta, rules, fs_scope),
        "EMBEDDED_DERIVATIVE_SEPARATION": _consensus(ctx, "EMBEDDED_DERIVATIVE_SEPARATION", runs, _emb_run),
        "FVOCI_DESIGNATION": _fvoci(ctx),
        "DEV_COST_CAPITALIZATION": _dev(ctx),
        "LEASE_EXEMPTION": _lease(ctx),
        "IP_FAIR_VALUE": _ip(ctx),
        "PPA_MEASUREMENT_PERIOD": _ppa(ctx),
        "BUSINESS_COMBINATION_COST_CLASS": _bc_cost(ctx),
    }
    failed = set(meta.get("read_failed_topics") or ())
    # r5: 없음에 기댄 값(해당 없음 등)은 어떤 표든 못 읽었으면 여전히 막는다 — 잔액·범주표가 아닌 표를 못 읽은 것으로
    # 새 「없음」 값이 생기지 않게(그 값들의 규칙은 회계사 의견이 갈려 이번 범위 밖)
    failed_any = failed | set(meta.get("read_failed_any_topics") or ())
    absence = rules["read_failed_absence_values"]
    out = []
    for topic in TOPICS:
        res = judged[topic]
        if res is None and topic in failed and _topic_marks(ctx.marks, topic):
            res = _res(UND, held="read_failed", evidence=ctx.reasons["read_failed"])
        if res is None:
            continue
        if (topic in failed and res["value"] == UND and res["held_reason"] in ("no_facts", "policy_only")) \
                or (topic in failed_any and res["value"] in absence.get(topic, ())):
            # 없음에 기댄 값(해당 없음 등)도 관련 주석 표를 읽지 못했으면 정할 수 없다(I1)
            res = _res(UND, held="read_failed", evidence=ctx.reasons["read_failed"])
        ep = res["evidence_period"] if "evidence_period" in res else res.get("basis_period")
        if res["value"] != UND and ep == "prior" and not res["evidence"].startswith("당기에 없음"):
            # c13(합의 규칙 3, v2q Q2): 당기 근거가 없고 전기 근거로 정한 값은 그렇게 밝힌다
            res = {**res, "evidence": f"당기에 없음, 전기 기준: {res['evidence']}"}
        out.append(_row(rcept_no, fs_scope, topic, res, meta))
    return out
