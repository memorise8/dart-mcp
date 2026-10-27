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

    def test_evidence_from_first_record_with_balance(self):
        ins = [I("equity", name="제1회 전환사채"), I("equity", prior={"equity_component": 5}, name="제2회 전환사채")]
        r = judge([], ins)["CONVERSION_OPTION_CLASS"]
        # c13: 전기 근거로 정한 값은 「당기에 없음, 전기 기준」을 앞에 붙인다(합의 규칙 3)
        self.assertTrue(r["evidence"].startswith("당기에 없음, 전기 기준: 제2회 전환사채 자본요소 5"))
        self.assertEqual(r["basis_period"], "prior")
        ins = [I(sep="separated", name="제1회"), I(sep="separated", cur={"derivative_liability": 9}, name="제2회")]
        r = judge([], ins)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertTrue(r["evidence"].startswith("제2회 파생상품부채 9"))
        self.assertEqual(r["basis_period"], "current")

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

    def test_no_row_vs_undetermined_is_not_review(self):
        # 1차는 줄은 있으나 기록 0개(사실도 없음 → 행 없음), 2차는 unconfirmed 기록뿐(판단불가) — 둘 다 확정 값 없음
        r = self.run2([], [I()])["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["held_reason"], "no_facts")

    def test_same_value_needs_review_in_either_run_wins(self):
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(조기상환청구권)", target="redemption", basis="label")
        r = judge([f], {"run1": [I(sep="unconfirmed")], "run2": [I(sep="not_separated")]})
        r = r["EMBEDDED_DERIVATIVE_SEPARATION"]
        # 1차 SEPARATED(사실) vs 2차 needs_review 는 값이 달라 확인 필요다. 같은 UNDETERMINED 끼리는 needs_review 가 이긴다.
        self.assertEqual(r["held_reason"], "needs_review")
        r = judge([F("HOST_LIABILITY", 9), F("CONV_OPTION_LIABILITY", 4)],
                  {"run1": [I()], "run2": [I("equity", cur={"equity_component": 5})]})["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["held_reason"], "needs_review")
        w = F("EMB_DERIV_LIABILITY", 7, target="other", basis="default")
        r = judge([w], {"run1": [I(sep="unconfirmed")], "run2": [I(sep="not_separated")]})
        self.assertEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["held_reason"], "needs_review")

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

    def test_separated_record_without_amount_cites_separation_evidence(self):
        # 분리 금액 칸은 없고 분리 문장만 — 그 상품을 기간 안에 가지고 있어야 한다(T11b W1: 파생상품 금액 사실)
        ins = [I(sep="separated", ev={"embedded_separation": ["s7", "s7"], "option_class": ["s1"]})]
        w = F("EMB_DERIV_LIABILITY", 9, "prior_end", target="other", basis="default")
        r = judge([w], ins)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["source_refs"]), ("SEPARATED", ["s7"]))
        self.assertEqual(r["evidence"], "당기에 없음, 전기 기준: 제3회 전환사채 주계약과 분리 (근거 s7)")

    def test_strong_fact_separated(self):
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(전환권)", target="conversion", basis="label")
        self.assertEqual(judge([f])["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "SEPARATED")

    def test_bracketed_negative_emb_fact_counts(self):
        f = F("EMB_DERIV_LIABILITY", -7, label="파생상품부채(전환권)", target="conversion", basis="label")
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
        # 무형자산 변동표가 없으면 비용 금액만으로는 정하지 않는다(c14 D3)
        r = judge([F("RND_EXPENSE", 500, "current_period")])["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["evidence"], "연구개발 관련 비용 금액은 있으나 당기 개발비 자산화 여부를 확인하지 못함")

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

    def test_ppa_bc_check_is_filing_level_and_one_scope(self):
        v1 = {"value": "FINAL", "evidence": "확정", "held_reason": ""}
        # 사업결합 표가 연결에만 있어도 별도 범위에서 확인 필요가 되면 안 된다(공시 단위 확인)
        r = judge([], v1_ppa=v1, bc_filing=True)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["value"], "FINAL")
        # v1 행은 범위가 없어 한 범위에만 낸다
        self.assertNotIn("PPA_MEASUREMENT_PERIOD", judge([], v1_ppa=v1, bc_filing=True, ppa_scope="consolidated"))
        self.assertIn("PPA_MEASUREMENT_PERIOD", judge([], v1_ppa=v1, bc_filing=True, ppa_scope="separate"))

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


SPPI = "내재파생상품을 포함하는 복합계약은 계약상 현금흐름이 원금과 이자로만 구성되어 있는지를 결정할 때 해당 복합계약 전체를 고려합니다."
WHOLE = ("전환우선주는 금융부채가 하나 이상의 내재파생상품을 포함하는 계약의 일부를 구성함에 따라 복합계약 전체를 "
         "당기손익-공정가치 측정 항목으로 지정하였고, 공정가치로 최초 측정되며 이후 공정가치로 재측정되고 있습니다.")
SEP_SENT = ("연결회사는 상환전환우선주 발행으로 수령한 금액 중 내재파생상품은 주계약인 부채요소로부터 분리하고 공정가치로 "
            "평가하여 당기손익으로 인식하였으며, 주계약인 부채요소는 상각후원가로 측정하고 관련 이자비용은 금융원가로 인식하였습니다.")
EMB = "EMBEDDED_DERIVATIVE_SEPARATION"
CONV = "CONVERSION_OPTION_CLASS"


class T11bPrecisionTest(unittest.TestCase):
    """T11b: 전체 자료 게이트의 틀린 3층 판정 4건(W1~W4)과 그 근처 문장·사실."""

    # ---- W1 넥셀: 분리 문장은 있으나 당기·전기 모두 상품 잔액 없음(전기 전량 전환) ----
    def nexel(self, **cur):
        rec = I(sep="separated", name="상환전환우선주", cur=cur or None, ev={"embedded_separation": ["s930"]})
        rec["movement"]["prior"] = ["converted"]
        return rec

    def test_w1_separation_sentence_without_holding_is_not_separated(self):
        facts = [F("CONV_OPTION_LIABILITY", 0, label=f"제{k}회 상환전환우선주 전환권 등", target="conversion")
                 for k in (1, 2)]
        marks = [M(EMB, "policy", SPPI, "s101"), M(EMB, "company_statement", SEP_SENT, "s930")]
        for ins in ([self.nexel()], {"run1": [self.nexel()], "run2": [self.nexel()]}):
            r = judge(facts, ins, marks, category_table=True)[EMB]
            self.assertIn(r["value"], ("UNDETERMINED", "NOT_APPLICABLE"))
            self.assertEqual(r["held_reason"], "no_facts")

    def test_w1_holding_by_record_balance_or_fact_keeps_separated(self):
        marks = [M(EMB, "company_statement", SEP_SENT, "s930")]
        self.assertEqual(judge([], [self.nexel(host_carrying=5)], marks)[EMB]["value"], "SEPARATED")
        rec = self.nexel()
        rec["balance"]["prior_end"]["face"] = 100  # 전기말에만 잔액(d6)
        self.assertEqual(judge([], [rec], marks)[EMB]["value"], "SEPARATED")
        for f in (F("CONV_OPTION_LIABILITY", 4), F("EMB_DERIV_LIABILITY", -3, target="other", basis="default"),
                  F("EMB_DERIV_ASSET", 2, "prior_end", target="redemption", basis="label")):
            self.assertEqual(judge([f], [self.nexel()], marks)[EMB]["value"], "SEPARATED", f["concept"])

    def test_w1_unrelated_cb_host_or_equity_is_not_holding(self):
        # 수정 1차: 전량 전환한 RCPS 의 분리 문장 + 다른 전환사채의 부채요소·자본요소 금액 → SEPARATED 아님
        marks = [M(EMB, "company_statement", SEP_SENT, "s930")]
        cb = I("equity", name="제5회 전환사채", cur={"host_carrying": 900, "equity_component": 50})
        for facts, ins in (([F("HOST_LIABILITY", 900, label="전환사채")], [self.nexel()]),
                           ([F("HOST_LIABILITY", 900, "prior_end"), F("CONV_OPTION_EQUITY", 50, label="전환권대가")],
                            [self.nexel()]),
                           ([F("HOST_LIABILITY", 900)], [self.nexel(), cb])):
            r = judge(facts, ins, marks)[EMB]
            self.assertNotEqual(r["value"], "SEPARATED")

    def test_w1_zero_or_unknown_period_fact_is_not_holding(self):
        for f in (F("EMB_DERIV_LIABILITY", 0), F("EMB_DERIV_LIABILITY", 9, "unknown"), F("FVOCI_EQUITY", 9)):
            r = judge([f], [self.nexel()])[EMB]
            self.assertEqual(r["value"], "UNDETERMINED", f["concept"])

    # ---- W2 페르소나: 분리 문장 + 복합계약 전체를 당기손익-공정가치로 지정한 문장(d8) ----
    def persona(self):
        return I(sep="separated", name="전환상환우선주부채", cur={"host_carrying": 11298892},
                 ev={"embedded_separation": ["s413"]})

    def test_w2_whole_contract_fvtpl_designation_conflicts_with_separation(self):
        marks = [M(EMB, "policy", SPPI, "s127"), M(EMB, "policy", WHOLE, "s202")]
        for ins in ([self.persona()], {"run1": [self.persona()], "run2": [self.persona()]}):
            r = judge([F("HOST_LIABILITY", 11298891696)], ins, marks)[EMB]
            self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
            self.assertIn("s202", r["review_note"])
            self.assertIn("s413", r["review_note"])
        # 분리 금액 사실(대상 확인)로 SEPARATED 가 될 때도 같다
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(전환권)", target="conversion", basis="label")
        r = judge([f], [], [M(EMB, "company_statement", WHOLE, "s202")])[EMB]
        self.assertEqual(r["held_reason"], "needs_review")
        self.assertIn("s202", r["review_note"])
        # 다른 주제로 표시된 문장이어도 같은 공시의 문장이면 본다
        r = judge([], [self.persona()], [M(CONV, "policy", WHOLE, "s202")])[EMB]
        self.assertEqual(r["held_reason"], "needs_review")

    def test_w2_designation_variants(self):
        for text in ("회사는 상환전환우선주 전체를 당기손익-공정가치측정금융부채로 지정하였습니다.",
                     "연결회사는 동 금융부채 전체를 당기손익-공정가치 측정 금융부채로 지정하고 있습니다.",
                     "회사는 복합금융상품 전체를 당기손익인식금융부채로 지정하였습니다."):
            r = judge([], [self.persona()], [M(EMB, "policy", text, "s9")])[EMB]
            self.assertEqual(r["held_reason"], "needs_review", text)

    def test_w2_near_miss_sentences_keep_separated(self):
        for text in (SPPI,
                     "내재파생상품을 포함하는 복합계약 전체를 당기손익-공정가치 측정 항목으로 지정할 수 있습니다.",
                     "복합계약 전체를 고려하여 당기손익-공정가치로 측정합니다.",
                     "전환사채의 전환권은 당기손익-공정가치 측정 금융부채로 지정하였습니다.",
                     "회사는 상환전환우선주의 전체 발행금액을 부채로 분류하였습니다."):
            r = judge([], [self.persona()], [M(EMB, "policy", text, "s9")])[EMB]
            self.assertEqual(r["value"], "SEPARATED", text)

    def test_w2_designation_without_separation_evidence_not_separated_value(self):
        # 분리 근거가 없으면 이 문장으로 SEPARATED 가 나오지 않는다(값을 새로 확정하지는 않음)
        r = judge([F("HOST_LIABILITY", 9)], [I(sep="unconfirmed")], [M(EMB, "policy", WHOLE, "s202")])[EMB]
        self.assertNotEqual(r["value"], "SEPARATED")

    # ---- W3·W4 케이이엠텍·다산: 자본 분류인데 같은 범위에 전환권 파생상품부채 금액 ----
    def eq_rec(self):
        return I("equity", name="전환사채", cur={"face": 5000000, "host_carrying": 5960135},
                 ev={"option_class": ["s269", "s272"]})

    def test_w4_equity_record_with_conversion_embedded_liability_needs_review(self):
        f = F("EMB_DERIV_LIABILITY", 87300, label="전환사채의 내재파생부채", target="conversion", basis="label",
              mult=1000)
        for ins in ([self.eq_rec()], {"run1": [self.eq_rec()], "run2": [self.eq_rec()]}):
            r = judge([F("HOST_LIABILITY", 5960134726), f], ins)[CONV]
            self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
            self.assertIn("s269", r["review_note"])
            self.assertIn("전환사채의 내재파생부채 87,300", r["review_note"])

    def test_w3_target_unknown_embedded_liability_blocks_equity(self):
        f = F("EMB_DERIV_LIABILITY", 2928440, label="내재파생금융상품부채", target="other", basis="default", mult=1000)
        r = judge([f], [self.eq_rec()])[CONV]
        self.assertEqual(r["held_reason"], "needs_review")
        f = F("EMB_DERIV_LIABILITY", 5, label="파생상품부채", target=None, basis=None)
        self.assertEqual(judge([f], [self.eq_rec()])[CONV]["held_reason"], "needs_review")
        # 기록 없이 사실만으로 EQUITY 가 될 때도 같다
        r = judge([F("CONV_OPTION_EQUITY", 10, label="전환권대가"), f])[CONV]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("전환권대가", r["review_note"])
        # 괄호(음수)로 적은 전환권 파생상품부채도 금액이다
        r = judge([F("CONV_OPTION_LIABILITY", -4, label="전환권파생상품부채")], [self.eq_rec()])[CONV]
        self.assertEqual(r["held_reason"], "needs_review")

    def test_non_conversion_or_absent_liability_does_not_block_equity(self):
        keep = [F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(조기상환청구권)", target="redemption", basis="label"),
                F("EMB_DERIV_LIABILITY", 7, label="신종자본증권 파생상품부채", target="other", basis="label"),
                F("EMB_DERIV_LIABILITY", 0, label="전환사채의 내재파생부채", target="conversion", basis="label"),
                F("EMB_DERIV_LIABILITY", 7, "unknown", label="전환사채의 내재파생부채", target="conversion",
                  basis="label"),
                F("EMB_DERIV_ASSET", 7, label="파생상품자산(전환권)", target="conversion", basis="label")]
        for f in keep:
            self.assertEqual(judge([f], [self.eq_rec()])[CONV]["value"], "EQUITY", f["label"])
            self.assertEqual(judge([F("CONV_OPTION_EQUITY", 10), f])[CONV]["value"], "EQUITY", f["label"])

    def test_unknown_period_conv_liability_does_not_block_equity(self):
        f = F("CONV_OPTION_LIABILITY", 40, "unknown", label="전환권파생상품부채")
        self.assertEqual(judge([f], [self.eq_rec()])[CONV]["value"], "EQUITY")
        self.assertEqual(judge([F("CONV_OPTION_EQUITY", 10), f])[CONV]["value"], "EQUITY")

    def test_currency_derivative_liability_table_does_not_block_equity(self):
        from dart_search_mcp.ledger.facts import extract_facts, load_concepts
        from tests.test_ledger_facts import _table
        t = _table([("파생금융상품부채(통화스왑)", [2, 2]), ("이자율스왑 파생상품부채", [3, 3]), ("통화선도 내재파생상품", [5, 5])],
                   note_title="파생상품", title="전환사채 관련 파생상품")
        facts = extract_facts([t], load_concepts())
        self.assertEqual(judge(facts, [self.eq_rec()])[CONV]["value"], "EQUITY")

    def test_derivative_liability_record_rules_unchanged(self):
        li = I("derivative_liability", cur={"derivative_liability": 700}, name="제4회 전환사채")
        f = F("EMB_DERIV_LIABILITY", 700, label="전환사채의 내재파생부채", target="conversion", basis="label")
        self.assertEqual(judge([f], [self.eq_rec(), li])[CONV]["value"], "BOTH")
        self.assertEqual(judge([f], [li])[CONV]["value"], "LIABILITY")


class FinalFixTest(unittest.TestCase):
    """최종 리뷰 수정(r3): I1 읽기 실패, I2 basis_period, I3(R34) 전환권 기간 근거, R35 대상 공시 기록 0."""

    def bare(self, option_class="equity", sep="unconfirmed", **kw):
        # 잔액·변동이 모두 비어 있는 기록(20260312001339/separate 꼴)
        return I(option_class, sep, name="제2회 무기명식 전환사채", **kw)

    # ---- I1: 없음에 기대는 값은 관련 주석 표를 읽지 못했으면 read_failed ----
    def test_i1_absence_values_become_read_failed(self):
        m = [M(EMB)]
        r = judge([], [], m, category_table=True, read_failed_topics={EMB})[EMB]
        self.assertEqual((r["value"], r["held_reason"], r["evidence"]),
                         ("UNDETERMINED", "read_failed", "관련 주석 표를 읽지 못함"))
        r = judge([F("IP_CARRYING", 100)], read_failed_topics={"IP_FAIR_VALUE"})["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "read_failed"))
        r = judge([F("FVPL_EQUITY", 30)], read_failed_topics={"FVOCI_DESIGNATION"})["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "read_failed"))
        s = M("DEV_COST_CAPITALIZATION", "company_statement", DevTest.EXP)
        self.assertEqual(judge([], [], [s])["DEV_COST_CAPITALIZATION"]["value"], "EXPENSED")
        r = judge([], [], [s], read_failed_topics={"DEV_COST_CAPITALIZATION"})["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "read_failed"))

    def test_i1_present_values_kept_when_read_failed(self):
        # 있는 금액·0 칸으로 정한 값은 그대로(다른 주제의 읽기 실패도 영향 없음)
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(전환권)", target="conversion", basis="label")
        self.assertEqual(judge([f], read_failed_topics={EMB})[EMB]["value"], "SEPARATED")
        r = judge([F("LEASE_SHORT_TERM_EXP", 0), F("LEASE_LOW_VALUE_EXP", 0)],
                  read_failed_topics={"LEASE_EXEMPTION"})["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "NONE")
        r = judge([F("IP_CARRYING", 100)], read_failed_topics={"LEASE_EXEMPTION"})["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_NOT_DISCLOSED")
        r = judge([], [], [M(EMB)], category_table=True, read_failed_topics={CONV})[EMB]
        self.assertEqual(r["value"], "NOT_APPLICABLE")

    # ---- I3(R34): 전환권 값은 당기·전기 잔액·변동·기간 아는 사실 중 하나가 있어야 ----
    def test_i3_conv_record_without_period_evidence_is_undetermined(self):
        for ins in ([self.bare()], [self.bare("derivative_liability")],
                    {"run1": [self.bare()], "run2": [self.bare()]}):
            r = judge([], ins)[CONV]
            self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))
            self.assertEqual(r["evidence"], "사채는 있으나 당기·전기 잔액·변동 근거 없음")
        # 기간 모르는 사실·0 금액은 근거가 아니다
        for f in (F("CONV_OPTION_EQUITY", 10, "unknown"), F("HOST_LIABILITY", 0, "prior_end")):
            self.assertEqual(judge([f], [self.bare()])[CONV]["value"], "UNDETERMINED", f["period"])

    def test_i3_near_miss_prior_movement_or_known_period_fact_keeps_value(self):
        rec = self.bare()
        rec["movement"]["prior"] = ["redeemed"]  # d6: 전기에 상환했어도 근거다
        r = judge([], [rec])[CONV]
        self.assertEqual((r["value"], r["basis_period"]), ("EQUITY", "prior"))
        rec = self.bare()
        rec["movement"]["current"] = ["converted"]
        self.assertEqual(judge([], [rec])[CONV]["basis_period"], "current")
        r = judge([F("HOST_LIABILITY", 900, "prior_end", label="전환사채")], [self.bare()])[CONV]
        self.assertEqual((r["value"], r["basis_period"]), ("EQUITY", "prior"))
        # 분류 칸이 0 이어도 다른 잔액 칸(부채요소)이 당기에 있으면 당기
        r = judge([], [self.bare(cur={"host_carrying": 5})])[CONV]
        self.assertEqual((r["value"], r["basis_period"]), ("EQUITY", "current"))

    def test_i3_period_from_uncited_record_of_same_class(self):
        # 재검토: 근거 줄은 첫 기록(잔액·변동 없음)인데 기간 근거는 다른 기록(전기 상환)에만 있음
        a = self.bare()
        b = self.bare()
        b["movement"]["prior"] = ["redeemed"]
        for ins in ([a, b], {"run1": [a, b], "run2": [a, b]}):
            r = judge([], ins)[CONV]
            self.assertEqual((r["value"], r["basis_period"]), ("EQUITY", "prior"))

    # ---- I2: 확정 행은 모두 basis_period 가 있다 ----
    def test_i2_basis_period_on_record_and_sentence_rows(self):
        # 분리 문장 기록 + 전기 파생상품 금액 사실 → 전기
        ins = [I(sep="separated", ev={"embedded_separation": ["s7"]})]
        w = F("EMB_DERIV_LIABILITY", 9, "prior_end", target="other", basis="default")
        self.assertEqual(judge([w], ins)[EMB]["basis_period"], "prior")
        rec = I(sep="separated", prior={"face": 100}, ev={"embedded_separation": ["s7"]})
        self.assertEqual(judge([], [rec])[EMB]["basis_period"], "prior")
        # 분리 안 함 기록: 잔액 → 변동 → 사실
        r = judge([F("HOST_LIABILITY", 9, "prior_end")], [I(sep="whole_fvpl")])[EMB]
        self.assertEqual((r["value"], r["basis_period"]), ("NOT_SEPARATED", "prior"))
        rec = I(sep="not_separated")
        rec["movement"]["current"] = ["issued"]
        self.assertEqual(judge([], [rec])[EMB]["basis_period"], "current")
        # 비용 처리 문장·해당 없음·v1 PPA 는 이 보고서 당기
        s = M("DEV_COST_CAPITALIZATION", "company_statement", DevTest.EXP)
        self.assertEqual(judge([], [], [s])["DEV_COST_CAPITALIZATION"]["basis_period"], "current")
        self.assertEqual(judge([], [], [M(EMB)], category_table=True)[EMB]["basis_period"], "current")
        v1 = {"value": "FINAL", "evidence": "확정", "held_reason": ""}
        r = judge([], v1_ppa=v1, bc_filing=True)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["basis_period"]), ("FINAL", "current"))

    def test_i2_not_separated_without_period_evidence_is_undetermined(self):
        r = judge([], [I(sep="not_separated", ev={"embedded_separation": ["s3"]})])[EMB]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))
        self.assertEqual(r["evidence"], "복합상품은 있으나 당기·전기 잔액·변동 근거 없음")

    # ---- R35: 사채·우선주 대상 공시인데 두 실행 모두 기록 0 → 해당 없음 대신 판단불가 ----
    def test_r35_targeted_zero_records_not_applicable_becomes_undetermined(self):
        m = [M(EMB)]
        for ins in ([], {"run1": [], "run2": []}, {"run1": [], "run2": None}, {"run1": None, "run2": None}):
            r = judge([], ins, m, category_table=True, instruments_targeted=True)[EMB]
            self.assertEqual((r["value"], r["held_reason"], r["evidence"]),
                             ("UNDETERMINED", "no_facts", "사채·우선주 대상 공시이나 회차 기록 없음"))

    def test_r35_near_miss_not_targeted_keeps_not_applicable(self):
        m = [M(EMB)]
        # 한 실행에만 기록이 있으면 R35 가 아니다(두 실행 값이 다르면 그대로 확인 필요)
        r = judge([], {"run1": [], "run2": [I()]}, m, category_table=True, instruments_targeted=True)[EMB]
        self.assertEqual(r["held_reason"], "needs_review")
        r = judge([], {"run1": None, "run2": None}, m, category_table=True, instruments_targeted=False)[EMB]
        self.assertEqual(r["value"], "NOT_APPLICABLE")
        # 대상이어도 다른 판정(분리 금액)은 그대로
        f = F("EMB_DERIV_LIABILITY", 7, label="파생상품부채(전환권)", target="conversion", basis="label")
        r = judge([f], {"run1": [], "run2": []}, m, category_table=True, instruments_targeted=True)[EMB]
        self.assertEqual(r["value"], "SEPARATED")


