"""2층 ② 사채·우선주 기록 — 프롬프트(i5)·호출·캐시·공시 단위 실행(스펙 §5.3, 브리프 T9).

LLM 은 주입된 `complete_json(messages, schema) -> dict` 로 부른다(백엔드 이미지 안에서는
scripts/ledger_instruments_in_backend.py 가 luna LLMClient 를 감싸 넘긴다). 표준 라이브러리와
instrument_input·instrument_validate 만 쓴다(1층 tables.py·dart-agent 를 import 하지 않는다).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

from .instrument_input import build_input, input_scopes, needs_instruments
from .instrument_validate import validate

# i1 → i2: 2026-10-11 100건 측정(근거 통과 43.7%) 뒤 개정, 보고서 T9 측정 1회.
# i2 → i3: fix round 4 — 검증기(R14·R15·기간 검사)와 맞춤. i2 가 가르치던 근거 중 검증기가 거부하는 것(파생상품부채 제목 표의
# 회차 행, 열 머리글 없는 「기말」 행, 행사가격 조정 표의 내재파생상품 행, 「전환권을 금융부채로 분류」 문장)을 지웠다.
# i3 → i4: fix round 5 — 기간 모르는 칸(당기말·현재 잔액 표, current_end 만), 회차 다른 칸(series_mismatch), 비잔액 표
# (만기·유동성·신용위험·이연법인세·상계·담보·거래상대방), 두 쪽 표기(ㆍ·괄호·붙여 쓰기), equity_conflict 를 검증기 그대로 적음.
# i4 → i5: T9b — 만기 구간·계약상현금흐름 열(만기 표의 합계 열 포함), 기간 모르는 칸 제외 표(취득일 현재·발행조건·자본금·주식·
# 전기말 현재), 범주 행렬의 다른 범주 열, 회차의 상품 종류, embedded_separation 근거 규칙(no_separation_ref)을 검증기 그대로 적음.
# i5 → i6: T9b fix round 4 — 분리 문장은 발행자 쪽만(보유자 낱말 문장 제외, 보유자 공시면 「당사가 발행한」), 투자 대상 이름·
# 보유자 제목은 부채 칸도 막음, 상품 뒤 변동 낱말 규칙의 예외(전환가치·발행자·(발행)·상환전환권·전환상환우선주)를 검증기 그대로 적음.
# i6 → i7: T9b fix round 5 — 분리 문장은 종류를 말해야(일반 옵션 낱말만이면 어느 종류도 아님, 바로 앞 문장 예외), 자본 분리
# 마디, 「분리하여 공시하지 않」, 남이 발행한 상품(「X가 발행한」·종속기업·관계기업), option_class 근거의 보유자 가드,
# 보유자 낱말 「취득」 전부(지배력을 취득 포함)를 검증기 그대로 적음.
# i7 → i8: T9c — option_class 는 R15 옵션 낱말 지도로만 종류에 묶음(일반 풋·콜·상환권 아님, 이어 쓰기·입력 단일 종류),
# 종류 낱말은 동사가 든 마디와 바로 앞 주어만, 증권 보유자 낱말, 보유자 공시의 「발행회사」, 제목이 붙은 「…당사가 발행한」,
# 제3자 지정 콜 문장을 검증기 그대로 적음.
PROMPT_VERSION = "i8"

CompleteJson = Callable[[list, dict], dict]

_AMOUNTS = {"face": None, "host_carrying": None, "derivative_liability": None,
            "derivative_asset": None, "equity_component": None}

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["instruments"],
    "properties": {"instruments": {"type": "array", "items": {
        "type": "object",
        "required": ["instrument", "series_key", "kind", "balance", "movement", "option_class",
                     "embedded_separation", "separated_targets", "evidence"],
        "properties": {
            "instrument": {"type": "string"},
            "series_key": {"type": "string"},
            "kind": {"enum": ["CB", "BW", "EB", "RCPS"]},
            "balance": {"type": "object", "properties": {
                p: {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in _AMOUNTS}}
                for p in ("current_end", "prior_end")}},
            "movement": {"type": "object", "properties": {p: {"type": "array", "items": {"enum": [
                "issued", "converted", "redeemed", "matured", "extinguished", "modified", "none"]}}
                for p in ("current", "prior")}},
            "option_class": {"enum": ["equity", "derivative_liability", "unconfirmed"]},
            "embedded_separation": {"enum": ["separated", "not_separated", "whole_fvpl", "unconfirmed"]},
            "separated_targets": {"type": "array", "items": {"enum": ["conversion", "redemption"]}},
            "evidence": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        }}}},
}

_SYSTEM_I8 = (
    "너는 감사보고서 주석에서 전환사채·신주인수권부사채·교환사채·상환전환우선주를 회차별로 기록한다. "
    "표 칸 id와 문장 id로만 근거를 댄다. 보이지 않는 값은 null 또는 unconfirmed로 둔다. "
    "사채의 부채요소(주계약)와 전환권 파생상품부채는 다른 칸이다. option_class 는 전환권·신주인수권·교환권의 분류다. "
    "JSON 객체 하나만 답한다."
)

_RULES_I8 = """규칙(근거는 코드가 아래 규칙 그대로 확인한다. 규칙에 맞지 않는 근거를 댄 값은 버려진다):
1. 회차(상품)마다 한 줄. kind 는 CB(전환사채)·BW(신주인수권부사채)·EB(교환사채)·RCPS(상환전환우선주) 중 하나.
   series_key 는 「제3회」 같은 회차 번호와 종류로 만든다(예: "CB-3"). 회차 번호가 보이지 않으면 series_key 는
   빈 문자열 "" 로 두고, 발행일·액면으로 묶고, 못 묶으면 따로 둔다. 같은 회차의 당기말·전기말 잔액은 한 줄에 담는다.
2. 금액은 칸에 인쇄된 숫자 그대로 적는다. 단위를 바꾸지 않는다: 표 단위가 천원이면 천원 숫자를 그대로 쓴다
   (「(단위: 천원)」 표의 1,499,943 → 1499943. 1499943000 이 아니다). 쉼표는 빼고, 괄호 (1,234) 는 -1234.
