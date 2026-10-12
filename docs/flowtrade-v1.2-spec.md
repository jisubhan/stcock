# FlowTrade Radar — Codex 증분 수정 지시서 v1.2 (2026-10-12)

> **바로 실행할 지시:** 현재 저장소를 먼저 조사하고, 기존 FlowTrade Radar의 모든 동작을 최대한 유지하면서 아래 P0/P1 기능을 코드·DB·API·UI·테스트에 반영하라. 새 프로젝트를 만들지 마라. 기존 v1.1의 NXT 5분봉 EARLY_ENTRY와 일봉 기반 지지·저항 엔진을 삭제하지 마라. 외부 시세/뉴스/투자자별 수급 데이터가 연결되지 않았다면 가짜 라이브 수치를 만들지 말고 기능별로 `UNAVAILABLE` 또는 `DELAYED`를 표시하라. 구현 가능 부분은 실제 동작하게 마무리하고, 불가 부분은 막힌 이유와 정확한 추가 정보(API 권한/키/데이터 권리)를 보고하라. **실계좌 자동주문은 구현하지 마라.**

## A. 목적과 이번 수정의 배경

기존 v1.1은 미국 야간시장 테마 분석 → 한국 종목 매핑 → NXT 프리마켓 5분봉 → 박스/과거 시가·종가/MA5 회복 → 저항 돌파 전 선진입 후보를 제공한다. 그러나 2026-10-12 장 초반처럼 **국내 뉴스/공시 이벤트가 미국장 연동 테마를 압도하고, 실제 한국 시장 개장 후 돈이 전혀 다른 종목으로 이동하는 경우**, 사전에 만든 Top 3가 신속히 교체되지 않을 위험이 발견되었다.

이번 v1.2의 1차 목표는 **개장 후 5~30분 내에 실제로 주목받는 테마·종목을 발견하여 가설/추천 순위를 수정하는 것**이다. `예측`과 `관측된 거래대금 집중`과 `검증된 투자자별 순매수`를 데이터 의미상 철저히 분리한다.

### 반드시 보존할 기능

- 미국 야간 정규장 및 가능한 범위의 시간외 움직임, 섹터 ETF, 관련 종목 분석. 데이터 제공 범위를 벗어나면 원문에 명시.
- KRX/NXT 장 구분, 합산/통합 시세, 동일 체결 중복 집계 방지, NXT 프리마켓 및 애프터마켓.
- 일봉 `MA5_DAILY`와 5분봉 `MA5_5M` 분리, 박스권, 과거 종가·시가의 confluence, 지지 지속시간.
- 5분봉 눌림·이평 회복의 `EARLY_ENTRY_WATCH` / `EARLY_ENTRY_READY`, 저항 돌파 후 `BREAKOUT_CONFIRMED`.
- 구조적 손절·목표가·손익비, paper-trading/replay, idempotent 알림, 주문 실행 금지.

### 오늘 사례에 관한 데이터 무결성

- 검증용 시나리오 예시: **국내 정책/지역 프로젝트 관련 테마성 뉴스**, **공개매수/기업공시로 인한 단일 종목 급등** 등. 실제 2026-10-12 뉴스 문구·발행시각·공시 원문·가격/거래대금은 외부 데이터로 독립 검증하기 전까지 테스트 픽스처 외 실측 사실로 저장하지 마라.
- 일별 상승률 Top 종목 ≠ 누적 거래대금 Top 종목 ≠ 업종 거래대금 증가속도 Top 테마 ≠ 외국인/기관 순매수 상위 종목. 화면과 로직은 분리해야 한다.
- 이벤트로 엮였다는 이유만으로 특정 상장기업이 직접 수혜를 본다고 단정하지 않는다. **공식 공시로 확인된 직접 관련 / 검증된 뉴스의 관련 / 시장 연상테마**를 구분한다.

---

## B. 구현 우선순위 — P0 / P1 / P2