def T(tid, note_title, labels, *, title="", headers=(), status="verified", scope="separate", kind="note", mult=1000):
    return {"table_id": tid, "fs_scope": scope, "status": status, "title": title,
            "region": {"kind": kind, "note_no": "9", "note_title": note_title, "sub_path": []},
            "unit": {"raw": "(단위: 천원)", "kind": "krw", "multiplier": mult, "currency": "KRW"},
            "rows": [{"row": i, "label": lb, "label_path": [lb], "cells": []} for i, lb in enumerate(labels, 1)],
            "columns": [{"col": i, "header_path": [h]} for i, h in enumerate(headers, 1)]}


class ScopeMetaTest(unittest.TestCase):
    def test_scope_meta(self):
        from dart_search_mcp.ledger.judge_rules import scope_meta
        tables = [
            T("R:00760:t001", "무형자산", ["기초", "취득", "상각", "기말"], headers=["개발비", "소프트웨어"]),
            T("R:00760:t002", "금융상품", ["상각후원가측정금융자산", "합계"], title="범주별 금융상품"),
            T("R:00760:t003", "리스", [], status="read_failed"),
            T("R:00761:t001", "무형자산", ["개발비"], headers=["취득원가", "장부금액"], scope="consolidated"),
        ]
        m = scope_meta(tables, "separate")
        self.assertTrue(m["movement_table"])
        self.assertTrue(m["category_table"])
        self.assertEqual(m["read_failed_topics"], {"LEASE_EXEMPTION"})
        self.assertEqual(m["tables"]["R:00760:t001"],
                         {"note_no": "9", "note_title": "무형자산", "kind": "note", "multiplier": 1000})
        # 구성표(당기말 > 취득원가·상각누계액·장부금액)는 변동표가 아니다
        comp = [T("R:00760:t010", "무형자산", ["개발비", "소프트웨어", "합계"],
                  headers=["당기말", "취득원가", "상각누계액", "장부금액"])]
        self.assertFalse(scope_meta(comp, "separate")["movement_table"])
        comp2 = [T("R:00760:t011", "무형자산", ["전기말 장부금액", "당기말 장부금액"], headers=["개발비"])]
        self.assertFalse(scope_meta(comp2, "separate")["movement_table"])
        mv = [T("R:00760:t012", "무형자산", ["개발비", "합계"],
                headers=["기초", "취득(*1)", "상각", "기말"])]
        self.assertTrue(scope_meta(mv, "separate")["movement_table"])
        mv2 = [T("R:00760:t013", "무형자산", ["기초금액", "대체", "무형자산상각비", "기말금액"], headers=["개발비"])]
        self.assertTrue(scope_meta(mv2, "separate")["movement_table"])
        mv3 = [T("R:00760:t014", "무형자산", ["개발비"],
                 headers=["기초장부금액", "취득(자본적지출포함)액", "상각비", "기말 장부금액"])]
        self.assertTrue(scope_meta(mv3, "separate")["movement_table"])
        mv4 = [T("R:00760:t015", "무형자산", ["당기초", "개별취득", "상각", "당기말"], headers=["개발비", "합계"])]
        self.assertTrue(scope_meta(mv4, "separate")["movement_table"])
        # 기초·증가만 있고 기말 낱말이 없으면 변동표로 보지 않는다(기초·기말·증가 모두 필요)
        mv5 = [T("R:00760:t016", "무형자산", ["개발비"], headers=["기초", "취득", "상각"])]
        self.assertFalse(scope_meta(mv5, "separate")["movement_table"])
        c = scope_meta(tables, "consolidated")
        self.assertFalse(c["movement_table"])
        self.assertFalse(c["category_table"])
        self.assertNotIn("R:00760:t001", c["tables"])


