# Toss 전체 종목·산업 분류 탐색 계약

공식 REST **1.2.24**의 공개 계약을 확인하고 읽기 전용 클라이언트를 확장했다. 전체 유니버스·섹터 API의 실제 인증 응답을 이번 작업에서 조회했다는 뜻은 아니다. 실제 응답·커버리지·지연 검증 전에는 연결 상태를 `UNAVAILABLE`로 표시하고, 기존 검증된 캐시가 오래됐으면 `STALE`로 표시한다.

## 확인한 공식 출처

- [canonical OpenAPI JSON](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json): 공개 GET HTTP 200, 버전 `1.2.24`, SHA-256 `7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40`.
- [공식 개요·요청 제한](https://openapi.tossinvest.com/openapi-docs/overview.md): 공개 GET HTTP 200. 제한은 클라이언트와 API 그룹별이며 실제 응답 헤더가 현재 한도를 알려준다.
- 저장소의 [응답 계약](../config/toss-openapi-contract.json)은 위 원본에서 필요한 읽기 전용 응답과 참조 스키마만 보존한다. 공개 계약 확인과 실제 데이터 관측은 별도이다.

## 추가 클라이언트 범위

| Python 메서드 | 공식 GET 경로 | 용도·범위 |
| --- | --- | --- |
| `stock_universe(market, *, status='ACTIVE', security_type=None, common_share=None)` | `/api/v1/stocks/all` | 지정 거래소의 Toss 거래 가능 종목 목록 |
| `sectors()` | `/api/v1/sectors` | Toss 산업 분류의 전체 계층 |
| `sector_detail(sector_id, country='KR')` | `/api/v1/sectors/{sectorId}` | 분류 프로필과 시장별 기간 등락률 |
| `sector_stocks(sector_id, country='KR')` | `/api/v1/sectors/{sectorId}/stocks` | 하위 분류를 포함한 보통주 구성 목록 |
| `sector_rankings(country='KR', ranking_type='MARKET_TRADING_AMOUNT', duration='1d')` | `/api/v1/sectors/rankings` | 산업 분류별 거래대금 또는 상승률 순위 |

시장·섹터 읽기는 OAuth 토큰만 필요하다. 계좌 식별 헤더·계좌 조회·주문은 이 범위에 포함하지 않는다. 공식 ETF 구성 조회 경로는 존재하지만 이번 클라이언트 통합에는 포함하지 않는다.

기존 주식 `rankings` 조회에도 `ranking_type`과 `duration` 선택을 추가한다. 기본값 `MARKET_TRADING_AMOUNT`·`1d`를 유지한다. 이는 누적 거래대금 순위이며 5분간 거래대금 증가속도 순위를 의미하지 않는다.

## 유니버스와 파라미터

`stocks/all`은 **Toss에서 거래 가능한 종목**을 거래소별로 반환한다. 거래소 전체 상장 명부 또는 전 종목 실시간 수집 완료와 동일하지 않다. 필터에 맞는 목록을 symbol 오름차순으로 한 번에 반환하며 페이지·count·100개 제한은 없다. 공식 문서는 하루 한 번 조회한 뒤 캐시하도록 권장한다.

| 항목 | 허용값·제약 |
| --- | --- |
| `market` | `KOSPI`, `KOSDAQ`, `NYSE`, `NASDAQ`, `AMEX`, `KR_ETC`, `US_ETC` |
| `status` | `SCHEDULED`, `ACTIVE`, `DELISTED`; 기본 `ACTIVE` |
| `securityType` | `STOCK`, `FOREIGN_STOCK`, `DEPOSITARY_RECEIPT`, `INFRASTRUCTURE_FUND`, `REIT`, `ETF`, `FOREIGN_ETF`, `ETN`, `STOCK_WARRANTS`; 생략 시 전체 유형 |
| `commonShare` | 실제 boolean; `true` 보통주, `false` 우선주, 생략 시 전체 |
| `marketCountry` | `KR`, `US` |
| `sectorId` | 목록에서 받은 식별자, `^[A-Za-z0-9_-]{1,40}$` |
| 섹터 `type` | `TOP_GAINERS`, `MARKET_TRADING_AMOUNT` |
| 섹터 `duration` | `1d`, `1w`, `1mo`, `3mo`, `6mo`, `1y`; `realtime` 없음 |
| 주식 랭킹 `type` | `MARKET_TRADING_AMOUNT`, `MARKET_TRADING_VOLUME`, `TOP_GAINERS`, `TOP_LOSERS`, `TOSS_SECURITIES_TRADING_AMOUNT`, `TOSS_SECURITIES_TRADING_VOLUME` |
| 주식 랭킹 `duration` | 위 6기간과 `realtime`; `TOP_GAINERS`·`TOP_LOSERS`는 `realtime` 불가 |
| 주식 랭킹 `count` | 1~100; 실제 응답은 더 적을 수 있음 |

`ListedStock`은 symbol·이름·유형·보통주 여부·ISIN만 제공하며 NXT 지원 여부는 없다. `/api/v1/stocks` 상세 조회를 **최대 200개 symbol**씩 나눠 `koreanMarketDetail.nxtSupported`·거래정지 정보를 확인해야 한다. 현재가 다건 조회도 공식 상한은 200개다. 국내 symbol에는 영문이 포함될 수 있으므로 숫자만 허용하면 안 된다. 여러 거래소 응답의 중복 종목은 symbol·ISIN과 시장 범위로 확인한다.

## 분류·수혜·측정의 의미

- 섹터는 **Toss 자체 산업 분류**다. 법정 KSIC나 공식 수혜기업 목록으로 표시하지 않는다. depth 0/1/2는 대/중/소분류이며 한 종목은 여러 섹터에 속할 수 있다.
- 섹터 기본 정보와 nullable `summary`는 그룹 프로필이다. `StockInfo`에는 기업 산업 코드나 사업 설명이 없다. 사업 설명·뉴스/공시 검색은 이 계약으로 해결되지 않는다.
- `sector_stocks`는 하위 분류의 종목을 포함한다. 부모·자식 그룹과 다중 소속을 단순 합산하면 중복이 생긴다. 원본 소속과 정규화된 기여도·커버리지 분모를 함께 보존한다.
- 구성 주식은 시가총액순이며 거래대금순이 아니다. `volume`·`tradingAmount`는 당일 누적값으로 시세가 없으면 null이다. 금액은 미국 종목도 원화 환산값으로 설명되어 있으므로 항목 `currency`를 함께 보존하고 알 수 없는 통화를 추정하지 않는다.
- 섹터 랭킹은 대·중분류만 최대 100개 반환한다. 소분류·소규모 구성·결측 그룹은 제외될 수 있으므로 전체 분류 탐색은 `sectors()`에서 시작한다. 빈 배열은 시장에 테마가 없다는 증거가 아니다.
- 상세 등락률은 소수비율이다. 기간별 결측은 null이고 섹터 요약도 null일 수 있다. 조회 시각을 provider의 측정시각으로 대체하지 않는다. 섹터 구성·랭킹 응답에는 공통 `rankedAt` 계약이 없어 값의 기준시각은 별도 검증 전까지 미확인이다. 주식 랭킹의 nullable `rankedAt`도 그대로 보존한다.

## 미등록 테마 연결과 이력

실제 응답으로 확인한 provider membership은 별도 산업 그룹으로 자동 등록할 수 있다. `provider_sector_id`·계층·원본 URL·응답 hash·문서 버전·수신시각·`first_seen_at`·매핑 버전을 저장한다. provider 측 유효시각이 없으면 미확인으로 두며 새 연결을 과거로 소급하지 않는다. 변경·제거는 이력을 남기고 replay 시각까지 알려진 버전만 적용한다.

산업 소속의 확신도와 뉴스의 기업 관련성·수혜 확신도를 분리한다. 기존 커스텀 테마와 키워드·규칙·LLM이 제안한 연결은 `THEMATIC_ASSOCIATION` 또는 `UNVERIFIED`와 WATCH로 유지하고, 생성 방법·규칙/모델 버전·검토 상태·근거를 기록한다. 관리자 검토나 LLM 추정만으로 `DIRECT_VERIFIED`를 만들지 않는다. 공식 원문이 특정 기업을 명시하고 그 기업에 관한 증거를 확인해야 직접 관계로 올릴 수 있다. 분류 소속만으로 진입 준비나 개장 후 주도 흐름을 확정하지 않는다.

## 요청 제한과 운영 권장

| 그룹 | 관련 API | 문서상 초당 최대 |
| --- | --- | --- |
| `STOCK_ALL` | 전체 종목 목록 | 1회 |
| `STOCK` | 종목 상세 | 5회 |
| `SECTOR` | 분류 목록·상세·구성 주식 | 5회 |
| `SECTOR_RANKING` | 섹터 랭킹 | 5회 |
| `RANKING` | 주식 랭킹 | 5회 |

한도는 사전 공지 없이 바뀔 수 있다. 목록·계층은 일일 캐시를 사용하고 그룹별 호출량을 합산한다. 구성 목록을 캐시했어도 함께 받은 누적 거래대금을 실시간 값으로 재사용하지 않는다. `X-RateLimit-Limit`·`X-RateLimit-Remaining`·`X-RateLimit-Reset`을 보존하고 429는 `Retry-After`와 백오프를 적용한다. `X-RateLimit-Reset`은 **토큰 1개 재충전까지 예상 초**이며 epoch 시각이 아니다. 이 문서의 상한은 현재 실행 환경에서 관측한 응답 헤더 값이 아니다.

공개 문서 조회만으로 전체시장 관측률, 실제 API 권한, 공급 지연을 증명할 수 없다. 성공한 실제 목록·상세·시세 응답과 해당 시각을 확보한 뒤에만 그 범위의 관측 상태를 갱신한다.

## 구현 검증

기존 응답 11개·참조 schema 36개를 유지하고 조회 경로 5개·schema 10개를 추가했다. 새 subset은 공개 원본의 응답 및 `$ref` closure와 일치한다. 설명·example과 계좌·주문 계약은 포함하지 않는다.

`test_documented_discovery.py`와 기존 `test_documented.py`는 **69개 통과**했다. ingestion·fast collector·quotes·adapter를 포함한 관련 회귀 검증은 **169개 통과**했다. 모두 모의 transport를 사용했다. 잘못된 argument가 인증 전에 차단되는지, 전체 목록이 100/200개에서 잘리지 않는지, nullable·decimal 원문과 미확인 timestamp가 보존되는지, 기존 단일 token·12호출 probe 동작이 유지되는지 확인했다. 실제 새 endpoint 인증·응답 성공을 대신하는 테스트는 아니다.