| 우선 | 항목 | 작업 완료 기준 |
|---|---|---|
| **P0-1** | Domestic Catalyst Radar: 한국 뉴스·공시 이벤트 수집 | 증분 수집, dedupe, 출처·시각·관련종목 표기, 부재 시 UNAVAILABLE |
| **P0-2** | Opening Flow Rotation: 09:00~09:30 실제 시장 반응 분석 | 1~5분 단위 테마 재계산, 순위 변동 사유 로그, 가짜 실시간 없음 |
| **P0-3** | Top 3 동적 교체 | 미국장 예측 Top 3와 국내 실제 관측 Top 3 분리, 명시적 승격/강등 |
| **P0-4** | Venue/시간/의미 검증 | KRX/NXT 통합 중복 제거, 5분봉 미완성 처리, 5~30분 과거 데이터만 활용 |
| **P0-5** | UI: 원인 설명 및 데이터 품질 | '왜 급등/왜 순위 변경' 출처 링크, LIVE/DELAYED/STALE 상태 |
| **P1-1** | 뉴스→테마→종목 관련성 점수 | 근거/확신도/반증/직접·간접 관련성 구분 |
| **P1-2** | Paper replay 및 성능측정 | 10/12 형식과 역방향 사례, 미래정보 누출 없음 |
| **P1-3** | 이벤트/로테이션 알림 | 스팸 억제, 변경 사유·기준시간 포함 |
| **P2** | 의미 기반 클러스터링/학습형 점수 | P0 안정화 후; 기본 규칙 기반 구현이 선행 |

---

## C. 데이터 파이프라인 1: 국내 뉴스·공시·정책/테마 재료 탐색

### C1. 소스 인터페이스

소스별 어댑터를 만들어 실제 사용 가능한 연결만 활성화한다.

```text
CatalystSourceAdapter:
    poll_since(cursor) -> list[RawCatalyst]
    health() -> {status, last_success_at, provider, coverage, latency_seconds}

후보 소스:
    DART/전자공시 (공시와 정기·수시 보고서)
    KIND/KRX 공식 공시 (허용되는 수집수단 및 약관 확인)
    계약된 한국 경제뉴스 API/피드 (별도 라이선스 필요 가능)
    정부/지자체/부처 공식 발표·보도자료 (허용되는 피드만)
    사내 허가된 추가 뉴스 제공자
```

- **공식 DART와 민간 뉴스 API는 서로 대체 불가.** DART에 없는 정책성 뉴스나 지역 이슈를 DART만으로 실시간 감지했다고 표시하지 마라.
- API 엔드포인트·요금제·WebSocket 제공 여부·갱신 주기·라이선스를 추측해서 구현하지 말 것. 프로젝트 내 실제 credentials 및 응답 포맷 확인 → provider adapter 구현 → 모의응답 테스트 → 연결상태 표시 순서로 진행.
- 크롤링 제한/robots/뉴스 저작권 준수. 뉴스 본문 전체 재배포 금지; 요약, 링크, 시각, 발행기관과 근거만 제공.
- 수집 실패 또는 장중 feed 지연 시 `news_coverage=UNAVAILABLE/STALE`를 표기하고 뉴스 미탐지를 시장에 재료가 없다는 뜻으로 해석하지 않는다.

### C2. 정규화된 이벤트 스키마

```python
CatalystEvent = {
  "event_id": "stable_hash_or_provider_id",
  "provider": "DART | KIND | LICENSED_NEWS | OFFICIAL_PRESS | ...",
  "source_url": "https://...",
  "title": "...",
  "published_at": "timezone-aware timestamp",
  "first_seen_at": "timezone-aware timestamp",   # 시스템이 처음 알게 된 시점
  "ingested_at": "timezone-aware timestamp",
  "updated_at": "timezone-aware timestamp",
  "event_type": "TENDER_OFFER | EARNINGS | LARGE_CONTRACT | MNA | POLICY | CAPEX | SECTOR_DEMAND | OTHER",
  "tickers": ["..."],
  "theme_ids": ["..."],
  "relationship": "DIRECT_VERIFIED | NEWS_REPORTED | THEMATIC_ASSOCIATION | UNVERIFIED",
  "evidence": [{"source_url": "...", "claim": "...", "strength": 0.0}],
  "novelty_score": 0.0,
  "materiality_score": 0.0,
  "confidence": 0.0,
  "status": "VERIFIED | UNCONFIRMED | CORRECTED | RETRACTED",
  "duplicate_group_id": "...",
  "coverage_status": "LIVE | DELAYED | STALE | UNAVAILABLE"
}
```