class CliTest(unittest.TestCase):
    def test_cli_end_to_end(self):
        import json
        import tempfile
        from pathlib import Path
        import importlib.util
        spec = importlib.util.spec_from_file_location("ledger_judge_cli",
                                                      Path(__file__).parents[1] / "scripts" / "ledger_judge.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        main = cli.main

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            led = d / "ledger"
            led.mkdir()
            cbc = dict(F("BC_CONSIDERATION", 100, "current_period"), fs_scope="consolidated")
            facts = [F("CONV_OPTION_EQUITY", 5), F("FVOCI_EQUITY", 7, "prior_end"), cbc]
            (led / "facts.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in facts))
            (led / "policy_marks.jsonl").write_text(json.dumps(M("LEASE_EXEMPTION"), ensure_ascii=False) + "\n")
            (d / "audit.jsonl").write_text(json.dumps({"rcept_no": R, "corp_code": "00999999", "fiscal_year": 2025}) + "\n")

            def run_row(recs, model="gpt-5.6-luna"):
                return json.dumps({"rcept_no": R, "fs_scope": "separate", "records": recs, "model_used": model,
                                   "prompt_version": "i9"}, ensure_ascii=False) + "\n"
            eq = [I("equity", cur={"equity_component": 5})]
            li = [I("derivative_liability", cur={"derivative_liability": 5})]
            # 1차: 마지막 줄(equity)을 쓴다. 2차: luna 가 아닌 줄은 버려 그 실행엔 기록이 없다 → 확인 필요
            (d / "run1.jsonl").write_text(run_row(li) + run_row(eq))
            (d / "run2.jsonl").write_text(run_row(eq, model="gpt-5.4-mini"))
            v1 = {"rcept_no": R, "corp_code": "00999999", "fiscal_year": 2025, "topic": "PPA_MEASUREMENT_PERIOD",
                  "value": "PROVISIONAL", "evidence": "잠정", "held_reason": ""}
            (d / "v1.jsonl").write_text(json.dumps(v1, ensure_ascii=False) + "\n")
            out = d / "out" / "topic_stance.v2.jsonl"
            rc = main(["--ledger", str(led), "--facts-audit", str(d / "audit.jsonl"), "--instruments",
                       str(d / "run1.jsonl"), "--instruments2", str(d / "run2.jsonl"), "--v1-stance",
                       str(d / "v1.jsonl"), "--tables", str(d / "no-tables"), "--out", str(out), "--workers", "1"])
            self.assertEqual(rc, 0)
            all_rows = [json.loads(x) for x in out.read_text().splitlines()]
            ppa = [r for r in all_rows if r["topic"] == "PPA_MEASUREMENT_PERIOD"]
            # 연결에만 사업결합 표가 있어도 확인 필요가 아니고, PPA 행은 연결 범위 하나만
            self.assertEqual([(r["fs_scope"], r["value"]) for r in ppa], [("consolidated", "PROVISIONAL")])
            rows = {r["topic"]: r for r in all_rows if r["fs_scope"] == "separate"}
            rows["PPA_MEASUREMENT_PERIOD"] = ppa[0]
            self.assertEqual(rows["CONVERSION_OPTION_CLASS"]["held_reason"], "needs_review")
            self.assertEqual(rows["FVOCI_DESIGNATION"]["value"], "DESIGNATED")
            self.assertEqual(rows["FVOCI_DESIGNATION"]["corp_code"], "00999999")
            self.assertEqual(rows["LEASE_EXEMPTION"]["held_reason"], "policy_only")
            recs = [json.loads(x) for x in (out.parent / "records.v2.jsonl").read_text().splitlines()]
            self.assertEqual(len(recs), len(all_rows))
            fv = next(r for r in recs if r["topic"] == "FVOCI_DESIGNATION")
            self.assertEqual([f["concept"] for f in fv["facts"]], ["FVOCI_EQUITY"])
            summary = json.loads((out.parent / "topic_stance.v2.summary.json").read_text())
            self.assertEqual(summary["rows"], len(all_rows))
            self.assertEqual(summary["instruments"]["run2"]["dropped_model"], 1)
            # 덮지 않는다
            self.assertEqual(main(["--ledger", str(led), "--facts-audit", str(d / "audit.jsonl"), "--instruments",
                                   str(d / "run1.jsonl"), "--instruments2", str(d / "run2.jsonl"), "--out", str(out)]), 2)

    def test_targeted_scopes_from_both_runs(self):
        # R35: 어느 실행이든 skipped 아닌 줄(llm_error·다른 모델 포함)이 있으면 그 공시·범위는 사채·우선주 대상
        import json
        import tempfile
        from pathlib import Path
        import importlib.util
        spec = importlib.util.spec_from_file_location("ledger_judge_cli2",
                                                      Path(__file__).parents[1] / "scripts" / "ledger_judge.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            rows = [{"rcept_no": R, "fs_scope": "separate", "records": [], "model_used": None, "error": "x"},
                    {"rcept_no": R, "fs_scope": None, "skipped": "no_tables", "records": []},
                    {"rcept_no": "20260101000001", "fs_scope": "consolidated", "records": [],
                     "model_used": "gpt-5.6-luna"}]
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            runs, stats, targeted = cli.load_runs(p, "gpt-5.6-luna", None)
            self.assertEqual(targeted, {(R, "separate"), ("20260101000001", "consolidated")})
            self.assertEqual(set(runs), {("20260101000001", "consolidated")})
        job = {"rcept_no": R, "facts": [], "marks": [M(EMB)], "run1": {"separate": []}, "run2": {},
               "scopes": ["separate"], "tables_dir": None, "targeted": ["separate"],
               "meta": {"corp_code": "1", "fiscal_year": 2025, "category_table": True}}
        row = next(r for r in cli.process(job)["rows"] if r["topic"] == EMB)
        self.assertEqual((row["value"], row["held_reason"]), ("UNDETERMINED", "no_facts"))
        job["targeted"] = []
        row = next(r for r in cli.process(job)["rows"] if r["topic"] == EMB)
        self.assertEqual(row["value"], "NOT_APPLICABLE")


def S(amount, unit_mult, period="current_end", sid="s1", text="당기말 현재 투자부동산의 공정가치는 …입니다."):
    """문장에서 읽은 투자부동산 공정가치 사실(c11, facts.sentence_facts 모양)."""
    return {"rcept_no": R, "fs_scope": "separate", "concept": "IP_FAIR_VALUE", "period": period, "amount": amount,
            "unit_kind": "krw", "amount_krw": amount * unit_mult, "label": "투자부동산의 공정가치", "label_path": [],
            "column": "", "target": None, "target_basis": None,
            "source": {"sentence_id": sid, "page": 1, "file": f"{R}_00760.xml"}, "source_status": "sentence",
            "concepts_version": "c11", "match_basis": "sentence", "text": text}


def FVM(sid="s926", text="(4) 투자부동산의 공정가치 모형의 적용당사는 투자부동산에 대한 후속측정을 공정가치로 평가하고 있으며, "
        "투자부동산의 공정가치는 … 결정되었습니다."):
    """공정가치 모형 문장 표시 사실(c11 IP_FV_MODEL, 금액 없음)."""
    f = S(0, 1, sid=sid, text=text)
    f.update({"concept": "IP_FV_MODEL", "amount": None, "amount_krw": None, "unit_kind": None,
              "label": "투자부동산 공정가치 모형"})
    return f


class LeaseParenthesizedTest(unittest.TestCase):
    """회계사 v2 표본(10-15): 괄호(음수)로 적은 리스료도 면제 적용 금액이다."""

    def test_treasure_hunter_both(self):
        # s083 트레져헌터 20260508000176: 단기리스료 (22,385,400), 소액자산 리스료 (9,286,674) → NONE 이었다
        cp, pp = "current_period", "prior_period"
        r = judge([F("LEASE_SHORT_TERM_EXP", -22385400, cp, "단기리스료"),
                   F("LEASE_SHORT_TERM_EXP", -33353260, pp, "단기리스료"),
                   F("LEASE_LOW_VALUE_EXP", -9286674, cp, "단기리스가 아닌 소액자산 리스료"),
                   F("LEASE_LOW_VALUE_EXP", -10086590, pp, "단기리스가 아닌 소액자산 리스료")])["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["basis_period"]), ("BOTH", "current"))
        self.assertIn("단기리스료 -22,385,400", r["evidence"])

    def test_unity_short_term_and_hana_low_value(self):
        # s097 유니티테크놀로지스코리아: 단기리스 리스료 (8,940)천원 / s100 하나자산운용: 소액리스료 (43,309)
        r = judge([F("LEASE_SHORT_TERM_EXP", -8940, "current_period", mult=1000)])["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["basis_period"]), ("SHORT_TERM", "current"))
        r = judge([F("LEASE_LOW_VALUE_EXP", -43309, "current_period")])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "LOW_VALUE")

    def test_combined_negative_row_follows_both_rule(self):
        # s087 휴림로봇 「단기리스 및 소액자산리스 관련 비용 (45,047)」, s091 무신사 「단기,소액자산 변동리스료 (12,881,327)」
        # 합친 행의 BOTH 는 R36 보류 그대로 — 다만 금액이 있으니 NONE 은 아니다
        r = judge([F("LEASE_SHORT_LOW_EXP", -45047, "current_period", mult=1000)])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "BOTH")

    def test_dsm_low_value_negative_with_combined_positive(self):
        # s099 디에스엠 20260318001079(별도): 소액기초자산리스 관련 비용 (6,979,968) + 다른 표 「단기 & 소액」 6,979,968
        cp = "current_period"
        r = judge([F("LEASE_LOW_VALUE_EXP", -6979968, cp), F("LEASE_LOW_VALUE_EXP", 0, "prior_period"),
                   F("LEASE_SHORT_LOW_EXP", 6979968, cp)])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "LOW_VALUE")

    def test_zero_dash_still_none(self):
        cp = "current_period"
        r = judge([F("LEASE_SHORT_TERM_EXP", 0, cp), F("LEASE_LOW_VALUE_EXP", 0, cp)])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "NONE")