3. 기간: 머리글 줄의 [current_end]·[prior_end] 는 그 열의 기간이다.
   - balance.current_end 금액은 [current_end] 열 칸에서, balance.prior_end 금액은 [prior_end] 열 칸에서 댄다.
   - [current_period]·[prior_period] 열(「당기」·「전기」) 칸은 그 열 머리글이 장부금액·잔액·기말을 말하거나, 행이
     「기말」·「장부가액」 행이거나, 표 제목이 「보고기간종료일 현재 …의 내역」 같은 시점 잔액 표(변동·손익·「당기 중」
     표가 아님)일 때만 각각 current_end·prior_end 금액이 된다.
   - [opening](기초) 열, 발행·상환·평가 같은 변동 열은 잔액 근거가 아니다.
   - 기간 표시가 없는 칸(글 표의 v 칸 포함)은 다음을 모두 지킬 때만 balance.current_end 금액으로 쓴다(prior_end 로는 쓰지
     않는다): 그 표의 값 열(금액 자리)이 하나, 표·주석 제목이 「당기말」이나 「현재」를 말함(「전기말 현재」만 말하면 아님),
     제목·주석 제목에 발행시·전환시·행사시·최초·평가·가치평가, 「취득일 현재」·「부여일」 같은 사건 날짜, 발행조건·주요 조건·
     발행내역·발행 내용, 자본금·주식(보통주식·주식선택권)이 없음. 「당기말 현재 …의 주요 발행조건」·「…발행내역」 글 표의
     권면총액·발행가액·액면가액, 「취득일 현재 이전대가」 표, 자본금 표, 「가. 전환시」·「나. 발행시」 같은 평가 표의 칸으로는
     잔액을 대지 않는다(그 금액은 null).
   - 잔액 표가 아닌 표의 칸은 어느 금액의 근거도 아니다(제목에 「현재」가 있어도): 표·주석 제목이 만기(만기분석·잔존계약만기)·
     유동성(유동성 위험)·신용위험·신용건전성·최대노출·이연법인세·상계·담보·거래상대방(집중도)을 말하는 표, 머리글이나 행이
     현금유입·현금유출·상계·담보·신용보강을 말하는 칸, 열이 거래상대방 종류(개인·기업·금융기관·정부 …)인 칸.
   - 만기 구간 열(「3개월 미만」·「1년 이내」·「1년~5년」·「5년 초과」)과 「계약상현금흐름」·「잔존계약만기」 열의 칸은 어느 금액
     (face·host_carrying·equity_component·derivative_*)의 근거도 아니다. 그런 열이 하나라도 있는 표에서는 「합계 열」도
     할인하지 않은 현금흐름 합이라 근거가 아니다. 그 표에서는 「장부금액」 열 칸만 쓸 수 있고, 없으면 다른 표(장부금액·
     공정가치 표, 범주별 금융상품 표)의 장부금액 칸을 댄다.
   - 회차: 근거 칸의 행 이름이나 위 묶음 행에 회차(「제8회」·「8회차」·「Series A」)가 있으면 그 회차가 이 줄의
     series_key 회차와 같아야 한다. 회차가 다르거나 series_key 가 "" 인데 칸에 회차가 붙어 있으면 그 금액은 버려진다.
     상품 종류도 같아야 한다: 칸의 행 이름·묶음 행·열 머리글이 상품 종류(전환사채·신주인수권부사채·교환사채·우선주)를
     말하면 이 줄의 kind 와 같아야 한다(CB-3 줄에 「제3회 신주인수권부사채」 칸, CB 줄에 「상환전환우선주부채」 칸은 버려진다).
     종류를 말하지 않는 칸(「제3회」·「전환권조정」)은 회차 번호만 본다.
4. null 이 아닌 필드는 모두 evidence 에 근거 id 를 댄다(금액마다, option_class·embedded_separation 도).
   evidence 키는 필드 경로다: "balance.current_end.face", "balance.prior_end.equity_component" 처럼.
   댈 근거가 없으면 금액은 null, 분류는 "unconfirmed" 로 둔다.
5. 근거 id 는 입력에 인쇄된 그대로만 쓴다: 칸 "<표 id>:r<행>:c<열>", 글 표의 금액 "<표 id>:r<행>:v<번호>",
   문장 "s<번호>"(예: "s494" — "s:494"·"<접수번호>:s494" 아님), 각주 "f:<표 id>:<번호>".
   표 id 만("<표 id>")이나 r0, 입력에 없는 열 번호는 쓰지 않는다. 만들어 내지 않는다.
6. 필드 뜻:
   - face: 액면(발행가액) 잔액. 그 금액이 적힌 칸(잔액 표의 「액면가액」·「전환사채」 행, 또는 규칙 3을 지키는 글 표 v 칸).
   - host_carrying: 사채의 부채요소(주계약) 장부금액. 인쇄된 장부금액·합계 칸 하나를 댄다. 그런 칸이 없으면
     같은 표·같은 열의 구성 칸(액면·원금·발행가액·사채할인발행차금·전환권조정·신주인수권조정·교환권조정·상환할증금·
     현재가치할인차금)을 모두 대고 인쇄된 값(차감은 음수)의 합을 적는다. 구성 칸은 모두 같은 회차여야 한다(행 이름이나
     위 묶음 행의 「제7회」·「제8회」가 섞이면 안 된다). 다른 열·다른 표·글 표 v 칸을 섞지 않는다. 우선주부채(주계약)도
     여기. 상품 전체를 당기손익-공정가치로 측정하면 그 전체 장부금액도 여기에 둔다.
   - derivative_liability: 옵션(내재파생상품)이 파생상품부채로 따로 잡힌 금액. 칸은 파생상품 칸이고 쪽(부채)이 명시돼야 한다:
     · 행 이름에 파생 낱말이 있고 부채 쪽이 보이는 행: 「파생상품부채」·「전환사채파생부채(전환권)」·「파생상품부채(전환권 및
       조기상환권)」, 또는 표·주석 제목이 「파생상품부채」만 말하는 표(「파생상품부채의 내역」)의 「…내재파생상품」 행.
     · 옵션 이름 행(전환권·조기상환청구권·매도청구권·콜옵션 …): 열 머리글이나 표·주석 제목이 「파생상품부채」를 말할 때만.
     · 「기말」 행·회차 행·상품 이름 행: 열 머리글이 「파생상품부채」를 말할 때만(제목만으로는 안 된다).
     아닌 것: 제목·열 머리글·행 이름이 두 쪽을 다 말하는 표(어떤 표기든: 「파생상품자산 및 부채」·「파생상품 자산 및 부채」·
     「파생상품자산과 파생상품부채」·「파생상품자산ㆍ부채」·「파생상품자산부채」·「파생금융자산(부채)」·「파생상품부채(자산)」)
     — 쪽을 모르므로 null. 쪽을 말하지 않는 행(「…내재파생상품」·「전환권 및 조기상환권」)은 제목이나 열 머리글이
     파생상품부채를 명시하지 않으면 근거가 아니다(예: 「…부채로 분류된 금융부채의 장부금액」 같은 행사가격 조정 공시 표의
     그런 행. 그 표에서도 「파생상품부채」처럼 행 이름이 쪽을 말하면 근거가 된다). 공정가치 수준 열(「수준1」·「수준 2」·「Level 3」·「공정가치 수준/합계」), 회계오류 수정·재작성 열
     (「조정금액」·「수정금액」·「재작성금액」), 현금흐름표 줄(「파생상품자산의 정산」), 손익·변동·민감도·만기분석 칸
     (평가이익·손실, 발행·행사·상환, 1% 증가, 1년 이내; 상품 이름 뒤에 변동 낱말이 오는 「전환사채 상환」·「전환사채 전환」·
     「상환전환우선주 발행」 같은 변동 행 — 상환·전환·행사·발행·증가·감소·취득·처분·대체 — 은 어느 금액의 근거도 아니다. 변동 행이 아닌 것: 옵션·상품 복합어와 표기 — 「제3회 전환사채 전환권(전환가치)」, 「파생상품부채(전환상환우선주-투자자 상환전환권)」, 「전환사채 및 전환상환우선주」, 「제10회 전환사채: 발행자의 매도청구권」, 「전환사채(발행) 매도청구권」), 「전환권조정」·「전환권가치」 같은 차감 칸(괄호 음수),
     「사채할인발행차금」·「상환할증금」·「합계」 행, 옵션 낱말 없이 상품 이름만 있는 행의 금액(상품 전체 장부금액),
     「전환사채 및 조기상환권」처럼 상품과 옵션을 함께 적은 행, 「비파생금융부채」·「비파생상품」처럼 비파생을 말하는
     행·열(파생상품 칸이 아니다). 금융상품 범주 표(열이 상각후원가측정금융부채·당기손익-공정가치측정금융부채·기타포괄손익 …)의
     「파생상품부채」 행은 다른 범주 열(「상각후원가측정금융부채」·「상각후원가측정금융상품」·「기타포괄손익-공정가치」 열의 0 등)
     칸이 근거가 아니다. 그 표에서는 그 행의 합계(「장부금액/합계」) 열이나 위험회피 열 칸을 댄다(「당기손익」 낱말이 든 열은
     손익 열로 읽혀 근거가 되지 않는다).
   - derivative_asset: 같은 규칙으로 쪽이 자산인 칸(「파생상품자산」 행, 제목이 「파생상품자산의 내역」인 표의 매도청구권·
     콜옵션 행). 이름(매도청구권·콜옵션)만으로 자산이라고 정하지 않는다.
   - equity_component: 전환권대가·신주인수권대가 등 자본요소(「전환사채의 자본요소」·「전환권대가」 행).
   - face·host_carrying·equity_component 는 파생상품 행(「파생상품부채」·「…파생…」 행)의 칸으로 대지 않는다
     (범주 표의 「파생상품부채」 × 상각후원가 열 0 같은 칸 — derivative_row 로 버려진다).
