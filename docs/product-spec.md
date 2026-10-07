# 실시간 국내주식 수급·차트 추천 시스템 — Codex 구현 명세

> 프로젝트 목적: 토스증권 Open API를 이용해 국내주식의 **실시간 수급 + 테마 강도 + 차트 구조**를 분석하고, 사용자가 장중 진입을 판단할 수 있도록 **추천 종목, 진입 관찰가, 손절 기준, 돌파 확인선, 추천 근거**를 실시간으로 제공하는 웹사이트를 구현한다.
>
> 중요: MVP의 목적은 **자동매매가 아니라 의사결정 지원형 실시간 추천**이다. 실제 주문은 기본 비활성화하고, 추후 별도 플래그와 사용자 확인을 통해 확장한다.

---

## 0. Codex에게 가장 먼저 지시할 것

이 문서를 읽은 뒤 바로 코딩부터 하지 말고 아래 순서로 진행한다.

1. 토스증권 Open API 최신 공식 문서와 실제 응답 스키마를 먼저 확인한다.
2. 특히 **국내주식 시세가 KRX와 NXT를 어떤 방식으로 제공하는지** 반드시 샘플 호출로 검증한다.
3. REST와 WebSocket 지원 여부는 현재 공식 문서 버전 기준으로 재확인한다.
4. 아래 아키텍처와 전략을 기준으로 MVP를 구현한다.
5. 불명확한 API 필드는 하드코딩하지 말고 adapter/interface 계층으로 격리한다.
6. 실계좌 주문 API는 MVP에서 호출하지 않는다.
7. 모든 추천은 "왜 추천했는가", "어디서 틀렸다고 판단하는가"가 UI에 반드시 노출되어야 한다.

공식 문서 기준 확인 대상:

- Base API: `https://openapi.tossinvest.com`
- WebSocket: `wss://openapi-ws.tossinvest.com/ws/v1`
- OAuth: `POST /oauth2/token`
- 현재가: `GET /api/v1/prices`
- 호가: `GET /api/v1/orderbook`
- 최근 체결: `GET /api/v1/trades`
- 캔들: `GET /api/v1/candles`
- 종목 기본정보: `GET /api/v1/stocks`
- 투자자별 수급: `GET /api/v1/stocks/{symbol}/investor-trading`
- 프로그램매매: `GET /api/v1/stocks/{symbol}/program-trades`
- 공매도: `GET /api/v1/stocks/{symbol}/short-selling`
- 랭킹: `GET /api/v1/rankings`
- 국내 장 운영 정보: `GET /api/v1/market-calendar/KR`
- 미국 장 운영 정보: `GET /api/v1/market-calendar/US`
- 국내 지수: `/api/v1/market-indicators/...`

토스증권 API의 rate limit은 API 그룹별로 다르므로 응답 헤더의 아래 값을 기준으로 동적 throttling을 구현한다.

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After`

429 발생 시 exponential backoff + jitter 적용.

---

# 1. 프로젝트 개요

## 1.1 프로젝트명

임시 프로젝트명:

**FlowTrade Radar**

대체 후보:

- FlowRadar
- MoneyFlow
- FlowEntry
- StockRadar KR

코드 내 프로젝트 이름은 일단 `flowtrade-radar` 사용.

---

## 1.2 핵심 철학

이 시스템은 단순히 "오늘 많이 오른 종목"을 추천하지 않는다.

우리가 역추적한 매매법의 핵심은 다음 순서다.

```text
미국장/글로벌 수급
        ↓
오늘 국내에서 강할 가능성이 높은 테마
        ↓
실제 국내 장에서 테마 동조 여부 확인
        ↓
그 테마 내 거래대금/수급이 좋은 종목
        ↓
일봉 차트에서 박스권 + 기준봉 + 5일선 + 지지/저항 확인
        ↓
좋은 가격으로 눌릴 때 대기
        ↓
지지 확인 또는 핵심 가격 재돌파
        ↓
진입 후보 추천
        ↓