class IpSentenceAndModelTest(unittest.TestCase):
    """회계사 v2 표본(10-15): 문장으로 공시한 투자부동산 공정가치·공정가치 모형."""

    def test_mrblue_sentence_differs(self):
        # s116 미스터블루: 장부금액 6,152,241천원, 「보고기간말 현재 투자부동산의 공정가치는 11,900,000천원입니다」
        r = judge([F("IP_CARRYING", 6152241, mult=1000, label="합 계"), F("IP_CARRYING", 6217456, "prior_end", mult=1000),
                   S(11900000, 1000, sid="s382", text="(4) 보고기간말 현재 투자부동산의 공정가치는 11,900,000천원입니다.")])
        r = r["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["basis_period"]), ("FV_DIFFERS", "current"))
        self.assertIn("s382", r["source_refs"])
        self.assertIn("공정가치는 11,900,000천원", r["evidence"])

    def test_mirae_sentence_equal_at_stated_precision(self):
        # s115 미래나노텍(연결): 장부금액 15,857,480천원, 「공정가치는 15,857백만원」 → 문장 단위(백만원)로 같다
        r = judge([F("IP_CARRYING", 15857480, mult=1000), S(15857, 1000000)])["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["basis_period"]), ("FV_EQUALS", "current"))
        r = judge([F("IP_CARRYING", 15859480, mult=1000), S(15857, 1000000)])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_DIFFERS")

    def test_sangsin_sentence_differs(self):
        # s119 상신금속: 장부금액 5,810,644천원, 「당기말 현재 투자부동산의 공정가치는 10,343백만원이며」
        r = judge([F("IP_CARRYING", 5810644, mult=1000), S(10343, 1000000)])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_DIFFERS")

    def test_table_pair_beats_sentence(self):
        t = f"{R}:00760:t900"
        r = judge([F("IP_CARRYING", 100, tid=t, row=3, col=1), F("IP_FAIR_VALUE", 100, tid=t, row=3, col=2),
                   S(9, 1)])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_EQUALS")

    def test_seohan_fair_value_model(self):
        # s120 서한: 공정가치 모형 → 당기말 장부금액 212,096,826천원이 공정가치이자 장부금액
        r = judge([F("IP_CARRYING", 212096826, mult=1000, label="합계"), F("IP_CARRYING", 211415701, "prior_end", mult=1000),
                   FVM()])["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["basis_period"]), ("FV_EQUALS", "current"))
        self.assertIn("합계 212,096,826천원 (당기말", r["evidence"])
        self.assertIn("s926", r["source_refs"])

    def test_model_needs_current_carrying(self):
        # 수정 1차(20260316000872): 당기말 장부금액이 없으면 모형 규칙을 쓰지 않는다(전기말로 FV_EQUALS 내지 않음)
        r = judge([F("IP_CARRYING", 2162070, "prior_end", mult=1000, label="건물"), FVM()])["IP_FAIR_VALUE"]
        self.assertNotEqual(r["value"], "FV_EQUALS")

    def test_blocked_model_sentence_never_not_disclosed(self):
        # 수정 4차 안전망: 모형 문장이 원가 근거로 막혔으면 FV_NOT_DISCLOSED 대신 확인 필요
        m = FVM(sid="s199")
        m["concept"] = "IP_FV_MODEL_BLOCKED"
        r = judge([F("IP_CARRYING", 295429547, mult=1000), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["held_reason"], "needs_review")
        self.assertIn("s199", r["evidence"])

    def test_model_mark_alone_is_not_used(self):
        # 공정가치 모형은 2층 문장 사실(IP_FV_MODEL)로만 본다 — 정책 표시(marks)는 그대로 판단불가 근거일 뿐
        m = M("IP_FAIR_VALUE", "company_statement", "당사는 투자부동산에 대한 후속측정을 공정가치로 평가하고 있습니다.")
        r = judge([F("IP_CARRYING", 100)], [], [m])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_NOT_DISCLOSED")

    def test_model_fact_alone_is_not_a_row_value(self):
        # 장부금액 없이 모형 문장만 있으면 행이 없다(금액 사실이 아니다)
        self.assertNotIn("IP_FAIR_VALUE", judge([FVM()]))

    def test_fair_value_model_with_differing_pair_needs_review(self):
        r = judge([F("IP_CARRYING", 100), F("IP_FAIR_VALUE", 150), FVM()])["IP_FAIR_VALUE"]
        self.assertEqual(r["held_reason"], "needs_review")

    def test_no_material_difference_sentence_is_approx_equal(self):
        # c14(김현명 D5 b): 금액 없이 「장부금액은 공정가치와 중요한 차이가 없습니다」 → 같음(근사), 공정가치 금액은 쓰지 않는다
        m = FVM(sid="s657", text="한편, 당기말 현재 투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다.")
        m["concept"] = "IP_FV_NO_MATERIAL_DIFF"
        r = judge([F("IP_CARRYING", 750124, mult=1000), m])["IP_FAIR_VALUE"]
        self.assertEqual((r["value"], r["basis_period"], r["value_flags"]), ("FV_EQUALS", "current", ["fv_approx"]))
        self.assertTrue(r["evidence"].startswith("장부금액과 유사(근사) — 회사가 중요한 차이가 없다고 판단함 · "
                                                 "공정가치 금액은 별도로 제시하지 않음: IP_CARRYING 750,124"))
        self.assertIn("중요한 차이가 없습니다", r["evidence"])
        self.assertIn("s657", r["source_refs"])

    def test_no_material_difference_without_current_carrying_needs_review(self):
        m = FVM(sid="s657", text="한편, 전기말 현재 투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다.")
        m["concept"] = "IP_FV_NO_MATERIAL_DIFF"
        r = judge([F("IP_CARRYING", 750124, "prior_end", mult=1000), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["held_reason"], "needs_review")
        self.assertIn("s657", r["evidence"])

    def test_real_fair_value_pair_wins_over_no_material_difference(self):
        m = FVM(sid="s657", text="투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다.")
        m["concept"] = "IP_FV_NO_MATERIAL_DIFF"
        r = judge([F("IP_CARRYING", 100), F("IP_FAIR_VALUE", 130), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_DIFFERS")
        self.assertNotIn("value_flags", r)

    def test_fair_value_leadin_without_amount_needs_review(self):
        m = FVM(sid="s489", text="(3) 당기말 현재 투자부동산의 공정가치는 다음과 같습니다.")
        m["concept"] = "IP_FV_LEADIN"
        r = judge([F("IP_CARRYING", 15414420, mult=1000), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["held_reason"], "needs_review")
        # 표 금액을 읽었으면 그대로 짝 비교
        r = judge([F("IP_CARRYING", 100), F("IP_FAIR_VALUE", 100), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_EQUALS")

    def test_kcttc_land_listing_differs(self):
        # s117 케이씨티시: 투자부동산 내역(당기말 46,012,668) / 공정가치 내역(당기말 95,702,928)
        r = judge([F("IP_CARRYING", 46012668, mult=1000, label="토지"),
                   F("IP_FAIR_VALUE", 95702928, mult=1000, label="공정가치")])["IP_FAIR_VALUE"]
        self.assertEqual(r["value"], "FV_DIFFERS")



def STMT(sid="s929", text="연결실체는 단기매매항목이 아닌 전략적 투자목적으로 보유하는 지분상품에 대해 최초 적용일에 "
                          "기타포괄손익-공정가치측정항목으로 지정하는 취소불가능한 선택권을 적용하였습니다."):
    return {"rcept_no": R, "fs_scope": "separate", "concept": "FVOCI_DESIGNATION_STMT", "period": "current_end",
            "amount": None, "unit_kind": None, "amount_krw": None, "label": "기타포괄손익-공정가치 지정 문장",
            "label_path": [], "column": "", "target": None, "target_basis": None,
            "source": {"sentence_id": sid, "page": 1, "file": "R_00760.xml"}, "source_status": "sentence",
            "concepts_version": "c12", "match_basis": "sentence", "text": text}


class C12FvociTest(unittest.TestCase):
    """회계사 두 명 합의 수정(r5): FVOCI 지정 문장, 합계 근거 줄."""

    def test_rules_version_r5(self):
        from dart_search_mcp.ledger.judge_rules import load_rules
        self.assertIn(load_rules()["rules_version"], ("r5", "r6", "r7"))

    def test_statement_with_total_only_is_designated(self):
        # s058 효성티앤씨: 주석 11 FVOCI 금융자산 총액 7,303백만원 + 주석 30 「…기타포괄손익으로 인식하도록 지정하였습니다」
        total = F("FVOCI_FA_TOTAL", 7303, label="기타포괄손익-공정가치측정금융자산", mult=1000000)
        r = judge([total])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))
        st = STMT(sid="t:R:00760:t614:title", text="회사는 특정 지분상품투자의 공정가치를 기타포괄손익으로 인식하도록 지정하였습니다(주석 5).")
        r = judge([total, st])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "current"))
        self.assertIn("인식하도록 지정하였습니다", r["evidence"])
        self.assertIn("7,303백만원", r["evidence"])
        self.assertIn("t:R:00760:t614:title", r["source_refs"])

    def test_statement_without_fvoci_balance_changes_nothing(self):
        # s052 우리벤처파트너스(두 명 모두 NOT_DESIGNATED 맞음): 우리금융지주 주식을 FVOCI 로 지정했으나 같은 해 전량 처분 —
        # 지정 문장만으로는 기말 보유를 알 수 없다
        st = STMT(text="연결실체는 주식의 포괄적 교환으로 취득한 우리금융지주 주식을 기타포괄손익-공정가치 측정 항목으로 지정하였습니다.")
        fvpl = F("FVPL_EQUITY", 50, label="당기손익-공정가치측정 지분상품")
        self.assertEqual(judge([fvpl, st])["FVOCI_DESIGNATION"]["value"], "NOT_DESIGNATED")
        self.assertNotIn("FVOCI_DESIGNATION", judge([st]))
        m = M("FVOCI_DESIGNATION")
        self.assertEqual(judge([st], marks=[m])["FVOCI_DESIGNATION"]["held_reason"], "policy_only")
        zero = F("FVOCI_FA_TOTAL", 0, label="기타포괄손익-공정가치측정금융자산")
        r = judge([zero, st], marks=[m])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "UNDETERMINED")

    def test_policy_mark_alone_still_policy_only(self):
        m = M("FVOCI_DESIGNATION", text="단기매매항목이 아닌 지분상품은 기타포괄손익-공정가치로 지정할 수 있습니다.")
        r = judge([], marks=[m])["FVOCI_DESIGNATION"]
        self.assertEqual(r["held_reason"], "policy_only")

    def test_equity_facts_keep_value_and_add_statement(self):
        eq = F("FVOCI_EQUITY", 22825786, label="시장성 있는 지분증권", mult=1000)
        r = judge([eq, STMT()])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "DESIGNATED")
        self.assertTrue(r["evidence"].startswith("시장성 있는 지분증권 22,825,786천원"))
        self.assertIn("s929", r["source_refs"])

    def test_total_evidence_line_shows_components(self):
        # s055 로체시스템즈: 근거 줄이 「합계」만 보여 무엇의 합계인지 몰랐다(회계사 두 명)
        f = F("FVOCI_EQUITY", 1085544, label="합계", mult=1000)
        f.update({"match_basis": "table_total", "section": "지분상품(비상장주식)",
                  "components": ["(주)베스테크", "(주)에이마크", "윈텔코퍼레이션(주)"]})
        self.assertEqual(evidence_line(f), "지분상품(비상장주식) 합계(구성: (주)베스테크·(주)에이마크·윈텔코퍼레이션(주)) "
                                           "1,085,544천원 (당기말)")
        f["components"] = [f"투자처{i}" for i in range(1, 8)]
        f["section"] = None
        self.assertEqual(evidence_line(f), "합계(구성: 투자처1·투자처2·투자처3·투자처4 외 3건) 1,085,544천원 (당기말)")


    def test_path_matched_equity_row_shows_path(self):
        # s058 효성티앤씨 t102: 「금융자산 / 비상장 주식 / … 수준 3」 × 「기타포괄손익인식금융자산」 열
        f = F("FVOCI_EQUITY", 7273, label="공정가치 서열체계 수준 3", mult=1000000)
        f["label_path"] = ["금융자산", "비상장 주식", "공정가치 서열체계의 모든 수준", "공정가치 서열체계 수준 3"]
        self.assertEqual(evidence_line(f), "금융자산 / 비상장 주식 / 공정가치 서열체계의 모든 수준 / 공정가치 서열체계 수준 3 "
                                           "7,273백만원 (당기말)")


class C12EmbTest(unittest.TestCase):
    def test_yukil_redemption_option_fact_separates_despite_unconfirmed_runs(self):
        # s039 육일씨엔에쓰: 두 실행 모두 embedded_separation unconfirmed, 「조기상환권」 172,558천원(대상 상환권, 행 이름)
        facts = [F("EMB_DERIV_LIABILITY", 172558, label="조기상환권", target="redemption", basis="label", mult=1000),
                 F("HOST_LIABILITY", 1740945, label="유동성전환사채", mult=1000)]
        ins = {"run1": [I("equity", cur={"host_carrying": 1740945, "equity_component": 260415})],
               "run2": [I("equity", cur={"host_carrying": 1740945, "equity_component": 260415})]}
        r = judge(facts, ins, read_failed_topics={"EMBEDDED_DERIVATIVE_SEPARATION"})["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("SEPARATED", "current"))
        self.assertIn("조기상환권 172,558천원", r["evidence"])


def PM(text, sid="s1686", kind="policy"):
    return M("PPA_MEASUREMENT_PERIOD", kind=kind, text=text, sid=sid)


_COWAY = ("연결회사의 사업결합 회계처리는 당기말 현재 잠정금액을 보고한 것으로, 취득일로부터 1년 이내의 측정기간 내에 "
          "사업결합과 관련된 사실과 상황에 대하여 새롭게 입수한 정보가 있는 경우에 조정될 수 있습니다.")


class C12PpaTest(unittest.TestCase):
    """s136 코웨이: v1 행은 관계없는 자본관리 문장에 「조정될 수 있습니다」 꼬리로 conditional — 회사 문장으로 잠정."""

    V1_COND = {"value": "UNDETERMINED", "evidence": "판정 불가: 조정하고, 주주들에게 자본을 반환하며", "held_reason": "conditional",
               "sentence_kind": "company_statement"}

    def bc(self):
        # 코웨이 t295 사업결합 표는 기간 열이 없다(period unknown)
        return [F("BC_NET_ASSETS_FV", 9775426, "unknown", label="순자산공정가치", mult=1000),
                F("BC_CONSIDERATION", 9775426, "unknown", label="총 이전대가", mult=1000)]

    def test_coway_provisional_sentence_with_bc_table(self):
        r = judge(self.bc(), marks=[PM(_COWAY)], v1_ppa=self.V1_COND)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["basis_period"], r["sentence_kind"]), ("PROVISIONAL", "current", "company_statement"))
        self.assertIn("당기말 현재 잠정금액을 보고한 것으로", r["evidence"])
        self.assertIn("s1686", r["source_refs"])
        # v1 행이 없어도 같다
        self.assertEqual(judge(self.bc(), marks=[PM(_COWAY)])["PPA_MEASUREMENT_PERIOD"]["value"], "PROVISIONAL")

    def test_provisional_sentence_needs_bc_table(self):
        r = judge([], marks=[PM(_COWAY)], v1_ppa=self.V1_COND)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "conditional"))

    def test_determined_v1_not_overridden(self):
        v1 = {"value": "FINAL", "evidence": "확정", "held_reason": ""}
        r = judge(self.bc(), marks=[PM(_COWAY)], v1_ppa=v1, bc_filing=True)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["value"], "FINAL")

    def test_other_sentences_not_provisional(self):
        for text in (
            # 측정기간 정책 형태(「완료되지 못한다면」)
            "사업결합 회계처리가 보고기간말 현재 완료되지 못한다면, 회계처리가 완료되지 못한 항목의 잠정금액을 재무제표에 보고하고 있습니다.",
            # 잠정금액이 확정금액으로 조정됨(확정 쪽 — 이번 범위 밖)
            "취득일 현재 존재해왔던 사실과 사건에 대하여, 보고기간종료일 현재 새롭게 입수한 정보를 토대로 사업결합에서 인식한 "
            "잠정금액은 다음과 같이 사업결합 회계처리에서확정금액으로 조정되었습니다.",
            # 관계기업 지분 취득(사업결합 아님)
            "회사는 당기 중 GC Labtech에 대한 유의적인 영향력을 취득하였습니다. 보고기간말 현재 기업인수가격배분(PPA) 등이 "
            "완료되지 않았음에 따라 잠정금액을 통하여 계상된 상기 장부금액은 측정기간 동안 조정될 수 있습니다.",
        ):
            r = judge(self.bc(), marks=[PM(text)], v1_ppa=self.V1_COND)["PPA_MEASUREMENT_PERIOD"]
            self.assertEqual(r["held_reason"], "conditional", text)


