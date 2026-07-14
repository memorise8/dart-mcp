# CLI Commands

이 문서는 `scripts/generate_docs.py`로 생성됩니다.

## 명령 목록

- `dart diagnostics`: 로컬 설정과 사용 가능한 명령/도구를 점검합니다.
- `dart search`: 회사명으로 DART 고유번호를 검색합니다.
- `dart company`: DART 고유번호로 기업 개황 정보를 조회합니다.
- `dart disclosures`: DART 공시 목록을 검색합니다.
- `dart collect-disclosures`: 지정한 기간의 공시를 3개월 이하 창으로 나눠 전체 페이지를 순회하며...
- `dart financial`: 단일회사 주요계정 재무제표를 조회합니다.
- `dart financial-full`: 단일회사 전체 재무제표를 조회합니다.
- `dart indicators`: 단일회사 주요 재무지표를 조회합니다.
- `dart multi-financial`: 여러 회사의 주요계정 재무제표를 한 번에 조회합니다.
- `dart multi-indicators`: 여러 회사의 주요 재무지표를 한 번에 조회합니다.
- `dart shareholders`: 대량보유 상황보고 정보를 조회합니다.
- `dart executive-stock`: 임원·주요주주 소유보고 정보를 조회합니다.
- `dart periodic`: 정기보고서 주요정보를 유형별로 조회합니다.
- `dart event`: 주요사항보고서를 이벤트 유형별로 조회합니다.
- `dart download`: 공시서류 원본파일을 다운로드합니다.
- `dart download-xbrl`: XBRL 재무제표 원본파일을 다운로드합니다.
- `dart taxonomy`: XBRL 택사노미 재무제표 양식을 조회합니다.
- `dart securities`: 증권신고서 주요정보를 조회합니다.
- `dart temis-topic-cases`: 감사보고서 사실(회계감사인)을 TEMIS(finov2)...
- `dart audit-documents`: 공시서류 원본 ZIP에서 감사보고서/연결감사보고서 XML을 추출합니다.
- `dart bulk-audit-documents`: 여러 필링의 감사보고서/연결감사보고서 XML을 일괄 추출하고, 필링별 상태를...
- `dart extract-audit-facts`: `dart bulk-audit-documents`로 받아둔 로컬 감사보고서...
- `dart tag-kam`: `dart extract-audit-facts`가 만든...
- `dart merge-kam-tags`: `dart tag-kam`이 만든 kam_tags.jsonl을 `dart...
- `dart serve`: MCP 서버를 시작합니다.

## 예시

```bash
uv run dart diagnostics
uv run dart search 삼성전자
uv run dart disclosures --corp 삼성전자 --from 20240101 --to 20241231 --type A
uv run dart financial 00126380 2024
uv run dart financial-full 00126380 2024 --fs CFS
uv run dart indicators 00126380 2024 --class M210000
uv run dart download-xbrl 00126380 2024 --report 11011 -o ./downloads
uv run dart taxonomy BS1
uv run dart serve
```