무효화 가격/손절 가격 제시
```

핵심 문장:

> 좋은 종목을 찾는 것보다 **좋은 종목을 좋은 가격에서 사는 것**을 우선한다.

---

# 2. 우리가 역추적한 실전 매매 규칙

## 2.1 테마가 가장 먼저다

개별 종목 차트보다 먼저 시장에서 **돈이 어디로 이동하는지** 판단한다.

예:

- AI 반도체
- 반도체 소부장
- 광통신 / CPO / 광모듈
- 원전 / AI 전력
- 2차전지
- 석유 / 정유
- 바이오
- 방산
- 로봇
- 태양광 / 에너지

하나의 종목이 강한 것이 아니라 **동일 테마 종목 최소 3개 이상이 동시에 움직이는지** 확인한다.

### 테마 강도 판단 예시

다음 요소를 합산한다.

- 테마 구성 종목 상승 비율
- 상승 종목 평균 수익률
- 테마 전체 거래대금
- 직전 5분 대비 거래대금 증가율
- 테마 내 거래대금 상위 3종목의 집중도
- 외국인/기관 수급
- 프로그램 매매
- 전일 대비 거래량 증가율
- 미국 관련 섹터의 overnight performance

---

# 3. 해외시장 → 국내 테마 매핑

매일 한국시간 아침에 미국 시장의 수급을 분석하고 국내 테마 후보를 만든다.

## 3.1 예시 매핑

```yaml
theme_mapping:
  optical_networking:
    us:
      - MRVL
      - CIEN
      - COHR
      - LITE
    kr:
      - 빛과전자
      - 대한광통신
      - 오이솔루션
      - 라이콤
      - 코위버
      - 우리로

  ai_semiconductor:
    us:
      - NVDA
      - AMD
      - AVGO
      - MU
      - MRVL
    kr:
      - 삼성전자
      - SK하이닉스
      - 원익IPS
      - 주성엔지니어링
      - 심텍

  ai_power_nuclear:
    us:
      - CEG
      - VST
      - GEV
      - CCJ
    kr:
      - 두산에너빌리티
      - 한전기술
      - 우리기술
      - 한전KPS

  battery:
    us:
      - ALB
      - TSLA
      - QS
    kr:
      - LG에너지솔루션
      - 삼성SDI
      - 에코프로비엠
      - 에코프로
      - 포스코퓨처엠

  energy_solar:
    us:
      - FSLR
      - ENPH
    kr:
      - HD현대에너지솔루션
      - 한화솔루션
```

**주의:** 위 목록은 초기 seed 데이터일 뿐이다. UI에서 사용자가 테마-종목 매핑을 추가/수정할 수 있게 한다.

---

# 4. 종목 추천 핵심 차트 규칙

## 4.1 기준봉(Significant Candle)

다음 조건을 만족하는 일봉을 `기준봉` 후보로 지정한다.

초기값:

```text
당일 상승률 >= +4%
AND
당일 거래량 / 최근 20일 평균 거래량 >= 1.8
AND
당일 거래대금 >= 설정값
```

추가 가점:

- 장대양봉
- 종가가 고가의 상단 30% 이내
- 외국인 순매수
- 기관 순매수
- 프로그램 순매수
- 신고가/전고점 돌파

설정값은 UI에서 변경 가능.

---

## 4.2 장대양봉 후 거래량 감소 + 종가 유지

매우 중요한 패턴.

```text
Day 0:
큰 거래량 + 장대양봉

Day 1~3:
거래량 감소
BUT
가격이 기준봉 종가 또는 몸통 상단에서 유지
```

해석:

> 상승 때는 큰 돈이 들어왔는데, 이후 거래량이 감소해도 가격이 내려오지 않는다.
> 즉 차익 매물이 강하지 않고 물량 소화 중일 가능성이 있다.

추천 점수 가점 조건:

```python
volume_ratio = current_volume / significant_candle_volume

if volume_ratio < 0.65 and close >= significant_candle.close * 0.99:
    score += 15
