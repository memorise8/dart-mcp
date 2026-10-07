"""사후 검사 규칙별 통과·실패. 문장은 스파이크 adjudication.md·out_luna.jsonl의 실제 문장이다."""

import unittest

from dart_search_mcp.topic_judge.postcheck import Verdict, ip_fair_value, postcheck
from dart_search_mcp.topic_judge.topics import TOPIC_DEFS
from dart_search_mcp.topic_judge.window import Window

T = TOPIC_DEFS

# 실제 문장(스파이크)
BYEOKJE = "정형화된 시장에서 거래되지 않아 시가가 없는 파생상품(상환전환우선주의 내재파생상품)에 대하여는 계약시점 및 계약시점 후 평가에 관한 회계처리를 하지 않음."  # #6
BNK = "또한, 위험회피회계의 수단으로 지정되지 않은 파생상품이나 금융상품으로부터 분리된 내재파생상품도 단기매매금융부채로 분류됩니다."  # #7
CLICK = "주계약과 경제적 특성 및 위험이 밀접하게 관련되어 있지 않고 당기손익-공정가치 측정 항목이 아닌 경우 별도의 파생상품으로 회계처리하고 있습니다."  # #9
LIHAN = "전환권조정 (966,183) (1,584,057)"  # #4
NH = "당사는 소액자산리스(가치가 낮은 소액 사무비품, IT 기기) 에 대해 인식면제 규정을 적용하고, 단기리스(리스기간이 12개월 이하인 리스)에 대해서는 인식면제 규정을 적용하지 않습니다."  # #27
DONGJIN = "회사는 단기리스와 소액자산 리스의 경우 예외규정을 선택하여 리스료를 리스기간에 걸쳐 정액기준으로 비용 인식합니다."  # #28
NAMYANG = "한편, 개발비를 제외한 내부적으로 창출된 무형자산은 발생시점에 비용항목으로 하여 당기손익에 반영하고 있습니다."  # #37
STANDARD = "리스이용자는 단기리스와 소액 기초자산 리스에 인식 면제를 선택할 수 있습니다."  # 함정 예시(기준서 문장)
MIRAE_A = "당기말 현재 투자부동산 750,375,238천원(전기말: 757,545,254천원)은 차입금과 관련하여 금융기관에 담보로 제공되어 있습니다(주석17, 43 참조)."
MIRAE_MID = "당기 중 연결실체는 투자부동산과 관련하여 1,203,366천원(전기: 707,278천원)의 손상차손환입을 인식하였으며, 당기 중 인식한 손상차손은 없습니다."
MIRAE_B = "한편, 당기말 투자부동산의 공정가치는 1,746,795백만원(전기말: 1,725,228백만원)이며, 연결실체는 공인된 외부 감정평가기관이 독립적으로 수행한 평가를 바탕으로 공정가치를 산출하였습니다."
SGC = ("(3) 보고기간 종료일 현재 투자부동산의 공정가치는 다음과 같습니다. (단위: 천원) 구분 당기말 전기말 "
       "장부금액 공정가치 장부금액 공정가치 토지 45,512 56,001 16,545,172 17,085,668 건물 10,489 374,293 "
       "합계 56,001 56,001 16,919,465 17,085,668 (4) 보고기간 종료일 현재 투자부동산은 장ㆍ단기차입금의 담보로 제공되어 있습니다.")
KGC = ("(4) 당기말과 전기말 현재 투자부동산의 공정가치와 장부금액은 다음과 같습니다. (단위: 천원) 구분 제 14(당) 기 제 13(전) 기 "
       "공정가치 장부금액 공정가치 장부금액 토지 6,254,250 4,164,954 6,254,250 4,164,954 건물 9,153,830 4,379,268 9,153,830 4,593,731 "
       "합계 15,408,080 8,544,222 15,408,080 8,758,685 투자부동산의 공정가치는 원가접근법과 시장접근법을 이용하여 결정되었습니다.")
