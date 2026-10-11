"""3층 규칙표 판정(judge_rules.py) 테스트 — 사실·기록·표시는 손으로 만든 dict 다."""
import unittest

from dart_search_mcp.ledger.judge_rules import (
    evidence_line, has_policy, host, judge_filing, undetermined,
)

R = "20260318001076"
TOPICS = ("CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION", "FVOCI_DESIGNATION",
          "DEV_COST_CAPITALIZATION", "LEASE_EXEMPTION", "IP_FAIR_VALUE", "PPA_MEASUREMENT_PERIOD",
          "BUSINESS_COMBINATION_COST_CLASS")
_N = [0]


def F(concept, amount, period="current_end", label=None, *, target=None, basis=None, mult=1, tid=None,
      row=None, col=1, column=""):
    _N[0] += 1
    return {"rcept_no": R, "fs_scope": "separate", "concept": concept, "period": period, "amount": amount,
            "unit_kind": "krw", "amount_krw": None if amount is None else amount * mult,
            "label": label or concept, "label_path": [label or concept], "column": column,
            "target": target, "target_basis": basis,
            "source": {"table_id": tid or f"{R}:00760:t{_N[0]:03d}", "page": 1, "row": row or _N[0], "col": col},
            "source_status": "verified", "concepts_version": "c5"}


def M(topic, kind="policy", text="정책 문장입니다.", sid="s1", exc=None):
    return {"rcept_no": R, "topic": topic, "kind": kind, "sentence_id": sid, "text": text, "exception": exc}


def bal(**kw):
    out = {"face": None, "host_carrying": None, "derivative_liability": None, "derivative_asset": None,
           "equity_component": None}
    out.update(kw)
    return out


def I(option_class="unconfirmed", sep="unconfirmed", cur=None, prior=None, name="제3회 전환사채", ev=None):
    return {"instrument": name, "series_key": "CB-3", "kind": "CB",
            "balance": {"current_end": bal(**(cur or {})), "prior_end": bal(**(prior or {}))},
            "movement": {"current": [], "prior": []}, "option_class": option_class,
            "embedded_separation": sep, "separated_targets": [], "evidence": ev or {}, "matched": {},
            "matched_multiplier": {}, "issues": []}


META = {"corp_code": "00123456", "fiscal_year": 2025}


def judge(facts=(), ins=(), marks=(), **meta):
    m = dict(META)
    m.update(meta)
    ins = ins if isinstance(ins, dict) else list(ins)
    return {r["topic"]: r for r in judge_filing(R, "separate", list(facts), ins, list(marks), m)}


class HelperTest(unittest.TestCase):
    def test_host(self):
        self.assertTrue(host([F("HOST_LIABILITY", 5, "prior_end")]))
        self.assertFalse(host([F("HOST_LIABILITY", 0), F("HOST_LIABILITY", None, "prior_end")]))

    def test_has_policy(self):
        self.assertTrue(has_policy([M("LEASE_EXEMPTION")], "LEASE_EXEMPTION"))
        self.assertFalse(has_policy([M("LEASE_EXEMPTION", kind="company_statement")], "LEASE_EXEMPTION"))

    def test_undetermined(self):
        self.assertEqual(undetermined(True, None)["held_reason"], "policy_only")
        r = undetermined(False, "사채는 있으나 전환권 분류 근거 없음")
        self.assertEqual((r["value"], r["held_reason"], r["evidence"]),
                         ("UNDETERMINED", "no_facts", "사채는 있으나 전환권 분류 근거 없음"))
        self.assertIsNone(undetermined(False, None))
        # 사실은 있으나 판정 근거가 모자라면 정책 문장이 있어도 no_facts 다(policy_only 는 사실·기록이 모두 없을 때만).
        self.assertEqual(undetermined(True, "증가액 없이 잔액만 있음")["held_reason"], "no_facts")