7. option_class: 전환권·신주인수권·교환권의 분류만 뜻한다. 조기상환청구권·매도청구권·풋옵션·콜옵션 근거는 option_class 의
   근거가 아니다(그런 행·문장은 embedded_separation 과 derivative_liability·derivative_asset 금액에만 쓴다).
   - "equity": 전환권대가·신주인수권대가 등 자본요소 칸이나 전환권(의 가치)을 자본으로 계상했다는 문장.
   - "derivative_liability": 아래 근거가 있을 때만.
     (가) 규칙 6의 derivative_liability 칸이면서 그 행 이름이나 열 머리글이 전환권·신주인수권·교환권을 말하는 칸
          (「파생상품부채(전환권 및 조기상환권)」, 「파생상품부채의 내역」 표의 「전환권」·「제3회 전환사채 전환권 등」 행).
          칸의 옵션 낱말이 이 줄의 kind 로 가야 한다: 전환권 → CB·RCPS, 신주인수권 → BW, 교환권 → EB
          (「파생상품부채(전환권 및 조기상환권)」은 CB·RCPS 줄만, 「신주인수권」 행은 BW 줄만). 칸의 행 이름·열 머리글이 상품 종류를
          말하면 kind 와 같아야 한다(규칙 3).
          일반 「파생상품부채」 행, 「…내재파생상품」 행, 「조기상환청구권」 행은 금액 근거는 되지만 option_class 근거는 아니다.
     (나) 전환권·신주인수권·교환권(이 든 옵션 목록)이 주어·목적어로 바로 조사를 받고 「파생상품부채로」·「파생상품으로」
          분류·인식·계상·회계처리했다는 문장: 「전환사채의 전환권 및 조기상환권은 파생상품부채로 인식하였습니다」, 「따라서
          전환권을 부채로 분류하고 파생상품으로 회계처리」(앞 문장을 이어 쓸 때, 아래), 또는 그 목록을 담은 내재파생상품이
          주어인 문장: 「전환사채에 내재된 전환권, 조기상환청구권 및 매도청구권이 포함된 내재파생상품은 … 내재파생상품으로
          회계처리」, 「내재파생상품(조기상환청구권, 전환권 등)을 … 파생상품부채로 인식」.
          (나) 문장의 종류 묶기(R15 옵션 낱말 지도): 그 문장의 옵션 낱말이 이 줄의 kind 로 가야 한다(전환권 → CB·RCPS,
          신주인수권 → BW, 교환권 → EB). 상환권·조기상환·콜·풋 같은 일반 옵션 낱말은 option_class 를 어느 종류에도 묶지 않는다
          (「해당되는 전환권과 상환권 등을 부채로 분류하고 파생상품으로 회계처리」는 BW·EB 줄의 근거가 아니다). 그리고 다음 중 하나:
          · 문장이 상품 종류 낱말을 말하면 그 종류는 규칙 8의 「동사가 든 마디」 규칙으로 읽고 kind 가 그 안에 있어야 한다.
          · 문장에 종류 낱말이 없으면 바로 앞 문장(번호가 하나 앞)이 이 종류 하나만 말하는 조건 아닌 「…다.」 문장이고 이
            문장이 새 항목 표시 없이 그것을 이어 쓸 때(규칙 8의 이어 쓰기와 같다): 「(17) 상환전환우선주당사가 발행한
            상환전환우선주는 …」 다음의 「따라서 전환권을 부채로 분류하고 …」는 RCPS 줄만의 근거이고 CB 줄의 근거가 아니다.
            앞 문장이 두 종류(「…발행한 전환사채와 상환전환우선주는 …」)를 말하면 이어 쓰지 않는다.
          · 그것도 아니면, 입력 전체(문장, 칸의 행 이름·열 머리글·표 제목·주석 제목)에 그 옵션 낱말이 가리키는 종류가 하나뿐일
            때만 그 종류다(「전환권과 조기상환권은 … 하나의 내재파생상품부채로 회계처리」는 입력에 전환사채만 있으면 CB, 전환사채와
            우선주가 함께 있으면 어느 줄의 근거도 아니다). 이때 바로 앞 문장이 조건·보유자·남의 상품 문장이거나 이 종류를 말하지
            않는 다른 종류 문장이면 아니다.
          · 문장에 「제5회」 같은 회차가 있으면(이어 쓰면 앞 문장 것도) 이 줄의 회차와 같아야 한다.
     근거가 아닌 문장: 「조기상환청구권 및 매도청구권은 … 파생상품으로 회계처리」(전환권이 없음), 「내재파생상품은 …」만 있는
     문장, 「전환권을 금융부채로 분류」·「유동부채로 분류」·「부채요소는 금융부채」(파생상품부채·파생상품이라고 하지 않음),
     「…경우 부채로 분류한다」 같은 조건부·부정 문장, 전환권을 자본(기타자본잉여금)으로 옮겼다는 문장, 「전환권 및 상환권이
     없는 …」, 상품 전체를 당기손익-공정가치로 지정했다는 문장, 종류를 묶지 못하는 문장(위 종류 묶기).
   - 사채 부채요소·전환권조정 칸만 있으면 derivative_liability 가 아니다. 근거가 없으면 "unconfirmed".
   - equity_conflict: 입력에 그 종류의 자본요소 칸(CB·RCPS 전환권대가·전환권의 자본요소, BW 신주인수권대가, EB 교환권대가;
     0 이 아니고 파생·부채 낱말이 없는 행)이 이 줄과 같은 회차이거나 회차가 붙어 있지 않으면, 다른 근거가 있어도
     "derivative_liability" 는 확인되지 않는다(그 전환권은 자본이었으므로). 그때는 근거에 맞게 "equity" 나 "unconfirmed".
   - 「파생상품」이라고 분류한 문장이라도 같은 문장이 자산(금융자산·파생상품자산)을 말하면 근거가 아니다(취득한
     전환사채의 전환권처럼 보유자 쪽 문장).
   - option_class 근거도 규칙 8의 보유자 규칙을 똑같이 지켜야 한다. (나) 문장: 보유자 낱말(규칙 8 목록)이 있으면 아니다
     (「(*2) 기타대출채권으로 분류된 전환사채와 관련된 금액으로 전환사채에 포함된 전환권 및 조기상환청구권을 별도로 분리하여
     내재파생상품으로 인식한 후, 취득시점의 …」), 남이 발행한 상품을 말하면 아니다(규칙 8), 보유자 공시에서는 자기 회사를
     발행자로 적어야 한다(「당사가 발행한 전환사채의 전환권은 파생상품부채로 분류」). 종류는 위 (나) 종류 묶기로만 정한다.
     (가) 칸: 투자 대상 이름(「B사 전환사채의 전환권 등」, 보유자 공시에서는 「발행회사 전환사채」도)·회사 이름(「(주)…」·「㈜」·
     주식회사) 칸은 아니고, 보유자 제목 표(「당사가 보유하고 있는 파생상품의 내역」)나
     보유자 공시의 칸은 입력에 이 종류의 발행자 문장이 있거나 그 표 제목이 「당사가 발행한 …」일 때만 근거가 된다.
