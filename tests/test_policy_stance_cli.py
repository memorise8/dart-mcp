import json
import tempfile
import unittest
from pathlib import Path
from dart_search_mcp.tools.policy_stance_cli import (
    PARSER_VERSION,
    PolicyStanceSourceError,
    run_extract_policy_stances,
)

FIX = Path(__file__).parent / "fixtures" / "policy"


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class RunExtractPolicyStancesTests(unittest.TestCase):
    def test_규칙만으로_기준서와_주제_행을_낸다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION", "PPE_MEASUREMENT", "REVENUE_METHOD"), use_llm=False)
            rows = _rows(out)
            by = {(r["rcept_no"], r["topic"], r["subject"]): r for r in rows}
            self.assertEqual(by[("20260001000001", "STD_EARLY_ADOPTION", "1109")]["value"], "EARLY_ADOPTED")
            self.assertEqual(by[("20260001000001", "STD_EARLY_ADOPTION", "1118")]["value"], "NOT_ADOPTED")
            self.assertEqual(by[("20260001000001", "PPE_MEASUREMENT", None)]["value"], "REVALUATION_MODEL")
            self.assertEqual(by[("20260001000002", "STD_EARLY_ADOPTION", "1118")]["value"], "UNDETERMINED")
            self.assertEqual(by[("20260001000002", "REVENUE_METHOD", None)]["value"], "OVER_TIME")
            self.assertTrue(all(r["parser_version"] == PARSER_VERSION for r in rows))
            self.assertTrue(all(r["evidence"] for r in rows))
            self.assertTrue(by[("20260001000002", "STD_EARLY_ADOPTION", "1118")]["evidence"].startswith("판정 불가: "))
            self.assertEqual(summary["reports"], 2)
            self.assertEqual(summary["by_topic"]["STD_EARLY_ADOPTION"]["EARLY_ADOPTED"], 1)
            self.assertTrue((Path(tmp) / "policy_stance.jsonl.summary.json").exists())

    def test_ps_v2_근거_메타_5열이_행에_실린다(self) -> None:
        """T4: 기존 열은 그대로 두고 근거 메타 5열을 덧붙인다(parser_version 은 ps-v2)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=False)
            rows = _rows(out)
            self.assertEqual(PARSER_VERSION, "ps-v2")
            for r in rows:
                for col in ("sentence_kind", "sentence_subject", "held_reason", "evidence_heading_path"):
                    self.assertIn(col, r)
                self.assertIsInstance(r["is_conditional"], bool)
            by = {(r["rcept_no"], r["subject"]): r for r in rows}
            not_adopted = by[("20260001000001", "1118")]
            self.assertEqual(
                (not_adopted["value"], not_adopted["sentence_kind"], not_adopted["sentence_subject"]),
                ("NOT_ADOPTED", "company_statement", "회사"))
            self.assertIn("아직 채택하지 않았으며", not_adopted["evidence"])
            self.assertEqual(not_adopted["held_reason"], "")
            held = by[("20260001000002", "1118")]
            self.assertEqual((held["value"], held["held_reason"]), ("UNDETERMINED", "EVIDENCE_INSUFFICIENT"))

    def test_LLM은_UNDETERMINED_블록에만_호출된다(self) -> None:
        """V7 R2 가드가 들어온 뒤에도 성공 경로(규칙이 못 가른 블록만 LLM 에 가고, 근거
        키워드가 블록에 있어 llm 로 채택됨)를 그대로 확인한다 — 가드가 거부하는 경로는
        test_R2_키워드_가드_거부는_llm_unsupported로_센다 가 별도로 담당한다. 공유 fixture
        (FIX/docs/20260001000002)의 1118호 블록은 "손익계산서에 새로운 중간합계를 도입합니다"
        뿐이라 R2 가드의 NOT_ADOPTED 근거 키워드가 전혀 없어(그래서 가드 거부 테스트가 그
        fixture 를 그대로 쓴다), 이 테스트는 근거 키워드("검토", "영향", "적용")가 있는
        전용 fixture 를 따로 둔다."""
        # T4(2026-09-08): 예전에는 둘째 문장이 "회사는 동 기준서의 적용 영향을 검토하고
        # 있습니다"였는데, 그 문형이 이제 규칙(회사의 미적용 진술)으로 잡혀 LLM 까지 가지
        # 않는다. LLM 경로를 시험하려면 규칙이 못 가르면서 R2 가드 키워드("적용")는 있는
        # 블록이어야 한다.
        xml = (
            "<DOCUMENT><BODY><P>(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 "
            "손익계산서에 새로운 중간합계를 도입합니다. 동 기준서의 적용 범위는 연결재무제표와 "
            "별도재무제표를 모두 포함합니다.</P></BODY></DOCUMENT>"
        )
        calls: list[str] = []
        def fake(messages, **kw):
            calls.append(messages[-1]["content"]); return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            rcept = "20260001000002"
            docs_dir = Path(tmp) / "docs" / rcept
            docs_dir.mkdir(parents=True)
            (docs_dir / f"{rcept}_00760.xml").write_text(xml, encoding="utf-8")
            facts_path = Path(tmp) / "audit_facts.jsonl"
            facts_path.write_text(
                json.dumps({"rcept_no": rcept, "corp_code": "00000002", "fiscal_year": "2025"}) + "\n",
                encoding="utf-8",
            )
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=facts_path, docs_dir=Path(tmp) / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(len(calls), 1)
            self.assertIn("중간합계", calls[0])
            row = next(r for r in _rows(out) if r["rcept_no"] == rcept)
            self.assertEqual((row["value"], row["method"], row["confidence"]), ("NOT_ADOPTED", "llm", 0.6))
            self.assertEqual(summary["llm_calls"], 1)

    def test_LLM_호출이_예외를_던져도_실행이_끝나고_UNDETERMINED로_남는다(self) -> None:
        def boom(messages, **kw):
            raise RuntimeError("gateway timeout")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertEqual(summary["llm_failures"], 1)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_LLM_단계_진입_전에_규칙_결과만으로도_출력이_존재한다(self) -> None:
        def boom(messages, **kw):
            raise RuntimeError("gateway timeout")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertTrue(out.exists())
            rows = _rows(out)
            self.assertTrue(any(r["rcept_no"] == "20260001000001" for r in rows))
            self.assertFalse(out.with_name(out.name + ".tmp").exists())

    def test_출력이_원자적이고_같은_키가_두_번_안_나온다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs",
                                       output_path=out, topics=("STD_EARLY_ADOPTION",), use_llm=False)
            keys = [(r["rcept_no"], r["topic"], r["subject"]) for r in _rows(out)]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertFalse((Path(tmp) / "policy_stance.jsonl.tmp").exists())

    def test_summary_json도_원자적으로_쓴다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs",
                                       output_path=out, topics=("STD_EARLY_ADOPTION",), use_llm=False)
            summary_path = Path(tmp) / "policy_stance.jsonl.summary.json"
            self.assertTrue(summary_path.exists())
            self.assertFalse(summary_path.with_name(summary_path.name + ".tmp").exists())

    def test_limit_0_은_아무것도_처리하지_않는다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=False, limit=0)
            self.assertEqual(summary["reports"], 0)
            self.assertEqual(_rows(out), [])

    def test_XML_없는_보고서는_skipped_no_xml_로_센다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_lines = FIX.joinpath("audit_facts.jsonl").read_text(encoding="utf-8").splitlines()
            facts_lines.append(json.dumps({"rcept_no": "20260001000099", "corp_code": "00000099", "fiscal_year": "2025"}))
            facts_path = Path(tmp) / "audit_facts.jsonl"
            facts_path.write_text("\n".join(facts_lines) + "\n", encoding="utf-8")
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=facts_path, docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=False)
            self.assertEqual(summary["facts"], 3)
            self.assertEqual(summary["reports"], 2)
            self.assertEqual(summary["skipped_no_xml"], 1)

    def test_출력_경로가_입력과_같으면_거부한다(self) -> None:
        with self.assertRaises(PolicyStanceSourceError):
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=FIX / "audit_facts.jsonl",
                topics=("STD_EARLY_ADOPTION",), use_llm=False)

    def test_llm_subjects_가_있으면_그_대상만_LLM_에_보낸다(self) -> None:
        calls: list[str] = []
        def fake(messages, **kw):
            calls.append(messages[-1]["content"]); return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            s = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                                           topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_subjects=frozenset({"9999"}))
            self.assertEqual(len(calls), 0)
            self.assertEqual(s["llm_calls"], 0)
            self.assertEqual(s["llm_subjects"], ["9999"])
            self.assertIsNotNone(s["llm_subjects_note"])
            s2 = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                                            topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_subjects=frozenset({"1118"}))
            self.assertEqual(len(calls), 1)
            self.assertEqual(s2["llm_calls"], 1)

    def test_llm_model_base_url이_judge_with_llm까지_전달된다(self) -> None:
        seen: dict = {}
        def fake(messages, **kw):
            seen["model"] = kw.get("model")
            seen["base_url"] = kw.get("base_url")
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake,
                llm_model="custom-model", llm_base_url="http://gw.local/v1")
            self.assertEqual(seen["model"], "custom-model")
            self.assertEqual(seen["base_url"], "http://gw.local/v1")
            self.assertEqual(summary["llm_model"], "custom-model")
            self.assertEqual(summary["llm_base_url"], "http://gw.local/v1")

    def test_summary에_llm_model_base_url_unanchored_키가_있다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertIn("llm_model", summary)
            self.assertIn("llm_base_url", summary)
            self.assertIn("llm_unanchored", summary)
            self.assertEqual(summary["llm_unanchored"], 0)

    def test_LLM_근거가_블록에_없으면_앵커_실패로_센다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "블록에는 없는 완전히 다른 구절입니다"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_unanchored"], 1)
            self.assertEqual(summary["llm_failures"], 0)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_게이트웨이_오류는_llm_gateway로_센다(self) -> None:
        """S-I1/Q-I1: KamLlmError(게이트웨이 다운)는 llm_unanchored 가 아니라 llm_gateway 로 센다 —
        예전엔 앵커 실패와 구분이 안 돼 "게이트웨이 장애"를 "앵커링 과실"로 오인할 수 있었다."""
        from dart_search_mcp.tools.kam_tagger import KamLlmError

        def boom(messages, **kw):
            raise KamLlmError("gateway down")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=boom)
            self.assertEqual(summary["llm_gateway"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)
            self.assertEqual(summary["llm_failures"], 0)  # KamLlmError는 judge_with_llm 내부에서 삼켜진다 — 예외가 아니다

    def test_파싱_실패는_llm_parse로_센다(self) -> None:
        def fake(messages, **kw):
            return "이것은 JSON이 아닙니다"

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_parse"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)

    def test_R18_가드_거부는_llm_unverified로_센다(self) -> None:
        """긍정 라벨인데 인용구가 단정 패턴과 안 맞아 거부된 경우(R18 가드) — 앵커 실패와는
        다른 사유이므로 llm_unverified 로 별도로 센다."""
        def fake(messages, **kw):
            return json.dumps({"label": "EARLY_ADOPTED", "evidence": "새로운 중간합계를 도입합니다"})

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_unverified"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_R2_키워드_가드_거부는_llm_unsupported로_센다(self) -> None:
        """V7 R2: 비긍정 라벨(NOT_ADOPTED)인데 블록에 그 값을 뒷받침하는 키워드가 하나도
        없으면(공유 fixture 20260001000002 의 1118호 블록은 "손익계산서에 새로운 중간합계를
        도입합니다" 뿐이라 NOT_ADOPTED 근거 키워드가 전혀 없다) 채택하지 않는다 — R18 가드
        (단정 패턴 미검증)와는 다른 사유이므로 llm_unsupported 로 별도로 센다."""
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(summary["llm_unsupported"], 1)
            self.assertEqual(summary["llm_unanchored"], 0)
            self.assertEqual(summary["llm_unverified"], 0)
            row = next(r for r in _rows(out) if r["rcept_no"] == "20260001000002")
            self.assertEqual((row["value"], row["method"]), ("UNDETERMINED", "none"))

    def test_llm_workers_병렬도_같은_결과를_낸다(self) -> None:
        def fake(messages, **kw):
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.jsonl"; b = Path(tmp) / "b.jsonl"
            run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=a,
                                       topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_workers=1)
            s = run_extract_policy_stances(facts_path=FIX / "audit_facts.jsonl", docs_dir=FIX / "docs", output_path=b,
                                           topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake, llm_workers=4)
            self.assertEqual(a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8"))
            self.assertEqual(s["llm_workers"], 4)

    def test_같은_키가_규칙으로_이미_판정됐으면_UNDET_블록이_있어도_LLM_호출_0회_V6_R5(self) -> None:
        """V6 R5: 같은 (rcept_no, topic, subject) 키에 블록이 여러 개일 때, 한 블록이 규칙으로
        판정됐으면(EARLY_ADOPTED) 다른 블록이 UNDETERMINED 라도 그 블록을 LLM 에 보낼 필요가
        없다 — 최종 by_key 값이 이미 확정됐기 때문이다."""
        xml = (
            "<DOCUMENT><BODY><P>2. 중요한 회계정책</P>"
            "<P>(3) 기업회계기준서 제1109호 '금융상품' 개정 위험회피회계 요건을 변경합니다. "
            "조기적용이 허용됨에 따라 당사는 동 기준서를 조기적용하였습니다.</P>"
            "<P>(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 기업회계기준서 제1118호는 "
            "제1001호 '재무제표 표시'를 대체합니다. 회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다.</P>"
            "<P>(9) 기업회계기준서 제1109호 '금융상품' 추가 개정 위험회피회계 표시를 보완합니다. "
            "손익계산서에 새로운 중간합계를 도입합니다.</P></BODY></DOCUMENT>"
        )
        calls: list[str] = []

        def fake(messages, **kw):
            calls.append(messages[-1]["content"])
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})

        with tempfile.TemporaryDirectory() as tmp:
            rcept = "20260005000001"
            docs_dir = Path(tmp) / "docs" / rcept
            docs_dir.mkdir(parents=True)
            (docs_dir / f"{rcept}_00760.xml").write_text(xml, encoding="utf-8")
            facts_path = Path(tmp) / "audit_facts.jsonl"
            facts_path.write_text(
                json.dumps({"rcept_no": rcept, "corp_code": "00000009", "fiscal_year": "2025"}) + "\n",
                encoding="utf-8",
            )
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=facts_path, docs_dir=Path(tmp) / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(len(calls), 0)
            self.assertEqual(summary["llm_calls"], 0)
            self.assertEqual(summary["llm_pending_raw"], 1)
            row = next(r for r in _rows(out) if r["subject"] == "1109")
            self.assertEqual((row["value"], row["method"]), ("EARLY_ADOPTED", "rule"))

    def test_같은_키에_UNDET_블록_두_개면_가장_긴_블록만_LLM에_보낸다_V6_R5(self) -> None:
        """V6 R5: 같은 키에 UNDETERMINED 블록이 둘이면(규칙으로 못 가른 채 남으면) LLM 호출은
        1회만 — 그중 가장 긴 블록 하나만 보낸다."""
        filler0 = "당사는 매 분기 정기 이사회를 통해 내부통제 절차를 점검하고 그 결과를 문서로 남깁니다. " * 20
        filler1 = "이 부분은 후속 보완 설명이 이어지는 참고 문단으로 별도의 판단 근거가 되지 않는 일반 서술입니다. " * 25
        xml = (
            "<DOCUMENT><BODY><P>2. 중요한 회계정책</P>"
            "<P>(3) 기업회계기준서 제1101호 '한국채택국제회계기준의 최초채택' 개정 최초채택 관련 규정을 보완합니다. "
            "이 개정은 손익계산서 표시 방법을 조정합니다. " + filler0 + "</P>"
            "<P>(9) 기업회계기준서 제1101호 '한국채택국제회계기준의 최초채택' 추가 개정 최초채택 관련 규정을 재차 "
            "보완하며 주석 공시 항목을 세분화하고 비교 표시 방법을 명확히 하는 내용을 포함하는 종합적인 개정입니다. "
            "이 부분은 특유마커B 참고 사항입니다. " + filler1 + "</P></BODY></DOCUMENT>"
        )
        calls: list[str] = []

        def fake(messages, **kw):
            calls.append(messages[-1]["content"])
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "특유마커B 참고 사항"})

        with tempfile.TemporaryDirectory() as tmp:
            rcept = "20260005000002"
            docs_dir = Path(tmp) / "docs" / rcept
            docs_dir.mkdir(parents=True)
            (docs_dir / f"{rcept}_00760.xml").write_text(xml, encoding="utf-8")
            facts_path = Path(tmp) / "audit_facts.jsonl"
            facts_path.write_text(
                json.dumps({"rcept_no": rcept, "corp_code": "00000010", "fiscal_year": "2025"}) + "\n",
                encoding="utf-8",
            )
            out = Path(tmp) / "policy_stance.jsonl"
            summary = run_extract_policy_stances(
                facts_path=facts_path, docs_dir=Path(tmp) / "docs", output_path=out,
                topics=("STD_EARLY_ADOPTION",), use_llm=True, call_fn=fake)
            self.assertEqual(len(calls), 1)
            self.assertIn("특유마커B", calls[0])
            self.assertEqual(summary["llm_calls"], 1)
            self.assertEqual(summary["llm_pending_raw"], 2)


class CliLlmWiringTests(unittest.TestCase):
    """Q-I6: --llm-model/--llm-base-url 의 CLI 배선은 지금까지 어떤 테스트도 지나지 않았다.
    tag-kam 테스트 관례(httpx.Client 를 소켓 없는 spy 로 교체)를 그대로 따라, CLI 옵션이
    실제 HTTP 호출(모델명·URL)까지 실제로 이어지는지 CliRunner 로 끝까지 확인한다."""

    def test_llm_model_base_url_옵션이_실제_HTTP_호출까지_배선된다(self) -> None:
        import httpx
        import server  # noqa: F401  — 레포 관례: server -> cli -> dart_search_mcp.tools.* 순서로 import
        import cli
        from click.testing import CliRunner

        captured: dict = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {"message": {"content": json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})}}
                    ]
                }

        class _FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *exc_info) -> bool:
                return False

            def post(self, url: str, json: dict):
                captured["url"] = url
                captured["model"] = json.get("model")
                return _FakeResponse()

        original_client = httpx.Client
        httpx.Client = _FakeClient  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "s.jsonl"
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    [
                        "extract-policy-stances",
                        "--facts", str(FIX / "audit_facts.jsonl"),
                        "--docs", str(FIX / "docs"),
                        "-o", str(out),
                        "--topics", "STD_EARLY_ADOPTION",
                        "--llm",
                        "--llm-model", "custom-model-x",
                        "--llm-base-url", "http://custom-gw.local/v1",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(captured["model"], "custom-model-x")
                self.assertEqual(captured["url"], "http://custom-gw.local/v1/chat/completions")
                self.assertIn("모델 custom-model-x", result.output)
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


class CliConsoleGatewayVisibilityTests(unittest.TestCase):
    """재리뷰 N5: 게이트웨이가 전면 장애(전부 KamLlmError)라도 llm_failures=0(예외가 아니라
    judge_with_llm 내부에서 삼켜짐)·llm_unanchored=0 이라 콘솔에 그 사실이 전혀 안 보였다 —
    summary json 을 열어야만 알 수 있었다. 콘솔 한 줄에 게이트웨이·파싱 실패 건수를 덧붙인다."""

    def test_게이트웨이_전면_장애가_콘솔에_보인다(self) -> None:
        import httpx
        import server  # noqa: F401
        import cli
        from click.testing import CliRunner

        class _DownClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_DownClient":
                return self

            def __exit__(self, *exc_info) -> bool:
                return False

            def post(self, url: str, json: dict):
                raise httpx.ConnectError("실제 네트워크 없음 - 테스트가 의도적으로 발생시킨 예외")

        original_client = httpx.Client
        httpx.Client = _DownClient  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "s.jsonl"
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    [
                        "extract-policy-stances",
                        "--facts", str(FIX / "audit_facts.jsonl"),
                        "--docs", str(FIX / "docs"),
                        "-o", str(out),
                        "--topics", "STD_EARLY_ADOPTION",
                        "--llm",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("게이트웨이 실패 1", result.output)
                self.assertIn("파싱 실패 0", result.output)
                self.assertIn("실패 0", result.output)  # llm_failures — 예외가 아니라 정상 흐름
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


class CliConsoleUnsupportedVisibilityTests(unittest.TestCase):
    """V7 R2: 비긍정 라벨 키워드 가드(unsupported)로 거부된 건수도 콘솔 요약에 보여야 한다."""

    def test_근거어_없음_건수가_콘솔에_보인다(self) -> None:
        import httpx
        import server  # noqa: F401
        import cli
        from click.testing import CliRunner

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {"message": {"content": json.dumps({"label": "NOT_ADOPTED", "evidence": "중간합계"})}}
                    ]
                }

        class _FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *exc_info) -> bool:
                return False

            def post(self, url: str, json: dict):
                return _FakeResponse()

        original_client = httpx.Client
        httpx.Client = _FakeClient  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "s.jsonl"
                runner = CliRunner()
                result = runner.invoke(
                    cli.cli,
                    [
                        "extract-policy-stances",
                        "--facts", str(FIX / "audit_facts.jsonl"),
                        "--docs", str(FIX / "docs"),
                        "-o", str(out),
                        "--topics", "STD_EARLY_ADOPTION",
                        "--llm",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("근거어 없음 1", result.output)
        finally:
            httpx.Client = original_client  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
