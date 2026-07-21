# 재개 Runbook — 새 클론·새 세션 인수인계

이 문서는 `docs/dev/00-overview.md`의 로드맵을 **새 세션 또는 새 작업 환경에서 안전하게
재개**하기 위한 최소 운영 절차다. 기능·설계 설명은 각 Phase 문서를 권위로 삼고, 이 문서는
그 실행 전제(코드, 비추적 자산, 외부 서비스)를 명확히 한다.

## 재개 전 기준

- 문서 세트가 처음 추가된 기준 커밋은 `dc2497628a3b27474ed3226eb2928b0f98993a7b`이다.
  재개 시에는 현재 `main`의 커밋을 기록하고, 이 문서가 설명하는 상태와 다르면 최신 코드·문서를
  우선한다.
- 진입점은 [00 개발 개요](00-overview.md)다. 현재 Phase별 설계·명령·상태는 [01](01-audit-facts-extraction.md),
  [02](02-kam-llm-tagging.md), [03](03-nice-financial-db.md), [04](04-public-data-mcp-ecosystem.md),
  [05](05-datasets.md)에 있다.
- API 키·토큰·사내 경로·개인정보는 이 문서나 Git에 기록하지 않는다.

## 새 클론에서 가능한 기본 검증

```bash
uv sync
uv run python -m unittest -q
uv run dart diagnostics
```

첫 두 명령은 코드와 단위 테스트를 검증한다. `dart diagnostics`는 로컬 설정과 키 설정 여부를
확인하되 키 값을 출력해서는 안 된다. DART 네트워크 호출은 `DART_API_KEY`가 있어야 하며, 키가
없는 상태에서도 mock 기반 테스트는 가능하다.

## 비추적 자산 인수인계 표

| 자산 | Git 상태 | 필요한 작업 | 재개 전 확인 |
|---|---|---|---|
| `dart_collected/` | `.gitignore` 대상, 약 15GB | 신뢰할 수 있는 별도 보관본에서 복사하거나 파이프라인으로 재생성 | `manifest.json`, `docs/`, `audit_facts.jsonl` 등 필요한 입력이 존재하는지 확인 |
| `nice_data.xlsx` | 현재 미추적 | 사용 권한과 별도 보관 위치를 확인 | 라이선스·출처를 확인하고 Git에 무단 추가하지 않음 |
| `nice_yaksik_export.py` | 현재 Git·작업트리에 없음 | 원본을 복구하고 검증 가능한 경로에 버전 관리하거나 CLI로 승격 | 복구 전에는 NICE 약식재무를 재실행하지 않음 |
| `.env` 및 API 키 | Git 제외 | 각 작업자가 적법한 키를 발급·설정 | 값이 로그·명령 이력·산출물에 노출되지 않는지 확인 |

`dart_collected/`는 대용량 원본이므로 커밋하지 않는다. 별도 보관본을 사용할 때는 복사 후 필요한
파일의 행 수와 요약 JSON을 [05 데이터셋](05-datasets.md)의 기록과 대조한다. 보관 위치나 백업
책임자가 바뀌면 이 표에 비밀이 아닌 식별자와 갱신일만 추가한다. 현재 작업트리의 복원 확인값은
[07 로컬 아티팩트 인벤토리](07-local-artifact-inventory.md)에 기록한다.

## 로드맵별 시작 조건

| 작업 | 지금 시작 가능한 범위 | 구현·라이브 완료 전 조건 |
|---|---|---|
| KIPRIS MCP 신설 | 스캐폴드, XML client, mock 테스트 | KIPRIS Plus 키·심의 후 라이브 검증; [04](04-public-data-mcp-ecosystem.md) 절차 준수 |
| 비상장 재무 확장 | 감사 XML 재무테이블 파서 설계·구현 | `dart_collected/docs/` 복원 또는 재수집; NICE 기준자료의 사용 권한 확인 |
| KAM Phase ③ | 태소노미·변환 정책 스펙 작성 | 19개 KAM 태그와 14개 TEMIS 키워드의 매핑, `topic_slug`/`case_id` 안정성, KAM 없는 회사 정책 결정 |
| nts/nps 라이브 검증 | mock 기반 코드 검토 | data.go.kr 키와 각 로컬 저장소, 원격 생성 권한 |

KAM Phase ③은 단순 배선 작업이 아니다. [02 KAM 태깅](02-kam-llm-tagging.md)에 적힌 결정 사항을
먼저 ADR 또는 구현 스펙으로 확정한 뒤 시작한다. TEMIS 파일 기반 소비와 향후 DB import의 현재
경계는 `README.md`의 "TEMIS 연동" 절을 따른다.

## 세션 종료 전 갱신 항목

다음 담당자가 추측하지 않도록, 상태가 바뀌면 관련 Phase 문서와 이 Runbook을 함께 갱신한다.

1. 기준 Git 커밋과 수행한 검증 명령·결과
2. 대용량 데이터 보관본의 비밀이 아닌 식별자, 생성 기간, 행 수 또는 요약 파일
3. NICE 스크립트의 복구 여부와 버전 관리된 실제 경로
4. 외부 키의 발급·라이브 검증 상태(키 값은 기록하지 않음)
5. Phase ③의 확정된 태그 매핑 및 TEMIS 호환성 결정
