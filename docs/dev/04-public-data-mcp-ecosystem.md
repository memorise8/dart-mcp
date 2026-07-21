# 04 공공데이터 MCP 생태계 (휴폐업·국민연금·특허)

NICE 데이터 중 DART가 못 채우는 것을 공개 API로 보완하는 **독립 MCP 저장소**들. 각 원천 = 별도 저장소
(바운디드 컨텍스트·키·생명주기 분리). 조인은 **사업자번호**(DART 기업개황에서 확보)로 소비자가 수행.

## nts-status-mcp — 국세청 사업자 휴폐업 ✅ 완성(로컬)
`/data_raid/ruci_workspace/repo/mcp/nts-status-mcp` · 63 테스트 · 4 CLI + 1 MCP도구
- **API**: `POST https://api.odcloud.kr/api/nts-businessman/v1/status?serviceKey=<KEY>`, body `{"b_no":[10자리…]}`(최대100). data.go.kr/data/15081808, 자동승인·무료, 100만/일.
- **사업자번호 직접조회**(핵심키). 반환: `b_stt`(계속/휴업/폐업), `b_stt_cd`(01/02/03), `tax_type`, `end_dt`(폐업일).
- CLI: `nts status <사업자번호…>`, `nts bulk-status --input <파일> -o results.jsonl [--field][--resume]`, `nts serve`, `nts diagnostics`. MCP: `check_business_status`.
- 정직성: 무효형식·미등록·배치실패·source_skipped 전부 행/summary에 표기(조용한 드롭 금지). 배치100·resume 크래시 자가복구.
- 키 env: `DATA_GO_KR_API_KEY`(redact). **라이브 검증은 키 발급 후**(구현은 mock 테스트만).

## nps-mcp — 국민연금 가입 사업장 ✅ 완성(로컬)
`/data_raid/ruci_workspace/repo/mcp/nps-mcp` · 51 테스트 · 5 CLI + 3 MCP도구
- **API**: `http://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2` (HTTP, GET, dataType=json, resultCode "00"). data.go.kr/data/3046071, 자동승인·무료.
- **3 오퍼레이션**: `getBassInfoSearchV2`(목록조회→seq) → `getDetailInfoSearchV2`(seq→가입자수 jnngpCnt·월보험료 crrmmNtcAmt·업종·등록/탈퇴일) → `getPdAcctoSttusInfoSearchV2`(seq→월별 취득 nwAcqzrCnt/상실 lssJnngpCnt).
- **사업자번호는 앞6자리 프리픽스 필터**(단건조회 불가·다건 가능). 정밀식별은 지역(법정동)+사업장명, 최종 seq.
- CLI: `nps search [--sido][--sggu][--emd][--name][--bizno][--page][--rows]`, `nps detail <seq>`, `nps period <seq> [--ym]`, `nps serve`, `nps diagnostics`.
- 스키마는 오픈소스 참조구현(Koomook/data-go-mcp-servers)으로 확정, **라이브 실측은 키 발급 후**.

## kipris-mcp — 특허 ⏳ 미착수 (다음 착수 1순위)
- **API**: KIPRIS Plus(plus.kipris.or.kr, 별도포털) — 개발 자동/운영 심의, 무료, 응답 **XML**.
- **사업자번호 직접 특허검색 불가 → 2-hop**: (a) "출원인 법인" 서비스로 사업자번호→특허고객번호/출원인명 매핑(약38만, 분기갱신, 출원이력 있는 법인만), (b) 특허검색(getAdvancedSearch)으로 출원번호·발명명칭·등록여부.
- 가장 복잡(별도포털·XML·심의·2단계). **nts/nps가 템플릿**(config/redact/client/app/server/cli + 정직 처리).

## 신설 절차 (kipris 착수 시)
1. 스캐폴드: 새 repo `git init` + pyproject(entry `kipris=cli:cli`) + gitignore(.env·산출물·스크래치) + 패키지 골격.
2. nts/nps의 config/redact/app/client/server/cli/tests 미러링.
3. 브레인스토밍→스펙→subagent(implementer→reviewer→fix)→최종 리뷰.
4. 실네트워크 없이 mock 테스트. 라이브는 키 발급 후.

## 불가 (공개 API 없음)
NICE의 **거래처(부가세 매출매입)·업체별 무역(수출입 개별자료)** = 세정·비공개 데이터, 공개 API 부재(NICE는 제휴/마이데이터로 확보).

## 공통 남은 것 (사용자 몫)
- data.go.kr / KIPRIS **API 키 발급** → 라이브 검증.
- nts/nps **GitHub 원격 생성**(현재 로컬 git only).
- 공통 Minor(nts/nps): redact 리터럴 import-time 스냅샷, pyproject `fastmcp` 선언 vs `mcp.server.fastmcp` import(양 저장소 동시 반영 권장).
