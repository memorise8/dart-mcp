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
  read_failed_topics          관련 주석 표를 읽지 못한 주제 집합
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
    """부호와 무관하게 금액이 있는 사실 — 괄호(음수)로 표시한 부채 금액(EMB_DERIV_*)용."""
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
    amt = ("-" if amount is None else _fmt_amount(amount)) + _fact_unit(fact)
    where = [_PERIOD_LABEL.get(fact.get("period") or "", "")]
    head = _heading((fact.get("source") or {}).get("table_id"), tables or {})
    if head:
        where.append(head)
    where = [w for w in where if w]
    label = (fact.get("label") or fact.get("concept") or "").strip()
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
                  facts: list[dict] = ()) -> dict:
    """기록 근거 결과. basis_period 는 근거 줄 칸의 기간(당기 우선), 그 칸이 비면 _period(기록 전체·facts)."""
    lines, refs, sides, head = [], [], set(), ""
    for rec, field in picks:
        side = _record_side(rec, field)
        line, r, h = _record_line(rec, field, side, tables)
        lines.append(line)
        refs.extend(r)
        sides.add(side)
        head = head or h
    basis = "current" if "current" in sides else ("prior" if "prior" in sides else None)
    basis = basis or _period([rec for rec, _ in picks], facts)
    ev = " / ".join(lines + ([extra_ev] if extra_ev else []))
    return _res(value, evidence=ev, basis=basis, refs=list(dict.fromkeys(refs)), kind="instrument_record",
                heading=head)


def _non_conversion(f: dict) -> bool:
    """내재파생 사실의 대상이 전환권이 아님이 분명한가 — 상환권(조기상환청구권 등)이거나, 행·경로·문맥이 정한
    「기타」(신종자본증권 등). 대상 낱말이 없어 붙은 other/default 와 대상 모름(None)은 분명하지 않다."""
    t = f.get("target")
    return t == "redemption" or (t == "other" and f.get("target_basis") not in (None, "default"))


def _equity_guard(ctx: Ctx, res: dict) -> dict:
    """EQUITY 판정에 같은 범위·기간 안 전환권 파생상품부채로 볼 금액이 있으면 확인 필요(d10, T11b W3·W4).
    CONV_OPTION_LIABILITY 금액(부호 무관), 또는 대상이 전환권이 아님이 분명하지 않은 EMB_DERIV_LIABILITY 금액이다.
    파생상품부채 기록(option_class=derivative_liability)이 없으면 부채·둘 다로 증명된 것은 아니어서 값을 정하지 않는다."""
    if res.get("value") != "EQUITY":
        return res
    li = [f for f in _usable(ctx.facts, "CONV_OPTION_LIABILITY") if _num(f.get("amount")) != 0] + \
        [f for f in _pos_abs(ctx.facts, "EMB_DERIV_LIABILITY") if not _non_conversion(f)]
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
        cls = ("BOTH" if feq and fli else "EQUITY" if feq else "LIABILITY" if fli else None)
        if cls:
            res = _from_facts(cls, feq + fli, ctx.tables)
            if cls == "BOTH":
                res["evidence"] = f"{evidence_line(_pick(feq), ctx.tables)} / {evidence_line(_pick(fli), ctx.tables)}"
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
    return _equity_guard(ctx, _from_records(value, picks, ctx.tables, facts=period_facts))


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

def _fvoci(ctx: Ctx) -> dict | None:
    topic = "FVOCI_DESIGNATION"
    eq = _pos(ctx.facts, "FVOCI_EQUITY")
    reserve = _nonzero(ctx.facts, "FVOCI_EQUITY_RESERVE")
    if eq or reserve:
        cur = [f for f in eq + reserve if _side(f) == "current"]
        return _from_facts("DESIGNATED", cur or (eq + reserve), ctx.tables)
    total = _pos(ctx.facts, "FVOCI_FA_TOTAL") + _nonzero(ctx.facts, "FVOCI_FA_RESERVE")
    fvpl = _pos(ctx.facts, "FVPL_EQUITY")
    if fvpl and not total:
        return _from_facts("NOT_DESIGNATED", fvpl, ctx.tables)
    if total:
        return ctx.und(topic, "fvoci_total_only")
    if _pos(ctx.facts, "FVOCI_DEBT") or _nonzero(ctx.facts, "FVOCI_DEBT_RESERVE"):
        return ctx.und(topic, "fvoci_debt_only")
    if _topic_facts(ctx, topic):
        return ctx.und(topic, "fvoci_no_equity")
    return ctx.und(topic, None)