KORAMCO = ("(5) 당기말 현재 투자부동산에 대한 공정가치 평가 내역은 다음과 같습니다. (단위: 천원) 구 분 당기말 "
           "장부금액 공정가치 투자부동산 207,402,685 435,700,000 (*) 투자부동산의 공정가치는 수익환원법으로 측정합니다.")
HANMI = ("(3) 당기말 및 전기말 현재 투자부동산의 공정가치는 다음과 같습니다. (단위: 천원) 구 분 당기말 전기말 장부금액 공정가치 "
         "장부금액 공정가치 토지 28,321,861 105,540,415 25,980,601 83,448,424 건물 29,754,463 16,117,689 회사는 투자부동산에 대해 "
         "공시지가 및 최근의 이용가능한 유사거래가격 등을 반영한 평가금액에 근거하여 산정하고 있습니다.(4) 당기 및 전기 중 합계 1,000 2,000")


def win(*pieces: str) -> Window:
    return Window(text=" … ".join(pieces), spans=tuple((0, len(p)) for p in pieces), anchor="keyword")


def raw(value: str, evidence: str, subject: str = "self") -> dict:
    return {"value": value, "evidence": evidence, "subject": subject}


class RuleTests(unittest.TestCase):
    def check(self, code, r, w) -> Verdict:
        return postcheck(T[code], r, w)

    def test_통과하면_값과_근거와_subject를_낸다(self) -> None:
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("NOT_SEPARATED", BYEOKJE), win("앞 문장. " + BYEOKJE))
        self.assertEqual((v.value, v.evidence, v.subject, v.held_reason), ("NOT_SEPARATED", BYEOKJE, "self", None))
        self.assertEqual(v.raw["value"], "NOT_SEPARATED")

    def test_model_undetermined(self) -> None:
        v = self.check("LEASE_EXEMPTION", raw("UNDETERMINED", STANDARD, "standard_text"), win(STANDARD))
        self.assertEqual((v.value, v.held_reason, v.subject), ("UNDETERMINED", "model_undetermined", "standard_text"))

    def test_1_invalid_value(self) -> None:
        for bad in ("MAYBE", None, 3, "IP_FAIR_VALUE"):
            v = self.check("LEASE_EXEMPTION", raw(bad, NH), win(NH))
            self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "invalid_value"))
        self.assertIsNone(self.check("LEASE_EXEMPTION", raw("LOW_VALUE", NH), win(NH)).held_reason)

    def test_2_not_self(self) -> None:
        v = self.check("LEASE_EXEMPTION", raw("BOTH", STANDARD, "standard_text"), win(STANDARD))
        self.assertEqual((v.value, v.held_reason, v.subject), ("UNDETERMINED", "not_self", "standard_text"))
        v = self.check("LEASE_EXEMPTION", {"value": "BOTH", "evidence": DONGJIN}, win(DONGJIN))
        self.assertEqual(v.held_reason, "not_self")
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN), win(DONGJIN)).value, "BOTH")

    def test_3_evidence_missing(self) -> None:
        for ev in ("", "   ", None):
            v = self.check("LEASE_EXEMPTION", raw("BOTH", ev), win(DONGJIN))
            self.assertEqual(v.held_reason, "evidence_missing")
        self.assertIsNone(self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN), win(DONGJIN)).held_reason)

    def test_4_evidence_not_in_window(self) -> None:
        v = self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN), win(NH))
        self.assertEqual(v.held_reason, "evidence_not_in_window")
        # 바꿔 쓴 근거도 실패
        v = self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN.replace("선택하여", "선택해")), win(DONGJIN))
        self.assertEqual(v.held_reason, "evidence_not_in_window")

    def test_4_줄바꿈_공백이_달라도_통과(self) -> None:
        wtext = DONGJIN.replace("예외규정을 ", "예외규정을\n  ").replace("리스료를 ", "리스료를\t")
        v = self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN), win(wtext))
        self.assertEqual((v.value, v.held_reason), ("BOTH", None))
        ev = DONGJIN.replace("선택하여 ", "선택하여\n")
        v = self.check("LEASE_EXEMPTION", raw("BOTH", ev), win(DONGJIN))
        self.assertEqual((v.value, v.evidence), ("BOTH", DONGJIN))

    def test_4_이웃_조각에_걸친_근거(self) -> None:
        head, tail = NH[:60], NH[60:]
        w = win("앞쪽 표 1,234", head, tail, "뒤쪽")
        for ev in (head + tail, head + " … " + tail):
            v = self.check("LEASE_EXEMPTION", raw("LOW_VALUE", ev), w)
            self.assertEqual((v.value, v.held_reason), ("LOW_VALUE", None), ev)
        # 이웃이 아닌 조각은 실패
        w2 = win(head, "가운데 조각입니다.", tail)
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("LOW_VALUE", head + tail), w2).held_reason, "evidence_not_in_window")

    def test_4_갈라진_어미와_원문_속_말줄임표(self) -> None:
        # 모델이 원문의 「있습니 다.」를 그대로 베꼈어도 doc_text와 같은 정규화로 맞는다.
        ev = "회사는 단기리스 및 소액 기초자산 리스에 대하여 인식 면제 규정을 적용하지 않습니 다."
        wt = "회사는 단기리스 및 소액 기초자산 리스에 대하여 인식 면제 규정을 적용하지 않습니다."
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("NONE", ev), win(wt)).value, "NONE")
        # 원문 자체에 「 … 」가 있어도 조각 경계는 spans로 정한다.
        inner = "회사는 사무용품 … 정수기 등 소액자산 리스에 인식 면제를 적용하고 단기리스에도 면제를 적용합니다."
        w = Window(text=inner + " … 뒤쪽 조각입니다 그리고 더", spans=((0, len(inner)), (100, 116)), anchor="keyword")
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("BOTH", inner), w).value, "BOTH")

    def test_4_가운데_문장을_생략한_두_문장(self) -> None:
        # 스파이크 #22: luna가 두 문장을 이어 붙이고 가운데 문장을 뺐다.
        w = win(MIRAE_A + MIRAE_MID + " " + MIRAE_B)
        self.assertIsNone(self.check("IP_FAIR_VALUE", raw("FV_NOT_DISCLOSED", MIRAE_A + " " + MIRAE_B), w).held_reason)
        # 순서가 뒤바뀌면 실패
        v = self.check("IP_FAIR_VALUE", raw("FV_NOT_DISCLOSED", MIRAE_B + " " + MIRAE_A), w)
        self.assertEqual(v.held_reason, "evidence_not_in_window")

    def test_5_조건절을_잘라낸_조각도_conditional(self) -> None:  # 리뷰 M1, #9 클릭나라
        frag = "별도의 파생상품으로 회계처리하고 있습니다."
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", frag), win("앞 문장입니다. " + CLICK + " 뒤 문장입니다."))
        self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "conditional"))
        # 두 문장 이어 붙이기에서 뒷 조각이 조건문 한가운데에서 시작해도 걸린다.
        w = win("회사는 사무실과 차량을 리스하고 있습니다. 소액자산 요건을 충족하는 경우에는 인식 면제를 적용합니다.")
        v = self.check("LEASE_EXEMPTION", raw("LOW_VALUE", "회사는 사무실과 차량을 리스하고 있습니다. 인식 면제를 적용합니다."), w)
        self.assertEqual(v.held_reason, "conditional")
        # 근거는 모델이 낸 그대로 남는다(문장 넓히기는 검사에만 쓴다).
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("NOT_SEPARATED", "회계처리를 하지 않음."), win(BYEOKJE))
        self.assertEqual((v.value, v.evidence), ("NOT_SEPARATED", "회계처리를 하지 않음."))

    def test_5_머리_표시와_붙은_번호는_문장을_넘지_않는다(self) -> None:  # 재리뷰 N1(trial100 실제 문맥)
        ctx = ("또한, 리스부채의 장부금액은 리스기간의 변경, 리스료의 변경 또는 기초자산을 매수하는 선택권 평가에 변동이 있는 경우 "
               "재측정됩니다.2.3.19.3 단기리스와 소액 기초자산 리스당사는 기계장치와 설비의 리스에 단기리스(즉, 이러한 리스는 "
               "리스기간이 리스개시일로부터 12개월 이하이고 매수선택권을 포함하지 않음)에 대한 인식 면제 규정을 적용합니다. 또한 "
               "당사는 소액자산으로 간주되는 사무용품의 리스에 소액자산 리스에 대한 인식 면제 규정을 적용합니다. 2.3.20 무형자산")
        ev = ("당사는 기계장치와 설비의 리스에 단기리스(즉, 이러한 리스는 리스기간이 리스개시일로부터 12개월 이하이고 매수선택권을 "
              "포함하지 않음)에 대한 인식 면제 규정을 적용합니다. 또한 당사는 소액자산으로 간주되는 사무용품의 리스에 소액자산 리스에 "
              "대한 인식 면제 규정을 적용합니다.")
        v = self.check("LEASE_EXEMPTION", raw("BOTH", ev), win(ctx))
        self.assertEqual((v.value, v.held_reason), ("BOTH", None))
        tmpl = ("(9) 리스 회사는 계약의 약정시점에, 계약 자체가 리스인지, 계약이 리스를 포함하는지를 판단합니다. 1) 회사가 리스이용자인 경우 "
                "회사는 단기리스와 소액 기초자산 리스를 제외한 모든 리스에 대하여 하나의 인식과측정 접근법을 적용합니다. 회사는 리스부채를 인식합니다.")
        ev2 = "회사는 단기리스와 소액 기초자산 리스를 제외한 모든 리스에 대하여 하나의 인식과측정 접근법을 적용합니다."
        v = self.check("LEASE_EXEMPTION", raw("BOTH", ev2), win(tmpl))
        self.assertEqual((v.value, v.held_reason), ("BOTH", None))
        # 머리 표시 뒤라도 진짜 조건절은 여전히 걸린다.
        cond = "1) 회사가 리스이용자인 경우 소액자산 요건을 충족하는 경우에는 인식 면제를 적용합니다."
        v = self.check("LEASE_EXEMPTION", raw("LOW_VALUE", "인식 면제를 적용합니다."), win(cond))
        self.assertEqual(v.held_reason, "conditional")

    def test_7_분류목록_조각도_걸린다(self) -> None:
        frag = "단기매매금융부채로 분류됩니다."
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", frag), win(BNK))
        self.assertEqual(v.held_reason, "classification_list")

    def test_5_conditional(self) -> None:
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", CLICK), win(CLICK))  # #9
        self.assertEqual(v.held_reason, "conditional")
        # 「경우」가 있어도 선택 동사가 함께 있으면 통과(#28)
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("BOTH", DONGJIN), win(DONGJIN)).value, "BOTH")
        amt = "제3회 전환사채의 전환권은 확정수량 조건을 충족하는 경우에 해당하여 전환권대가로 분류하였습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("EQUITY", amt), win(amt)).value, "EQUITY")
        amt2 = "회사가 발행한 전환사채의 전환권은 조건을 충족하는 경우에 해당하여 자본 항목 전환권대가 1,112,489,070원으로 표시하였습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("EQUITY", amt2), win(amt2)).value, "EQUITY")

    def test_6_value_words_전환권조정_한_줄(self) -> None:
        for value in ("LIABILITY", "EQUITY"):  # #4 리한, #5 이엠앤아이
            v = self.check("CONVERSION_OPTION_CLASS", raw(value, LIHAN), win("사채 1,000,000 " + LIHAN))
            self.assertEqual(v.held_reason, "value_words_missing")
        ok = "당사가 발행한 전환사채의 전환권은 파생상품부채로 인식하였습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("LIABILITY", ok), win(ok)).value, "LIABILITY")
        kraver = "(주4) 전환상환우선주, 전환권 및 상환권 금융부채 인식"  # #3 크레이버
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("LIABILITY", kraver), win(kraver)).value, "LIABILITY")
        ok3 = "당사는 전환사채의 전환권을 금융부채로 인식하였습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("LIABILITY", ok3), win(ok3)).value, "LIABILITY")
        ok2 = "당사가 발행한 복합금융상품은 상환전환우선주이며 자본요소는 공정가치와 부채요소의 차이로 최초 인식합니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("EQUITY", ok2), win(ok2)).value, "EQUITY")

    def test_6_value_words_개발비(self) -> None:
        v = self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", NAMYANG), win(NAMYANG))  # #37
        self.assertEqual(v.held_reason, "value_words_missing")
        amort = "자본화된 개발비는 추정내용연수 5년 동안 정액법으로 상각합니다."  # #41
        self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", amort), win(amort)).held_reason,
                         "value_words_missing")
        row = "개발비 1,523,640 (312,000) 1,211,640"
        v = self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", row), win("무형자산 내역 산업재산권 12,000 " + row))
        self.assertEqual((v.value, v.held_reason), ("CAPITALIZED", None))
        stmt = "당기 중 신제품 개발 프로젝트 관련 지출 1,200,000천원을 개발비로 인식하였습니다."
        self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", stmt), win(stmt)).value, "CAPITALIZED")

    def test_6_부정형은_필요한_말이_아니다(self) -> None:  # 리뷰 M2
        neg = "연구단계 지출은 개발비로 인식하지 않고 발생 즉시 비용으로 처리하였습니다."
        self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", neg), win(neg)).held_reason,
                         "value_words_missing")
        neg2 = "당사는 전환사채의 전환권을 금융부채로 분류하지 않았습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("LIABILITY", neg2), win(neg2)).held_reason,
                         "value_words_missing")
        neg3 = "전환권은 금융부채로 인식되지 않습니다."
        self.assertEqual(self.check("CONVERSION_OPTION_CLASS", raw("LIABILITY", neg3), win(neg3)).held_reason,
                         "value_words_missing")
        pos = "당기 중 지출 1,200,000천원을 개발비로 자산화하였습니다."
        self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", pos), win(pos)).value, "CAPITALIZED")

    def test_6_인용이_끊겨도_문장의_부정형은_걸린다(self) -> None:  # 재리뷰 N4
        sent = "연구단계 지출은 개발비로 인식하지 않고 발생 즉시 비용으로 처리하였습니다."
        v = self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", "연구단계 지출은 개발비로 인식"), win(sent))
        self.assertEqual(v.held_reason, "value_words_missing")

    def test_6_내재파생_분리는_내재_파생이_문장에_있어야(self) -> None:  # 재리뷰 N2
        tmpl = ("④ 복합금융상품 회사는 당기손익인식지정항목으로 분류한 복합금융상품을 제외한 전환사채 또는 신주인수권부사채 등 "
                "복합금융상품에 대하여 부채요소와 자본요소를 분리하여 회계처리하고 있습니다.")
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", tmpl[2:]), win(tmpl))
        self.assertEqual(v.held_reason, "value_words_missing")
        ok = "연결실체는 상기 전환사채의 조기상환청구권 및 전환권을 내재파생상품으로 별도로 분리하여 공정가치로 인식하였습니다."
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", "별도로 분리하여 공정가치로 인식하였습니다."), win(ok))
        self.assertEqual((v.value, v.held_reason), ("SEPARATED", None))

    def test_6_FVOCI_지정_기준서_꼴은_보류(self) -> None:  # 재리뷰 N3
        tmpl = ("반면에 최초 인식시점에 기타포괄손익-공정가치항목으로 지정한 지분상품에 대한 투자는 이전에 인식한 손익누계액을 "
                "당기손익으로 재분류하지 않으나 이익잉여금으로 대체합니다.")
        v = self.check("FVOCI_DESIGNATION", raw("DESIGNATED", tmpl), win(tmpl))
        self.assertEqual(v.held_reason, "value_words_missing")
        ok = "연결실체는 전략적 업무제휴 등의 목적으로 보유하고 있는 지분증권에 대해 기타포괄손익-공정가치 측정 항목으로 지정하였습니다."
        self.assertEqual(self.check("FVOCI_DESIGNATION", raw("DESIGNATED", ok), win(ok)).value, "DESIGNATED")
        both = "회사는 전략적 투자 목적의 지분상품을 기타포괄손익-공정가치 측정 항목으로 지정하였으며, 지정한 지분상품에서 발생하는 배당금은 당기손익으로 인식합니다."
        self.assertEqual(self.check("FVOCI_DESIGNATION", raw("DESIGNATED", both), win(both)).value, "DESIGNATED")

    def test_6_개발비_금액은_실제_금액만(self) -> None:  # 리뷰 L1
        for ev in ("개발비(1) 는 정액법으로 5년간 상각합니다.", "개발비 20~30년 동안 상각합니다.", "개발비 5 년"):
            self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", ev), win(ev)).held_reason,
                             "value_words_missing", ev)
        for ev in ("개발비(*1) 1,523,640 (312,000)", "개발비 (1,234,567)"):
            self.assertEqual(self.check("DEV_COST_CAPITALIZATION", raw("CAPITALIZED", ev), win(ev)).value, "CAPITALIZED", ev)

    def test_6_value_words_리스_NONE과_FVOCI(self) -> None:
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("NONE", DONGJIN), win(DONGJIN)).held_reason, "value_words_missing")
        none_ok = "회사는 단기리스 및 소액 기초자산 리스에 대하여 인식 면제 규정을 적용하지 않습니다."
        self.assertEqual(self.check("LEASE_EXEMPTION", raw("NONE", none_ok), win(none_ok)).value, "NONE")
        fv_bad = "회사는 보유 지분상품을 모두 당기손익-공정가치 측정 금융자산으로 분류합니다."
        self.assertEqual(self.check("FVOCI_DESIGNATION", raw("NOT_DESIGNATED", fv_bad), win(fv_bad)).held_reason,
                         "value_words_missing")
        fv_ok = "회사는 보유 지분상품을 기타포괄손익-공정가치 측정 항목으로 지정하지 않았습니다."
        self.assertEqual(self.check("FVOCI_DESIGNATION", raw("NOT_DESIGNATED", fv_ok), win(fv_ok)).value, "NOT_DESIGNATED")

    def test_7_classification_list(self) -> None:
        v = self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("SEPARATED", BNK), win(BNK))  # #7·8·10·14
        self.assertEqual(v.held_reason, "classification_list")
        self.assertEqual(self.check("EMBEDDED_DERIVATIVE_SEPARATION", raw("NOT_SEPARATED", BYEOKJE), win(BYEOKJE)).value,
                         "NOT_SEPARATED")


