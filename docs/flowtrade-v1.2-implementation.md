# FlowTrade Radar v1.2 구현·검증 기록

국내 재료와 개장 관측을 미국장 사전 가설에서 분리했다. 같은 이벤트·관측의 first-seen/revision availability와 측정 범위를 저장하고, 충분한 관측이 없으면 Top 3를 비워 안전하게 축소한다. 자동주문 경로는 추가하지 않았다. 연결 한계는 [gap analysis](flowtrade-v1.2-gap-analysis.md)에 별도로 기록한다.

## 추가한 책임 단위

| 파일/모듈 | 동작 |
| --- | --- |
| `app/catalysts/` | Raw/Event·source health schema, 검증된 계약에 한정한 JSON GET adapter, DEMO fixture, cursor/dedupe, provenance·정정·철회와 as-of 저장 |
| `app/radar/models.py`, `sessions.py`, `flow.py` | KST 시간·session, turnover/속도/coverage/동일 시각 상대값, integrated 중복 방지·multi-theme attribution |
| `app/radar/collector.py`, `fast_collector.py` | 동일 소유 OAuth client의 별도 국내 REST 관측, 최초 counter baseline, 공급자 timestamp freshness·rate backoff |
| `app/radar/rotation.py` | 세 종류 Top 3, 가설 강등/새 관측 승격, 두 window 확인·hysteresis·dwell, 확정 재료가 있는 큰 변화의 즉시 재평가 |
| `app/radar/runtime.py`, `presentation.py`, `routes.py` | lifecycle worker, 상태/이력·source 연결, read API envelope, 과거 query의 현재 추천 오인 방지 |
| `app/strategy/intraday.py` | session별 완성 5분봉, `MA5_DAILY`/`MA5_5M`, 지지 지속시간·회복·저항·손절/RR와 위험 gate |
| `app/replay/` | 합성 개장 timeline과 시나리오, available prefix만 재생, 관측 기반 성능 지표와 entry 성능 미제공 표시 |
| `app/db/migrations/`, `app/radar/storage.py` | 기존 SQLite에 additive v1.2 schema, append-only 상태/revision·audit·idempotent 저장 |
| `components/OpeningRadar.tsx`, `opening-radar.css`, `lib/radar-types.ts` | 개장 레이더, 현재 관측/DEMO 리플레이, 세 목록·원인 분해·company별 관련성·출처·first-seen·품질 |
| `components/PriceChart.tsx`, `RadarApp.tsx` | 기존 화면에 레이더/확정 5분봉·장중 상태를 연결하고 미완성/미검증 판단을 구분 |

위 backend 경로는 `apps/api/`, frontend 경로는 `apps/web/` 기준이다. 원래 `strategy/engine.py`와 `strategy/backtest.py`를 삭제하거나 새 추천 엔진으로 덮어쓰지 않았다. 기존 HEAD에 완전한 v1.1 장중 모듈이 없었으므로 신규 장중 구현과 합성 회귀 검증을 보완한 것이다.

## 데이터 무결성

이벤트는 `published_at`, 최초 `first_seen_at`, 해당 버전의 `ingested_at`, `revision_published_at`, `revision_available_at`을 구분한다. 운영 HTTP 응답 수신 후 시스템 clock으로 first_seen을 찍으며 source 입력으로 덮어쓰지 않는다. 수정 시 최신 claim이 이전 claim을 대체하지만 과거 revision은 보존한다. 반복 보도는 정확한 공통 fact ID 또는 provider item ID로 묶고 제목 유사도만으로 별개 사건을 합치지 않는다.

관계는 company별 `ticker_relations`의 `DIRECT_VERIFIED`/`NEWS_REPORTED`/`THEMATIC_ASSOCIATION`/`UNVERIFIED`를 사용한다. 한 company의 공식 근거를 다른 alias company에 전파하지 않는다. 실제 공식 target 근거가 없는 공개매수 조건은 저장하지 않는다. 관계가 약하면 WATCH만 허용한다.