- `published_at`(원문 게시시간), `first_seen_at`(우리에게 알려진 시간), `ingested_at`은 구별한다. 재현/백테스트에서는 **`first_seen_at` 이후에만** 시그널 가능.
- 같은 이벤트에 대한 언론사 반복 보도는 dedupe. 원문 공시 정정·뉴스 수정·철회 시 재평가.
- `TENDER_OFFER`: 공개매수가, 기준주가, 기간, 조건, 지분비율을 **공식 원문 확인 시에만** 구조화. 가격 차이는 확정 수익이 아님.
- `POLICY`: 인허가·발표·방문·계획·논의 vs 계약 확정 등 **사건 확정성**을 구분; 지역 연관성만으로 확정 수혜라고 하지 않음.
- 뉴스와 종목 연관도는 공식 직접 언급 > 신뢰도 높은 보도에 명시 > 공급망 추정 > 시장 테마 연상. 관련성 낮으면 WATCH만 허용.

### C3. 감시 기간과 재료 반영

- 휴장 기간 포함 마지막 국내 거래일 이후 **새롭게 발생한 한국 뉴스/공시를 누락 없이** 탐색. 예: 10/8 장 마감 후 10/9 휴일~10/12 개장 전.
- 주말과 공휴일에 발생한 이벤트는 첫 거래일 오전까지 강하게 검토하되 오래된 동일 뉴스 중복 우대 금지.
- 개장 전 수집만 하지 말고 **장중 신규 공시/뉴스**도 별도 증분 처리. 임계 수초의 진짜 속보를 지원하는 데이터가 없으면 `near-real-time`이라 표시하지 않음.
- 미국 섹터 매핑과 별개의 `DOMESTIC_NEWS_CATALYST` 파이프라인으로 운용하고, 두 파이프라인 간 충돌/일치 모두 설명 가능해야 함.

---

## D. 데이터 파이프라인 2: 실시간 국내 수급 방향 (Opening Flow Rotation)

### D1. '수급'을 먼저 정확히 정의

반드시 아래를 서로 다른 필드로 모델링한다.

1. **거래대금(turnover)**: 체결가격 × 체결수량의 합(가능하면 체결 단위). **매수자금 순유입액과 동의어 아님.** 거래당 매도도 존재.
2. **거래대금 증가속도(turnover_velocity)**: 관측구간 내 원/분; 단순 누적 순위와 분리.
3. **상대 거래대금(relative_turnover, RVOL)**: 종목별 평소 같은 *장 구간*과 비교(08시 NXT vs 08시 NXT, 09시 KRX vs 과거 09시 KRX). 충분한 기준 자료가 없다면 NULL 및 `INSUFFICIENT_HISTORY`.
4. **체결강도/호가 불균형**: 제공자 데이터 정의·가용성 검증 시 별도 기록; 거래대금과 혼동 금지.
5. **외국인/기관/개인 순매수**: 제공자별 공개 가능 주기와 지연을 표시. 장중 제공되지 않으면 NULL; 거래대금·가격상승으로 외국인 순매수를 만들어 내지 않음.
6. **테마 참여 폭(theme breadth)**: 동일 테마 안에서 상승 중인 종목 수, 상대 거래대금 상위 종목 수, 테마 총거래대금 기여도.
7. **전체 시장 대비 점유율**: 커버한 종목군/시장 범위가 명확할 때만 계산; 커버리지 없이 `국장 전체 돈의 30%` 같은 문구 금지.

### D2. KRX+NXT 및 구간 데이터 무결성

- venue는 `KRX`, `NXT`, `CONSOLIDATED`를 구분. 통합 데이터가 이미 KRX/NXT를 합쳤다면 venue 데이터를 재합산하지 않는다.
- KRX와 NXT 거래가 서로 다른 타임스탬프·장 운영시간·휴장에 걸릴 수 있으니 공식 장 일정은 config/calendar로 확인. **시간대를 Asia/Seoul, timestamp timezone-aware로 저장.**
- 전후 장: NXT 프리마켓/메인/애프터, KRX 정규장의 유효 세션을 분리. 기존 5분봉 builder와 공유하되 데이터 계층에서 각 세션 경계·갭을 명시.
- 분봉은 `[start, end)`로 정의. 09:10봉의 마감 확정은 09:15 이후. 현재 봉은 `PARTIAL`로 표기하고 확정봉 기반 전략과 분리.
- stale quote와 초단기 체결, 마지막 호가와 확정봉 가격을 서로 바꿔치기하지 않는다. 보이는 가격의 `as_of` 필수.
- 장중 거래대금이 9시 1분에 급격히 증가하는 것은 정상일 수 있음. **같은 시점 이력과 비교**하고 시장 전체 급증과 구분.

