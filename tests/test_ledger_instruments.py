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
    PROMPT_I6,
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
            # T9b: not_separated 는 분리하지 않았다는 문장이 근거여야 한다
            "s13": "전환사채에 내재된 조기상환청구권은 주계약과 밀접하게 관련되어 있어 분리하지 않았습니다.",
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
            "embedded_separation": ["s13"],
            "balance.current_end.face": ["T1:r3:c1"],
            "balance.current_end.host_carrying": ["T1:r6:c1"],
            "balance.current_end.equity_component": ["T1:r7:c1"],
            "balance.prior_end.equity_component": ["T1:r7:c2"],
        },
    }
    rec.update(over)
    return rec


def _no_equity(inp):
    """자본요소 칸을 뺀 입력 — derivative_liability 를 확인하는 테스트용(자본요소가 있으면 equity_conflict)."""
    inp["cells"] = {k: v for k, v in inp["cells"].items() if "자본요소" not in (v.get("label") or "")
                    and "대가" not in (v.get("label") or "")}
    return inp


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
            "embedded_separation": ["s13"],
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
        # T9b: 전환사채 표(T1) 금액은 빼 둔다 — RCPS 기록이 「전환사채」 칸을 대면 종류가 달라 series_mismatch
        rec = _record(kind="RCPS", series_key="RCPS-1", option_class="derivative_liability",
                      embedded_separation="separated", separated_targets=["conversion", "redemption"],
                      balance={}, separated_amount={"current_end": 2500419206, "prior_end": 12213899634})
        rec["evidence"] = {"option_class": ["T2:r3:c4", "f:T2:1"], "embedded_separation": ["f:T2:1"],
                           "separated_amount.current_end": ["T2:r3:c4"],
                           "separated_amount.prior_end": ["T2:r3:c4"]}
        out, reasons = validate(rec, _inp())
        self.assertEqual(out["separated_amount"]["current_end"], 2500419206)
        self.assertIsNone(out["separated_amount"]["prior_end"])
        # R15: 「파생상품부채(*)」 행·「내재파생상품 분리금액」 각주는 전환권을 말하지 않는다 → option_class 근거 아님.
        # T9b: 「분리금액」은 분리했다는 진술이 아니고, 「파생상품부채(*)」 행은 상품을 말하지 않는다 → no_separation_ref
        self.assertEqual(reasons, ["amount_mismatch:separated_amount.prior_end", "no_derivative_ref", "no_separation_ref"])
        self.assertEqual(out["option_class"], "unconfirmed")

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
        inp = _no_equity(_inp())
        inp["sentences"]["s12"] = "전환권은 파생상품부채로 분류하였습니다."
        rec = _record(option_class="derivative_liability")
        for period in ("current_end", "prior_end"):
            rec["balance"][period]["equity_component"] = None   # 자본요소가 있으면 equity_conflict
            rec["evidence"].pop(f"balance.{period}.equity_component")
        rec["evidence"]["option_class"] = ["T1:r3:c1", "s12"]
        # T9b: 전환권이 파생상품부채면 분리다 — not_separated 와 함께 확인되지 않는다
        rec["embedded_separation"] = "separated"
        rec["evidence"]["embedded_separation"] = ["s12"]
        out, reasons = validate(rec, inp)
        self.assertEqual(out["option_class"], "derivative_liability")
        self.assertEqual(out["embedded_separation"], "separated")
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
        inp = _no_equity(_inp())
        inp["sentences"]["s20"] = "전환권은 파생상품부채로 분류하지 않고 자본으로 분류하였습니다."
        inp["sentences"]["s21"] = "매도청구권은 파생상품자산으로 인식하였습니다."
        for ids in (["T4:r2:c1"], ["s20"], ["s21"], ["T4:r2:c1", "s20"]):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], "unconfirmed", ids)
            self.assertIn("no_derivative_ref", reasons)
        # R15: 전환권을 말하지 않는 일반 「파생상품부채」 행은 option_class 근거가 아니다(금액 근거로는 된다)
        for ids in (["T4:r3:c1"], ["T4:r4:c1"]):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], "unconfirmed", ids)
        inp["cells"]["T4:r5:c1"] = dict(inp["cells"]["T4:r3:c1"], label="전환권", label_path=["파생상품부채", "전환권"])
        rec = _record(option_class="derivative_liability")
        rec["evidence"]["option_class"] = ["T4:r5:c1"]
        out, reasons = validate(rec, inp)
        self.assertEqual(out["option_class"], "derivative_liability")


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
        self.assertEqual(msgs[0]["content"], PROMPT_I6["system"])
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

    def _amount(self, inp, key, value, cid, period="current_end", kind="CB"):
        rec = _record(kind=kind, series_key=f"{kind}-3")  # T9b: 칸이 말하는 상품 종류와 같은 종류의 기록
        rec["balance"][period][key] = value
        rec["evidence"][f"balance.{period}.{key}"] = [cid]
        return validate(rec, inp)

    def test_cells_carry_titles(self):
        inp = _option_table_input()
        c = inp["cells"]["R:00760:t108:r2:c2"]
        self.assertEqual(c["table_title"], "20.2 보고기간종료일 현재 파생상품부채의 내역은 다음과 같습니다.")
        self.assertEqual(c["note_title"], "파생상품")

    def test_option_rows_in_liability_table(self):
        inp = _option_table_input()
        # c2 = 전기말 열 → prior_end 금액(fix round 4 기간 검사). option_class 는 R15: 전환권 행만.
        for value, cid, oc, kind in ((1042788774, "R:00760:t108:r2:c2", "derivative_liability", "RCPS"),
                                     (2112984200, "R:00760:t108:r3:c2", "unconfirmed", "RCPS"),
                                     (1551072, "R:00760:t178:r1:c2", "unconfirmed", "EB")):
            out, reasons = self._amount(inp, "derivative_liability", value, cid, "prior_end", kind)
            self.assertEqual(out["balance"]["prior_end"]["derivative_liability"], value, cid)
            self.assertEqual([r for r in reasons if "derivative" in r], [], cid)
            self.assertEqual(out["matched"]["balance.prior_end.derivative_liability"], cid)
            out, reasons = self._amount(inp, "derivative_liability", value, cid)   # 당기말 금액으로 대면 기간이 틀림
            self.assertIn("period_mismatch:balance.current_end.derivative_liability", reasons, cid)
            rec = _record(option_class="derivative_liability", kind=kind, series_key=f"{kind}-3")
            rec["evidence"]["option_class"] = [cid]
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], oc, cid)

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
        self.assertNotIn("CB-?", PROMPT_I6["example"])
        self.assertIn('"series_key": ""', PROMPT_I6["example"])
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


def _dcell(label, *, path=None, col="당기말", tt="", nt="", value=100, multiplier=1000, unit_kind="krw",
           period="current_end"):
    return {"raw": f"{value:,}", "value": value, "unit_kind": unit_kind, "multiplier": multiplier,
            "period": period, "label": label, "label_path": path or [label], "col_header": col,
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
        inp = _no_equity(_inp())
        inp["cells"]["T5:r2:c1"] = _dcell("제7회 상환전환우선주 내재파생상품", tt="파생상품부채의 내역")
        inp["cells"]["T6:r2:c1"] = _dcell("제7회 상환전환우선주 내재파생상품", tt=_EXERCISE_TT)
        inp["sentences"]["s30"] = "조기상환권 및 전환권은 파생상품으로 인식하였습니다."
        inp["sentences"]["s31"] = "상환전환우선주는 상환권이 있으므로 금융부채로 분류하였습니다."
        for ids, want in ((["T5:r2:c1"], "unconfirmed"), (["s30"], "derivative_liability"),  # T5: R15 — 전환권을 말하지 않음
                          (["T6:r2:c1"], "unconfirmed"), (["s31"], "unconfirmed")):
            rec = _record(option_class="derivative_liability")
            rec["evidence"]["option_class"] = ids
            out, _ = validate(rec, inp)
            self.assertEqual(out["option_class"], want, ids)
        # 칸이 제7회 상환전환우선주 — 기록 회차·종류가 같아야 한다(fix round 5 series_mismatch, T9b 종류)
        rec = _record(series_key="RCPS-7", kind="RCPS")
        rec["balance"]["current_end"]["derivative_liability"] = 100
        rec["evidence"]["balance.current_end.derivative_liability"] = ["T5:r2:c1"]
        out, _ = validate(rec, inp)
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 100)
        self.assertEqual(out["matched_multiplier"]["balance.current_end.derivative_liability"], 1000)


def _host_inp():
    """20250728000410 t165 「8회차 비유동전환사채의 변동내역」(천원)을 본뜬 칸."""
    tt = "18.4.1 8회차 비유동전환사채의 변동내역은 다음과 같습니다."
    pe = "prior_end"  # 기록은 balance.prior_end — 칸도 전기말 열(fix round 4 기간 검사)
    cells = {
        "H:r1:c1": _dcell("액면금액", value=3500000, tt=tt, nt="사채", period=pe),
        "H:r2:c1": _dcell("전환권조정", value=-1581021, tt=tt, nt="사채", period=pe),
        "H:r3:c1": _dcell("상환할증금", value=0, tt=tt, nt="사채", period=pe),
        "H:r1:c2": _dcell("액면금액", value=3000000, tt=tt, nt="사채", period=pe),
        "H:r4:c1": _dcell("전환사채의 자본요소", value=1000, tt=tt, nt="사채", period=pe),
        "D:r1:c1": _dcell("파생상품부채", value=1000, tt="파생상품부채", period=pe),
        "U:r1:c1": _dcell("액면금액", value=3500000, multiplier=1, tt=tt, period=pe),
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
            "S:r1:c1": _dcell("제1회 전환사채", value=1000, tt="전환사채 내역", period="prior_end"),
            "S:r2:c1": _dcell("제2회 전환사채", value=2000, tt="전환사채 내역", period="prior_end"),
            "S:r3:c1": _dcell("제1회 전환권조정", value=-100, tt="전환사채 내역", period="prior_end"),
            "S:r4:c1": _dcell("제1회 전환사채", value=1000, tt="전환사채 내역", period="prior_end"),
            "P:r1:c1": _dcell("액면금액", value=1000, tt="전환사채 내역", period="prior_end"),
            "P:r2:c1": _dcell("파생상품부채", value=-100, tt="전환사채 내역", period="prior_end"),
            "P:r3:c1": _dcell("전환권가치", value=-100, tt="전환사채 내역", period="prior_end"),
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
        rec = _host_record(900, ["S:r4:c1", "S:r3:c1"])   # 같은 회차 원금+조정은 된다(기록 회차도 같아야 — fix round 5)
        rec["series_key"] = "CB-1"
        out, reasons = validate(rec, inp)
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
        self.assertEqual(c["n_value_cols"], 2)
        rec = _record()
        rec["balance"] = {"current_end": {"face": 35000000000}}
        rec["evidence"] = {"balance.current_end.face": ["R:00760:t061:r3:v2"]}
        out, reasons = validate(rec, inp)
        # fix round 4: 기간 없는 v 칸 + 회차 열 둘 → 어느 기간 잔액인지 모른다
        self.assertIsNone(out["balance"]["current_end"]["face"])
        self.assertIn("period_mismatch:balance.current_end.face", reasons)
        one = self._terms()
        one["columns"] = one["columns"][:3]
        one["rows"] = [{"row": 3, "label": "35,000,000,000원", "label_path": ["액면가액", "35,000,000,000원"], "cells": []}]
        inp = build_input("R", [one], [])
        self.assertEqual(inp["cells"]["R:00760:t061:r3:v1"]["n_value_cols"], 1)
        rec["evidence"] = {"balance.current_end.face": ["R:00760:t061:r3:v1"]}
        out, reasons = validate(rec, inp)
        # fix round 5: 발행조건 표(제목이 당기말·현재를 말하지 않음)의 기간 없는 칸은 잔액이 아니다
        self.assertIsNone(out["balance"]["current_end"]["face"])
        self.assertIn("period_mismatch:balance.current_end.face", reasons)
        one["title"] = "당기말 현재 전환사채의 내역은 다음과 같습니다."
        inp = build_input("R", [one], [])
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


# ---- fix round 4: R15(option_class 는 전환권·신주인수권·교환권만)·회차 경로·양쪽 제목·기간·비잔액 칸 ----

def _pcell(label, *, path=None, col="당기말", period="current_end", value=100, multiplier=1000, tt="", nt="",
           n_value_cols=2, region_kind="note"):
    return {"raw": f"{value:,}", "value": value, "unit_kind": "krw", "multiplier": multiplier, "period": period,
            "label": label, "label_path": path or [label], "col_header": col, "table_title": tt, "note_title": nt,
            "n_value_cols": n_value_cols, "region_kind": region_kind}


# 20260318001087 s391·s390, 20260323001574 s489·s1179, 20260319001143 s1131 원문 그대로
S391 = ("전환사채에 내재된 조기상환청구권 및 매도청구권은 내재파생상품의 분리조건을 모두 충족하므로 주계약에서 분리하여 "
        "별도의 파생상품으로 회계처리하고 있습니다.")
S390 = ("발행가액에서 부채요소를 차감하고 매도청구권과 조기상환청구권을 가감한 잔여가치는 전환권의 가치에 해당하며, "
        "세효과 차감후 금액을 자본으로 계상하였습니다.")
S1179 = ("전환사채에 내재된 전환권, 조기상환청구권 및 매도청구권이 포함된 내재파생상품은주계약과의 분리조건을 충족하므로 "
         "주계약에서 분리되는 내재파생상품으로 회계처리하였으며, 내재파생상품의 공정가치는 지배기업의 주가변동에 따라 변동합니다.")
S1131 = "(*2) 제 3자에게 매각할수 있는 발행자의 콜옵션이 파생상품자산으로 계상되었으며, 투자자의 조기상환권이 파생상품부채로 분류되었습니다."


class R15OptionClassTest(unittest.TestCase):
    """R15: option_class 근거는 전환권·신주인수권·교환권을 일치 범위 안에서 말해야 한다. 풋·콜 근거는 아니다."""

    def test_sentences(self):
        from dart_search_mcp.ledger.instrument_validate import _positive_deriv_liability_sentence as pos
        yes = [S1179,
               "상환전환우선주의 전환권 및 조기상환권은 자본요소에 해당하지 않으므로 주계약에서 분리하여 파생상품부채로 인식하였습니다.",
               "전환권과 조기상환권은 파생상품부채로 분류하였습니다.",
               "신주인수권부사채의 신주인수권은 파생상품부채로 인식하였습니다.",
               "교환권은 파생상품부채로 계상하였습니다."]
        no = [S391, S390, S1131,
              "조기상환청구권은 파생상품부채로 분류하였습니다.",
              "회사는 내재파생상품을 주계약에서 분리하여 파생상품부채로 인식하였습니다.",
              "내재파생상품은 파생상품으로 회계처리하였습니다.",
              "전환권은 자본으로 분류하고, 조기상환청구권은 파생상품으로 인식하였습니다.",
              "전환권 행사가격 조정 조건으로 인해 상환전환우선주는 파생상품부채로 분류하였습니다.",
              "전환권이 포함된 사채의 조기상환청구권은 파생상품부채로 분류하였습니다.",
              "신주인수권부사채는 파생상품부채로 분류하였습니다.",
              # 20260319000686 s710 — 당기 중 전환권을 자본(기타자본잉여금)으로 옮김
              "(주2) 전환사채에 내재된 전환권 및 상환권이 내재파생상품의 분리조건을 모두 충족하므로 주계약에서 분리하여 별도의 "
              "파생상품으로 분류하고 공정가치로 평가하였으며, 당기 중 계약변경으로 전환권을 파생상품에서 분리하여 기타자본잉여금으로 "
              "분류함으로써 당기말 현재 상환권만 계상되어 있습니다.",
              # 20260403001826 s173 — 「전환권 및 상환권이 없는 우선주」: 전환권은 주어가 아니다
              "동 복합금융상품은 전부 부채요소로 구분되며, 복합금융상품 전체의 공정가치와 전환권 및 상환권이 없는 우선주의 "
              "공정가치 차이를 파생상품부채로 인식하고 있습니다."]
        for t in yes:
            self.assertTrue(pos(t), t)
        for t in no:
            self.assertFalse(pos(t), t)

    def _inp(self):
        tt = "(2) 당기말과 전기말 현재 파생상품부채의 내역은 다음과 같습니다."
        return {"rcept_no": "R", "tables_text": "", "cells": {
            "D:r1:c1": _pcell("조기상환청구권", tt=tt, nt="파생상품"),
            "D:r2:c1": _pcell("제7회 상환전환우선주 내재파생상품", tt=tt),
            "D:r3:c1": _pcell("제3회 전환사채", path=["제3회 전환사채", "전환권/조기상환권"], tt=tt),
            "D:r4:c1": _pcell("파생상품부채", tt="파생상품"),
            "D:r5:c1": _pcell("제3회 전환사채 전환권 등 (*2)", tt=tt),
            "E:r4:c1": _pcell("전환권대가", tt="23. 기타자본구성요소 당기말과 전기말 현재 기타자본구성요소의 내역", nt="기타자본구성요소"),
        }, "sentences": {"s391": S391, "s390": S390, "s1179": S1179, "s1131": S1131}}

    def test_validate_option_class(self):
        inp = _no_equity(self._inp())
        for ids, want in ((["s391"], "unconfirmed"), (["s1131"], "unconfirmed"), (["D:r1:c1"], "unconfirmed"),
                          (["D:r2:c1"], "unconfirmed"), (["D:r4:c1"], "unconfirmed"), (["D:r1:c1", "s391"], "unconfirmed"),
                          (["s1179"], "derivative_liability"), (["D:r3:c1"], "derivative_liability"),
                          (["D:r5:c1"], "derivative_liability")):
            rec = {"instrument": "제3회 전환사채", "series_key": "CB-3", "kind": "CB", "balance": {},
                   "option_class": "derivative_liability", "embedded_separation": None, "evidence": {"option_class": ids}}
            out, reasons = validate(rec, inp)
            self.assertEqual(out["option_class"], want, ids)
            if want == "unconfirmed":
                self.assertIn("no_derivative_ref", reasons, ids)

    def test_put_row_still_counts_for_amount(self):
        """풋·콜 행은 option_class 근거는 아니지만 파생상품 금액 근거는 된다."""
        rec = {"instrument": "제3회 전환사채", "series_key": "CB-3", "kind": "CB",
               "balance": {"current_end": {"derivative_liability": 100}}, "option_class": None,
               "embedded_separation": None, "evidence": {"balance.current_end.derivative_liability": ["D:r1:c1"]}}
        out, reasons = validate(rec, self._inp())
        self.assertEqual(reasons, [])
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 100)

    def test_equity_by_conversion_option_value(self):
        """20260318001087: 전환권은 자본(s390·t176 전환권대가) — 같은 공시의 s391 은 풋·콜."""
        rec = {"instrument": "전환사채", "series_key": "", "kind": "CB", "balance": {}, "option_class": "equity",
               "embedded_separation": None, "evidence": {"option_class": ["E:r4:c1", "s390"]}}
        out, reasons = validate(rec, self._inp())
        self.assertEqual(out["option_class"], "equity")