8. embedded_separation: 근거의 뜻까지 코드가 확인한다. 맞지 않으면 "unconfirmed"(no_separation_ref)로 버려진다.
   근거 문장은 이 줄의 상품 종류 낱말(전환사채·신주인수권부사채·교환사채·상환전환우선주·전환우선주)을 말해야 하고 kind 와
   같아야 한다. 종류 낱말 없이 옵션 낱말(매도청구권·콜옵션·풋옵션·조기상환청구권, 전환권·신주인수권·교환권)만 있는 문장은
   어느 종류의 근거도 아니다. 예외 하나: 바로 앞 문장(번호가 하나 앞인 문장, s777 이면 s776)이 이 종류 하나만 말하는
   「…다.」 문장이고, 이 문장이 「(*2)」·「(주2)」·「(1)」·「가.」 같은 새 항목 표시 없이 그것을 이어 쓸 때(앞 문장의 회차도
   본다; 앞 문장이 보유자·남의 상품 문장이거나 「…경우」·「…때에는」 조건 문장이면 아니다). 예: s776 「회사가 발행한 복합금융상품은 … 신주인수권부사채와
   교환사채입니다」는 두 종류를 말하므로 s777 「매도청구권(콜옵션)은 …」은 어느 줄의 근거도 아니다.
   종류 낱말은 동사가 든 마디에서만 읽는다(분리 동사 「분리하여」·「분리한」 …, 또는 옵션 목록 → 「파생상품(부채)로
   분류·인식」; 마디는 「,」·「…고 」·「…며 」·마침표로 나누고, 「전환권, 조기상환청구권 및 매도청구권」·「전환사채,
   신주인수권부사채」처럼 옵션·상품 낱말을 나열한 쉼표로는 나누지 않는다):
   · 동사가 든 마디가 종류 낱말을 말하면 그 종류만이다. 「당기 중 전환우선주부채등은 모두 보통주로 전환되었으며, 당기말 현재
     전환사채 관련 조기상환권은 … 주계약과 분리하여 하나의 내재파생상품으로 인식」은 CB 근거이고 RCPS 근거가 아니다.
   · 동사가 든 마디가 종류를 말하지 않고 동사 앞에 제 주제어(옵션·상품·회사 + 은/는)가 있으면 아무 종류의 근거도 아니다:
     「상환전환우선주부채는 … 주계약과 조기상환권 및 전환권으로 구분되며, 주계약은 상각후원가로 평가되고, 조기상환권 및
     전환권은 파생상품으로 인식」, 「주계약 부채요소는 상환전환우선주부채로 계상되어 있으며, 전환권 및 조기상환권은
     파생상품부채로 계상」. 앞 마디의 목적어(「당사는 상환전환우선주를 발행하였으며, 전환권을 분리하여 …」)도 묶지 않는다.
   · 동사가 든 마디가 종류도 제 주제어도 없으면 동사 바로 앞의 주어(앞 마디의 마지막 「…은/는/이/가」 구)의 종류다:
     「상기 전환상환우선주가 … 인식하였으며, 전환권 및 조기상환권을 내재파생금융부채로 별도로 분리하고」 → RCPS,
     「전환상환우선주에 내재된 전환권 및 조기상환권은 …하였으며, 전환권 및 조기상환권이 … 분리하여」 → RCPS.
   · 1층이 각주에 붙여 온 뒷 문장(「…분리되었습니다.(5) 당기 및 전기의 전환상환우선주 변동내역은 …」)의 종류는 묶지 않는다.
   이 마디 규칙은 separated 근거 문장과 option_class (나) 문장에 쓴다. 이 규칙은 separated·
   not_separated·whole_fvpl 근거 문장 모두에 적용된다. 근거 칸은 옵션 낱말로는 안 되고 상품 종류 낱말이 있어야 한다(아래 (가)). 「당사는 내재파생상품을 주계약과 분리하여 별도로 회계처리」처럼 상품을 말하지 않는 문장은 근거가 아니다.
   문장에 「제5회」 같은 회차가 있으면 이 줄의 회차와 같아야 한다. 「…경우」·「…때에는」 조건 문장, 보유자 쪽 문장
   (취득한 상품·투자한 상품, 「금융자산」을 말하는 문장)은 근거가 아니다.
   분리 문장은 회사 자신이 발행한 상품(발행자 쪽)의 것이어야 한다. 다음 보유자 낱말이 든 문장은 separated 근거가 아니다:
   「발행회사에 대하여」, 「행사할 수 있는 권리」, 보유(보유하고 있는·보유한·보유 중·보유목적),
   취득(취득시점·취득한·취득하여·지배력을 취득 등 「취득」이 든 모든 말),
   「대출채권」(「기타대출채권으로 분류된 전환사채」), 「매매목적」, 투자(「투자자」는 괜찮다), 「금융자산」(「…금융자산으로 분류」,
   당기손익-공정가치측정금융자산), 증권 보유 낱말 — 매도가능증권·단기매매증권·만기보유증권·유가증권(「유가증권시장」은 아님)·
   당기손익인식금융자산·매도가능금융자산·기타포괄손익-공정가치측정금융자산(「(*) 매도가능증권으로 계상된 전환사채의
   내재파생상품인 전환권 및 조기상환청구권을 분리하여 파생상품자산으로 인식」은 보유자 문장이다).
   「사채권자가 보유한 조기상환청구권」처럼 보유의 주어가 사채권자·투자자이면 괜찮다.
   보유자 공시: 입력에 「…전환사채 및 신주인수권부사채를 매매목적으로 보유하고 있습니다」·「보유하고 있는 전환사채」·「취득한
   전환사채」·「…금융자산으로 분류」·「매도가능증권으로 계상된 전환사채」 같은 보유자 진술 문장, 「B사 전환사채의 전환권 등」 같은 투자 대상 이름 칸, 또는 제목이
   「보유하고 있는 전환사채」처럼 보유와 상품을 함께 말하는 표가 있으면, 분리 문장은 자기 회사를 발행자로 적어야 한다
   (「당사가 발행한」·「연결회사가 발행한 상환전환우선주에 내재된 전환권 및 조기상환권은 분리하여 파생상품부채로 인식」·
   「회사는 … 발행하였」). 그렇지 않은 분리 문장은 그 공시에서 근거가 아니다. 보유자 공시에서는 「발행회사」가 자기 회사가
   아니다(투자 대상 회사일 수 있다): 「발행회사가 발행한 …」 문장, 「발행회사(의) 전환사채」 칸·문장은 그 공시에서 근거가 아니다.
   남이 발행한 상품: 「X가/이 발행한」에서 X 가 자기 회사(당사·회사·동사·연결회사·지배회사·지배기업·연결실체·연결기업·
   모회사·본사, 보유자 공시가 아니면 발행회사)가 아닌 문장. 주석 제목이 주어에 붙어 온 「(17) 상환전환우선주당사가 발행한」·
   「(14) 전환사채당사가 발행한」·「3.16 전환사채회사가 발행한」·「2.9 복합금융상품당사가 발행한」은 자기 회사(당사·회사)다(「(*2) 상기 이외에 종속기업인 오스틴제약(주)가 발행한 전환우선주와 관련하여 인수자에게
   풋옵션(상환보증)을 제공 … 파생금융부채로 계상」 — 종속기업 상품에 대한 보증), 종속기업·관계기업·공동기업·투자회사·
   자회사의 상품·발행을 말하는 문장, 「B사(의) 전환사채」 문장은 separated·not_separated·option_class 근거가 아니다.
   - "separated"(separated_targets 에 분리한 것: "conversion"·"redemption"): 다음 중 하나.
     (가) 그 상품의 분리 파생상품 금액 칸: 기본은 규칙 6의 derivative_liability 칸(부채 쪽 잔액 칸, 0 아님, 회차·종류
          맞음)이고, 행 이름·열 머리글(없으면 표 끝 문장·주석 제목)에 상품 종류 낱말(전환사채·신주인수권부사채·교환사채·
          상환전환우선주)이 있어야 한다(「제2회 전환사채 조기상환청구권」, 「전환사채 내역」 표의 「파생상품부채」 행).
          매도청구권·콜옵션·풋옵션·조기상환청구권 같은 옵션 낱말만 있는 칸, 범주 표의 일반 「파생상품부채」 행은 아니다.
          부채 칸도 상품 이름 앞에 투자 대상 회사 이름(「B사 전환사채의 전환권 등」 × 「파생상품부채」, 「종속회사의 전환사채」·
          「자회사 전환사채」 포함)이 있으면 근거가 아니다. 표 제목(앞 문장 포함)·주석 제목이 보유자 쪽(「회사가 보유하고 있는
          파생상품의 내역」·보유한·취득한·투자)을 말하는 표의 부채 칸, 위 보유자 공시의 부채 칸은 입력에 같은 종류(회차)의
          발행자 분리 문장(아래 (나))이 함께 있을 때만 근거가 된다. 보유자 공시의 부채 칸은 그 표 제목이 자기 회사를 발행자로
          적을 때(「당기말 현재 당사가 발행한 전환사채 및 관련 내재파생상품의 세부내역」)도 근거가 된다. 행 이름·열 머리글에
          회사 이름(「(주)에이투지엔터테인먼트 전환우선주 풋옵션」·「㈜」·주식회사·Inc·Ltd)이 있는 칸은 부채·자산 모두 아니다.
          파생상품자산 칸은 자산 칸만으로는 근거가 되지 않는다: 입력에 같은 종류(회차)의 상품 분리 문장(아래 (나), 자기
          회사·부정·조건 아님)이 함께 있어야 하고, 그때에도 다음을 모두 지켜야 한다: 행 이름이나 열 머리글 자체가 이 줄의 상품 종류를 말함
          (「전환사채 매도청구권」 — 제목만으로는 안 됨), 행·열이 제3자(「제3자 지정가능 매도청구권」)·권자(「신주인수권부사채권자의
          신주인수권」 — 보유자)를 말하지 않음, 묶음 행에 회사 이름·투자 층(「(주)…」·「㈜」·「주식회사」·「…투자 합자회사지분」·
          조합·펀드)이 없음, 상품 이름 앞에 투자 대상 회사 이름(「B사 전환사채의 전환권 등」·「에이비씨사의 전환사채」)이 없음
          (당사·회사·동사·연결회사·지배회사·모회사·본사는 자기 회사라 괜찮다; 발행회사는 보유자 공시가 아닐 때만 괜찮다;
          종속회사·자회사는 안 된다), 표 제목(앞 문장 포함)·주석
          제목이 「당사가 보유하고 있는 파생상품」·「보유한」·「취득한」·「투자」처럼 보유자 쪽을 말하지 않음(자산 칸은 발행자
          문장이 있어도 보유자 제목이면 안 된다).
     (나) 그 상품의 분리 문장: 「분리하여 … 파생상품(부채)·공정가치」(「…조기상환청구권은 … 주계약에서 분리하여 별도의
          파생상품으로 회계처리」), 또는 옵션 목록이 주어로 「파생상품(부채)로 분류·인식」(「조기상환권 및 전환권은
          파생상품으로 인식」). 「파생상품자산으로」 잡았다는 문장(제3자 지정 매도청구권 등)만으로는 아니다.
          제3자 지정 콜(제3자 지정·지정권자·발행자가 지정하는 자·제3자에게 매도) 문장은 분리 동사가 든 마디가 부채 쪽(파생상품부채·
          금융부채)을 말하지 않고 파생상품자산을 말하거나 콜(매도청구권·콜옵션)만 말하면 separated 근거가 아니다(「한편, 전환사채에
          포함된 제3자 지정 콜옵션을 별도의 파생상품자산으로 분리하여 인식하였습니다」). 이런 줄은 같은 종류의 부채 쪽 분리
          문장(「당사가 발행한 전환사채의 조기상환청구권은 주계약과 분리하여 파생상품부채로 인식」)이나 부채 쪽 분리 칸(위 (가))을
          근거로 함께 대야 확인된다. 부채 쪽을 함께 말하는 문장(「제3자지정 콜옵션 및 조기상환권에 대해 각각 파생상품자산 및
          주계약과 분리한 후 파생상품부채로 회계처리」), 풋도 함께 분리하는 문장(「조기상환청구권과 발행자(또는 발행자가 지정하는
          제3자) 매도청구권을 주계약으로부터 분리하여 별도의 파생상품으로 분류」)은 근거가 된다. 위의 보유자 낱말
          문장(「…발행회사에 대하여 전환권 … 을 행사할 수 있는 권리는 파생상품자산으로 인식」, 「기타대출채권으로 분류된 전환사채 …
          취득시점의 주계약 …」)은 분리라고 적혀 있어도 아니다.
     아닌 것: 「부채부분과 자본부분(전환권대가)으로 분리」·「부채요소와 자본요소로 분리」(자본 분리는 내재파생상품 분리가 아님;
     분리 동사는 제 마디(「,」·「…고」·「…며」로 나눈 조각)로 읽으므로 「매도청구권(콜옵션)은 … 파생상품자산으로 구분하여
     인식하고 복합금융상품은 부채요소와 자본요소로 분리하여 인식」처럼 다른 마디가 파생을 말해도 아니다),
     「해당 파생상품은 별도로 분리하여 공시하지 않았습니다」·「분리하여 표시하지 않」(공시 방식일 뿐 분리 진술이 아니다 —
     같은 문장의 다른 마디가 분리를 말할 때만 그 마디로 정한다),
     댄 근거 중에 분리하지 않았다는 문장이 섞인 경우(「조기상환청구권은 분리요건을 충족하지 못하여 주계약에 포함」).
   - "not_separated": 다음 중 하나이고, 이 줄에 확인된 derivative_liability·derivative_asset 금액이 없고, 입력에 그 상품의
     분리 파생상품 칸이나 분리 문장이 없을 때만.
     (가) 분리하지 않았다는 문장: 「…풋옵션가치를 분리하여 계상하고 있지 않습니다」, 「…조기상환청구권은 내재파생상품
          분리요건을 충족하지 못하여 주계약에 포함」, 「…분리하지 않았습니다」.
     (나) 그 상품(부채요소)을 상각후원가로 측정한다는 문장(파생·공정가치·분리·옵션 낱말 없이): 「당사는 전환사채의
          부채요소를 상각후원가로 측정하고 있습니다」. 또는 그 상품 행의 상각후원가 칸(「상환전환우선주부채」 ×
          「상각후원가측정금융부채」, 0 아님). 「금융부채는 상각후원가로 측정」처럼 상품을 말하지 않으면 아니다.
     (다) 상품 전체를 당기손익-공정가치로 지정한 문장·칸(아래 whole_fvpl 근거).
     「비분리형 신주인수권부사채」는 신주인수권을 떼어 팔 수 없다는 상품 이름일 뿐 분리하지 않았다는 근거가 아니다.
   - "whole_fvpl": 「…전환상환우선주 전체를 당기손익-공정가치 측정 금융부채로 지정」 같은 문장(「지정」이 아니면 같은 문장에
     「전체」·「하나의」·「분리하지 않고」가 있어야 한다), 또는 당기손익-공정가치측정금융부채 열(묶음 행)의 그 상품 행 칸(0 아님).
     「금융부채」가 아닌 「당기손익-공정가치 측정항목으로 지정」, 「…자본요소를 만족하지 못할 경우 … 지정」 같은 조건 문장,
     「전환권은 당기손익-공정가치측정금융부채로 분류」(옵션만 말하고 상품 전체가 아님), 남이 발행한 상품을 당기손익-공정가치측정금융자산으로
     분류한 문장·칸은 아니다. 확인된 파생상품 금액이 있거나 입력에 그 상품의 분리 칸·문장이 있으면 아니다.
   - 위에 맞는 근거가 없으면 "unconfirmed".
