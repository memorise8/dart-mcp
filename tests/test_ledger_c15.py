"""c15(김현명 2단계: D1·D2·D6·D7·C1·C3) — 사실 추출(facts.py)·판정(judge_rules.py)·상품 행(rights.py)."""
import unittest

from dart_search_mcp.ledger.facts import _acq_dates, _bc_facts, _fvoci_disposal_facts
from dart_search_mcp.ledger.judge_rules import judge_filing, scope_meta
from dart_search_mcp.ledger.rights import instrument_rights, product_key
from tests.test_ledger_judge import F, I, META, R, T, judge


def S(concept, text="문장.", date=None, scope="consolidated", sid="s9"):
    f = {"rcept_no": R, "fs_scope": scope, "concept": concept, "period": "current_period", "amount": None,
         "unit_kind": None, "amount_krw": None, "label": concept, "label_path": [], "column": "", "target": None,
         "target_basis": None, "source": {"sentence_id": sid, "page": 1, "file": "R_00761.xml"},
         "source_status": "sentence", "concepts_version": "c15", "match_basis": "sentence", "text": text}
    if date:
        f["date"] = date
    return f


def BC():
    return dict(F("BC_CONSIDERATION", 5, "prior_period"), fs_scope="consolidated")


def judge_c(facts=(), **meta):
    m = dict(META)
    m.update(meta)
    return {r["topic"]: r for r in judge_filing(R, "consolidated", list(facts), [], [], m)}