시장 interval과 5분봉은 `[start,end)`다. 확정 봉의 end·최초 수신시각 전에는 고가·종가를 사용할 수 없다. 미완성은 PARTIAL/PREVIEW로 분리한다. 과거 `as_of` 조회는 당시 알려진 observations·theme map·revision만 읽고 현재 metadata를 과거로 복사하지 않는다. 현재-only provider health의 과거 상태가 없으면 `UNAVAILABLE`로 표시한다.

세 API 목록은 `premarket_hypothesis_top3`, `live_opening_flow_top3`, `entry_ready_top3`다. 가설의 강도와 실측 거래대금의 강도, 가격 위험을 통과한 진입은 별개의 판단이다. live coverage·동일 시각 상대 거래대금이 부족하면 관측 Top 3가 비어 있다. VI·거래정지·spread·유동성·완성봉·손절/RR 중 필수 gate가 unknown이면 `READY`를 만들지 않는다. 실제 순매수가 없으면 null로 남긴다.

## 새 API

| GET 경로 | 목적 / parameter |
| --- | --- |
| `/api/market/overview` | 현재 개장 상태·세 Top 3·재료·이력·품질; optional `as_of` |
| `/api/catalysts` | source별 재료; `since`, `verified`, `as_of` |
| `/api/themes/opening-flow` | `window=1m|5m`, `session=KRX|NXT|CONSOLIDATED`, `as_of` |
| `/api/themes/rotation-history` | `date`, `as_of` 기준 불변 승격/강등 기록 |
| `/api/recommendations` | `view=premarket|live-flow|entry-ready`, `as_of` |
| `/api/feeds/health` | 뉴스·공시·가격·순매수·동일 시각 이력의 기능별 상태 |
| `/api/analytics/opening-replay` | 명시적인 DEMO; `session`, `snapshot_time`, `scenario`, optional aware `as_of` |

응답은 `as_of`, `data_freshness`, `coverage`, `provider`, `measurement_definition`, 설명 code를 제공한다. 현재 API의 미래 또는 timezone 없는 `as_of`는 거부한다. 과거 조회는 현재 추천으로 사용하지 않도록 STALE/history 안내를 붙인다. 기존 `/api/stocks`, 종목 상세·테마·전략·모의매매·백테스트 경로는 유지한다.

앱내 알림은 `NEW_DOMESTIC_CATALYST`와 실제 구현한 theme 상태 전이(개장 확인, 가설 반증, leader 변경, breadth 붕괴, feed stale)를 fingerprint·cooldown으로 저장한다. `NEW_THEME_DISCOVERED`, coverage 저하 등은 상태/reason code로도 기록한다. 명세 G의 모든 문자열이 별도의 알림 type으로 전송되는 구현은 아니다.

## 설정과 실행

기본값은 `config/flowtrade-v1.2.yml`의 `flowtrade_v1_2` 아래에 있다. source 계약은 별도의 non-secret 파일이다.

| 이름 | 의미 |
| --- | --- |
| `RADAR_CONFIG_PATH` | v1.2 YAML override의 절대 경로 |
| `CATALYST_PROVIDER_CONFIG` | 실제 검토된 news/feed 계약 배열 JSON의 절대 경로; default endpoint 없음 |
| `API_DATA_DIR` | SQLite·cursor·audit·override 저장 경로 |
| `TOSS_DATA_MODE` | `demo`는 실제 가격 수집을 끄고 `auto`는 검증 조건을 통과한 자료만 사용 |
| `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` | 기존 서버-only binding; 이 변경에서 값을 파일/로그에 저장하거나 새 인증을 성공했다고 주장하지 않음 |

`domestic_catalyst_radar`, `opening_flow_rotation`, `replace_premarket_top3_after_open`가 기능 flag이고 `require_provenance_for_news`와 `disable_order_execution`은 true를 유지한다. 기본 quote freshness 15초·coverage 0.65·history 10 session·weight는 검증 전 초깃값이다. 이익률이나 공급자 SLA가 아니다.

