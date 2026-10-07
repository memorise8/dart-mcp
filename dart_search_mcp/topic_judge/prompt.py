"""WS8 판정 프롬프트(스파이크 `prompt.md` 구조 그대로)."""

from __future__ import annotations

from .topics import TopicDef

_TEMPLATE = """당신은 한국 감사보고서 주석에서 **해당 회사 자신의** 회계처리(정책 입장)를 판정하는 회계 검토자입니다.

[회사] {corp_name}
[주제] {topic_id} — {topic_title}
[선택지]
{values}

[근거 채택 규칙 — 아래에 해당하는 문장은 근거가 될 수 없고, 다른 근거가 없으면 value=UNDETERMINED]
1. 기준서의 일반 규정을 옮겨 적은 문장(예: "~할 수 있습니다", "~하는 경우 ~로 인식합니다"처럼 회사가 실제로 그렇게 했다는 진술이 없는 것).
2. 조건문만 있는 문장("…하는 경우 …한다")으로 회사가 실제로 그 처리를 했다는 진술이 없는 것.
3. 제목·목록 머리말·표 머리(예: "(1) 분류", "다음의 측정 범주로 분류합니다.")만 있는 것.
4. 다른 실체(종속기업·관계기업·피투자회사·합병 상대방·지배기업 등)에 관한 문장.
5. "아직 시행일이 도래하지 않은 / 적용하지 않은 제·개정 기준서" 부분의 문장.
단, 재무제표 본문·표의 계정과목명과 금액(예: 자본 항목 "전환권대가 1,112,489,070")은 회사가 그 처리를 했다는 근거로 쓸 수 있습니다. 연결실체("연결회사", "연결실체", "당사", "회사")가 하는 진술은 이 회사 자신(self)으로 봅니다.

[이 주제의 함정 예시]
{traps}

[출력] 아래 JSON 한 개만 출력합니다. 다른 텍스트 금지.
{{"value": "<선택지 중 하나>", "evidence": "<원문 창에서 그대로 복사한 한 문장(또는 짧은 구간). 판단 근거가 없으면 빈 문자열>", "subject": "self|other|standard_text|conditional|none"}}
- evidence는 원문을 한 글자도 바꾸지 말고 그대로 복사합니다(요약·번역·생략부호 금지).
- subject: self=이 회사가 실제로 한 처리, other=다른 실체, standard_text=기준서 일반규정, conditional=조건문만, none=근거 없음.
- subject가 self가 아니면 value는 UNDETERMINED 입니다.

[원문 창]
<<<
{window}
>>>
"""


def render_prompt(defn: TopicDef, *, corp_name: str, window: str) -> str:
    """주제 정의·회사명·창으로 판정 프롬프트를 만든다.

    `str.format`은 한 번만 적용되므로 회사명·창 안의 중괄호는 그대로 남는다."""
    return _TEMPLATE.format(
        corp_name=corp_name,
        topic_id=defn.code,
        topic_title=defn.title,
        values="\n".join(f"- {v}: {defn.value_help[v]}" for v in defn.values),
        traps="\n".join(f"- {t}" for t in defn.traps),
        window=window,
    )
