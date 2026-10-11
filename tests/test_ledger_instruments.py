"""2층 ② 사채·우선주 기록 — 입력 묶음(instrument_input)·근거 검증(instrument_validate) 테스트. LLM 없음."""
import copy
import unittest

from dart_search_mcp.ledger.facts import extract_facts, load_concepts
from dart_search_mcp.ledger.instrument_input import (
    MAX_SENTENCES,
    MAX_TABLES,
    build_input,
    needs_instruments,
)
from dart_search_mcp.ledger.instrument_validate import validate
from dart_search_mcp.ledger.tables import build_filing
from tests.test_ledger_facts import _table
from tests.test_ledger_tables import load_fixture

ILDONG = "20260318001076"
METANET = "20251205000494"


def _filing(name):
    return build_filing(name.split("_")[0], load_fixture(name), {})


def _cell(value, *, label, label_path=None, unit_kind="krw", multiplier=1, period="current_end"):
    return {"raw": f"{value:,}", "value": value, "unit_kind": unit_kind, "multiplier": multiplier,
            "period": period, "label": label, "label_path": label_path or [label]}


def _inp():
    """손으로 만든 입력 묶음 — 일동홀딩스 전환사채 표(천원)와 내재파생 표(원)."""
    return {
        "rcept_no": "R",
        "cells": {
            "T1:r3:c1": _cell(1000000, label="전환사채", multiplier=1000),
            "T1:r5:c1": _cell(-103911, label="전환권조정", multiplier=1000),
            "T1:r6:c1": _cell(896089, label="합 계", multiplier=1000),
            "T1:r7:c1": _cell(121352, label="전환사채의 자본요소", multiplier=1000),
            "T1:r7:c2": _cell(519263, label="전환사채의 자본요소", multiplier=1000, period="prior_end"),
            "T2:r3:c4": _cell(2500419206, label="파생상품부채(*)"),
            "T3:r2:c1": _cell(120000, label="전환사채", unit_kind="shares"),
        },
        "tables_text": "",
        "sentences": {
            "s10": "회사는 전환사채를 발행하였습니다.",
            "s11": "전환권은 자본으로 분류하였습니다.",
            "f:T2:1": "(*) 전환상환우선주부채 중 내재파생상품 분리금액입니다.",
        },
    }


def _record(**over):
    rec = {
        "instrument": "제3회 무기명식 무보증 사모 전환사채",
        "series_key": "CB-3",
        "kind": "CB",
        "balance": {
            "current_end": {"face": 1000000, "host_carrying": 896089, "derivative_liability": None,
                            "derivative_asset": None, "equity_component": 121352},
            "prior_end": {"face": None, "host_carrying": None, "derivative_liability": None,
                          "derivative_asset": None, "equity_component": 519263},
        },
        "movement": {"current": ["converted"], "prior": ["issued"]},
        "option_class": "equity",
        "embedded_separation": "not_separated",
        "separated_targets": [],
        "evidence": {
            "option_class": ["T1:r7:c1", "s11"],
            "embedded_separation": ["s11"],
            "balance.current_end.face": ["T1:r3:c1"],
            "balance.current_end.host_carrying": ["T1:r6:c1"],
            "balance.current_end.equity_component": ["T1:r7:c1"],
            "balance.prior_end.equity_component": ["T1:r7:c2"],
        },
    }
    rec.update(over)
    return rec