class EquityConflictTest(unittest.TestCase):
    """같은 입력에 그 종류의 자본요소(전환권대가 등, 0 아님)가 있고 회차가 같거나 모르면 derivative_liability 는 확인하지
    않는다(20250728000410: 5~9회차 전환권대가 + 일반 문장 「내재파생상품(조기상환청구권, 전환권, …)을 … 파생상품부채로 인식」,
    20260403002552: 기타자본 전환권대가 + 회차 없는 「전환사채 전환권」 파생상품부채 행). 근거 칸의 회차가 기록 회차와 다르면 그
    칸도 근거가 아니다."""

    S448 = ("18.5.2 파생상품부채회사는 전환사채와 신주인수권부사채에 포함된 내재파생상품(조기상환청구권, 전환권, 신주인수권, "
            "콜옵션 등)을 주계약인 사채에서 분리하여 파생상품부채로 인식하였습니다.")

    def _rec(self, key, ids, kind="CB"):
        return {"instrument": f"제{key}회", "series_key": f"{kind}-{key}", "kind": kind, "balance": {},
                "option_class": "derivative_liability", "embedded_separation": None, "evidence": {"option_class": ids}}

    def _inp(self, eq_cells):
        cells = {"D:r4:c1": _pcell("전환사채 전환권", col="당기말/파생상품부채", tt="파생상품부채 내역"),
                 "D:r5:c1": _pcell("제3회 전환사채 전환권 등", tt="파생상품부채의 내역")}
        cells.update(eq_cells)
        return {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {"s448": self.S448}}

    def test_conflict(self):
        eq_any = {"E:r1:c1": _pcell("전환권대가", value=857132, tt="36.4.1 5회차 유동전환사채")}
        eq_9 = {"E:r1:c1": _pcell("제9회 전환사채 전환권대가", value=7813440, tt="관련 금융부채 장부금액")}
        eq_zero = {"E:r1:c1": _pcell("전환권대가", value=0, tt="기타자본")}
        eq_liab = {"E:r1:c1": _pcell("전환권대가(파생상품금융부채)", value=-3031345449, tt="수준 3")}
        eq_bw = {"E:r1:c1": _pcell("신주인수권대가", value=252013, tt="기타자본구성요소")}
        cases = [(eq_any, "5", ["s448"], "unconfirmed"), (eq_any, "5", ["D:r4:c1"], "unconfirmed"),
                 (eq_9, "9", ["s448"], "unconfirmed"), (eq_9, "10", ["s448"], "derivative_liability"),
                 (eq_zero, "5", ["s448"], "derivative_liability"), (eq_liab, "5", ["D:r4:c1"], "derivative_liability"),
                 (eq_bw, "5", ["s448"], "derivative_liability"), ({}, "5", ["D:r4:c1"], "derivative_liability"),
                 ({}, "5", ["D:r5:c1"], "unconfirmed"), ({}, "3", ["D:r5:c1"], "derivative_liability")]
        for eq, key, ids, want in cases:
            out, reasons = validate(self._rec(key, ids), self._inp(eq))
            self.assertEqual(out["option_class"], want, (eq, key, ids))
        out, reasons = validate(self._rec("5", ["s448"]), self._inp(eq_any))
        self.assertIn("equity_conflict", reasons)
        out, _ = validate(self._rec("5", ["s448"], kind="BW"), self._inp(eq_bw))
        self.assertEqual(out["option_class"], "unconfirmed")


class HostSumSeriesPathTest(unittest.TestCase):
    """20260407003648:00761:t166 — 회차가 label_path 부모에 있다. 7회+8회 합은 거부, 7회만의 합은 통과."""

    def _inp(self):
        tt = "(3) 보고기간 종료일 현재 사채의 내역은 다음과 같습니다."
        cells = {}
        for r, (series, item, v) in enumerate([("제7회 무기명 사모전환사채", "발행가액", 15912200),
                                                ("제7회 무기명 사모전환사채", "전환권조정", -5951333),
                                                ("제8회 무기명 사모전환사채", "발행가액", 9500000),
                                                ("제8회 무기명 사모전환사채", "전환권조정", -3510420)], start=1):
            cells[f"S:r{r}:c3"] = _pcell(item, path=[series, item], col="전기말", period="prior_end", value=v,
                                         tt=tt, nt="차입금 및 사채")
        cells["S:r9:c3"] = _pcell("전환권조정", col="전기말", period="prior_end", value=-100, tt=tt)
        return {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}

    def _rec(self, value, ids):
        return {"instrument": "제7회 전환사채", "series_key": "CB-7", "kind": "CB",
                "balance": {"prior_end": {"host_carrying": value}}, "option_class": None,
                "embedded_separation": None, "evidence": {"balance.prior_end.host_carrying": ids}}

    def test_mixed_series_rejected(self):
        ids = ["S:r1:c3", "S:r2:c3", "S:r3:c3", "S:r4:c3"]
        out, reasons = validate(self._rec(15950447, ids), self._inp())
        self.assertIsNone(out["balance"]["prior_end"]["host_carrying"])
        # 회차 있는 칸 + 회차 없는 칸도 한 회차가 아니다
        out, _ = validate(self._rec(15912200 - 100, ["S:r1:c3", "S:r9:c3"]), self._inp())
        self.assertIsNone(out["balance"]["prior_end"]["host_carrying"])

    def test_one_series_accepted(self):
        out, reasons = validate(self._rec(15912200 - 5951333, ["S:r1:c3", "S:r2:c3"]), self._inp())
        self.assertEqual(reasons, [])
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], 9960867)


class BothSidesTitleTest(unittest.TestCase):
    def test_title_naming_both_sides_is_unknown(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_title_side
        for t in ("(1) 당기말과 전기말 현재 파생상품자산 및 부채의 구성내역은 다음과 같습니다.",
                  "20. 파생상품(1) 당기말과 전기말 현재 파생상품 자산 및 부채의 구성내역은 다음과 같습니다.",
                  "(3) 당기와 전기 중 파생상품자산 및 부채의 관련 손익은 다음과 같습니다.① 당기",
                  "파생상품부채 및 자산의 내역", "파생상품자산과 파생상품부채의 내역", "파생상품(자산/부채)",
                  "파생금융상품 자산·부채", "콜옵션은 파생상품자산으로 인식하였습니다. (2) 파생상품부채의 내역"):
            self.assertIsNone(_deriv_title_side(t), t)
        self.assertEqual(_deriv_title_side("(2) 당기말과 전기말 현재 파생상품부채의 내역은 다음과 같습니다."), "liability")
        self.assertEqual(_deriv_title_side("파생상품자산의 내역"), "asset")

    def test_real_wrong_sides(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cases = [  # 20260312001406 t150·t153 r7, 20260318000805:00761:t100:r3:c1
            _pcell("SCL에너지 전환사채 콜옵션", tt="(1) 당기말과 전기말 현재 파생상품자산 및 부채의 구성내역은 다음과 같습니다.",
                   nt="파생상품"),
            _pcell("SCL에너지 전환사채 콜옵션", nt="파생상품",
                   tt="20. 파생상품(1) 당기말과 전기말 현재 파생상품자산 및 부채의 구성내역은 다음과 같습니다."),
            _pcell("전환사채 풋옵션", col="장부금액", period="current_period", value=-4773200000, multiplier=1,
                   tt="(3) 당기와 전기 중 파생상품자산 및 부채의 관련 손익은 다음과 같습니다.① 당기", nt="파생상품"),
        ]
        for c in cases:
            self.assertIsNone(_deriv_side(c), c["label"])


class PeriodCheckTest(unittest.TestCase):
    """balance.current_end.* 는 current_end 칸(또는 잔액을 명시한 current_period 칸), prior_end 는 prior_end 칸.
    기간을 모르는 칸은 값 열이 하나인 표에서만."""

    def _rec(self, period, ids, key="face", value=100):
        return {"instrument": "제3회 전환사채", "series_key": "CB-3", "kind": "CB",
                "balance": {period: {key: value}}, "option_class": None, "embedded_separation": None,
                "evidence": {f"balance.{period}.{key}": ids}}

    def test_period_match(self):
        cells = {
            "A:r1:c1": _pcell("전환사채", period="current_end"),
            "A:r1:c2": _pcell("전환사채", col="전기말", period="prior_end"),
            "B:r1:c1": _pcell("전환사채", col="당기", period="current_period"),
            "B:r2:c1": _pcell("전환사채", col="장부금액", period="current_period"),
            "B:r3:c1": _pcell("기말", path=["기말"], col="당기", period="current_period"),
            "B:r3:c2": _pcell("기말", path=["기말"], col="전기", period="prior_period"),
            "U:r1:c1": _pcell("전환사채", col="금액", period="unknown", n_value_cols=1, tt="당기말 현재 전환사채"),
            "U2:r1:c1": _pcell("전환사채", col="금액", period="unknown", n_value_cols=2),
            "N:r1:c1": _pcell("전환사채", col="금액", period=None, n_value_cols=2),
            "O:r1:c1": _pcell("전환사채", col="기초", period="opening"),
        }
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        ok = [("current_end", "A:r1:c1"), ("prior_end", "A:r1:c2"), ("current_end", "B:r2:c1"),
              ("current_end", "B:r3:c1"), ("prior_end", "B:r3:c2"), ("current_end", "U:r1:c1")]
        bad = [("current_end", "A:r1:c2"), ("prior_end", "A:r1:c1"), ("current_end", "B:r1:c1"),
               ("prior_end", "B:r2:c1"), ("current_end", "U2:r1:c1"), ("current_end", "N:r1:c1"),
               ("current_end", "O:r1:c1"), ("prior_end", "B:r3:c1"),
               ("prior_end", "U:r1:c1")]  # fix round 5: 기간 모르는 칸은 current_end 에만
        for period, cid in ok:
            out, reasons = validate(self._rec(period, [cid]), inp)
            self.assertEqual(out["balance"][period]["face"], 100, (period, cid))
        for period, cid in bad:
            out, reasons = validate(self._rec(period, [cid]), inp)
            self.assertIsNone(out["balance"][period]["face"], (period, cid))
            self.assertIn(f"period_mismatch:balance.{period}.face", reasons, (period, cid))

    def test_status_table_period_columns(self):
        """「보고기간종료일 현재 …의 내역」 표의 당기·전기 열(1층 current_period·prior_period)은 당기말·전기말 잔액이다.
        변동표·「당기 중」 표·제목에 「현재」가 없는 표는 아니다(20260403002050 t060, 20260319001100 t140, 20260319000958 t104)."""
        status = "10. 전환사채(1) 보고기간종료일 현재 전환사채의 내역은 다음과 같습니다(단위:원)."
        cells = {
            "S:r1:c3": _pcell("권면금액", path=["제3회 무보증 사모 전환사채", "권면금액"], col="당 기",
                              period="current_period", tt=status),
            "S:r1:c4": _pcell("권면금액", path=["제3회 무보증 사모 전환사채", "권면금액"], col="전 기",
                              period="prior_period", tt=status),
            "F:r1:c3": _pcell("발행", col="당기", period="current_period",
                              tt="(4) 당기 중 전환사채 장부가액의 변동내역은 다음과 같습니다.(당기)"),
            "Q:r4:c1": _pcell("전환권대가(주1)", col="당기", period="current_period",
                              tt="(2) 자본잉여금의 내용은 다음과 같습니다."),
            "G:r1:c1": _pcell("전환사채", col="당기", period="current_period",
                              tt="보고기간종료일 현재 전환사채를 발행하였습니다. (3) 당기 중 전환사채의 변동내역"),
            "P:r7:c2": _pcell("기말장부가액", col="제19회무기명식무보증사모전환사채", period="prior_period", tt="(전기)"),
        }
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        for period, cid in (("current_end", "S:r1:c3"), ("prior_end", "S:r1:c4"), ("prior_end", "P:r7:c2")):
            out, reasons = validate(self._rec(period, [cid]), inp)
            self.assertEqual(out["balance"][period]["face"], 100, cid)
        for period, cid in (("current_end", "F:r1:c3"), ("current_end", "Q:r4:c1"), ("current_end", "G:r1:c1"),
                            ("prior_end", "S:r1:c3")):
            out, reasons = validate(self._rec(period, [cid]), inp)
            self.assertIsNone(out["balance"][period]["face"], cid)

    def test_period_on_host_sum_and_derivative(self):
        tt = "전환사채 내역"
        cells = {"H:r1:c2": _pcell("액면금액", col="전기말", period="prior_end", value=1000, tt=tt),
                 "H:r2:c2": _pcell("전환권조정", col="전기말", period="prior_end", value=-100, tt=tt),
                 "D:r1:c2": _pcell("파생상품부채", col="전기말", period="prior_end", tt="파생상품부채의 내역")}
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        out, _ = validate(self._rec("current_end", ["H:r1:c2", "H:r2:c2"], "host_carrying", 900), inp)
        self.assertIsNone(out["balance"]["current_end"]["host_carrying"])
        out, _ = validate(self._rec("prior_end", ["H:r1:c2", "H:r2:c2"], "host_carrying", 900), inp)
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], 900)
        out, reasons = validate(self._rec("current_end", ["D:r1:c2"], "derivative_liability"), inp)
        self.assertIsNone(out["balance"]["current_end"]["derivative_liability"])

    def test_input_cells_carry_table_shape(self):
        t = _table([("전환사채", [1, 2])], note_title="전환사채", tid="R:00760:t001")
        inp = build_input("R", [t], [])
        c = inp["cells"]["R:00760:t001:r1:c1"] if "R:00760:t001:r1:c1" in inp["cells"] else next(iter(inp["cells"].values()))
        self.assertEqual(c["n_value_cols"], 2)
        self.assertEqual(c["region_kind"], "note")