9. movement: 그 기간에 있었던 일 — issued(발행)·converted(전환)·redeemed(상환)·matured(만기)·
   extinguished(소멸)·modified(조건 변경)·none(변동 없음). 모르면 빈 목록.
10. 대상 상품이 없으면 {"instruments": []}."""

_EXAMPLE_I8 = """예시 입력 1(일동홀딩스, 천원 표):
[T7] (consolidated) / 제목: 당기말과 전기말 현재 전환사채의 세부내역 / 단위: (단위: 천원)
머리글 | c1=당기말 [current_end] | c2=전기말 [prior_end]
r2 전환사채의 부채요소
r3 전환사채 | c1=1,000,000 | c2=82,915,000
r4 전환사채상환할증금 | c1=- | c2=7,511,222
r5 전환권조정 | c1=(103,911) | c2=(30,905,828)
r6 합 계 | c1=896,089 | c2=59,520,394
r7 전환사채의 자본요소 | c1=121,352 | c2=519,263
예시 출력 1:
{"instruments": [{"instrument": "전환사채", "series_key": "", "kind": "CB",
 "balance": {"current_end": {"face": 1000000, "host_carrying": 896089, "derivative_liability": null,
                             "derivative_asset": null, "equity_component": 121352},
             "prior_end": {"face": 82915000, "host_carrying": 59520394, "derivative_liability": null,
                           "derivative_asset": null, "equity_component": 519263}},
 "movement": {"current": [], "prior": []},
 "option_class": "equity", "embedded_separation": "unconfirmed", "separated_targets": [],
 "evidence": {"option_class": ["T7:r7:c1"],
              "balance.current_end.face": ["T7:r3:c1"], "balance.current_end.host_carrying": ["T7:r6:c1"],
              "balance.current_end.equity_component": ["T7:r7:c1"],
              "balance.prior_end.face": ["T7:r3:c2"], "balance.prior_end.host_carrying": ["T7:r6:c2"],
              "balance.prior_end.equity_component": ["T7:r7:c2"]}}]}