### D3. 시간대별 감지 흐름

```text
전일 장후~08:00
  Overnight US Theme Hypotheses (주제별 예측, 예측임을 표시)
  Domestic Catalyst Inbox (휴장 포함 한국 공시/뉴스)
  => Pre-open watchlists, 예상 근거 & 약점

08:00~장 시작 전
  NXT real-time price & turnover; 상대 거래대금/참여 종목
  => Pre-open Flow Candidates

09:00~09:05
  KRX 개장 체결 급증, 신규 갭/시초가, 테마별 초반 강도
  => OPENING_DISCOVERY (너무 이른 수급 결론 경계)

09:05~09:10
  5분봉 최초 확정, 상대 거래대금, 종목 수, 재료 확인
  => OPENING_ROTATION_CANDIDATE

09:10~09:30
  거래대금 증가속도 유지/확산, 종목간 동조, MA5 및 가격지지
  => ROTATION_CONFIRMED / PRIOR_HYPOTHESIS_REJECTED

09:30~장 마감
  5~15분 슬라이딩 윈도우로 주도 테마 재검증, 새 공시 수시 반영
  => TREND_CONTINUES / FADE / NEW_ROTATION
```

**구현은 시간별 하드코딩보다는 시장 session state + rolling window 상태기계로 작성**할 것. 09:02/09:05/09:10/09:20/09:30 등 UI 스냅샷과 이벤트 로그를 남긴다.

### D4. 테마 그래프 및 진짜 자금 집중 지표

테마당 종목을 매핑하되 공급망·수혜가 확정되지 않은 관계는 낮은 가중치만 준다. 한 종목이 여러 테마에 속할 수 있으므로 각 테마의 거래대금을 단순 합해 전체 시장과 비교하지 않음(중복계상 방지). `theme_attribution_weight`를 활용하고 raw view도 별도 보존한다.

```python
ThemeFlowSnapshot = {
  "theme_id": "...", "as_of": "...", "window_minutes": 5,
  "coverage": {"symbols_seen": 0, "symbols_expected": 0, "venues": ["..."]},
  "turnover_krw": 0,                     # 관측된 구간 체결 거래대금
  "turnover_velocity_krw_per_min": 0,
  "relative_turnover": None,              # 이력 없으면 NULL
  "breadth_up_ratio": None,
  "breadth_high_rvol_ratio": None,
  "concentration_top1_ratio": None,       # 테마 내 한 종목 집중인지
  "price_momentum_5m": None,
  "catalyst_event_ids": [],
  "confirmed_investor_netbuy": None,      # 실제 값/공급 가능 시에만
  "confidence": 0.0,
  "quality": "LIVE | DELAYED | STALE | PARTIAL | UNAVAILABLE",
  "reason_codes": []
}
```

- 테마 1종목이 강한 상황: `single-name strength`, 테마 폭 넓게 확산: `broad-based strength`를 명시.
- 테마 간 순위가 바뀌면 *어떤 측정값이 바뀌어서* 바뀌었는지 설명(`turnover_velocity +`, `breadth +`, `prior leaders weaken`).
- 테마 내 순위 상위 종목의 유동성이 부족하거나 주문호가 스프레드가 크면 단타 진입 신호 제한.

---

## E. 추천 및 순위 엔진 v1.2: 예측과 사실의 분리

### E1. Top 3를 두 개로 쪼갠다

**별도의 두 리스트를 UI/API에 동시에 표시**한다.

- `PREMARKET_HYPOTHESIS_TOP3` : 미국장/해외 테마 + 국내 재료 + 일봉 기반 사전 관찰 후보. **예측 리스트.**
- `LIVE_OPENING_FLOW_TOP3` : 실제 KRX/NXT 장 초반 거래대금·상대 증가속도·테마 참여종목 수·뉴스를 반영하여 **실측 데이터가 충분할 때** 만든 현재 강도 순위.
- `ENTRY_READY_TOP3` : 종목별 리스크와 진입 조건을 통과한 *실행 관찰 후보*. LIVE 테마 1등이라도 매수가 위험하면 ENTRY_READY에 넣지 않는다.

