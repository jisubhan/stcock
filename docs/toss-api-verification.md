# Toss 증권 API 공식 문서 및 실제 응답 확인 기록

확인일: 2026-10-07 (Asia/Seoul). 게시 후 공식 문서 조회 완료: 약 16:06 KST.

## 현재 확인 상태

환경 게시 후 공식 문서에 접근할 수 있게 되었다. 사용자 제공 문서 주소에서 연결된 공식 source of truth를 따라 REST **1.2.19**, WebSocket **1.2.2** 계약을 확인했다. 이후 승인된 실제 OAuth 호출 한 번은 **HTTP 403 `access_denied` / `IP address not allowed`**를 반환했다. 현재 live 연결의 blocker는 API 호출 출구 IP의 Toss 허용 목록이며, 성공한 access token·시세 응답·WebSocket 관측은 아직 없다.

이 문서 작성 에이전트는 공개 문서만 조회했고 별도의 인증 호출을 하지 않았다. 실제 OAuth 결과는 루트 에이전트가 한 번 호출한 안전한 상태 요약을 전달받아 기록했다. 자격 증명 원문 조회·복사 또는 주문 호출은 하지 않았다. 문서에서 확인한 시장 의미와 실제 응답에서 관측한 사실을 구분해야 한다.

게시 후 현재 실행 환경에 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` 변수가 존재한다는 이름/설정 여부 확인 결과를 전달받았다. 문서 조회 당시 `TOSS_ACCESS_TOKEN`, `TOSS_API_MANIFEST`는 없었다. 이전 초안 메타데이터의 `has_saved_binding: false`만으로 현재 주입된 변수를 사용할 수 없다고 단정하거나 중복 비밀 바인딩을 요구해서는 안 된다.

## 공식 출처

아래 URL은 공개 HTTPS GET, TLS 검증을 유지한 상태로 모두 HTTP 200을 반환했다. 임의 API 경로 탐색이 아니라 공식 페이지의 링크를 따라 확인했다.

| 공식 URL | 역할 / 관측 |
| --- | --- |
| https://developers.tossinvest.com/docs | 사용자 제공 API 가이드; HTTP 200 HTML, `/llms.txt` 링크 제공 |
| https://developers.tossinvest.com/llms.txt | 문서 source of truth URL 목록; HTTP 200 text/plain |
| https://openapi.tossinvest.com/openapi-docs/overview.md | quick start, 인증, 제한 헤더, WebSocket 안내; HTTP 200 Markdown |
| https://openapi.tossinvest.com/openapi-docs/latest/openapi.json | canonical OpenAPI 3.1.0, REST 버전 **1.2.19**; HTTP 200 JSON |
| https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json | canonical AsyncAPI 3.0, WebSocket 버전 **1.2.2**; HTTP 200 JSON |
| https://openapi.tossinvest.com/openapi-docs/faq.md | 통합 가격, 캔들, 거래량, 시장 의미 FAQ; HTTP 200 Markdown |
| https://openapi.tossinvest.com/openapi-docs/latest/api-reference/README.md | Markdown API reference index; HTTP 200 Markdown |
| https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/StockInfoApi.md | 투자자·프로그램 수급 단위와 시장 범위; HTTP 200 Markdown |

이번에 받은 OpenAPI JSON의 SHA-256:

```text
7588debd863ac074e9e408185413ab573c3d45e9221e8cedaeec7c54ee295f03
```

공개 문서 원본은 `/tmp/public-docs/`에 보관했다. `read-only-bootstrap-reference.json`은 공식 source/version/hash와 제한된 조회 경로를 모은 비밀 없는 참고 자료다. `official_documentation_confirmed: true`, `actual_response_verified: false`, `venue_sample_verified: false`를 구분하며, production 활성화 manifest가 아니다.

## 확인한 OAuth 계약

Source: OpenAPI `paths./oauth2/token.post`, `OAuth2TokenRequest`, `OAuth2TokenResponse`, `OAuth2ErrorResponse`.

- URL: `POST https://openapi.tossinvest.com/oauth2/token`.
- 요청 body: `application/x-www-form-urlencoded`. 필수 field는 `grant_type=client_credentials`, `client_id`, `client_secret`. client field는 주입된 서버 환경 변수에서만 읽는다.
- 성공: HTTP 200의 **root JSON**에 `access_token` (string), `token_type` (`Bearer`), `expires_in` (integer, 초). 다른 API의 `result` envelope을 사용하지 않는다.
- 인증된 조회: `Authorization: Bearer {access_token}`. 이 MVP의 시장 조회에는 계좌 header가 필요하지 않다.
- **refresh token을 제공하지 않는다.** 만료 시 동일 endpoint로 새 access token을 발급한다.
- **client당 유효 token은 한 개이며 재발급하면 이전 token이 즉시 무효화된다.** 여러 프로세스의 독립 발급을 피하고 공용 token 관리와 단일 갱신을 사용해야 한다.