```

단순 거래량 감소가 아니라 **가격 유지와 반드시 함께 평가**한다.

---

# 5. 박스권(Box) 탐지

친구 매매법에서 매우 중요.

## 5.1 박스 정의

최근 5~30 거래일 동안 반복적으로 지지/저항을 받은 가격 영역을 찾는다.

단순 최고/최저가만 사용하지 말고 다음을 반영:

- swing high
- swing low
- 거래량이 크게 발생한 가격대
- 기준봉 시가
- 기준봉 종가
- 기준봉 고가/저가
- 최근 N일 반복 터치 가격

예:

```text
BOX_HIGH = 149,900
BOX_LOW  = 141,000
```

돌파 이후에는:

```text
기존 저항 → 새로운 지지 후보
```

로 역할이 전환된다.

---

# 6. 5일 이동평균선 규칙

친구 전략에서 가장 중요한 눌림 진입 기준 중 하나.

## 6.1 기본

```python
ma5 = mean(last_5_integrated_closes)
```

단, 국내 주식에서는 **KRX 정규장 종가만 사용하지 말 것.**

가능하면:

> KRX + NXT를 반영한 통합 일봉/최종 체결가 기준 5일선

을 사용한다.

---

## 6.2 NXT 주의사항 — 매우 중요

사용자는 앞으로 모든 국내주식 분석에서 **NXT까지 포함한 통합 가격**을 기준으로 한다.

토스증권 API 문서에서 시장 캘린더는 KRX/NXT를 제공하지만, `prices`, `trades`, `candles` 데이터가 **KRX/NXT를 개별 venue로 구분하는지 또는 이미 통합된 시세인지 문서만으로 확정하지 않는다.**

따라서 구현 첫 단계에서 반드시 확인:

1. trade WebSocket payload에 `market`, `venue`, `exchange`, `session`과 같은 필드가 존재하는가?
2. `/prices`의 lastPrice는 KRX/NXT 중 어느 시장인가?
3. `/candles?interval=1d`의 OHLC는 KRX only인지 통합인지?
4. 08:00~08:50 NXT 프리마켓 체결이 `/trades` 또는 WebSocket에 나타나는가?
5. 15:30 이후 NXT 체결이 당일 candle에 반영되는가?

### 검증 결과별 구현

#### Case A — Toss가 통합 OHLC를 제공

그 데이터를 그대로 사용.

#### Case B — venue별 체결 데이터를 제공

자체 통합 일봉 생성:

```text
integrated_open  = 해당 거래일 전체 세션 최초 체결
integrated_high  = 전체 세션 최고가
integrated_low   = 전체 세션 최저가
integrated_close = NXT 애프터마켓 포함 마지막 체결
integrated_volume = 전체 세션 체결량 합
```

#### Case C — venue 정보가 불충분

UI에 반드시 표시:

> "현재 Toss API에서 제공되는 통합 시세 기준"

그리고 KRX-only 값과 혼동하지 않는다.

---

# 7. 핵심 진입 패턴

## Pattern A — 5일선 눌림 반등

가장 중요한 패턴.

```text
상승 추세
 ↓
주가가 5일선 접근
 ↓
5일선 부근에서 저점 형성
 ↓
매도세 감소
 ↓
반등
```

### 추천 조건

```python
distance_to_ma5_pct <= 1.2
and trend_up
and theme_strength >= threshold
```

추가 확인:

- 1분봉 또는 5분봉 저점 형성
- 저점 갱신 중단
- 체결/거래량 증가
- 직전 1~5분 고점 돌파

---

## Pattern B — 5일선 터치 후 핵심 피벗 재돌파

친구가 특히 강조한 패턴.

예:

```text
170,200 = 핵심 지지/저항선

가격 하락
 ↓
5일선 터치
 ↓
반등
 ↓
170,200 재돌파
 ↓
거래량 증가
```

이 경우 강한 양봉 가능성이 높다고 판단.

### 엔진 조건

```python
if touched_ma5_within_today:
    if current_price > pivot_price:
        if volume_acceleration > threshold:
            signal = "STRONG_RECLAIM"
```

UI:

```text
🔥 강한 재돌파 후보
5일선 반등 후 170,200원 재탈환
거래량 증가 확인
```

---

## Pattern C — 박스 돌파 후 Retest

```text
BOX_HIGH 돌파
 ↓
상승
 ↓
BOX_HIGH 근처로 되돌림
 ↓
지지
 ↓
반등
```

초기 진입 추천:

```text
BOX_HIGH ± 0.5~1.0%
```

단, 도달했다고 바로 추천하지 말고 **지지 확인 후** 추천.

---

## Pattern D — 지지선 하회 후 Reclaim

SK이노베이션 실전 사례와 유사.

```text
중요 가격 아래로 잠깐 이탈
 ↓
장중 저점 형성
 ↓
중요 가격대 재접근
 ↓
단기 고점 돌파
 ↓
진입
```

"싸졌으니 매수"가 아니라 **회복을 확인한 뒤 진입**.

---

# 8. 손절 규칙

가장 중요한 원칙:

> 가격이 내려갔다는 이유로 손절하는 것이 아니라, **진입 논리가 깨졌을 때 손절한다.**

---

## 8.1 5일선 아래 기준봉/박스 확인

예:

```text
핵심 피벗
 ↓
5일선
 ↓
기준봉 시가 / 박스 상단
 ↓
기준봉 저가 / 박스 하단
```

5일선 이탈만으로 무조건 손절하지 않는다.

친구 규칙:

```text
5일선 이탈
→ 아래 기준봉/박스 확인
→ 박스 하단까지 깨고 회복 실패
→ 손절
```

---

## 8.2 최대 손실 방어

차트 무효화가 너무 멀 경우 위험.

기본값:

```yaml
risk:
  max_stop_loss_pct: 2.0