> `LIVE_OPENING_FLOW_TOP3`의 신선한 시장 커버리지·상대 거래대금이 부족하면 비워두고 `검증 불가` 표시. 사전 예측 Top3를 라이브 Top3인 것처럼 재포장 금지.

### E2. 실시간 동적 스코어 (가설적 초기값, 백테스트 후 보정)

```yaml
flowtrade_v1_2:
  feature_flags:
    domestic_catalyst_radar: true
    opening_flow_rotation: true
    replace_premarket_top3_after_open: true
    require_provenance_for_news: true
    disable_order_execution: true
  data:
    timezone: Asia/Seoul
    max_live_quote_age_seconds: 15   # 공급자 실제 지연에 맞게 수정
    max_news_age_for_new_catalyst_hours: 72
    min_theme_coverage_ratio: 0.65   # 1차 가설: 관측되지 않으면 UNAVAILABLE
    historical_same_time_sessions: 10 # 부족 시 상대 거래대금 N/A
  refresh:
    quotes_seconds: 5                # 제공자 rate limit에 맞춰 조정
    theme_snapshots_seconds: 60
    news_poll_seconds: 90            # 제공자 rate limit에 맞춰 조정
    top3_seconds: 60
  scoring:
    # 백테스트 전에는 임의 가중치이며 수익성 보증 X
    live_theme:
      turnover_velocity: 0.25
      relative_turnover: 0.20
      breadth: 0.20
      price_momentum: 0.10
      confirmed_catalyst: 0.15
      data_quality: 0.10
    penalty:
      single_name_only: true
      low_coverage: true
      stale_inputs: true
      extreme_spread: true
      unverified_catalyst: true
    min_confirmation_windows: 2
    promote_hysteresis_points: 8
    min_leader_dwell_seconds: 180
  entry:
    require_completed_5m_bar_for_confirmed_recovery: true
    min_reward_risk: 2.0
    max_stop_distance_pct: 2.0
    no_chasing_if_to_first_resistance_rr_below: 1.0
  notification:
    theme_cooldown_seconds: 600
    require_material_rank_change: true
```

- 임계값/비중은 **검증 전 초깃값**이다. 시계열 재생 시험과 종목별 표본으로 조정 가능해야 한다.
- `confirmed_catalyst`는 신뢰할 수 있는 새 정보가 있는지를 뜻하며, **뉴스가 없다는 이유로 실측 강한 수급을 제외하지 않는다.** 뉴스 없이 움직이는 테마는 `UNEXPLAINED_MOMENTUM`.
- 탐색 점수와 매매 점수는 별도. `LIVE_OPENING_FLOW_TOP3`와 `ENTRY_READY_TOP3`를 혼동 금지.
- 1분마다 미세한 점수변동으로 1~3위가 춤추지 않도록 minimum dwell/hysteresis, 반면 명백한 새 재료·급격한 순위 역전에는 즉시 재평가.

### E3. 기존 가설의 탈락·새 가설의 승격

상태 전이 예시:

```text
HYPOTHESIS (overnight)
  ├── not supported by opening turnover/breadth → WATCH_ONLY
  ├── contradicted by opening data  → REJECTED_FOR_TODAY
  └── confirmed by opening data     → LIVE_THEME_CONFIRMED

UNCOVERED_DOMESTIC_CATALYST
  └── volume velocity + breadth + integrity pass
            → NEW_THEME_DISCOVERED → LIVE_THEME_CONFIRMED

LIVE_THEME_CONFIRMED
  ├── market leaders lose support   → FADING
  └── setup-level MA5/support/risk pass → ENTRY_WATCH → ENTRY_READY
```

이전 사전 Top3가 실제 개장과 다를 때 **이전 예측을 고집하지 말고** 관측된 강한 새 테마로 교체한다. 단, 이미 발생한 신호를 기록에서 삭제하지 말고 `demoted_at`, `reason`, `rank_history`를 남긴다.

### E4. 종목별 진입은 기존 친구의 패턴을 유지

```text
강한 테마가 발견되었다 ≠ 매수하라는 뜻.

후보 종목:
- 일봉: 추세, MA5_DAILY, 최근 n일 중요 OPEN/CLOSE/HIGH/LOW,
  박스권, 반복 테스트된 지지·저항, 급등 후 과열 정도
- 5분봉: NXT/KRX 일관된 가격, MA5_5M, 지지 지속시간,
  눌림 → 회복, 기준가 돌파 전 손익비
- 위험: 첫 저항까지 기대수익 / 구조적 손절 거리,
  슬리피지, 스프레드, 거래정지/VI/급등 리스크
- 5분봉 미완성 시: '회복 시도'는 PREVIEW,
  '회복 확정'은 확정봉 종료시각 이후만
```