(표에 회차 번호가 없으므로 series_key 는 "". 자본요소 행이 있으므로 option_class 는 equity.
 파생상품부채 칸이 없으므로 derivative_liability 는 null. 천원 숫자를 그대로, 당기말 열은 current_end, 전기말 열은 prior_end.)

예시 입력 2(파생상품부채 표, 천원):
[T9] (separate) / 주석: 파생상품 / 제목: 당기말과 전기말 현재 파생상품부채의 내역은 다음과 같습니다. / 단위: (단위: 천원)
머리글 | c1=당기말 [current_end] | c2=전기말 [prior_end]
r2 제7회 상환전환우선주 전환권 및 조기상환권 | c1=2,500,419 | c2=4,084,843
s12: 상환전환우선주의 전환권 및 조기상환권은 자본요소에 해당하지 않으므로 주계약에서 분리하여 파생상품부채로 인식하였습니다.
예시 출력 2(그 회차 줄의 일부):
 "series_key": "RCPS-7", "kind": "RCPS", "balance": {"current_end": {"derivative_liability": 2500419, ...},
 "prior_end": {"derivative_liability": 4084843, ...}}, "option_class": "derivative_liability",
 "embedded_separation": "separated", "separated_targets": ["conversion", "redemption"],
 "evidence": {"balance.current_end.derivative_liability": ["T9:r2:c1"],
              "balance.prior_end.derivative_liability": ["T9:r2:c2"], "option_class": ["T9:r2:c1", "s12"],
              "embedded_separation": ["s12"]}
(행 이름이 전환권을 말하고 제목이 파생상품부채만 말하므로 금액과 option_class 근거가 된다.)

예시 입력 3(풋·콜만 파생상품, 전환권은 자본):
[T5] (separate) / 주석: 파생상품 / 제목: (1) 당기말 현재 파생상품부채의 내역은 다음과 같습니다. / 단위: (단위: 원)
머리글 | c1=당기말 [current_end]
r1 제2회 전환사채 조기상환청구권 | c1=300,000,000
s20: 전환사채에 내재된 조기상환청구권 및 매도청구권은 내재파생상품의 분리조건을 모두 충족하므로 주계약에서 분리하여 별도의 파생상품으로 회계처리하고 있습니다.
s21: 발행가액에서 부채요소를 차감하고 매도청구권과 조기상환청구권을 가감한 잔여가치는 전환권의 가치에 해당하며, 세효과 차감후 금액을 자본으로 계상하였습니다.
예시 출력 3(그 회차 줄의 일부):
 "series_key": "CB-2", "kind": "CB", "balance": {"current_end": {"derivative_liability": 300000000, ...}},
 "option_class": "equity", "embedded_separation": "separated", "separated_targets": ["redemption"],
 "evidence": {"balance.current_end.derivative_liability": ["T5:r1:c1"], "option_class": ["s21"],
              "embedded_separation": ["s20"]}
(조기상환청구권 행은 파생상품부채 금액의 근거지만 option_class 근거가 아니다. 전환권은 s21 에서 자본이다.
 「파생상품자산 및 부채」처럼 두 쪽을 다 말하는 제목의 표였다면 금액은 null 이다.)"""


_USER_I8 = """{rules}

출력 형식(JSON):
{schema}

{example}

---
입력 표(칸 id = <표 id>:r<행>:c<열>):
{tables_text}