class C12ReadFailedTest(unittest.TestCase):
    """읽지 못한 표는 잔액표·범주표일 때만 주제를 막는다(r5)."""

    def rf(self, title, note="전환사채"):
        from dart_search_mcp.ledger.judge_rules import scope_meta
        return scope_meta([T("R:00760:t141", note, [], title=title, status="read_failed")], "separate")["read_failed_topics"]

    def test_terms_tables_do_not_block(self):
        for title in ("(2) 전환사채의 발행조건에 관한 주요사항은 다음과 같습니다.",
                      "(3) 당기말 현재 전환되지 아니한 상환전환우선주부채의 상세 발행내역은 다음과 같습니다.",
                      "11.3 회사는 전기 중 우선주를 발행하였으며 세부내용은 다음과 같습니다.",
                      "2) 주요 계약내용발행된 상환전환우선주의 전환과 상환에 관한 사항은 다음과 같습니다.",
                      "(가) 제15회차 무기명식 이권부 무보증 사모 전환사채"):
            self.assertEqual(self.rf(title), set(), title)

    def test_balance_category_or_untitled_tables_block(self):
        self.assertEqual(self.rf("(1) 당기말 현재 전환사채의 장부금액 내역은 다음과 같습니다."),
                         {"CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION"})
        self.assertEqual(self.rf("", note="차입금및사채"), {"CONVERSION_OPTION_CLASS", "EMBEDDED_DERIVATIVE_SEPARATION"})
        self.assertIn("FVOCI_DESIGNATION", self.rf("(1) 범주별 금융상품", note="금융상품"))

    def test_terms_table_still_blocks_absence_values(self):
        # 발행조건 표만 못 읽어도 「해당 없음」 같은 없음 값은 정하지 않는다(그 규칙은 회계사 의견이 갈려 범위 밖)
        from dart_search_mcp.ledger.judge_rules import scope_meta
        meta = scope_meta([T("R:00760:t068", "자본금과 주식발행초과금", [], status="read_failed",
                             title="11.3 회사는 전기 중 우선주를 발행하였으며 세부내용은 다음과 같습니다.")], "separate")
        self.assertEqual(meta["read_failed_topics"], set())
        self.assertIn("EMBEDDED_DERIVATIVE_SEPARATION", meta["read_failed_any_topics"])
        marks = [M("EMBEDDED_DERIVATIVE_SEPARATION")]
        r = judge([], marks=marks, category_table=True)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["value"], "NOT_APPLICABLE")
        r = judge([], marks=marks, category_table=True, read_failed_topics=meta["read_failed_topics"],
                  read_failed_any_topics=meta["read_failed_any_topics"])["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "read_failed"))

    def test_yukil_terms_table_no_longer_read_failed(self):
        # s039: 발행조건 표 t141 만 못 읽었다 — 사실이 없을 때 read_failed 가 아니라 원래 사유가 남는다
        meta = self.rf("(2) 전환사채의 발행조건에 관한 주요사항은 다음과 같습니다.")
        r = judge([F("HOST_LIABILITY", 100)], read_failed_topics=meta)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "no_facts"))



