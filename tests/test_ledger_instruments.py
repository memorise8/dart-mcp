"""2층 ② 사채·우선주 기록 — 입력 묶음(instrument_input)·근거 검증(instrument_validate) 테스트. LLM 없음."""
import copy
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.ledger.facts import extract_facts, load_concepts
from dart_search_mcp.ledger.instrument_input import (
    MAX_SENTENCES,
    MAX_TABLES,
    build_input,
    input_scopes,
    needs_instruments,
)
from dart_search_mcp.ledger import instruments as instruments_mod
from dart_search_mcp.ledger.instruments import (
    OUTPUT_SCHEMA,
    PROMPT_I2,
    PROMPT_VERSION,
    Cache,
    build_messages,
    cache_key,
    extract_instruments,
    run_filings,
    summarize_out,
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
            "T4:r2:c1": _cell(5000, label="파생상품자산"),
            "T4:r3:c1": _cell(7000, label="파생상품부채"),
            "T4:r4:c1": _cell(9000, label="파생상품", label_path=["부채", "파생상품"]),
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


class ValidatorT8FollowupTest(unittest.TestCase):
    """T8 리뷰에서 넘어온 검증기 보강(T9)."""

    def test_derivative_liability_amount_on_total_row(self):
        """일동 r6 「합 계」(부채요소 합계 896,089)·r7 자본요소는 derivative_liability 금액 근거가 아니다."""
        for value, cid in ((896089, "T1:r6:c1"), (121352, "T1:r7:c1")):
            rec = _record()
            rec["balance"]["current_end"]["derivative_liability"] = value
            rec["evidence"]["balance.current_end.derivative_liability"] = [cid]
            out, reasons = validate(rec, _inp())
            self.assertIsNone(out["balance"]["current_end"]["derivative_liability"], cid)
            self.assertIn("not_derivative:balance.current_end.derivative_liability", reasons)
            self.assertEqual(out["evidence"]["balance.current_end.derivative_liability"], [])
            self.assertNotIn("balance.current_end.derivative_liability", out["matched"])

    def test_derivative_amount_side(self):
        cases = [("derivative_liability", 7000, "T4:r3:c1", True),
                 ("derivative_liability", 9000, "T4:r4:c1", True),   # 부채 > 파생상품
                 ("derivative_liability", 5000, "T4:r2:c1", False),  # 파생상품자산
                 ("derivative_asset", 5000, "T4:r2:c1", True),
                 ("derivative_asset", 7000, "T4:r3:c1", False)]
        for key, value, cid, ok in cases:
            rec = _record()
            rec["balance"]["current_end"][key] = value
            rec["evidence"][f"balance.current_end.{key}"] = [cid]
            out, reasons = validate(rec, _inp())
            got = out["balance"]["current_end"][key]
            self.assertEqual(got, value if ok else None, (key, cid))
            if not ok:
                self.assertIn(f"not_derivative:balance.current_end.{key}", reasons)

    def test_matched_cell_and_evidence_narrowed(self):
        rec = _record()
        rec["evidence"]["balance.current_end.face"] = ["s10", "T1:r6:c1", "T1:r3:c1"]
        rec["balance"]["current_end"]["equity_component"] = 1   # 맞는 칸 없음
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["matched"]["balance.current_end.face"], "T1:r3:c1")
        self.assertEqual(out["matched_multiplier"]["balance.current_end.face"], 1000)
        self.assertEqual(out["evidence"]["balance.current_end.face"], ["T1:r3:c1"])
        self.assertEqual(out["evidence"]["balance.current_end.equity_component"], [])
        self.assertNotIn("balance.current_end.equity_component", out["matched"])
        self.assertEqual(reasons, ["amount_mismatch:balance.current_end.equity_component"])

    def test_null_enums_unconfirmed_without_reason(self):
        rec = _record(option_class=None)
        del rec["embedded_separation"]
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["option_class"], "unconfirmed")
        self.assertEqual(out["embedded_separation"], "unconfirmed")
        self.assertEqual(reasons, [])

    def test_option_liability_not_by_asset_cell_or_negative_sentence(self):
        inp = _inp()
        inp["sentences"]["s20"] = "전환권은 파생상품부채로 분류하지 않고 자본으로 분류하였습니다."
        inp["sentences"]["s21"] = "매도청구권은 파생상품자산으로 인식하였습니다."
        for ids in (["T4:r2:c1"], ["s20"], ["s21"], ["T4:r2:c1", "s20"]):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], "unconfirmed", ids)
            self.assertIn("no_derivative_ref", reasons)
        for ids in (["T4:r3:c1"], ["T4:r4:c1"]):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], "derivative_liability", ids)


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


class BuildInputFollowupTest(unittest.TestCase):
    def test_footnote_cap_no_dangling_ids(self):
        tables = []
        for i in range(30):
            t = _table([("전환사채", [1, 2])], note_title="전환사채", title="전환사채 내역", tid=f"R:00760:t{i:03d}")
            t["footnotes"] = [{"mark": f"(*{k})", "text": f"각주 {i}-{k}"} for k in range(1, 11)]
            tables.append(t)
        inp = build_input("R", tables, [])
        import re
        rendered = set(re.findall(r"^(f:\S+)", inp["tables_text"], re.M))
        self.assertEqual(len(rendered), MAX_SENTENCES)
        self.assertTrue(rendered <= set(inp["sentences"]))
        self.assertLessEqual(len(inp["sentences"]), MAX_SENTENCES)

    def test_per_scope(self):
        sep = _table([("전환사채", [1, 2])], note_title="전환사채", tid="R:00760:t001")
        con = _table([("전환사채", [3, 4])], note_title="전환사채", tid="R:00761:t001")
        other = _table([("매출", [5, 6])], note_title="수익", title="매출 내역", tid="R:00761:t002")
        sep.update(fs_scope="separate", file="R_00760.xml")
        con.update(fs_scope="consolidated", file="R_00761.xml")
        other.update(fs_scope="consolidated", file="R_00761.xml")
        paras = [{"id": "s1", "file": "R_00760.xml", "page": 1, "text": "별도 전환사채 문장."},
                 {"id": "s2", "file": "R_00761.xml", "page": 1, "text": "연결 전환사채 문장."},
                 {"id": "s3", "file": "R.xml", "page": 1, "text": "범위 모르는 전환사채 문장."}]
        self.assertEqual(input_scopes([sep, con, other]), ["consolidated", "separate"])
        self.assertEqual(input_scopes([other]), [])
        inp = build_input("R", [sep, con, other], paras, fs_scope="separate")
        self.assertEqual(inp["fs_scope"], "separate")
        self.assertEqual({k.split(":r")[0] for k in inp["cells"]}, {"R:00760:t001"})
        self.assertEqual(set(inp["sentences"]), {"s1", "s3"})
        inp = build_input("R", [sep, con, other], paras, fs_scope="consolidated")
        self.assertEqual({k.split(":r")[0] for k in inp["cells"]}, {"R:00761:t001"})
        self.assertEqual(set(inp["sentences"]), {"s2", "s3"})
        self.assertNotIn("fs_scope", build_input("R", [sep], paras))


