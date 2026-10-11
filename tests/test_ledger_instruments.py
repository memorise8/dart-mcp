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
from dart_search_mcp.ledger.instruments import (
    OUTPUT_SCHEMA,
    PROMPT_I1,
    Cache,
    build_messages,
    cache_key,
    extract_instruments,
    run_filings,
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
        self.assertEqual(out["prompt_version"], "i1")
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
        self.assertEqual(msgs[0]["content"], PROMPT_I1["system"])
        for phrase in ("전환사채·신주인수권부사채·교환사채·상환전환우선주", "표 칸 id와 문장 id로만",
                       "null 또는 unconfirmed", "부채요소(주계약)와 전환권 파생상품부채는 다른 칸"):
            self.assertIn(phrase, msgs[0]["content"])
        user = msgs[1]["content"]
        self.assertIn(inp["tables_text"], user)
        self.assertIn("s11: 전환권은 자본으로 분류하였습니다.", user)
        self.assertIn("f:T2:1: ", user)
        for phrase in ("천원 숫자를 그대로", "「전환권조정」·「사채할인발행차금」", "만들어 내지 않는다",
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
            self.assertEqual((il["prompt_version"], il["model"], il["records"], il["dropped"]), ("i1", "dry", [], []))
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

if __name__ == "__main__":
    unittest.main()
