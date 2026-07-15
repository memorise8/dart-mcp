# 설계: 수집된 감사보고서 XML의 구조화 추출 (Option A, Phase ①)

작성일: 2026-07-14
상태: 설계안 (사용자 리뷰 대기)
선행: `.omo/drafts/dart-company-audit-collection.md` (수집), 기존 `temis_export.py` (DartTopicCase 변환)

## 1. 목표와 범위

`dart_collected/docs/`에 수집된 48,373건 감사보고서 XML(14GB)에서 **결정론적 파서**로
핵심 감사 사실을 뽑아, 질의 가능한 구조화 산출물과 기존 temis 파이프라인 입력을 만든다.

- **Phase ① (이번):** 규칙 기반 파서. 비용 0, 전수, 재현 가능.
  추출 대상: 감사인 · 감사의견 유형 · 계속기업 불확실성 여부 · KAM/강조사항 **원문 보존**.
- **Phase ② (다음, 별도):** 보존된 KAM 원문을 LLM으로 주제 태깅. ①의 산출물을
  UPDATE만 하므로 재파싱·재다운로드 없음. 이번 설계는 ②가 공짜로 얹히도록 자리만 마련한다.

범위 밖: LLM 호출(②), temis DB 쓰기(MCP는 JSON 산출만, temis가 적재 — 기존 제약 유지),
재무제표 수치 파싱, 연결/별도 구분 이상의 재무 분석.

## 2. 데이터 근거 (실측)

| corp_cls | 건수 | 비중 | 정기보고서 API | KAM 존재 |
|---|---|---|---|---|
| E(외감 비상장) | 44,773 | 92% | ❌ | 대부분 없음 |
| Y/K/N(상장) | 3,764 | 8% | ✅ | 있음 |