class ConversionTest(unittest.TestCase):
    def test_host_liability_not_option_liability(self):
        # Review Focus 3: 전환사채·사채할인발행차금·전환권조정만 있고 파생상품부채 칸이 없음
        facts = [F("HOST_LIABILITY", 1000, label="전환사채"), F("HOST_LIABILITY", -50, label="사채할인발행차금"),
                 F("HOST_LIABILITY", -30, label="전환권조정")]
        r = judge(facts)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))
        self.assertEqual(r["evidence"], "사채는 있으나 전환권 분류 근거 없음")
        # 기록이 부채요소만 갖고 option_class 를 확인하지 못했으면 부채가 아니다
        r = judge(facts, [I(cur={"host_carrying": 920})])["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))

    def test_conv_both(self):
        ins = [I("equity", cur={"equity_component": 519263}),
               I("derivative_liability", cur={"derivative_liability": 700}, name="제4회 전환사채")]
        r = judge([], ins)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"], r["basis_period"]), ("BOTH", "", "current"))
        self.assertEqual(r["confidence"], 1.0)
        # 기록이 없으면 사실로
        r = judge([F("CONV_OPTION_EQUITY", 10), F("CONV_OPTION_LIABILITY", 3)])["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["value"], "BOTH")

    def test_conv_equity_from_facts_only_without_records(self):
        r = judge([F("CONV_OPTION_EQUITY", 519263, label="전환사채의 자본요소"), F("HOST_LIABILITY", 9)])
        self.assertEqual(r["CONVERSION_OPTION_CLASS"]["value"], "EQUITY")

    def test_unconfirmed_records_block_facts_fallback(self):
        # 기록이 있으면(모두 unconfirmed 라도) 사실로 메우지 않는다(R14·brief)
        r = judge([F("CONV_OPTION_EQUITY", 10)], [I()])["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))

    def test_conv_record_vs_fact_conflict_needs_review(self):
        r = judge([F("CONV_OPTION_LIABILITY", 40, label="전환권파생상품부채")],
                  [I("equity", cur={"equity_component": 5})])["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("EQUITY", r["review_note"])


class ConsensusTest(unittest.TestCase):
    def run2(self, a, b, facts=()):
        return judge(facts, {"run1": a, "run2": b})

    def test_agree(self):
        r = self.run2([I("equity", cur={"equity_component": 5})], [I("equity", cur={"equity_component": 5})])
        self.assertEqual(r["CONVERSION_OPTION_CLASS"]["value"], "EQUITY")

    def test_disagree_is_needs_review(self):
        r = self.run2([I("equity", cur={"equity_component": 5})],
                      [I("derivative_liability", cur={"derivative_liability": 5})])["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("EQUITY", r["review_note"])
        self.assertIn("LIABILITY", r["review_note"])

    def test_one_run_missing_with_confirmed_record_is_needs_review(self):
        r = self.run2([I("equity", cur={"equity_component": 5})], None)["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["held_reason"], "needs_review")

    def test_one_run_missing_without_confirmed_records_not_review(self):
        r = self.run2([I()], None, facts=[F("HOST_LIABILITY", 5)])["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["held_reason"], "no_facts")

    def test_both_missing_uses_facts(self):
        r = self.run2(None, None, facts=[F("CONV_OPTION_EQUITY", 5)])
        self.assertEqual(r["CONVERSION_OPTION_CLASS"]["value"], "EQUITY")


class EmbeddedTest(unittest.TestCase):
    def test_emb_whole_fvpl_is_not_separated(self):
        r = judge([F("HOST_LIABILITY", 9)], [I(sep="whole_fvpl")])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["value"], "NOT_SEPARATED")

    def test_separated_record(self):
        r = judge([], [I(sep="separated", cur={"derivative_liability": 7})])
        self.assertEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "SEPARATED")

    def test_strong_fact_separated(self):
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(전환권)", target="conversion", basis="label")
        self.assertEqual(judge([f])["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "SEPARATED")

    def test_weak_fact_alone_never_separated(self):
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채", target="conversion", basis="context")
        r = judge([f])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))

    def test_needs_review_conflict(self):
        # 기록은 분리 안 함인데 분리 금액(대상 확인된 파생상품부채)이 있음
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(조기상환청구권)", target="redemption", basis="label")
        r = judge([f], [I(sep="not_separated")])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("not_separated", r["review_note"])
        self.assertIn("파생상품부채(조기상환청구권)", r["review_note"])
        self.assertEqual(r["confidence"], 0.0)
        # 약한 사실도 확인된 not_separated 와 어긋나면 확인 필요
        w = F("EMB_DERIV_LIABILITY", 7, target="other", basis="default")
        r = judge([w], [I(sep="not_separated")])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["held_reason"], "needs_review")
        # 회사 문장은 「분리하지 않」인데 분리 금액이 있음
        s = M("EMBEDDED_DERIVATIVE_SEPARATION", "company_statement",
              "회사는 전환사채에 포함된 조기상환청구권을 주계약과 분리하지 않았습니다.", "s9")
        r = judge([f], [], [s])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["held_reason"], "needs_review")

    def test_not_applicable_requires_category_table(self):
        m = [M("EMBEDDED_DERIVATIVE_SEPARATION")]
        self.assertEqual(judge([], [], m, category_table=True)["EMBEDDED_DERIVATIVE_SEPARATION"]["value"],
                         "NOT_APPLICABLE")
        r = judge([], [], m)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["held_reason"], "policy_only")
        # 잔액 0(「-」)은 「없음」이다
        r = judge([F("HOST_LIABILITY", 0, "prior_end")], [], m, category_table=True)
        self.assertEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "NOT_APPLICABLE")
        for f in (F("HOST_LIABILITY", 5, "prior_end"), F("EMB_DERIV_ASSET", 5, "unknown")):
            r = judge([f], [], m, category_table=True)
            self.assertNotEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "NOT_APPLICABLE")
        r = judge([], [I()], m, category_table=True)
        self.assertNotEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "NOT_APPLICABLE")

    def test_compound_without_evidence_undetermined(self):
        r = judge([F("HOST_LIABILITY", 9)], [I(sep="unconfirmed")])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))