```sh
bash scripts/setup.sh
npm run dev
```

미연결 상태에서 `/api/feeds/health`와 관측 목록이 `UNAVAILABLE`/빈 목록인 것은 안전한 동작이다. 새 source 연결·인증·라이선스가 제공되지 않았으면 DEMO 리플레이와 실제 화면을 혼합하지 않는다.

2026-10-12의 공개 공식 REST 문서는 HTTP 200·OpenAPI 1.2.24로 확인했고, 선택된 인증·조회 응답 11개와 참조 schema 36개는 저장된 1.2.19와 validation 구조·format이 동일했다. 읽기 전용 축약 계약의 버전과 원본 해시를 1.2.24로 갱신했고, probe 기록은 실제 사용 계약 버전을 따른다. 주문 경로는 추가하지 않았다. 원본 URL·해시와 수급 시각/단위의 한계는 [gap analysis](flowtrade-v1.2-gap-analysis.md)에 기록했다. 실제 인증·시세·뉴스의 현재 운영 성공을 대신하는 검증은 아니다. 이전 OAuth 403은 2026-10-07의 기록이다.

## DB migration과 rollback

`001_radar_v12.sql`과 catalyst의 `CATALYST_SCHEMA_SQL`을 공유 connection에서 idempotent하게 적용한다. migration ID는 `radar_v12_001`이다. 기존 paper trade·notification read·legacy backtest 테이블은 남는다.

- `catalyst_events`, `catalyst_revisions`, `catalyst_evidence`, `catalyst_cursors`, `catalyst_provider_health`.
- `ticker_theme_relations`, `turnover_samples`, `minute_symbol_metrics`, `theme_flow_snapshots`.
- `theme_rank_history`, `symbol_rank_history`, `recommendation_audit`, `market_data_provider_health`.
- `radar_state`, `radar_state_revisions`, `radar_notifications`, `schema_migrations`.

repository root에서 적용:

```sh
PYTHONPATH=apps/api .venv/bin/python scripts/migrate-radar.py
```

rollback을 선택하면 서버/worker를 먼저 종료하고 같은 `API_DATA_DIR`로 실행한다.

```sh
PYTHONPATH=apps/api .venv/bin/python scripts/migrate-radar.py --down
```

`--down`은 SQLite backup API로 `API_DATA_DIR/backups/radar-before-v12-rollback-*.sqlite3`를 만든 후 신규 v1.2 테이블만 제거한다. 이때 v1.2 이벤트·관측·audit는 활성 DB에서 사라지고 backup에 남는다. 기존 데이터 보존은 테스트로 확인한다. up은 additive 적용이며 자동 pre-up backup 생성 동작을 주장하지 않는다. 같은 새 버전의 서버를 재시작하면 자동 up이 다시 실행되므로 지속적인 code rollback은 이전 코드 버전과 함께 계획한다.

이번 로컬 실제 upgrade에서는 기존 `.runtime/radar.sqlite3`를 SQLite backup API로 `.runtime/backups/radar-before-v12-upgrade-2026-10-12.sqlite3`에 먼저 보관한 뒤 up을 적용했고, legacy 테이블 보존을 확인했다. 이 수동 사전 backup과 script의 자동 down backup은 별개의 동작이다.

## T01–T14 테스트 대응

테스트 입력의 `mode=live` 또는 `DIRECT_VERIFIED`도 constructed input일 수 있다. 아래는 합성/MockTransport와 단위 검증이며 실제 시장·뉴스 연결 증거가 아니다.