class C12Fix1JudgeTest(unittest.TestCase):
    """e54903f 리뷰 수정 1차(r5): DESIGNATED 는 당기 FVOCI 잔액, 전환권 값 동결."""

    def eq(self, amount, period, since="c12"):
        f = F("FVOCI_EQUITY", amount, period, label="시장성 있는 지분증권", mult=1000)
        if since:
            f["since"] = since
        return f

    def test_new_prior_only_balance_designated_prior(self):
        # 20260402001564 모양: c12 새 사실이 전기에만 0 초과, 당기 0 — c14(K2): c11 옛 사실과 같은 규칙
        r = judge([self.eq(0, "current_end"), self.eq(500, "prior_end")])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "prior"))
        self.assertTrue(r["evidence"].startswith("당기에 없음, 전기 기준: "))
        self.assertTrue(r["evidence"].endswith("당기말 잔액 없음(기중 처분 여부 미확인)"))
        self.assertEqual(r["value_flags"], ["fvoci_no_current_balance"])
        old = judge([self.eq(500, "prior_end", since=None)])["FVOCI_DESIGNATION"]
        self.assertEqual(old["value_flags"], ["fvoci_no_current_balance"])
        self.assertNotIn("value_flags", judge([self.eq(300, "current_end")])["FVOCI_DESIGNATION"])

    def test_current_balance_designated(self):
        r = judge([self.eq(300, "current_end"), self.eq(500, "prior_end")])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "current"))

    def test_c11_prior_only_rows_keep_c11_value(self):
        # c11 부터 있던 사실(표시 없음)로 전기만 있는 행은 c11 값(DESIGNATED, 전기) 그대로
        r = judge([self.eq(500, "prior_end", since=None)])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "prior"))

    def test_statement_needs_current_balance(self):
        # 20260325000813 모양: 지정 문장 + FVOCI 금융자산 총액이 전기에만
        cur0 = F("FVOCI_FA_TOTAL", 0, "current_end", label="기타포괄손익-공정가치측정금융자산")
        prior = F("FVOCI_FA_TOTAL", 900, "prior_end", label="기타포괄손익-공정가치측정금융자산")
        r = judge([cur0, prior, STMT()])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("전기만 FVOCI 잔액", r["evidence"])
        cur = F("FVOCI_FA_TOTAL", 700, "current_end", label="기타포괄손익-공정가치측정금융자산")
        self.assertEqual(judge([cur, prior, STMT()])["FVOCI_DESIGNATION"]["value"], "DESIGNATED")


def PLF(concept, amount, period="prior_end", label="파생상품부채(전환사채전환권및상환권)", target="conversion"):
    f = F("DERIV_PL_COLUMN", amount, period, label=label, target=target, basis="label", column="평가이익")
    f["orig_concept"] = concept
    return f


class C12ConvFreezeTest(unittest.TestCase):
    """전환권(CONV)은 회계사 의견이 갈린 주제 — c12 에서 손익 열 사실만 빠져 값이 바뀌면 확인 필요로 둔다."""

    def test_pl_column_change_puts_conv_in_review(self):
        # 20260318000934 모양: 사채 기록 EQUITY + (c11) 손익 열 파생상품부채 → c11 needs_review, c12 EQUITY
        # (c14: 같은 기간 금액만 막으므로 손익 열 사실도 당기)
        ins = [I("equity", cur={"equity_component": 100})]
        facts = [F("CONV_OPTION_EQUITY", 100, label="전환권대가"), PLF("EMB_DERIV_LIABILITY", 66833, "current_end")]
        r = judge(facts, ins)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("c12 근거 변경 — 전환권 규칙 결정 대기", r["evidence"])

    def test_pl_column_only_liability_row_in_review_not_dropped(self):
        # 20260316000965 모양: 부채 근거가 손익 열 「신주인수권」뿐 — c11 LIABILITY, c12 행 없음 → 확인 필요
        r = judge([PLF("CONV_OPTION_LIABILITY", 3947, label="신주인수권")])
        self.assertEqual((r["CONVERSION_OPTION_CLASS"]["value"], r["CONVERSION_OPTION_CLASS"]["held_reason"]),
                         ("UNDETERMINED", "needs_review"))

    def test_same_value_without_proxy_effect_unchanged(self):
        ins = [I("equity", cur={"equity_component": 100})]
        facts = [F("CONV_OPTION_EQUITY", 100, label="전환권대가"), PLF("EMB_DERIV_LIABILITY", 5, target="redemption",
                                                                      label="조기상환청구권")]
        r = judge(facts, ins)["CONVERSION_OPTION_CLASS"]
        self.assertEqual(r["value"], "EQUITY")
        # 대리 사실은 EMB 판정에 쓰지 않는다
        self.assertNotEqual(judge([PLF("EMB_DERIV_LIABILITY", 5, target="redemption", label="조기상환청구권"),
                                   F("HOST_LIABILITY", 10)])["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "SEPARATED")



def LS(concept, amount, period="current_period", sid="s1079", text="당기 중 … 소액자산 리스에 따른 리스료 … 총 현금유출액은 …"):
    f = F(concept, amount, period, label=concept)
    f.update({"match_basis": "sentence", "source": {"sentence_id": sid, "page": 1, "file": "R_00760.xml"},
              "source_status": "sentence", "text": text})
    return f


_LEASE_POLICY = "단기리스와 소액자산 리스에 대한 리스료는 리스기간에 걸쳐 정액기준으로 비용을 인식합니다."


class C13LeaseTest(unittest.TestCase):
    def test_daebong_policy_and_outflow_sentence_both(self):
        # s098 대봉엘에스: 두 분 모두 「둘 다」(정책 설명 + 현금유출액 문장 속 단기·소액 구분 공시)
        facts = [LS("LEASE_LOW_VALUE_EXP", 124303438), LS("LEASE_SHORT_TERM_EXP", 7008183)]
        r = judge(facts, marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["basis_period"]), ("BOTH", "current"))
        self.assertIn("정책: " + _LEASE_POLICY, r["evidence"])

    def test_same_sentence_shown_once(self):
        t = "당기 중 리스의 총 현금유출액은 323,475천원이며, 이 중 소액리스료는 2,317천원이며, 단기 리스료는 18,716천원입니다."
        facts = [LS("LEASE_LOW_VALUE_EXP", 2317, text=t), LS("LEASE_SHORT_TERM_EXP", 18716, text=t)]
        r = judge(facts, marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])["LEASE_EXEMPTION"]
        self.assertEqual(r["evidence"].count(t), 1)

    def test_outflow_sentence_without_policy_not_used(self):
        # c14(김현명 C2): 정책 설명이 없어도 보조 근거로 쓴다 — 「현금유출액 근거·비용 인식액 미확인」으로 직접 근거와 나눈다
        facts = [LS("LEASE_LOW_VALUE_EXP", 124303438), LS("LEASE_SHORT_TERM_EXP", 7008183)]
        r = judge(facts)["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["value_flags"]), ("BOTH", ["lease_cash_outflow"]))
        self.assertTrue(r["evidence"].endswith(" / 현금유출액 근거·비용 인식액 미확인"))
        r = judge(facts, marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])["LEASE_EXEMPTION"]
        self.assertIn("정책+지급액(현금유출) 근거 · 비용 인식액 미확인", r["evidence"])
        self.assertEqual(r["value_flags"], ["lease_cash_outflow"])
        # 표 금액으로 정한 값에는 붙이지 않는다
        r = judge([F("LEASE_SHORT_TERM_EXP", 50, "current_period", label="단기리스료"), LS("LEASE_LOW_VALUE_EXP", 9)])
        self.assertNotIn("value_flags", r["LEASE_EXEMPTION"])

    def test_table_facts_win_over_sentences(self):
        facts = [F("LEASE_SHORT_TERM_EXP", 50, "current_period", label="단기리스료"), LS("LEASE_LOW_VALUE_EXP", 124303438)]
        r = judge(facts, marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "SHORT_TERM")

    def test_combined_row_flag_and_label(self):
        # s087 휴림로봇: 합친 한 줄만 — 면제 적용(값은 지금 이름 BOTH), 개별 금액 구분 불가 표시와 표지(값 이름은 C2 대기)
        f = F("LEASE_SHORT_LOW_EXP", -45047, "current_period", label="단기리스 및 소액자산리스 관련 비용", mult=1000)
        r = judge([f], marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])["LEASE_EXEMPTION"]
        self.assertEqual(r["value"], "BOTH")
        self.assertEqual(r["value_flags"], ["lease_combined_row"])
        self.assertIn("단기·소액 합산 공시, 개별 금액 구분 불가", r["evidence"])
        self.assertIn("정책: ", r["evidence"])
        self.assertNotIn("value_flags", judge([F("LEASE_SHORT_TERM_EXP", 5, "current_period")])["LEASE_EXEMPTION"])


