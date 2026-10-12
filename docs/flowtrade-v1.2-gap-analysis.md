# FlowTrade Radar v1.2: 조사 결과와 연결 한계

대상 명세는 [v1.2 증분 지시서](flowtrade-v1.2-spec.md)다. 이 기록은 저장소에서 발견한 기능, 이번에 추가한 코드, 실제 외부 자료 검증을 구분한다. 2026-10-12 사례의 뉴스·공시·가격·거래대금을 독립 확인했다는 기록이 아니다.

## 저장소에서 발견한 상태

Next.js/React 웹, FastAPI, 로컬 SQLite, YAML 전략·테마 설정, Toss REST의 검증 계약과 수집 코드가 있었다. 기존 전략은 일봉 MA5·MA20, 박스·기준봉·지지/저항·눌림/돌파와 위험 계산을 수행하며, 실제 완전한 자료가 없으면 합성 데모로 전환한다.

첨부 문서는 완전한 기존 v1.1의 NXT 5분봉 `EARLY_ENTRY` 엔진을 전제로 하지만, 이번 수정 전 HEAD에는 `strategy/intraday.py`, 명시적 `MA5_5M`/`EARLY_ENTRY_*` 상태 또는 해당 회귀 테스트가 없었다. 기존 `strategy/engine.py`와 `strategy/backtest.py`는 보존하고 별도의 장중 엔진과 합성 회귀 시나리오를 추가했다. 신규 합성 10/8 사례가 실제 과거 시장 데이터 또는 이미 있던 완전한 v1.1을 검증한 것으로 해석되면 안 된다.

| 요구 | 수정 전 발견 상태 | 이번 구현 | 실제 자료/운영 검증 한계 |
| --- | --- | --- | --- |
| P0-1 국내 재료 | 뉴스·공시 pipeline과 revision 저장 없음 | source adapter, 엄격한 이벤트 schema, cursor·dedupe·정정/철회·시점별 조회, 기능별 health | DART/KIND/계약 뉴스/정부 feed 모두 실제 연결 미확인, `UNAVAILABLE` |
| P0-2 개장 흐름 | 전체 legacy snapshot 수집에 의존; 관측 구간 거래대금 이력 없음 | 별도 REST turnover collector, 동일 session rolling interval·속도·참여 폭·coverage·상대 거래대금 | 실제 최신 counter·quote·venue 검증과 충분한 비교 이력 필요 |
| P0-3 Top 3 교체 | 기존 종목 추천 목록 하나 | 장전 가설·개장 관측·위험 통과 진입 목록 분리, 승격/강등·hysteresis·dwell·이력 | 실제 coverage/RVOL가 부족하면 관측 목록을 비움; 데모 가설로 채우지 않음 |
| P0-4 시간/venue | 국내 일봉·분봉 정규화와 snapshot freshness 중심 | KST session, point-in-time 관측/revision, consolidated 중복 배제, `[start,end)` 5분봉 확정 | Toss의 개별 tick venue 판별과 실제 NXT 사례 검증은 새로 완료되지 않음 |
| P0-5 설명 UI | legacy 추천/차트 화면 | 개장 레이더, 원인 분해, 출처·발행/첫 수신·품질·coverage, 합성 리플레이 선택 | 새 UI는 자료 부재를 표시하며 실제 국내 전체 시장을 관측했다고 주장하지 않음 |
| P1-1 관련성 | 설정된 미국/국내 테마 mapping | company별 공식 직접/뉴스 명시/연상 관계·confidence·근거, 공개매수 조건 보호 | source 계약과 실제 company reference 없이 직접 수혜로 승격하지 않음 |
| P1-2 리플레이 | 합성 일봉 walk-forward 백테스트 | 개장 7가지 합성 시나리오, 일반 합성 날짜 5개, 시간 prefix 검증·관측 지표 | 실제 진입 정답·수수료/세금 포함 수익·MFE/MAE는 미검증/`UNAVAILABLE` |
| P1-3 알림 | legacy 앱내 알림 | 신규 재료·주요 theme 상태 전이 기록, fingerprint·cooldown·시각·사유 | 명세 G의 모든 알림 종류를 각각 구현한 것은 아님; 외부 메시징을 연결하지 않음 |
| P2 의미/학습 | 없음 | 현재 규칙 기반 score/state machine 유지 | 의미 기반 clustering·학습형 점수·실측 calibration 미구현 |

## 실제 소스 가용성