| ID | 대표 파일 / 정확한 테스트 |
| --- | --- |
| T01 휴일 rollover | `test_catalysts.py::test_T01_holiday_rollover_from_actual_last_trading_close` |
| T02 반복/수정/철회 | `test_catalysts.py::test_T02_common_story_dedupe_preserves_distinct_facts_and_revisions` |
| T03 단일종목/참여 폭 | `test_flow_metrics.py::test_t03_single_name_strength_does_not_become_broad_theme_strength`, `test_t03_three_participating_symbols_are_broad_based` |
| T04 새 국내 흐름 교체 | `test_rotation.py::test_t04_new_domestic_theme_replaces_weak_premarket_hypothesis`, `test_opening_replay.py::test_t04_domestic_observed_flow_supersedes_weak_us_premarket_hypothesis` |
| T05 거래대금/순매수 분리 | `test_flow_metrics.py::test_t05_turnover_and_price_gains_never_invent_investor_netbuy`, `test_t05_investor_currency_and_share_counts_are_not_added_together` |
| T06 venue/delivery 중복 | `test_flow_metrics.py::test_t06_integrated_feed_is_not_added_to_its_component_venues`, `test_t06_distinct_krx_and_nxt_turnover_can_be_summed_once` |
| T07 완성 5분봉/late receipt | `test_intraday.py::test_t07_future_minutes_cannot_leak_a_five_minute_high_or_close`, `test_t07_late_received_minute_stays_unavailable_until_first_seen` |
| T08 news/revision lookahead | `test_catalysts.py::test_T08_first_seen_and_revision_availability_prevent_lookahead`, `test_opening_replay.py::test_t08_early_publication_with_0953_first_receipt_is_still_invisible` |
| T09 결측/stale | `test_flow_metrics.py::test_t09_missing_observations_are_explicitly_unavailable`, `test_rotation.py::test_t09_stale_quotes_clear_observed_rank_without_reusing_hypotheses` |
| T10 안정성/큰 변화 | `test_rotation.py::test_t10_small_score_crossing_does_not_churn_incumbent_leader`, `test_t10_major_new_verified_rotation_bypasses_dwell_and_two_window_wait` |
| T11 multi-theme attribution | `test_flow_metrics.py::test_t11_multitheme_attribution_preserves_raw_view_and_covered_share` |
| T12 성공/반대/급락 시나리오 | `test_opening_replay.py::test_t12_pump_then_fade_loses_domestic_leader_and_reports_adverse_momentum`, `test_t12_at_least_five_general_days_are_test_shapes_with_no_future_entry_proof` |
| T13 장중 회귀 보완 | `test_intraday.py::test_t13_invented_oct8_recovery_is_watch_before_completion_then_ready`, `test_t13_full_future_history_cannot_confirm_0925_or_unseen_0930_recovery` |
| T14 소스 차단 | `test_catalysts.py::test_T14_auth_or_rate_block_has_no_fabricated_events_or_repeated_calls`, `test_T14_unapproved_license_contract_or_missing_binding_sends_no_request` |