class ValidatorTest(unittest.TestCase):
    def test_validator_keeps_good_record(self):
        rec = _record()
        out, reasons = validate(copy.deepcopy(rec), _inp())
        self.assertEqual(reasons, [])
        self.assertEqual(out["balance"], rec["balance"])
        self.assertEqual(out["option_class"], "equity")
        self.assertEqual(out["embedded_separation"], "not_separated")
        self.assertEqual(out["series_key"], "CB-3")
        self.assertEqual(out["evidence"], rec["evidence"])

    def test_validator_rejects_missing_ref(self):
        rec = _record(evidence={
            "option_class": ["T9:r1:c1", "s999"],
            "embedded_separation": ["s11"],
            "balance.current_end.face": ["T1:r3:c1"],
            "balance.current_end.host_carrying": ["T1:r6:c1"],
            "balance.current_end.equity_component": ["T7:r7:c1"],
            "balance.prior_end.equity_component": ["T1:r7:c2"],
        })
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["option_class"], "unconfirmed")
        self.assertIsNone(out["balance"]["current_end"]["equity_component"])
        self.assertIn("missing_ref:option_class", reasons)
        self.assertIn("missing_ref:balance.current_end.equity_component", reasons)
        # 다른 필드는 그대로
        self.assertEqual(out["balance"]["prior_end"]["equity_component"], 519263)
        self.assertEqual(out["embedded_separation"], "not_separated")

    def test_validator_value_without_evidence_key_is_missing_ref(self):
        rec = _record()
        del rec["evidence"]["balance.current_end.face"]
        out, reasons = validate(rec, _inp())
        self.assertIsNone(out["balance"]["current_end"]["face"])
        self.assertIn("missing_ref:balance.current_end.face", reasons)

    def test_validator_rejects_amount_mismatch(self):
        rec = _record()
        rec["balance"]["current_end"]["equity_component"] = 121355   # 칸은 121,352
        rec["balance"]["prior_end"]["equity_component"] = 519263000  # 원 환산값 ≠ 원래 단위 값
        out, reasons = validate(rec, _inp())
        self.assertIsNone(out["balance"]["current_end"]["equity_component"])
        self.assertIsNone(out["balance"]["prior_end"]["equity_component"])
        self.assertIn("amount_mismatch:balance.current_end.equity_component", reasons)
        self.assertIn("amount_mismatch:balance.prior_end.equity_component", reasons)

    def test_validator_amount_tolerance_one(self):
        rec = _record()
        rec["balance"]["current_end"]["equity_component"] = 121353
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["balance"]["current_end"]["equity_component"], 121353)
        self.assertEqual(reasons, [])

    def test_validator_rejects_amount_unit_mismatch(self):
        rec = _record()
        rec["balance"]["current_end"]["face"] = 120000
        rec["evidence"]["balance.current_end.face"] = ["T3:r2:c1"]  # 주식 수 칸
        out, reasons = validate(rec, _inp())
        self.assertIsNone(out["balance"]["current_end"]["face"])
        self.assertIn("amount_mismatch:balance.current_end.face", reasons)

    def test_validator_amount_sentence_only_is_mismatch(self):
        rec = _record()
        rec["evidence"]["balance.current_end.face"] = ["s10"]
        out, reasons = validate(rec, _inp())
        self.assertIsNone(out["balance"]["current_end"]["face"])
        self.assertIn("amount_mismatch:balance.current_end.face", reasons)

    def test_validator_separated_amount(self):
        rec = _record(kind="RCPS", series_key="RCPS-1", option_class="derivative_liability",
                      embedded_separation="separated", separated_targets=["conversion", "redemption"],
                      separated_amount={"current_end": 2500419206, "prior_end": 12213899634})
        rec["evidence"].update({"option_class": ["T2:r3:c4", "f:T2:1"], "embedded_separation": ["f:T2:1"],
                                "separated_amount.current_end": ["T2:r3:c4"],
                                "separated_amount.prior_end": ["T2:r3:c4"]})
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["separated_amount"]["current_end"], 2500419206)
        self.assertIsNone(out["separated_amount"]["prior_end"])
        self.assertEqual(reasons, ["amount_mismatch:separated_amount.prior_end"])
        self.assertEqual(out["option_class"], "derivative_liability")

    def test_validator_host_only_not_liability(self):
        """Review Focus 3: 전환사채·전환권조정 행만 근거면 전환권 부채가 아니다."""
        rec = _record(option_class="derivative_liability")
        rec["evidence"]["option_class"] = ["T1:r3:c1", "T1:r5:c1"]
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["option_class"], "unconfirmed")
        self.assertIn("host_only", reasons)

    def test_validator_derivative_liability_needs_derivative_ref(self):
        rec = _record(option_class="derivative_liability")
        rec["evidence"]["option_class"] = ["s10"]
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["option_class"], "unconfirmed")
        self.assertIn("no_derivative_ref", reasons)

    def test_validator_derivative_liability_by_sentence(self):
        inp = _inp()
        inp["sentences"]["s12"] = "전환권은 파생상품부채로 분류하였습니다."
        rec = _record(option_class="derivative_liability")
        rec["evidence"]["option_class"] = ["T1:r3:c1", "s12"]
        out, reasons = validate(rec, inp)
        self.assertEqual(out["option_class"], "derivative_liability")
        self.assertEqual(reasons, [])

    def test_validator_derivative_amount_on_host_row(self):
        """부채요소 칸을 derivative_liability 금액 근거로 대면 버린다."""
        rec = _record()
        rec["balance"]["current_end"]["derivative_liability"] = 1000000
        rec["evidence"]["balance.current_end.derivative_liability"] = ["T1:r3:c1"]
        out, reasons = validate(rec, _inp())
        self.assertIsNone(out["balance"]["current_end"]["derivative_liability"])
        self.assertIn("host_only:balance.current_end.derivative_liability", reasons)

    def test_validator_drops_bad_kind(self):
        out, reasons = validate(_record(kind="BOND"), _inp())
        self.assertIsNone(out)
        self.assertEqual(reasons, ["bad_kind:BOND"])

    def test_validator_bad_enum(self):
        out, reasons = validate(_record(embedded_separation="maybe"), _inp())
        self.assertEqual(out["embedded_separation"], "unconfirmed")
        self.assertIn("bad_value:embedded_separation", reasons)

    def test_series_key_from_name(self):
        out, _ = validate(_record(series_key=""), _inp())
        self.assertEqual(out["series_key"], "CB-3")
        out, _ = validate(_record(series_key=None, kind="RCPS", instrument="제 12 회 상환전환우선주"), _inp())
        self.assertEqual(out["series_key"], "RCPS-12")
        out, _ = validate(_record(series_key="", instrument="상환전환우선주", kind="RCPS"), _inp(), seq=2)
        self.assertEqual(out["series_key"], "RCPS-?2")
        out, _ = validate(_record(series_key="", instrument="전환사채"), _inp())
        self.assertEqual(out["series_key"], "CB-?1")

    def test_validator_does_not_mutate_input(self):
        rec = _record(option_class="derivative_liability")
        before = copy.deepcopy(rec)
        validate(rec, _inp())
        self.assertEqual(rec, before)


