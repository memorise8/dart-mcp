"""WS8 정책 주제 LLM 판정(주제 정의·프롬프트·창 고르기·사후 검사).

기존 4개 주제(`policy_rules`·`policy_llm`)와는 따로 돈다. 판정 로직은 여기에
두고, 실행기는 `complete_json(prompt) -> dict` 하나만 주입한다(스펙 §3).
"""
