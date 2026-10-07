import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.topic_judge.topics import TOPIC_DEFS
from dart_search_mcp.topic_judge.window import Window, doc_text, excluded_ranges, pick_window

FILL = "회사는 해당 거래를 검토하였습니다. "  # 키워드가 없는 채움 문장(18자)

NOTYET_SECTION = (
    "2.2 제정·개정 기준서 (2) 공표되었으나 아직 시행되지 않은 제·개정 기준서 "
    "기업회계기준서 제1116호 '리스' 개정 - 리스이용자는 단기리스와 소액 기초자산 리스에 "
    "인식 면제를 선택할 수 있습니다. 회사는 동 개정이 재무제표에 미치는 영향을 검토 중입니다. "
)


def pad(n: int) -> str:
    return (FILL * (n // len(FILL) + 1))[:n]


class ExclusionTests(unittest.TestCase):
    def test_미시행_절은_다음_번호_머리까지_빠진다(self) -> None:
        text = pad(100) + NOTYET_SECTION + "3. 중요한 회계정책 " + pad(100)
        ranges = excluded_ranges(text)
        self.assertEqual(len(ranges), 1)
        a, b = ranges[0]
        self.assertLessEqual(a, text.index("아직 시행되지"))
        self.assertGreater(b, text.index("소액 기초자산"))
        self.assertLessEqual(b, text.index("3. 중요한 회계정책") + 1)


class PickWindowTests(unittest.TestCase):
    lease = TOPIC_DEFS["LEASE_EXEMPTION"]

    def test_미시행_절_안에만_키워드가_있으면_None(self) -> None:
        text = pad(300) + NOTYET_SECTION + "3. 중요한 회계정책 " + pad(300)
        self.assertIsNone(pick_window(self.lease, text))

    def test_키워드가_없으면_None(self) -> None:
        self.assertIsNone(pick_window(self.lease, pad(500)))

    def test_미시행_절_밖의_키워드를_고른다(self) -> None:
        self_sentence = "회사는 단기리스와 소액자산 리스의 경우 예외규정을 선택하여 리스기간에 걸쳐 정액기준으로 비용 인식합니다."
        text = pad(200) + NOTYET_SECTION + "3. 중요한 회계정책 " + pad(3000) + self_sentence + pad(3000)
        w = pick_window(self.lease, text)
        self.assertIsNotNone(w)
        self.assertIn(self_sentence, w.text)
        self.assertNotIn("검토 중입니다", w.text)
        self.assertEqual(w.anchor, self.lease.priority_res[1].pattern)

    def test_우선위치가_첫_키워드보다_창에_먼저_온다(self) -> None:
        conv = TOPIC_DEFS["CONVERSION_OPTION_CLASS"]
        table_line = "사채 100,000,000 전환권조정 (12,345,678) 사채할인발행차금 (1,000,000)"
        policy = "당사가 발행한 전환사채의 전환권은 확정수량의 지분상품으로 교환되므로 자본으로 분류하였으며, 자본 항목 전환권대가 1,112,489,070원으로 표시하였습니다."
        text = pad(500) + table_line + pad(1000) + policy + pad(1500)
        w = pick_window(conv, text)
        self.assertIsNotNone(w)
        self.assertEqual(w.anchor, conv.priority_res[0].pattern)
        self.assertIn(policy, w.text)
        self.assertIn("전환권조정", w.text)
        self.assertLess(w.text.index("전환권대가"), w.text.index("전환권조정"))
        self.assertIn(" … ", w.text)
        # spans 는 원문 좌표이고 첫 구간이 우선 위치를 품는다.
        a, b = w.spans[0]
        self.assertTrue(a <= text.index("전환권대가") < b)

    def test_우선위치가_키워드에서_멀면_키워드가_중심(self) -> None:
        conv = TOPIC_DEFS["CONVERSION_OPTION_CLASS"]
        text = "자본 항목 전환권대가 1,000원 " + pad(5000) + "전환권 행사기간은 2025년부터입니다. " + pad(3000)
        # 전환권대가 자체도 키워드(전환권) 위치라 ±1,500 안이다 → 우선 위치
        w = pick_window(conv, text)
        self.assertEqual(w.anchor, conv.priority_res[0].pattern)
        text2 = "파생상품부채 1,000원 " + pad(5000) + "전환권 행사기간은 2025년부터입니다. " + pad(3000)
        w2 = pick_window(conv, text2)
        self.assertNotEqual(w2.anchor, conv.priority_res[0].pattern)
        self.assertIn("전환권 행사기간", w2.text)
        self.assertNotIn("파생상품부채", w2.text)

    def test_우선위치가_없으면_keyword(self) -> None:
        ip = TOPIC_DEFS["DEV_COST_CAPITALIZATION"]
        text = pad(800) + "개발비는 5년 동안 정액법으로 상각합니다." + pad(800)
        w = pick_window(ip, text)
        self.assertEqual(w.anchor, "keyword")
        self.assertIn("개발비는 5년", w.text)

    def test_창_길이는_limit_이하(self) -> None:
        conv = TOPIC_DEFS["CONVERSION_OPTION_CLASS"]
        parts = []
        for i in range(6):
            parts.append(pad(1200) + f"제{i+1}회 전환사채의 전환권대가는 자본으로 분류하였습니다. ")
        text = "".join(parts) + pad(1000)
        w = pick_window(conv, text)
        self.assertLessEqual(len(w.text), 2000)
        w2 = pick_window(conv, text, limit=500)
        self.assertLessEqual(len(w2.text), 500)
        for a, b in w2.spans:
            self.assertLessEqual(b - a, 500)

    def test_겹치는_구간은_합친다(self) -> None:
        lease = self.lease
        text = pad(600) + "(2) 리스이용자 회사는 단기리스와 소액 기초자산 리스에 대해 인식 면제를 적용합니다." + pad(600)
        w = pick_window(lease, text)
        self.assertNotIn(" … ", w.text)
        self.assertEqual(len(w.spans), 1)
        a, b = w.spans[0]
        self.assertEqual(w.text, text[a:b])


class DocTextTests(unittest.TestCase):
    def test_xml을_읽고_태그를_지운다(self) -> None:
        rc = "20260401000123"
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / rc
            folder.mkdir()
            (folder / f"{rc}.xml").write_text(
                "<DOCUMENT><P>회사는&nbsp;단기리스와\n  소액자산 리스에 대해</P>"
                "<TD>인식 면제를 적용합니다.</TD><P>R&amp;D 비용 &lt;주석&gt;</P></DOCUMENT>",
                encoding="utf-8",
            )
            t = doc_text(Path(d), rc)
        self.assertIsNotNone(t)
        self.assertNotIn("<", t.replace("<주석", ""))
        self.assertIn("회사는 단기리스와 소액자산 리스에 대해 인식 면제를 적용합니다.", t)
        self.assertIn("R&D 비용", t)
        self.assertNotIn("  ", t)
        self.assertNotIn("&nbsp;", t)

    def test_폴더가_없으면_None(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(doc_text(Path(d), "20260401000999"))


class WindowTypeTests(unittest.TestCase):
    def test_필드(self) -> None:
        w = Window(text="a", spans=((0, 1),), anchor="keyword")
        self.assertEqual((w.text, w.spans, w.anchor), ("a", ((0, 1),), "keyword"))


if __name__ == "__main__":
    unittest.main()