class NeedsInstrumentsTest(unittest.TestCase):
    def test_ildong_needs(self):
        f = _filing(f"{ILDONG}_cb.xml")
        facts = extract_facts(f["tables"], load_concepts())
        self.assertTrue(needs_instruments(facts, f["tables"]))

    def test_metanet_needs_by_fact(self):
        f = _filing(f"{METANET}_fv.xml")
        facts = extract_facts(f["tables"], load_concepts())
        self.assertTrue(needs_instruments(facts, f["tables"]))

    def test_plain_bond_note_does_not_need(self):
        t = _table([("사채", [1000, 900])], note_title="사채")
        self.assertFalse(needs_instruments([], [t]))
        self.assertFalse(needs_instruments([{"concept": "LEASE_LIABILITY"}], [t]))

    def test_convertible_note_title_needs(self):
        for title in ("전환사채", "신주인수권부사채", "교환사채", "상환전환우선주부채"):
            t = _table([("x", [1, 2])], note_title=title)
            self.assertTrue(needs_instruments([], [t]), title)

    def test_fact_concepts_need(self):
        t = _table([("x", [1, 2])], note_title="기타")
        for c in ("HOST_LIABILITY", "CONV_OPTION_EQUITY", "CONV_OPTION_LIABILITY", "EMB_DERIV_LIABILITY",
                  "EMB_DERIV_ASSET"):
            self.assertTrue(needs_instruments([{"concept": c}], [t]), c)