class NonBalanceCellsTest(unittest.TestCase):
    """현금흐름표 줄, 공정가치 수준 열, 회계오류 수정·재작성 열은 파생상품 잔액이 아니다."""

    def test_rejected(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cases = [
            _pcell("파생상품자산의 정산", col="제 47(당) 기", period="current_period", region_kind="statement_cf"),  # 20260312001406 t026 r26
            _pcell("파생상품자산", col="", region_kind="statement_cf"),                                           # 20260430001002 t022 r42
            _pcell("파생상품부채", col="조정금액", period="unknown", tt="회계오류 수정의 영향"),                  # 20250728000408 t296
            _pcell("파생상품부채", col="재작성금액", period="unknown"),
            _pcell("파생상품부채", col="회계오류 수정의 영향/조정금액"),
            _pcell("파생상품부채", col="(가) 관련 수정금액", period="prior_end"),                                 # 20260407001606 t141
            _pcell("파생상품부채", col="수준2"),                                                                  # 20260402001813 t049
            _pcell("파생상품자산", col="당기말/수준 2"),
            _pcell("파생상품자산", col="Level 2", period="prior_end"),
            _pcell("파생상품부채", col="공정가치 수준/합 계"),
            # 말뭉치 점검 4회: 수준3 변동표의 매입·매도·기타 열(20260319000846 t274, 20260320000990 t068)
            _pcell("파생상품자산", col="매입", tt="범 주 기 초 당기손익 매입 매도 기타 기 말"),
            _pcell("파생상품자산", col="매도"),
            _pcell("파생상품자산", col="기타(*)", tt="구 분 기초금액 신규 상환 및 처분 평가 기타(*) 기말금액"),
            _pcell("파생상품부채", col="결제"),
        ]
        for c in cases:
            self.assertIsNone(_deriv_side(c), (c["label"], c["col_header"]))
        self.assertEqual(_deriv_side(_pcell("파생상품부채", col="당기말")), "liability")
        self.assertEqual(_deriv_side(_pcell("파생상품부채", col="제 15(당) 기말", region_kind="statement_bs")), "liability")


class PromptI3Test(unittest.TestCase):
    def test_i3_version_and_rules(self):
        self.assertEqual(PROMPT_VERSION, "i6")  # i3 규칙 문구는 i6 에도 남는다
        user = build_messages(_inp())[1]["content"]
        for phrase in ("1499943000 이 아니다", '"s:494"', "<표 id>:r<행>:v<번호>", "[current_end]",
                       "null 이 아닌 필드는 모두 evidence", "「전환권가치」", "「전환사채 및 조기상환권」",
                       "전환권·신주인수권·교환권", "조기상환청구권·매도청구권", "기간", "수준", "현금흐름표",
                       "파생상품자산 및 부채", "회차"):
            self.assertIn(phrase, user)
        # 검증기가 거부하는 근거를 가르치지 않는다(i2 의 네 줄)
        for banned in ("「전환권을 금융부채로 분류」 같은 문장)", "파생상품부채\n     변동표의 「기말」 행",
                       "제목이 「파생상품부채의 내역」인 표의 회차 행", "「…내재파생상품」·「전환권 및 조기상환권」 같은 옵션 행"):
            self.assertNotIn(banned, user)

    def test_prompt_examples_pass_validator(self):
        """프롬프트 예시 출력이 지금 검증기를 통과한다(예시가 거부되는 근거를 가르치지 않는다)."""
        for inp, rec in instruments_mod._EXAMPLE_CASES_I6:
            out, reasons = validate(copy.deepcopy(rec), inp)
            self.assertEqual(reasons, [], rec.get("series_key"))
            for f, ids in rec["evidence"].items():
                self.assertTrue(out["evidence"].get(f), f)



# ---- fix round 5 (실제 공시 칸에서 만든 경우) ----
def _rec5(key, period, value, ids, *, series_key="CB-3", kind="CB", option_class=None, oc_ids=None):
    ev = {f"balance.{period}.{key}": ids}
    if oc_ids:
        ev["option_class"] = oc_ids
    return {"instrument": "전환사채", "series_key": series_key, "kind": kind,
            "balance": {period: {key: value}}, "option_class": option_class, "embedded_separation": None,
            "evidence": ev}


class BothSidesSpellingTest(unittest.TestCase):
    """두 쪽을 다 말하는 제목·머리글·행 이름은 어떤 표기든 쪽을 모른다(ㆍ U+318D, ∙, 붙여 쓰기, 괄호)."""

    def test_spellings_unknown(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_title_side
        for t in ("파생상품자산ㆍ부채", "파생상품자산∙부채", "파생상품자산부채", "파생금융자산(부채)", "파생상품부채(자산)",
                  "파생상품 자산(부채)의 내역", "파생상품자산ㆍ파생상품부채", "파생상품부채/자산", "파생금융상품자산 ( 부채 )",
                  "전기 당기손익공정가치측정금융자산 파생금융자산(부채)"):
            self.assertIsNone(_deriv_title_side(t), t)
        self.assertEqual(_deriv_title_side("파생상품부채(유동)"), "liability")
        self.assertEqual(_deriv_title_side("파생상품자산(비유동)의 내역"), "asset")

    def test_real_wrong_side_cell(self):
        """20260318001372:00760:t058:r5:c2 「기말 잔액」 × 「파생금융자산(부채)」 (156,000) — 자산으로 받으면 안 된다."""
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cell = _pcell("기말 잔액", col="파생금융자산(부채)", period="prior_period", value=-156000,
                      tt="전기 당기손익공정가치측정금융자산 파생금융자산(부채)", nt="재무위험관리")
        self.assertIsNone(_deriv_side(cell))
        inp = {"rcept_no": "R", "cells": {"t058:r5:c2": cell}, "tables_text": "", "sentences": {}}
        out, reasons = validate(_rec5("derivative_asset", "prior_end", -156000, ["t058:r5:c2"]), inp)
        self.assertIsNone(out["balance"]["prior_end"]["derivative_asset"])
        # 행 이름이 두 쪽을 말해도 모른다
        for lab in ("파생상품자산(부채)", "파생상품자산ㆍ부채", "파생상품부채(자산)"):
            self.assertIsNone(_deriv_side(_pcell(lab, tt="파생상품부채의 내역")), lab)


class UnknownPeriodTest(unittest.TestCase):
    """기간 모르는 칸은 값 열 하나인 잔액 표(제목·주석이 당기말·현재, 발행시·전환시·행사시·최초·평가 없음)에서
    current_end 에만 맞는다. 20260323000654:00761 t052·t054·t055 r1:v1(가치평가 표 「가. 전환시」·「나. 발행시」)."""

    T052 = ("② 가치평가기법과 직접적 또는 간접적으로 관측 가능한 유의적 투입변수당기말과 전기말 현재 수준 3의 공정가치 측정을 "
            "위해 사용된 평가기법과 직접적 또는 간접적으로 관측가능한 유의적 투입변수는 다음과 같습니다.1) 당기가. 전환시")

    def _cell(self, tt, value=100, n=1):
        return _pcell("파생상품부채", col=None, period=None, value=value, tt=tt, nt="금융상품", n_value_cols=n)

    def test_event_valuation_tables_rejected(self):
        from dart_search_mcp.ledger.instrument_validate import _period_ok
        for tt in (self.T052, "나. 발행시", "다. 전환시", "당기말 현재 행사시 파생상품부채", "최초 인식 당기말 현재",
                   "당기말 현재 가치평가"):
            c = self._cell(tt)
            self.assertFalse(_period_ok(c, "current_end"), tt)
            self.assertFalse(_period_ok(c, "prior_end"), tt)
        cells = {"t052:r1:v1": self._cell(self.T052, 12921817627), "t054:r1:v1": self._cell("나. 발행시", 8130484175)}
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        for cid, v in (("t052:r1:v1", 12921817627), ("t054:r1:v1", 8130484175)):
            for period in ("current_end", "prior_end"):
                out, reasons = validate(_rec5("derivative_liability", period, v, [cid]), inp)
                self.assertIsNone(out["balance"][period]["derivative_liability"], (cid, period))
                self.assertIn(f"period_mismatch:balance.{period}.derivative_liability", reasons)

    def test_single_column_balance_table_current_only(self):
        from dart_search_mcp.ledger.instrument_validate import _period_ok
        for tt in ("당기말 현재 파생상품부채의 내역은 다음과 같습니다.", "보고기간종료일 현재 파생상품부채"):
            c = self._cell(tt)
            self.assertTrue(_period_ok(c, "current_end"), tt)
            self.assertFalse(_period_ok(c, "prior_end"), tt)
        self.assertFalse(_period_ok(self._cell("파생상품부채의 내역"), "current_end"))      # 당기말·현재 없음
        self.assertFalse(_period_ok(self._cell("당기말 현재 파생상품부채", n=2), "current_end"))  # 값 열 둘
        # 주석 제목이 당기말·현재를 말해도 된다
        c = _pcell("파생상품부채", col=None, period=None, tt="파생상품부채", nt="당기말 현재 파생상품", n_value_cols=1)
        self.assertTrue(_period_ok(c, "current_end"))


class NonDerivativeWordTest(unittest.TestCase):
    """「비파생금융부채」는 파생상품부채가 아니다 — 20260317000731:00760:t183:r3:c1(31,921,365), 20260406000195:00760:t074:r4:c1."""

    def test_non_derivative_rows(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cases = [
            _pcell("비파생금융부채(*)", col="장부금액", value=31921365, multiplier=1, n_value_cols=5,
                   tt="보고기간종료일 현재 금융부채의 잔존계약만기에 따른 만기분석은 다음과 같습니다.", nt="위험관리"),
            _pcell("비파생금융부채(*)", col="장부금액", value=766894, multiplier=1, n_value_cols=5,
                   tt="보고기간말 현재 금융부채의 잔존계약만기에 따른 만기 분석은 다음과 같습니다.", nt="위험관리"),
            # 만기 제목이 아니어도 비파생은 파생이 아니다
            _pcell("비파생금융부채", col="장부금액", tt="당기말 현재 금융부채의 내역", nt="금융상품"),
            _pcell("기말", col="비파생금융부채", tt="당기말 현재 금융부채의 내역"),
            _pcell("비파생상품부채", path=["금융부채", "비파생상품부채"], tt="파생상품부채의 내역"),
        ]
        for c in cases:
            self.assertIsNone(_deriv_side(c), (c["label"], c["col_header"]))
        cell = cases[2]
        inp = {"rcept_no": "R", "cells": {"t183:r3:c1": cell}, "tables_text": "", "sentences": {}}
        out, reasons = validate(_rec5("derivative_liability", "current_end", 100, ["t183:r3:c1"]), inp)
        self.assertIsNone(out["balance"]["current_end"]["derivative_liability"])


class SeriesMismatchTest(unittest.TestCase):
    """금액 근거 칸(합 포함)의 회차가 기록 회차와 다르면 series_mismatch. 20260407003648:00761:t166 r3+r4 c3 = 5,989,580
    (8회)을 CB-7 기록에 댄 경우."""

    def _inp(self):
        return HostSumSeriesPathTest()._inp()

    def test_other_series_sum_rejected(self):
        rec = _rec5("host_carrying", "prior_end", 5989580, ["S:r3:c3", "S:r4:c3"], series_key="CB-7")
        out, reasons = validate(rec, self._inp())
        self.assertIsNone(out["balance"]["prior_end"]["host_carrying"])
        self.assertIn("series_mismatch:balance.prior_end.host_carrying", reasons)
        rec = _rec5("host_carrying", "prior_end", 5989580, ["S:r3:c3", "S:r4:c3"], series_key="CB-8")
        out, reasons = validate(rec, self._inp())
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], 5989580)
        self.assertEqual(reasons, [])

    def test_other_series_single_cell_rejected(self):
        rec = _rec5("face", "prior_end", 9500000, ["S:r3:c3"], series_key="CB-7")
        out, reasons = validate(rec, self._inp())
        self.assertIsNone(out["balance"]["prior_end"]["face"])
        self.assertIn("series_mismatch:balance.prior_end.face", reasons)
        # 기록 회차를 모르면 회차 붙은 칸은 맞지 않는다
        rec = _rec5("face", "prior_end", 9500000, ["S:r3:c3"], series_key="")
        out, reasons = validate(rec, self._inp())
        self.assertIsNone(out["balance"]["prior_end"]["face"])
        self.assertIn("series_mismatch:balance.prior_end.face", reasons)
        # 회차 없는 칸은 회차 없는 기록·있는 기록 모두 받는다
        rec = _rec5("host_carrying", "prior_end", -100, ["S:r9:c3"], series_key="CB-7")
        out, reasons = validate(rec, self._inp())
        self.assertEqual(out["balance"]["prior_end"]["host_carrying"], -100)

    def test_derivative_row_other_series(self):
        tt = "당기말 현재 파생상품부채의 내역은 다음과 같습니다."
        cells = {"D:r1:c1": _pcell("제8회 전환사채 전환권", tt=tt, nt="파생상품")}
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        out, reasons = validate(_rec5("derivative_liability", "current_end", 100, ["D:r1:c1"], series_key="CB-7"), inp)
        self.assertIsNone(out["balance"]["current_end"]["derivative_liability"])
        self.assertIn("series_mismatch:balance.current_end.derivative_liability", reasons)
        out, reasons = validate(_rec5("derivative_liability", "current_end", 100, ["D:r1:c1"], series_key="CB-8"), inp)
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 100)

    def test_series_a(self):
        cells = {"D:r1:c1": _pcell("파생상품부채", path=["Series A", "파생상품부채"], tt="당기말 현재 파생상품부채")}
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        out, reasons = validate(_rec5("derivative_liability", "current_end", 100, ["D:r1:c1"], series_key="RCPS-A",
                                      kind="RCPS"), inp)
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 100)