극단적 갭상승, 단기 상한가 근접, 유동성 부족, 호가 공백은 추천 경고 혹은 진입 제외. 여전히 **자동 매매 금지**.

---

## F. API/DB/UI 변경 제안 (현재 저장소를 확인해 맞춰 통합)

### F1. 백엔드 모듈(예시명)

```text
adapters/catalysts/*         국내 공시/뉴스 source 어댑터
services/catalyst_ingestion  cursor, dedupe, provenance, revision
services/catalyst_linker     종목/테마 관련성, 증거 보관
services/market_sessions     KRX/NXT 캘린더 및 session state
services/flow_metrics        turnover, velocity, relative time-of-day
services/theme_graph         종목-테마 다대다 / 중복 거래대금 방지
services/rotation_engine     사전 가설 vs 장 초반 관측, 승격/강등
services/explainability      순위 변경 요약 & 원인 코드
services/market_data_health  feed coverage, latency, stale
replay/opening_session       point-in-time 이벤트 재생
```

프로젝트 구조/사용언어가 다르면 동일 책임 단위로 구현한다.

### F2. 저장 구조

- `catalyst_events` / `catalyst_revisions` / `catalyst_evidence`
- `ticker_theme_relations` (relationship, weight, effective_from, provenance)
- `minute_symbol_metrics` (as_of, venue, turnover, quote_freshness, bar_status)
- `theme_flow_snapshots` (as_of, window, coverage, velocity, breadth, reliability)
- `theme_rank_history` / `symbol_rank_history` / `recommendation_audit`
- `market_data_provider_health`

기존 테이블/인덱스가 있으면 확장·재사용. migration up/down 또는 rollback 전략 포함. 중복 이벤트/체결 idempotency key 정의.

### F3. API 예시 (기존 route와 호환)

```text
GET /api/market/overview?as_of=...
GET /api/catalysts?since=...&verified=...
GET /api/themes/opening-flow?window=5m&session=KRX
GET /api/themes/rotation-history?date=2026-10-12
GET /api/recommendations?view=premarket|live-flow|entry-ready
GET /api/feeds/health
GET /api/analytics/opening-replay?session=2026-10-12
```

응답에는 `as_of`, `data_freshness`, `coverage`, `measurement_definition`, `provider`, `explanation_codes` 필수. endpoint와 응답 형태는 현 API 계약 유지 우선.

### F4. 메인 화면 (핵심 UX)

```text
[시장 상태] 09:12 KST · KRX 정규장 · NXT 메인 · 데이터 지연 4초

[전날 예상 테마] (가설)
1 광통신  2 정유  3 ESS

[개장 후 실제 강도] (측정)
1 신규 테마 X — 거래대금 증가속도 ↑ · 5종목 동반 상승
2 신규 테마 Y — 공시 확인 · 단일 종목 이슈
3 ESS         — 3종목 동반 상승

[예상 vs 실제 차이]
광통신: 순위 하락 — 시초 거래대금 증가속도/참여 폭 부족
신규 테마 X: 순위 진입 — 국내 뉴스 + 현물 거래대금 증가

[매매 진입 후보] (종목별 리스크 통과시에만)
A. WATCH  | 기준선/지지확인 진행 중
B. EARLY_ENTRY_READY | 최초 저항까지 손익비 ...
C. NO_TRADE | 갭 과열, 손익비 불리

[데이터 상태] 한국뉴스 LIVE / 공시 LIVE / 외국인수급 DELAYED /
              시장가 NXT LIVE / KRX LIVE / 커버 종목 78%
```

- UI에 뉴스 사실과 테마 연상관계를 시각적으로 명확히 구분. 발행시각+첫 수신시각 표시.
- 랭킹 클릭 시 **원인 분해**: 거래대금 (원) / 원분속도 / 동일시각 상대배수 / 참여종목수 / 핵심 뉴스 원문 / 데이터 품질 / 가격 리스크.
- **급등률 Top과 거래대금 Top을 혼합하지 말 것.** 별도 탭 또는 별도 컬럼.
- 지연 데이터는 `실시간`, `지금` 표시 금지. 오래된 데이터로 추천한다면 상태 `HOLD / DATA_STALE`.