class C13DisplayTest(unittest.TestCase):
    def test_prior_only_basis_says_so(self):
        r = judge([F("DEV_COST_ADDITION", 300, "prior_period", label="개발비 증가")])["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["basis_period"], "prior")
        self.assertTrue(r["evidence"].startswith("당기에 없음, 전기 기준: "))
        r = judge([F("DEV_COST_ADDITION", 300, "current_period", label="개발비 증가")])["DEV_COST_CAPITALIZATION"]
        self.assertFalse(r["evidence"].startswith("당기에 없음"))

    def test_mixed_periods_split_current_first(self):
        # s003 팬스타엔터프라이즈 모양: 자본요소는 전기말, 파생상품부채는 당기말 — c14(김현명 C1): 기준시점이 다르면
        # 「둘 다」가 아니다. 상품이 다를 수 있어 한쪽을 고르지 않고 확인 필요(근거는 기간별로 나눠 보인다)
        ins = [I("equity", prior={"equity_component": 1230541}, name="신주인수권부사채"),
               I("derivative_liability", cur={"derivative_liability": 3601074}, name="전환사채")]
        r = judge([], ins)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertRegex(r["evidence"], r"기준시점이 다른 자본·부채 기록.*당기: 전환사채 파생상품부채 3,601,074.* / 전기 기준: "
                                        r"신주인수권부사채 자본요소 1,230,541")
        # 같은 시점이면 그대로 「둘 다」
        ins = [I("equity", cur={"equity_component": 5}, name="신주인수권부사채"),
               I("derivative_liability", cur={"derivative_liability": 7}, name="전환사채")]
        self.assertEqual(judge([], ins)["CONVERSION_OPTION_CLASS"]["value"], "BOTH")

    def test_lease_both_mixed_periods_split(self):
        r = judge([F("LEASE_SHORT_TERM_EXP", 5, "current_period", label="단기리스료"),
                   F("LEASE_LOW_VALUE_EXP", 7, "prior_period", label="소액리스료")])["LEASE_EXEMPTION"]
        self.assertRegex(r["evidence"], r"^당기: 단기리스료 5원 \(당기\) / 전기 기준: 소액리스료 7원 \(전기\)")

    def test_dev_expensed_shows_amount(self):
        # s067 아젤리스코리아: 「…경상개발비로 비용처리한 금액은 다음과 같습니다」 + 판관비 경상개발비 9,754천원
        st = M("DEV_COST_CAPITALIZATION", text="당기 및 전기 중 회사가 개발비 요건을 충족하지 못하여 경상개발비로 비용처리한 금액은 "
                                                "다음과 같습니다.", sid="s169")
        rnd = F("RND_EXPENSE", 9754, "current_period", label="판매비와관리비", mult=1000)
        rnd["label_path"] = ["판매비와관리비", "경상개발비"]
        r = judge([rnd, F("RND_EXPENSE", 29824, "prior_period", label="판매비와관리비", mult=1000)], marks=[st])
        r = r["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "EXPENSED")
        self.assertIn("비용 금액: 판매비와관리비 / 경상개발비 9,754천원 (당기)", r["evidence"])

    def test_fvoci_not_designated_names_category_table(self):
        # s042·s044·s048·s050: 근거는 「범주별 금융상품 표에 FVOCI 없음」이 먼저
        fvpl = F("FVPL_EQUITY", 50, label="당기손익-공정가치측정 지분상품")
        meta = {"category_table": True, "category_fvoci": False, "category_table_id": "R:00760:t061",
                "tables": {"R:00760:t061": {"note_no": "4", "note_title": "범주별 금융상품", "kind": "note", "multiplier": 1}}}
        r = judge([fvpl], **meta)["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "NOT_DESIGNATED")
        self.assertTrue(r["evidence"].startswith("FVOCI 없음(FVTPL만): 범주별 금융상품 표(주석 4 범주별 금융상품)"))
        self.assertIn("R:00760:t061", r["source_refs"])
        meta["category_fvoci"] = True  # 범주표에 FVOCI 열이 있으면 그렇게 쓰지 않는다
        self.assertFalse(judge([fvpl], **meta)["FVOCI_DESIGNATION"]["evidence"].startswith("FVOCI 없음"))

    def test_scope_meta_category_fvoci(self):
        from dart_search_mcp.ledger.judge_rules import scope_meta
        t = T("R:00760:t061", "금융상품", ["상각후원가측정금융자산", "당기손익-공정가치측정금융자산"], title="범주별 금융상품",
              headers=["상각후원가측정금융자산", "당기손익-공정가치측정금융자산"])
        t["rows"][0]["cells"] = [{"col": 1, "value": 100}, {"col": 2, "value": 0}]
        m = scope_meta([t], "separate")
        self.assertEqual((m["category_table"], m["category_fvoci"], m["category_table_id"]), (True, False, "R:00760:t061"))
        t2 = T("R:00760:t062", "금융상품", ["상각후원가측정금융자산"], title="범주별 금융상품",
               headers=["상각후원가측정금융자산", "기타포괄손익-공정가치측정금융자산"])
        t2["rows"][0]["cells"] = [{"col": 1, "value": 100}, {"col": 2, "value": 0}]
        # s050 피에이치에이 t032: FVOCI 열은 있으나 모두 0 → FVOCI 없음
        self.assertFalse(scope_meta([t, t2], "separate")["category_fvoci"])
        t2["rows"][0]["cells"][1]["value"] = 5
        self.assertTrue(scope_meta([t, t2], "separate")["category_fvoci"])



def PC(sid="s1345", text="당기 중 식별가능 순자산에 대한 가치평가를 반영하여 측정기간 내에 사업결합 회계처리를 완료하였습니다."):
    return {"rcept_no": R, "fs_scope": "separate", "concept": "PPA_COMPLETED_STMT", "period": "current_end", "amount": None,
            "unit_kind": None, "amount_krw": None, "label": "사업결합 회계처리 완료 문장", "label_path": [], "column": "",
            "target": None, "target_basis": None, "source": {"sentence_id": sid, "page": 1, "file": "R_00761.xml"},
            "source_status": "sentence", "concepts_version": "c13", "match_basis": "sentence", "text": text}


class C13PpaCompletionTest(unittest.TestCase):
    V1 = {"value": "UNDETERMINED", "evidence": "판정 불가: 자본위험 관리", "held_reason": "evidence_not_in_window"}

    def bc(self):
        return [F("BC_CONSIDERATION", 2284875000, "prior_period", label="이전대가")]

    def test_jiran_completion_final(self):
        r = judge(self.bc() + [PC()], v1_ppa=self.V1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["basis_period"]), ("FINAL", "current"))
        self.assertIn("측정기간 내에 사업결합 회계처리를 완료하였습니다", r["evidence"])

    def test_completion_needs_bc_table_and_undetermined_v1(self):
        self.assertEqual(judge([PC()], v1_ppa=self.V1)["PPA_MEASUREMENT_PERIOD"]["value"], "UNDETERMINED")
        v1 = {"value": "PROVISIONAL", "evidence": "잠정", "held_reason": ""}
        self.assertEqual(judge(self.bc() + [PC()], v1_ppa=v1, bc_filing=True)["PPA_MEASUREMENT_PERIOD"]["value"],
                         "PROVISIONAL")

    def test_completion_and_provisional_sentence_together_review(self):
        m = M("PPA_MEASUREMENT_PERIOD", text="연결회사의 사업결합 회계처리는 당기말 현재 잠정금액을 보고한 것으로, 조정될 수 있습니다.")
        r = judge(self.bc() + [PC()], marks=[m], v1_ppa=self.V1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))



class C13Fix1Test(unittest.TestCase):
    def test_ppa_v1_row_never_gets_prior_prefix(self):
        # 20251117000187 모양: v1 FINAL 「당기 중 … 인수가격배분이 완료되었으며」, 사업결합 표는 전기 칸 — 기간은 v1 문장 것이 아니다
        v1 = {"value": "FINAL", "evidence": "당기 중 식별가능한 자산(부채) 인식 등 인수가격배분이 완료되었으며", "held_reason": ""}
        r = judge([F("BC_CONSIDERATION", 100, "prior_period", label="이전대가")], v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["basis_period"]), ("FINAL", "prior"))
        self.assertEqual(r["evidence"], "확정—직접 확인: " + v1["evidence"])  # c14 D6 근거 유형

    def test_fact_based_prior_rows_keep_prefix(self):
        r = judge([F("DEV_COST_ADDITION", 300, "prior_period", label="개발비 증가")])["DEV_COST_CAPITALIZATION"]
        self.assertTrue(r["evidence"].startswith("당기에 없음, 전기 기준: "))

    def test_fvoci_none_without_amount_category_table_has_no_citation(self):
        fvpl = F("FVPL_EQUITY", 50, label="당기손익-공정가치측정 지분상품")
        r = judge([fvpl], category_table=True, category_fvoci=False, category_table_id=None)["FVOCI_DESIGNATION"]
        self.assertTrue(r["evidence"].startswith("FVOCI 없음(FVTPL만) / "))

    def test_scope_meta_cites_amount_category_table_only(self):
        # 20260317000787 sep: t028(회계정책 목록, 금액 없음)이 먼저 걸렸다 — 금액 있는 범주표 t080 을 쓴다
        # 20260323000097: 「금융손익」 표(범주별 손익)는 범주 잔액 표가 아니다
        from dart_search_mcp.ledger.judge_rules import scope_meta
        pol = T("R:00760:t028", "중요한 회계정책", ["상각후원가측정금융자산", "당기손익-공정가치측정금융자산"], title="범주별 금융상품")
        pl = T("R:00760:t109", "금융손익", ["상각후원가측정금융자산"], title="19-1 범주별 금융손익", headers=["이자수익"])
        pl["rows"][0]["cells"] = [{"col": 1, "value": 30}]
        cat = T("R:00760:t080", "금융상품", ["상각후원가측정금융자산"], title="범주별 금융상품", headers=["상각후원가측정금융자산"])
        cat["rows"][0]["cells"] = [{"col": 1, "value": 100}]
        self.assertEqual(scope_meta([pol, pl, cat], "separate")["category_table_id"], "R:00760:t080")
        # t080 은 표 제목이 「(1) 금융자산」(범주는 주석 제목에), 행 「상각후원가 금융자산」(띄어 씀) — 인용 후보로는 받는다
        t080 = T("R:00760:t080", "범주별 금융상품", ["당기손익-공정가치측정금융자산", "상각후원가 금융자산"], title="(1) 금융자산",
                 headers=["당기말"])
        t080["rows"][0]["cells"] = [{"col": 1, "value": 4872144}]
        m = scope_meta([pol, pl, t080], "separate")
        self.assertEqual((m["category_table"], m["category_table_id"], m["category_fvoci"]), (True, "R:00760:t080", False))
        m = scope_meta([pol, pl], "separate")
        self.assertEqual((m["category_table"], m["category_table_id"]), (True, None))
        # s050 피에이치에이 t032 「…범주별 장부금액과 순손익 내역」은 잔액 표 — 인용한다
        t032 = T("R:00760:t032", "금융상품의 범주", ["현금및현금성자산"], headers=["상각후원가측정"],
                 title="(1) 보고기간 종료일 현재의 금융자산의 범주별 장부금액과 순손익 내역은 다음과 같습니다.")
        t032["rows"][0]["cells"] = [{"col": 1, "value": 85265973}]
        self.assertEqual(scope_meta([pol, t032], "separate")["category_table_id"], "R:00760:t032")


