'use client';

import { useCallback, useEffect, useState } from 'react';
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, ChevronDown, Clock3, ExternalLink, FlaskConical, Info, Layers3, Newspaper, Radio, RefreshCw, ShieldCheck, Target } from 'lucide-react';
import { api, money } from '@/lib/api';
import type { OpeningOverview, RadarCatalyst, RadarCoverage, RadarFeedHealth, RadarList, RadarMetrics, RadarQuality, RadarRankItem, RadarTransition } from '@/lib/radar-types';
import './opening-radar.css';

const replayTimes = ['09:02', '09:05', '09:10', '09:20', '09:30'];
const replayScenarios: Record<string, string> = { domestic_rotation: '국내 재료로 전환', us_leadership: '미국장 가설 유지', reverse_rotation: '역방향 전환', sector_divergence: '테마 동조 실패', single_name_only: '단일 종목 급등', pump_then_fade: '급등 후 약화', data_outage: '자료 장애' };
const qualityLabels: Record<RadarQuality, string> = { LIVE: 'LIVE', DELAYED: 'DELAYED', STALE: 'STALE', PARTIAL: 'PARTIAL', UNAVAILABLE: 'UNAVAILABLE', DEMO: 'DEMO', REPLAY: 'DEMO REPLAY' };
const stateLabels: Record<string, string> = { UNSEEN: '관측 전', PREMARKET_HYPOTHESIS: '장전 가설', OPENING_ROTATION_CONFIRMED: '개장 전환 확인', PREMARKET_HYPOTHESIS_REJECTED: '장전 가설 기각', HYPOTHESIS: '장전 가설', WATCH_ONLY: '관찰로 강등', REJECTED_FOR_TODAY: '오늘 가설 기각', LIVE_THEME_CONFIRMED: '관측 강도 확인', NEW_THEME_DISCOVERED: '새 테마 발견', FADING: '강도 약화', OPENING_DISCOVERY: '개장 탐색', OPENING_ROTATION_CANDIDATE: '전환 후보', ROTATION_CONFIRMED: '전환 확인', PRIOR_HYPOTHESIS_REJECTED: '기존 가설 기각', ENTRY_WATCH: '진입 관찰', ENTRY_READY: '진입 조건 충족', EARLY_ENTRY_WATCH: '선진입 관찰', EARLY_ENTRY_READY: '선진입 조건 충족', BREAKOUT_CONFIRMED: '돌파 확인', NO_TRADE: '진입 제외', HOLD: '분석 보류', DATA_STALE: '자료 지연 · 보류' };
const reasonLabels: Record<string, string> = { INSUFFICIENT_HISTORY: '동일 장 시각 이력 부족', LOW_COVERAGE: '종목 관측 범위 부족', STALE_INPUTS: '관측 자료 지연', UNEXPLAINED_MOMENTUM: '확인된 재료 없이 강세', SINGLE_NAME_STRENGTH: '한 종목에 집중', BROAD_BASED_STRENGTH: '여러 종목 동반 강세', HYSTERESIS_HOLD: '미세 순위 변동 보류', DWELL_HOLD: '최소 유지시간 관찰', INVESTOR_NETBUY_UNAVAILABLE: '투자자 순매수 미제공', NEWS_UNAVAILABLE: '뉴스 데이터 미연결', QUOTE_UNAVAILABLE: '현재가 데이터 미연결' };
const relationshipLabels: Record<RadarCatalyst['relationship'], string> = { DIRECT_VERIFIED: '공식 직접 관련', NEWS_REPORTED: '보도상 관련', THEMATIC_ASSOCIATION: '시장 연상', UNVERIFIED: '관련 미확인' };

