import unittest
from collections import Counter

from dart_search_mcp.topic_judge.gate import gate
from dart_search_mcp.topic_judge.sample import allocate, draw_samples


def rows_for(topic: str, dist: dict[str, int]) -> list[dict]:
    out = []
    i = 0
    for value, n in dist.items():
        for _ in range(n):
            i += 1
            out.append({"rcept_no": f"2026{i:010d}", "corp_code": f"{i:08d}", "fiscal_year": 2025, "topic": topic,
                        "value": value, "evidence": f"근거 {i}", "corp_name": f"회사{i}", "excerpt": f"창 {i}"})
    return out


class AllocateTests(unittest.TestCase):
    def test_비율대로_합_20(self) -> None:
        q = allocate({"BOTH": 70, "SHORT_TERM": 20, "LOW_VALUE": 10}, 20)
        self.assertEqual(q, {"BOTH": 14, "SHORT_TERM": 4, "LOW_VALUE": 2})

    def test_큰_나머지_순(self) -> None:
        # 20 * (5/9, 3/9, 1/9) = 11.11, 6.67, 2.22 → 11, 7, 2
        self.assertEqual(allocate({"A": 50, "B": 30, "C": 10}, 20), {"A": 11, "B": 7, "C": 2})

    def test_값마다_최소_1건(self) -> None:
        q = allocate({"A": 990, "B": 9, "C": 1}, 20)
        self.assertEqual(sum(q.values()), 20)
        self.assertEqual(q["C"], 1)
        self.assertGreaterEqual(q["B"], 1)

    def test_행이_모자라면_있는_만큼(self) -> None:
        self.assertEqual(allocate({"A": 5, "B": 3}, 20), {"A": 5, "B": 3})
        q = allocate({"A": 100, "B": 2}, 20)
        self.assertEqual((sum(q.values()), q["B"]), (20, 1))
        self.assertEqual(allocate({"A": 30, "B": 1}, 20), {"A": 19, "B": 1})


class DrawSamplesTests(unittest.TestCase):
    rows = (rows_for("LEASE_EXEMPTION", {"BOTH": 70, "SHORT_TERM": 20, "LOW_VALUE": 10, "UNDETERMINED": 200})
            + rows_for("DEV_COST_CAPITALIZATION", {"CAPITALIZED": 40, "EXPENSED": 3, "UNDETERMINED": 50}))

    def test_층화_합_20과_UNDETERMINED_제외(self) -> None:
        s = draw_samples(self.rows)
        by = Counter((r["topic"], r["value"]) for r in s)
        self.assertEqual(sum(v for (t, _), v in by.items() if t == "LEASE_EXEMPTION"), 20)
        self.assertEqual(sum(v for (t, _), v in by.items() if t == "DEV_COST_CAPITALIZATION"), 20)
        self.assertEqual(by[("LEASE_EXEMPTION", "LOW_VALUE")], 2)
        self.assertNotIn("UNDETERMINED", {r["value"] for r in s})
        self.assertGreaterEqual(by[("DEV_COST_CAPITALIZATION", "EXPENSED")], 1)

    def test_행_모양(self) -> None:
        r = draw_samples(self.rows)[0]
        for k in ("sample_id", "topic", "rcept_no", "corp_code", "corp_name", "fiscal_year", "value", "evidence",
                  "dart_url", "excerpt"):
            self.assertIn(k, r)
        self.assertEqual(r["dart_url"], f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}")
        ids = [x["sample_id"] for x in draw_samples(self.rows)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_시드_고정_재현(self) -> None:
        a = draw_samples(self.rows)
        b = draw_samples(list(reversed(self.rows)))
        self.assertEqual(a, b)
        c = draw_samples(self.rows, seed=1)
        self.assertNotEqual([r["rcept_no"] for r in a], [r["rcept_no"] for r in c])

    def test_corp_name_없어도_된다(self) -> None:
        rows = [{k: v for k, v in r.items() if k not in ("corp_name", "excerpt")} for r in self.rows]
        s = draw_samples(rows, per_topic=5)
        self.assertEqual((s[0]["corp_name"], s[0]["excerpt"]), ("", ""))


class GateTests(unittest.TestCase):
    samples = draw_samples(rows_for("LEASE_EXEMPTION", {"BOTH": 30, "SHORT_TERM": 10})
                           + rows_for("FVOCI_DESIGNATION", {"DESIGNATED": 12}))

    def answers(self, wrong: int, partial: int = 0) -> dict[str, str]:
        out = {}
        lease = [s["sample_id"] for s in self.samples if s["topic"] == "LEASE_EXEMPTION"]
        for i, sid in enumerate(lease):
            out[sid] = "wrong" if i < wrong else ("partial" if i < wrong + partial else "ok")
        for s in self.samples:
            if s["topic"] == "FVOCI_DESIGNATION":
                out[s["sample_id"]] = "ok"
        return out

    def test_wrong_1은_통과(self) -> None:
        g = gate(self.samples, self.answers(wrong=1, partial=2))
        t = g["topics"]["LEASE_EXEMPTION"]
        self.assertEqual((t["n"], t["ok"], t["partial"], t["wrong"], t["pass"]), (20, 17, 2, 1, True))
        self.assertEqual(len(g["fix_list"]), 2)
        self.assertTrue(all(f["answer"] == "partial" and f["topic"] == "LEASE_EXEMPTION" for f in g["fix_list"]))

    def test_wrong_2는_불통과(self) -> None:
        g = gate(self.samples, self.answers(wrong=2))
        self.assertFalse(g["topics"]["LEASE_EXEMPTION"]["pass"])
        self.assertFalse(g["passed"])

    def test_표본_수_미달은_불통과(self) -> None:
        g = gate(self.samples, self.answers(wrong=0))
        f = g["topics"]["FVOCI_DESIGNATION"]
        self.assertEqual((f["n"], f["wrong"], f["pass"]), (12, 0, False))
        self.assertTrue(g["topics"]["LEASE_EXEMPTION"]["pass"])
        self.assertFalse(g["passed"])

    def test_답이_없는_표본은_n에_안_든다(self) -> None:
        a = self.answers(wrong=0)
        a.pop(next(s["sample_id"] for s in self.samples if s["topic"] == "LEASE_EXEMPTION"))
        t = gate(self.samples, a)["topics"]["LEASE_EXEMPTION"]
        self.assertEqual((t["n"], t["unanswered"], t["pass"]), (19, 1, False))

    def test_잘못된_답_값(self) -> None:
        with self.assertRaises(ValueError):
            gate(self.samples, {self.samples[0]["sample_id"]: "maybe"})
        with self.assertRaises(ValueError):
            gate(self.samples, {"없는-ID": "ok"})


if __name__ == "__main__":
    unittest.main()