if __name__ == "__main__":
    unittest.main()


class C14LeaseTest(unittest.TestCase):
    def test_single_value_with_both_policy_says_other_unconfirmed(self):
        # s082 에이자기관리부동산투자회사 모양(김현명 D4): 소액 금액만 + 정책은 단기·소액 둘 다 → 값은 소액, 단기는 「미확인」
        f = F("LEASE_LOW_VALUE_EXP", 1560, "current_period", label="단기리스가 아닌 소액자산 리스료", mult=1000)
        pol = "단기리스(리스개시일에 리스기간이 12개월 이하인 리스)와 소액자산리스의 경우 예외 규정을 선택하여 리스료를 비용을 인식합니다."
        r = judge([f], marks=[M("LEASE_EXEMPTION", text=pol)])["LEASE_EXEMPTION"]
        self.assertEqual((r["value"], r["value_flags"]), ("LOW_VALUE", ["lease_policy_both"]))
        self.assertTrue(r["evidence"].endswith(" / 정책은 단기·소액 모두 면제 / 단기: 실제 적용 내역 미확인"))
        r = judge([F("LEASE_SHORT_TERM_EXP", 5, "current_period")], marks=[M("LEASE_EXEMPTION", text=pol)])["LEASE_EXEMPTION"]
        self.assertIn("소액: 실제 적용 내역 미확인", r["evidence"])

    def test_single_policy_or_both_value_no_note(self):
        f = F("LEASE_LOW_VALUE_EXP", 7, "current_period")
        r = judge([f], marks=[M("LEASE_EXEMPTION", text="소액자산 리스는 인식면제를 적용하여 비용을 인식합니다.")])
        self.assertNotIn("value_flags", r["LEASE_EXEMPTION"])
        r = judge([f, F("LEASE_SHORT_TERM_EXP", 5, "current_period")], marks=[M("LEASE_EXEMPTION", text=_LEASE_POLICY)])
        self.assertNotIn("실제 적용 내역 미확인", r["LEASE_EXEMPTION"]["evidence"])


class C14DevTest(unittest.TestCase):
    def rnd(self, amount=9754, period="current_period"):
        f = F("RND_EXPENSE", amount, period, label="판매비와관리비", mult=1000)
        f["label_path"] = ["판매비와관리비", "경상연구개발비"]
        return f

    def test_rnd_amount_and_movement_table_without_dev_expensed(self):
        # s078 아이티센피엔에스 모양(김현명 D3·b6): 판관비 경상연구개발비 + 무형자산 변동표에 개발비 없음 → 비용 처리
        r = judge([self.rnd()], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["basis_period"], r["value_flags"]), ("EXPENSED", "current", ["dev_expensed_by_amount"]))
        self.assertEqual(r["evidence"], "비용 금액: 판매비와관리비 / 경상연구개발비 9,754천원 (당기) / 무형자산 변동표에 당기 개발비 증가 없음")

    def test_negative_rnd_amount_counts(self):
        r = judge([self.rnd(-9754)], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "EXPENSED")

    def test_past_balance_with_zero_current_addition_expensed_current_year_only(self):
        # cc(10-27 사용자 결정): 과거 개발비 잔액 + 올해 증가 0 → 비용 처리, 「올해 자산화 없음(과거 개발비 잔액 있음)」
        facts = [F("DEV_COST_ASSET", 500), F("DEV_COST_ADDITION", 0, "current_period", label="개발비 / 취득"),
                 F("DEV_COST_ADDITION", 40, "prior_period", label="개발비 / 취득"), self.rnd()]
        r = judge(facts, movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual((r["value"], r["basis_period"], r["value_flags"]),
                         ("EXPENSED", "current", ["expensed_current_year_only"]))
        self.assertTrue(r["evidence"].startswith("올해 자산화 없음(과거 개발비 잔액 있음): 개발비 / 취득 0 (당기)"))
        self.assertIn("비용 금액: 판매비와관리비 / 경상연구개발비 9,754천원 (당기)", r["evidence"])

    def test_past_balance_without_read_current_addition_stays_undetermined(self):
        # 당기 증가 칸을 못 읽었으면(0 사실 없음) 못 찾은 것 — 정하지 않는다
        r = judge([F("DEV_COST_ASSET", 500), self.rnd()], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "UNDETERMINED")
        r = judge([F("DEV_COST_ADDITION", 40, "prior_period"), self.rnd()], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "CAPITALIZED")

    def test_current_addition_capitalized_and_shows_expense(self):
        facts = [F("DEV_COST_ADDITION", 30, "current_period", label="개발비 증가"), self.rnd()]
        r = judge(facts, movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "CAPITALIZED")
        self.assertIn(" / 비용 처리도 확인: 판매비와관리비 / 경상연구개발비 9,754천원 (당기)", r["evidence"])

    def test_prior_rnd_or_no_movement_table_not_expensed(self):
        self.assertEqual(judge([self.rnd(period="prior_period")], movement_table=True)["DEV_COST_CAPITALIZATION"]["value"],
                         "UNDETERMINED")
        self.assertEqual(judge([self.rnd()], movement_table=False)["DEV_COST_CAPITALIZATION"]["value"], "UNDETERMINED")

    def test_statement_path_first_and_read_failure_blocks(self):
        st = M("DEV_COST_CAPITALIZATION", text="당기 및 전기 중 회사가 개발비 요건을 충족하지 못하여 경상개발비로 비용처리한 금액은 "
                                                "다음과 같습니다.", sid="s169")
        r = judge([self.rnd()], marks=[st], movement_table=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "EXPENSED")
        self.assertNotIn("value_flags", r)
        r = judge([self.rnd()], movement_table=True, read_failed_any_topics={"DEV_COST_CAPITALIZATION"})
        self.assertEqual(r["DEV_COST_CAPITALIZATION"]["held_reason"], "read_failed")


class C14ConvTest(unittest.TestCase):
    def test_s016_other_right_liability_beside_equity_component(self):
        # s016 마이크로디지탈(김현명 C1): 회차별 표에 전환권대가와 파생상품부채(조기상환권)·파생상품자산(매도청구권) →
        # 전환권은 자본, 파생상품부채는 별도 정보
        tid = f"{R}:00760:t200"
        facts = [F("CONV_OPTION_EQUITY", 4599428, label="전환권대가", tid=tid),
                 F("EMB_DERIV_LIABILITY", 5251822, label="파생상품부채", tid=tid),
                 F("EMB_DERIV_ASSET", 1463113, label="파생상품자산", tid=tid)]
        r = judge(facts)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["value_flags"]), ("EQUITY", ["conv_other_right_shown"]))
        self.assertIn("별도: 다른 권리 파생상품부채 파생상품부채 5,251,822원 (당기말)(전환권 판정에 합산 안 함)", r["evidence"])

    def test_liability_in_other_table_still_blocks(self):
        facts = [F("CONV_OPTION_EQUITY", 100, label="전환권대가"), F("EMB_DERIV_LIABILITY", 50, label="파생상품부채")]
        self.assertEqual(judge(facts)["CONVERSION_OPTION_CLASS"]["held_reason"], "needs_review")

    def test_prior_liability_does_not_block_current_equity(self):
        facts = [F("CONV_OPTION_EQUITY", 100, label="전환권대가"), F("EMB_DERIV_LIABILITY", 50, "prior_end", label="파생상품부채")]
        self.assertEqual(judge(facts)["CONVERSION_OPTION_CLASS"]["value"], "EQUITY")

    def test_facts_both_needs_same_period(self):
        facts = [F("CONV_OPTION_EQUITY", 100, label="전환권대가"), F("CONV_OPTION_LIABILITY", 50, "prior_end", label="전환권")]
        r = judge(facts)["CONVERSION_OPTION_CLASS"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("전기 기준: 전환권 50원 (전기말)", r["evidence"])
        facts[1] = F("CONV_OPTION_LIABILITY", 50, label="전환권")
        self.assertEqual(judge(facts)["CONVERSION_OPTION_CLASS"]["value"], "BOTH")


class C14PpaTest(unittest.TestCase):
    def bc(self):
        return [F("BC_CONSIDERATION", 100, "prior_period", label="이전대가")]

    def test_completion_sentence_final_direct(self):
        r = judge(self.bc() + [PC()], v1_ppa=C13PpaCompletionTest.V1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["value_flags"]), ("FINAL", ["ppa_final_direct"]))
        self.assertTrue(r["evidence"].startswith("확정—직접 확인: "))

    def test_v1_final_on_retro_adjustment_only_needs_review(self):
        # 20260318001133·20260409000247 모양(김현명 D6): 잠정금액 소급조정 사실만 — 확정이 아니다
        for ev in ("전기 중 (주)미디어캐슬의 사업결합 관련 매수가격배분 금액을 잠정금액으로 표시하였으며, 당기 중 … 취득일 기준으로 "
                   "잠정금액을 소급 조정하였습니다.",
                   "연결실체는 취득일에 유ㆍ무형자산 등의 공정가치에 대해 독립적인 가치평가가 완료되지 않아 잠정적으로 결정하였으며, 당기 중 "
                   "… 식별가능순자산의 잠정금액을 소급하여 조정하였습니다."):
            v1 = {"value": "FINAL", "evidence": ev, "held_reason": ""}
            r = judge(self.bc(), v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
            self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"), ev)

    def test_v1_final_with_completion_keeps_final(self):
        v1 = {"value": "FINAL", "evidence": "연결회사는 사업결합 회계처리를 전기에 완료하지 못하여 … 당기 중 이전대가 배분을 포함한 "
                                            "사업결합 회계처리를 완료하였습니다.", "held_reason": ""}
        r = judge(self.bc(), v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["value_flags"]), ("FINAL", ["ppa_final_direct"]))


class C14DevLabelGuardTest(unittest.TestCase):
    def test_scope_meta_dev_asset_label(self):
        # 20260311003212 t056 모양: 열 방향 변동표, 머리글에 「개발비」 — 2층이 금액을 못 읽어도 개발비가 있다는 것은 안다
        from dart_search_mcp.ledger.judge_rules import scope_meta
        t = T("R:00760:t056", "기타무형자산", ["취득원가", "기초", "대체(*1)", "기말"], title="1) 당기",
              headers=["특허권", "소프트웨어", "개발비"])
        self.assertTrue(scope_meta([t], "separate")["dev_asset_label"])
        t2 = T("R:00760:t057", "무형자산", ["판매비와관리비", "경상연구개발비"], title="상각비 배분", headers=["당기"])
        self.assertFalse(scope_meta([t2], "separate")["dev_asset_label"])

    def test_dev_label_without_facts_not_expensed(self):
        f = F("RND_EXPENSE", 9754, "current_period", label="경상연구개발비")
        r = judge([f], movement_table=True, dev_asset_label=True)["DEV_COST_CAPITALIZATION"]
        self.assertEqual(r["value"], "UNDETERMINED")