---

## G. 알림: 새 주도 테마 포착과 기존 예측 실패 감지

```text
NEW_DOMESTIC_CATALYST
NEW_THEME_DISCOVERED
OPENING_ROTATION_CONFIRMED
PREMARKET_HYPOTHESIS_REJECTED
THEME_LEADER_CHANGED
THEME_BREADTH_COLLAPSED
EARLY_ENTRY_WATCH / EARLY_ENTRY_READY / BREAKOUT_CONFIRMED
DATA_FEED_STALE / DATA_COVERAGE_LOW
```

알림 예시:

- `[09:08][가설 변경] 사전 1위 광통신 → 실측 5위: 동일시간 대비 거래대금 증가속도 부족 (커버리지 82%)`
- `[09:11][신규 테마] 국내 이슈 관련 테마 진입: 5종목 상승/거래대금 속도 ↑ / 뉴스 1건 (원문 링크)`
- `[09:17][주의] 데이터 갱신 3분 지연 — 실측 수급 랭킹 중지`

숫자는 **UI·테스트용 예시**이므로 실측값으로 표시하지 마라. 중복 메시지는 event fingerprint와 쿨다운, 상태 전이 기준으로 차단한다.

---

## H. 시계열 백테스트/재현 테스트: 반드시 포함

### H1. 핵심 검증 질문

- **09:05, 09:10, 09:20, 09:30 각 시각까지 실제 수집됐던 데이터만으로 어떤 Top 3를 만들었는가?**
- 09:10 시점에 최종 15:30 상승종목을 미리 알고 있었다는 가정이 섞이지 않았는가?
- 특정 종목 하루 +30%라고 테마 거래대금/수급이 1위라고 잘못 판정하지 않는가?
- 뉴스 기사가 09:53에 게시됐다면 09:10에는 사용할 수 없는가? (`published_at`, `first_seen_at` 모두 체크.)
- 이벤트 제목만으로 해당 기업을 공식 수혜기업이라 단정하지 않는가?
- 프리마켓과 KRX 정규장 체결을 중복 합산하지 않는가?
- 이수페타시스 사례에서 09:30 회복을 09:25 진입의 확정근거로 당겨 쓰지 않는가?

### H2. 필수 테스트

1. **T01 뉴스 휴일/주말 롤오버:** 마지막 거래일 이후 발생한 한국 뉴스/공시가 첫 거래일 장전 목록에 표시된다.
2. **T02 수정공시·중복뉴스:** 같은 뉴스 반복 보도는 1이벤트로 취급, 정정/철회 시 신호 재평가.
3. **T03 단일종목 vs 테마 동반상승:** 한 종목 급등만으로 테마 '폭넓은 수급' 확정하지 않는다.
4. **T04 흐름 역전:** 사전 예측 테마 A가 개장 후 약하고 새 B가 거래대금·breadth 상승하면 실측 Top3가 B로 교체.
5. **T05 거래대금 ≠ 순매수:** 투자자별 매매 데이터 미제공이면 순매수 null, 거래대금으로 채우지 않는다.
6. **T06 venue 중복:** integrated feed + venue feeds를 받아도 중복 거래대금 합산 없음.
7. **T07 5분봉 확정:** 09:10봉 완료 전에 09:10~09:15 고가와 종가 사용 금지.
8. **T08 뉴스 lookahead:** 기사 first_seen_at=09:53이면 09:10 replay에는 존재하지 않는다.
9. **T09 데이터 장애:** 뉴스/STOCK quote 결측 시 명확한 경고와 안전한 기능 축소.
10. **T10 랭킹 안정:** 소폭 점수차 순위 요동 억제, 큰 변화는 빠른 승격.
11. **T11 multi-theme 중복:** 다대다 종목 관계에서도 시장 전체 점유율이 비정상적으로 100% 초과하지 않게 집계·표시.
12. **T12 성공/실패 동시 평가:** 2026-10-12 사례 **형태의 합성 fixtures**와 일반 거래일 5개 이상, 섹터 동조 안 되는 날, 급등 후 급락한 날 평가.
13. **T13 전략 보존:** 기존 2026-10-08 SK이노베이션·이수페타시스의 EARLY_ENTRY/backtest 회귀 테스트 통과.
14. **T14 소스 차단:** 뉴스 API 인증 실패·레이트 리밋·license 불가 시 fabricated news 없이 폴백.