def _dev_expensed(ctx: Ctx) -> dict | None:
    pat, req, exc = ctx.rx["dev_exp"]
    for m in _topic_marks(ctx.marks, "DEV_COST_CAPITALIZATION"):
        for sent in _SENT_SPLIT_RE.split(m.get("text") or ""):  # 문단 안 문장 하나 안에서만 본다
            if pat.search(sent) and req.search(sent) and not exc.search(sent):
                return {**m, "text": sent.strip()}
    return None


def _dev(ctx: Ctx) -> dict | None:
    topic = "DEV_COST_CAPITALIZATION"
    add = _pos(ctx.facts, "DEV_COST_ADDITION")
    if add:
        return _from_facts("CAPITALIZED", add, ctx.tables)
    asset = _pos(ctx.facts, "DEV_COST_ASSET")
    stmt = _dev_expensed(ctx)
    if stmt and not asset:
        # 문장 근거라 기간 사실이 없다 — 이 보고서의 문장이므로 basis_period 는 당기(I2)
        return _res("EXPENSED", evidence=(stmt.get("text") or "").strip()[:300], basis="current",
                    refs=[stmt.get("sentence_id")], kind="company_statement")
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


def _lease(ctx: Ctx) -> dict | None:
    topic = "LEASE_EXEMPTION"
    s_all, l_all = _usable(ctx.facts, "LEASE_SHORT_TERM_EXP"), _usable(ctx.facts, "LEASE_LOW_VALUE_EXP")
    c_all = _usable(ctx.facts, "LEASE_SHORT_LOW_EXP")
    s, lo, c = _pos(ctx.facts, "LEASE_SHORT_TERM_EXP"), _pos(ctx.facts, "LEASE_LOW_VALUE_EXP"), \
        _pos(ctx.facts, "LEASE_SHORT_LOW_EXP")
    value = _lease_value(bool(s), bool(lo))
    if value:
        res = _from_facts(value, s + lo, ctx.tables)
        if value == "BOTH":
            res["evidence"] = f"{evidence_line(_pick(s), ctx.tables)} / {evidence_line(_pick(lo), ctx.tables)}"
            # 두 칸이 같은 기간에 함께 있을 때만 그 기간을 쓴다
            sides = {_side(f) for f in s} & {_side(f) for f in lo}
            res["basis_period"] = "current" if "current" in sides else ("prior" if "prior" in sides else
                                                                         res["basis_period"])
        return res
    if c:
        if s_all or l_all:
            return _review(f"단기·소액 합산 행은 있으나 개별 행은 0: {evidence_line(_pick(c), ctx.tables)}")
        return _from_facts("BOTH", c, ctx.tables)
    if (s_all and l_all) or c_all:
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


def _ip(ctx: Ctx) -> dict | None:
    topic = "IP_FAIR_VALUE"
    car, fv = _pos(ctx.facts, "IP_CARRYING"), _pos(ctx.facts, "IP_FAIR_VALUE")
    if not car:
        return ctx.und(topic, "ip_no_carrying" if fv else None)
    pairs = _ip_pairs(car, fv)
    if pairs:
        pairs.sort(key=lambda p: (_side(p[0]) != "current", -_num(p[0].get("amount_krw") or p[0].get("amount"))))
        c, f = pairs[0]
        key = lambda x: x.get("amount_krw") if x.get("amount_krw") is not None else x.get("amount")  # noqa: E731
        value = "FV_EQUALS" if key(c) == key(f) else "FV_DIFFERS"
        res = _from_facts(value, [c, f], ctx.tables)
        res["evidence"] = f"{evidence_line(c, ctx.tables)} / {evidence_line(f, ctx.tables)}"
        res["basis_period"] = _side(c)
        return res
    sent = [m for m in _topic_marks(ctx.marks, topic) if ctx.rx["ip_fv_sentence"].search(m.get("text") or "")]
    if sent:
        return _review(f"공정가치 금액이 표에 없고 문장({sent[0].get('sentence_id')})에만 있음")
    if fv:  # 공정가치는 있으나 장부금액과 같은 기간이 아님
        return ctx.und(topic, "ip_period_mismatch")
    return _from_facts("FV_NOT_DISCLOSED", car, ctx.tables)