```

단, 자동매매가 아니라 추천 시스템이므로 UI에는:

```text
기술적 손절: 159,100원
예상 손실: -1.7%
```

같이 명시.

기술적 손절이 -3~5% 이상 멀면 해당 종목의 추천 점수를 감점한다.

---

# 9. 목표가/익절 규칙

고정 +2% 익절 전략이 아니다.

실전 사례:

- 올릭스: 116,700 → 122,600 = 약 +5.1%
- SK이노베이션: 148,700 → 152,000 = 약 +2.2%
- HD현대에너지솔루션: 148,800 → 152,000 = 약 +2.15%

즉:

> 손실은 제한하고 수익은 다음 저항까지 열어둔다.

목표가격 후보:

1. 당일/전일 고점
2. 박스 상단
3. 신고가
4. 과거 대량거래 가격
5. 최근 swing high
6. R:R 기준 2R, 3R

UI 예:

```text
진입 관찰가: 148,800
손절: 146,900 (-1.28%)
1차 저항: 152,000 (+2.15%)
2차 저항: 155,900 (+4.77%)
Risk/Reward: 1 : 3.7
```

---

# 10. 추천 점수 모델

100점 만점.

```yaml
weights:
  theme_strength: 25
  money_flow: 20
  chart_structure: 25
  support_confluence: 15
  risk_reward: 10
  realtime_confirmation: 5
```

## 10.1 Theme Strength — 25점

```python
theme_score = (
    breadth_score * 0.30
    + return_score * 0.20
    + traded_value_score * 0.30
    + us_lead_score * 0.20
)
```

## 10.2 Money Flow — 20점

- 거래대금 증가
- 외국인 순매수
- 기관 순매수
- 프로그램 순매수
- 거래량 증가

## 10.3 Chart Structure — 25점

- 상승 추세
- 기준봉 존재
- 장대양봉 이후 가격 유지
- 박스 돌파
- 신고가/전고점 돌파
- 거래량 감소 횡보

## 10.4 Support Confluence — 15점

다음이 동일 가격 ±1% 안에 많이 모일수록 점수 증가.

- 5일선
- 기준봉 시가
- 기준봉 종가
- 직전 종가
- 전고점
- 박스 상단
- 최근 저점
- 거래량 밀집 가격

```python
if confluence_count >= 3:
    score += 15
elif confluence_count == 2:
    score += 10
elif confluence_count == 1:
    score += 4
```

## 10.5 Risk/Reward — 10점

```text
RR >= 3.0 → 10
RR >= 2.0 → 8
RR >= 1.5 → 5
RR < 1.5 → 추천 제외 또는 강한 감점
```

---

# 11. 추천 등급

```text
90~100: S
80~89 : A
70~79 : B
60~69 : WATCH
<60   : 제외
```

실제 매수 후보 UI에는 기본적으로 **A 이상만** 노출.

사용자가 "전체 후보 보기"를 누르면 B/WATCH까지 표시.

---

# 12. 장중 추천 상태 머신

각 종목은 상태를 가진다.

```text
SCANNING
↓
WATCH
↓
APPROACHING_ENTRY
↓
ENTRY_READY
↓
BREAKOUT_CONFIRM
↓
TAKE_PROFIT_ZONE

or

INVALIDATED
```

예:

```json
{
  "symbol": "322000",
  "state": "ENTRY_READY",
  "entryZone": [148000, 149500],
  "pivot": 151000,
  "stop": 146500,
  "target1": 152000,
  "target2": 156000,
  "score": 87
}
```

---

# 13. 실시간 추천 엔진

## 13.1 전체 시장을 WebSocket으로 무조건 구독하지 말 것

Rate limit/구독 제한 및 시스템 부하 방지를 위해 2단계 구조 사용.

### Stage 1 — REST 기반 후보군 생성

랭킹 API 활용:

- 거래대금 상위
- 거래량 상위
- 상승률 상위

주기:

```text
NXT 프리마켓: 10~20초
KRX 장중: 15~30초
```

Top 100~200 후보 추출.

### Stage 2 — 후보군 실시간 스트리밍

Top 후보만 WebSocket 구독.

예:

```text
총 30~80종목
```

구독 데이터:

- 실시간 체결
- 호가

필요 시 후보 변경 시 구독 목록 갱신.

---

# 14. 장 운영 시간

토스증권 `GET /api/v1/market-calendar/KR`를 source of truth로 사용한다.

하드코딩된 시간만 믿지 않는다.

논리상 세션:

```text
NXT 프리마켓
KRX 정규장
NXT 애프터마켓
```

UI에 현재 세션 표시:

```text
● NXT PRE-MARKET
● KRX REGULAR
● NXT AFTER-MARKET
● CLOSED
```

---

# 15. 사이트 화면

## 15.1 메인 Dashboard

상단:

```text
오늘 시장 상태
KOSPI / KOSDAQ
현재 세션
시장 위험도
미국장 요약
```

그 아래:

### 오늘 강한 테마

카드 예:

```text
1. 광통신/CPO        92
2. AI 전력/원전      88
3. 반도체 소부장     81
4. 2차전지           72
```

각 카드:

- 테마 점수
- 상승 종목 비율
- 총 거래대금
- 대표 종목
- 미국 선행 테마

---

## 15.2 실시간 추천 Top 3

가장 중요한 화면.

카드 예:

```text
#1 대한광통신                     A 87점

