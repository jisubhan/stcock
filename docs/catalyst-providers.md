# Domestic Catalyst Radar: 소스 계약과 데이터 의미

국내 뉴스·공시 수집은 미국장 테마 분석과 독립된 `app/catalysts/` 파이프라인이다. 이벤트·종목 연결·증분 cursor·정정 이력·시점별 조회는 구현했고, 외부 소스 연결은 확인한 계약만 활성화한다. 실제 국내 속보, DART 또는 KIND를 이미 수집 중이라고 표시하지 않는다.

## 현재 소스 상태

2026-10-12에 비밀 값 없이 변수 이름/설정 여부와 공개 문서 접근을 확인했다.

| 소스 | 실제 연결 상태 | 필요한 정보 |
| --- | --- | --- |
| DART | `UNAVAILABLE`; 전용 공식 adapter 미구현 | 접근 가능한 공식 API·인증 계약, 실제 응답 스키마, 발행시각 정밀도와 정정 관계, 필요한 승인된 키 바인딩 |
| KIND/KRX | `UNAVAILABLE` | 허용된 API/피드·이용약관·응답 계약과 필요한 인증 방식 |
| 계약 한국 뉴스 | `UNAVAILABLE` | 제공자 이름, 메타데이터/요약 재사용 라이선스, 검증한 endpoint·schema·증분 수정/철회 계약·필요한 인증 바인딩 |
| 정부/지역 발표 | `UNAVAILABLE` | 수집이 허용된 공식 HTTPS 피드, 게시시각과 수정 schema |

`DART_API_KEY`, `DART_KEY`, `KIND_API_KEY`, `NEWS_API_KEY`는 당시 미설정이었다. 키가 필요한지·어떤 인증 방식인지 공식 계약을 확인한 뒤 실제 필요한 바인딩만 추가해야 한다. 키 존재만으로 연결이 검증되지는 않는다. DART 공시가 제공되어도 정책·지역 뉴스 전체의 수집을 대신하지 않는다.

공개 문서 후보를 정상 HTTPS 프록시로 각각 한 번 조회했으나 모두 CONNECT 403이었다. 원본 사이트의 HTTP 응답을 받지 못했고 네트워크 허용 규칙이나 TLS 검증을 변경하지 않았다.

- DART: `https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001`
- KIND: `https://kind.krx.co.kr/`
- 정책브리핑 RSS 안내: `https://www.korea.kr/etc/rss.do`

따라서 일반적으로 알려진 DART 경로를 추정하여 live adapter에 넣지 않았다. 날짜만 제공되는 소스에 자정이라는 가상의 실제 게시시각을 부여하지 않는다. 시간 정밀도가 확인되는 계약 또는 별도 날짜 정밀도 지원이 먼저 필요하다.

## 확인된 계약을 연결하는 인터페이스

`CatalystSourceAdapter`는 `poll_since(ProviderCursor) -> PollResult`, `health() -> ProviderHealth`, `aclose()`를 제공한다. 초기 수집의 `last_trading_close`는 실제 국내 캘린더에서 확인한 마지막 거래일 장 마감 시각을 전달한다. 날짜를 전날로 단순 빼거나 평일만 계산하지 않는다. 이후 cursor와 overlap을 이용해 휴일·주말·장중 새 기사와 정정을 증분 처리한다.

관리자가 검토한 **비밀이 없는** 계약 JSON 파일을 `CATALYST_PROVIDER_CONFIG`에 절대 경로로 지정하면 `LicensedJSONFeedAdapter`를 구성할 수 있다. 기본 live registry에는 endpoint가 없다. URL을 채팅이나 임의 요청 parameter로 받아 호출하는 기능은 없다.

파일은 `FeedContract` 객체의 배열이며 다음을 명시해야 한다.

- `provider`, `endpoint`, `official_document_url`, `allowed_domains`, `source_kind`, `coverage`.
- 실제 확인 후에만 `contract_verified`, `response_schema_verified`, `incremental_revision_contract_verified`, `license_allowed`를 true로 지정한다.
- `response_schema`, `records_path`, `field_map`. 최소 mapping은 `provider_item_id`, `source_url`, `title`, `published_at`, `evidence`다. source contract의 field와 타입을 그대로 검사하고 이후 정규화한다.
- `since_query` 또는 `cursor_query`, 필요할 때 `next_cursor_path`, 수정·철회도 포함하는 증분 의미, `overlap_seconds`. 초기 cursor가 없다면 검증한 `last_trading_close` 또는 `initial_since`를 지정해야 한다.
- 필요한 경우 `auth_env`, `auth_header`, `auth_prefix`. 비밀 값은 환경에서 읽고 endpoint의 승인된 HTTPS 목적지에만 보낸다. static query나 evidence URL에 비밀을 저장하지 않는다.
- 지연을 실제 확인한 경우에만 `expected_delay_seconds`; 미확인 시 latency는 null과 `LATENCY_NOT_VERIFIED`다. stale 판정 기준은 `stale_after_seconds`로 관리한다.