- **API 경로는 8%만 커버** → 로컬 XML 파싱이 92%의 유일한 소스. (전량 다운로드의 근거)
- 감사보고서는 **표준서식**. 실측 확인 사항:
  - 감사의견 문구는 회계기준에 따라 **`공정하게 표시`(일반기업회계기준)** 또는
    `적정하게 표시`(K-IFRS 등) — 파서는 **둘 다** 처리.
  - **감사인명은 이미 manifest `flr_nm`에 존재** (예: `대주회계법인`). XML은 검증용.
  - `핵심감사사항`(KAM)은 상장사에 존재, "이슈 제목 + 핵심감사사항으로 결정된 이유" 서술.
  - ⚠️ **계속기업 오탐 위험:** `계속기업전제`/`계속기업으로서의 존속능력`은 *모든* 보고서의
    "경영진의 책임" 표준 문구에 등장. going_concern 판정은 반드시 **강조 섹션**
    (`계속기업 관련 중요한/중대한 불확실성`, 또는 "계속기업으로서의 존속능력에 ...
    의문을 제기할 수 있는 중요한 불확실성이 존재") landmark로만.

## 3. 아키텍처

```
dart_collected/docs/<rcept>/<rcept>_<acode>.xml   dart_collected/manifest.json
                 │                                          │
                 └───────────────┬──────────────────────────┘
                                 ▼
                  audit_xml_parser.py  (순수·결정론·무네트워크)
                   parse_audit_xml(xml_bytes, meta) -> ParsedAuditReport
                                 │
              ┌──────────────────┴───────────────────┐
              ▼                                       ▼
   (A) 구조화 사실 JSONL                    (B) 어댑터 → AuditReportRecord
   audit_facts.jsonl (1행/1공시)                    │
   + summary manifest                     기존 convert_audit_reports_to_topic_cases()
   ← 사용자가 원한 질의 테이블                        │
   ← ②의 입력(KAM 원문 보존)               DartTopicCase JSON → temis (기존 경로 재사용)
```

- **파서는 순수 함수** (기존 `temis_export`와 동일 원칙: `datetime.now()` 등 부수효과 없음,
  타임스탬프는 호출자 주입). 단위 테스트가 실 XML 픽스처로 각 규칙 검증.
- **하류 재사용:** (B) 어댑터가 `ParsedAuditReport`를 기존 `AuditReportRecord`로 매핑하면
  `convert_audit_reports_to_topic_cases → topic_cases_to_json` 전체가 무수정 재사용된다.
- ⚠️ **(B)는 손실 투영이다:** `AuditReportRecord`(→DartTopicCase)엔 `going_concern`/
  `going_concern_snippet`/`opinion_snippet`/`kam_present`/`stock_code`/`report_name`/
  `rcept_dt`/`settlement_month`/`parse_flags` 필드가 없어 DartTopicCase로 전파되지
  않는다(의도됨). 이 필드들의 원천은 항상 `audit_facts.jsonl`(A) — temis가 이 값들이
  필요하면 (A)를 직접 소비하거나 DartTopicCase 스키마를 확장해야 한다.

## 4. 산출 스키마 (A) — `ParsedAuditReport` / `audit_facts.jsonl`

한 공시(rcept_no) 당 1행.

| 필드 | 출처 | 비고 |
|---|---|---|
| `rcept_no` | manifest | 안정 키 |
| `corp_code`,`corp_name`,`corp_cls`,`stock_code` | manifest | |
| `report_name`,`rcept_dt` | manifest | |
| `category` | manifest | 감사보고서/연결감사보고서/사업보고서 |
| `fiscal_year` (int) | 파생 | **§6 결정 필요** |
| `auditor` | manifest `flr_nm` (+XML 검증) | |
| `audit_opinion` | XML 파싱 | enum: `적정`/`한정`/`부적정`/`의견거절`/`unknown` |
| `opinion_snippet` | XML | 판정 근거 문장(감사의견 문단 발췌) |
| `going_concern` (bool) | XML 강조섹션 landmark | 오탐 방지 규칙 §2 |
| `going_concern_snippet` | XML | 있을 때만, 아니면 "" |
| `kam_present` (bool) | XML | `핵심감사사항` 섹션 존재 |
| `kam_raw` (str) | XML | **원문 보존** (②의 입력) |
| `emphasis_raw` (str) | XML | 강조사항 원문 |
| `kam_tags` (list, nullable) | **①에선 항상 `null`/`[]`** | **②가 채움** — 스키마 자리만 |
| `parse_flags` (obj) | 파서 | 필드별 성공/실패 → 정직한 커버리지 |
| `source_url`,`doc_path` | manifest | |

- JSONL(1행/1레코드) 선택: 48k 스트리밍·부분읽기·resume append 친화.
- `kam_tags` nullable 컬럼을 지금 넣어 ② 때 스키마 변경 없이 UPDATE.

## 5. 파서 규칙 (결정론)

XML은 `<TITLE>`로 대분류만 표시되고 감사의견/KAM은 "독립된 감사인의 감사보고서" 본문의
**텍스트 landmark**로 구분된다. 파서는 태그 제거 후 평문에서 다음 순서 landmark로 구간 분할:

`감사의견` → `감사의견근거` → (`강조사항`) → (`핵심감사사항`) → `재무제표에 대한 경영진과
지배기구의 책임` → `재무제표감사에 대한 감사인의 책임` → (말미 `계속기업 관련 중요한 불확실성`).

- **audit_opinion 분류** (감사의견 문단 내 "우리의 의견으로는 …" 문장 기준):
  - `표시하고 있지 않습니다` → **부적정**
  - `의견을 표명하지 않습니다` / `의견거절` → **의견거절**
  - `제외하고는` + `표시하고 있습니다` → **한정**
  - `공정하게|적정하게 표시하고 있습니다` (부정어 없음) → **적정**
  - 위 어디에도 안 걸리면 `unknown` + `parse_flags.opinion=false` (정직하게 미상 표기)
- **going_concern:** 강조/불확실성 섹션에 계속기업 landmark가 있을 때만 true. 경영진책임
  보일러플레이트는 제외 (§2).
- **auditor:** manifest `flr_nm` 우선. XML 말미 서명("…회계법인"/"…감사반")과 대조,
  불일치 시 `parse_flags.auditor_mismatch=true` 기록(값은 manifest 유지).
- **kam_raw/emphasis_raw:** 해당 landmark~다음 landmark 구간 원문. 없으면 "".
- 서식 이형(제목 변형·구간 누락)으로 특정 필드 실패 시 그 필드만 빈 값 + `parse_flags`.
  전체 실패(감사의견 문단 자체 미발견)는 summary에 집계.

## 6. 결정 (확정 — 2026-07-14 사용자 승인)

1. **`fiscal_year` 규칙 (확정):** **보고기간 종료일의 연도**를 `fiscal_year`로 쓴다.
   `(2025.03) → fiscal_year=2025`, `(2024.12) → fiscal_year=2024`. 추가로 `settlement_month`
   (예: 3, 12)를 함께 저장해 비-12월 결산 회사를 식별 가능하게 한다. report_name 말미
   `(YYYY.MM)` 파싱 실패 시 `rcept_dt`/XML 보고기간 종료일로 폴백, 그래도 실패면 해당 행은
   `fiscal_year` 미상 → DartTopicCase 변환에서만 skip(기존 `_validate_fact` 동작), audit_facts에는
   `parse_flags.fiscal_year=false`로 남긴다.
2. **산출물 범위 (확정):** 이번 v1에서 **둘 다** 낸다 — (A) `audit_facts.jsonl`(1차, 조회 테이블)
   + (B) `dart_topic_cases.json`(어댑터 → 기존 `convert_audit_reports_to_topic_cases` 재사용,
   temis 적재용). (B)는 기존 CLI/변환 재사용이라 저비용.

## 7. 인터페이스 (CLI, 대량이므로 CLI 전용 — 기존 collect/bulk-audit 관례)

```
dart extract-audit-facts \
  --manifest dart_collected/manifest.json \
  --docs-dir dart_collected/docs \
  -o dart_collected/audit_facts.jsonl \
  [--resume] [--limit N] [--corp-cls E,Y,K,N]
```
- rcept별 예외 격리, 체크포인트+resume(기존 `bulk_audit.py` 패턴 재사용),
  실행 파라미터 가드, summary(의견 분포/파싱 실패 집계) 별도 JSON.
- 단건 파서는 순수함수라 MCP tool로도 얇게 노출 가능(선택): `parse_audit_facts(rcept_no)`.

## 8. 테스트 계획

- 파서 단위: 실 XML 픽스처(적정/공정 두 문구, 상장 KAM 있음, 비상장 KAM 없음,
  계속기업 보일러플레이트 오탐 방지) — repo에 소형 픽스처 커밋(원문 일부 발췌, 14GB 미커밋).
- 분류 표: 4개 의견 유형 각각 문구 케이스.
- 어댑터: `ParsedAuditReport → AuditReportRecord` 계약, 기존 temis 테스트 회귀.
- CLI: resume/파라미터가드/예외격리 (기존 bulk 테스트 패턴).

## 9. Phase ② 연결점 (이번엔 구현 안 함, 계약만)

- 입력: `audit_facts.jsonl`의 `kam_raw`가 비지 않은 행 (사실상 상장 8%).
- 출력: 같은 `rcept_no` 행의 `kam_tags` UPDATE (append-only enrichment).
- 비결정성·비용은 ②에 격리. ①의 결정론 산출물은 불변.
```
