"""회계정책 주석 문장의 분해·분류와 목록 서문(preamble) 판별.

왜 별도 모듈인가 — 회계사 확인(2026-09-08, P1·P2)에서 판정값이 아니라 **근거 문장 선택**이
틀린 축으로 지목됐다: 규칙이 기준서 설명문("동 기준서는 2027년 …부터 적용되며 조기적용이
허용됩니다")을 근거로 잡고, 바로 뒤에 있는 회사 문장("연결실체는 … 영향을 검토 중에
있습니다")을 놓쳤다. 그래서 "누가(주어) 무엇을(서술) 말한 문장인가"를 먼저 가르고, 그 위에서
근거를 고른다. `policy_rules` 가 700줄을 넘지 않도록 분류기만 여기로 뺀다(분류 규칙은
`classify_sentence` 하나, 판정 규칙은 policy_rules 하나 — 경계가 분명하다).

목록 서문 귀속도 같은 뿌리다. 「2.2.2 … 조기 적용하지 아니한 기준서는 다음과 같습니다」 는
**뒤에 오는** 항목의 서문인데, 블록이 앞 항목에서 끝나지 않고 이 문장을 삼키면 앞 항목
(당기 신규 적용 목록에 있는 기준서)이 미적용으로 뒤집힌다(실측: 올래디오 20260327000721,
제1117호). 서문은 뒤 항목에만 붙이고(=앞 항목의 근거 후보에서 뺀다), 항목은 자기 앞의
가장 가까운 서문에 귀속시킨다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 문장 끝 — 합쇼체/평서체 종결 어미 + 마침표. 어미를 요구하므로 점 날짜("2027. 1. 1.")나
# 소수점은 문장 경계로 잡히지 않는다. 마침표 뒤 공백은 선택이다 — 원문 XML 을 평문화하면
# 태그 자리가 사라져 "…포함합니다.동 기준서는 …"처럼 공백 없이 붙는 경우가 흔하다(실측:
# 네오팜 20260316001522). 공백을 요구하면 그 두 문장이 하나로 뭉쳐 근거가 설명문에서
# 시작해 버린다.
_SENT_END_RE = re.compile(r"(?:다|음|함)\.\s*")

# 문장의 주어 후보 — 첫 번째로 나오는 것을 그 문장의 주어로 본다. "동 기준서는 회사의
# 재무제표에 …" 처럼 회사가 관형어로만 나오는 문장을 회사 진술로 오분류하지 않으려면
# "가장 먼저 나오는 주격/주제격 명사"를 봐야 한다(조사 은/는/이/가를 요구한다 — "회사의"는
# 주어가 아니다).
_SUBJECT_RE = re.compile(
    r"(연결실체|연결회사|연결기업|연결집단|그룹|당사|회사|경영진|당기업"
    r"|동\s*기준서|본\s*기준서|이\s*기준서|해당\s*기준서|새로운\s*기준서|동\s*개정사항|해당\s*개정사항"
    r"|해당\s*개정|동\s*개정|개정사항|기준서|해석서)\s*(?:은|는|이|가)(?![가-힣])"
)
_COMPANY_SUBJECTS = frozenset({"당사", "회사", "경영진", "당기업"})
_GROUP_SUBJECTS = frozenset({"연결실체", "연결회사", "연결기업", "연결집단", "그룹"})
# 회사(또는 연결실체)가 주어인 자리. 위 두 어휘 집합에서 그대로 유도한다 — 회사 주어 목록이
# 여기 하나뿐이어야 다른 모듈(policy_rules_measurement 의 절 경계 판별)이 같은 것을 본다.
# 긴 이름을 먼저 시도해야 "연결회사"가 "회사"로 잘리지 않는다.
COMPANY_SUBJECT_RE = re.compile(
    r"(?:" + "|".join(sorted(_COMPANY_SUBJECTS | _GROUP_SUBJECTS, key=len, reverse=True))
    + r")\s*(?:은|는|이|가)(?![가-힣])"
)

SUBJECT_COMPANY = "회사"
SUBJECT_GROUP = "연결실체"
SUBJECT_STANDARD = "기준서"
SUBJECT_UNKNOWN = "불명"

KIND_COMPANY = "company_statement"
KIND_STANDARD = "standard_description"
KIND_CONDITIONAL = "conditional"
KIND_PREAMBLE = "list_preamble"
KIND_OTHER = ""

# 기준서가 주어일 때 "기준서 설명문"으로 보는 서술어. 브리프 지정(적용됩니다/허용됩니다/
# 요구합니다/대체합니다/정의하고) + 같은 부류의 실측 변형(개정/도입/포함/명확히).
_DESCRIPTION_VERB_RE = re.compile(
    r"적용(?:됩니다|되며|되고|되는|될|할|하되|해야|하여야|합니다)|허용(?:됩니다|되며|된|됨)|가능합니다"
    r"|요구(?:합니다|하고|됩니다|하며)|대체(?:합니다|하며|할)|정의(?:하고|합니다)"
    r"|도입(?:합니다|하며|한)|포함(?:합니다|하며)|개정(?:되었습니다|하였습니다)|명확히\s*하"
)
# 조건절 표지 — 「…하는 경우」「다만 …의 경우」. 조건절은 회사의 확정 진술이 아니다.
_CONDITIONAL_RE = re.compile(r"하는\s*경우|경우에는|다만[^.]{0,40}경우|하기로\s*선택하는")

# 목록 서문 — 뒤따르는 항목 목록을 여는 문장.
_LIST_INTRO_RE = re.compile(r"다음(?:과\s*같|의|은)")
# 서문의 극성. 채택(당기 신규 적용) 서문 / 미채택(시행일 미도래·조기적용 안 함) 서문.
PREAMBLE_ADOPTED = "adopted"
PREAMBLE_NOT_ADOPTED = "not_adopted"
# 서문 후보는 찾았지만 그 서문이 항목과 **다른 절**에 있어 귀속을 확정할 수 없는 상태
# (Fix round 1, Important 2). 값을 주지 않고 보류 사유(ATTRIBUTION_UNCLEAR)로 넘긴다.
PREAMBLE_SECTION_MISMATCH = "section_mismatch"
_PREAMBLE_NOT_ADOPTED_RE = re.compile(
    r"(?:조기\s*)?(?:적용|채택|도입)하지\s*(?:않은|않는|아니한|아니하였|않았)"
    r"|시행일이\s*도래하지\s*(?:않|아니)|아직\s*시행일이|공표되었으나\s*아직"
)
_PREAMBLE_ADOPTED_RE = re.compile(
    r"(?:신규로|새로이|새로)\s*적용(?:하였|하고|한)|적용한\s*제\s*ㆍ?\s*개정|채택한\s*제\s*ㆍ?\s*개정"
    r"|당기(?:부터)?\s*(?:새로|신규로)\s*적용"
)


@dataclass(frozen=True, slots=True)
class Sentence:
    """분류된 문장 하나. `start` 는 분해 대상 텍스트 안에서의 시작 위치다."""

    start: int
    text: str
    kind: str
    subject: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _subject_of(sentence: str) -> tuple[str, str]:
    """(원형 주어 토큰, 정규화된 주어). 주어를 못 찾으면 ("", "불명")."""
    m = _SUBJECT_RE.search(sentence)
    if m is None:
        return "", SUBJECT_UNKNOWN
    raw = re.sub(r"\s+", "", m.group(1))
    if raw in _COMPANY_SUBJECTS:
        return raw, SUBJECT_COMPANY
    if raw in _GROUP_SUBJECTS:
        return raw, SUBJECT_GROUP
    return raw, SUBJECT_STANDARD


def preamble_polarity(sentence: str) -> str:
    """목록 서문의 극성 — `PREAMBLE_ADOPTED` | `PREAMBLE_NOT_ADOPTED` | "" (서문이 아니거나 중립).

    중립 서문("주요 개정내용은 다음과 같습니다", "…예상되는 주요 회계정책은 다음과 같습니다")은
    ""를 돌려준다 — 그런 문장은 뒤 항목의 적용 여부를 규정하지 않으므로 근거 후보에서 뺄
    이유도, 앞 항목을 귀속시킬 이유도 없다."""
    if _LIST_INTRO_RE.search(sentence) is None:
        return ""
    # 미채택 서문을 먼저 본다 — "채택하지 않은"에는 "채택한"이 부분 문자열로 들어 있다.
    if _PREAMBLE_NOT_ADOPTED_RE.search(sentence):
        return PREAMBLE_NOT_ADOPTED
    if _PREAMBLE_ADOPTED_RE.search(sentence):
        return PREAMBLE_ADOPTED
    return ""


def classify_sentence(sentence: str) -> tuple[str, str]:
    """(kind, subject). kind 는 company_statement|standard_description|conditional|list_preamble|""("" =미분류).

    우선순위: 목록 서문 → 조건절 → 회사 진술 → 기준서 설명문 → 미분류. 서문이 가장 앞인 이유는
    서문이 "이 문장은 뒤 항목의 것"이라는 귀속 정보를 담기 때문이고(회사가 주어인 서문도 있다 —
    "연결회사는 … 다음의 기준서를 조기적용하지 아니하였습니다"), 조건절이 그다음인 이유는
    조건절 안의 회사 언급이 회사의 확정 진술이 아니기 때문이다."""
    _raw, subject = _subject_of(sentence)
    if _LIST_INTRO_RE.search(sentence) is not None and re.search(r"(?:다|음|함)\.\s*$", sentence.strip()):
        return KIND_PREAMBLE, subject
    if _CONDITIONAL_RE.search(sentence) is not None:
        return KIND_CONDITIONAL, subject
    if subject in (SUBJECT_COMPANY, SUBJECT_GROUP):
        return KIND_COMPANY, subject
    if subject == SUBJECT_STANDARD and _DESCRIPTION_VERB_RE.search(sentence) is not None:
        return KIND_STANDARD, subject
    return KIND_OTHER, subject


def split_sentences(text: str) -> list[Sentence]:
    """text 를 문장으로 자르고 각 문장을 분류한다. 문장 경계가 하나도 없으면 전체가 문장 하나다.

    반환된 문장들의 [start, end) 는 text 를 빈틈없이 덮는다 — 호출자가 매치 위치(pos)로
    문장을 되찾을 수 있어야 하기 때문이다."""
    out: list[Sentence] = []
    pos = 0
    for m in _SENT_END_RE.finditer(text):
        seg = text[pos : m.end()]
        if seg.strip():
            kind, subject = classify_sentence(seg)
            out.append(Sentence(pos, seg, kind, subject))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        kind, subject = classify_sentence(tail)
        out.append(Sentence(pos, tail, kind, subject))
    return out


# 절 번호 표제("2.2.2 회사가 적용하지 않은 …") — 블록의 상위 절 경로이자 블록 종료 경계다.
# **정본은 이 하나**다: `policy_blocks` 도 이 패턴을 그대로 쓴다(Fix round 2). 경계 판단과 절
# 조회가 다른 패턴을 쓰면, 한쪽만 표제를 못 보고도 두 값이 "일치"해 서문 귀속 검사가 조용히
# 통과한다(아래 lookbehind 결함이 정확히 그 모양이었다).
#
# lookbehind 는 **숫자**와 **숫자+마침표**(중첩 번호 "1.2.2.2" 의 안쪽 조각)만 막는다.
# Fix round 2(1차 리뷰 잔여): 예전 `(?<![\d.])` 는 앞이 마침표이기만 하면 막아서, 평문화 과정에서
# 마침표 뒤 공백이 사라진 "…추가하였습니다.2.2.2공표되었으나 아직 시행되지 않은 기준서" 의
# 절 표제를 통째로 놓쳤다(같은 공백 소실이 문장 분해에서도 실재한다 — 네오팜
# "…포함합니다.동 기준서는…"). 그러면 2.2.2 항목의 절이 2.2.1 로 읽히고, 앞 절 서문의 절도
# 2.2.1 이라 **거짓 일치**로 귀속 검사를 통과해 항목이 ADOPTED 로 뒤집힌다.
# 뒤쪽 조건(단위 명사 배제 + 한글 두 자)은 소수("1.5 배로", "2.3 배")를 표제로 오인하지 않기 위한 것.
SECTION_RE = re.compile(
    r"(?<!\d)(?<!\d\.)(\d{1,2}(?:\.\d{1,2}){1,3})\s*"
    r"(?!(?:배|억|만|천|원|개|명|년|월|일|시간|퍼센트|포인트|미터|톤|리터|프로)(?![가-힣]))"
    r"(?=[가-힣]{2})"
)
SECTION_WINDOW = 3000


def find_section(text: str, pos: int, window: int = SECTION_WINDOW) -> str:
    """pos 앞의 가장 가까운 절 번호("2.2.2"). 없으면 ""."""
    lo = max(0, pos - window)
    found = ""
    for m in SECTION_RE.finditer(text, lo, pos):
        found = m.group(1)
    return found


LEAD_WINDOW = 2000

# 목록 표제형 서문 — "2.2.1 회사가 채택한 제ㆍ개정 기준서" 처럼 문장(마침표)이 아니라 표제로만
# 목록의 성격을 밝히는 형태다. 실측(20260310002005 등)에서 2.2.1 절이 표제뿐이고 "다음의 …
# 신규로 적용하였습니다" 문장이 아예 없는 보고서가 있어, 문장 서문만 보면 그 절의 항목들이
# 전부 판단 보류로 남는다.
_HEADING_LEAD_RE = re.compile(
    r"(?:\(\s*\d{1,2}\s*\)|\d{1,2}(?:\.\d{1,2}){1,3}|\d{1,2}\)|[가-힣]\.)\s*"
    r"[^.]{0,25}?(?:조기\s*적용하지\s*(?:않은|아니한)|적용하지\s*(?:않은|아니한)|채택하지\s*않은"
    r"|신규\s*적용한|신규로\s*적용한|채택한|적용한)\s*"
    r"[^.]{0,15}?(?:기준서|해석서|회계기준)"
)
# 한글+마침표 항목 기호("가.", "나.", "다.")는 종결 어미와 생김새가 같다 — 평문에서 마침표
# 뒤 공백이 사라지면 "…있다.회사가 리스제공자로서 비교기간에 적용한 회계정책은 기업회계기준…"
# 의 "다." 가 항목 기호로 잡혀 문장 한가운데가 표제형 서문이 된다(실측 20250827000159/1115,
# Fix round 1 Important 2). 이 기호로 시작하는 표제만 "문장 첫머리" 를 요구한다 — 숫자 기호
# ("2.2.1", "(1)")는 앞 낱말에 붙어 나오는 일이 흔해서(예: "…공시사항의 변경2.2.1 …") 같은
# 조건을 걸 수 없다.
_HANGUL_DOT_MARKER_RE = re.compile(r"^[가-힣]\.")


def _heading_start_ok(chunk: str, match: re.Match[str]) -> bool:
    """한글+마침표 기호로 시작하는 표제형 서문만 문장 첫머리를 요구한다(위 주석 참고)."""
    if _HANGUL_DOT_MARKER_RE.match(match.group(0)) is None:
        return True
    before = chunk[: match.start()].rstrip()
    return not before or before.endswith(".")


def find_lead(
    text: str, pos: int, *, section: str = "", window: int = LEAD_WINDOW
) -> tuple[str, str] | None:
    """pos 앞의 가장 가까운 **극성 있는** 목록 서문 (서문, 극성). 없으면 None.

    항목(블록)이 어느 목록에 속하는지는 그 항목 **앞** 서문이 정한다. 중간에 다른 항목들이
    끼어 있어도 상관없다 — 같은 목록의 형제 항목들이기 때문이다. 반대로 새 목록이 시작되면
    그 서문이 더 가까우므로 자연히 그쪽이 이긴다. `window` 는 엉뚱하게 먼 서문에 귀속되는
    것을 막는 상한이다(실측 표본에서 서문-항목 거리는 최대 1,000자 남짓).

    문장형 서문과 표제형 서문이 같은 자리(=한 문장 안에 표제와 서문 문장이 붙어 있는 흔한
    모양)를 가리키면 문장형을 쓴다 — 근거로 더 많은 것을 말해 준다. 표제형은 그 문장 **뒤**에
    새 목록이 열릴 때만 이긴다.

    `section`(항목이 속한 절 번호)이 주어지면 서문이 **같은 절** 안에 있어야 한다
    (Fix round 1, Important 2). 다음 절의 표제가 인식되지 않는 형태일 때 — 예: "2.2.2 공표
    되었으나 아직 시행되지 않은 기준서" 처럼 극성 어휘가 없는 표제 — 거리만 보면 앞 절(2.2.1)의
    채택 서문이 그대로 딸려 와 항목이 ADOPTED 로 뒤집힌다. 절이 다르면 값을 주지 않고
    `PREAMBLE_SECTION_MISMATCH` 를 돌려준다(보류 사유로 남긴다)."""
    lo = max(0, pos - window)
    chunk = text[lo:pos]
    best: tuple[str, str] | None = None
    best_end = 0  # 지금까지 채택한 서문이 chunk 안에서 끝나는 자리
    for sent in split_sentences(chunk):
        polarity = preamble_polarity(sent.text)
        if polarity:
            best = (sent.text.strip(), polarity)
            best_end = sent.end
    for m in _HEADING_LEAD_RE.finditer(chunk):
        if best is not None and m.start() < best_end:
            continue
        if not _heading_start_ok(chunk, m):
            continue
        heading = m.group(0).strip()
        polarity = PREAMBLE_NOT_ADOPTED if _PREAMBLE_NOT_ADOPTED_RE.search(heading) else PREAMBLE_ADOPTED
        best = (heading, polarity)
        best_end = m.end()
    if best is None:
        return None
    # 서문의 절은 그 서문이 **끝나는** 자리에서 읽는다 — 서문 문장은 절 표제를 앞에 달고
    # 시작하는 일이 흔해서("2.2 회계정책과 공시의 변경 2.2.1 회사가 채택한 … 적용하였습니다.")
    # 시작 위치에서 읽으면 한 단계 앞 절이 나온다.
    if section and find_section(text, lo + best_end) != section:
        return ("", PREAMBLE_SECTION_MISMATCH)
    return best