이 adapter는 GET JSON feed와 header 인증을 지원한다. 공식 DART처럼 아직 확인하지 못한 특정 인증·query·날짜 schema의 지원을 주장하지 않는다. source-specific 형식이 다르면 계약 확인 후 별도 adapter를 구현한다. `$ref`·composition 등 현재 validator가 지원하지 않는 schema keyword는 무시하지 않고 거부한다.

401/403은 같은 adapter의 자동 재시도를 차단한다. 429는 `Retry-After` 동안 요청하지 않으며 기존 cursor를 보존한다. 인증·라이선스·schema 실패 시 새로운 이벤트를 만들지 않고 `UNAVAILABLE` 또는 과거 성공이 있으면 `STALE`로 전환한다. 연결 상태가 바뀌면 설정 검토 후 서비스를 재구성해 다시 확인한다. `UNAVAILABLE`은 “국내 재료가 없다”는 뜻이 아니다.

## 이벤트·종목 관련성·원문 보관

URL·제목·최소 claim·발행기관·시각·근거 강도만 저장하며 원문 뉴스 본문 전체를 저장하거나 재배포하지 않는다. source URL은 자격 증명이 없는 HTTPS여야 한다. 관련성은 종목별 `ticker_relations`에 분리한다.

- `DIRECT_VERIFIED`: 공식 공시/발표의 **명시된 company symbol과 그 company를 가리키는 공식 evidence**가 모두 확인된 경우만 가능하다.
- `NEWS_REPORTED`: 계약 뉴스에 종목이 명시된 경우. 공식 직접 수혜 확인과는 구분한다.
- `THEMATIC_ASSOCIATION`: 회사명 alias나 테마명에 따른 연상. confidence가 제한되고 `WATCH_ONLY_RELATIONSHIP`을 붙인다.
- `UNVERIFIED`: 위 근거가 없는 관계.

event의 대표 relationship을 다른 관련 종목에 그대로 적용하지 않는다. 공급망·지역·방문·계획만으로 수혜 또는 계약 확정을 만들지 않는다. 정책 certainty는 발표·계획·논의·승인·확정 계약을 별도로 보존한다. 공개매수 가격·기간·조건·지분비율은 해당 company의 공식 원문 evidence가 있는 경우만 구조화한다. 가격 차이를 확정 수익으로 설명하지 않는다.

## 중복·정정·시점별 조회

`canonical_story_id`는 확인된 source가 제공한 **정확하고 공통인 사실 identity**다. 제목 유사도·테마·같은 날짜로 자동 묶지 않는다. 이 identity가 없으면 `(mode, provider, provider_item_id)`로 분리해 비슷한 제목의 별개 사건이 남도록 한다. provider·item·content hash가 같은 반복 수신은 idempotent하며 novelty를 다시 올리지 않는다. cross-provider 반복 보도는 한 event와 여러 provenance로 보존한다.

`published_at`은 원문 게시시각, `first_seen_at`은 시스템 최초 수신시각, `ingested_at`은 해당 버전 처리시각이다. 운영 수집은 HTTP 응답을 받은 뒤 시스템 clock으로 first_seen을 찍으며 source가 보내는 first_seen 값을 신뢰하지 않는다. UTC의 timezone-aware 시각으로 저장하고 한국 화면은 Asia/Seoul로 표시한다.

정정/철회 때 최초 first_seen을 바꾸지 않고 `revision_published_at`, `revision_available_at`, `revision_id`를 추가한 append-only 버전을 저장한다. `revision_available_at` 전에는 수정된 사실을 사용할 수 없다. as-of 조회는 게시·첫 수신·해당 revision 가용시각이 모두 기준 시각 이내인 최신 버전만 반환한다. 철회된 이벤트는 이력에 남지만 추천 근거로 다시 사용하지 않는다.

cursor는 이벤트 batch와 같은 SQLite transaction에서만 갱신한다. batch의 한 항목이 시각·schema 검증에 실패하면 일부 이벤트를 먼저 저장하거나 cursor를 앞으로 보내지 않는다. 현재 조회는 provider 장애를 반영하고, 과거 as-of 조회는 당시 revision의 coverage를 보존한다.

## 저장 및 검증

`CATALYST_SCHEMA_SQL`은 root의 v1.2 migration에서 실행한다. `app.radar.storage.connection()`을 공유하며 `catalyst_events`, `catalyst_revisions`, `catalyst_evidence`, `catalyst_cursors`, `catalyst_provider_health` 테이블과 조회 index를 추가한다. `catalyst_events`는 최신 projection이고 진실의 이력은 revisions에 있다. migration rollback은 root의 사전 DB backup을 복구하는 방식을 사용한다. 테이블을 drop하면 신규 이벤트 이력을 잃으므로 운영에서 자동 삭제하지 않는다.

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests/test_catalysts.py -q
```

T01 휴일 rollover, T02 반복/수정/철회, T08 first_seen·revision lookahead, T14 인증·license·schema·rate failure와 추가 provenance 검사를 모의 응답으로 검증한다. fixture adapter는 **명시적인 DEMO/replay에서만** 사용하며 live 기본값으로 추가되지 않는다. 2026-10-12 형태의 합성 사건이 실제 해당 날짜의 뉴스라고 저장되지 않는다. 실제 연결된 뉴스/공시 feed, 관측 지연 또는 실제 속보 탐지 성공은 아직 검증하지 못했다.