class NonBalanceTablesTest(unittest.TestCase):
    """제목·주석 제목이 만기·유동성·신용위험·이연법인세·상계·담보를 말하거나, 머리글이 현금유입·현금유출·거래상대방
    종류인 칸은 잔액이 아니다(「현재」가 제목에 있어도)."""

    CASES = [
        # 20260319001047:00760:t108 r8·r9 c6
        _pcell("현금유입", path=["위험회피목적파생상품부채(주1)", "현금유입"], col="합 계/〈비파생금융상품〉",
               period="current_period", value=3372116, n_value_cols=5, nt="유동성 위험",
               tt="(3) 금융부채의 잔존계약만기 분석 당기말과 전기말 현재 금융부채의 잔존계약만기는 다음과 같습니다."),
        _pcell("현금유출", path=["위험회피목적파생상품부채(주1)", "현금유출"], col="합 계/〈비파생금융상품〉",
               period="current_period", value=3483486, n_value_cols=5, nt="유동성 위험",
               tt="(3) 금융부채의 잔존계약만기 분석 당기말과 전기말 현재 금융부채의 잔존계약만기는 다음과 같습니다."),
        # 20260319001047:00760:t100 신용건전성, t070 최대노출
        _pcell("위험회피목적파생상품자산", col="합 계", period="current_period", value=22174, nt="보험위험",
               tt="당기말과 전기말 현재 위험회피목적 파생상품자산의 신용건전성은 다음과 같습니다."),
        _pcell("위험회피목적파생상품자산", col="최대노출", period="current_period", value=22174, nt="보험위험",
               tt="2) 신용보강 후 최대노출정도당기말과 전기말 현재 신용위험 보강 후 최대노출정도는 다음과 같습니다."),
        # 20260407002477:00760:t108:r19:c3 이연법인세 변동
        _pcell("파생상품부채", col="기말금액", value=0, n_value_cols=5, nt="매각예정비유동자산비유동자산(또는",
               tt="32-3. 당기와 전기 중 이연법인세자산(부채)의 변동내역은 다음과 같습니다.① 당기"),
        # 20260325000202:00761:t360:r9:c2 거래상대방 집중도
        _pcell("파생상품자산", col="기업", value=392715, n_value_cols=3, nt="위험관리",
               tt="35-2-7 보고기간종료일 현재 연결실체의 금융자산의 신용위험에 대한 거래상대방별 집중도는 다음과 같습니다.〈당기말〉"),
        # 상계·담보 열
        _pcell("파생상품부채", col="상계되는 금액", tt="당기말 현재 파생상품부채"),
        _pcell("파생상품부채", col="담보", tt="당기말 현재 파생상품부채"),
        _pcell("파생상품부채", col="금융기관", tt="당기말 현재 파생상품부채"),
        _pcell("파생상품부채", tt="당기말 현재 상계 대상 금융상품"),
        _pcell("파생상품부채", tt="당기말 현재 파생상품부채", nt="신용위험"),
    ]

    def test_deriv_side_rejects(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        for c in self.CASES:
            self.assertIsNone(_deriv_side(c), (c["label_path"], c["col_header"], c["table_title"]))
        self.assertEqual(_deriv_side(_pcell("파생상품부채", tt="당기말 현재 파생상품부채의 내역")), "liability")

    def test_amounts_rejected(self):
        for i, c in enumerate(self.CASES):
            cell = dict(c, period="current_end")
            inp = {"rcept_no": "R", "cells": {"X:r1:c1": cell}, "tables_text": "", "sentences": {}}
            for key in ("derivative_liability", "derivative_asset", "host_carrying", "face"):
                out, reasons = validate(_rec5(key, "current_end", cell["value"], ["X:r1:c1"]), inp)
                self.assertIsNone(out["balance"]["current_end"][key], (i, key))


class HostSumPeriodReasonTest(unittest.TestCase):
    def test_period_only_failure_reason(self):
        tt = "전환사채 내역"
        cells = {"H:r1:c2": _pcell("액면금액", col="전기말", period="prior_end", value=1000, tt=tt),
                 "H:r2:c2": _pcell("전환권조정", col="전기말", period="prior_end", value=-100, tt=tt)}
        inp = {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": {}}
        out, reasons = validate(_rec5("host_carrying", "current_end", 900, ["H:r1:c2", "H:r2:c2"]), inp)
        self.assertIsNone(out["balance"]["current_end"]["host_carrying"])
        self.assertEqual(reasons, ["period_mismatch:balance.current_end.host_carrying"])


class HolderSideSentenceTest(unittest.TestCase):
    """보유자 쪽 문장(취득한 전환사채의 전환권 → 금융자산)은 옵션 부채 근거가 아니다 — 자산은 문장 전체에서 본다."""

    def test_holder_side(self):
        from dart_search_mcp.ledger.instrument_validate import _option_liability_sentence
        no = ("취득한 전환사채에 내재된 전환권은 파생상품으로 분류하여 당기손익-공정가치측정금융자산으로 계상하고 있습니다.",
              "회사가 취득한 전환사채의 전환권은 파생상품으로 인식하였으며, 이는 파생상품자산에 포함되어 있습니다.")
        for t in no:
            self.assertFalse(_option_liability_sentence(t), t)
        self.assertTrue(_option_liability_sentence(
            "전환사채에 내재된 전환권은 파생상품으로 분류하였습니다. 회사는 금융자산을 공정가치로 측정합니다."))


class PromptI4Test(unittest.TestCase):
    def test_i4_version_and_rules(self):
        self.assertEqual(PROMPT_VERSION, "i6")  # i6 가 i4 문구를 그대로 담는다
        user = build_messages(_inp())[1]["content"]
        for phrase in ("equity_conflict", "자본요소", "회차가 다르", "만기", "이연법인세", "현금유입", "거래상대방",
                       "파생금융자산(부채)", "발행시", "비파생"):
            self.assertIn(phrase, user)
        self.assertNotIn("「비파생금융부채」.", user)



# ---- T9b (실제 공시 칸·문장 값을 build_input 출력에서 손으로 옮긴 경우 — 테스트는 dart_collected 를 읽지 않는다) ----
def _rcell(value, label, *, path=None, col, period="current_end", tt, nt, n_value_cols, multiplier=1):
    return {"raw": f"{value:,}", "value": value, "unit_kind": "krw", "multiplier": multiplier, "period": period,
            "label": label, "label_path": path or [label], "col_header": col, "table_title": tt, "note_title": nt,
            "n_value_cols": n_value_cols, "region_kind": "note"}


def _inp9b(cells, sentences=None):
    return {"rcept_no": "R", "cells": cells, "tables_text": "", "sentences": sentences or {}}


# 20260403000226:00760 — t032 만기분석(「(1) 당기말」, 주석 재무위험관리), t036 장부금액·공정가치, t050 범주별
_T032 = {f"20260403000226:00760:t032:r6:c{c}": _rcell(v, "상환전환우선주부채(*2)", col=h, tt="(1) 당기말", nt="재무위험관리",
                                                    n_value_cols=6)
         for c, h, v in ((1, "3개월 미만", 65941634783), (2, "3개월~1년 이하", 0), (3, "1년~2년 이하", 0),
                         (4, "2년~5년 이하", 28543514150), (5, "5년 초과", 0), (6, "합계", 94485148933))}
_T036_R14 = _rcell(53344946525, "유동상환전환우선주부채", col="당기말/장부금액", nt="공정가치", n_value_cols=3,
                   tt="금융상품의 종류별 장부금액 및 공정가치는 다음과 같습니다.")
_T050_R5 = _rcell(53344946525, "상환전환우선주부채", col="상각후원가 측정금융부채", tt="가. 당기말", nt="범주별 금융상품",
                  n_value_cols=4)


class MaturityColumnTest(unittest.TestCase):
    """만기 구간·계약상현금흐름 열의 칸은 어느 금액의 근거도 아니다. 만기 구간 열이 있는 표의 「합계」 열도 같다(할인하지 않은
    금액). 장부금액 열은 아니다. 20260403000226:00760:t032:r6:c6 「상환전환우선주부채(*2)」 × 「합계」 = 94,485,148,933 →
    current_end 거부. 진짜 장부금액 53,344,946,525(t036 r14, t050 r5)."""

    def test_real_maturity_total_rejected_all_fields(self):
        cells = dict(_T032, **{"20260403000226:00760:t036:r14:c1": _T036_R14})
        inp = _inp9b(cells)
        c6 = "20260403000226:00760:t032:r6:c6"
        for key in ("face", "host_carrying", "equity_component", "derivative_liability", "derivative_asset"):
            rec = _rec5(key, "current_end", 94485148933, [c6], series_key="", kind="RCPS")
            out, reasons = validate(rec, inp)
            self.assertIsNone(out["balance"]["current_end"][key], key)
            self.assertEqual(reasons, [f"not_balance:balance.current_end.{key}"], key)
        rec = _rec5("host_carrying", "current_end", 53344946525, ["20260403000226:00760:t036:r14:c1"], series_key="",
                    kind="RCPS")
        out, reasons = validate(rec, inp)
        self.assertEqual(out["balance"]["current_end"]["host_carrying"], 53344946525)
        self.assertEqual(reasons, [])

    def test_bucket_column_alone_rejected(self):
        from dart_search_mcp.ledger.instrument_validate import _non_balance_cell
        for col in ("3개월 미만", "1년 이내", "1년~5년", "5년 초과", "계약상현금흐름", "잔존계약만기/1년 이내", "6개월 이내",
                    "1년 초과 5년 이내"):
            self.assertTrue(_non_balance_cell(_pcell("전환사채", col=col, tt="전환사채 내역")), col)
        for col in ("장부금액", "당기말/장부금액", "당기말", "합계", "2025년 12월 31일"):
            self.assertFalse(_non_balance_cell(_pcell("전환사채", col=col, tt="전환사채 내역")), col)

    def test_row_descriptor_not_maturity(self):
        """내역 표의 행 묶음에 든 만기 서술(20260323001468:00760:t102 「2025년~2026년」, 20260318001029:00760:t110
        「1년이내」)은 만기 구간 열이 아니다 — 그 표의 칸을 빼지 않는다."""
        from dart_search_mcp.ledger.instrument_validate import _non_balance_ids
        tt = "16. 차입금 및 사채(1) 당기말과 전기말 현재 차입금 및 사채의 내역은 다음과 같습니다."
        cells = {"B:r2:c4": _pcell("단기차입금(*1)", path=["중소기업은행", "단기차입금(*1)", "2.88~4.20", "2025년~2026년"],
                                   tt=tt, value=500),
                 "B:r3:c4": _pcell("제3회 전환사채", path=["제3회 전환사채", "1년이내"], tt=tt, value=900),
                 "B:r3:c5": _pcell("제3회 전환사채", path=["제3회 전환사채", "1년이내"], tt=tt, value=900, col="합계")}
        self.assertEqual(_non_balance_ids(cells), set())
        out, reasons = validate(_rec5("face", "current_end", 900, ["B:r3:c5"]), _inp9b(cells))
        self.assertEqual(out["balance"]["current_end"]["face"], 900)

    def test_carrying_column_in_maturity_table_kept(self):
        tt = "당기말 현재 금융부채의 내역"
        cells = {"M:r1:c1": _pcell("전환사채", col="장부금액", value=900, tt=tt),
                 "M:r1:c2": _pcell("전환사채", col="계약상현금흐름", value=1000, tt=tt),
                 "M:r1:c3": _pcell("전환사채", col="1년 이내", value=1000, tt=tt),
                 "M:r1:c4": _pcell("전환사채", col="합계", value=1000, tt=tt)}
        inp = _inp9b(cells)
        out, reasons = validate(_rec5("host_carrying", "current_end", 900, ["M:r1:c1"]), inp)
        self.assertEqual(out["balance"]["current_end"]["host_carrying"], 900)
        for cid in ("M:r1:c2", "M:r1:c3", "M:r1:c4"):
            out, reasons = validate(_rec5("face", "current_end", 1000, [cid]), inp)
            self.assertIsNone(out["balance"]["current_end"]["face"], cid)
            self.assertEqual(reasons, ["not_balance:balance.current_end.face"], cid)
        # 같은 금액·같은 열 이름이라도 만기 구간 열이 없는 표의 합계 열은 받는다
        inp2 = _inp9b({"N:r1:c4": _pcell("전환사채", col="합계", value=1000, tt=tt)})
        out, reasons = validate(_rec5("face", "current_end", 1000, ["N:r1:c4"]), inp2)
        self.assertEqual(out["balance"]["current_end"]["face"], 1000)


class UnknownPeriodExclusionTest(unittest.TestCase):
    """기간 모르는 칸의 current_end 허용은 「취득일 현재」, 발행조건·주요 조건·발행내역 표, 자본금·주식 표, 「전기말 현재」만
    말하는 표에서는 쓰지 않는다."""

    CASES = [
        # 20260319000768:00761:t034:r2:c1 (취득일 이전대가)
        ("20260319000768:00761:t034:r2:c1",
         _rcell(5000000, "전환사채", col="금 액", period="unknown", n_value_cols=1, multiplier=1000, nt="지배회사의",
                tt="취득일 현재 이전대가의 종류별 공정가치는 다음과 같습니다."), "face"),
        # 20260402003465:00761:t135:r1:v1 「당기말 현재 전환사채의 발행조건」 액면가액
        ("20260402003465:00761:t135:r1:v1",
         _rcell(700000000, "액면가액", col=None, period=None, n_value_cols=1, nt="차입금",
                tt="당기말 현재 전환사채의 발행조건은 아래와 같습니다."), "face"),
        # 20260403001116:00760:t058:r4:v1 「당기말 현재 … 발행내역」 권면총액
        ("20260403001116:00760:t058:r4:v1",
         _rcell(9000000000, "사채의 권면총액", col=None, period=None, n_value_cols=1, nt="전환사채",
                tt="(3) 당기말 현재 전환되지 아니한 전환사채의 발행내역은 다음과 같습니다."), "face"),
        # 20250828001542:00761:t130:r4:v1 「…내역 및 발행조건」 발행가액
        ("20250828001542:00761:t130:r4:v1",
         _rcell(5625000000, "발행가액", col=None, period=None, n_value_cols=1, nt="전환우선주",
                tt="(2) 당기말 현재 연결회사가 발행한 전환상환우선주의 내역 및 발행조건은 다음과 같습니다."), "face"),
        # 20250813001239:00760:t074:r10:v1 주석 자본금
        ("20250813001239:00760:t074:r10:v1",
         _rcell(3999967400, "발행금액", col=None, period=None, n_value_cols=1, nt="자본금",
                tt="(3) 보고기간종료일 현재 전환상환우선주의 내역은 다음과 같습니다."), "face"),
        # 20251013000078:00760:t035:r4:v1 자본금 표
        ("20251013000078:00760:t035:r4:v1",
         _rcell(35000000000, "자본금", col=None, period=None, n_value_cols=1, nt="자본금",
                tt="6. 자본금(1) 당기말 현재 자본금의 내역은 다음과 같습니다."), "face"),
        # 「주요 조건」, 주식 표, 전기말 현재만
        ("X:r1:v1", _rcell(100, "권면총액", col=None, period=None, n_value_cols=1, nt="사채",
                           tt="당기말 현재 전환사채의 주요 조건은 다음과 같습니다."), "face"),
        ("X:r2:v1", _rcell(100, "발행가액", col=None, period=None, n_value_cols=1, nt="사채",
                           tt="당기말 현재 발행한 보통주식의 내역"), "face"),
        ("20260403001272:00760:t062:r10:v1",
         _rcell(225000, "전환가격", col=None, period=None, n_value_cols=1, nt="전환사채",
                tt="(3) 전기말 현재 당사의 전환사채 발행조건은 다음과 같습니다."), "face"),
        ("X:r3:v1", _rcell(100, "전환사채", col=None, period=None, n_value_cols=1, nt="사채",
                           tt="전기말 현재 전환사채의 내역"), "host_carrying"),
    ]

    def test_excluded(self):
        from dart_search_mcp.ledger.instrument_validate import _period_ok
        for cid, c, key in self.CASES:
            self.assertFalse(_period_ok(c, "current_end"), cid)
            out, reasons = validate(_rec5(key, "current_end", c["value"], [cid], series_key=""), _inp9b({cid: c}))
            self.assertIsNone(out["balance"]["current_end"][key], cid)
            self.assertEqual(reasons, [f"period_mismatch:balance.current_end.{key}"], cid)

    def test_balance_tables_still_allowed(self):
        from dart_search_mcp.ledger.instrument_validate import _period_ok
        for tt in ("당기말 현재 파생상품부채의 내역은 다음과 같습니다.", "보고기간종료일 현재 전환사채의 내역",
                   "당기말 현재 상환전환우선주식부채의 내역"):
            c = _rcell(100, "전환사채", col=None, period=None, n_value_cols=1, nt="사채", tt=tt)
            self.assertTrue(_period_ok(c, "current_end"), tt)


class CategoryMatrixTest(unittest.TestCase):
    """범주 행렬에서 파생상품 행은 자기 범주 열(당기손익-공정가치·파생·위험회피)에서만 파생상품 칸이다.
    20260401000435:00760:t039:r11:c2 「파생상품부채」 × 「장부금액/상각후원가측정금융부채」 = 0,
    20260325000202:00761:t338:r5:c3 「파생상품자산」 × 「상각후원가측정금융상품」 = 0."""

    TT = "② 금융부채가. 당기말"

    def _c(self, col, value):
        return _rcell(value, "파생상품부채", col=col, tt=self.TT, nt="금융상품", n_value_cols=5, multiplier=1000)

    def test_other_category_column_rejected(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side
        cells = {"20260401000435:00760:t039:r11:c1": self._c("장부금액/당기손익-공정가치측정금융부채", 75778394),
                 "20260401000435:00760:t039:r11:c2": self._c("장부금액/상각후원가측정금융부채", 0),
                 "20260401000435:00760:t039:r11:c4": self._c("장부금액/합 계", 75778394)}
        self.assertIsNone(_deriv_side(cells["20260401000435:00760:t039:r11:c2"]))
        # c1(당기손익-공정가치 열)은 T9b 이전부터 열의 「손익」 낱말(_NOT_BALANCE_RE)로 거부된다 — 여기서 완화하지 않는다
        self.assertIsNone(_deriv_side(cells["20260401000435:00760:t039:r11:c1"]))
        self.assertEqual(_deriv_side(cells["20260401000435:00760:t039:r11:c4"]), "liability")
        inp = _inp9b(cells)
        out, reasons = validate(_rec5("derivative_liability", "current_end", 0, ["20260401000435:00760:t039:r11:c2"],
                                      series_key=""), inp)
        self.assertIsNone(out["balance"]["current_end"]["derivative_liability"])
        self.assertEqual(reasons, ["not_derivative:balance.current_end.derivative_liability"])
        out, reasons = validate(_rec5("derivative_liability", "current_end", 75778394,
                                      ["20260401000435:00760:t039:r11:c4"], series_key=""), inp)
        self.assertEqual(out["balance"]["current_end"]["derivative_liability"], 75778394)
        asset = _rcell(0, "파생상품자산", col="상각후원가측정금융상품", nt="금융상품 공정가치", n_value_cols=5,
                       multiplier=1000, tt="34-10 보고기간종료일 현재 금융상품의 범주별 분류는 다음과 같습니다.〈당기말〉")
        self.assertIsNone(_deriv_side(asset))
        for col in ("기타포괄손익-공정가치측정금융자산", "상각후원가측정금융자산", "매도가능금융자산", "대여금및수취채권",
                    "위험회피목적파생상품", "장부금액/위험회피수단"):
            side = _deriv_side(_rcell(1, "파생상품자산", col=col, tt="범주별 금융상품", nt="금융상품", n_value_cols=4))
            self.assertEqual(side is not None, "위험회피" in col, col)


class SeriesKindTest(unittest.TestCase):
    """회차 일치는 상품 종류도 본다: CB-3 기록이 「제3회 신주인수권부사채」 칸을 대면 series_mismatch. 칸에 종류가 없으면
    번호만 본다."""

    def test_kind_mismatch(self):
        tt = "당기말 현재 사채의 내역"
        cells = {"K:r1:c1": _pcell("제3회 신주인수권부사채", tt=tt, value=1000),
                 "K:r2:c1": _pcell("제3회", path=["신주인수권부사채", "제3회"], tt=tt, value=2000),
                 "K:r3:c1": _pcell("제3회", tt=tt, value=3000),
                 "K:r4:c1": _pcell("제3회 전환사채", tt=tt, value=4000)}
        inp = _inp9b(cells)
        for cid, v in (("K:r1:c1", 1000), ("K:r2:c1", 2000)):
            out, reasons = validate(_rec5("face", "current_end", v, [cid]), inp)
            self.assertIsNone(out["balance"]["current_end"]["face"], cid)
            self.assertEqual(reasons, ["series_mismatch:balance.current_end.face"], cid)
            out, reasons = validate(_rec5("face", "current_end", v, [cid], series_key="BW-3", kind="BW"), inp)
            self.assertEqual(out["balance"]["current_end"]["face"], v, cid)
        out, reasons = validate(_rec5("face", "current_end", 3000, ["K:r3:c1"]), inp)
        self.assertEqual(out["balance"]["current_end"]["face"], 3000)
        out, reasons = validate(_rec5("face", "current_end", 4000, ["K:r4:c1"]), inp)
        self.assertEqual(out["balance"]["current_end"]["face"], 4000)
        out, reasons = validate(_rec5("face", "current_end", 4000, ["K:r4:c1"], series_key="RCPS-3", kind="RCPS"), inp)
        self.assertIsNone(out["balance"]["current_end"]["face"])


# 실제 문장(원문 그대로)
S1307 = ("(주2) 전환사채에 내재된 조기상환청구권(Put Option)은 내재파생상품 분리요건을 충족하지 못하여 주계약 사채에 포함하여 "
         "부채로 인식하였습니다.")
S1308 = "(주3) 제3자 지정 매도청구권(Call Option)은 별도의 파생상품자산(당기손익공정가치측정금융자산)으로 구분 계상하였습니다."
S1317 = ("- 연결실체는 발행 전환사채 매도청구권(Call Option)을 외부평가기관에 공정가치평가액에 대해 별도의 파생상품자산으로 "
         "회계처리하였습니다.")
S98 = ("2.10 전환사채전환사채는 발행가액을 일반사채에 해당하는 부채부분과 전환권에 해당하는 자본부분으로 분리하여 자본부분의 "
       "가치를 전환권대가로 인식하고 있습니다.")
S262 = "상기 전환사채의 전환권은 자본(전환권대가)으로 분류되었으며, 사채권자의 풋옵션가치를 분리하여 계상하고 있지 않습니다."
S321 = ("또한, 전환사채에내재된 조기상환청구권은 내재파생상품 분리요건을 충족하지 못하여 주계약에 포함된 내재파생금융부채로 "
        "인식하였습니다.")
S188 = ("동 복합금융상품에 대하여 회사는 주계약과 내재파생상품을 포함한 전환상환우선주 전체를 당기손익-공정가치 측정 금융부채로 "
        "지정하였습니다.")
S213 = ("회사는 동 우선주에 포함된 전환권을 별도의 파생상품으로 분리하지 않고 최초 인식 시 전환우선주 전체를 당기손익-공정가치 "
        "측정 금융부채로 지정하였습니다.")
S207 = ("전환우선주의 내재된 전환권은 분리요건을 충족하지 않으므로 주계약과 내재파생상품을 분리하지 않고 하나의 당기손익-공정가치측정 "
        "금융부채로 재무상태표상 전환우선주부채로 계상하였습니다.")
S201 = ("전환상환우선주의 전환권이 자본요소를 만족하지 못할 경우 전환권과 조기상환권을 부채로 분류하며, 당기손익-공정가치측정 "
        "금융부채로 지정하여 하나의 금융부채로 보아 최초 인식합니다.")
S573 = "따라서 연결실체는 당기손익-공정가치 측정항목으로지정하여 공정가치의 변동을 당기손익으로 인식하고 있습니다."
S4 = ("당기말 현재 당사는 특수관계자인 ㈜유엑스엔이 발행한 전환상환우선주 및 신주인수권부사채를 당기손익-공정가치측정금융자산으로 "
      "계정을 분류하였으며, 전환상환우선주 및 신주인수권부사채 관련 약정사항은 다음과 같습니다.")
S488 = ("(*) 상환전환우선주부채는 원금에 대한 상환의무를 가지는 주계약과 조기상환권 및 전환권으로 구분되며, 주계약은 상각후원가로 "
        "평가되고, 조기상환권 및 전환권은 파생상품으로 인식하였습니다.")
S196 = "당사는 내재파생상품을 주계약과 분리하여 별도로 회계처리하고 있습니다."


def _rec_emb(claim, ids, *, kind="CB", series_key="CB-4", balance=None, evidence=None):
    ev = {"embedded_separation": ids}
    ev.update(evidence or {})
    return {"instrument": "전환사채", "series_key": series_key, "kind": kind, "balance": balance or {},
            "option_class": None, "embedded_separation": claim, "separated_targets": [], "evidence": ev}


class EmbeddedSeparationTest(unittest.TestCase):
    """embedded_separation 은 뜻이 맞는 근거가 있어야 확인된다(id 존재만으로는 아님). 아니면 no_separation_ref."""

    def _check(self, claim, ids, inp, ok, **kw):
        out, reasons = validate(_rec_emb(claim, ids, **kw), inp)
        if ok:
            self.assertEqual(out["embedded_separation"], claim, ids)
            self.assertEqual(reasons, [], ids)
        else:
            self.assertEqual(out["embedded_separation"], "unconfirmed", ids)
            self.assertEqual(reasons, ["no_separation_ref"], ids)

    def test_round5_wrong_records(self):
        # 20260319000983 연결 CB-4: 풋은 분리요건 미충족(주계약 포함), 제3자 지정 콜은 별도 파생상품자산 — 분리 아님
        inp = _inp9b({}, {"s1307": S1307, "s1308": S1308, "s1317": S1317})
        self._check("separated", ["s1307", "s1308", "s1317"], inp, False)
        for sid in ("s1307", "s1308", "s1317"):
            self._check("separated", [sid], inp, False)
        # 20260402002113 별도 CB-?1: 부채부분·자본부분(전환권대가) 분리는 내재파생상품 분리가 아니다
        self._check("separated", ["s98"], _inp9b({}, {"s98": S98}), False, series_key="")

    def test_separation_sentences(self):
        inp = _inp9b({}, {"s391": S391, "s488": S488, "s1179": S1179, "s196": S196})
        self._check("separated", ["s391"], inp, True)
        self._check("separated", ["s1179"], inp, True)
        self._check("separated", ["s488"], inp, True, kind="RCPS", series_key="")
        self._check("separated", ["s196"], inp, False)          # 어느 상품인지 말하지 않는다
        # 20260319001119 s413 — 「분리요건을 만족하는 … 조기상환청구권은 … 파생상품부채의 계정과목으로 계상」: 분리 동사가 없다
        s413 = ("(3) 파생금융상품전환사채의 최초 발행시점에 내재파생상품의 분리요건을 만족하는 발행자의 조기상환청구권은 "
                "파생상품자산, 사채권자의 조기상환청구권은 파생상품부채의 계정과목으로 계상하여 매 보고기간말에 공정가치로 "
                "평가하고 있습니다.")
        self._check("separated", ["s413"], _inp9b({}, {"s413": s413}), False)
        # 회사 자신의 풋 분리(콜이 제3자 지정이어도), 겹친 주어(「전환권은 자본으로 분류되지 않았으며, 전환권과 …」)
        s440 = ("(1) 당사는 전환사채에 부여된 조기상환청구권과 발행자(또는 발행자가 지정하는 제3자) 매도청구권을 주계약으로부터 "
                "분리하여 별도의 파생상품으로 분류하고 있습니다.")  # 20250812000814:00760 s440 원문
        s252 = ("(*2) 전환사채에 내재된 전환권은 자본으로 분류되지 않았으며, 전환권과 조기상환청구권(풋옵션)을 하나의 "
                "파생상품부채로 분류하였습니다.")  # 20260319000952:00760 s252 원문
        self._check("separated", ["s440"], _inp9b({}, {"s440": s440}), True)
        self._check("separated", ["s252"], _inp9b({}, {"s252": s252}), True)
        no = {
            "k": "신주인수권부사채에 내재된 조기상환청구권은 주계약에서 분리하여 파생상품부채로 인식하였습니다.",  # 종류 다름
            "n": "제5회 전환사채의 조기상환청구권은 주계약에서 분리하여 파생상품부채로 인식하였습니다.",          # 회차 다름
            "c": "전환사채에 내재된 파생상품이 주계약과 밀접하게 관련되어 있지 않은 경우 분리하여 파생상품으로 회계처리합니다.",
            "h": "회사가 취득한 전환사채의 전환권은 분리하여 파생상품으로 인식하고 당기손익-공정가치측정금융자산으로 계상하였습니다.",
            "e": "전환사채는 부채요소와 자본요소로 분리하여 부채요소는 공정가치로 측정하였습니다.",
            "m": "전환사채의 조기상환청구권은 분리하지 않았으며, 전환권은 분리하여 파생상품부채로 인식하였습니다.",
        }
        for k, t in no.items():
            self._check("separated", [k], _inp9b({}, {k: t}), False)
        self._check("separated", ["n"], _inp9b({}, {"n": no["n"]}), True, series_key="CB-5")

    def test_separation_cells(self):
        tt = "당기말 현재 파생상품부채의 내역은 다음과 같습니다."
        cells = {"D:r1:c1": _pcell("제4회 전환사채 조기상환청구권", tt=tt, nt="파생상품", value=300),
                 "D:r2:c1": _pcell("파생상품부채", tt=tt, nt="파생상품", value=300),        # 상품을 말하지 않음
                 "D:r3:c1": _pcell("제4회 전환사채 조기상환청구권", tt=tt, nt="파생상품", value=0),
                 "D:r4:c1": _pcell("제5회 전환사채 조기상환청구권", tt=tt, nt="파생상품", value=300),
                 "D:r5:c1": _pcell("전환권대가", tt="전환사채 내역", nt="전환사채", value=300),
                 "D:r6:c1": _pcell("파생상품부채", tt="당기말 현재 전환사채의 내역", nt="전환사채", value=300),
                 "D:r7:c1": _pcell("제4회 신주인수권부사채 조기상환청구권", tt=tt, nt="파생상품", value=300)}
        inp = _inp9b(cells)
        self._check("separated", ["D:r1:c1"], inp, True)
        self._check("separated", ["D:r6:c1"], inp, True)        # 표 제목이 전환사채를 말한다
        for cid in ("D:r2:c1", "D:r3:c1", "D:r4:c1", "D:r5:c1", "D:r7:c1"):
            self._check("separated", [cid], inp, False)
        # 범주 행렬의 일반 파생상품부채 행(상품 없음)
        t039 = {"20260401000435:00760:t039:r11:c1": _rcell(75778394, "파생상품부채", nt="금융상품", n_value_cols=5,
                                                          col="장부금액/당기손익-공정가치측정금융부채", tt="② 금융부채가. 당기말")}
        self._check("separated", list(t039), _inp9b(t039), False)

    def test_not_separated(self):
        sents = {"s262": S262, "s321": S321, "s188": S188, "a": "당사는 전환사채의 부채요소를 상각후원가로 측정하고 있습니다."}
        inp = _inp9b({}, sents)
        for sid in ("s262", "s321", "a"):
            self._check("not_separated", [sid], inp, True, series_key="CB-1")
        self._check("not_separated", ["s188"], inp, True, kind="RCPS", series_key="RCPS-1")  # 전체 FVPL 지정(d8)
        # 분리 문장·정책 문장·자본 분리 문장은 아니다
        for sid, t in (("s391", S391), ("s98", S98), ("p", "금융부채는 상각후원가로 측정합니다.")):
            self._check("not_separated", [sid], _inp9b({}, {sid: t}), False, series_key="CB-1")
        # 입력에 그 상품의 분리 파생상품 칸이 있거나, 기록에 확인된 파생상품 금액이 있으면 아니다.
        # (입력 전체 검사는 build_input 이 넘긴 문장·칸만 본다 — MAX_SENTENCES 로 잘린 문장의 모순은 못 잡는다. 최선 노력 검사.)
        cell = {"D:r1:c1": _pcell("전환사채 조기상환청구권", tt="당기말 현재 파생상품부채의 내역", nt="파생상품", value=300)}
        self._check("not_separated", ["s262"], _inp9b(cell, sents), False, series_key="CB-1")
        bal = {"current_end": {"derivative_liability": 300}}
        ev = {"balance.current_end.derivative_liability": ["D:r1:c1"]}
        self._check("not_separated", ["s262"], _inp9b(cell, sents), False, series_key="CB-1", balance=bal, evidence=ev)

    def test_not_separated_cells(self):
        # 20250812000814:00760:t116:r6:c4 「제4회 비분리형신주인수권부사채」 — 비분리형은 신주인수권 분리 여부(상품 이름)이다
        bw = _rcell(7500000000, "제4회 비분리형신주인수권부사채 (*3)", col="장부금액/당기말", nt="차입금", n_value_cols=4,
                    path=["제4회 비분리형신주인수권부사채 (*3)", "KRW", "0.0% (0.0%)", "2026.09.13"],
                    tt="(2) 당기말과 전기말 현재 차입금 및 차입부채의 만기 및 이자율 등은 다음과 같습니다.")
        self._check("not_separated", ["B:r6:c4"], _inp9b({"B:r6:c4": bw}), False, kind="BW", series_key="BW-4")
        # 20260403000226:00760:t050:r5:c1 상환전환우선주부채 × 상각후원가 측정금융부채(파생상품 칸 없음)
        cid = "20260403000226:00760:t050:r5:c1"
        self._check("not_separated", [cid], _inp9b({cid: _T050_R5}), True, kind="RCPS", series_key="")
        deriv = _pcell("전환권(상환전환우선주부채)", tt="당기말 현재 파생상품부채의 내역", nt="파생상품", value=5)
        self._check("not_separated", [cid], _inp9b({cid: _T050_R5, "D:r1:c1": deriv}), False, kind="RCPS",
                    series_key="")

    def test_whole_fvpl(self):
        sents = {"s188": S188, "s213": S213, "s207": S207, "s201": S201, "s573": S573, "s4": S4,
                 "o": "전환권은 당기손익-공정가치측정금융부채로 분류하였습니다."}
        inp = _inp9b({}, sents)
        for sid in ("s188", "s213", "s207"):
            self._check("whole_fvpl", [sid], inp, True, kind="RCPS", series_key="")
        for sid in ("s201", "s573", "s4", "o"):
            self._check("whole_fvpl", [sid], inp, False, kind="RCPS", series_key="")
        # 20260319001068:00760:t134:r8:c2 보유자 쪽(당기손익공정가치측정 금융자산 > 전환사채)
        held = _rcell(365445, "전환사채", path=["당기손익공정가치측정 금융자산", "전환사채"], col="당기말", nt="금융상품",
                      n_value_cols=2, multiplier=1000,
                      tt="30. 금융상품(1) 당기말 및 전기말 현재 범주별 금융상품의 내역은 다음과 같습니다.")
        self._check("whole_fvpl", ["H:r8:c2"], _inp9b({"H:r8:c2": held}), False, series_key="")
        fv = _rcell(1000, "상환전환우선주부채", col="장부금액/당기손익-공정가치측정금융부채", tt="② 금융부채가. 당기말",
                    nt="금융상품", n_value_cols=5)
        self._check("whole_fvpl", ["F:r8:c1"], _inp9b({"F:r8:c1": fv}), True, kind="RCPS", series_key="")
        self._check("whole_fvpl", ["F:r8:c1"], _inp9b({"F:r8:c1": dict(fv, value=0)}), False, kind="RCPS",
                    series_key="")
        self._check("whole_fvpl", ["F:r8:c1"], _inp9b({"F:r8:c1": fv}), False, kind="CB", series_key="")


class T9bFixRound1Test(unittest.TestCase):
    """fix round 1: 분리 칸은 기본이 부채 쪽이고, 칸은 상품 종류 낱말로만 상품에 묶인다(일반 옵션 낱말 아님).
    자산 쪽 칸은 행·열이 기록 종류를 말하고 제3자·권자(보유자)·회사 이름 층이 없을 때만."""

    REAL = [
        # 20260407000185:00760:t072:r2:c3 보유자 쪽 BW 신주인수권(투자회사 층)
        ("BW", _rcell(1562153, "신주인수권부사채권자의 신주인수권", path=["신주인수권부사채권자의 신주인수권", "(주)아이티아이"],
                      col="전기말", period="prior_end", nt="파생상품", n_value_cols=2, multiplier=1000,
                      tt="13. 파생상품자산(1) 보고기간종료일 현재 파생상품의 주요 내용은 다음과 같습니다.")),
        # 20260316001629:00760:t054:r1:c2 제3자 지정가능 콜
        ("CB", _rcell(1165850, "전환사채 제 3자 지정가능 매도청구권(콜옵션)",
                      path=["파생상품자산", "전환사채 제 3자 지정가능 매도청구권(콜옵션)"], col="공정가치",
                      nt="금융상품 공정가치", n_value_cols=2, multiplier=1000,
                      tt="3) 당기말 현재 당사는 공정가치 서열체계에서 수준3으로 분류되는 반복적인 공정가치 측정치에 대해 다음의 "
                         "가치평가기법과 투입변수를 사용하고 있습니다.")),
        # 20260323001689:00760:t250:r2:c3 관계없는 지분 콜
        ("CB", _rcell(5234, "키스톤뱅커스1호사모투자 합자회사지분 매도청구권(call option)", col="기말 금액", nt="파생상품",
                      n_value_cols=3, multiplier=1000,
                      tt="30. 파생상품(1) 파생상품자산당기 중 파생상품자산의 변동내역은 다음과 같습니다.")),
        # 20260319000615:00760:t066:r1:c2 일반 「매도청구권」만
        ("CB", _rcell(1078209, "매도청구권", col="전기말", period="prior_end", nt="파생상품", n_value_cols=2, multiplier=1000,
                      tt="10. 파생상품자산당기말 및 전기말 현재 당사의 파생상품자산의 내역은 다음과 같습니다.")),
    ]

    def test_real_asset_cells_not_separation(self):
        for kind, c in self.REAL:
            out, reasons = validate(_rec_emb("separated", ["X:r1:c1"], kind=kind, series_key=""), _inp9b({"X:r1:c1": c}))
            self.assertEqual(out["embedded_separation"], "unconfirmed", c["label"])
            self.assertEqual(reasons, ["no_separation_ref"], c["label"])

    def test_generic_option_liability_cell_needs_kind_word(self):
        tt = "당기말 현재 파생상품부채의 내역은 다음과 같습니다."
        cells = {"D:r1:c1": _pcell("조기상환청구권", tt=tt, nt="파생상품", value=300),
                 "D:r2:c1": _pcell("전환사채 조기상환청구권", tt=tt, nt="파생상품", value=300),
                 "D:r3:c1": _pcell("조기상환청구권", tt=tt, nt="전환사채", value=300)}   # 주석 제목이 종류를 말함
        inp = _inp9b(cells)
        out, reasons = validate(_rec_emb("separated", ["D:r1:c1"], series_key=""), inp)
        self.assertEqual(reasons, ["no_separation_ref"])
        for cid in ("D:r2:c1", "D:r3:c1"):
            out, reasons = validate(_rec_emb("separated", [cid], series_key=""), inp)
            self.assertEqual(out["embedded_separation"], "separated", cid)

    def test_own_kind_asset_cell_allowed(self):
        tt = "당기말 현재 파생상품자산의 내역은 다음과 같습니다."
        ok = _pcell("전환사채 매도청구권", tt=tt, nt="파생상품", value=300)
        for label in ("매도청구권", "전환사채 제3자 지정 매도청구권", "전환사채권자의 매도청구권"):
            c = _pcell(label, tt="전환사채 관련 파생상품자산의 내역", nt="전환사채", value=300)
            out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], series_key=""), _inp9b({"A:r1:c1": c}))
            self.assertEqual(reasons, ["no_separation_ref"], label)
        c = _pcell("전환사채 매도청구권", path=["㈜에이비씨", "전환사채 매도청구권"], tt=tt, nt="파생상품", value=300)
        out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], series_key=""), _inp9b({"A:r1:c1": c}))
        self.assertEqual(reasons, ["no_separation_ref"])
        # fix round 3(R17): 자산 칸만으로는 확인하지 않는다 — 같은 종류의 분리 문장이 입력에 있어야 한다
        out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], series_key=""), _inp9b({"A:r1:c1": ok}))
        self.assertEqual(reasons, ["no_separation_ref"])
        out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], series_key=""), _inp9b({"A:r1:c1": ok}, {"s1": S391}))
        self.assertEqual(out["embedded_separation"], "separated")

    def test_targets_cleared_when_unconfirmed(self):
        rec = _rec_emb("separated", ["s98"], series_key="")
        rec["separated_targets"] = ["conversion"]
        out, reasons = validate(rec, _inp9b({}, {"s98": S98}))
        self.assertEqual(out["embedded_separation"], "unconfirmed")
        self.assertEqual(out["separated_targets"], [])
        rec = _rec_emb("separated", ["s391"], series_key="")
        rec["separated_targets"] = ["redemption"]
        out, _ = validate(rec, _inp9b({}, {"s391": S391}))
        self.assertEqual(out["separated_targets"], ["redemption"])

    def test_derivative_row_not_non_derivative_amount(self):
        """20260401000435:00760:t039:r11:c2 「파생상품부채」 = 0 은 face·host_carrying·equity_component 근거가 아니다."""
        cid = "20260401000435:00760:t039:r11:c2"
        cell = _rcell(0, "파생상품부채", col="장부금액/상각후원가측정금융부채", tt="② 금융부채가. 당기말", nt="금융상품",
                      n_value_cols=5, multiplier=1000)
        for key in ("face", "host_carrying", "equity_component"):
            out, reasons = validate(_rec5(key, "current_end", 0, [cid], series_key=""), _inp9b({cid: cell}))
            self.assertIsNone(out["balance"]["current_end"][key], key)
            self.assertEqual(reasons, [f"derivative_row:balance.current_end.{key}"], key)

    def test_prompt_rule8_matches(self):
        user = build_messages(_inp())[1]["content"]
        self.assertNotIn("옵션만 — 분리다", user)
        for phrase in ("제3자", "권자", "회사 이름", "상품 종류 낱말", "파생상품자산 칸"):
            self.assertIn(phrase, user)