현재가        18,150
추천 진입      17,800 ~ 18,100
5일선         17,320
핵심 피벗      18,530
손절           17,200
1차 목표       18,880
2차 목표       19,600

예상 손익비     1 : 2.7

추천 이유
✓ 광통신 테마 강도 1위
✓ 거래대금 상위
✓ 상승 5일선
✓ 박스 돌파 후 눌림
✓ 외국인 수급 유입

현재 상태
🟡 진입 구간 접근 중
```

---

# 16. Chart UI

TradingView Lightweight Charts 또는 Apache ECharts 추천.

표시:

- Candlestick
- Volume
- MA5
- MA20(optional)
- 기준봉 영역
- Box high/low
- Entry zone
- Stop line
- Target lines
- Pivot
- 현재가

---

# 17. 추천 상세 페이지

URL:

```text
/stocks/{symbol}
```

섹션:

1. 실시간 가격
2. 오늘 거래대금/거래량
3. 테마 정보
4. 차트
5. 5일선
6. 박스
7. 기준봉
8. 외국인/기관 수급
9. 프로그램 매매
10. 추천 진입구간
11. 무효화/손절
12. 목표가격
13. 추천 이유
14. 위험요인

---

# 18. 알림

초기에는 브라우저 notification 또는 Telegram/Discord 선택.

예:

```text
[ENTRY READY]

대한광통신
현재가 17,950

5일선/박스 지지 확인
18,100 재탈환

Entry: 17,900 ~ 18,050
Stop: 17,300
Target: 18,880 / 19,600
Score: 88
```

또는:

```text
[BREAKOUT]

원익IPS
143,500 돌파
거래량 5분 평균 대비 2.3배

5일선 반등 후 박스 상단 재돌파
```

---

# 19. 데이터 모델

PostgreSQL 사용.

가능하면 TimescaleDB extension 고려.

## tables

### stock_master

```sql
symbol
name
market
currency
active
updated_at
```

### candles_1m

```sql
symbol
timestamp
open
high
low
close
volume
venue nullable
```

### candles_1d

```sql
symbol
date
open
high
low
close
volume
integrated boolean
```

### investor_flow

```sql
symbol
date
retail_net
foreign_net
institution_net
other_net
```

### program_flow

```sql
symbol
date
arbitrage_net
non_arbitrage_net
total_net
```

### theme

```sql
id
name
```

### theme_stock

```sql
theme_id
symbol
weight
```

### theme_us_mapping

```sql
theme_id
ticker
weight
```

### signals

```sql
id
symbol
timestamp
signal_type
score
current_price
entry_low
entry_high
stop_price
target1
target2
reason_json
status
```

### strategy_backtest

```sql
signal_id
entry_price
max_favorable_excursion
max_adverse_excursion
target_hit
stop_hit
result_pct
```

---

# 20. 기술 스택

권장:

## Frontend

```text
Next.js
TypeScript
Tailwind CSS
shadcn/ui
TanStack Query
TradingView Lightweight Charts
```

## Backend

```text
Python 3.12
FastAPI
Pydantic
httpx
websockets
pandas / numpy
SQLAlchemy
```

## Storage

```text
PostgreSQL
Redis
```

Redis 역할:

- 실시간 가격 cache
- 종목별 상태
- theme score
- distributed lock
- rate-limit state

---

# 21. 디렉터리 구조

```text
flowtrade-radar/
├── apps/
│   ├── web/
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   └── ...
│   │
│   └── api/
│       ├── app/
│       │   ├── api/
│       │   ├── brokers/
│       │   │   └── toss/
│       │   │       ├── auth.py
│       │   │       ├── market_data.py
│       │   │       ├── websocket.py
│       │   │       └── schemas.py
│       │   ├── market/
│       │   ├── strategy/
│       │   │   ├── significant_candle.py
│       │   │   ├── box_detector.py
│       │   │   ├── moving_average.py
│       │   │   ├── support_resistance.py
│       │   │   ├── theme_strength.py
│       │   │   ├── entry_engine.py
│       │   │   ├── risk_reward.py
│       │   │   └── scoring.py
│       │   ├── services/
│       │   ├── db/
│       │   └── main.py
│       └── tests/
│
├── config/
│   ├── themes.yml
│   └── strategy.yml
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

# 22. Toss Adapter