function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function ratio(value: unknown) { return finite(value) ? `${(value * 100).toFixed(0)}%` : '검증 불가'; }
function amount(value: unknown) { return finite(value) ? `${money(value)}원` : '데이터 없음'; }
function time(value?: string | null, date = false) {
  if (!value) return '시각 미제공';
  try { return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', ...(date ? { month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
  catch { return '시각 미확인'; }
}
function sourceLink(value?: string) { if (!value) return null; try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
function provider(value?: string | string[]) { return Array.isArray(value) ? value.join(' · ') : value || '연결 없음'; }
function QualityBadge({ status = 'UNAVAILABLE', replay = false }: { status?: RadarQuality; replay?: boolean }) {
  const normalized: RadarQuality = typeof status === 'string' && status in qualityLabels ? status : 'UNAVAILABLE';
  const label = replay && normalized === 'LIVE' ? 'DEMO · 관측' : qualityLabels[normalized];
  return <span className={`or-quality or-quality-${normalized.toLowerCase()}`}>{label}</span>;
}
function MetricLine({ label, value, note }: { label: string; value: string; note?: string }) { return <div className="or-metric-line"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>; }
function MetricBreakdown({ metrics, replay }: { metrics: RadarMetrics; replay: boolean }) {
  const net = metrics.confirmed_investor_netbuy;
  const netValues = net?.values;
  return <div className="or-measurements"><MetricLine label="관측 구간 거래대금" value={amount(metrics.turnover_krw)} note={metrics.window_minutes ? `${metrics.window_minutes}분 구간 · 매수 순유입 아님` : '체결 거래대금 · 매수 순유입 아님'} /><MetricLine label="거래대금 증가속도" value={finite(metrics.turnover_velocity_krw_per_min) ? `${money(metrics.turnover_velocity_krw_per_min)}원/분` : '데이터 없음'} /><MetricLine label="동일 장 시각 상대 거래대금" value={finite(metrics.relative_turnover) ? `${metrics.relative_turnover.toFixed(2)}배` : 'N/A'} note={finite(metrics.relative_turnover) ? `${metrics.history_sessions ?? '—'}개 과거 장 구간 기준` : 'INSUFFICIENT_HISTORY · 기준 이력 부족'} /><MetricLine label="상승 참여 비율" value={ratio(metrics.breadth_up_ratio)} /><MetricLine label="커버 종목군 내 점유율" value={ratio(metrics.covered_universe_share ?? metrics.covered_universe_share_ratio)} note="관측한 종목군 기준 · 국내 전체 시장 아님" /><MetricLine label="한 종목 집중도" value={ratio(metrics.concentration_top1_ratio)} />{net && netValues ? <div className="or-netbuy"><div><strong>확인된 투자자 순매수</strong><QualityBadge status={net.quality} replay={replay} /></div>{Object.entries(netValues).map(([key, value]) => <p key={key}><span>{{foreign: '외국인', institution: '기관', retail: '개인', program: '프로그램'}[key] || key}</span><strong>{finite(value) ? `${value >= 0 ? '+' : ''}${money(value)}${net.unit === 'SHARES' ? '주' : net.unit === 'KRW' ? '원' : ' (단위 미확인)'}` : '미제공'}</strong></p>)}<small>{provider(net.provider)} · {time(net.as_of, true)}</small></div> : <div className="or-null-note"><ShieldCheck size={14} /><span>투자자별 순매수 미제공<br /><small>거래대금으로 순매수를 대신 계산하지 않습니다.</small></span></div>}</div>;
}
function CatalystLinks({ ids, catalysts, replay }: { ids?: string[]; catalysts: RadarCatalyst[]; replay: boolean }) {
  const events = catalysts.filter(event => ids?.includes(event.event_id));
  if (!events.length) return null;
  return <div className="or-related-catalysts">{events.map(event => { const url = sourceLink(event.source_url); return <div key={event.event_id}><span className={`or-relationship or-relationship-${event.relationship.toLowerCase()}`}>{relationshipLabels[event.relationship]}</span>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{replay ? '합성 시나리오 출처' : event.title}<ExternalLink size={11} /></a> : <span>{replay ? '합성 fixture · 실제 원문 아님' : event.title}</span>}</div>; })}</div>;
}
function TickerRelationships({ event }: { event: RadarCatalyst }) {
  const symbols = [...new Set([...(event.tickers || []), ...(event.ticker_relations || []).map(item => item.symbol)])];
  if (!symbols.length) return null;
  return <div className="or-ticker-relations"><span>종목별 관련성</span><div>{symbols.slice(0, 6).map(symbol => {
    const relation = event.ticker_relations?.find(item => item.symbol === symbol);
    const label = relation ? relationshipLabels[relation.relationship] || '관련 미확인' : '개별 관계 미제공';
    return <span key={symbol} title={`${symbol}: ${label}. 이벤트 전체의 관련성 등급을 이 종목에 자동 적용하지 않습니다.`}><b>{symbol}</b>{label}</span>;
  })}{symbols.length > 6 && <small>외 {symbols.length - 6}종목</small>}</div><small>이벤트와 직접 관련된 기업과 시장에서 연상한 종목을 구분합니다.</small></div>;
}
function RankRow({ item, index, kind, catalysts, replay }: { item: RadarRankItem; index: number; kind: 'hypothesis' | 'flow' | 'entry'; catalysts: RadarCatalyst[]; replay: boolean }) {
  const metrics = item.metrics || {};
  const name = item.name || item.theme_name || item.symbol || '이름 미제공';
  const entryLow = item.entry_low ?? item.entry?.low;
  const entryHigh = item.entry_high ?? item.entry?.high;
  const stop = item.stop ?? item.structural_stop ?? item.risk?.stop;
  const rr = item.rr_to_first_resistance ?? item.risk?.first_resistance_reward_risk;
  const explanation = item.explanation || item.reasons?.join(' · ') || '판단 근거가 아직 제공되지 않았습니다.';
  return <article className={`or-rank-row or-rank-${kind}`}><div className="or-rank-title"><span className="or-rank-number">{item.rank || index + 1}</span><div><h4>{name}</h4><span>{item.symbol ? `${item.symbol} · ` : ''}{stateLabels[item.state || ''] || item.state || (kind === 'hypothesis' ? '가설' : '관찰')}</span></div>{finite(item.score) && <strong className="or-score">{item.score.toFixed(0)}<small>점</small></strong>}</div><p className="or-explanation">{explanation}</p>{kind === 'flow' && metrics.strength_type && <span className="or-strength-type">{{'broad-based strength':'여러 종목 동반 강세','single-name strength':'한 종목 집중 강세',mixed:'참여 폭 혼재',unavailable:'참여 폭 확인 필요'}[metrics.strength_type] || '참여 폭 확인 필요'}</span>}{kind === 'flow' && <div className="or-flow-preview"><div><span>거래대금 속도</span><strong>{finite(metrics.turnover_velocity_krw_per_min) ? `${money(metrics.turnover_velocity_krw_per_min)}원/분` : '미제공'}</strong></div><div><span>동일 장 시각 상대값</span><strong>{finite(metrics.relative_turnover) ? `${metrics.relative_turnover.toFixed(2)}배` : 'N/A · 이력 부족'}</strong></div></div>}{kind === 'entry' && <div className="or-entry-preview"><span>{finite(entryLow) && finite(entryHigh) ? '진입 관찰' : '판단 기준 현재가'} <strong>{finite(entryLow) && finite(entryHigh) ? `${money(entryLow)} ~ ${money(entryHigh)}원` : amount(item.price)}</strong></span><span>구조적 손절 <strong>{amount(stop)}</strong></span><span>첫 저항 손익비 <strong>{finite(rr) ? `1 : ${rr.toFixed(2)}` : '검증 불가'}</strong></span></div>}<details className="or-rank-detail"><summary aria-label={`${name} ${kind === 'hypothesis' ? '가설' : kind === 'flow' ? '측정' : '진입'} 근거 펼치기`}>{kind === 'hypothesis' ? '가설 근거와 출처' : kind === 'flow' ? '측정값과 자료 범위' : '진입 근거와 자료 범위'}<ChevronDown size={13} /></summary>{kind !== 'hypothesis' && <MetricBreakdown metrics={metrics} replay={replay} />}{kind === 'hypothesis' && <p className="or-detail-copy">이 목록은 장전 관찰 가설입니다. 실제 개장 거래대금과 참여 폭이 이를 지지하는지는 별도로 검증합니다.</p>}<CatalystLinks ids={item.catalyst_event_ids} catalysts={catalysts} replay={replay} />{(item.source_urls || []).map((url, n) => { const safe = sourceLink(url); return safe ? <a key={`${url}-${n}`} className="or-source-link" href={safe} target="_blank" rel="noopener noreferrer">{replay ? '합성 관측 출처' : '관측 원문'} <ExternalLink size={12} /></a> : null; })}{item.as_of && <span className="or-as-of">판단 기준 {time(item.as_of, true)} KST</span>}</details></article>;
}
function RankList({ title, subtitle, kind, list, catalysts, replay, mode, failed }: { title: string; subtitle: string; kind: 'hypothesis' | 'flow' | 'entry'; list?: RadarList; catalysts: RadarCatalyst[]; replay: boolean; mode?: string; failed: boolean }) {
  const status = failed && kind !== 'hypothesis' ? 'STALE' : list?.status || 'UNAVAILABLE';
  const allowed = kind === 'hypothesis' || (!failed && (replay ? !['UNAVAILABLE', 'STALE'].includes(status) : mode === 'live' && status === 'LIVE'));
  const items = allowed ? list?.items || [] : [];
  const empty = kind === 'hypothesis' ? '검증된 장전 가설 자료가 없어요.' : kind === 'flow' ? '관측 강도를 검증할 자료가 부족해요.' : '위험과 진입 조건을 통과한 후보가 없어요.';
  return <section className={`or-list or-list-${kind}`} aria-label={title}><div className="or-list-header"><div className="or-list-icon">{kind === 'hypothesis' ? <Layers3 size={17} /> : kind === 'flow' ? <Activity size={17} /> : <Target size={17} />}</div><div><h3>{title}</h3><p>{subtitle}</p></div><QualityBadge status={status} replay={replay} /></div>{items.length ? <div className="or-list-items">{items.slice(0, 3).map((item, index) => <RankRow key={`${item.theme_id || item.symbol || index}`} item={item} index={index} kind={kind} catalysts={catalysts} replay={replay} />)}</div> : <div className="or-empty"><div className="or-empty-dots"><span /><span /><span /></div><strong>{empty}</strong><p>{list?.reason || (kind === 'flow' ? '시세 신선도, 종목 커버리지, 동일 장 시각 이력을 확인합니다. 장전 가설을 이 목록으로 옮겨 채우지 않습니다.' : kind === 'entry' ? '관측 강도 1위도 매수가·손절·손익비가 확인되기 전에는 진입 후보가 아닙니다.' : '미국장·국내 재료·일봉의 출처와 시점을 확인한 후 표시합니다.')}</p></div>}</section>;
}
function CatalystRow({ event, replay }: { event: RadarCatalyst; replay: boolean }) {
  const url = sourceLink(event.source_url);
  return <article className="or-catalyst"><div><span className={`or-relationship or-relationship-${event.relationship.toLowerCase()}`}>{relationshipLabels[event.relationship] || '관련 미확인'}</span><span className="or-provider">{event.provider}{replay ? ' · 합성 fixture' : ''}</span>{event.status && <span className="or-catalyst-state">{{VERIFIED: replay ? '합성 근거' : '원문 확인', UNCONFIRMED: '미확인', CORRECTED: '정정', RETRACTED: '철회'}[event.status] || event.status}</span>}</div><h4>{event.title}</h4><div className="or-catalyst-meta"><span>원문 발행 {time(event.published_at, true)}</span><span>첫 수신 {time(event.first_seen_at, true)}</span><span>KST</span>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{replay ? '합성 출처' : '원문 보기'}<ExternalLink size={12} /></a> : <span className="or-no-source">{replay ? '실제 뉴스 원문 아님' : '원문 URL 미제공'}</span>}</div><TickerRelationships event={event} /></article>;
}
function TransitionRow({ item, themeNames }: { item: RadarTransition; themeNames: Record<string, string> }) {
  const previousRank = item.previous_rank ?? item.from_rank;
  const rank = item.rank ?? item.to_rank;
  const metrics = item.metrics || item.measurements || {};
  const previous = item.previous_measurements || metrics.previous_metrics || metrics.previous;
  const promoted = rank != null && (previousRank == null || rank < previousRank);
  const demoted = previousRank != null && (rank == null || rank > previousRank);
  const previousVelocity = previous?.turnover_velocity_krw_per_min ?? metrics.changes?.turnover_velocity_krw_per_min?.previous;
  const previousBreadth = previous?.breadth_up_ratio ?? metrics.changes?.breadth_up_ratio?.previous;
  const name = item.theme_name || themeNames[item.theme_id] || item.theme_id;
  return <article className={`or-transition ${promoted ? 'or-promoted' : demoted ? 'or-demoted' : 'or-observed'}`}><span className="or-transition-icon">{promoted ? <ArrowUpRight size={16} /> : demoted ? <ArrowDownRight size={16} /> : <Activity size={16} />}</span><div><div className="or-transition-title"><strong>{name}</strong><span>{previousRank == null && rank == null ? <>{stateLabels[item.previous_state || ''] || '관측 전'}<ArrowRight size={11} />{stateLabels[item.state || ''] || '상태 변경'}</> : <>{previousRank != null ? `${previousRank}위` : '순위 밖'}<ArrowRight size={11} />{rank != null ? `${rank}위` : '순위 밖'}</>}</span><time>{time(item.as_of)} KST</time></div><p>{item.explanation || item.reasons?.join(' · ') || stateLabels[item.state || item.transition || ''] || '관측 기준에 따라 상태 변경'}</p><div className="or-transition-measures">{finite(metrics.turnover_velocity_krw_per_min) && <span>속도 {finite(previousVelocity) ? `${money(previousVelocity)} → ` : ''}{money(metrics.turnover_velocity_krw_per_min)}원/분</span>}{finite(metrics.breadth_up_ratio) && <span>상승 참여 {finite(previousBreadth) ? `${ratio(previousBreadth)} → ` : ''}{ratio(metrics.breadth_up_ratio)}</span>}{finite(metrics.relative_turnover) && <span>상대 거래대금 {metrics.relative_turnover.toFixed(2)}배</span>}</div>{item.reason_codes?.filter(code => reasonLabels[code]).length ? <div className="or-reason-tags">{item.reason_codes.filter(code => reasonLabels[code]).slice(0, 3).map(code => <span key={code}>{reasonLabels[code]}</span>)}</div> : null}</div></article>;
}
function FeedRow({ title, feed, replay, failed }: { title: string; feed?: RadarFeedHealth; replay: boolean; failed: boolean }) {
  const status = failed ? 'STALE' : feed?.status || 'UNAVAILABLE';
  return <div className="or-feed"><div><span>{title}</span><QualityBadge status={status} replay={replay} /></div><p>{provider(feed?.provider)}</p><span>{feed?.last_success_at ? `마지막 수신 ${time(feed.last_success_at)}` : '수신 시각 없음'}{finite(feed?.latency_seconds) ? ` · 지연 ${feed.latency_seconds.toFixed(0)}초` : ''}</span>{feed?.reason && <small>{feed.reason}</small>}</div>;
}
function CoverageNote({ coverage }: { coverage?: RadarCoverage }) {
  const seen = coverage?.covered_universe_symbols ?? coverage?.symbols_seen;
  const expected = coverage?.expected_universe_symbols ?? coverage?.symbols_expected;
  const value = coverage?.universe_ratio ?? coverage?.ratio;
  return <div className="or-coverage"><Radio size={14} /><span>관측 종목 {finite(seen) ? seen : '—'} / {finite(expected) ? expected : '—'}개 <strong>{ratio(value)}</strong></span><span>{coverage?.venues?.length ? coverage.venues.join(' · ') : '시장 범위 미확인'}</span><span>설정된 커버 종목군 기준 · 국내 전체 시장 아님</span></div>;
}