class BuildInputTest(unittest.TestCase):
    def test_ildong_cells_and_text(self):
        f = _filing(f"{ILDONG}_cb.xml")
        inp = build_input(ILDONG, f["tables"], f["paragraphs"])
        self.assertEqual(inp["rcept_no"], ILDONG)
        tid = "20260318001076:00761:t002"
        cell = inp["cells"][f"{tid}:r7:c2"]
        self.assertEqual(cell["value"], 519263)
        self.assertEqual(cell["raw"], "519,263")
        self.assertEqual(cell["unit_kind"], "krw")
        self.assertEqual(cell["multiplier"], 1000)
        self.assertEqual(cell["period"], "prior_end")
        self.assertEqual(cell["label"], "전환사채의 자본요소")
        self.assertEqual(inp["cells"][f"{tid}:r7:c1"]["value"], 121352)
        self.assertIn(f"[{tid}]", inp["tables_text"])
        self.assertIn("r7 전환사채의 자본요소 | c1=121,352 | c2=519,263", inp["tables_text"])
        self.assertIn("c1=당기말", inp["tables_text"])
        # 표지(other) 표는 넣지 않는다
        self.assertFalse(any(k.startswith("20260318001076:00761:t001") for k in inp["cells"]))
        # 문장 번호는 1층 문단 id 그대로
        self.assertEqual(inp["sentences"]["s2"], "⑤ 당기말과 전기말 현재 전환사채의 세부내역은 다음과 같습니다.")

    def test_metanet_footnote_sentence(self):
        f = _filing(f"{METANET}_fv.xml")
        inp = build_input(METANET, f["tables"], f["paragraphs"])
        tid = "20251205000494:00760:t002"
        self.assertEqual(inp["cells"][f"{tid}:r3:c4"]["value"], 2500419206)
        self.assertEqual(inp["cells"][f"{tid}:r3:c4"]["multiplier"], 1)
        self.assertEqual(inp["sentences"][f"f:{tid}:1"], "전환상환우선주부채 중 내재파생상품 분리금액입니다.")
        self.assertIn(f"f:{tid}:1", inp["tables_text"])
        self.assertIn("s4", inp["sentences"])

    def test_caps_prefer_instrument_notes(self):
        tables = []
        for i in range(MAX_TABLES + 5):
            tables.append(_table([("파생상품자산", [i, i])], note_title="금융상품", title="금융상품 내역", tid=f"R:00760:t{i:03d}"))
        cb = _table([("전환사채", [1, 2])], note_title="전환사채", title="전환사채 내역", tid="R:00760:t900")
        tables.append(cb)
        paras = [{"id": f"s{k}", "file": "R_00760.xml", "page": 1, "text": f"파생상품 문장 {k}."}
                 for k in range(1, MAX_SENTENCES + 20)]
        paras.append({"id": "s999", "file": "R_00760.xml", "page": 1, "text": "전환사채의 전환가액은 1,000원입니다."})
        inp = build_input("R", tables, paras)
        tids = {k.split(":r")[0] for k in inp["cells"]}
        self.assertEqual(len(tids), MAX_TABLES)
        self.assertIn("R:00760:t900", tids)
        self.assertLessEqual(len(inp["sentences"]), MAX_SENTENCES)
        self.assertIn("s999", inp["sentences"])

    def test_caps_push_tables_without_cells_back(self):
        tables = [_table([("전환사채", [None, None])], note_title="전환사채", title="전환사채 내역",
                         tid=f"R:00760:t{i:03d}") for i in range(MAX_TABLES)]
        tables.append(_table([("파생상품부채", [5, 6])], note_title="금융상품", title="금융상품 내역", tid="R:00760:t900"))
        inp = build_input("R", tables, [])
        self.assertIn("R:00760:t900:r1:c1", inp["cells"])
        self.assertEqual(inp["tables_text"].count("[R:00760:t"), MAX_TABLES)

    def test_validate_against_built_input(self):
        f = _filing(f"{ILDONG}_cb.xml")
        inp = build_input(ILDONG, f["tables"], f["paragraphs"])
        tid = "20260318001076:00761:t002"
        rec = {"instrument": "전환사채", "series_key": "", "kind": "CB",
               "balance": {"current_end": {"equity_component": 121352}, "prior_end": {"equity_component": 519263}},
               "option_class": "derivative_liability", "embedded_separation": "unconfirmed",
               "evidence": {"option_class": [f"{tid}:r3:c1", f"{tid}:r5:c1"],
                            "balance.current_end.equity_component": [f"{tid}:r7:c1"],
                            "balance.prior_end.equity_component": [f"{tid}:r7:c2"]}}
        out, reasons = validate(rec, inp)
        self.assertEqual(out["option_class"], "unconfirmed")
        self.assertEqual(reasons, ["host_only"])
        self.assertEqual(out["balance"]["prior_end"]["equity_component"], 519263)


if __name__ == "__main__":
    unittest.main()
