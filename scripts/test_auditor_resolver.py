"""감사인 리졸버 — §13.4 실측 케이스를 그대로 재현한다."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditor_resolver import clean_firm, extract_tail_firm, is_firm, resolve_auditor


def test_회계법인과_감사반만_감사인이다():
    assert is_firm("대주회계법인") is True
    assert is_firm("성림공인회계사감사반(제503호)") is True
    assert is_firm("주식회사 케이엘넷") is False
    assert is_firm("") is False


def test_자간이_벌어진_서명을_정리한다():
    assert clean_firm("한 빛 회 계 법 인") == "한빛회계법인"


def test_주소_잔재를_머리에서_뗀다():
    # §13.4 실측 꼬리 케이스: '태화빌딩태율회계법인'
    assert clean_firm("태화빌딩태율회계법인") == "태율회계법인"


def test_대표이사_꼬리를_뗀다():
    # §13.4 실측 꼬리 케이스: '회계법인 ○○ 대표이사 김…' 어순
    assert clean_firm("회계법인공의대표이사김수현") == "회계법인공의"


def _doc(sig: str) -> str:
    return (
        "<DOCUMENT>"
        "<TITLE>독립된 감사인의 감사보고서</TITLE>"
        "<P>우리는 재무제표를 감사하였습니다.</P>"
        "<P>서울특별시 강남구 테헤란로 123</P>"
        f"<P>{sig}</P>"
        "<TITLE>재 무 상 태 표</TITLE><P>자산총계</P>"
        "</DOCUMENT>"
    )


def test_섹션_최하단에서_상호를_찾는다():
    assert extract_tail_firm(_doc("대 주 회 계 법 인 대표이사 홍길동")) == "대주회계법인"


def test_감사반도_찾는다():
    assert extract_tail_firm(_doc("성림공인회계사감사반(제503호)")) == "성림공인회계사감사반(제503호)"


def test_섹션이_없으면_문서끝에서_찾는다():
    xml = "<DOCUMENT><P>본문</P><P>삼정회계법인</P></DOCUMENT>"
    assert extract_tail_firm(xml) == "삼정회계법인"


def test_flr_nm이_법인이면_그대로_쓴다():
    resolved, source = resolve_auditor("<DOCUMENT></DOCUMENT>", "삼일회계법인")
    assert (resolved, source) == ("삼일회계법인", "flr_nm")


def test_flr_nm이_회사명이면_섹션에서_복구한다():
    resolved, source = resolve_auditor(_doc("대주회계법인"), "주식회사 케이엘넷")
    assert resolved == "대주회계법인"
    assert source == "section_tail"


def test_아무데서도_못찾으면_unresolved():
    resolved, source = resolve_auditor("<DOCUMENT><P>본문뿐</P></DOCUMENT>", "주식회사 어딘가")
    assert resolved == ""
    assert source == "unresolved"