# 실제 공시 20260226007165의 4열 표(취득원가·감가상각누계액·장부금액·공정가치).
FOUR_COL = ("11.1 보고기간말 현재 당사가 보유하고 있는 투자부동산의 내역은 다음과 같습니다. (1) 당기 (단위: 원) "
            "구분 취득원가 감가상각누계액 장부금액 공정가치 토지 3,557,865,229 - 3,557,865,229 1,635,256,350 "
            "건물 8,300,967,578 (553,397,837) 7,747,569,741 5,238,488,970 "
            "합 계 11,858,832,807 (553,397,837) 11,305,434,970 6,873,745,320 (*)취득시점에 감정평가를 받았습니다.")
# 실제 공시 20260318000844: 당기·전기 4열씩, 주석 표시가 붙은 머리.
FOUR_COL_2P = ("19. 투자부동산 (1) 당기말과 전기말 현재 투자부동산의 내역은 다음과 같습니다. (단위 : 천원) 과 목 제 73(당) 기 "
               "제 72(전) 기 취득원가 상각누계액 장부금액 공정가치(*1) 취득원가 상각누계액 장부금액 공정가치(*1) "
               "토지 16,018,281 - 16,018,281 31,652,954 13,646,538 - 13,646,538 28,144,000 "
               "합 계 23,442,712 (1,950,449) 21,492,263 31,652,954 19,842,833 (1,765,996) 18,076,837 28,144,000 (*1) 투자부동산의 공정가치는 독립된 평가기관이 측정하였습니다.")