공식 오류 구분은 token endpoint에 대한 실제 요청 후에만 적용한다.

| 응답 | 공식 의미 |
| --- | --- |
| HTTP 400 `invalid_request` / `unsupported_grant_type` | 요청 body·Content-Type·중복 field 등 형식 문제; 먼저 형식을 확인 |
| HTTP 401 `invalid_client` | client 인증 실패 또는 비활성 client |
| HTTP 403 `access_denied` + IP 안내 | client의 허용 IP에 없는 호출 IP |
| HTTP 429 | 인증 API 그룹 제한; 운영 헤더에 따라 재시도 |

토큰·client secret·원문 OAuth 응답·에러 본문을 로그나 파일에 저장하지 않는다. 오류는 HTTP 상태와 허용한 오류 code만 출력한다. 프록시가 주입한 환경 값은 placeholder일 수 있으므로 이를 잘못된 키라고 단정하거나 decode·Base64 변환·서명하지 않는다. 공식 form-urlencoded 요청과 지정된 HTTPS 프록시 경로를 사용한 실제 결과로 판단한다.

## 실제 OAuth 결과와 허용 IP 설정

게시된 환경에서 공식 token endpoint에 client credentials form 요청을 **한 번** 수행한 결과:

```text
HTTP status: 403
OAuth error code: access_denied
허용한 진단 문구: IP address not allowed
성공한 access token: 없음
반환된 호출 IP 숫자: 없음
시세·캔들·수급·WebSocket 실제 샘플: 미호출 / 미확인
```

이 결과는 초기의 프록시 CONNECT 403이나 지원하지 않는 공개 경로의 `edge-blocked`와 다르다. 공식 token endpoint의 실제 OAuth 403은 OpenAPI의 허용 IP 오류 계약과 일치한다. client 변수의 존재는 확인했으나 이 오류만으로 credential 값 자체의 유효성이 검증되었다고 판단하지 않는다. IP 설정 변경이라는 의미 있는 상태 변화 전에는 같은 인증 요청을 반복하거나 새 token·중복 client 비밀을 요구하지 않는다.