def _ppa(ctx: Ctx) -> dict | None:
    topic = "PPA_MEASUREMENT_PERIOD"
    # v1 행은 범위(별도·연결)가 없다 — 실행기가 정한 한 범위(연결이 있으면 연결)에만 낸다
    if ctx.meta.get("ppa_scope") and ctx.meta["ppa_scope"] != ctx.fs_scope:
        return None
    v1 = ctx.meta.get("v1_ppa")
    if not v1:
        return ctx.und(topic, None)
    value = v1.get("value") or UND
    bc = _usable(ctx.facts, "BC_CONSIDERATION") + _usable(ctx.facts, "BC_NET_ASSETS_FV") + \
        _usable(ctx.facts, "BC_RELATED_COST")
    # 사업결합 표 확인은 공시 단위다(연결에만 있는 공시가 많다)
    if value in ("PROVISIONAL", "FINAL") and not bc and not ctx.meta.get("bc_filing"):
        return _review(f"옛 판정 {value}이나 {ctx.reasons['ppa_no_bc_table']}")
    res = _res(value, held=v1.get("held_reason") or "", evidence=v1.get("evidence") or "",
               kind=v1.get("sentence_kind"), heading=v1.get("evidence_heading_path") or "")
    if bc and value in ("PROVISIONAL", "FINAL"):
        res["source_refs"] = [_fact_ref(f) for f in bc]
        res["basis_period"] = _basis(bc)
    if value != UND and not res["basis_period"]:
        res["basis_period"] = "current"  # v1 문장은 이 보고서의 문장 — 당기(I2)
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
    out = {"tables": {}, "movement_table": False, "category_table": False, "read_failed_topics": set()}
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
            out["read_failed_topics"].update(k for k, rx in topic_notes.items() if rx.search(head))
            continue
        labels = [r.get("label") or "" for r in t.get("rows") or []]
        if mv_note.search(note):
            # 머리글 낱말 통째로 맞춘다(「당기말」의 「기말」, 「상각누계액」의 「상각」 같은 부분 일치는 안 됨)
            tokens = {_header_token(x) for x in labels} | \
                {_header_token(h) for c in t.get("columns") or [] for h in c.get("header_path") or []}
            if all(any(rx.match(tok) for tok in tokens) for rx in mv_tokens):
                out["movement_table"] = True
        if cat_note.search(head) and (cat_head.search(t.get("title") or "") or any(cat_row.search(lb) for lb in labels)):
            out["category_table"] = True
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
    }


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
        "CONVERSION_OPTION_CLASS": _consensus(ctx, "CONVERSION_OPTION_CLASS", runs, _conv_run),
        "EMBEDDED_DERIVATIVE_SEPARATION": _consensus(ctx, "EMBEDDED_DERIVATIVE_SEPARATION", runs, _emb_run),
        "FVOCI_DESIGNATION": _fvoci(ctx),
        "DEV_COST_CAPITALIZATION": _dev(ctx),
        "LEASE_EXEMPTION": _lease(ctx),
        "IP_FAIR_VALUE": _ip(ctx),
        "PPA_MEASUREMENT_PERIOD": _ppa(ctx),
        "BUSINESS_COMBINATION_COST_CLASS": _bc_cost(ctx),
    }
    failed = set(meta.get("read_failed_topics") or ())
    absence = rules["read_failed_absence_values"]
    out = []
    for topic in TOPICS:
        res = judged[topic]
        if res is None and topic in failed and _topic_marks(ctx.marks, topic):
            res = _res(UND, held="read_failed", evidence=ctx.reasons["read_failed"])
        if res is None:
            continue
        if topic in failed and (res["value"] == UND and res["held_reason"] in ("no_facts", "policy_only")
                                or res["value"] in absence.get(topic, ())):
            # 없음에 기댄 값(해당 없음 등)도 관련 주석 표를 읽지 못했으면 정할 수 없다(I1)
            res = _res(UND, held="read_failed", evidence=ctx.reasons["read_failed"])
        out.append(_row(rcept_no, fs_scope, topic, res, meta))
    return out