class AcqDateExtractTest(unittest.TestCase):
    def test_keyword_then_date(self):
        self.assertEqual(_acq_dates("…지분 53.3%를 2,537,698백만원에 취득(간주취득일: 2023년 3월 31일)하였으며"), ["2023-03-31"])

    def test_date_then_keyword(self):
        s = "당사는 2023년 11월 14일 이사회 및 2023년 12월 22일 주주총회를 통해서 2024년 1월 25일을 합병기일로 하여 ㈜A를 흡수합병하였습니다."
        self.assertEqual(_acq_dates(s), ["2024-01-25"])

    def test_nearest_date_to_control(self):
        # 20260313001235: 승인일(5월 2일)이 아니라 취득일(7월 1일)
        s = "연결회사는 2025년 5월 2일 금융위원회로부터 자회사 편입을 승인받아 2025년 7월 1일자로 B에 대한 지분 100%를 취득하여 지배력을 획득하였습니다."
        self.assertEqual(_acq_dates(s), ["2025-07-01"])

    def test_skips(self):
        for s in ("동일지배하의 사업결합으로 2024년 11월 1일을 합병기일로 하였습니다.",
                  "동 사업결합에 대하여 취득일이 당해 보고기간의 개시일인 2025년 1월 1일이라고 가정할 경우, 매출액은 …",
                  "당사는 2025년 12월 1일을 합병기일로 하여 100% 자회사인 (주)C를 흡수합병하였습니다.",
                  "당기 신규 인식한 관계기업으로 간주취득일은 2025년 7월 1일입니다.",
                  "회사는 2024년 12월 26일을 합병기일로 하여 100% 지배기업인 G를 흡수합병하였습니다."):
            self.assertEqual(_acq_dates(s), [], s)

    def test_only_in_bc_note(self):
        filing = {"rcept_no": R, "tables": [], "paragraphs": [
            {"id": "s1", "file": "R_00761.xml", "text": "38. 사업결합"},
            {"id": "s2", "file": "R_00761.xml", "text": "연결회사는 2024년 3월 29일 D의 지분 26.95%를 취득하여 지배력을 획득하였습니다."},
            {"id": "s3", "file": "R_00761.xml", "text": "39. 리스"},
            {"id": "s4", "file": "R_00761.xml", "text": "자산의 취득일: 2023년 1월 1일"}]}
        got = [(f["concept"], f.get("date")) for f in _bc_facts(filing, {}, "c15")]
        self.assertEqual(got, [("BC_ACQUISITION_DATE", "2024-03-29")])

    def test_dated_current_deal_without_word(self):
        filing = {"rcept_no": R, "period_end": "2025-12-31", "tables": [], "paragraphs": [
            {"id": "s1", "file": "R_00761.xml", "text": "38. 사업결합"},
            {"id": "s2", "file": "R_00761.xml",
             "text": "(3) 전기중 연결범위대상으로 포함된 (주)인콤은 2025년 8월 20일 이트론 가상화사업부의 지분 100%를 취득하였으며, "
                     "본 취득으로 연결범위에포함되었습니다."}]}
        got = sorted((f["concept"], f.get("date")) for f in _bc_facts(filing, {}, "c15"))
        self.assertEqual(got, [("BC_CURRENT_DEAL_STMT", None)])

    def bc_note(self, *texts, pe="2025-12-31"):
        paras = [{"id": "s0", "file": "R_00761.xml", "text": "38. 사업결합"}] + \
            [{"id": f"s{i}", "file": "R_00761.xml", "text": t} for i, t in enumerate(texts, 1)]
        return sorted((f["concept"], f.get("date")) for f in
                      _bc_facts({"rcept_no": R, "period_end": pe, "tables": [], "paragraphs": paras}, {}, "c15"))

    def test_review_1_month_only_current_deal(self):
        # 리뷰 c15 #1: 「2025년 5월 … 인수」(일 없음)도 당기 거래 — 측정기간 종료 확정을 막는다
        got = self.bc_note("2023년 3월 31일자로 (주)A의 지분을 취득하여 지배력을 획득하였습니다.",
                           "2025년 5월 (주)비의 지분 70%를 인수하였습니다.")
        self.assertIn(("BC_CURRENT_DEAL_STMT", None), got)
        self.assertIn(("BC_ACQUISITION_DATE", "2023-03-31"), got)
        self.assertIn(("BC_CURRENT_DEAL_STMT", None), self.bc_note("2025년 중 (주)씨를 인수하였습니다."))
        self.assertNotIn(("BC_CURRENT_DEAL_STMT", None), self.bc_note("2023년 5월 (주)비의 지분 70%를 인수하였습니다."))

    def test_review_1_month_only_acquisition_date_blocks(self):
        got = self.bc_note("연결회사는 지배력을 2024년 11월(이하 실제 취득일)에 획득하였습니다.")
        self.assertIn(("BC_ACQUISITION_DATE", "unknown"), got)
        r = judge_c([S("BC_ACQUISITION_DATE", date="2022-01-01"), S("BC_ACQUISITION_DATE", date="unknown"), BC()],
                    period_end="2025-12-31")
        self.assertNotIn("PPA_MEASUREMENT_PERIOD", r)

    def test_review_2_no_new_sub_same_clause(self):
        filing = {"rcept_no": R, "tables": [], "paragraphs": [{"id": "s1", "file": "R_00761.xml",
                  "text": "당기 중 신규로 연결에 포함된 회사는 (주)에이이며, 연결에서 제외된 기업은 없습니다."}]}
        self.assertEqual([f["concept"] for f in _bc_facts(filing, {}, "c15")], [])

    def test_review_3_merger_with_negated_other_clause(self):
        filing = {"rcept_no": R, "period_end": "2025-12-31", "tables": [], "paragraphs": [
            {"id": "s1", "file": "R_00761.xml", "text": "1. 일반사항"},
            {"id": "s2", "file": "R_00761.xml", "text": "당기 중 (주)비를 흡수합병하였으며, 이로 인한 영업권은 없습니다."},
            {"id": "s3", "file": "R_00761.xml", "text": "회사는 2025년 3월 1일 (주)디를 흡수합병하였습니다."},
            {"id": "s4", "file": "R_00761.xml", "text": "당기 중 흡수합병한 회사는 없습니다."}]}
        deals = lambda paras: [f["source"]["sentence_id"] for f in _bc_facts({**filing, "paragraphs": paras}, {}, "c15")  # noqa: E731
                               if f["concept"] == "BC_CURRENT_DEAL_STMT"]  # 범위마다 한 사실(첫 문장)
        ps = filing["paragraphs"]
        self.assertEqual(deals(ps), ["s2"])
        self.assertEqual(deals([ps[0], ps[2]]), ["s3"])  # 「당기」 없이 이번 기간 날짜로 쓴 합병
        self.assertEqual(deals([ps[0], ps[3]]), [])

    def test_no_new_subsidiary_and_outside_deal(self):
        filing = {"rcept_no": R, "tables": [], "paragraphs": [
            {"id": "s1", "file": "R_00761.xml", "text": "1. 일반사항"},
            {"id": "s2", "file": "R_00761.xml", "text": "당기 중 연결범위의 변동은 없습니다."},
            {"id": "s3", "file": "R_00761.xml", "text": "당기 중 회사는 E를 흡수합병하였습니다."},
            {"id": "s4", "file": "R_00761.xml", "text": "당기 중 F를 신규 설립하여 지배력을 획득하였습니다."}]}
        got = sorted(f["concept"] for f in _bc_facts(filing, {}, "c15"))
        self.assertEqual(got, ["BC_CURRENT_DEAL_STMT", "BC_NO_NEW_SUBSIDIARY"])