class FakeLLM:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.response)


class ExtractInstrumentsTest(unittest.TestCase):
    def test_keeps_validated_records(self):
        good = _record()
        bad = _record(kind="BOND")
        partly = _record(instrument="제4회 전환사채", series_key="CB-4")
        partly["evidence"]["option_class"] = ["T9:r1:c1"]
        llm = FakeLLM({"instruments": [good, bad, partly, "x"]})
        out = extract_instruments(_inp(), llm, None, model="luna")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(out["rcept_no"], "R")
        self.assertEqual(out["prompt_version"], PROMPT_VERSION)
        self.assertEqual(out["model"], "luna")
        self.assertEqual([r["series_key"] for r in out["records"]], ["CB-3", "CB-4"])
        self.assertEqual(out["records"][0]["issues"], [])
        self.assertEqual(out["records"][1]["option_class"], "unconfirmed")
        self.assertIn("missing_ref:option_class", out["records"][1]["issues"])
        self.assertEqual(out["dropped"], ["bad_kind:BOND", "bad_record"])

    def test_host_liability_not_option_liability(self):
        """Review Focus 3: 전환사채·전환권조정·합계만 있고 파생상품부채 칸이 없으면 전환권 분류는 부채가 아니다."""
        f = _filing(f"{ILDONG}_cb.xml")
        inp = build_input(ILDONG, f["tables"], f["paragraphs"])
        tid = "20260318001076:00761:t002"
        rec = {"instrument": "전환사채", "series_key": "CB-1", "kind": "CB",
               "balance": {"current_end": {"face": 1000000, "host_carrying": 896089,
                                           "derivative_liability": 896089, "derivative_asset": None,
                                           "equity_component": None}},
               "option_class": "derivative_liability", "embedded_separation": "separated",
               "separated_targets": ["conversion"],
               "evidence": {"option_class": [f"{tid}:r3:c1", f"{tid}:r5:c1", f"{tid}:r6:c1"],
                            "embedded_separation": [f"{tid}:r5:c1"],
                            "balance.current_end.face": [f"{tid}:r3:c1"],
                            "balance.current_end.host_carrying": [f"{tid}:r6:c1"],
                            "balance.current_end.derivative_liability": [f"{tid}:r6:c1"]}}
        out = extract_instruments(inp, FakeLLM({"instruments": [rec]}), None)
        r = out["records"][0]
        self.assertEqual(r["option_class"], "unconfirmed")
        self.assertIsNone(r["balance"]["current_end"]["derivative_liability"])
        self.assertEqual(r["balance"]["current_end"]["host_carrying"], 896089)
        self.assertEqual(r["matched"]["balance.current_end.host_carrying"], f"{tid}:r6:c1")
        self.assertEqual(r["matched_multiplier"]["balance.current_end.host_carrying"], 1000)
        self.assertIn("no_derivative_ref", r["issues"])

    def test_cache_skips_second_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.jsonl"
            llm = FakeLLM({"instruments": [_record()]})
            first = extract_instruments(_inp(), llm, Cache(path))
            second = extract_instruments(_inp(), llm, Cache(path))   # 파일에서 다시 읽은 캐시
            self.assertEqual(len(llm.calls), 1)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(first["records"], second["records"])
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["key"], cache_key(_inp()))
            self.assertEqual(row["response"], {"instruments": [_record()]})   # 원 응답
            other = _inp()
            other["sentences"]["s99"] = "다른 입력."
            extract_instruments(other, llm, Cache(path))
            self.assertEqual(len(llm.calls), 2)

    def test_llm_error(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Cache(Path(d) / "c.jsonl")
            out = extract_instruments(_inp(), FakeLLM(exc=RuntimeError("boom")), cache)
            self.assertEqual(out["records"], [])
            self.assertEqual(out["dropped"], ["llm_error"])
            self.assertNotIn(cache_key(_inp()), cache)

    def test_bad_json(self):
        for resp in ("{not json", {"answer": []}, {"instruments": "x"}, ["x"]):
            out = extract_instruments(_inp(), FakeLLM(resp), None)
            self.assertEqual(out["records"], [], resp)
            self.assertEqual(out["dropped"], ["bad_json"], resp)
        out = extract_instruments(_inp(), FakeLLM(json.dumps({"instruments": [_record()]})), None)
        self.assertEqual(len(out["records"]), 1)

    def test_messages(self):
        inp = _inp()
        inp["tables_text"] = "[T1] (separate) / 단위: (단위: 천원)\nr3 전환사채 | c1=1,000,000"
        msgs = build_messages(inp)
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertEqual(msgs[0]["content"], PROMPT_I2["system"])
        for phrase in ("전환사채·신주인수권부사채·교환사채·상환전환우선주", "표 칸 id와 문장 id로만",
                       "null 또는 unconfirmed", "부채요소(주계약)와 전환권 파생상품부채는 다른 칸"):
            self.assertIn(phrase, msgs[0]["content"])
        user = msgs[1]["content"]
        self.assertIn(inp["tables_text"], user)
        self.assertIn("s11: 전환권은 자본으로 분류하였습니다.", user)
        self.assertIn("f:T2:1: ", user)
        for phrase in ("천원 숫자를 그대로", "「사채할인발행차금」·「상환할증금」·「합계」", "만들어 내지 않는다",
                       '"option_class": "equity"', "derivative_liability", "whole_fvpl"):
            self.assertIn(phrase, user)
        self.assertEqual(OUTPUT_SCHEMA["required"], ["instruments"])


def _write_gz(path: Path, obj) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def _run_dir(d: Path):
    """1층 결과 흉내: 일동(대상, 연결), 일반(대상 아님), 표 0개 대상(사실만 있음), 두 범위 공시."""
    tables_dir = d / "tables"
    tables_dir.mkdir()
    ildong = _filing(f"{ILDONG}_cb.xml")
    _write_gz(tables_dir / f"{ILDONG}.json.gz", ildong)
    plain = _table([("매출", [1, 2])], note_title="수익", title="매출 내역", tid="P:00760:t001")
    _write_gz(tables_dir / "P.json.gz", {"rcept_no": "P", "tables": [plain], "paragraphs": []})
    _write_gz(tables_dir / "Z.json.gz", {"rcept_no": "Z", "tables": [], "paragraphs": []})
    sep = _table([("전환사채", [1, 2])], note_title="전환사채", tid="B:00760:t001")
    con = _table([("전환사채", [3, 4])], note_title="전환사채", tid="B:00761:t001")
    sep.update(fs_scope="separate", file="B_00760.xml")
    con.update(fs_scope="consolidated", file="B_00761.xml")
    _write_gz(tables_dir / "B.json.gz", {"rcept_no": "B", "tables": [sep, con], "paragraphs": []})
    ildong_facts = extract_facts(ildong["tables"], load_concepts())
    facts = {"Z": [{"rcept_no": "Z", "concept": "HOST_LIABILITY", "fs_scope": "separate"}], ILDONG: ildong_facts}
    return tables_dir, facts, [ILDONG, "P", "Z", "B", "MISSING"]


class RunFilingsTest(unittest.TestCase):
    def test_run_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tables_dir, facts, rcepts = _run_dir(d)
            out = d / "out" / "instruments.jsonl"
            llm = FakeLLM({"instruments": []})
            summary = run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=rcepts,
                                  complete_json=llm, model="dry", workers=2,
                                  usage_getter=lambda: {"prompt_tokens": 1})
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            keys = sorted((r["rcept_no"], r["fs_scope"]) for r in rows)
            self.assertEqual(keys, [(ILDONG, "consolidated"), ("B", "consolidated"), ("B", "separate"),
                                    ("Z", None)])
            z = next(r for r in rows if r["rcept_no"] == "Z")
            self.assertEqual(z["skipped"], "no_tables")
            il = next(r for r in rows if r["rcept_no"] == ILDONG)
            self.assertEqual((il["prompt_version"], il["model"], il["records"], il["dropped"]), (PROMPT_VERSION, "dry", [], []))
            self.assertEqual(il["usage"], {"prompt_tokens": 1})
            self.assertGreater(il["n_cells"], 0)
            self.assertEqual(len(llm.calls), 3)
            self.assertEqual(summary["needs"], 3)
            self.assertEqual(summary["done"], 3)
            self.assertEqual(summary["skipped_no_tables"], 1)
            self.assertEqual(summary["missing"], 1)
            # 다시 돌리면 아무것도 부르지 않는다
            summary = run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=rcepts,
                                  complete_json=llm, model="dry", workers=1)
            self.assertEqual(len(llm.calls), 3)
            self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 4)
            self.assertEqual(summary["already_done"], 3)

    def test_llm_error_rows_retried_and_limit(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tables_dir, facts, rcepts = _run_dir(d)
            out = d / "i.jsonl"
            run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=rcepts,
                        complete_json=FakeLLM(exc=RuntimeError("x")), model="m", workers=1, limit=1)
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(r["rcept_no"], r["dropped"]) for r in rows], [(ILDONG, ["llm_error"])])
            llm = FakeLLM({"instruments": []})
            run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=[ILDONG],
                        complete_json=llm, model="m", workers=1)
            self.assertEqual(len(llm.calls), 1)
            last = json.loads(out.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last["dropped"], [])