추가 `test_fast_collector.py`는 counter baseline·provider timestamp·15초 gate·rate backoff·소유 client 공유를, `test_radar_api.py`는 route/provenance·과거 map/health·migration/backup을 검증한다. 원래 전략/백테스트 tests도 전체 suite에 포함한다.

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests -q
npm run typecheck
npm run build
```

2026-10-12 로컬 통합 검증에서 전체 API pytest **479개 통과**, TypeScript typecheck·production build 성공, desktop/mobile 브라우저 검사 **52개 통과**를 확인했다. catalyst 14개·개장 replay 35개를 포함한 합성/모의 테스트이며 실제 공급자 연결·시장 수익성을 입증하지 않는다.

## DEMO 리플레이와 성능 해석

리플레이는 `08:55`, `09:02`, `09:05`, `09:10`, `09:20`, `09:30`의 알려진 prefix만 사용한다. 7가지 시나리오는 국내 rotation, 미국 선행 유지, 역방향 rotation, 섹터 비동조, 단일종목, 급등 후 약화, 자료 장애다. 일반 날짜 5개도 합성 label이며 실제 영업일/실제 거래를 확인한 날짜가 아니다.

```text
GET /api/analytics/opening-replay?session=2026-10-12&snapshot_time=09:10&scenario=domestic_rotation
```

`DetectionLatency`는 최초 수신→선별, `Top3TurnoverRecall@09:10`은 당시 window의 참조 theme 대비이며 종가 상승종목을 정답으로 쓰지 않는다. breadth precision·stability·잘못된 가설 보유 시간도 DEMO 지표다. 실제 risk-qualified entry 정답이 없는 `EntryCandidatePrecision`, MFE/MAE, 체결·비용·세금 포함 순수익은 `UNAVAILABLE`/null로 남긴다.

새 개장 레이더는 현재 자료 부재 화면과 별도의 DEMO 선택을 제공한다. 실제 생성된 로컬 브라우저 산출물은 다음과 같다. 현재 화면은 미연결 상태, 리플레이는 합성 자료다.

- [현재 desktop](/tmp/flowtrade-v12-e2e/desktop-dashboard.png), [현재 mobile](/tmp/flowtrade-v12-e2e/mobile-dashboard.png).
- [DEMO replay desktop](/tmp/flowtrade-v12-e2e/desktop-opening-replay.png), [DEMO replay mobile](/tmp/flowtrade-v12-e2e/mobile-opening-replay.png).
- [5분봉 상세](/tmp/flowtrade-v12-5m-detail.png), [출처·company별 관련성](/tmp/flowtrade-v12-provenance-relations.png).

이번 완료 범위는 코드·DB·API·UI와 모의/로컬 검증이다. GitHub push, Railway 재배포, source license·계정 활성화 또는 실제 자동주문은 수행한 것으로 기록하지 않는다.

## 변경 파일 전체 목록

기존 저장소의 변경·추가 파일은 55개다. `.runtime`의 실제 DB와 사전 백업, `.next` 빌드 결과, screenshot은 Git 추적 목록에 포함하지 않는다.

```text
.env.example
README.md
apps/api/app/brokers/toss/documented.py
apps/api/app/catalysts/__init__.py
apps/api/app/catalysts/adapters.py
apps/api/app/catalysts/models.py
apps/api/app/catalysts/service.py
apps/api/app/catalysts/store.py
apps/api/app/db/migrations/001_radar_v12.sql
apps/api/app/db/migrations/001_radar_v12_down.sql
apps/api/app/main.py
apps/api/app/market/ingestion.py
apps/api/app/radar/__init__.py
apps/api/app/radar/collector.py
apps/api/app/radar/config.py
apps/api/app/radar/fast_collector.py
apps/api/app/radar/flow.py
apps/api/app/radar/models.py
apps/api/app/radar/presentation.py
apps/api/app/radar/rotation.py
apps/api/app/radar/routes.py
apps/api/app/radar/runtime.py
apps/api/app/radar/sessions.py
apps/api/app/radar/storage.py
apps/api/app/replay/__init__.py
apps/api/app/replay/fixtures.py
apps/api/app/replay/opening_session.py
apps/api/app/strategy/intraday.py
apps/api/tests/test_catalysts.py
apps/api/tests/test_documented.py
apps/api/tests/test_fast_collector.py
apps/api/tests/test_flow_metrics.py
apps/api/tests/test_intraday.py
apps/api/tests/test_opening_replay.py
apps/api/tests/test_radar_api.py
apps/api/tests/test_rotation.py
apps/web/components/OpeningRadar.tsx
apps/web/components/PriceChart.tsx
apps/web/components/RadarApp.tsx
apps/web/components/opening-radar.css
apps/web/lib/radar-types.ts
apps/web/lib/types.ts
apps/web/package.json
config/flowtrade-v1.2.yml
config/toss-openapi-contract.json
docs/catalyst-providers.md
docs/flowtrade-v1.2-gap-analysis.md
docs/flowtrade-v1.2-implementation.md
docs/flowtrade-v1.2-spec.md
docs/implementation-status.md
package-lock.json
package.json
scripts/migrate-radar.py
scripts/smoke.mjs
scripts/start-prod.mjs
```