입력 문장(id: 글):
{sentences}"""

PROMPT_I8 = {"version": PROMPT_VERSION, "system": _SYSTEM_I8, "rules": _RULES_I8, "example": _EXAMPLE_I8,
             "user": _USER_I8}


def _example_cell(value, label, *, path=None, col, period, tt, nt=None, multiplier=1, n_value_cols=2):
    return {"raw": f"{value:,}", "value": value, "unit_kind": "krw", "multiplier": multiplier, "period": period,
            "label": label, "label_path": path or [label], "col_header": col, "table_title": tt, "note_title": nt,
            "n_value_cols": n_value_cols, "region_kind": "note"}


def _example_cases() -> list[tuple[dict, dict]]:
    """프롬프트 예시의 (입력 묶음, 기록) — 예시가 검증기를 통과하는지 테스트가 확인한다(예시가 거부되는 근거를 가르치지 않게)."""
    t7 = "당기말과 전기말 현재 전환사채의 세부내역"
    rows7 = {3: ("전환사채", 1000000, 82915000), 5: ("전환권조정", -103911, -30905828), 6: ("합 계", 896089, 59520394),
             7: ("전환사채의 자본요소", 121352, 519263)}
    cells1 = {}
    for r, (label, cur, pri) in rows7.items():
        cells1[f"T7:r{r}:c1"] = _example_cell(cur, label, col="당기말", period="current_end", tt=t7, multiplier=1000)
        cells1[f"T7:r{r}:c2"] = _example_cell(pri, label, col="전기말", period="prior_end", tt=t7, multiplier=1000)
    rec1 = {"instrument": "전환사채", "series_key": "", "kind": "CB",
            "balance": {"current_end": {"face": 1000000, "host_carrying": 896089, "derivative_liability": None,
                                        "derivative_asset": None, "equity_component": 121352},
                        "prior_end": {"face": 82915000, "host_carrying": 59520394, "derivative_liability": None,
                                      "derivative_asset": None, "equity_component": 519263}},
            "movement": {"current": [], "prior": []}, "option_class": "equity", "embedded_separation": "unconfirmed",
            "separated_targets": [],
            "evidence": {"option_class": ["T7:r7:c1"], "balance.current_end.face": ["T7:r3:c1"],
                         "balance.current_end.host_carrying": ["T7:r6:c1"],
                         "balance.current_end.equity_component": ["T7:r7:c1"], "balance.prior_end.face": ["T7:r3:c2"],
                         "balance.prior_end.host_carrying": ["T7:r6:c2"],
                         "balance.prior_end.equity_component": ["T7:r7:c2"]}}
    t9 = "당기말과 전기말 현재 파생상품부채의 내역은 다음과 같습니다."
    lab9 = "제7회 상환전환우선주 전환권 및 조기상환권"
    inp2 = {"rcept_no": "EX2", "tables_text": "", "cells": {
        "T9:r2:c1": _example_cell(2500419, lab9, col="당기말", period="current_end", tt=t9, nt="파생상품", multiplier=1000),
        "T9:r2:c2": _example_cell(4084843, lab9, col="전기말", period="prior_end", tt=t9, nt="파생상품", multiplier=1000)},
        "sentences": {"s12": "상환전환우선주의 전환권 및 조기상환권은 자본요소에 해당하지 않으므로 주계약에서 분리하여 "
                             "파생상품부채로 인식하였습니다."}}
    rec2 = {"instrument": "제7회 상환전환우선주", "series_key": "RCPS-7", "kind": "RCPS",
            "balance": {"current_end": {"derivative_liability": 2500419}, "prior_end": {"derivative_liability": 4084843}},
            "option_class": "derivative_liability", "embedded_separation": "separated",
            "separated_targets": ["conversion", "redemption"],
            "evidence": {"balance.current_end.derivative_liability": ["T9:r2:c1"],
                         "balance.prior_end.derivative_liability": ["T9:r2:c2"], "option_class": ["T9:r2:c1", "s12"],
                         "embedded_separation": ["s12"]}}
    inp3 = {"rcept_no": "EX3", "tables_text": "", "cells": {
        "T5:r1:c1": _example_cell(300000000, "제2회 전환사채 조기상환청구권", col="당기말", period="current_end",
                                  tt="(1) 당기말 현재 파생상품부채의 내역은 다음과 같습니다.", nt="파생상품", n_value_cols=1)},
        "sentences": {
            "s20": "전환사채에 내재된 조기상환청구권 및 매도청구권은 내재파생상품의 분리조건을 모두 충족하므로 주계약에서 "
                   "분리하여 별도의 파생상품으로 회계처리하고 있습니다.",
            "s21": "발행가액에서 부채요소를 차감하고 매도청구권과 조기상환청구권을 가감한 잔여가치는 전환권의 가치에 "
                   "해당하며, 세효과 차감후 금액을 자본으로 계상하였습니다."}}
    rec3 = {"instrument": "제2회 전환사채", "series_key": "CB-2", "kind": "CB",
            "balance": {"current_end": {"derivative_liability": 300000000}}, "option_class": "equity",
            "embedded_separation": "separated", "separated_targets": ["redemption"],
            "evidence": {"balance.current_end.derivative_liability": ["T5:r1:c1"], "option_class": ["s21"],
                         "embedded_separation": ["s20"]}}
    return [({"rcept_no": "EX1", "tables_text": "", "cells": cells1, "sentences": {}}, rec1), (inp2, rec2), (inp3, rec3)]


_EXAMPLE_CASES_I8 = _example_cases()


def build_messages(inp: dict) -> list[dict]:
    sentences = "\n".join(f"{sid}: {text}" for sid, text in (inp.get("sentences") or {}).items()) or "(없음)"
    user = _USER_I8.format(rules=_RULES_I8, schema=json.dumps(OUTPUT_SCHEMA, ensure_ascii=False),
                           example=_EXAMPLE_I8, tables_text=inp.get("tables_text") or "(없음)",
                           sentences=sentences)
    return [{"role": "system", "content": _SYSTEM_I8}, {"role": "user", "content": user}]


def cache_key(inp: dict) -> str:
    raw = PROMPT_VERSION + json.dumps(inp, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Cache:
    """키 → 원 응답. jsonl append(한 줄 {"key", "response", "model_used"}). 스레드 안전."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, object] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # 끊긴 마지막 줄
                    if isinstance(row, dict) and "key" in row:
                        self._data[row["key"]] = {"response": row.get("response"),
                                                  "model_used": row.get("model_used")}

    def get(self, key: str):
        """원 응답(없으면 None)."""
        with self._lock:
            return (self._data.get(key) or {}).get("response")

    def entry(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def put(self, key: str, response, model_used: str | None = None) -> None:
        with self._lock:
            self._data[key] = {"response": response, "model_used": model_used}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "response": response, "model_used": model_used},
                                    ensure_ascii=False) + "\n")


def _parse(response) -> list | None:
    if isinstance(response, (str, bytes)):
        try:
            response = json.loads(response)
        except ValueError:
            return None
    if not isinstance(response, dict):
        return None
    items = response.get("instruments")
    return items if isinstance(items, list) else None


def extract_instruments(inp: dict, complete_json: CompleteJson, cache: Cache | None = None, *,
                        model: str = "", usage_getter: Callable[[], dict | None] | None = None) -> dict:
    """입력 묶음 하나 → {"rcept_no", "records", "dropped", "prompt_version", "model", "model_used", "usage",
    "cached"}.

    records 는 검증을 거친 기록(필드별 사유는 record["issues"]), dropped 는 버린 기록·호출의 사유다.
    호출이 실패하면 dropped=["llm_error"], 응답 JSON 이 깨지면 ["bad_json"]. 실패 응답은 캐시하지 않는다.
    기록 하나의 검증이 예외를 내면 그 기록만 "bad_record" 로 버린다(캐시된 응답으로 이어 해도 멈추지 않게).
    model_used 는 실제로 답한 모델(usage_getter 의 model_used, 없으면 model)이고 캐시에도 남는다.
    """
    out = {"rcept_no": inp.get("rcept_no"), "records": [], "dropped": [], "prompt_version": PROMPT_VERSION,
           "model": model, "model_used": None, "usage": None, "cached": False}
    if inp.get("fs_scope") is not None:
        out["fs_scope"] = inp["fs_scope"]
    key = cache_key(inp)
    entry = cache.entry(key) if cache is not None else None
    if entry is not None:
        response = entry["response"]
        out["cached"] = True
        out["model_used"] = entry.get("model_used")
    else:
        try:
            response = complete_json(build_messages(inp), OUTPUT_SCHEMA)
        except Exception as exc:  # noqa: BLE001 — 공시 하나가 실행 전체를 멈추지 않게
            out["dropped"] = ["llm_error"]
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out["usage"] = usage_getter() if usage_getter else None
            return out
        out["usage"] = usage_getter() if usage_getter else None
        out["model_used"] = (out["usage"] or {}).get("model_used") or model or None
    items = _parse(response)
    if items is None:
        out["dropped"] = ["bad_json"]
        return out
    if cache is not None and not out["cached"]:
        cache.put(key, response, out["model_used"])
    for seq, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            out["dropped"].append("bad_record")
            continue
        try:
            rec, reasons = validate(item, inp, seq=seq)
        except Exception:  # noqa: BLE001
            out["dropped"].append("bad_record")
            continue
        if rec is None:
            out["dropped"].extend(reasons)
            continue
        rec["issues"] = reasons
        out["records"].append(rec)
    return out


def dry_complete_json(messages: list, schema: dict) -> dict:
    """LLM 없는 가짜(--dry): 언제나 빈 목록."""
    return {"instruments": []}


# ---------- 공시 단위 실행 ----------