export default function OpeningRadar() {
  const [source, setSource] = useState<'current' | 'replay'>('current');
  const [snapshot, setSnapshot] = useState('09:10');
  const [scenario, setScenario] = useState('domestic_rotation');
  const [data, setData] = useState<OpeningOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [expandedNews, setExpandedNews] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => { setRefreshing(true); setRefreshKey(value => value + 1); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setData(null);
    const fetchOverview = async () => {
      const path = source === 'replay' ? `/analytics/opening-replay?session=2026-10-12&snapshot_time=${encodeURIComponent(snapshot)}&scenario=${encodeURIComponent(scenario)}` : '/market/overview';
      try { const value = await api<OpeningOverview>(path, { signal: controller.signal }); if (!controller.signal.aborted) { setData(value); setError(''); } }
      catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '개장 레이더 자료를 가져오지 못했습니다.'); }
      finally { if (!controller.signal.aborted) { setLoading(false); setRefreshing(false); } }
    };
    void fetchOverview();
    const timer = source === 'current' ? setInterval(() => void fetchOverview(), 60_000) : null;
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [source, snapshot, scenario, refreshKey]);
  const replay = source === 'replay' || data?.mode === 'replay' || data?.mode === 'demo';
  const events = data?.catalysts || [];
  const transitions = data?.rotation_history || [];
  const lists = [data?.premarket_hypothesis_top3, data?.live_opening_flow_top3, data?.entry_ready_top3];
  const themeNames: Record<string, string> = {};
  for (const list of lists) for (const item of list?.items || []) if (item.theme_id) themeNames[item.theme_id] = item.theme_name || item.name || item.theme_id;
  const freshness = data?.data_freshness || {};
  return <section className="opening-radar" aria-label="개장 흐름 레이더"><div className="or-heading"><div><span className="or-eyebrow"><Radio size={13} /> OPENING RADAR <b>v1.2</b></span><h2>개장 후, 가설을 다시 검증합니다.</h2><p>장전 예상과 실제 거래대금 집중, 위험을 통과한 진입 후보를 구분합니다.</p></div><div className="or-controls"><div className="or-source-tabs" role="group" aria-label="개장 레이더 자료 선택"><button type="button" aria-pressed={source === 'current'} onClick={() => setSource('current')}>관측 모니터</button><button type="button" aria-pressed={source === 'replay'} onClick={() => setSource('replay')}><FlaskConical size={13} /> DEMO 리플레이</button></div><button className="or-refresh" type="button" aria-label="개장 레이더 자료 새로고침" disabled={refreshing || loading} onClick={refresh}><RefreshCw size={15} className={refreshing ? 'or-spin' : ''} /></button></div></div>{replay ? <div className="or-replay-banner"><FlaskConical size={18} /><div><strong>합성 시나리오를 재생하고 있어요. <span>DEMO</span></strong><p>2026-10-12 사례의 형태를 만든 테스트 데이터입니다. 실제 당일 뉴스·가격·거래대금이 아닙니다.</p></div><div className="or-replay-selects"><label>합성 시나리오<select aria-label="개장 리플레이 시나리오" value={scenario} onChange={event => setScenario(event.target.value)}>{Object.entries(replayScenarios).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>재생 기준 시각<select aria-label="개장 리플레이 기준 시각" value={snapshot} onChange={event => setSnapshot(event.target.value)}>{replayTimes.map(value => <option key={value} value={value}>{value} KST</option>)}</select></label></div></div> : <div className="or-current-bar"><Clock3 size={14} /><span>{data ? `${time(data.as_of, true)} KST` : '기준 시각 확인 중'}</span><span>{data?.session_label || '장 세션 미확인'}</span><QualityBadge status={error ? 'STALE' : data?.quality || data?.data_freshness?.quotes?.status || 'UNAVAILABLE'} /><small>자료 범위별 상태를 아래에서 확인하세요.</small></div>}{error && <div className="or-error" role="status"><Info size={16} /><span>자료 갱신에 실패해 관측 순위와 진입 판단을 보류합니다. <small>{error}</small></span><button type="button" onClick={refresh}>다시 확인</button></div>}{loading ? <div className="or-loading"><RefreshCw size={19} className="or-spin" /><span>{source === 'replay' ? '해당 시각까지 알려진 자료를 재생합니다.' : '개장 관측 자료를 확인합니다.'}</span></div> : <><CoverageNote coverage={data?.coverage} /><div className="or-three-lists"><RankList title="장전 가설 Top 3" subtitle="예측 · 개장 반응으로 다시 검증" kind="hypothesis" list={data?.premarket_hypothesis_top3} catalysts={events} replay={replay} mode={data?.mode} failed={Boolean(error)} /><RankList title="개장 관측 Top 3" subtitle="측정 · 거래대금과 참여 폭" kind="flow" list={data?.live_opening_flow_top3} catalysts={events} replay={replay} mode={data?.mode} failed={Boolean(error)} /><RankList title="진입 관찰 Top 3" subtitle="조건 · 지지와 위험 기준 통과" kind="entry" list={data?.entry_ready_top3} catalysts={events} replay={replay} mode={data?.mode} failed={Boolean(error)} /></div><div className="or-bottom-grid"><section className="or-bottom-panel"><div className="or-panel-heading"><h3><Newspaper size={16} /> 국내 재료와 출처</h3><QualityBadge status={freshness.news?.status || freshness.catalysts?.status || 'UNAVAILABLE'} replay={replay} /></div>{events.length ? <div>{events.slice(0, expandedNews ? events.length : 2).map(event => <CatalystRow key={event.event_id} event={event} replay={replay} />)}{events.length > 2 && <button className="or-more" onClick={() => setExpandedNews(value => !value)}>{expandedNews ? '재료 목록 접기' : `재료 ${events.length - 2}건 더 보기`}<ChevronDown size={13} /></button>}</div> : <div className="or-bottom-empty"><Newspaper size={23} /><p>확인할 수 있는 국내 뉴스·공시가 없습니다.</p><span>뉴스 미연결은 시장에 재료가 없다는 뜻이 아닙니다. 공시와 정책·경제 뉴스는 별도 자료입니다.</span></div>}</section><section className="or-bottom-panel"><div className="or-panel-heading"><h3><Activity size={16} /> 가설 승격·강등 기록</h3><span className="or-count">{transitions.length}건</span></div>{transitions.length ? <div>{transitions.slice(-6).reverse().map((item, index) => <TransitionRow key={item.transition_id || `${item.theme_id}-${item.as_of}-${index}`} item={item} themeNames={themeNames} />)}</div> : <div className="or-bottom-empty"><Layers3 size={23} /><p>검증된 순위 변경 기록이 없습니다.</p><span>속도·상대 거래대금·참여 폭이 바뀌면 판단 시각과 측정 근거를 함께 남깁니다.</span></div>}</section></div><section className="or-feed-panel" aria-label="개장 레이더 기능별 자료 상태"><div className="or-panel-heading"><h3><ShieldCheck size={16} /> 기능별 데이터 상태</h3><span className="or-feed-note">{replay ? '합성 재생 자료 · 실제 공급자 연결 상태 아님' : '상태를 기능별로 구분합니다.'}</span></div><div className="or-feed-grid"><FeedRow title="한국 경제·정책 뉴스" feed={freshness.news} replay={replay} failed={Boolean(error)} /><FeedRow title="국내 기업 공시" feed={freshness.disclosures} replay={replay} failed={Boolean(error)} /><FeedRow title="현재가·체결 거래대금" feed={freshness.quotes} replay={replay} failed={Boolean(error)} /><FeedRow title="투자자별 순매수" feed={freshness.investor_netbuy} replay={replay} failed={Boolean(error)} /><FeedRow title="동일 장 시각 비교 이력" feed={freshness.history} replay={replay} failed={Boolean(error)} /></div></section><div className="or-footnote"><Info size={13} /><p>거래대금은 체결 금액이며 매수자금 순유입액이 아닙니다. 관측 강도와 진입 적합성은 별도입니다. {data?.notice || '실제 주문은 비활성화되어 있습니다.'}</p><span>{provider(data?.provider)}</span></div></>}</section>;
}
