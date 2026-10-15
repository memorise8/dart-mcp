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
        self.assertTrue(r["evidence"].startswith("제2회 전환사채 자본요소 5"))
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
        self.assertEqual(r["evidence"], "제3회 전환사채 주계약과 분리 (근거 s7)")

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

    def test_no_material_difference_sentence_needs_review(self):
        # 금액 없이 「장부금액은 공정가치와 중요한 차이가 없습니다」만 있으면 FV_NOT_DISCLOSED 로 정하지 않는다(Q4 대기)
        m = FVM(sid="s657", text="한편, 당기말 현재 투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다.")
        m["concept"] = "IP_FV_NO_MATERIAL_DIFF"
        r = judge([F("IP_CARRYING", 750124, mult=1000), m])["IP_FAIR_VALUE"]
        self.assertEqual(r["held_reason"], "needs_review")
        self.assertIn("s657", r["evidence"])

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


if __name__ == "__main__":
    unittest.main()