절대 strategy 코드 안에서 Toss API를 직접 호출하지 않는다.

interface:

```python
class BrokerMarketData:
    async def get_prices(self, symbols: list[str]): ...
    async def get_orderbook(self, symbol: str): ...
    async def get_trades(self, symbol: str): ...
    async def get_candles(self, symbol: str, interval: str): ...
    async def get_investor_trading(self, symbol: str): ...
    async def get_program_trades(self, symbol: str): ...
    async def get_rankings(self, ranking_type: str): ...
    async def get_market_calendar(self): ...
```

나중에 다른 증권사 API로 바꿀 수 있어야 한다.

---

# 23. 전략 설정 파일

`config/strategy.yml`

```yaml
screen:
  min_traded_value_krw: 10000000000
  candidate_limit: 100

significant_candle:
  min_gain_pct: 4.0
  min_volume_ratio_20d: 1.8
  close_position_min: 0.65

moving_average:
  short: 5
  medium: 20

entry:
  ma5_distance_pct: 1.2
  pivot_reclaim_confirm_pct: 0.15
  confirmation_minutes: 3

risk:
  max_stop_loss_pct: 2.0
  min_risk_reward: 1.8

theme:
  min_constituents: 3
  min_breadth_pct: 55
```

모든 값은 코드 하드코딩 금지.

---

# 24. 알고리즘 의사코드

```python
async def scan_market():

    market = await load_market_state()
    candidates = await load_top_candidates()
    themes = score_themes(candidates)

    strong_themes = [
        t for t in themes
        if t.score >= THEME_THRESHOLD
    ]

    results = []

    for stock in stocks_in(strong_themes):

        daily = await load_daily_candles(stock)
        intraday = await load_intraday(stock)

        ma5 = calc_ma(daily, 5)
        sig_candle = detect_significant_candle(daily)
        box = detect_box(daily)

        levels = detect_support_resistance(
            daily=daily,
            sig_candle=sig_candle,
            box=box,
            ma5=ma5,
        )

        realtime = get_realtime_state(stock)

        entry = evaluate_entry(
            realtime=realtime,
            ma5=ma5,
            levels=levels,
            box=box,
        )

        rr = calculate_risk_reward(
            entry=entry,
            stop=levels.invalid_price,
            targets=levels.targets,
        )

        score = calculate_score(
            theme=strong_themes,
            flow=stock.flow,
            chart=stock.chart,
            confluence=levels,
            rr=rr,
            realtime=realtime,
        )

        if score >= MIN_RECOMMEND_SCORE:
            results.append(...)
```

---

# 25. 추천 이유를 사람이 이해할 수 있게 생성

LLM이 숫자를 마음대로 판단하지 않게 한다.

전략 엔진이 먼저 구조화된 reason을 만든다.

예:

```json
{
  "theme": "광통신/CPO",
  "themeScore": 91,
  "signals": [
    "5일 이동평균선 지지",
    "전일 기준봉 종가 유지",
    "박스 상단 돌파 후 retest",
    "외국인 순매수",
    "거래량 감소 중 가격 유지"
  ]
}
```

프론트는 이것을 그대로 표시.

향후 LLM은 이 JSON을 자연어 설명으로 바꾸는 역할만 한다.

**LLM이 직접 매수가/손절가를 생성하면 안 된다.**

모든 숫자는 deterministic engine에서 계산.

---

# 26. 미국장 분석

토스 API가 미국 주식도 같은 API로 지원하므로 미국 테마 watchlist를 저장하고 밤사이 performance를 계산한다.

예:

```python
for theme in us_themes:
    returns = get_overnight_returns(theme.tickers)
    volume_ratios = get_volume_ratio(theme.tickers)
    theme.us_score = weighted_score(...)
```

다음날 국내 pre-market score에 반영:

```python
kr_theme_score = (
    domestic_realtime_score * 0.70
    + us_overnight_score * 0.30
)
```

미국장에서 섹터가 강했어도 국내 NXT에서 동조하지 않으면 추천하지 않는다.

---

# 27. 추천 예시 — 시스템이 최종 출력해야 할 형태

```json
{
  "rank": 1,
  "symbol": "010170",
  "name": "대한광통신",
  "theme": "광통신/CPO",
  "score": 88,
  "state": "ENTRY_READY",
  "price": 18050,
  "entry": {
    "low": 17800,
    "high": 18100
  },
  "technical": {
    "ma5": 17320,
    "pivot": 18530,
    "boxHigh": 17800,
    "boxLow": 17000,
    "significantCandleDate": "2026-10-06"
  },
  "risk": {
    "stop": 17200,
    "stopLossPct": -4.7
  },
  "targets": [18880, 19600],
  "reasons": [
    "광통신/CPO 테마 강도 상위",
    "거래대금 증가",
    "박스 돌파 후 눌림",
    "5일선 상승",
    "미국 광네트워킹 섹터 강세"
  ]
}
```