class ListNeedsTest(unittest.TestCase):
    def test_list_needs(self):
        from dart_search_mcp.ledger.instruments import list_needs
        with tempfile.TemporaryDirectory() as d:
            tables_dir, facts, rcepts = _run_dir(Path(d))
            self.assertEqual(list_needs(tables_dir, facts, rcepts),
                             [(ILDONG, ["consolidated"]), ("Z", []), ("B", ["consolidated", "separate"])])


class BackendScriptDryTest(unittest.TestCase):
    def test_dry_main(self):
        path = Path(__file__).resolve().parent.parent / "scripts" / "ledger_instruments_in_backend.py"
        spec = importlib.util.spec_from_file_location("ledger_instruments_in_backend", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tables_dir, facts, rcepts = _run_dir(d)
            ledger = d / "ledger"
            ledger.mkdir()
            lines = [f for fs in facts.values() for f in fs]
            lines.append({"rcept_no": "P", "fs_scope": "separate", "concept": "LEASE_LIABILITY"})
            (ledger / "facts.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in lines),
                                                encoding="utf-8")
            (d / "list.txt").write_text("\n".join(rcepts) + "\n", encoding="utf-8")
            out = d / "out" / "instruments.jsonl"
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mod.main(["--tables", str(tables_dir), "--ledger", str(ledger), "--out", str(out),
                               "--rcept-list", str(d / "list.txt"), "--workers", "2", "--dry"])
            self.assertEqual(rc, 0)
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(r["model"] == "dry" for r in rows))
            summary = json.loads((d / "out" / "instruments.jsonl.summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["done"], 3)
            self.assertFalse((d / "out" / "instruments.jsonl.cache.jsonl").exists())
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mod.main(["--tables", str(tables_dir), "--ledger", str(ledger), "--out", str(out),
                               "--rcept-list", str(d / "list.txt"), "--needs-out", str(d / "needs.tsv")])
            self.assertEqual(rc, 0)
            self.assertEqual((d / "needs.tsv").read_text(encoding="utf-8").splitlines(),
                             [f"{ILDONG}\tconsolidated", "Z\t", "B\tconsolidated,separate"])


def _option_table_input():
    """20260331002536:00760:t108(파생상품 주석 「20.2 … 파생상품부채의 내역」)·20250822000274 t178·
    파생상품자산 표·전환사채 표를 본뜬 손 표 → build_input."""
    liab = _table([(["파생상품부채(주주간약정)(*)"], [14075409, 31265969]),
                   (["전환권(상환전환우선주부채)"], [None, 1042788774]),
                   (["조기상환권(상환전환우선주부채)"], [None, 2112984200]),
                   (["전환권조정"], [-5, -6]),
                   (["합 계"], [14075409, 3187038943])],
                  note_title="파생상품", title="20.2 보고기간종료일 현재 파생상품부채의 내역은 다음과 같습니다.",
                  tid="R:00760:t108")
    eb = _table([(["교환사채 조기상환권"], [None, 1551072])], note_title="파생상품부채",
                title="(1) 당기말 및 전기말 현재 파생상품부채의 내역은 다음과 같습니다.", tid="R:00760:t178")
    asset = _table([(["매도청구권"], [700, 800])], note_title="파생상품",
                   title="파생상품자산의 내역", tid="R:00760:t200")
    both = _table([(["전환권"], [900, 901])], note_title="파생상품",
                  title="파생상품자산 및 파생상품부채의 내역", tid="R:00760:t201")
    cb = _table([(["전환권"], [300, 301])], note_title="전환사채", title="전환사채 내역", tid="R:00760:t202")
    return build_input("R", [liab, eb, asset, both, cb], [])


class OptionRowInDerivativeTableTest(unittest.TestCase):
    """fix round 1-1: 파생상품부채 표 안의 옵션 이름 행(「전환권(…)」·「조기상환권」)은 표 제목으로 쪽을 정한다."""

    def _amount(self, inp, key, value, cid):
        rec = _record()
        rec["balance"]["current_end"][key] = value
        rec["evidence"][f"balance.current_end.{key}"] = [cid]
        return validate(rec, inp)

    def test_cells_carry_titles(self):
        inp = _option_table_input()
        c = inp["cells"]["R:00760:t108:r2:c2"]
        self.assertEqual(c["table_title"], "20.2 보고기간종료일 현재 파생상품부채의 내역은 다음과 같습니다.")
        self.assertEqual(c["note_title"], "파생상품")

    def test_option_rows_in_liability_table(self):
        inp = _option_table_input()
        for value, cid in ((1042788774, "R:00760:t108:r2:c2"), (2112984200, "R:00760:t108:r3:c2"),
                           (1551072, "R:00760:t178:r1:c2")):
            out, reasons = self._amount(inp, "derivative_liability", value, cid)
            self.assertEqual(out["balance"]["current_end"]["derivative_liability"], value, cid)
            self.assertEqual([r for r in reasons if "derivative" in r], [], cid)
            self.assertEqual(out["matched"]["balance.current_end.derivative_liability"], cid)
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = [cid]
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], "derivative_liability", cid)

    def test_option_rows_excluded_cases(self):
        inp = _option_table_input()
        cases = [("derivative_liability", -5, "R:00760:t108:r4:c1", "host_only"),        # 전환권조정
                 ("derivative_liability", 14075409, "R:00760:t108:r5:c1", "not_derivative"),  # 합 계
                 ("derivative_liability", 700, "R:00760:t200:r1:c1", "not_derivative"),  # 자산 표
                 ("derivative_liability", 900, "R:00760:t201:r1:c1", "not_derivative"),  # 쪽 모름
                 ("derivative_liability", 300, "R:00760:t202:r1:c1", "not_derivative")]  # 파생 표 아님
        for key, value, cid, reason in cases:
            out, reasons = self._amount(inp, key, value, cid)
            self.assertIsNone(out["balance"]["current_end"][key], cid)
            self.assertIn(f"{reason}:balance.current_end.{key}", reasons, cid)
        out, reasons = self._amount(inp, "derivative_asset", 700, "R:00760:t200:r1:c1")
        self.assertEqual(out["balance"]["current_end"]["derivative_asset"], 700)
        for cid in ("R:00760:t108:r4:c1", "R:00760:t200:r1:c1", "R:00760:t202:r1:c1"):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = [cid]
            out, _ = validate(rec, inp)
            self.assertEqual(out["option_class"], "unconfirmed", cid)


