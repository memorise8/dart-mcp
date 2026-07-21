# 로컬 아티팩트 인벤토리 — 복원 확인 스냅샷

이 문서는 Git에 넣지 않는 `dart_collected/`의 **로컬 스냅샷**이다. 대용량 원본을 버전 관리하기
위한 문서가 아니며, 다른 환경으로 복사한 데이터가 이 스냅샷과 같은지 확인하는 기준이다.

## 스냅샷 범위

- 확인일: 2026-07-22 (Asia/Seoul)
- 위치: 이 저장소 루트의 `dart_collected/`
- Git 상태: `.gitignore` 대상, 커밋 금지
- 총 크기: 약 15GB
- 감사 XML: 49,979개
- 외부 보관본의 위치·책임자는 아직 이 문서에 등록되지 않았다. 이 스냅샷은 현재 작업트리의
  존재를 보장할 뿐, 별도 백업의 존재를 증명하지 않는다.

## 핵심 산출물

| 파일 | 크기 | 행 수 | SHA-256 |
|---|---:|---:|---|
| `manifest.json` | 20MB | — | `a6f6c0ebeec6adf8e282378de85469d06f351c86bb7c1439835cc1db87dff949` |
| `audit_facts.jsonl` | 79MB | 47,477 | `04116ed0eff1feb3d5a0af43aff403f5d4e56922e605619cffd3582a8467f451` |
| `kam_tags.jsonl` | 781KB | 3,061 | `028feb61f9ef3ddae094b103d4ba5b6a4ab0e3e28e6c40530ee2c655e1a45959` |
| `audit_facts.enriched.jsonl` | 79MB | 47,477 | `ebf3f2006912e0ec6865b450dc554f06fac3f02282d54954ab1fee834bcf1498` |
| `nice_yaksik_financials.jsonl` | 1.2MB | 2,983 | `8ce9bbc597e4f40a7dad19c03c8a3bb7c5f2165713beb0b3753717f7bc0f7e55` |
| `nice_yaksik_financials.csv` | 345KB | — | `ffdbc803a3536e7fb323c6bbe3de9c26f6620f7b3258a0b4518d19abb29d3e88` |

요약 파일의 SHA-256은 다음과 같다.

| 파일 | SHA-256 |
|---|---|
| `audit_facts.jsonl.summary.json` | `62c8b118dc27d1fb23d4e55214d9f48005a191c46d546dd4da90311b7a2ae426` |
| `kam_tags.jsonl.summary.json` | `4dd0d9daf8fa4a3edf76cf06c3d154b82b73b89871348e08989bd2fb69883d8d` |

## 복원 확인

별도 보관본에서 데이터를 복사한 뒤, 필요한 경로·행 수·해시를 이 문서와 대조한다.

```bash
test -f dart_collected/manifest.json
test -d dart_collected/docs
find dart_collected/docs -type f -name '*.xml' -printf '.' | wc -c
wc -l dart_collected/audit_facts.jsonl dart_collected/kam_tags.jsonl \
  dart_collected/audit_facts.enriched.jsonl dart_collected/nice_yaksik_financials.jsonl
sha256sum dart_collected/manifest.json dart_collected/audit_facts.jsonl \
  dart_collected/kam_tags.jsonl dart_collected/audit_facts.enriched.jsonl \
  dart_collected/nice_yaksik_financials.jsonl dart_collected/nice_yaksik_financials.csv
```

신규 수집·증분 작업으로 산출물이 변하면, 원본 데이터는 커밋하지 말고 이 문서의 확인일·크기·행 수·해시를
함께 갱신한다. 파이프라인 의미와 파일별 내용은 [05 데이터셋](05-datasets.md)을 따른다.