### H3. 성능 리포트

- `DetectionLatency`: 이벤트를 시스템이 첫 수신한 시점부터 선별까지 지연(기사 최초 발생시간과 분리).
- `Top3TurnoverRecall@09:10`, `ThemeBreadthPrecision`, `Top3Stability`, `IncorrectPremarketHoldMinutes`.
- `EntryCandidatePrecision`, MFE/MAE, 슬리피지·수수료·세금 반영 종목별 검증.
- 사후 장마감 상승률만으로 '수급 탐지 성공' 평가하지 않는다.
- 리플레이 결과에 `data source`, `coverage`, `event availability`를 명시.

---

## I. 구현 순서와 작업 보고 형식

1. 프로젝트 구조, 현재 데이터 소스/토스증권 API 범위, 기존 chart/entry engine, 저장·테스트·스케줄러 분석. 현재 구현 상태를 표로 남긴다.
2. `docs/flowtrade-v1.2-gap-analysis.md`에 실제로 사용 가능한 한국 뉴스·공시, KRX/NXT, 투자자별 수급의 **API 가용성과 지연** 기록.
3. 소스 어댑터 + 이벤트 스키마/저장/중복제거/소스 상태 구축.
4. 거래대금, 증가속도, 상대배수, 참여종목수, 실측 커버리지 도입. 1~5분 rolling metrics 구현.
5. `OpeningFlowRotationEngine` 상태기계와 가설 Top3 / 실측 Top3 / 진입 Top3 분리 구현.
6. 09:00 개장 전환·09:05~09:30 랭킹·테마 재료 알림 구현.
7. UI에 근거, 타임스탬프, 데이터 지연, 예측 실패/수정 이유 연결.
8. T01~T14 테스트 + 기존 v1.1 회귀 테스트, lint/typecheck/build.
9. 재현 가능한 데모 리플레이/모의 데이터를 **DEMO** 배지로 제공하되 라이브와 혼합 금지.
10. 완료 보고: 변경 파일, DB migration, config/env 이름(비밀값 제외), 실행 방법, 실제 연결된 소스/미연결 소스, 테스트 결과, 제한·다음 단계.

### 최종 수용 기준 (DoD)

- [ ] 기존 기능(미국장 + NXT + 일봉 박스 + MA5 + 선진입)이 깨지지 않는다.
- [ ] 국내 휴장 중/개장 중 국내 뉴스와 공시가 별도 파이프라인에 기록되며 출처·시각을 볼 수 있다.
- [ ] 09:00~09:30 실제 데이터에 따라 사전 Top3가 바뀔 수 있다.
- [ ] Top3 3종류(사전·실측·진입)와 거래대금/순매수/상승률이 명시적으로 분리된다.
- [ ] 새 테마가 선택된 이유, 이전 예상이 탈락한 이유를 시점별 로그와 UI에서 확인할 수 있다.
- [ ] 데이터가 없거나 지연되면 확정 추천·실시간 수급 표시를 중지한다.
- [ ] 뉴스 사후출현·미완성 캔들·KRX/NXT 중복·거래대금=순매수 오해의 테스트를 통과한다.
- [ ] 수익 보장 표현이나 라이브 자동매매는 없다.

---

## J. Codex 응답 템플릿

```text
[1] Existing repository findings: stack, adapters, providers and coverage
[2] Implemented changes: itemized P0/P1 and file paths
[3] Added config/migrations and required env vars (no secrets)
[4] Realtime data proven: feeds, timestamps, delays, venue semantics
[5] Provider unavailable / not implemented: exact blocker & graceful fallback
[6] Tests/build/replay: executed commands and results
[7] Example screenshot/API response with DEMO/LIVE status as appropriate
[8] Remaining risks and honest limits (especially investor net-buy and news coverage)
```

> **최종 지시:** 지금 저장소에 맞게 실제 수정하고 테스트를 실행하라. API 권한/출처가 없으면 mock 데이터를 라이브로 둔갑시키지 말고, 연결 가능한 기능부터 완료하라. 불확실한 일중 수급에 대해 확정적인 1위 발표를 만들지 마라.