class IpFairValueTests(unittest.TestCase):
    ip = T["IP_FAIR_VALUE"]

    def test_4열_표는_장부금액_공정가치_열을_읽는다(self) -> None:  # 리뷰 H1
        value, ev = ip_fair_value(FOUR_COL)
        self.assertEqual(value, "FV_DIFFERS")  # 11,305,434,970 ≠ 6,873,745,320
        self.assertTrue(ev.endswith("11,305,434,970 6,873,745,320"))
        same = FOUR_COL.replace("11,305,434,970 6,873,745,320", "11,305,434,970 11,305,434,970")
        self.assertEqual(ip_fair_value(same)[0], "FV_EQUALS")  # 취득원가·누계액을 읽으면 DIFFERS가 된다
        self.assertEqual(ip_fair_value(FOUR_COL_2P)[0], "FV_DIFFERS")
        eq2 = FOUR_COL_2P.replace("21,492,263 31,652,954 19,842,833", "21,492,263 21,492,263 19,842,833")
        self.assertEqual(ip_fair_value(eq2)[0], "FV_EQUALS")

    def test_2기간_표의_빈_칸은_None(self) -> None:  # 재리뷰 N5
        # 당기 합계 행의 상각누계액 칸이 비어(「-」도 없이) 열이 밀린 경우.
        blank = FOUR_COL_2P.replace("합 계 23,442,712 (1,950,449) 21,492,263", "합 계 23,442,712 21,492,263")
        self.assertIsNone(ip_fair_value(blank))

    def test_열을_확정할_수_없으면_None(self) -> None:
        short = FOUR_COL.replace("합 계 11,858,832,807 (553,397,837) 11,305,434,970 6,873,745,320", "합 계 11,858,832,807 (553,397,837)")
        self.assertIsNone(ip_fair_value(short))
        dash = FOUR_COL.replace("11,305,434,970 6,873,745,320", "11,305,434,970 -")
        self.assertIsNone(ip_fair_value(dash))

    def test_Window를_넘기면_spans_조각으로_읽는다(self) -> None:  # 리뷰 L4
        table = SGC.replace("건물 10,489 374,293", "건물 … 10,489 374,293")  # 원문 자체에 「 … 」
        w = Window(text=table, spans=((0, len(table)),), anchor="keyword")
        self.assertEqual(ip_fair_value(w)[0], "FV_EQUALS")

    def test_당기_열이_같으면_FV_EQUALS(self) -> None:  # #26 SGC솔루션
        value, ev = ip_fair_value(SGC)
        self.assertEqual(value, "FV_EQUALS")
        self.assertTrue(ev.startswith("당기말"))
        self.assertTrue(ev.endswith("합계 56,001 56,001 16,919,465 17,085,668"))
        self.assertIn(ev, SGC)

    def test_공정가치_장부금액_순서와_당_표기(self) -> None:
        self.assertEqual(ip_fair_value(KGC)[0], "FV_DIFFERS")
        self.assertEqual(ip_fair_value(KORAMCO)[0], "FV_DIFFERS")

    def test_합계_행이_표_밖이면_None(self) -> None:
        self.assertIsNone(ip_fair_value(HANMI))

    def test_숫자가_없거나_전기_먼저면_None(self) -> None:
        self.assertIsNone(ip_fair_value(MIRAE_B))
        self.assertIsNone(ip_fair_value("전기말 당기말 장부금액 공정가치 합계 100 200 300 400"))
        self.assertIsNone(ip_fair_value("장부금액 공정가치 합계 100 200"))

    def test_postcheck가_숫자로_결정한다(self) -> None:
        # 모델은 FV_DIFFERS라 했지만 당기 열은 같다(#26에서 mini가 전기 열을 읽은 오류).
        claim = "(3) 보고기간 종료일 현재 투자부동산의 공정가치는 다음과 같습니다."
        v = postcheck(self.ip, raw("FV_DIFFERS", claim), win(SGC))
        self.assertEqual((v.value, v.held_reason), ("FV_EQUALS", None))
        self.assertIn("합계 56,001 56,001", v.evidence)

    def test_공정가치모형이면_FV_EQUALS(self) -> None:  # #25 아시아리얼티
        ev = "최초 인식 후 투자부동산은 공정가치모형을 적용하여 공정가치로 측정합니다."
        v = postcheck(self.ip, raw("FV_DIFFERS", ev), win(ev))
        self.assertEqual((v.value, v.evidence, v.held_reason), ("FV_EQUALS", ev, None))
        spaced = "투자부동산은 공정가치 모형을 적용합니다."  # 리뷰 L3
        self.assertEqual(postcheck(self.ip, raw("FV_DIFFERS", spaced), win(spaced)).value, "FV_EQUALS")

    def test_공정가치모형_부정은_지름길을_타지_않는다(self) -> None:
        for ev in ("회사는 투자부동산에 원가모형을 적용하며 공정가치모형은 적용하지 않습니다.",
                   "투자부동산의 후속측정은 공정가치 모형이 아니라 원가모형에 따릅니다."):
            v = postcheck(self.ip, raw("FV_EQUALS", ev), win(ev))
            self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "ip_numbers_missing"), ev)

    def test_근사치_문장은_숫자_없이_FV_EQUALS(self) -> None:  # #23 트라이씨클
        ev = "회사는 투자부동산의 공정가치와 장부금액의 차이가 중요하지 않은 것으로 판단하고 있습니다."
        v = postcheck(self.ip, raw("FV_EQUALS", ev), win(ev))
        self.assertEqual((v.value, v.held_reason), ("FV_EQUALS", None))
        bien = "당기말 현재 장부금액이 공정가치의 합리적인 근사치인 것으로 판단하고 있습니다."
        self.assertEqual(postcheck(self.ip, raw("FV_EQUALS", bien), win(bien)).value, "FV_EQUALS")
        # FV_DIFFERS에는 근사치 예외가 없다.
        v = postcheck(self.ip, raw("FV_DIFFERS", ev), win(ev))
        self.assertEqual(v.held_reason, "ip_numbers_missing")

    def test_숫자가_없으면_ip_numbers_missing(self) -> None:  # #22 미래에셋(단위가 달라 숫자 비교 불가)
        w = win(MIRAE_A + " " + MIRAE_B)
        v = postcheck(self.ip, raw("FV_DIFFERS", MIRAE_B), w)
        self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "ip_numbers_missing"))
        nd = "회사는 투자부동산의 공정가치를 신뢰성 있게 측정할 수 없어 공정가치를 공시하지 아니하였습니다."
        v = postcheck(self.ip, raw("FV_NOT_DISCLOSED", nd), win(nd))
        self.assertEqual((v.value, v.held_reason), ("FV_NOT_DISCLOSED", None))


if __name__ == "__main__":
    unittest.main()