class MalformedRecordTest(unittest.TestCase):
    """fix round 1-2: 형이 틀린 LLM 기록이 실행을 멈추지 않는다."""

    def test_validate_tolerates_wrong_types(self):
        bad = [_record(movement=["issued"]), _record(evidence=["T1:r3:c1"]), _record(series_key=3),
               _record(balance=["x"]), _record(balance={"current_end": [1, 2]}), _record(separated_targets="x"),
               _record(evidence={"option_class": "T1:r7:c1"}), _record(movement={"current": "issued"}),
               _record(instrument=None, series_key=None)]
        for rec in bad:
            out, reasons = validate(rec, _inp())
            self.assertIsNotNone(out)
            self.assertIsInstance(out["series_key"], str)
            self.assertIsInstance(out["evidence"], dict)
            self.assertIsInstance(out["movement"], dict)
        out, _ = validate(_record(evidence=["T1:r3:c1"]), _inp())
        self.assertIsNone(out["balance"]["current_end"]["face"])
        out, _ = validate(_record(movement=["issued"]), _inp())
        self.assertEqual(out["movement"], {"current": [], "prior": []})

    def test_extract_drops_crashing_record_and_resume_does_not_crash(self):
        resp = {"instruments": [_record(movement=["issued"], evidence=["T1:r3:c1"]), _record()]}
        with tempfile.TemporaryDirectory() as d:
            cache = Cache(Path(d) / "c.jsonl")
            out = extract_instruments(_inp(), FakeLLM(resp), cache)
            self.assertEqual(len(out["records"]), 2)
            orig = instruments_mod.validate

            def boom(rec, inp, **kw):
                if rec.get("series_key") == "CB-3" and rec.get("movement") == ["issued"]:
                    raise AttributeError("x")
                return orig(rec, inp, **kw)
            instruments_mod.validate = boom
            try:
                out = extract_instruments(_inp(), FakeLLM(exc=AssertionError("must not call")), Cache(Path(d) / "c.jsonl"))
            finally:
                instruments_mod.validate = orig
            self.assertTrue(out["cached"])
            self.assertEqual(out["dropped"], ["bad_record"])
            self.assertEqual(len(out["records"]), 1)


class ModelUsedTest(unittest.TestCase):
    def test_cache_keeps_model_used(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.jsonl"
            usage = lambda: {"prompt_tokens": 5, "model_used": "gpt-5.6-luna"}  # noqa: E731
            first = extract_instruments(_inp(), FakeLLM({"instruments": []}), Cache(path), model="luna",
                                        usage_getter=usage)
            self.assertEqual(first["usage"], {"prompt_tokens": 5, "model_used": "gpt-5.6-luna"})
            self.assertEqual(first["model_used"], "gpt-5.6-luna")
            second = extract_instruments(_inp(), FakeLLM(exc=AssertionError("no")), Cache(path), model="luna",
                                         usage_getter=usage)
            self.assertTrue(second["cached"])
            self.assertIsNone(second["usage"])
            self.assertEqual(second["model_used"], "gpt-5.6-luna")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["model_used"], "gpt-5.6-luna")

    def test_model_used_defaults_to_model(self):
        out = extract_instruments(_inp(), FakeLLM({"instruments": []}), None, model="luna")
        self.assertEqual(out["model_used"], "luna")


class PromptExampleTest(unittest.TestCase):
    def test_example_has_no_synthetic_series_key(self):
        self.assertNotIn("CB-?", PROMPT_I2["example"])
        self.assertIn('"series_key": ""', PROMPT_I2["example"])
        self.assertNotIn("CB-?", build_messages(_inp())[1]["content"])