class T9bFixRound2Test(unittest.TestCase):
    """fix round 2: 보유자·투자 대상 자산 칸 — 20260327000244:00760:t073·t074(「당사가 보유하고 있는 파생상품의 내역」,
    「B사 전환사채의 전환권 등」·「H사 신주인수권부사채의 신주인수권 등」)은 분리 근거가 아니다."""

    T073 = _rcell(302801, "B사 전환사채의 전환권 등", col="파생상품자산", nt="파생상품", n_value_cols=4, multiplier=1000,
                  tt="21. 파생상품 보고기간종료일 현재 당사가 보유하고 있는 파생상품의 내역은 다음과 같습니다.〈당기말〉")
    T074 = _rcell(315789, "H사 신주인수권부사채의 신주인수권 등", col="파생상품자산", period="prior_end", nt="파생상품",
                  n_value_cols=4, multiplier=1000, tt="〈전기말〉")
    S198 = "당사는 파생상품을 포함한 전환사채 및 신주인수권부사채를 매매목적으로 보유하고 있습니다."

    def test_real_holder_cells(self):
        for kind, cid, c in (("CB", "20260327000244:00760:t073:r3:c1", self.T073),
                             ("BW", "20260327000244:00760:t074:r6:c1", self.T074)):
            inp = _inp9b({cid: c}, {"s198": self.S198})
            for sk in ("", f"{kind}-1"):
                out, reasons = validate(_rec_emb("separated", [cid], kind=kind, series_key=sk), inp)
                self.assertEqual(out["embedded_separation"], "unconfirmed", (cid, sk))
                self.assertEqual(reasons, ["no_separation_ref"], (cid, sk))

    def test_holder_title_and_investee_prefix(self):
        from dart_search_mcp.ledger.instrument_validate import _separation_cell
        base = dict(label="전환사채 매도청구권", label_path=["전환사채 매도청구권"])
        ok = _pcell("전환사채 매도청구권", tt="당기말 현재 파생상품자산의 내역", nt="파생상품", value=300)
        self.assertTrue(_separation_cell("A:r1:c1", ok, "CB", None, set(), sep_sentence=True))
        self.assertFalse(_separation_cell("A:r1:c1", ok, "CB", None, set()))  # R17: 문장 없이 자산 칸만으로는 아님
        for tt, nt in (("당기말 현재 당사가 보유하고 있는 파생상품자산의 내역", "파생상품"),
                       ("당기말 현재 파생상품자산의 내역", "보유한 금융상품"),
                       ("취득한 전환사채 관련 파생상품자산의 내역", "파생상품"),
                       ("파생상품자산(투자 전환사채)의 내역", "파생상품")):
            c = dict(ok, table_title=tt, note_title=nt, **base)
            self.assertFalse(_separation_cell("A:r1:c1", c, "CB", None, set(), sep_sentence=True), (tt, nt))
        for label in ("B사 전환사채 매도청구권", "에이비씨사의 전환사채 콜옵션", "Kxx사 상환전환우선주 전환권"):
            c = dict(ok, label=label, label_path=[label])
            kind = "RCPS" if "우선주" in label else "CB"
            self.assertFalse(_separation_cell("A:r1:c1", c, kind, None, set(), sep_sentence=True), label)
        for label in ("당사 전환사채 매도청구권", "회사 전환사채 매도청구권", "연결회사의 전환사채 매도청구권"):
            c = dict(ok, label=label, label_path=[label])
            self.assertTrue(_separation_cell("A:r1:c1", c, "CB", None, set(), sep_sentence=True), label)
        # 「투자자의 조기상환청구권」(부채 쪽)은 막지 않는다
        liab = _pcell("전환사채 투자자의 조기상환청구권", tt="당기말 현재 파생상품부채의 내역", nt="파생상품", value=300)
        self.assertTrue(_separation_cell("L:r1:c1", liab, "CB", None, set()))

    def test_host_sum_runs_after_derivative_row(self):
        """derivative_row 로 버린 칸이 하나여도 host 합 경로는 돈다."""
        tt = "당기말 현재 전환사채의 내역"
        cells = {"H:r1:c1": _pcell("사채", path=["전환사채 및 파생상품부채", "사채"], tt=tt, value=900),
                 "H:r2:c1": _pcell("전환권조정", path=["전환사채 및 파생상품부채", "전환권조정"], tt=tt, value=0)}
        inp = _inp9b(cells)
        out, reasons = validate(_rec5("host_carrying", "current_end", 900, ["H:r1:c1", "H:r2:c1"]), inp)
        self.assertEqual(out["balance"]["current_end"]["host_carrying"], 900)
        self.assertEqual(out["matched"]["balance.current_end.host_carrying"], "H:r1:c1+H:r2:c1")
        self.assertEqual(reasons, [])
        out, reasons = validate(_rec5("host_carrying", "current_end", 900, ["H:r1:c1"]), inp)
        self.assertEqual(reasons, ["derivative_row:balance.current_end.host_carrying"])

    def test_prompt_rule8_holder(self):
        user = build_messages(_inp())[1]["content"]
        for phrase in ("보유하고 있는", "B사 전환사채", "당사·회사"):
            self.assertIn(phrase, user)


