# 03 NICE 약식재무(ab08) 재무 DB

**저장소:** dart-search-mcp (산출물·설계) · **상태:** ✅ 상장사 1차 생성 · ⚠️ 생성 스크립트는 현재 Git과 작업트리에 없음

## 무엇인지
DART 재무 API + 우리 감사의견을 결합해 **NICE평가정보 약식재무(ab08) 스키마**의 재무 DB를 만든다.
행=DART 공시 회사, 헤더=NICE 약식재무 항목.

## 핵심 결정 (실측 근거)
- **NICE 데이터 명세**(`nice_data.xlsx`): 12분류/36테이블. DART로 잘 채우는 건 **재무정보·기업개요**뿐(신용/휴폐업/특허/거래처/국민연금/무역 등은 원천 다름 → [04](04-public-data-mcp-ecosystem.md)).
- **전체 재무제표(ab01)는 부적합**: 실측 결과 DART(IFRS)와 NICE 재무계정코드(K-GAAP 카탈로그 2,032개)가 **33%만 일치**(삼성전자 102계정 중 34개). DART 상장사는 전부 K-IFRS 공시, NICE는 옛 K-GAAP/제조업 코드 → 체계 불일치로 1:1 매핑 불가.
- **약식재무(ab08)로 전환**: 집계성 6항목은 깨끗이 매핑되고, **감사의견코드는 우리가 이미 보유**(NICE엔 없는 축).
- 대상 = **상장 ~2,983사(고유)**, DART 재무 API. 비상장 39,658사는 API 미커버(재무는 감사 XML에 있음 → 확장 항목).

## 스키마 매핑
| NICE ab08 | 소스 | 비고 |
|---|---|---|
| 업체코드 | (없음)→**사업자번호·법인번호** | DART `company.json`(bizr_no/jurir_no) |
| 기준년월 | audit_facts fiscal_year+settlement_month | YYYYMM |
| 매출액·순이익·총자산·부채총계·자본금·법인세비용차감전계속사업이익 | DART `fnlttSinglAcnt.json`(주요계정) | **÷1000(원→천원)**, 연결(CFS) 우선·별도(OFS) 폴백 |
| 감사의견코드 | audit_facts.audit_opinion | 적정→10, 한정→20, 부적정→30, 의견거절→40 (**base만**; NICE 서브코드 21/22/31/… 는 우리 데이터에 없음), unknown→"" |

## 재현 상태와 다음 조치
상장사 1차 결과를 만든 `scratchpad/nice_yaksik_export.py`는 당시의 **미커밋 scratchpad
산출물**이며, 현재 이 저장소의 Git과 작업트리에는 없다. 따라서 기존 결과를 같은 명령으로
재생성할 수 있다고 가정하면 안 된다.

- `dart_collected/nice_yaksik_financials.{jsonl,csv}`가 현재 로컬에 있으면 **역사적
  산출물**로만 취급한다. `dart_collected/` 자체도 Git에 포함되지 않는다.
- 재실행이 필요하면 먼저 원본 스크립트를 복구한 뒤, 검증 가능한 저장소 경로에 커밋하거나
  `dart nice-yaksik-export` CLI로 승격한다. 실제 경로·명령·생성 기준 커밋을 확인한 뒤에만
  이 문서를 갱신한다.
- API 호출량은 회사당 `company` + `fnlttSinglAcnt` 2회, 전체 약 6,000회이며 CSV는 UTF-8-sig로
  생성됐다는 과거 실행 기록만 남아 있다.

## 실제 결과
`dart_collected/nice_yaksik_financials.{jsonl,csv}` — 2,983행. 재무값 2,744(92%) / 사업자번호 2,962(99%) / 감사의견코드 2,903(97%).
감사의견 분포: 적정 2,778 / 의견거절 116 / 한정 9 / unknown 80. (239 재무 미보유=정기보고서 미제출/비12월/미제출연도, 정직하게 빈값.)

## 이어서 개발 / 확장
1. **비상장 39,658사 재무**: DART API로는 불가하나 **감사 XML(dart_collected/docs)에 재무제표 첨부**돼 있음 → Phase ①형 재무테이블 파서 신규 개발 → 전수 42k 커버.
2. **재사용 CLI 승격**: 원본 스크립트를 복구·검증한 뒤 `dart nice-yaksik-export --facts ... -o ...` 커밋 기능으로 승격.
3. 감사의견 서브코드: 우리 파서를 확장해 한정/부적정/의견거절 사유를 세분하면 NICE 21/22/31/… 매핑 가능.