class ResumeAndSummaryTest(unittest.TestCase):
    def test_bad_json_rows_retried(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tables_dir, facts, _ = _run_dir(d)
            out = d / "i.jsonl"
            run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=[ILDONG],
                        complete_json=FakeLLM("{broken"), model="m", workers=1)
            llm = FakeLLM({"instruments": []})
            run_filings(tables_dir=tables_dir, facts=facts, out_path=out, rcepts=[ILDONG],
                        complete_json=llm, model="m", workers=1)
            self.assertEqual(len(llm.calls), 1)

    def test_summarize_last_line_per_key(self):
        rec_ok = {"series_key": "CB-1", "issues": []}
        rec_bad = {"series_key": "CB-2", "issues": ["missing_ref:option_class", "amount_mismatch:balance.x"]}
        rows = [
            {"rcept_no": "A", "fs_scope": "separate", "records": [], "dropped": ["llm_error"], "model_used": None},
            {"rcept_no": "A", "fs_scope": "separate", "records": [rec_ok, rec_bad], "dropped": ["bad_kind:BOND"],
             "model_used": "gpt-5.6-luna", "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
            {"rcept_no": "A", "fs_scope": "consolidated", "records": [rec_ok], "dropped": [],
             "model_used": "gpt-5.6-luna", "cached": True},
            {"rcept_no": "B", "fs_scope": None, "skipped": "no_tables", "records": [], "dropped": []},
            {"rcept_no": "C", "fs_scope": "separate", "records": [], "dropped": ["bad_json"]},
        ]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "i.jsonl"
            out.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"cut', encoding="utf-8")
            s = summarize_out(out)
        self.assertEqual(s["lines"], 5)
        self.assertEqual(s["keys"], 4)
        self.assertEqual(s["filings"], 3)
        self.assertEqual(s["skipped_no_tables"], 1)
        self.assertEqual(s["records"], 3)
        self.assertEqual(s["records_all_pass"], 2)
        self.assertEqual(s["evidence_pass_rate"], round(2 / 3, 4))
        self.assertEqual(s["issues"], {"amount_mismatch": 1, "missing_ref": 1})
        self.assertEqual(s["dropped"], {"bad_json": 1, "bad_kind": 1})
        self.assertEqual(s["model_used"], {"gpt-5.6-luna": 2})
        self.assertEqual(s["usage"]["prompt_tokens"], 10)
        self.assertEqual(s["cached"], 1)

    def test_script_summarize_and_needs_unreadable(self):
        path = Path(__file__).resolve().parent.parent / "scripts" / "ledger_instruments_in_backend.py"
        spec = importlib.util.spec_from_file_location("ledger_instruments_in_backend_s", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tables_dir, facts, rcepts = _run_dir(d)
            (tables_dir / "BROKEN.json.gz").write_bytes(b"not gzip")
            ledger = d / "ledger"
            ledger.mkdir()
            (ledger / "facts.jsonl").write_text("", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.main(["--tables", str(tables_dir), "--ledger", str(ledger), "--out", str(d / "o.jsonl"),
                          "--needs-out", str(d / "needs.tsv")])
            self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1])["unreadable"], 1)
            out = d / "o.jsonl"
            out.write_text(json.dumps({"rcept_no": "A", "fs_scope": "separate", "records": [{"issues": []}],
                                       "dropped": []}) + "\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = mod.main(["--out", str(out), "--summarize"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1])["evidence_pass_rate"], 1.0)
            self.assertTrue((d / "o.jsonl.measure.json").exists())


def _dcell(label, *, path=None, col="당기말", tt="", nt="", value=100, multiplier=1000, unit_kind="krw"):
    return {"raw": f"{value:,}", "value": value, "unit_kind": unit_kind, "multiplier": multiplier,
            "period": "current_end", "label": label, "label_path": path or [label], "col_header": col,
            "table_title": tt, "note_title": nt}


_EXERCISE_TT = "(3) 발행자의 주가 변동에 따라 행사가격이 조정되는 조건으로 인해 부채로 분류된 각 금융부채의 장부금액은 다음과 같습니다."


class MeasuredDerivativeRowsTest(unittest.TestCase):
    """측정 1회 행 모양 + R14(정밀도 우선) 재검토 사례. 괄호는 출처 공시·표."""

    def test_accepted_sides(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cases = [
            # 20260403002552 t103 — 자산·부채 두 열 변동표: 열 머리글이 파생상품자산을 말한다
            (_dcell("기말", col="전기/파생상품자산",
                    tt="(5) 당기 및 전기의 전환사채와 관련된 파생상품자산과 파생상품부채의 변동내역은 다음과 같습니다."),
             "asset"),
            (_dcell("기말", col="당기/파생상품부채", tt="전환사채 및 파생상품부채의 변동내역"), "liability"),
            (_dcell("Series C", col="당기말/파생상품부채", tt="3) 보고기간종료일 현재 우선주부채의 세부내역은 다음과 같습니다.",
                    nt="우선주"), "liability"),                                                   # 20260327000673 t132
            # 옵션 행·내재파생상품 행 + 제목이 명시적으로 파생상품부채
            (_dcell("전환권(상환전환우선주부채)", tt="20.2 보고기간종료일 현재 파생상품부채의 내역은 다음과 같습니다.",
                    nt="파생상품"), "liability"),
            (_dcell("제7회 상환전환우선주 내재파생상품", tt="당기말과 전기말 현재 파생상품부채의 내역"), "liability"),
            (_dcell("파생상품부채(*)", tt="공정가치 서열체계"), "liability"),
            (_dcell("매도청구권", tt="파생상품자산의 내역", nt="파생상품"), "asset"),
        ]
        for cell, side in cases:
            self.assertEqual(_deriv_side(cell), side, cell["label_path"])

    def test_still_rejected(self):
        """Review Focus 3 + R14: 부채요소·합계·상품 전체 금액, 쪽 근거가 명시적이지 않은 행은 파생상품 칸이 아니다."""
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cases = [
            # R14-1: 상품 이름 행은 제목만으로 파생이 아니다
            _dcell("전환상환우선주부채", nt="전환상환우선주",                                    # 20260414001859 t061 r1
                   tt="17. 전환상환우선주부채 및 파생상품부채(1) 당기말 및 전기말 현재 전환상환우선주부채 및 파생상품부채의 내역은 다음과 같습니다."),
            _dcell("신주인수권부사채", col="당기말/유동", nt="차입금 및 사채",                    # 20260324000545 t092 r3
                   tt="(1) 당기말 및 전기말 현재 신주인수권부사채 및 파생상품부채의 구성내역은 다음과 같습니다."),
            _dcell("전환상환우선주부채(*)", nt="전환상환우선주",                                 # 20260318001243 t348 r1
                   tt="38. 전환상환우선주(1) 당기말 현재 전환상환우선주부채 및 파생상품부채의 구성내역은 다음과 같습니다."),
            _dcell("우선주부채", col="전기초", nt="우선주",                                     # 20260323001721 t118 r1
                   tt="(2) 전기 중 우선주부채와 우선주파생상품부채의 변동내역은 다음과 같습니다."),
            _dcell("4회차 전환상환우선주", tt="당기말과 전기말 현재 파생상품부채의 내역"),
            _dcell("제1종 전환상환우선주", tt=_EXERCISE_TT, nt="금융부채"),                      # 20251002000318 t112
            # R14-2: 비파생금융부채는 파생 쪽 근거가 아니다(20260323001499 t125 만기분석)
            _dcell("상환전환우선주 내재파생상품", tt="당기말 현재 비파생금융부채의 계약에 따른 만기분석은 다음과 같습니다."),
            # R14-3: 기말 행은 열 머리글이 파생·옵션을 말해야 한다
            _dcell("기말", col="전환사채", tt="전환사채 및 파생상품부채의 변동내역"),
            _dcell("기말", col="당기", tt="(2) 보고기간 종료일 현재 파생상품부채의 변동내역은 다음과 같습니다."),
            _dcell("보고기간종료일 장부금액", path=["제6차 전환상환우선주", "보고기간종료일 장부금액"], col="전기",
                   tt="파생상품부채로 인식된 전환상환우선주의 내재파생상품이 당기손익에 미치는 영향은 다음과 같습니다."),
            # R14-5: 제목의 일반 「부채」 낱말로 옵션 행 쪽을 정하지 않는다, 전환권가치·대가는 옵션 행이 아니다
            _dcell("제7회 상환전환우선주 내재파생상품", tt=_EXERCISE_TT, nt="상환전환우선주"),       # 20260403002153 t137
            _dcell("제3회 전환사채", path=["제3회 전환사채", "전환권/조기상환권"], tt=_EXERCISE_TT, nt="전환사채"),
            _dcell("전환권가치", tt="전환사채의 부채요소와 자본요소의 내역", nt="전환사채"),
            _dcell("전환권대가", tt="파생상품부채의 내역"),
            # R14-7: 이름 기본값 없음(20260402002479 매도청구권 = 지배기업에 준 콜, 20260319000958 조기상환청구권)
            _dcell("매도청구권", tt="전환사채의 내용", nt="전환사채"),
            _dcell("조기상환청구권(주2)", tt="(3) 전환사채1) 전환사채의 내용은 다음과 같습니다.", nt="차입금"),
            _dcell("당기말", col="제4회 전환사채 매도청구권(Call Option)/당 기",
                   tt="21.4 보고기간말 현재 전환사채관련 파생상품의 변동내역은 다음과 같습니다.", nt="전환사채"),
            _dcell("조기매도청구권1차", tt="(3) 보고기간 종료일 현재 공정가치로 측정하는 파생상품의 내역은 다음과 같습니다.",
                   nt="공정가치측정금융자산"),
            # 그대로 지키는 것
            _dcell("제13회 전환사채 및 조기상환권", tt="파생상품부채의 내역"),
            _dcell("제1회 기명식 무보증 사모 전환사채", col="전환권가치", value=-9641342,
                   tt="(3) 보고기간 종료일 현재 전환사채의 세부내역은 다음과 같습니다.(당 기)", nt="전환사채"),
            _dcell("전환권조정", tt="파생상품부채의 내역"),
            _dcell("합 계", tt="파생상품부채의 내역"),
            _dcell("상환전환우선주 내재파생상품", col="공정가치", tt="① 당기말", nt="금융상품 공정가치"),
            _dcell("기말", col="당기", tt="(5) 전환사채와 관련된 파생상품자산과 파생상품부채의 변동내역"),
            # 말뭉치 점검(1,500건)에서 나온 잔액 아닌 칸·합친 열
            _dcell("제5회 전환사채", col="감소/파생상품평가이익", nt="사채",                   # 20250728000408 t162
                   tt="내재파생상품(조기상환청구권, 전환권 등)을 주계약인 사채에서 분리하여 파생상품부채로 인식하였습니다."),
            _dcell("㈜신한은행", path=["유의적인 영향력을행사하는 기업", "㈜신한은행"],      # 20260317000323 t165
                   col="상환전환우선주부채및 파생상품부채", tt="특수관계자 채권채무", nt="특수관계자와의 거래"),
            _dcell("신규발행", col="당 기/파생상품부채", tt="(2) 당기와 전기 중 상환전환우선주부채의 변동내역"),  # t090
            _dcell("신주인수권 행사", col="파생상품자산", tt="파생상품자산 및 파생상품부채의 변동내역"),     # 20260323001402 t142
            _dcell("파생상품부채", col="기초자산 가격/1% 감소", tt="민감도 분석"),                       # 20260326001209 t107
            _dcell("파생상품평가손실", tt="파생상품부채의 내역"),
            _dcell("기초", col="파생상품부채", tt="파생상품부채의 변동내역"),
            _dcell("수준간이동", col="전 기/파생상품부채", tt="수준3으로 분류된 금융상품의 변동 내역"),        # 20260316001642 t032
            _dcell("최초인식", col="전 기/파생상품부채", tt="수준3으로 분류된 금융상품의 변동 내역"),
            _dcell("유가증권", col="당기말/파생상품자산(위험회피목적)", tt="금융상품의 범주별 분류"),
            _dcell("파생금융상품부채", col="1년 ~ 5년", tt="구분 3개월 미만 3개월 ~ 1년 1년 ~ 5년", nt="위험관리"),  # 20260318000018 t305
            _dcell("파생상품부채", col="계약상 현금흐름", tt="금융부채의 만기분석", nt="위험관리"),         # 20260313001235 t189
            _dcell("대손상각비", path=["당기손익", "대손상각비"], col="단기매매목적파생상품자산/부채",          # 20260316000969 t233
                   tt="금융자산 및 금융부채의 범주별 손익내역"),
        ]
        for cell in cases:
            self.assertIsNone(_deriv_side(cell), (cell["label_path"], cell["col_header"]))

    def test_non_derivative_boundary(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_title_side
        self.assertIsNone(_deriv_title_side("당기말 현재 비파생금융부채의 계약에 따른 만기분석은 다음과 같습니다."))
        self.assertEqual(_deriv_title_side("비파생금융부채와 파생상품부채의 만기"), "liability")

    def test_option_liability_sentences(self):
        from dart_search_mcp.ledger.instrument_validate import _positive_deriv_liability_sentence as pos
        yes = ["전환권은 파생상품부채로 분류하였습니다.",
               "주계약은 상각후원가로 평가되고, 조기상환권 및 전환권은 파생상품으로 인식하였습니다.",
               "연결회사는 전환사채와 신주인수권부사채에 포함된 내재파생상품(조기상환청구권, 전환권, 신주인수권, 콜옵션 등)을 "
               "주계약인 사채에서 분리하여 파생상품부채로 인식하였습니다."]
        no = [
            # 20260316001629 s341 — 사채의 유동 분류
            "당기말 현재 당사가 발행한 전환사채는 자기지분상품으로 결제할 수 있는 전환권 행사가능 기간이 1년 이내에 도래하므로 유동부채로 분류하였습니다.",
            # 20260323001264 s413
            "(*8) 당사는 보고기간종료일 현재 1년 이내 행사가능한 조기상환청구권(Put option) 및 전환권이 부여되어 있는 사채를 유동부채로 분류하였습니다.",
            # 20260402002743 s507 — 뒤에 붙은 조건(「…분류된 경우로서」)
            "(5) 발행자의 주가변동에 따라 행사가격이 조정되는 조건이 있는 금융상품과 관련된 전환권 또는 신주인수권이 금융부채로 "
            "분류된 경우로서, 해당 전환권 또는 신주인수 등을 포함하는 금융부채의 장부금액 및 관련 손익의 내역은 다음과 같습니다.",
            "상환전환우선주는 상환권이 있으므로 금융부채로 분류하였습니다.",
            "전환사채의 부채요소는 금융부채로 분류하였습니다.",
            "전환상환우선주는 회사의 주가 변동에 따라 행사가격이 조정되는 조건이 있어 전환권을 금융부채로 분류하고 있으며",
            "전환상환우선주의 전환권이 자본요소를 만족하지 못할 경우 전환권과 조기상환권을 파생상품부채로 분류하며",
            "전환권이 파생상품부채로 분류되는 경우에는 공정가치로 측정합니다.",
            "동 복합금융상품에 대하여 회사는 주계약과 내재파생상품을 포함한 전환상환우선주 전체를 당기손익-공정가치 측정 금융부채로 지정하였습니다.",
            "전환권은 파생상품부채로 분류하지 않고 자본으로 분류하였습니다.",
            "내재파생상품은 상환전환우선주에 대한 전환권 및 조기상환권에 대한 공정가치 평가액입니다.",
            "전환권대가는 자본으로 분류하였습니다.",
            "전환권은 비파생금융부채로 분류하였습니다.",
            # 20250728000410 f:…t171:1 앞 문장 — 콜옵션 → 파생상품자산, 뒤 표 머리 「파생상품부채의 변동내역」
            "회사는 전환사채에 발행자에게 부여된 제3자 지정 콜옵션을 별도의 파생상품자산으로 구분하여 회계처리하였으며, "
            "해당 효과를 파생상품평가손익에 반영하였습니다.",
            "보고기간말 현재 파생상품부채의 변동내역은 다음과 같습니다.",
        ]
        for t in yes:
            self.assertTrue(pos(t), t)
        for t in no:
            self.assertFalse(pos(t), t)

    def test_option_class_by_rows_and_sentence(self):
        inp = _inp()
        inp["cells"]["T5:r2:c1"] = _dcell("제7회 상환전환우선주 내재파생상품", tt="파생상품부채의 내역")
        inp["cells"]["T6:r2:c1"] = _dcell("제7회 상환전환우선주 내재파생상품", tt=_EXERCISE_TT)
        inp["sentences"]["s30"] = "조기상환권 및 전환권은 파생상품으로 인식하였습니다."
        inp["sentences"]["s31"] = "상환전환우선주는 상환권이 있으므로 금융부채로 분류하였습니다."
        for ids, want in ((["T5:r2:c1"], "derivative_liability"), (["s30"], "derivative_liability"),
                          (["T6:r2:c1"], "unconfirmed"), (["s31"], "unconfirmed")):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, _ = validate(rec, inp)
            self.assertEqual(out["option_class"], want, ids)
        rec = _record()
        rec["balance"]["current_end"]["derivative_liability"] = 100
        rec["evidence"]["balance.current_end.derivative_liability"] = ["T5:r2:c1"]
        out, _ = validate(rec, inp)
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 100)
        self.assertEqual(out["matched_multiplier"]["balance.current_end.derivative_liability"], 1000)


def _host_inp():
    """20250728000410 t165 「8회차 비유동전환사채의 변동내역」(천원)을 본뜬 칸."""
    tt = "18.4.1 8회차 비유동전환사채의 변동내역은 다음과 같습니다."
    cells = {
        "H:r1:c1": _dcell("액면금액", value=3500000, tt=tt, nt="사채"),
        "H:r2:c1": _dcell("전환권조정", value=-1581021, tt=tt, nt="사채"),
        "H:r3:c1": _dcell("상환할증금", value=0, tt=tt, nt="사채"),
        "H:r1:c2": _dcell("액면금액", value=3000000, tt=tt, nt="사채"),
        "H:r4:c1": _dcell("전환사채의 자본요소", value=1000, tt=tt, nt="사채"),
        "D:r1:c1": _dcell("파생상품부채", value=1000, tt="파생상품부채"),
        "U:r1:c1": _dcell("액면금액", value=3500000, multiplier=1, tt=tt),
    }
    return {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {"s11": "x"}}


def _host_record(value, ids, key="host_carrying"):
    rec = {"instrument": "제8회 전환사채", "series_key": "CB-8", "kind": "CB",
           "balance": {"prior_end": {key: value}}, "option_class": None, "embedded_separation": None,
           "evidence": {f"balance.prior_end.{key}": ids}}
    return rec


class HostSumTest(unittest.TestCase):
    def test_component_sum_accepted(self):
        ids = ["H:r1:c1", "H:r2:c1", "H:r3:c1"]
        out, reasons = validate(_host_record(1918979, ids), _host_inp())
        self.assertEqual(reasons, [])
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], 1918979)
        self.assertEqual(out["matched"]["balance.prior_end.host_carrying"], "H:r1:c1+H:r2:c1+H:r3:c1")
        self.assertEqual(out["matched_multiplier"]["balance.prior_end.host_carrying"], 1000)
        self.assertEqual(out["evidence"]["balance.prior_end.host_carrying"], ids)

    def test_component_sum_rejected(self):
        bad = [(1918979, ["H:r1:c1", "H:r2:c1", "H:r3:c1", "T9:r1:c1"]),   # 없는 칸 섞임
               (1918978 - 5, ["H:r1:c1", "H:r2:c1", "H:r3:c1"]),             # 합이 다름
               (-1581021 + 3000000, ["H:r1:c2", "H:r2:c1"]),                 # 다른 열
               (3500000 - 1581021 + 1000, ["H:r1:c1", "H:r2:c1", "H:r4:c1"]),  # 자본요소 섞임
               (3500000 - 1581021 + 1000, ["H:r1:c1", "H:r2:c1", "D:r1:c1"]),  # 다른 표·파생 칸
               (3500000 - 1581021, ["U:r1:c1", "H:r2:c1"]),                  # 다른 표·배수
               (3500000, ["H:r1:c1"] * 1 + ["H:r3:c1"] * 0)]                  # 칸 하나면 그 칸 값 그대로(합 아님)
        for value, ids in bad[:-1]:
            out, reasons = validate(_host_record(value, ids), _host_inp())
            self.assertIsNone(out["balance"]["prior_end"]["host_carrying"], ids)
            self.assertIn("amount_mismatch:balance.prior_end.host_carrying", reasons)

    def test_component_whitelist_series_and_v_cells(self):
        """R14-6: 구성 목록 밖 행·파생 낱말 행·회차가 둘 섞인 행·글 표 v 칸은 합하지 않는다."""
        inp = _host_inp()
        tt = "재무상태표"
        inp["cells"].update({
            "B:r59:c3": _dcell("유동성전환사채(주석14,19,20,23)", value=4000000000, multiplier=1, tt=tt),
            "B:r61:c3": _dcell("전환권조정", value=-1796762839, multiplier=1, tt=tt),
            "S:r1:c1": _dcell("제1회 전환사채", value=1000, tt="전환사채 내역"),
            "S:r2:c1": _dcell("제2회 전환사채", value=2000, tt="전환사채 내역"),
            "S:r3:c1": _dcell("제1회 전환권조정", value=-100, tt="전환사채 내역"),
            "S:r4:c1": _dcell("제1회 전환사채", value=1000, tt="전환사채 내역"),
            "P:r1:c1": _dcell("액면금액", value=1000, tt="전환사채 내역"),
            "P:r2:c1": _dcell("파생상품부채", value=-100, tt="전환사채 내역"),
            "P:r3:c1": _dcell("전환권가치", value=-100, tt="전환사채 내역"),
            "V:r1:v1": _dcell("액면가액", value=1000, tt="발행조건"),
            "V:r1:v2": _dcell("액면가액", value=2000, tt="발행조건"),
        })
        bad = [(4000000000 - 1796762839, ["B:r59:c3", "B:r61:c3"]),   # 20260402000871 t013 — 재무상태표 줄
               (3000, ["S:r1:c1", "S:r2:c1"]),                         # 회차 둘
               (900, ["P:r1:c1", "P:r2:c1"]), (900, ["P:r1:c1", "P:r3:c1"]),
               (3000, ["V:r1:v1", "V:r1:v2"])]
        for value, ids in bad:
            rec = _host_record(value, ids)
            out, reasons = validate(rec, inp)
            self.assertIsNone(out["balance"]["prior_end"]["host_carrying"], ids)
        out, reasons = validate(_host_record(900, ["S:r4:c1", "S:r3:c1"]), inp)   # 같은 회차 원금+조정은 된다
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], 900)

    def test_sum_only_for_host_carrying(self):
        for key in ("face", "derivative_liability", "equity_component"):
            out, reasons = validate(_host_record(1918979, ["H:r1:c1", "H:r2:c1", "H:r3:c1"], key), _host_inp())
            self.assertIsNone(out["balance"]["prior_end"][key], key)