class T9bFixRound3Test(unittest.TestCase):
    """fix round 3(R17): 자산 칸은 같은 종류의 분리 문장(자기 회사, 부정·조건 아님)이 입력에 있을 때만 separated 를 확인한다.
    상품 이름 뒤 변동 낱말 행(「전환사채 상환」·「전환사채 전환」)은 잔액 칸이 아니다 — 20260318000556:00760:t040:r4:c2."""

    TT = "당기말 현재 파생상품자산의 내역은 다음과 같습니다."

    def test_asset_cell_needs_sentence(self):
        cell = {"A:r1:c1": _pcell("신주인수권부사채 수의상환권", tt=self.TT, nt="파생상품", value=300)}
        out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], kind="BW", series_key=""), _inp9b(cell))
        self.assertEqual(reasons, ["no_separation_ref"])
        sep = "회사는 신주인수권부사채에 내재된 수의상환권을 주계약에서 분리하여 파생상품자산으로 인식하였습니다."
        out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], kind="BW", series_key=""), _inp9b(cell, {"s9": sep}))
        self.assertEqual(out["embedded_separation"], "separated")
        self.assertEqual(reasons, [])
        bad = {"n": "신주인수권부사채의 수의상환권은 주계약과 분리하지 않았습니다.",                      # 부정
               "c": "신주인수권부사채의 내재파생상품이 분리요건을 충족하는 경우 분리하여 파생상품으로 인식합니다.",  # 조건
               "h": "취득한 신주인수권부사채의 신주인수권은 분리하여 파생상품으로 인식하였습니다.",          # 보유자
               "k": "전환사채의 조기상환청구권은 주계약에서 분리하여 파생상품부채로 인식하였습니다."}          # 다른 종류
        for sid, t in bad.items():
            out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], kind="BW", series_key=""),
                                    _inp9b(cell, {sid: t}))
            self.assertEqual(reasons, ["no_separation_ref"], sid)

    def test_liability_cell_still_alone(self):
        cell = {"D:r1:c1": _pcell("제4회 전환사채 조기상환청구권", tt="당기말 현재 파생상품부채의 내역", nt="파생상품", value=300)}
        out, reasons = validate(_rec_emb("separated", ["D:r1:c1"]), _inp9b(cell))
        self.assertEqual(out["embedded_separation"], "separated")

    def test_movement_rows_after_kind(self):
        from dart_search_mcp.ledger.instrument_validate import _deriv_side, _non_balance_cell
        cid = "20260318000556:00760:t040:r4:c2"
        c = _rcell(-538564, "전환사채 상환", col="파생상품자산", period="prior_period", nt="공정가치", n_value_cols=3,
                   multiplier=1000, tt="전기 당기손익-공정가치 측정 금융자산 파생상품자산 파생상품부채")
        self.assertIsNone(_deriv_side(c))
        self.assertTrue(_non_balance_cell(c))
        out, reasons = validate(_rec_emb("separated", [cid], series_key=""), _inp9b({cid: c}, {}))
        self.assertEqual(reasons, ["no_separation_ref"])
        for label in ("전환사채 상환", "전환사채 전환", "제3회 전환사채의 행사", "상환전환우선주 발행", "신주인수권부사채 증가",
                      "교환사채 감소", "전환사채 취득", "전환사채 처분", "전환사채 대체"):
            self.assertTrue(_non_balance_cell(_pcell(label, tt="파생상품부채의 변동")), label)
            self.assertIsNone(_deriv_side(_pcell(label, col="파생상품부채", tt="파생상품부채의 변동")), label)
        for label in ("전환사채 전환권", "전환사채 조기상환청구권", "전환사채 상환할증금", "상환전환우선주부채", "전환상환우선주 전환권",
                      "전환사채 미상환잔액", "전환사채 행사가격", "제2회 전환사채 조기상환권", "사채할인발행차금"):
            self.assertFalse(_non_balance_cell(_pcell(label, tt="당기말 현재 전환사채의 내역")), label)
        # 금액 필드도 거부
        mv = _pcell("전환사채 상환", path=["전환사채 상환"], tt="전환사채의 변동", value=500)
        out, reasons = validate(_rec5("face", "current_end", 500, ["M:r1:c1"]), _inp9b({"M:r1:c1": mv}))
        self.assertEqual(reasons, ["not_balance:balance.current_end.face"])

    def test_prompt_r17(self):
        user = build_messages(_inp())[1]["content"]
        for phrase in ("자산 칸만으로는", "「전환사채 상환」"):
            self.assertIn(phrase, user)