---

# 28. 실제 사례를 Regression Test로 사용

다음 과거 사례는 전략 엔진의 regression fixture로 만든다.

## Case 1 — 올릭스

실제 매매:

```text
매수 116,700
08:10

매도 122,600
09:20

수익률 ≈ +5.1%
```

특징:

- 장대양봉
- 이후 거래량 감소
- 기준봉 종가 위 유지
- NXT 프리마켓 진입
- 지지 근처 매수

---

## Case 2 — SK이노베이션

```text
매수 148,700
09:30

매도 152,000
09:50

수익률 ≈ +2.2%
```

특징:

- 박스권
- 중요 저항/지지 가격 존재
- 석유 + 2차전지 테마
- 장초반 저점 확인
- 중요 가격대 회복 후 진입

---

## Case 3 — 심텍

친구가 제시한 적절 진입선:

```text
170,200
```

규칙:

```text
170,200 이탈
→ 5일선 확인
→ 5일선에서 1차 진입 가능
→ 반등
→ 170,200 재돌파
→ 강한 상승 후보

5일선 이탈
+
기준봉 박스 하단 이탈
→ 손절
```

이 규칙을 unit test로 작성.

---

## Case 4 — HD현대에너지솔루션

사용자 실제 매매:

```text
매수 148,800
매도 152,000
수익률 ≈ +2.15%
```

당일 사전 테마:

```text
미국 에너지 강세
→ 국내 에너지 테마 후보
```

---

# 29. 백테스트

실전 적용 전에 반드시 백테스트 모듈 구현.

### Signal 발생 후 측정

```text
+1%
+2%
+3%
+5%

도달 여부
```

그리고:

```text
-1%
-2%

도달 여부
```

MFE:

```text
Maximum Favorable Excursion
```

MAE:

```text
Maximum Adverse Excursion
```

저장.

## 성과 지표

Dashboard:

```text
Signal count
Win rate
Average return
Average MFE
Average MAE
Profit factor
Max losing streak
Theme-specific win rate
Entry-pattern win rate
```

특히 비교:

```text
MA5 BOUNCE
PIVOT RECLAIM
BOX RETEST
BREAKOUT
```

어떤 패턴이 실제로 가장 성공률 높은지 확인.

---

# 30. Paper Trading

실제 주문 전 반드시 paper trading.

기능:

```text
추천 발생
→ virtual buy
→ stop / target monitoring
→ result 저장
```

최소:

```text
100~300 signals
```

수집 후 strategy parameter 튜닝.

---

# 31. 실주문 기능

MVP에서는 비활성화.

향후:

```env
ENABLE_LIVE_TRADING=false
```

`true`일 때도:

1. 사용자 명시적 활성화
2. 1회 최대 주문금액
3. 하루 최대 손실
4. 종목당 최대 비중
5. kill switch

필수.

예:

```yaml
live_trading:
  max_position_pct: 10
  max_single_order_krw: 1000000
  max_daily_loss_pct: 2
```

조건 주문 API의 OCO / OTO 사용 여부도 향후 검토.

---

# 32. 보안

절대 frontend에 저장 금지:

```text
TOSS_CLIENT_ID
TOSS_CLIENT_SECRET
ACCESS_TOKEN
ACCOUNT_SEQ
```

`.env`:

```text
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
DATABASE_URL=
REDIS_URL=
```

서버 IP는 토스증권 Open API 허용 IP에 등록.

로그에 token 출력 금지.

---

# 33. 사용자 경험

추천 사이트는 단순히:

```text
"대한광통신 사세요"
```

라고 표시하면 안 된다.

반드시:

```text
왜?
어디에서?
어디까지?
어디 깨지면 틀린 것인가?
```

를 동시에 보여준다.

예:

```text
대한광통신

현재 상태:
5일선 눌림 대기

추천 진입:
17,800~18,100

이유:
광통신 테마 거래대금 1위
상승 5일선
박스 돌파 후 retest

무효화:
17,200 하회 후 회복 실패

상승 확인:
18,530 재돌파

목표:
18,880
19,600
```

---

# 34. 추천 신뢰도 표시

UI:

```text
Theme       █████████  92
Flow        ████████   81
Chart       █████████  90
Entry       ███████    76
Risk/Reward ████████   85
```

사용자가 AI의 결론만 보지 않고 근거를 확인하게 한다.

---

# 35. MVP 구현 단계

## Phase 1

토스증권 연동.

- OAuth
- 현재가
- 일봉
- 1분봉
- 거래대금 랭킹
- 투자자별 수급
- 시장 캘린더