class FvociTest(unittest.TestCase):
    def test_fvoci_debt_only_undetermined(self):
        r = judge([F("FVOCI_DEBT", 100)])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))

    def test_designated(self):
        self.assertEqual(judge([F("FVOCI_EQUITY", 100)])["FVOCI_DESIGNATION"]["value"], "DESIGNATED")
        self.assertEqual(judge([F("FVOCI_EQUITY_RESERVE", -3)])["FVOCI_DESIGNATION"]["value"], "DESIGNATED")

    def test_total_only_never_designated(self):
        r = judge([F("FVOCI_FA_TOTAL", 100)])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))

    def test_not_designated(self):
        r = judge([F("FVPL_EQUITY", 100, label="당기손익-공정가치측정 지분상품")])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "NOT_DESIGNATED")
        # FVOCI 금융자산 총액이 있으면 그 안에 지분상품이 있을 수 있어 확정하지 않는다
        r = judge([F("FVPL_EQUITY", 100), F("FVOCI_FA_TOTAL", 5)])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "UNDETERMINED")


class DevTest(unittest.TestCase):
    EXP = "당사는 당기 중 개발비 인식요건을 충족하지 못하여 개발 관련 지출 전액을 경상개발비로 비용처리하였습니다."

    def test_dev_balance_only_undetermined(self):
        r = judge([F("DEV_COST_ASSET", 500), F("DEV_COST_AMORT", 50, "current_period")])["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["held_reason"], r["evidence"]),
                         ("UNDETERMINED", "no_facts", "증가액 없이 잔액만 있음"))

    def test_rnd_only_undetermined(self):
        r = judge([F("RND_EXPENSE", 500, "current_period")])["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["evidence"], "경상연구개발비만 있음")

    def test_capitalized(self):
        r = judge([F("DEV_COST_ADDITION", 0, "current_period"), F("DEV_COST_ADDITION", 30, "prior_period")])
        r = r["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("CAPITALIZED", "prior"))

    def test_dev_expensed_sentence_with_addition_is_capitalized(self):
        m = [M("DEV_COST_CAPITALIZATION", "company_statement", self.EXP, "s5")]
        r = judge([F("DEV_COST_ADDITION", 30, "current_period")], [], m)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "CAPITALIZED")
        r = judge([], [], m)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "EXPENSED")
        self.assertEqual(r["source_refs"], ["s5"])
        # 잔액이 있으면 비용 처리로 확정하지 않는다
        r = judge([F("DEV_COST_ASSET", 9)], [], m)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "UNDETERMINED")

    def test_generic_policy_sentence_not_expensed(self):
        s = ("개발활동 관련 비용 중 일정요건을 충족하는 미래의 경제적 효익이 확실한 비용은 개발비의 과목으로 무형자산으로 "
             "처리하며, 이외의 경우에는 경상개발비의 과목으로 판매비와관리비로 처리하고 있습니다.")
        s2 = "개발비의 인식 요건을 충족하지 못한 개발활동과 관련한 비용은 당기비용으로 처리하고 있습니다."
        r = judge([], [], [M("DEV_COST_CAPITALIZATION", text=s), M("DEV_COST_CAPITALIZATION", text=s2, sid="s2")])
        self.assertEqual(r["DEV_COST_CAPITALIZATION"]["held_reason"], "policy_only")
        # 같은 문단의 다른 문장에 과거형·금액이 있어도 그 문장은 일반 정책이다
        s3 = s2 + " 당기 중 연구개발비 1,234천원을 지출하였습니다."
        r = judge([], [], [M("DEV_COST_CAPITALIZATION", text=s3)])
        self.assertEqual(r["DEV_COST_CAPITALIZATION"]["held_reason"], "policy_only")

    def test_amortization_exception(self):
        e = M("DEV_COST_CAPITALIZATION", "exception", "개발비 | 5년 | 정액법", "t:x:r3", exc="dev_amortization")
        # 변동표가 있는데 증가액이 없으면 판단불가
        r = judge([F("DEV_COST_ASSET", 9)], [], [e], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["evidence"], "상각 정책은 있으나 증가액 없음")
        # 변동표가 없고 개발비 잔액이 있으면 자산화
        r = judge([F("DEV_COST_ASSET", 9)], [], [e], movement_table=False)["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("CAPITALIZED", "current"))
        # 변동표 여부를 모르면 사실로 짐작: 증가·상각 사실이 있으면 변동표가 있다
        r = judge([F("DEV_COST_ASSET", 9), F("DEV_COST_AMORT", 1, "current_period")], [], [e])
        self.assertEqual(r["DEV_COST_CAPITALIZATION"]["value"], "UNDETERMINED")


class LeaseTest(unittest.TestCase):
    def test_no_lease_liability_not_exempt(self):
        # Review Focus 5: 리스부채가 없다는 사실만으로 면제가 되지 않는다
        r = judge([], [], [M("LEASE_EXEMPTION")])["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "policy_only"))
        r = judge([F("LEASE_LIABILITY", 0), F("LEASE_LIABILITY", 0, "prior_end")], [], [M("LEASE_EXEMPTION")])
        self.assertEqual(r["LEASE_EXEMPTION"]["value"], "UNDETERMINED")
        r = judge([F("LEASE_LIABILITY", 500)], [], [M("LEASE_EXEMPTION")])
        self.assertEqual(r["LEASE_EXEMPTION"]["value"], "UNDETERMINED")

    def test_values(self):
        cp = "current_period"
        self.assertEqual(judge([F("LEASE_SHORT_TERM_EXP", 5, cp), F("LEASE_LOW_VALUE_EXP", 2, cp)])
                         ["LEASE_EXEMPTION"]["value"], "BOTH")
        self.assertEqual(judge([F("LEASE_SHORT_TERM_EXP", 5, cp), F("LEASE_LOW_VALUE_EXP", 0, cp)])
                         ["LEASE_EXEMPTION"]["value"], "SHORT_TERM")
        self.assertEqual(judge([F("LEASE_LOW_VALUE_EXP", 2, "prior_period")])["LEASE_EXEMPTION"]["value"],
                         "LOW_VALUE")
        self.assertEqual(judge([F("LEASE_SHORT_TERM_EXP", 0, cp), F("LEASE_LOW_VALUE_EXP", None, cp)])
                         ["LEASE_EXEMPTION"]["value"], "NONE")
        self.assertEqual(judge([F("LEASE_SHORT_LOW_EXP", 9, cp)])["LEASE_EXEMPTION"]["value"], "BOTH")

    def test_one_zero_column_is_not_none(self):
        r = judge([F("LEASE_SHORT_TERM_EXP", 0, "current_period")], [], [M("LEASE_EXEMPTION")])
        self.assertEqual(r["LEASE_EXEMPTION"]["value"], "UNDETERMINED")

    def test_combined_row_contradicted_needs_review(self):
        cp = "current_period"
        r = judge([F("LEASE_SHORT_LOW_EXP", 9, cp), F("LEASE_SHORT_TERM_EXP", 0, cp), F("LEASE_LOW_VALUE_EXP", 0, cp)])
        self.assertEqual(r["LEASE_EXEMPTION"]["held_reason"], "needs_review")


class IpTest(unittest.TestCase):
    def test_values(self):
        t = f"{R}:00760:t900"
        r = judge([F("IP_CARRYING", 100, tid=t, row=3, col=1), F("IP_FAIR_VALUE", 100, tid=t, row=3, col=2)])
        self.assertEqual(r["IP_FAIR_VALUE"]["value"], "FV_EQUALS")
        r = judge([F("IP_CARRYING", 100), F("IP_FAIR_VALUE", 150)])
        self.assertEqual(r["IP_FAIR_VALUE"]["value"], "FV_DIFFERS")
        r = judge([F("IP_CARRYING", 100)])
        self.assertEqual(r["IP_FAIR_VALUE"]["value"], "FV_NOT_DISCLOSED")
        r = judge([], [], [M("IP_FAIR_VALUE")])
        self.assertEqual(r["IP_FAIR_VALUE"]["held_reason"], "policy_only")

    def test_fair_value_only_in_sentence_needs_review(self):
        m = M("IP_FAIR_VALUE", "company_statement", "투자부동산의 공정가치는 1,234,567천원입니다.")
        r = judge([F("IP_CARRYING", 100)], [], [m])
        self.assertEqual(r["IP_FAIR_VALUE"]["held_reason"], "needs_review")

    def test_same_period_required(self):
        r = judge([F("IP_CARRYING", 100), F("IP_FAIR_VALUE", 100, "prior_end")])["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))


class BusinessCombinationTest(unittest.TestCase):
    def test_ppa_carried_from_v1(self):
        v1 = {"value": "PROVISIONAL", "evidence": "잠정 금액", "held_reason": "", "sentence_kind": "company_statement"}
        r = judge([F("BC_CONSIDERATION", 100, "current_period")], v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["evidence"]), ("PROVISIONAL", "잠정 금액"))
        # 사업결합 표(BC_* 사실)가 없으면 확인 필요
        r = judge([], v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        v1u = {"value": "UNDETERMINED", "evidence": "x", "held_reason": "conditional"}
        r = judge([], v1_ppa=v1u)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["held_reason"], "conditional")

    def test_bc_cost_class(self):
        f = F("BC_RELATED_COST", 30, "current_period", label="사업결합 관련 원가(판매비와관리비)")
        self.assertEqual(judge([f])["BUSINESS_COMBINATION_COST_CLASS"]["value"], "OPERATING")
        f = F("BC_RELATED_COST", 30, "current_period", label="사업결합관련원가 - 영업외비용")
        self.assertEqual(judge([f])["BUSINESS_COMBINATION_COST_CLASS"]["value"], "NON_OPERATING")
        f = F("BC_RELATED_COST", 30, "current_period", label="취득관련원가")
        r = judge([f])["BUSINESS_COMBINATION_COST_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))


class CommonTest(unittest.TestCase):
    def test_policy_only_all_topics(self):
        # Review Focus 5: 사실이 하나도 없고 정책 문장만 있으면 모든 주제가 판단불가/policy_only
        rows = judge([], [], [M(t) for t in TOPICS])
        self.assertEqual(set(rows), set(TOPICS))
        for t, r in rows.items():
            self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "policy_only"), t)
            self.assertTrue(r["evidence"])

    def test_no_marks_no_facts_no_rows(self):
        self.assertEqual(judge(), {})

    def test_basis_period_prior_when_only_prior(self):
        r = judge([F("FVOCI_EQUITY", 0), F("FVOCI_EQUITY", 70, "prior_end")])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "prior"))
        r = judge([F("FVOCI_EQUITY", 1), F("FVOCI_EQUITY", 70, "prior_end")])["FVOCI_DESIGNATION"]
        self.assertEqual(r["basis_period"], "current")
        r = judge([], [I("equity", prior={"equity_component": 5})])["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["basis_period"], "prior")

    def test_unknown_period_ignored(self):
        r = judge([F("FVOCI_EQUITY", 70, "unknown")], [], [M("FVOCI_DESIGNATION")])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "UNDETERMINED")

    def test_row_fields(self):
        r = judge([F("FVOCI_EQUITY", 70)])["FVOCI_DESIGNATION"]
        for k in ("rcept_no", "corp_code", "fiscal_year", "topic", "subject", "value", "evidence", "method",
                  "confidence", "block_offset", "sentence_kind", "sentence_subject", "held_reason",
                  "evidence_heading_path", "is_conditional", "parser_version", "fs_scope", "basis_period",
                  "source_refs", "review_note"):
            self.assertIn(k, r)
        self.assertEqual((r["subject"], r["method"], r["confidence"], r["block_offset"], r["is_conditional"],
                          r["parser_version"], r["fs_scope"], r["corp_code"], r["fiscal_year"]),
                         ("회사", "ledger_rules", 1.0, None, False, "ws8-v2", "separate", "00123456", 2025))
        self.assertEqual(len(r["source_refs"]), 1)
        self.assertRegex(r["source_refs"][0], r":t\d+:r\d+:c\d+$")

    def test_evidence_line_format(self):
        tid = f"{R}:00760:t087"
        f = F("CONV_OPTION_EQUITY", 519263, label="전환사채의 자본요소", mult=1000, tid=tid)
        tables = {tid: {"note_no": "15", "note_title": "사채", "kind": "note"}}
        self.assertEqual(evidence_line(f, tables), "전환사채의 자본요소 519,263천원 (당기말, 주석 15 사채)")
        self.assertEqual(evidence_line(f, {}), "전환사채의 자본요소 519,263천원 (당기말)")
        g = F("LEASE_SHORT_TERM_EXP", 1234567, "prior_period", label="단기리스료")
        self.assertEqual(evidence_line(g, {}), "단기리스료 1,234,567원 (전기)")
        r = judge([f], tables=tables)["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["evidence"], "전환사채의 자본요소 519,263천원 (당기말, 주석 15 사채)")
        self.assertEqual(r["evidence_heading_path"], "주석 15 사채")

    def test_record_evidence_line(self):
        tid = f"{R}:00760:t108"
        ins = [I("equity", cur={"equity_component": 519263},
                 ev={"balance.current_end.equity_component": [f"{tid}:r2:c1"], "option_class": [f"{tid}:r2:c1"]})]
        tables = {tid: {"note_no": "15", "note_title": "전환사채", "kind": "note", "multiplier": 1000}}
        r = judge([], ins, tables=tables)["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["evidence"], "제3회 전환사채 자본요소 519,263천원 (당기말, 주석 15 전환사채)")
        self.assertEqual(r["source_refs"], [f"{tid}:r2:c1"])

    def test_read_failed(self):
        r = judge([], [], [M("LEASE_EXEMPTION")], read_failed_topics={"LEASE_EXEMPTION"})["LEASE_EXEMPTION"]
        self.assertEqual(r["held_reason"], "read_failed")


if __name__ == "__main__":
    unittest.main()