class T9bFixRound4Test(unittest.TestCase):
    """fix round 4(R17 「자기 회사」): 분리 문장은 발행자 쪽만 — 보유자 낱말(발행회사에 대하여·행사할 수 있는 권리·보유·취득·
    대출채권·투자·금융자산으로 분류)이 든 문장은 아니다. 보유자 진술(「…전환사채…를 매매목적으로 보유」)이나 보유자 표가 있는
    공시에서는 분리 문장이 자기 회사를 발행자로 적어야 한다(「당사가 발행한」). 투자 대상 이름·보유자 제목은 부채 칸도 막는다.
    상품 뒤 변동 낱말 규칙은 옵션 복합어(상환전환권·전환상환우선주)·전환가치·발행자·「(발행)」을 변동으로 읽지 않는다.
    실제 칸·문장은 build_input 출력(20260327000244:00760 등)에서 손으로 옮겼다."""

    # 20260327000244:00760 (별도) — 전환사채·신주인수권부사채 보유자
    S137 = ("(*2) 기타대출채권으로 분류된 전환사채 및 신주인수권부사채와 관련된 금액으로 전환사채 및 신주인수권부사채에 내재된 "
            "전환권, 신주인수권 및 조기상환청구권을 별도로 분리하여 파생상품으로 인식한 후, 취득시점의 주계약의 공정가치와 "
            "전환사채 및 신주인수권부사채 액면금액의 차이를 현재가치할인차금으로 인식하였습니다.")
    S198 = "당사는 파생상품을 포함한 전환사채 및 신주인수권부사채를 매매목적으로 보유하고 있습니다."
    S199 = ("전환사채 및 신주인수권부사채에 포함되어 있는 전환권 등은 내재파생상품의 분리요건을 충족함에 따라 주계약과 분리하여 "
            "해당가치를 이항모형으로 평가하고 있으며, 발행회사에 대하여 전환권, 신주인수권 및 조기상환권을 행사할 수 있는 권리는 "
            "파생상품자산으로 인식하고, 발행회사의 매수청구권을 파생상품부채로 인식하고 있습니다.")
    # 20260410001301:00761 s419 — 자기 회사를 발행자로 적은 분리 문장
    S419 = ("연결회사가 발행한 상환전환우선주에 내재된 전환권 및 조기상환권은 분리하여 파생상품부채로 인식되었으며, "
            "상환전환우선주의 원본금액은 상환전환우선주부채로 계상하였습니다.")
    TT073 = "21. 파생상품 보고기간종료일 현재 당사가 보유하고 있는 파생상품의 내역은 다음과 같습니다.〈당기말〉"
    # 20260327000244:00760 t073:r3:c2 · t074:r2:c2 · t074:r6:c2 (부채 쪽, 투자 대상 이름)
    LIAB = (("CB", "20260327000244:00760:t073:r3:c2",
             _rcell(17849, "B사 전환사채의 전환권 등", col="파생상품부채", nt="파생상품", n_value_cols=4, multiplier=1000,
                    tt=TT073)),
            ("CB", "20260327000244:00760:t074:r2:c2",
             _rcell(20392, "C사 전환사채의 전환권 등", col="파생상품부채", period="prior_end", nt="파생상품", n_value_cols=4,
                    multiplier=1000, tt="〈전기말〉")),
            ("BW", "20260327000244:00760:t074:r6:c2",
             _rcell(25985, "H사 신주인수권부사채의 신주인수권 등", col="파생상품부채", period="prior_end", nt="파생상품",
                    n_value_cols=4, multiplier=1000, tt="〈전기말〉")))

    def test_holder_sentences_not_separation(self):
        for sid, text in (("s199", self.S199), ("s137", self.S137)):
            for kind in ("CB", "BW"):
                for extra in ({}, {"s198": self.S198}):
                    out, reasons = validate(_rec_emb("separated", [sid], kind=kind, series_key=""),
                                            _inp9b({}, {sid: text, **extra}))
                    self.assertEqual(out["embedded_separation"], "unconfirmed", (sid, kind, extra))
                    self.assertEqual(reasons, ["no_separation_ref"], (sid, kind, extra))

    def test_holder_sentence_does_not_unlock_asset_cell(self):
        ok = _pcell("전환사채 매도청구권", tt="당기말 현재 파생상품자산의 내역", nt="파생상품", value=300)
        for text in (self.S199, self.S137):
            out, reasons = validate(_rec_emb("separated", ["A:r1:c1"], series_key=""), _inp9b({"A:r1:c1": ok}, {"s1": text}))
            self.assertEqual(reasons, ["no_separation_ref"])

    def test_issuer_sentence_still_works(self):
        for kind, sid, text in (("CB", "s391", S391), ("CB", "s1179", S1179), ("RCPS", "s419", self.S419)):
            out, reasons = validate(_rec_emb("separated", [sid], kind=kind, series_key=""), _inp9b({}, {sid: text}))
            self.assertEqual(out["embedded_separation"], "separated", sid)
            self.assertEqual(reasons, [], sid)

    def test_holder_filing_needs_issuer_named_sentence(self):
        # 보유자 진술이 있는 공시: 발행자를 적지 않은 분리 문장(S391)은 아니고, 「연결회사가 발행한」 문장은 된다
        out, reasons = validate(_rec_emb("separated", ["s391"], series_key=""),
                                _inp9b({}, {"s198": self.S198, "s391": S391}))
        self.assertEqual(reasons, ["no_separation_ref"])
        out, reasons = validate(_rec_emb("separated", ["s419"], kind="RCPS", series_key=""),
                                _inp9b({}, {"s198": self.S198, "s419": self.S419}))
        self.assertEqual(out["embedded_separation"], "separated")
        # 보유자 표(투자 대상 이름 칸)만 있어도 같다
        kind, cid, c = self.LIAB[0]
        out, reasons = validate(_rec_emb("separated", ["s391"], series_key=""), _inp9b({cid: c}, {"s391": S391}))
        self.assertEqual(reasons, ["no_separation_ref"])

    def test_holder_wording_not_loosened(self):
        from dart_search_mcp.ledger.instrument_validate import _holder_wording
        self.assertTrue(_holder_wording("당사는 B사 전환사채를 보유하고 있으며 전환권을 분리하여 파생상품자산으로 인식하였습니다."))
        self.assertFalse(_holder_wording("사채권자가 보유한 조기상환청구권은 주계약에서 분리하여 파생상품부채로 인식하였습니다."))
        for t in (self.S199, self.S137):
            self.assertTrue(_holder_wording(t))

    def test_holder_statement_detection(self):
        from dart_search_mcp.ledger.instrument_validate import _holder_statement
        self.assertTrue(_holder_statement(self.S198))
        self.assertTrue(_holder_statement("당사가 보유하고 있는 전환사채는 당기손익-공정가치측정금융자산으로 분류하였습니다."))
        self.assertFalse(_holder_statement("사채권자가 보유한 조기상환청구권은 주계약에서 분리하여 파생상품부채로 인식하였습니다."))
        self.assertFalse(_holder_statement(S391))
        self.assertFalse(_holder_statement(self.S419))
        # 실제 보유자 진술(build_input 문장): 20250812000814:00760 s520, 20260319000952:00760 s195, 20260327000244 s137
        for t in ("3) 가격위험당사는 당기말 현재 재무제표상 당기손익인식공정가치측정금융자산으로 분류되는 NKGen Biotech Inc.가 "
                  "발행한 해외전환사채 및 신주인수권을 보유하고 있으며, 해당 장부금액 963백만원은 …",
                  "(*) (주)위드에이스튜디오 전환사채, (주)게임체인저월드와이드 상환전환우선주는 당사가 이사선임권을 통한 유의적 "
                  "영향력을 보유하고 있어 관계기업에 해당하나, 소유지분에 연계된 이익에 실질적으로 현재 접근하지 못함에 따라 금융상품 "
                  "기준서를 적용하여 장기투자자산으로 분류되었습니다(주석 26 참조).",
                  self.S137,
                  # 20251205000494:00761 s1290 — 지배기업은 자기 회사
                  "(*) 지배기업이 보유한 전환사채를 보통주 전환함에 따라 지배력을 취득하였습니다.",
                  # 20260318000859:00760 s560, 20260319000952:00761 s774
                  "(5) 당사가 보유한 (주)티아이티엘 전환사채와 관련하여 특정 요건을 충족할 경우 당사는 당사가 발행한 "
                  "전환상환우선주와 상계할 수 있는 약정을 체결하고 있습니다.",
                  "(*1) 종속기업인 (주)에이투지엔터테인먼트에 대한 전환우선주를 보유하고 있으며, 이사선임권을 통해 종속기업에 "
                  "유의적인 영향력을 행사할 수 있다고 판단하여 관계기업으로 분류하였습니다."):
            self.assertTrue(_holder_statement(t), t[:30])
        # 보유자 진술이 아닌 실제 문장: 희석주당이익(20260403000455 s412), 자기 회사 상품(20250728000410 s668),
        # 상품이 목적어가 아님(20260403002153 s482), 발행자 콜옵션을 금융자산으로(20260319000952 s251)
        for t in ("회사가 보유하고 있는 희석성 잠재적보통주로는 전환상환우선주가 있으며, 희석주당순손실 산정을 위해 …",
                  "(2) 특수관계자 거래 주석 35에 기재하고 있는 바와 같이 연결회사는 2024년 중 유의적인 영향력을 행사하는 기업이 "
                  "보유하고 있는 연결회사의 전환사채 액면 460억원을 현금 28억원에 양수하는 거래를 진행하였습니다.",
                  "(*2) 상환전환우선주 투자자로 투자계약서 상 기타비상무이사 1인을 지명할 수 있는 권리를 보유하고 있는 기업입니다.",
                  "(*1) 전환사채에 포함된 발행자에게 부여된 제3자 지정 가능 콜옵션을 별도의 금융자산으로 분류하였습니다.",
                  # 20250728000410:00761 s684 일부 — 남이 보유한 자기 회사 상품
                  "…유의적인 영향력을 행사하는 기업이 보유 중인 연결회사가 발행한 전환사채 액면 75,500백만원과 관련된 "
                  "파생상품부채가 17,962백만원의 금융부채로 계상되어 있습니다.",
                  # 20251128000641:00760 s44 일부 — 의결권을 보유한(보유의 목적어가 의결권)
                  "보고기간종료일 현재 당사의 보통주 발행주식수는 4,128,220주, 보통주자본금은 412,822천원이며, 의결권을 보유한 "
                  "전환상환우선주 및 전환우선주 등의 우선주 발행주식수는 3,271,294주이며, 주요 주주의 구성내역은 다음과 같습니다.",
                  # 20260320000625:00760 s355·s377 — 보유자가 투자자·제3자
                  "(*2) 종속회사가 발행한 상환전환우선주를 보유한 투자자가 회사에 매도할 수 있는 풋옵션입니다.",
                  "(*2) (주)그로젠바이오가 발행하고 제3자가 보유하고 있는 의결권 있는 상환전환우선주를 포함하여 고려한 지분율은 "
                  "당기말 현재 44.6%(전기말 42.3%)입니다.",
                  # 20260402001950:00761 s367 — 보유자의 청구(발행자 문장)
                  "(*1) 연결회사는 보유자의 청구에 따라 상환전환우선주를 상환해야 할 의무가 있으며 보유자에 대한 상환이 연결회사의 "
                  "재량이 아닌 보유자의 선택에 의해 결정되어지므로, 상환전환우선주 중 보유자의 청구에 따라 지급하여야 하는 부채를 "
                  "금융부채로 분류하였습니다.",
                  # 20250828001542:00761 s46 — 주주 지분(우선주는 전환·상환이 붙어야 상품)
                  "지배기업의 보통주와 의결권이 있는 우선주를 합산한 보유주식수와 지분율을 기준으로 최대주주는 ○○○입니다."):
            self.assertFalse(_holder_statement(t), t[:30])

    def test_real_holder_liability_cells(self):
        for kind, cid, c in self.LIAB:
            for sents in ({}, {"s198": self.S198}):
                for sk in ("", f"{kind}-1"):
                    out, reasons = validate(_rec_emb("separated", [cid], kind=kind, series_key=sk), _inp9b({cid: c}, sents))
                    self.assertEqual(out["embedded_separation"], "unconfirmed", (cid, sk))
                    self.assertEqual(reasons, ["no_separation_ref"], (cid, sk))

    def test_liability_cell_holder_title_and_filing(self):
        from dart_search_mcp.ledger.instrument_validate import _separation_cell
        own = _pcell("전환사채 전환권", tt=self.TT073, col="파생상품부채", nt="파생상품", value=300)
        # 보유자 제목(일반 「…보유하고 있는 파생상품」) 표의 부채 칸: 같은 종류 발행자 분리 문장이 있어야 한다
        self.assertFalse(_separation_cell("L:r1:c1", own, "CB", None, set()))
        self.assertTrue(_separation_cell("L:r1:c1", own, "CB", None, set(), sep_sentence=True))
        out, reasons = validate(_rec_emb("separated", ["L:r1:c1"], series_key=""), _inp9b({"L:r1:c1": own}))
        self.assertEqual(reasons, ["no_separation_ref"])
        out, reasons = validate(_rec_emb("separated", ["L:r1:c1"], series_key=""),
                                _inp9b({"L:r1:c1": own}, {"s391": S391}))
        self.assertEqual(out["embedded_separation"], "separated")
        # 보유자 공시의 부채 칸(제목은 평범): 발행자를 적은 문장이 있어야 한다
        plain = _pcell("전환사채 전환권", tt="당기말 현재 파생상품부채의 내역", nt="파생상품", value=300)
        self.assertFalse(_separation_cell("L:r1:c1", plain, "CB", None, set(), holder_filing=True))
        out, reasons = validate(_rec_emb("separated", ["L:r1:c1"], series_key=""),
                                _inp9b({"L:r1:c1": plain}, {"s198": self.S198}))
        self.assertEqual(reasons, ["no_separation_ref"])
        # 보유자 공시라도 표 제목이 자기 회사를 발행자로 적으면 부채 칸은 된다 — 20260319000952:00760:t113:r7:c1
        t113 = _rcell(1, "파생상품부채(*2)", col="당기말", nt="전환사채", n_value_cols=2, multiplier=1000,
                      tt="2) 당기말 현재 당사가 발행한 전환사채 및 관련 내재파생상품의 세부내역은 다음과 같습니다.")
        t113["value"] = 1250000
        self.assertTrue(_separation_cell("L:r1:c1", t113, "CB", None, set(), holder_filing=True))
        out, reasons = validate(_rec_emb("separated", ["L:r1:c1"], series_key=""),
                                _inp9b({"L:r1:c1": t113}, {"s198": self.S198}))
        self.assertEqual(out["embedded_separation"], "separated")
        # 회사 이름이 든 부채 칸 — 20260319000952:00760:t119:r2:c1(종속기업 우선주 투자자에게 써 준 풋옵션)
        a2z = _rcell(500, "(주)에이투지엔터테인먼트 전환우선주 풋옵션(*)", col="당기말/유동", nt="파생상품", n_value_cols=4,
                     multiplier=1000, tt="(2) 파생상품부채의 상세내역은 다음과 같습니다.")
        self.assertFalse(_separation_cell("L:r1:c1", a2z, "RCPS", None, set(), sep_sentence=True))
        # 투자 대상 이름은 문장이 있어도 막는다(부채·자산 모두); 종속회사·자회사도 막는다(거부는 안전 — 문서화)
        for label in ("B사 전환사채의 전환권 등", "종속회사의 전환사채 전환권", "자회사 전환사채 전환권"):
            c = dict(plain, label=label, label_path=[label])
            self.assertFalse(_separation_cell("L:r1:c1", c, "CB", None, set(), sep_sentence=True), label)
        # 자산 칸은 보유자 제목이면 발행자 문장이 있어도 막는다(fix round 2 그대로)
        asset = _pcell("전환사채 매도청구권", tt=self.TT073, col="파생상품자산", nt="파생상품", value=300)
        self.assertFalse(_separation_cell("A:r1:c1", asset, "CB", None, set(), sep_sentence=True))
        # 부채 칸이 보유자 표에서 막혀도 not_separated 모순 검사는 여전히 그 칸을 본다(느슨해지지 않음)
        out, reasons = validate(_rec_emb("not_separated", ["s262"], series_key=""),
                                _inp9b({"L:r1:c1": own}, {"s262": S262}))
        self.assertEqual(reasons, ["no_separation_ref"])

    def test_movement_word_exclusions(self):
        from dart_search_mcp.ledger.instrument_validate import _non_balance_cell
        real = (  # (cid, label_path, col, title) — build_input 출력
            ("20260319000779:00760:t152:r2:c1", ["제3회 전환사채 전환권(전환가치)"], "당 기 말/장부금액", "부채로 분류된 각 금융부채의 장부금액"),
            ("20260318001334:00761:t059:r1:c4", ["파생상품부채(전환상환우선주-투자자 상환전환권)", "수준 3", "내재파생상품",
                                                 "이항모형,위험조정할인율모형"], "당기말", "(4) 가치평가기법과 투입변수"),
            ("20260624000063:00761:t116:r2:c2", ["금융부채", "전환사채 및 전환상환우선주"], "당기말", "1) 금융자산 및 부채의 장부금액"),
            ("20260319000854:00761:t057:r2:c2", ["파생상품자산", "제10회 전환사채: 발행자의 매도청구권"], "당기말",
             "① 당기말 현재 상기 전환사채와 관련하여 인식하고 있는 파생금융자산ㆍ부채의 내역은 다음과 같습니다."),
            ("20260403003860:00760:t059:r8:c1", ["전환사채(발행) 매도청구권"], "공정가치", "금융상품 공정가치"),
        )
        for cid, path, col, tt in real:
            c = _pcell(path[-1], path=path, col=col, tt=tt, nt="")
            self.assertFalse(_non_balance_cell(c), cid)
        # 진짜 변동 행은 그대로 변동이다
        for label in ("전환사채 상환", "전환사채 전환", "전환사채 상환권 행사", "전환상환우선주 전환", "상환전환우선주 발행"):
            self.assertTrue(_non_balance_cell(_pcell(label, tt="파생상품부채의 변동")), label)
        # 금액 필드: t116 칸은 face 근거로 not_balance 가 아니다
        cid = "20260624000063:00761:t116:r2:c2"
        c = _pcell("전환사채 및 전환상환우선주", path=["금융부채", "전환사채 및 전환상환우선주"], tt="1) 금융자산 및 부채의 장부금액",
                   value=947128)
        out, reasons = validate(_rec5("face", "current_end", 947128, [cid], series_key=""), _inp9b({cid: c}))
        self.assertNotIn("not_balance:balance.current_end.face", reasons)

    def test_prompt_i6(self):
        self.assertEqual(PROMPT_VERSION, "i6")
        user = build_messages(_inp())[1]["content"]
        for phrase in ("발행회사에 대하여", "행사할 수 있는 권리", "매매목적으로 보유", "당사가 발행한", "부채 칸도",
                       "종속회사", "전환가치", "발행자의 매도청구권", "전환상환우선주-투자자 상환전환권",
                       "당사가 발행한 전환사채 및 관련 내재파생상품", "(주)에이투지엔터테인먼트"):
            self.assertIn(phrase, user)

class PromptI5Test(unittest.TestCase):
    def test_i5_version_and_rules(self):
        self.assertEqual(PROMPT_VERSION, "i6")  # i6 가 i5 문구를 그대로 담는다
        user = build_messages(_inp())[1]["content"]
        for phrase in ("계약상현금흐름", "합계 열", "취득일 현재", "발행조건", "자본금", "상각후원가측정금융부채",
                       "신주인수권부사채", "no_separation_ref", "분리하여", "자본부분", "당기손익-공정가치 측정 금융부채로 지정",
                       "비분리형", "상각후원가"):
            self.assertIn(phrase, user)


if __name__ == "__main__":
    unittest.main()