[공식 quick start](https://openapi.tossinvest.com/openapi-docs/overview.md)의 명시된 사용자 설정 경로:

> 토스증권 WTS에 로그인 → 설정 → Open API → 메뉴 하단의 **허용 IP 관리**에서 API 호출을 허용할 IP를 등록합니다. 등록된 허용 IP 목록에 없는 IP에서의 호출은 403으로 차단됩니다.

[공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)의 “허용 IP를 등록했는데 403이 발생해요” 안내:

> 실행할 때마다 공인 IP가 바뀌는 환경(클라우드 인스턴스, 서버리스, 모바일 네트워크 등)이면 등록한 IP와 실제 호출 IP가 달라져 차단됩니다. 고정 IP를 확보한 뒤 허용 IP 목록에 등록하세요. 클라우드·데이터센터 대역이라는 이유로 별도 차단하지는 않습니다.

따라서 다음에 필요한 값은 **Toss API로 나가는 실제 네트워크 경로의 공인 출구 IP**다. 현재 오류 응답에는 이 숫자가 없고 이 Codex 환경의 고정 egress 제공 여부도 확인하지 못했다. 환경/배포 네트워크 제공자의 공식 설정 또는 지원에서 고정 출구 IP를 확인한 후 위 WTS 메뉴에 등록해야 한다. 컨테이너 내부 IP나 사용자 PC의 공인 IP를 이 서버의 출구 IP로 대신 등록하지 않는다.

출구 IP가 계속 변경되는 환경이면 안정적인 API 운영 전에 지원되는 고정 egress/NAT 또는 고정 IP 서버에서 백엔드를 실행하는 구성이 필요하다. 현재 문서는 Codex가 고정 출구 IP를 제공한다고 보장하지 않는다. Toss 공개 문서에는 동적 IP 예외, wildcard/CIDR 허용 범위 또는 광범위 허용 목록 설정에 대한 확인된 안내가 없어 그런 설정을 제안하지 않는다. 임의 IP 조회 서비스 호출이나 프록시 우회로 이 경로를 추측하지 않았다.

고정 출구 IP와 Toss 허용 IP 등록이 해결되면 주입된 동일 client 바인딩으로 공식 OAuth 한 번을 다시 검증하고, 성공했을 때만 정해진 read-only 시세·캔들·캘린더·수급 샘플과 WebSocket 검증을 이어간다. 실제 주문은 지원하지 않는다.

## 확인한 REST 조회 계약

시장 조회 성공 응답의 root는 `result` envelope이다. 금액·가격·수량은 대부분 **decimal string**이므로 실제 schema 검증 후 별도 정규화해야 한다. nullable field를 0 또는 현재 시각으로 임의 대체해서는 안 된다.

| 조회 | 확인한 요청 | 결과와 주의점 |
| --- | --- | --- |
| 현재가 | `GET /api/v1/prices?symbols=...`; 최대 200개 | `result[]`: `symbol`, `lastPrice`, `currency`, nullable `timestamp`. **등락률·누적 거래량·거래대금은 이 응답에 없다.** |
| 캔들 | `GET /api/v1/candles?symbol=...&interval=1d` 또는 `1m`; `count` 1~200, `adjusted` 기본 true, optional `before` | `result.candles[]`: `timestamp`, `openPrice`, `highPrice`, `lowPrice`, `closePrice`, `volume`, `currency`. 최신순. `result.nextBefore`를 다음 요청에 그대로 전달 |
| 종목 정보 | `GET /api/v1/stocks?symbols=...`; 최대 200개 | `result[]`: `symbol`, `name`, `market`, `currency` 등. 국내 상세의 `koreanMarketDetail.nxtSupported`, `krxTradingSuspended`, nullable `nxtTradingSuspended` 확인 |
| 랭킹 | `GET /api/v1/rankings`; 필수 `type`, `marketCountry`, `duration`; `count` 최대 100 | `result.rankings[]`: `symbol`, `price.lastPrice`, `price.basePrice`, nullable `price.changeRate`, `tradingVolume`, `tradingAmount`; `result.rankedAt` nullable |
| 투자자 수급 | `GET /api/v1/stocks/{symbol}/investor-trading`; optional `count` 최대 100, `until` | `result.records[]`: `date`, `updatedAt`, `foreigner`, `institution` 등. 매수·매도·순매수는 **주식 수(주)**이며 거래대금이 아니다 |
| 프로그램 매매 | `GET /api/v1/stocks/{symbol}/program-trades`; optional `count`, `until` | `result.records[]`: `date`, `arbitrage`, `nonArbitrage`; 각 `buyVolume`, `sellVolume`, `netBuyVolume`. **KRX 전용 주식 수**, NXT 미포함 |
| 국내 캘린더 | `GET /api/v1/market-calendar/KR`; optional `date` | `result.today`, `previousBusinessDay`, `nextBusinessDay`; 각각 `date`, nullable `integrated`에 세션 시간 |
| 미국 캘린더 | `GET /api/v1/market-calendar/US`; optional `date` | 별도 공식 US calendar schema 사용 |
| 국내 지표 | `GET /api/v1/market-indicators/prices?symbols=KOSPI,KOSDAQ` | `result[]`: `symbol`, `lastPrice`, `timestamp`. NASDAQ·S&P500 종목 symbol처럼 요청하지 않는다 |

랭킹 `type`는 `MARKET_TRADING_AMOUNT`, `MARKET_TRADING_VOLUME`, `TOP_GAINERS`, `TOP_LOSERS`, `TOSS_SECURITIES_TRADING_AMOUNT`, `TOSS_SECURITIES_TRADING_VOLUME`다. `marketCountry`는 KR/US. `duration`은 `realtime`, `1d`, `1w`, `1mo`, `3mo`, `6mo`, `1y`이며 TOP_GAINERS/TOP_LOSERS는 realtime을 지원하지 않는다.

`price.changeRate`는 **소수 비율** (`0.0125`는 1.25%)이다. MARKET_*는 시장 전체, TOSS_SECURITIES_*는 토스증권 체결 기준이므로 합치거나 같은 의미로 표시하지 않는다. TOP_*의 기준가는 선택 기간 시작 시점이며 나머지 type은 전일 기준가다. 현재가와 랭킹을 결합할 때 출처와 집계 시각·기간을 보존한다.

투자자 수급은 KRX+NXT 통합 **거래량**이다. `foreigner`는 등록외국인만 포함하며 시장 지표 투자자 매매대금의 등록·미등록 합계와 다르다. 당일 `individual`, 기관 세부 `breakdown`, `otherCorporation` 등 일부는 null이고 확정치는 늦게 반영된다. `updatedAt`을 보존하고 null을 순매수 0으로 해석하지 않는다. 프로그램 거래량을 현재가로 곱해 실제 순매수 거래대금이라고 표시하지 않는다.

읽기 쉬운 공식 원문: [투자자 매매동향](https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/StockInfoApi.md#getStockInvestorTrading), [프로그램 매매](https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/StockInfoApi.md#getStockProgramTrades).

캔들 `1m` timestamp는 봉 종료 시각으로 `[timestamp - 1분, timestamp)`를 나타낸다. `1d`는 현지 자정의 거래일이다. 응답은 내림차순이므로 MA와 전략 계산 전에 거래일·시간순으로 정렬한다. inclusive pagination 경계는 중복 제거한다. `before`의 timezone `+`는 HTTP client가 안전하게 URL encode하게 한다.

## KRX / NXT 의미: 문서 근거와 실제 샘플의 구분

[공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)의 직접 안내:

> 국내 캔들·체결은 KRX 만 집계하나요? — KRX + NXT 통합 시세입니다. 거래소를 지정해 조회하는 파라미터나 거래소를 식별하는 응답 필드는 없습니다. NXT 에서 거래되지 않는 종목은 KRX 시세로 자동 제공됩니다.

> 국내 1분봉은 몇 시부터 몇 시까지 제공되나요? — 통합 장 운영 시간인 08:01 ~ 20:00 (프리마켓 시작 ~ 애프터마켓 종료) 의 봉이 제공됩니다.

> 일봉 거래량과 1분봉 거래량 합계가 다릅니다 — 두 봉은 집계 범위가 다릅니다. 1분봉은 접속매매 체결만 분 단위로 누적하고, 일봉은 그날의 모든 매매 구분을 합산합니다. 1분봉만으로 일봉을 재구성할 수 없습니다.

추가로 FAQ는 국내 REST·WebSocket 호가를 KRX+NXT 가격별 합산 통합 호가로 설명하며 venue별 지정·식별 field는 지원하지 않는다고 밝힌다. 종가단일가 15:30 체결은 15:31 분봉에 포함된다. 체결이 없는 구간은 직전 종가 OHLC와 volume 0일 수 있다.

국내 캘린더는 KRX/NXT **별도 캘린더가 아니라 통합 모드**다. `integrated`의 nullable `preMarket`, `regularMarket`, `afterMarket`에 `startTime`, `endTime`이 있고 단일가 시각 field가 nullable이다. 정규장·애프터마켓은 각 거래소 시간의 합집합이며 모두 휴장하면 integrated는 null이다. 실제 장시간은 응답을 사용하고 예제의 고정 시간을 hardcode하지 않는다.

**위 내용은 공식 문서 의미 검증 완료이며, 특정 시각의 실제 NXT 체결 관측 완료를 뜻하지 않는다.** 실제 확인 단계에서는 가격·종목 정보·일봉·분봉·캘린더를 제한된 읽기 요청으로 받고 공식 schema를 검증한다. NXT 지원 종목의 과거 또는 현재 프리마켓·애프터마켓 timestamp를 확인한 후 증거에 `documentation_confirmed`와 `observed_sample_confirmed`를 각각 기록한다. venue field가 없으므로 단일 tick을 KRX 또는 NXT로 임의 분류하지 않는다. 소스는 “Toss 제공 KRX+NXT 통합 시세”로 표시할 수 있으나 실제 데이터가 합성 fixture라면 그런 표시는 사용할 수 없다.

미국 데이터는 전체 시장 NBBO가 아니다. 공식 FAQ에 따르면 일부 거래소 기준이며 세션에 따라 원천이 다를 수 있다. 미국 일봉 OHLC·종가는 정규장 기준이고 거래량은 더 넓은 범위일 수 있다. 따라서 국내 통합 시세 정책을 미국에 그대로 적용하지 않는다.

## 확인한 WebSocket 계약

Source: [AsyncAPI JSON](https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json), [overview의 웹소켓 연동](https://openapi.tossinvest.com/openapi-docs/overview.md).

- URL: `wss://openapi-ws.tossinvest.com/ws/v1`; TLS와 Bearer handshake header 사용.
- 시세 선언은 TEXT 프레임의 JSON **배열**: `[{"type":"trade:kr","codes":["005930"]}]`. 배열이 현재 전체 구독을 대체한다. `subscribe`/`unsubscribe` action 객체는 사용하지 않는다.
- 국내 `trade:kr`, `orderbook:kr`는 통합 데이터이며 미국은 `trade:us`, `orderbook:us`다. 이 MVP는 계좌·주문 채널을 구독하지 않는다.
- 수신은 top-level `type`: `subscriptions` (ack), `message` (`topic`과 `data`), `error`, `pong`으로 구분한다.
- client 송신이 180초 없으면 연결이 종료될 수 있다. 순수 TEXT `PING`을 60초 간격으로 보내는 것이 권장된다.
- account당 동시 연결 2개, 연결당 channel×symbol 조합 100개, 선언 5회/초. 재연결 전에 이전 연결을 닫고 backoff+jitter 후 전체 구독을 재선언한다.
- 시세 프레임은 **LOSSY**이며 sequence·누적 거래량·매수/매도 구분이 없다. 프레임 합산으로 누적 거래량이나 체결 강도를 만들어내지 않는다. 누적값과 복구는 검증한 REST 자료를 사용한다.

이 기록은 WebSocket 문서 계약 확인이며 실제 handshake/ack/tick 확인이 아니다.

## 제한 헤더

공식 overview는 클라이언트×API 그룹 제한과 다음 실제 header 의미를 명시한다.

- `X-RateLimit-Limit`: 현재 허용 초당 요청 수 / burst capacity.
- `X-RateLimit-Remaining`: 남은 bucket token.
- `X-RateLimit-Reset`: **token 한 개 재충전까지 예상 초**; epoch timestamp가 아니다.
- `Retry-After`: 429 때 재시도 권장 초.

`MARKET_DATA`, `MARKET_DATA_CHART`, `RANKING`, `STOCK`, `STOCK_TRADING_TREND`, `MARKET_INFO`, `AUTH` 등 그룹별로 상태를 분리한다. 공식 수치는 운영 중 바뀔 수 있으므로 응답 header를 우선하고 429에서 exponential backoff+jitter를 적용한다.

## 안전한 실행과 활성화

환경 변경은 이미 실행 중인 FastAPI 프로세스에 자동 반영되지 않을 수 있다. 게시 후 새 실행 문맥에서 이름/설정 여부를 확인하고 현재 작업에서 실행한 API를 새 환경을 상속한 프로세스로 재시작한다. `scripts/dev.mjs`는 이미 정상 응답하는 API를 재사용하므로 `npm run dev`만 재시작하면 오래된 API가 남을 수 있다. FastAPI와 dev script는 `.env`를 자동으로 읽지 않는다.

초기 readiness 진단은 네트워크를 호출하지 않는다.

```sh
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/verify_toss.py
```

기존 production adapter의 `--live`는 response/venue 검증 완료 manifest가 있어야 실행된다. 최초 실제 응답 수집은 이번에 확인한 공식 OAuth·GET 계약만 사용하는 별도의 제한된 discovery 절차로 진행하며, 수집을 위해 `verified` flag를 임의로 켜지 않는다. 검증한 source URL/version/hash, 요청 parameter, 응답 schema, 관측 timestamp와 시장 의미의 증거를 비밀 없는 manifest에 기록한다.

공식 raw schema에는 `$ref`, `allOf`, `oneOf`, nullable 타입과 decimal string이 있다. 기존 제한된 `ResponseSchema` vocabulary가 이를 모두 표현하는지 확인하고 지원되는 resolver/validator와 정규화 처리를 먼저 준비한다. 현재가에 없는 거래량·거래대금 field를 추정한 mapping으로 채우지 않는다.

adapter 준비와 대시보드 활성화도 구분한다. 실제 ingestion·전략 입력 연결·데이터 시각/범위/출처·부분 누락·장애 동작 검증 이후에만 실제 UI를 live로 표시한다. 추천 근거의 수급은 정확한 단위와 venue 범위를 보존한다. 실제 주문은 계속 지원하지 않는다.

보수적인 공개 진단 스크립트는 기본 실행 시 네트워크를 호출하지 않으며 registry에 검증된 endpoint가 없으면 `--probe`를 거부한다. 문서 확인만으로 그 registry 또는 production manifest가 자동 활성화되지는 않는다.

```sh
python scripts/verify-toss-api.py
python scripts/verify-toss-api.py --check-docs
python scripts/verify-toss-api.py --sample /path/to/redacted-sample.json --schema /path/to/verified-schema.json
```

## 연결 오류 이력

아래는 현재 상태와 구분한 과거 관측이다.

| 확인 단계 | 관측 / 해석 |
| --- | --- |
| 초기 확인 | API·문서·WebSocket 후보 도메인 모두 프록시 `CONNECT 403`; 원본 HTTP 응답 없음 |
| 2026-10-07 15:44 KST | 공개 API root와 추측 문서 후보 `/docs`, `/openapi.json`, `/v3/api-docs`, `/swagger-ui/index.html`에서 HTTP 403 JSON `edge-blocked`, “요청한 API 경로를 지원하지 않습니다.”; 미인증 요청이므로 credential/IP 문제로 단정하지 않음 |
| 같은 단계 | WebSocket 호스트 HTTPS root HTTP 401; 실제 WSS handshake 확인을 뜻하지 않음 |
| 사용자 문서 주소 제공 후 | `https://developers.tossinvest.com/docs`는 아직 프록시 CONNECT 403; additive 허용 규칙의 초안은 저장됐지만 `requires_publish: true`라 runtime 적용 증거가 아니었음 |
| 사용자가 환경 게시 후 | 사용자 문서 URL HTTP 200과 연결된 canonical REST/WS 문서 HTTP 200 확인; 현재 runtime client 변수 주입 확인, 문서 계약 확인 완료 |
| 게시 후 실제 OAuth 한 번 | 공식 `/oauth2/token`이 HTTP 403 `access_denied` / `IP address not allowed` 반환. 호출 IP 숫자 미제공; token·시세·WebSocket 실제 검증은 미완료. 공식 WTS 허용 IP 관리와 고정 공인 출구 IP 확인이 다음 prerequisite |