class FvociDisposalExtractTest(unittest.TestCase):
    def sce(self, header):
        return {"table_id": "R:00761:t010", "fs_scope": "consolidated", "status": "verified", "page": 3, "title": "",
                "region": {"kind": "statement_ce"},
                "columns": [{"col": 0, "header_path": ["과 목"]}, {"col": 1, "header_path": [header]}],
                "rows": [{"row": 1, "label": "2024.01.01(전기초)", "cells": []},
                         {"row": 2, "label": "기타포괄손익-공정가치측정금융자산 처분", "cells": [{"col": 1, "value": 5}]},
                         {"row": 3, "label": "2025.01.01(당기초)", "cells": []},
                         {"row": 4, "label": "기타포괄손익-공정가치측정금융자산 처분", "cells": [{"col": 1, "value": 7}]}]}

    def test_sce_retained_earnings_column_by_block(self):
        got = _fvoci_disposal_facts({"rcept_no": R, "period_end": "2025-12-31", "tables": [self.sce("이익잉여금")]}, "c15")
        self.assertEqual([(f["period"], f["amount"]) for f in got], [("prior_period", 5), ("current_period", 7)])

    def test_revaluation_surplus_column_not_retained_earnings(self):
        got = _fvoci_disposal_facts({"rcept_no": R, "period_end": "2025-12-31", "tables": [self.sce("재평가잉여금")]}, "c15")
        self.assertEqual(got, [])

    def test_sce_date_needs_january_first(self):
        t = self.sce("이익잉여금")
        t["rows"][2]["label"] = "2025.01.15 유상증자"  # 기초 행이 아니다 — 묶음은 여전히 전기
        got = _fvoci_disposal_facts({"rcept_no": R, "period_end": "2025-12-31", "tables": [t]}, "c15")
        self.assertEqual([f["period"] for f in got], ["prior_period"])

    def test_sce_oci_column_only_is_not_equity_disposal(self):
        # 채무상품 재분류는 이익잉여금으로 가지 않는다 — 기타포괄손익누계액 열만 움직이면 쓰지 않는다
        got = _fvoci_disposal_facts({"rcept_no": R, "period_end": "2025-12-31", "tables": [self.sce("기타포괄손익누계액")]},
                                    "c15")
        self.assertEqual(got, [])