def load_instrument_facts(facts_path: Path) -> dict[str, list[dict]]:
    """facts.jsonl → {rcept_no: [{"concept", "fs_scope"}]} (needs_instruments 에 필요한 칸만)."""
    from .instrument_input import INSTRUMENT_FACT_CONCEPTS
    out: dict[str, list[dict]] = {}
    if not Path(facts_path).exists():
        return out
    with Path(facts_path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            f = json.loads(line)
            if f.get("concept") in INSTRUMENT_FACT_CONCEPTS:
                out.setdefault(f["rcept_no"], []).append({"concept": f["concept"], "fs_scope": f.get("fs_scope")})
    return out


def _load_filing(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def list_needs(tables_dir: Path, facts: dict[str, list[dict]], rcepts: Iterable[str],
               stats: Counter | None = None) -> list[tuple[str, list[str]]]:
    """LLM 없이 대상 공시와 입력 범위(fs_scope) 목록만 센다. 범위가 빈 공시는 표 0개(no_tables)다.
    stats 를 주면 missing(파일 없음)·unreadable(읽기 실패) 수를 센다."""
    stats = stats if stats is not None else Counter()
    out = []
    for rcept in rcepts:
        path = Path(tables_dir) / f"{rcept}.json.gz"
        if not path.exists():
            stats["missing"] += 1
            continue
        try:
            tables = _load_filing(path).get("tables") or []
        except Exception:  # noqa: BLE001
            stats["unreadable"] += 1
            continue
        if needs_instruments(facts.get(rcept, []), tables):
            out.append((rcept, input_scopes(tables)))
    return out


_RETRY_DROPS = {"llm_error", "bad_json", "input_error"}


def read_done(out_path: Path) -> set[tuple[str, str | None]]:
    """이미 끝낸 (rcept_no, fs_scope). 끊긴 마지막 줄과 llm_error·bad_json·input_error 줄은 빼서 다음 실행이 다시 부르게 한다
    (그래서 같은 키가 여러 줄일 수 있다 — 읽는 쪽은 마지막 줄을 쓴다)."""
    done: set[tuple[str, str | None]] = set()
    if not out_path.exists():
        return done
    with out_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if _RETRY_DROPS & set(row.get("dropped") or []):
                continue
            done.add((row.get("rcept_no"), row.get("fs_scope")))
    return done


def _ensure_trailing_newline(path: Path) -> None:
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            last = fh.read(1)
        if last != b"\n":
            with path.open("ab") as fh:
                fh.write(b"\n")


def run_filings(*, tables_dir: Path, facts: dict[str, list[dict]], out_path: Path, rcepts: Iterable[str],
                complete_json: CompleteJson, model: str, cache: Cache | None = None, workers: int = 4,
                limit: int | None = None, usage_getter: Callable[[], dict | None] | None = None,
                log: Callable[[str], None] = lambda m: None) -> dict:
    """공시마다 needs_instruments → fs_scope 마다 입력 묶음 → 호출·검증 → out_path 에 한 줄씩 append.

    이어 하기: out_path 에 이미 있는 (rcept_no, fs_scope) 는 건너뛴다. 대상인데 넣을 표가 0개인 공시는
    {"skipped": "no_tables"} 줄을 남긴다. limit 은 이번 실행에서 새로 처리할 대상 공시 수 상한이다.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_trailing_newline(out_path)
    done = read_done(out_path)
    done_rcepts = {r for r, _s in done}
    counts: Counter = Counter()
    lock = threading.Lock()
    t0 = time.time()

    def inc(key: str, n: int = 1) -> None:
        with lock:
            counts[key] += n

    def jobs():
        taken = 0
        for rcept in rcepts:
            if limit is not None and taken >= limit:
                return
            path = Path(tables_dir) / f"{rcept}.json.gz"
            if not path.exists():
                inc("missing")
                continue
            inc("scanned")
            try:
                filing = _load_filing(path)
            except Exception:  # noqa: BLE001
                inc("read_error")
                continue
            tables = filing.get("tables") or []
            if not needs_instruments(facts.get(rcept, []), tables):
                continue
            inc("needs")
            scopes = input_scopes(tables)
            todo = [s for s in scopes if (rcept, s) not in done]
            if not scopes:
                if rcept in done_rcepts:
                    inc("already_done")
                    continue
                taken += 1
                yield rcept, None, None, filing
                continue
            if not todo:
                inc("already_done")
                continue
            taken += 1
            for s in todo:
                yield rcept, s, tables, filing

    fh = out_path.open("a", encoding="utf-8")

    def write(row: dict) -> None:
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()

    def one(job) -> None:
        rcept, scope, tables, filing = job
        if scope is None:
            write({"rcept_no": rcept, "fs_scope": None, "skipped": "no_tables", "records": [], "dropped": [],
                   "prompt_version": PROMPT_VERSION, "model": model, "usage": None})
            with lock:
                counts["skipped_no_tables"] += 1
            return
        start = time.time()
        try:
            inp = build_input(rcept, tables, filing.get("paragraphs") or [], fs_scope=scope)
        except Exception as exc:  # noqa: BLE001
            write({"rcept_no": rcept, "fs_scope": scope, "records": [], "dropped": ["input_error"],
                   "error": f"{type(exc).__name__}: {exc}"[:200], "prompt_version": PROMPT_VERSION,
                   "model": model, "usage": None})
            with lock:
                counts["dropped:input_error"] += 1
            return
        res = extract_instruments(inp, complete_json, cache, model=model, usage_getter=usage_getter)
        res.update({"n_cells": len(inp["cells"]), "n_sentences": len(inp["sentences"]),
                    "elapsed_sec": round(time.time() - start, 2)})
        write(res)
        with lock:
            counts["done"] += 1
            counts["records"] += len(res["records"])
            counts["cached"] += int(res["cached"])
            for d in res["dropped"]:
                counts[f"dropped:{d.split(':')[0]}"] += 1
            for r in res["records"]:
                for reason in r.get("issues") or []:
                    counts[f"issue:{reason.split(':')[0]}"] += 1
            if counts["done"] % 20 == 0:
                log(f"{counts['done']} done {time.time() - t0:.0f}s")

    try:
        if workers <= 1:
            for job in jobs():
                one(job)
        else:
            # 공시를 한꺼번에 메모리에 올리지 않게 진행 중 작업을 workers*2 개로 묶는다.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending: list = []
                for job in jobs():
                    pending.append(pool.submit(one, job))
                    if len(pending) >= workers * 2:
                        pending.pop(0).result()
                for fut in pending:
                    fut.result()
    finally:
        fh.close()
    summary = dict(sorted(counts.items()))
    summary.update({"prompt_version": PROMPT_VERSION, "model": model, "elapsed_sec": round(time.time() - t0, 1)})
    return summary


def summarize_out(out_path: Path) -> dict:
    """출력 jsonl 을 (rcept_no, fs_scope) 마다 마지막 줄만으로 다시 센다(이어 하기 뒤 측정용).

    evidence_pass_rate = 필드 사유(issues)가 하나도 없는 기록 / 전체 기록.
    """
    last: dict[tuple, dict] = {}
    lines = 0
    with Path(out_path).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            lines += 1
            last[(row.get("rcept_no"), row.get("fs_scope"))] = row
    issues: Counter = Counter()
    issue_fields: Counter = Counter()
    dropped: Counter = Counter()
    models: Counter = Counter()
    usage: Counter = Counter()
    records = all_pass = skipped = cached = 0
    for row in last.values():
        if row.get("skipped"):
            skipped += 1
            continue
        cached += int(bool(row.get("cached")))
        if row.get("model_used"):
            models[row["model_used"]] += 1
        for k, v in (row.get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                usage[k] += v
        for d in row.get("dropped") or []:
            dropped[str(d).split(":")[0]] += 1
        for rec in row.get("records") or []:
            records += 1
            rs = rec.get("issues") or []
            all_pass += int(not rs)
            for r in rs:
                issues[str(r).split(":")[0]] += 1
                issue_fields[str(r)] += 1
    return {
        "lines": lines, "keys": len(last), "filings": len({k[0] for k in last}),
        "skipped_no_tables": skipped, "cached": cached, "records": records, "records_all_pass": all_pass,
        "evidence_pass_rate": round(all_pass / records, 4) if records else None,
        "issues": dict(sorted(issues.items())), "issue_fields": dict(issue_fields.most_common(30)),
        "dropped": dict(sorted(dropped.items())), "model_used": dict(models), "usage": dict(usage),
        "prompt_version": PROMPT_VERSION,
    }