class SentenceIdFormatTest(unittest.TestCase):
    def test_canonical_sentence_ids(self):
        for given in ("s:11", "R:00761:s11", "20260319001119:00761:s11"):
            rec = _record()
            rec["evidence"]["option_class"] = [given]
            out, reasons = validate(rec, _inp())
            self.assertEqual(out["option_class"], "equity", given)
            self.assertEqual(out["evidence"]["option_class"], ["s11"], given)
        for given in ("s:999", "T1", "T1:r0", "T1:r7"):
            rec = _record()
            rec["evidence"]["option_class"] = [given]
            out, reasons = validate(rec, _inp())
            self.assertEqual(out["option_class"], "unconfirmed", given)


class TextTableCellsTest(unittest.TestCase):
    """20250910000615 t061 「전환사채의 주요 발행조건」 — 값 열 없는 글 표의 금액에 v id 를 준다."""

    def _terms(self):
        t = _table([("x", [1])], note_title="차입금", title="(5) 전환사채의 주요 발행조건은 다음과 같습니다.",
                   tid="R:00760:t061")
        t["columns"] = [{"col": 0, "header_path": ["구분"], "role": "label"},
                        {"col": 1, "header_path": ["구분"], "role": "label"},
                        {"col": 2, "header_path": ["제1회 전환사채"], "role": "label"},
                        {"col": 3, "header_path": ["제2회 전환사채"], "role": "label"}]
        t["unit"] = {"raw": "(단위: 천원)", "kind": "krw", "multiplier": 1000, "currency": "KRW"}
        t["rows"] = [
            {"row": 2, "label": "2027년 2월 26일", "label_path": ["만기일", "2026년 9월 5일", "2027년 2월 26일"], "cells": []},
            {"row": 3, "label": "35,000,000,000원", "label_path": ["액면가액", "20,000,000,000원", "35,000,000,000원"],
             "cells": []},
            {"row": 12, "label": "9,269,068주", "label_path": ["주식수", "5,997,001주", "9,269,068주"], "cells": []},
            {"row": 13, "label": "x", "label_path": ["발행가액", "(1,000)천원"], "cells": []},
        ]
        return t

    def test_v_cells(self):
        inp = build_input("R", [self._terms()], [])
        c = inp["cells"]["R:00760:t061:r3:v2"]
        self.assertEqual((c["value"], c["unit_kind"], c["multiplier"], c["label"]), (35000000000, "krw", 1, "액면가액"))
        self.assertEqual(inp["cells"]["R:00760:t061:r3:v1"]["value"], 20000000000)
        self.assertEqual(inp["cells"]["R:00760:t061:r13:v1"]["value"], -1000)
        self.assertEqual(inp["cells"]["R:00760:t061:r13:v1"]["multiplier"], 1000)
        self.assertFalse([k for k in inp["cells"] if ":r12:" in k or ":r2:" in k])
        self.assertIn("r3 액면가액 | v1=20,000,000,000원 | v2=35,000,000,000원", inp["tables_text"])
        self.assertIn("r2 만기일 | 2026년 9월 5일 | 2027년 2월 26일", inp["tables_text"])
        self.assertIn("열 | 구분 | 구분 | 제1회 전환사채 | 제2회 전환사채", inp["tables_text"])
        rec = _record()
        rec["balance"] = {"current_end": {"face": 35000000000}}
        rec["evidence"] = {"balance.current_end.face": ["R:00760:t061:r3:v2"]}
        out, reasons = validate(rec, inp)
        self.assertEqual(out["balance"]["current_end"]["face"], 35000000000)
        self.assertEqual(out["matched_multiplier"]["balance.current_end.face"], 1)

    def test_text_table_counts_as_having_cells(self):
        from dart_search_mcp.ledger.instrument_input import _has_cells
        self.assertTrue(_has_cells(self._terms()))

    def test_period_tag_in_header(self):
        t = _table([("전환사채", [1, 2])], note_title="전환사채", tid="R:00760:t001")
        inp = build_input("R", [t], [])
        self.assertIn("머리글 | c1=current_end [current_end] | c2=prior_end [prior_end]", inp["tables_text"])


class PromptI2Test(unittest.TestCase):
    def test_i2_rules(self):
        self.assertEqual(PROMPT_VERSION, "i2")
        user = build_messages(_inp())[1]["content"]
        for phrase in ("1499943000 이 아니다", '"s:494"', "<표 id>:r<행>:v<번호>", "[current_end]",
                       "null 이 아닌 필드는 모두 evidence", "같은 표·같은 열에 구성 칸", "「전환권가치」",
                       "조건부", "전체 장부금액도 여기에", "「전환사채 및 조기상환권」"):
            self.assertIn(phrase, user)

if __name__ == "__main__":
    unittest.main()