## Phase 2

차트 분석.

- MA5
- 기준봉
- 박스
- support/resistance
- volume ratio

## Phase 3

테마 엔진.

- theme config
- US ↔ KR mapping
- breadth
- total traded value
- leader detection

## Phase 4

실시간 추천.

- Top 3
- entry zone
- stop
- targets
- reason

## Phase 5

웹 UI.

## Phase 6

backtest + paper trading.

---

# 36. MVP Acceptance Criteria

다음이 모두 가능해야 MVP 완료.

### 데이터

- [ ] Toss OAuth 자동 갱신
- [ ] 국내 현재가 조회
- [ ] 1분봉/일봉 조회
- [ ] NXT 포함 여부 검증 완료
- [ ] 투자자 수급 조회
- [ ] 거래대금 랭킹 조회
- [ ] 미국 watchlist 가격 조회

### 전략

- [ ] 5일선 계산
- [ ] 기준봉 탐지
- [ ] 박스 탐지
- [ ] 지지/저항 생성
- [ ] pivot reclaim 탐지
- [ ] risk/reward 계산
- [ ] theme score 계산

### UI

- [ ] 오늘 강한 테마 Top 5
- [ ] 추천 종목 Top 3
- [ ] 실시간 현재가
- [ ] 진입구간
- [ ] 5일선
- [ ] 박스
- [ ] 손절
- [ ] 목표가
- [ ] 추천 근거

### 품질

- [ ] 숫자 계산은 LLM 사용 금지
- [ ] recommendation 이유 설명 가능
- [ ] retry/rate limit 처리
- [ ] API 장애 시 graceful degradation
- [ ] 테스트 fixture 통과

---

# 37. 절대 하지 말 것

1. 상승률 상위 종목을 곧바로 추천하지 않는다.
2. 갭상승 종목을 무조건 추격 추천하지 않는다.
3. 5일선 하나만 보고 추천하지 않는다.
4. 테마 동조 없이 개별 급등주를 추천하지 않는다.
5. 손절 위치 없는 추천을 생성하지 않는다.
6. risk/reward 1.5 미만은 원칙적으로 추천하지 않는다.
7. LLM에게 숫자 계산을 맡기지 않는다.
8. KRX 가격과 NXT 포함 가격을 섞어서 표시하지 않는다.
9. API 데이터의 시장/세션 의미가 불명확한데 임의로 가정하지 않는다.
10. 실제 주문 기능을 MVP에서 자동 활성화하지 않는다.

---

# 38. Codex 첫 작업 목록

Codex는 다음 순서로 작업한다.

```text
1. monorepo scaffold
2. Toss OAuth adapter
3. Toss API schema validation script
4. NXT/KRX price semantics validation
5. database schema
6. market data ingestion
7. MA5 calculator
8. significant candle detector
9. box detector
10. support/resistance detector
11. theme config + score engine
12. recommendation engine
13. REST API
14. dashboard
15. chart
16. regression tests
17. backtest
18. paper trading
```

---

# 39. 첫 번째 개발 완료 시 보여줘야 할 결과

Codex는 구현 완료 후 다음을 출력한다.

```text
1. 현재 프로젝트 구조
2. 환경변수 설정 방법
3. Toss API 연결 테스트 결과
4. NXT/KRX 데이터가 실제로 어떻게 반환되는지
5. 예제 종목 5개의 MA5/박스/피벗 계산 결과
6. 추천 Top 3 예제
7. 실행 명령어
8. 테스트 결과
```

---

# 40. 최종 목표

최종 사이트를 열면 사용자는 장중에 다음과 같은 정보를 즉시 보고 싶다.

```text
08:32

오늘의 주도 테마

1. 광통신/CPO       92
2. AI 전력          88
3. 반도체 소부장    81

━━━━━━━━━━━━━━━━━━━━

실시간 추천

#1 대한광통신
Score 88

현재가: 18,050
진입: 17,800~18,100
손절: 17,200
상승 확인: 18,530
목표: 18,880 / 19,600

상태:
🟢 ENTRY READY

이유:
✓ 미국 광통신 강세
✓ 국내 광통신 동반 상승
✓ 거래대금 증가
✓ 5일선 상승
✓ 박스 돌파 후 retest

━━━━━━━━━━━━━━━━━━━━

#2 ...

#3 ...
```

시스템의 궁극적인 목표는:

> **"오늘 어떤 종목이 오를까?"를 맞히는 것이 아니라,  
> 돈이 몰리는 테마 안에서 상승할 확률 대비 손실 위험이 작은 가격대를 실시간으로 찾아주는 것.**

이 원칙을 모든 구현 결정의 최우선 기준으로 사용한다.