| 소스/항목 | 확인한 사실 | 현재 정확한 blocker |
| --- | --- | --- |
| Toss REST/WS 문서 | 2026-10-12 공식 REST OpenAPI를 공개 GET으로 HTTP 200 수신, 버전 1.2.24 확인. 이전 REST 1.2.19의 선택 응답 11개·참조 schema 36개와 validation 구조가 동일함. WS 1.2.2는 이전 작업의 확인 기록 | 공개 문서 접근 성공은 인증 성공·실제 가격/수급/WS tick 관측과 별개이며 이번 작업에서 이를 새로 입증하지 않음 |
| 이전 Toss OAuth | 2026-10-07의 실제 요청 한 번은 HTTP 403 `access_denied` / `IP address not allowed`였음; 당시 REST 문서 1.2.19 | 과거 결과다. 현재 성공 또는 현재 동일 실패를 재실측한 것으로 표현하지 않음; 필요한 실제 출구 IP/허용 IP와 안전한 인증 검증 필요 |
| DART | 현재 조사에서 공개 공식 가이드 접근이 프록시 CONNECT 403; `DART_API_KEY`/`DART_KEY` 미설정 | 전용 공식 adapter 미구현: 접근 가능한 인증·응답·발행시각 정밀도·정정 계약과 필요한 승인된 키 |
| KIND/KRX | 공개 사이트 접근 CONNECT 403; `KIND_API_KEY` 미설정 | 허용된 API/피드·이용약관·schema·인증 계약 미확인 |
| 한국 경제/정책 뉴스 | 지정된 실제 계약 provider가 없고 `NEWS_API_KEY` 미설정 | provider, metadata/요약 재사용 license, endpoint/schema, 증분 수정/철회·인증 방식 |
| 정부/지자체 발표 | 정책브리핑 RSS 안내 접근 CONNECT 403 | 허용되는 실제 feed·timestamp/revision 계약 미확인 |
| 장중 투자자 순매수 | 현재 공식 문서는 일별 KRX+NXT 통합 투자자 **정수 주식 수**, KRX 전용 프로그램 **정수 주식 수**를 설명. 금액 축이 아님 | 실제 관측 시각·지연·coverage가 검증되지 않아 live 값은 null/`UNAVAILABLE`; 투자자 record의 `updatedAt`은 정확한 순매수 갱신시각으로 단정하지 않음 |
| VI·spread·진입 위험 | 새 엔진에 명시적 risk gate 있음 | 현재 source가 각 위험을 안전하다고 입증하지 못하면 null 유지, `READY` 금지 |

CONNECT 403은 해당 source 서버의 인증 실패가 아니다. DART, KIND, 뉴스는 서로 다른 coverage이고 한 소스로 정책/지역 이슈 전체를 대신하지 않는다. source 미탐지는 시장에 재료가 없다는 뜻이 아니다. 자세한 source 계약은 [catalyst-providers.md](catalyst-providers.md)에 있다.

오늘 확인한 공식 원본은 [Toss REST OpenAPI](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)이며 SHA-256은 `7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40`이다. 비교 대상은 저장소의 1.2.19 축약 계약이었다. 선택된 응답의 `type`, `required`, `enum`, nullable, 제약조건, `$ref`/조합 구조와 `format`은 동일하다. 기존 축약 계약은 path 설명·request body를 보존하지 않으므로 요청 전체나 설명 문구의 과거 대비 변경까지 입증한 비교는 아니다.

투자자 `updatedAt`은 보유·CFD까지 포함한 기록 전체 갱신시각이며 다음 영업일에도 갱신될 수 있다. 프로그램 일별 record에는 `updatedAt`이 없다. 기존 정규화는 주식 수를 float로 변환하고 `flowAsOf`에 날짜를 저장하므로 큰 정수의 완전한 정확도나 장중 순매수 시각 정밀도를 입증하지 않는다. 새 레이더의 지연·결측 상태를 실제 LIVE 순매수로 승격하지 않는다.

## 측정 범위와 지연의 정확한 의미

새 collector는 ingestion 소유자의 동일 OAuth client를 공유하며, 인증과 실제 venue sample 검증이 끝나기 전에 두 번째 client나 자동 token 발급을 만들지 않는다. 국내 관측은 미국 수익률을 포함한 legacy bundle의 성공 여부와 분리된다. 미국장 가설이 없거나 기존 전체 snapshot 분석이 실패해도, 국내 인증·venue·counter·quote 자체가 유효하면 국내 개장 흐름을 계산할 수 있다.

운영상 요청 목표는 REST 5초 간격이다. WebSocket 스트림이 아니며 공급자 응답 시간, rate limit/Retry-After와 backoff가 실제 간격을 늘릴 수 있다. 15초는 관측 quote/ranking timestamp의 **허용 freshness gate**이지 실제 15초 이하 공급 지연을 입증한 숫자가 아니다. UI 갱신·theme snapshot·Top 3는 기본 60초, 뉴스 poll은 기본 90초다. 연결 안 된 뉴스의 polling 숫자로 near-real-time 지원을 주장하지 않는다.

거래대금은 공급자의 동일 거래일/세션 누적 counter의 실제 차이를 사용한다. 첫 수신은 baseline이고 구간 거래대금을 만들어내지 않는다. 가격×거래량, 캔들 합산이나 상승률로 누락된 counter·외국인 순매수를 대체하지 않는다. KRX/NXT가 이미 합쳐진 자료는 constituent venue feed와 다시 더하지 않는다.

상대 거래대금은 기본 **동일 session·동일 elapsed time·동일 window를 FULL/COMPLETE하게 덮는 과거 10개 비교 session**을 요구한다. calendar 날짜 10일 또는 서버 실행 10일이 이 조건을 보장하지 않는다. REST의 관측 초가 불규칙하거나 interval이 경계를 가로지르면 정확한 비교 window가 만들어지지 않을 수 있다. 그러면 `INSUFFICIENT_HISTORY`와 null을 유지한다. 시장 점유율도 실제 covered universe 기준이며 국내 전체 시장의 자금 비율로 확대하지 않는다.

## 확인 범위 밖에 남는 사항

- 2026-10-12 실제 기사 문구·공시 조건·게시시각·가격·거래대금, 2026-10-08 실제 SK이노베이션/이수페타시스 회복·수익성.
- 실제 feed latency, 국내 전체 종목 탐지 recall, 투자자별 장중 순매수 권리와 지연, 실제 VI/spread risk 관측.
- 실제 trade execution과 비용·세금·slippage를 포함한 entry 성능, 학습형/의미 기반 clustering.
- 이번 변경의 GitHub push·Railway 재배포·외부 source 계정/라이선스 활성화. 이 문서는 코드/DB/API/UI와 모의 검증에 관한 기록이다.

완료 구현 및 T01–T14 mapping은 [flowtrade-v1.2-implementation.md](flowtrade-v1.2-implementation.md)에 있다.