class D6MeasurementPeriodTest(unittest.TestCase):
    def test_one_year_elapsed_final(self):
        r = judge_c([S("BC_ACQUISITION_DATE", "2024년 3월 29일 지배력 획득", date="2024-03-29"), BC()],
                    period_end="2025-12-31")["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["value_flags"]), ("FINAL", ["ppa_final_measurement_period"]))
        self.assertTrue(r["evidence"].startswith("확정—측정기간 종료에 따른 판단: 취득일 2024-03-29, 보고기간말 2025-12-31"))

    def test_deemed_year_end_exactly_one_year(self):
        r = judge_c([S("BC_ACQUISITION_DATE", date="2024-12-31"), BC()], period_end="2025-12-31")["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["value"], "FINAL")

    def test_needs_acquisition_method_table(self):
        # 동일지배·지배기업 합병처럼 이전대가·순자산 공정가치 표가 없으면 측정기간을 따지지 않는다
        self.assertNotIn("PPA_MEASUREMENT_PERIOD",
                         judge_c([S("BC_ACQUISITION_DATE", date="2023-01-01")], period_end="2025-12-31"))

    def test_not_elapsed_not_provisional(self):
        # 1년이 지나지 않았다는 이유만으로 잠정으로 판정하지 않는다
        self.assertNotIn("PPA_MEASUREMENT_PERIOD",
                         judge_c([S("BC_ACQUISITION_DATE", date="2025-07-01")], period_end="2025-12-31"))

    def test_any_recent_or_current_deal_blocks(self):
        r = judge_c([S("BC_ACQUISITION_DATE", date="2023-01-01"), S("BC_ACQUISITION_DATE", date="2025-07-01")],
                    period_end="2025-12-31")
        self.assertNotIn("PPA_MEASUREMENT_PERIOD", r)
        r = judge_c([S("BC_ACQUISITION_DATE", date="2023-01-01"), S("BC_CURRENT_DEAL_STMT", "당기 중 발생한 사업결합")],
                    period_end="2025-12-31")
        self.assertNotIn("PPA_MEASUREMENT_PERIOD", r)

    def test_conflict_with_provisional_statement(self):
        m = {"rcept_no": R, "topic": "PPA_MEASUREMENT_PERIOD", "kind": "company_statement", "sentence_id": "s5",
             "text": "연결회사의 사업결합 회계처리는 당기말 현재 잠정금액을 보고한 것으로, 측정기간 내에 추가 조정될 수 있습니다.",
             "exception": None}
        r = {x["topic"]: x for x in judge_filing(R, "consolidated", [S("BC_ACQUISITION_DATE", date="2023-01-01"), BC()],
                                                 [], [m], {**META, "period_end": "2025-12-31"})}["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))
        self.assertIn("측정기간이 지남", r["evidence"])

    def test_conflict_with_provisional_v1(self):
        v1 = {"value": "PROVISIONAL", "evidence": "당기말 현재 잠정금액", "held_reason": ""}
        r = judge_c([S("BC_ACQUISITION_DATE", date="2023-01-01"), BC()], period_end="2025-12-31", v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "needs_review"))

    def test_v1_retro_only_becomes_period_final(self):
        v1 = {"value": "FINAL", "evidence": "당기 중 잠정금액을 소급하여 조정하였습니다.", "held_reason": ""}
        r = judge_c([S("BC_ACQUISITION_DATE", date="2024-06-30"), BC()], period_end="2025-12-31", v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["value_flags"]), ("FINAL", ["ppa_final_measurement_period"]))
        self.assertIn("옛 판정 근거: 당기 중 잠정금액을 소급", r["evidence"])

    def test_prior_year_acquisition_sentence(self):
        # s121 팬엔터테인먼트: 「연결회사는 전기 중 (주)미디어캐슬의 지배력을 획득하였으며」 — 날짜 없이 전기 중
        r = judge_c([S("BC_ACQUISITION_DATE", "연결회사는 전기 중 (주)B의 지배력을 획득하였으며", date="prior_period"), BC()],
                    period_end="2025-12-31")["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["value"], "FINAL")
        self.assertIn("취득일 전기 중(전기말 2024-12-31 이전)", r["evidence"])
        filing = {"rcept_no": R, "paragraphs": [], "tables": [{
            "table_id": "R:00761:t258", "fs_scope": "consolidated", "page": 9, "rows": [],
            "title": "(1) 연결회사는 전기 중 (주)팬에프앤비 및 (주)미디어캐슬의 지배력을 획득하였으며,이전한 대가 및 영업권",
            "region": {"kind": "note", "note_title": "사업결합"}}]}
        self.assertEqual([f.get("date") for f in _bc_facts(filing, {}, "c15")], ["prior_period"])

    def test_prior_provisional_shown(self):
        r = judge_c([S("BC_ACQUISITION_DATE", date="2024-06-30"), BC(),
                     S("BC_PRIOR_PROVISIONAL_STMT", "전기말 현재 공정가치를 산정 준비 중에 있었으며")],
                    period_end="2025-12-31")["PPA_MEASUREMENT_PERIOD"]
        self.assertIn("전기말 당시 잠정: 전기말 현재", r["evidence"])


class D7NoBusinessCombinationTest(unittest.TestCase):
    NO = "당기 중 연결범위의 변동은 없습니다."

    def test_composite_no_bc(self):
        r = judge_c([S("BC_NO_NEW_SUBSIDIARY", self.NO)])["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual((r["value"], r["value_flags"]), ("NO_BUSINESS_COMBINATION", ["no_bc_composite"]))

    def test_any_trace_blocks(self):
        stmt = [S("BC_NO_NEW_SUBSIDIARY", self.NO)]
        for extra, meta in (([S("BC_CURRENT_DEAL_STMT", "당기 중 E를 흡수합병하였습니다.")], {}),
                            ([], {"bc_note_table": True}), ([], {"bc_any_fact": True}),
                            ([dict(F("BC_CONSIDERATION", 5, "prior_period"), fs_scope="consolidated")], {})):
            self.assertNotIn("PPA_MEASUREMENT_PERIOD", judge_c(stmt + extra, **meta), (extra, meta))

    def test_separate_scope_not_decided(self):
        self.assertNotIn("PPA_MEASUREMENT_PERIOD", judge([S("BC_NO_NEW_SUBSIDIARY", self.NO, scope="separate")]))

    def test_v1_value_kept(self):
        v1 = {"value": "UNDETERMINED", "evidence": "판정 불가", "held_reason": "policy_only"}
        r = judge_c([S("BC_NO_NEW_SUBSIDIARY", self.NO)], v1_ppa=v1)["PPA_MEASUREMENT_PERIOD"]
        self.assertEqual(r["value"], "NO_BUSINESS_COMBINATION")


class C3FvociDisposalTest(unittest.TestCase):
    def test_prior_balance_current_disposal_all_sold(self):
        r = judge([F("FVOCI_EQUITY", 100, "prior_end"), F("FVOCI_EQUITY_DISPOSAL", 30, "current_period")])["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "DESIGNATED")
        self.assertIn("기중 전량 처분·기말 보유 없음", r["evidence"])
        self.assertNotIn("미확인", r["evidence"])
        self.assertEqual(r["value_flags"], ["fvoci_disposed", "fvoci_no_current_balance"])

    def test_current_balance_and_disposal(self):
        r = judge([F("FVOCI_EQUITY", 100), F("FVOCI_EQUITY_DISPOSAL", 30, "current_period")])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["value_flags"]), ("DESIGNATED", ["fvoci_disposed"]))
        self.assertIn("기중 처분 이력 있음", r["evidence"])

    def test_disposal_only(self):
        r = judge([F("FVOCI_EQUITY_DISPOSAL", 30, "current_period")])["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["basis_period"]), ("DESIGNATED", "current"))
        self.assertIn("기중 전량 처분·기말 보유 없음", r["evidence"])

    def test_prior_only_without_disposal_unchanged(self):
        r = judge([F("FVOCI_EQUITY", 100, "prior_end")])["FVOCI_DESIGNATION"]
        self.assertIn("당기말 잔액 없음(기중 처분 여부 미확인)", r["evidence"])


def cat_table(rows, cols, scope="separate"):
    return {"table_id": "R:00760:t030", "fs_scope": scope, "status": "verified", "title": "범주별 금융상품",
            "region": {"kind": "note", "note_no": "7", "note_title": "범주별 금융상품", "sub_path": []},
            "unit": {"multiplier": 1000},
            "columns": [{"col": i, "header_path": [h], "role": "value"} for i, h in enumerate(cols, 1)],
            "rows": [{"row": i, "label": lb, "label_path": [lb], "cells": [{"col": c, "value": v} for c, v in vals]}
                     for i, (lb, vals) in enumerate(rows, 1)]}


class D2NoEquityTest(unittest.TestCase):
    def meta(self, t):
        return scope_meta([t], "separate")

    def test_amortized_cost_only(self):
        t = cat_table([("현금및현금성자산", [(1, 10)]), ("매출채권", [(1, 20)])], ["상각후원가측정금융자산"])
        m = self.meta(t)
        self.assertEqual((m["category_fv_asset"], m["category_fvpl_liability"]), (False, False))
        r = judge(**m)["FVOCI_DESIGNATION"]
        self.assertEqual(r["value"], "NO_EQUITY_INSTRUMENT")
        self.assertTrue(r["evidence"].startswith("대상 지분상품 없음: 범주별 금융상품 표"))

    def test_fvpl_asset_blocks(self):
        t = cat_table([("현금및현금성자산", [(1, 10)]), ("기타금융자산", [(2, 5)])],
                      ["상각후원가측정금융자산", "당기손익-공정가치측정금융자산"])
        m = self.meta(t)
        self.assertTrue(m["category_fv_asset"])
        self.assertNotIn("FVOCI_DESIGNATION", judge(**m))

    def test_category_fv_units(self):
        from dart_search_mcp.ledger.judge_rules import _category_fv, _fv_trace
        t = cat_table([("파생상품부채", [(1, 4)]), ("현금", [(2, 9)])], ["당기손익-공정가치측정금융부채", "상각후원가측정금융자산"])
        self.assertEqual(_category_fv(t), (False, True))
        hedge = cat_table([("위험회피파생상품부채", [(1, 4)])], ["당기손익-공정가치측정금융부채"])
        self.assertEqual(_category_fv(hedge), (False, False))
        self.assertTrue(_fv_trace(cat_table([("장기투자증권", [(1, 4)])], ["당기말"])))
        self.assertFalse(_fv_trace(cat_table([("관계기업투자주식", [(1, 4)])], ["당기말"])))

    def test_fv_trace_elsewhere_blocks(self):
        # 범주표는 상각후원가뿐이어도 재무상태표·손익 주석에 공정가치 금융자산·지분증권 금액이 있으면 정하지 않는다
        cat = cat_table([("현금", [(1, 10)])], ["상각후원가측정금융자산"])
        bs = dict(cat_table([("당기손익-공정가치측정금융자산평가이익", [(1, 3)])], ["당기"]), table_id="R:00760:t031",
                  title="금융수익", region={"kind": "note", "note_no": "25", "note_title": "금융수익", "sub_path": []})
        m = scope_meta([cat, bs], "separate")
        self.assertTrue(m["fv_trace_any"])
        self.assertNotIn("FVOCI_DESIGNATION", judge(**m))

    def test_stage1_values_off(self):
        t = cat_table([("현금", [(1, 10)])], ["상각후원가측정금융자산"])
        self.assertNotIn("FVOCI_DESIGNATION", judge(stage1_values=True, **self.meta(t)))

    def test_read_failed_blocks(self):
        t = cat_table([("현금", [(1, 10)])], ["상각후원가측정금융자산"])
        r = judge(**{**self.meta(t), "read_failed_any_topics": {"FVOCI_DESIGNATION"}})["FVOCI_DESIGNATION"]
        self.assertEqual((r["value"], r["held_reason"]), ("UNDETERMINED", "read_failed"))


class D1PresumedTest(unittest.TestCase):
    def meta(self, rows=(("차입금", [(1, 10)]),), cols=("상각후원가측정금융부채",)):
        return scope_meta([cat_table(list(rows), list(cols))], "separate")

    def recs(self):
        return [I("equity", cur={"host_carrying": 900, "equity_component": 50},
                  ev={"balance.current_end.host_carrying": ["R:00760:t040:r3:c1"]})]

    def test_presumed(self):
        recs = self.recs()
        r = judge(ins={"run1": recs, "run2": recs}, **{**self.meta(), "category_table": True})["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual((r["value"], r["value_flags"], r["basis_period"]),
                         ("NOT_SEPARATED_PRESUMED", ["emb_presumed"], "current"))
        self.assertTrue(r["evidence"].startswith("추정(직접 미분리 문장 없음): 제3회 전환사채 주계약 상각후원가"))

    def test_runs_disagree_presumed_vs_undetermined(self):
        # 한 실행만 추정이면 「두 실행 값이 다름」 확인 필요가 아니라 판단불가 그대로
        other = [I("equity", cur={"equity_component": 50})]
        r = judge(ins={"run1": self.recs(), "run2": other}, **{**self.meta(), "category_table": True})
        self.assertNotEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["held_reason"], "needs_review")
        self.assertEqual(r["EMBEDDED_DERIVATIVE_SEPARATION"]["value"], "UNDETERMINED")

    def test_single_run_not_presumed(self):
        r = judge(ins={"run1": self.recs(), "run2": None}, **{**self.meta(), "category_table": True})
        self.assertNotEqual(r.get("EMBEDDED_DERIVATIVE_SEPARATION", {}).get("value"), "NOT_SEPARATED_PRESUMED")

    def test_blockers(self):
        recs = self.recs()
        ins = {"run1": recs, "run2": recs}
        fvpl = self.meta(rows=(("파생상품부채", [(1, 10)]),), cols=("당기손익-공정가치측정금융부채",))
        self.assertTrue(fvpl["category_fvpl_liability"])
        for facts, meta in (([], fvpl),
                            ([F("EMB_DERIV_LIABILITY", 5, label="파생상품부채")], self.meta())):
            r = judge(facts, ins=ins, **{**meta, "category_table": True})
            self.assertNotEqual(r.get("EMBEDDED_DERIVATIVE_SEPARATION", {}).get("value"), "NOT_SEPARATED_PRESUMED")
        no_host = [I("equity", cur={"equity_component": 50})]
        r = judge(ins={"run1": no_host, "run2": no_host}, **{**self.meta(), "category_table": True})
        self.assertNotEqual(r.get("EMBEDDED_DERIVATIVE_SEPARATION", {}).get("value"), "NOT_SEPARATED_PRESUMED")

    def test_not_applicable_prefix(self):
        from tests.test_ledger_judge import M
        r = judge(marks=[M("EMBEDDED_DERIVATIVE_SEPARATION")], category_table=True)["EMBEDDED_DERIVATIVE_SEPARATION"]
        self.assertEqual(r["value"], "NOT_APPLICABLE")
        self.assertTrue(r["evidence"].startswith("대상 상품 없음: "))


class C1RightsTest(unittest.TestCase):
    def test_product_key(self):
        self.assertEqual(product_key({"kind": "CB", "instrument": "제3회 무기명식 전환사채", "series_key": "x"}), "CB-3")
        self.assertEqual(product_key({"kind": "CB", "instrument": "전환사채", "series_key": "CB-?1"}), "CB-1")
        self.assertEqual(product_key({"kind": "EB", "instrument": "교환 사채", "series_key": ""}), "EB:교환사채")

    def test_agreeing_runs_only(self):
        a = I("equity", cur={"equity_component": 5})
        b = I("derivative_liability", cur={"derivative_liability": 5})
        rows = instrument_rights(R, "separate", [a], [a], META)
        self.assertEqual([(r["product_key"], r["right"], r["class"], r["basis_period"]) for r in rows],
                         [("CB-3", "conversion", "EQUITY", "current")])
        self.assertEqual(instrument_rights(R, "separate", [a], [b], META), [])
        self.assertEqual(instrument_rights(R, "separate", [a], None, META), [])

    def test_duplicate_key_dropped(self):
        a = I("equity", cur={"equity_component": 5})
        b = dict(I("equity", cur={"equity_component": 7}), instrument="제3회 전환사채(2)")
        self.assertEqual(instrument_rights(R, "separate", [a, b], [a, b], META), [])


if __name__ == "__main__":
    unittest.main()
