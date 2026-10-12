# NXT 시세 표시 준비: 2026-10-12 운영 확인

이번 작업의 범위는 **2026-10-13 NXT 개장 시 실제 시세를 바로 확인할 수 있도록 가격 표시를 준비하는 것**이다. 자동 시그널, 새 뉴스 연결, WebSocket 또는 실계좌 주문은 추가하지 않는다.

이 기록은 기존 v1.2 완료 보고 뒤에 확인한 새로운 운영 증거다. 기존 보고서의 미연결 상태와 2026-10-07 OAuth 403은 당시의 기록으로 보존한다. 현재 연결 상태는 아래의 더 최근 관측으로 판단한다.

## 확인한 운영 상태

공개 상태 조회: [Railway `/api/connection`](https://joengisgod.up.railway.app/api/connection).

- 2026-10-12 **23:33 KST**: 루트 에이전트가 실제 Railway의 연결 상태를 HTTP GET으로 확인했다.
- **23:40:17 / 23:40:43 KST**: 문서 담당 에이전트가 같은 공개 상태를 HTTP 200으로 재확인했다. 이 확인은 상태 조회이며 별도의 인증 호출이나 POST를 수행하지 않았다.
- 상태에 저장된 실제 검증 시각 `lastProbe.checkedAt`: **23:30:43.766 KST**. REST 문서 버전은 **1.2.24**다.
- OAuth는 `passed`, HTTP **200**, 응답 schema `passed`다. `venue_verified=true`, `sampleStatus=verified`, `verificationStatus=verified`도 확인했다.
- 검증 기록의 `calls=12`는 **OAuth 1회와 조회 11종**의 합이다. 조회 12종이라는 의미가 아니다.

| 실제 조회 sample | 응답 수 | 상태 |
| --- | ---: | --- |
| 국내 현재가 `kr_prices` | 1 | HTTP 200 / schema passed |
| 국내 일봉 `daily_candles` | 30 | HTTP 200 / schema passed |
| 국내 분봉 `minute_candles` | 200 | HTTP 200 / schema passed |
| 국내 랭킹 `kr_rankings` | 100 | HTTP 200 / schema passed |
| 국내 캘린더 `kr_calendar` | 단일 calendar 객체 | HTTP 200 / schema passed |
| 투자자 매매 `investor` | 10 | HTTP 200 / schema passed |
| 프로그램 매매 `program` | 10 | HTTP 200 / schema passed |
| 미국 현재가 `us_prices` | 2 | HTTP 200 / schema passed |
| 종목 정보 `stock_master` | 1 | HTTP 200 / schema passed |
| NXT 프리마켓 구간 분봉 `nxt_premarket` | 50 | HTTP 200 / schema passed |
| NXT 애프터마켓 구간 분봉 `nxt_aftermarket` | 200 | HTTP 200 / schema passed |

이 sample 수는 반환된 record 수다. 전체 시장 커버리지 또는 내일 개장 때 관측할 종목 수를 뜻하지 않는다. 투자자·프로그램 sample 성공도 장중 순매수의 실시간성이나 금액 단위를 입증하지 않는다.

## 인증 성공과 현재 시세 준비 상태

23시대에는 국내 거래가 종료된 상태다. 루트의 관측에서 quote는 STALE이었고, 위 공개 연결 상태는 `ready=false`, legacy `mode=demo`였다. 23:40:43의 `dataQuality.status=missing`은 **검증된 실시간 전체 snapshot이 아직 없다는 상태**였다. 이때 `brokerMode=blocked`가 함께 표시되더라도 저장된 OAuth 결과는 HTTP 200 `passed`였다. 전체 분석 snapshot의 준비 상태와 인증 결과를 구분해야 한다.

NXT sample 검증은 공식 통합 시세 계약, NXT 거래 지원 종목 정보, 공식 calendar의 전후 장 범위 안에 있는 유효한 분봉·0보다 큰 거래량을 사용한다. 이는 검증된 전후 장의 실제 조회 증거다. **내일 08:00 정각의 새 체결을 이미 관측했다는 의미는 아니다.** 해당 API는 개별 체결의 거래소를 식별하는 응답 필드를 제공하지 않으므로 개별 가격을 NXT 전용 체결로 단정하지 않는다.

## 가격 표시 개선의 확인 범위

국내 가격 조회·표시를 전체 분석 bundle과 랭킹에서 분리하는 수정을 구현하고 로컬 검증을 완료했다. 새 `GET /api/market/quotes`와 별도 가격 panel은 전체 추천용 데이터가 부족하거나 랭킹이 지연되어도, 유효한 실제 가격과 공급자 timestamp를 별도로 표시한다. 가격 자료 상태와 추천·거래대금 흐름의 상태를 구분한다.

표시 시 실제 자료의 출처·기준시각·LIVE/STALE/UNAVAILABLE 상태를 유지한다. 장 종료 가격은 실제 가격이더라도 최신 장중 가격과 구분하고, null이나 오래된 가격을 합성 현재가로 채우지 않는다. `ready=false`인 전체 분석을 가격 조회 자체의 실패로 해석하지 않도록 상태를 나눈다.

표시하는 가격의 timestamp가 오래되었거나 네트워크 요청이 실패하면 STALE로 표시한다. DEMO 리플레이 선택 시 실제 가격 panel을 해제하여 합성 재생 자료와 혼합하지 않는다.

날짜가 바뀌면 공식 캘린더를 다시 확인한다. 장전 서버 재시작 시에는 아직 완료되지 않은 당일 세션의 분봉을 기다리지 않고, 최근 완료된 공식 NXT 장전 세션의 실제 분봉으로 거래소 범위를 검증한다. 07:59·08:00:02·09:01의 cold-start 회귀를 모의 공식 응답으로 검증했다. 실제 quote는 공급자 시각이 신선해야 하므로 과거 검증 봉을 현재가로 대체하지 않는다.

`002_quote_monitor.sql`은 실제 quote 이력·최신 가격 cache·기능별 상태를 추가하며 기존 전략/모의매매 데이터는 유지한다. current GET은 전체 시세 이력을 순회하지 않고 제한된 최신 cache를 읽는다. 기존 SQLite를 `.runtime/backups/radar-before-quote-monitor-2026-10-12.sqlite3`로 별도 사전 백업한 뒤 추가 migration을 적용했다. rollback script는 신규 quote schema를 먼저 제거한 후 v1.2 schema를 제거하며 legacy 데이터를 보존한다.

## 구현 후 로컬 검증

| 검사 | 확인 결과 |
| --- | --- |
| 전체 API pytest | **523개 통과**, 4.44초; 기존 Starlette deprecation warning 1개 |
| TypeScript typecheck | 통과 |
| 최신 production build | 통과 |
| 로컬 production standalone·임시 API 기반 브라우저 검사 | **58개 통과**; desktop 1440px / mobile 390px |
| production DEMO의 `/api/market/quotes` | HTTP 200, `items=[]`, UNAVAILABLE; 합성 현재가를 실제 quote로 제공하지 않음 |
| 별도 브라우저 quote fixture | 신선한 quote LIVE와 전체 분석 DEMO의 공존, 오래된 timestamp STALE, 네트워크 실패 STALE, 리플레이에서 실제 panel 해제 확인 |

아래 이미지는 **합성 브라우저 응답 fixture로 UI 동작을 확인한 screenshot**이다. 실제 시장의 성공 사진이나 내일 개장 시세의 증거가 아니다.

- [Desktop quote fixture](/tmp/flowtrade-nxt-readiness-e2e/desktop-independent-quotes-fixture.png).
- [Mobile quote fixture](/tmp/flowtrade-nxt-readiness-e2e/mobile-independent-quotes-fixture.png).

이 기록의 완료 범위는 구현과 위 로컬 검증이다. 새 가격 경로의 Railway 배포·원격 응답과 GitHub commit 결과는 이 문서 작성 시점에 미확인이다. 앞 절의 실제 Railway 인증·sample 성공과 이번 새 경로의 원격 배포 확인을 구분한다.

## 내일 개장에 대해 남는 검증

2026-10-13 08:00 이후 공급자가 제공하는 첫 유효 가격·timestamp, 서버 최초 수신시각, 웹 표시시각을 실제로 확인해야 한다. 개장 시점의 공급자 갱신, REST polling 간격, 응답시간·rate limit, 서버와 브라우저 갱신이 함께 영향을 준다.

오늘의 인증·조회 성공은 내일 **08:00 정각 표시 또는 특정 초 이내 지연 SLA**를 입증하지 않는다. 개장 전 거래 없는 시점에는 이전 가격·STALE 또는 UNAVAILABLE 표기가 가능하다. 최신 시세의 실제 수신과 화면 표시를 확인한 뒤 지연을 평가한다.

이번 확인에서 새 뉴스 feed, WebSocket 수신, 자동 시그널 또는 주문 기능의 운영 성공은 확인하지 않았다. `live_orders_enabled=false`이며 키·토큰·출구 IP 값을 이 문서에 저장하지 않는다.
