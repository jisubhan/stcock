'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDown, ArrowRight, BarChart3, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3, ExternalLink, Globe2, Info, Layers3, RefreshCw, Search, ShieldCheck, Sparkles, Telescope } from 'lucide-react';
import { api, compactMoney, money } from '../lib/api';
import type { DiscoveryCandle, DiscoveryDetail, DiscoveryEvidence, DiscoveryOverview, DiscoveryStock, DiscoveryStockPage, DiscoveryTheme } from '../lib/discovery-types';
import PriceChart, { type ChartLevels } from './PriceChart';
import './market-discovery.css';

const PAGE_SIZE = 25;
const OVERVIEW_INTERVAL = 30_000;
const DETAIL_INTERVAL = 5_000;
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const count = (value: unknown) => number(value) ? `${money(value)}개` : '미확인';
const priority = (value: unknown) => number(value) && value >= 0 && value <= 100 ? value.toFixed(0) : '—';
const amount = (value: unknown) => number(value) && value >= 0 ? compactMoney(value) : '미제공';
const safeUrl = (value: unknown) => typeof value === 'string' && /^https?:\/\//i.test(value) ? value : null;

function time(value: unknown) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return '시각 미제공';
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(stamp)) : '시각 미제공';
}

const qualityLabels: Record<string, string> = { LIVE: 'LIVE', PARTIAL: 'PARTIAL', DELAYED: 'DELAYED', STALE: 'STALE', UNAVAILABLE: 'UNAVAILABLE', ANALYSIS_ONLY: '분석 자료', READY: '분석 캐시', PENDING: '분석 대기', WATCH: 'WATCH', OBSERVED: '관측 자료', VERIFIED: '근거 확인', THEME_WATCH: '테마 관찰' };
const relationLabels: Record<string, string> = { OFFICIAL_SECTOR_MEMBER: 'Toss 산업 분류 소속', OFFICIAL_SECTOR_CLASSIFICATION: 'Toss 산업 분류', DIRECT_VERIFIED: '직접 관계 확인', NEWS_REPORTED: '뉴스 보도 관계', THEMATIC_ASSOCIATION: '테마 연관', UNVERIFIED: '관계 미검증' };
const factorLabels: Record<string, string> = { us_1d: '미국장 1일', us_1w: '미국장 1주', domestic_sector_performance: '국내 업종 성과', domestic_breadth: '국내 참여 폭', domestic_velocity: '거래대금 속도', chart_structure: '차트 구조', catalyst: '뉴스·공시 재료' };
const analysisLabels: Record<string, string> = {
  QUOTE_UNAVAILABLE_AT_AS_OF: '판단 시각에 확인된 실제 시세가 없습니다.',
  COMPLETED_DAILY_TECHNICAL_STRUCTURE: '완료된 일봉으로 이동평균과 가격 구조를 계산했습니다.',
  INSUFFICIENT_COMPLETED_DAILY_BARS: '일봉 기술 분석에 필요한 완료 봉이 부족합니다.',
  SIGNIFICANT_CANDLE_VOLUME_BASELINE_UNAVAILABLE: '기준봉을 판단할 이전 거래량 이력이 부족합니다.',
  BOX_VOLUME_STRUCTURE_UNAVAILABLE: '박스 구간의 거래량·가격 구조를 확인하지 못했습니다.',
  SIGNIFICANT_CANDLE_TRADING_AMOUNT_IS_PRICE_VOLUME_ESTIMATE: '기준봉 거래대금은 종가 × 거래량 추정값입니다.',
  FIVE_MINUTE_OBSERVATIONS_INVALID: '5분봉 구성을 위한 실제 관측 검증이 필요합니다.',
  INVALID_OFFICIAL_SESSION: '공식 장 세션 자료를 확인하지 못했습니다.',
  DAILY_SOURCE_OR_VENUE_MISMATCH: '일봉의 출처·거래소 범위를 검증하지 못했습니다.',
  MINUTE_SOURCE_VENUE_OR_TIME_SEMANTICS_MISMATCH: '분봉의 출처·시각 기준을 검증하지 못했습니다.',
  THEME_UNAVAILABLE_AT_AS_OF: '판단 시각에 알려진 테마 연결 근거가 없습니다.',
};
function analysisText(value: string) { return analysisLabels[value] || (/^[A-Z0-9_]+$/.test(value) ? '일부 관측 입력의 추가 검증이 필요합니다.' : value); }

function Status({ status }: { status?: string }) {
  const value = status || 'UNAVAILABLE';
  return <span className={`md-status md-status-${value.toLowerCase().replace(/[^a-z_]/g, '')}`}>{qualityLabels[value] || '자료 확인 중'}</span>;
}

function useDiscoveryResource<T>(path: string | null, interval: number, reload: number) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setData(null);
    setFailed(false);
    setLoading(Boolean(path));
    if (!path) return;
    let cancelled = false;
    let pending = false;
    const controller = new AbortController();
    const read = async () => {
      if (pending) return;
      pending = true;
      try {
        const value = await api<T>(path, { signal: controller.signal });
        if (!cancelled) { setData(value); setFailed(false); }
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        pending = false;
        if (!cancelled) setLoading(false);
      }
    };
    void read();
    const timer = window.setInterval(() => void read(), interval);
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer); };
  }, [path, interval, reload]);
  return { data, loading, failed };
}

function Evidence({ items }: { items?: DiscoveryEvidence[] }) {
  if (!items?.length) return <p className="md-subtle">개별 판단 근거와 원문 출처가 아직 제공되지 않았습니다.</p>;
  return <div className="md-evidence-list">{items.slice(0, 6).map((item, index) => {
    const url = safeUrl(item.source_url);
    return <div className="md-evidence" key={`${item.kind}-${index}`}>
      <span className="md-relation">{relationLabels[item.relationship || item.kind || ''] || '개별 관계 확인 중'}</span>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{item.title || '원문 근거'} <ExternalLink size={10} /></a> : <strong>{item.title || '원문 출처 미제공'}</strong>}
      <small>원문 시각 {time(item.published_at || item.provider_at)} <span>·</span> 최초 확인 {time(item.first_seen_at)}</small>
    </div>;
  })}</div>;
}

function ThemeCard({ item, rank, observed, selected, onSelect }: { item: DiscoveryTheme; rank: number; observed: boolean; selected: boolean; onSelect: () => void }) {
  const score = item.watch_score ?? item.score;
  const turnover = item.observed_turnover_krw ?? item.turnover_krw ?? item.metrics?.turnover_krw ?? item.turnover?.allocated_trading_amount_krw;
  const members = item.recommended_stocks || item.members || [];
  return <article className={`md-theme-card ${selected ? 'md-selected' : ''}`} data-testid="discovery-theme-card" data-kind={observed ? 'observed' : 'hypothesis'}>
    <div className="md-theme-title"><span className="md-rank">{rank}</span><div><h4>{item.name}</h4><span>{observed ? '관측 거래대금 · 예상과 별도' : '예상 테마 · WATCH'}</span></div><Status status={item.quality} /></div>
    <div className="md-theme-value">{observed ? <><strong>{amount(turnover)}</strong><span>관측된 체결 거래대금</span></> : <><strong>{priority(score)}<small>/100</small></strong><span>관찰 우선순위 · 수익 확률 아님</span></>}</div>
    <div className="md-theme-coverage"><span>구성 {count(item.coverage?.members_expected)}</span><span>관측 {count(item.coverage?.members_observed)}</span><span>최신 시세 {count(item.coverage?.quote_fresh_count)}</span>{!observed && number(item.available_max_points) && <span>확인된 판단 입력 {item.available_max_points}/100점</span>}</div>
    {observed && <p className="md-subtle">조회된 랭킹 종목군 · 순매수 아님 · 다중 산업 소속의 거래대금은 중복 없이 배분합니다.</p>}
    {!observed && <p className="md-subtle">산업 분류 소속은 뉴스의 직접 수혜 확인과 다릅니다. 예상 관찰 시점 {typeof item.expected_timing === 'string' ? item.expected_timing : '미평가'}</p>}
    {observed && <p className="md-subtle">누적 거래대금 기준 {time(item.provider_at)} KST · 공급자 시각 미제공은 분석 자료로 구분합니다.</p>}
    {members.length > 0 && <div className="md-member-preview">{members.slice(0, 3).map(member => <span key={member.symbol}>{member.name || member.symbol}</span>)}</div>}
    <details className="md-theme-evidence"><summary>판단 근거와 출처 <ChevronDown size={12} /></summary><Evidence items={item.evidence} />{item.factors && <div className="md-factor-list">{Object.entries(item.factors).map(([key, factor]) => <div key={key}><span>{factorLabels[key] || '추가 판단 자료'}</span><strong>{number(factor.points) ? `${factor.points.toFixed(1)}${number(factor.max_points) ? ` / ${factor.max_points}` : ''}점` : '미제공'}</strong><Status status={factor.quality} /></div>)}</div>}</details>
    <button className="md-theme-open" type="button" aria-pressed={selected} onClick={onSelect}>{selected ? <Check size={13} /> : <Layers3 size={13} />}{selected ? '선택한 테마의 종목' : '테마 종목 살펴보기'}<ArrowRight size={13} /></button>
  </article>;
}

function ThemeList({ title, subtitle, items, observed, selected, onSelect, loading, unavailable }: { title: string; subtitle: string; items: DiscoveryTheme[]; observed: boolean; selected: string; onSelect: (id: string) => void; loading: boolean; unavailable: boolean }) {
  return <section className={`md-theme-panel ${observed ? 'md-observed-panel' : ''}`}><div className="md-panel-heading"><span className="md-step">02</span><div><h3>{observed ? <Activity size={16} /> : <Sparkles size={16} />}{title}</h3><p>{subtitle}</p></div><span className="md-list-count">{items.length}개</span></div>{loading ? <div className="md-empty"><RefreshCw size={21} className="md-spin" /><strong>테마 근거를 확인합니다.</strong></div> : items.length ? <div className="md-theme-grid">{items.slice(0, 3).map((item, index) => <ThemeCard key={item.id} item={item} rank={index + 1} observed={observed} selected={selected === item.id} onSelect={() => onSelect(item.id)} />)}</div> : <div className="md-empty"><Layers3 size={26} /><strong>{observed ? '검증된 거래대금 선도 테마가 없습니다.' : '근거를 갖춘 예상 테마를 기다리고 있습니다.'}</strong><p>{unavailable ? '실제 유니버스·분류·시세 자료를 아직 확인하지 못했습니다.' : '빈 목록은 시장에 테마가 없다는 뜻이 아닙니다.'} {observed ? '예상 순위를 관측 순위로 대체하지 않습니다.' : '자료가 부족한 테마에 점수를 만들지 않습니다.'}</p></div>}</section>;
}

function ThemeStockPicks({ theme, onSelect }: { theme: DiscoveryTheme; onSelect: (item: DiscoveryStock) => void }) {
  const items = theme.recommended_stocks || [];
  if (!items.length) return <div className="md-picks-empty">이 테마의 개별 관찰 순위를 산출할 실제 시세·분석 근거가 부족합니다. 아래 목록에서 구성 종목을 먼저 확인할 수 있습니다.</div>;
  return <div className="md-theme-picks"><div><h4>테마 내 우선 관찰</h4><p>WATCH 점수로 정렬한 후보입니다. 현재 진입 확정이나 수익 확률이 아닙니다.</p></div><div>{items.slice(0, 3).map((item, index) => {
    const score = typeof item.watch_score === 'object' ? item.watch_score?.score : item.watch_score;
    return <button type="button" key={item.symbol} data-testid="discovery-stock-pick" aria-label={`${item.name || item.symbol} 테마 후보 실제 차트 관찰`} onClick={() => onSelect({ symbol: item.symbol, name: item.name || item.symbol, price: item.quote?.price ?? item.price ?? null, quote_at: item.quote?.quote_at, quote_status: item.quote?.status, watch_score: score, analysis_status: item.technical_quality, theme_refs: [{ id: theme.id, name: theme.name, relationship: item.association_kind, source_url: item.source_url }] })}><span className="md-pick-rank">{index + 1}</span><span><strong>{item.name || item.symbol}</strong><small>{item.symbol} · 산업 연결</small></span><span className="md-pick-score">{priority(score)}<small>/100</small></span><ArrowRight size={13} /></button>;
  })}</div></div>;
}

function quoteStatus(status: string | undefined, quoteAt: string | null | undefined, now: number, failed: boolean) {
  const stamp = typeof quoteAt === 'string' && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(quoteAt) ? Date.parse(quoteAt) : NaN;
  if (failed) return 'STALE';
  if (status !== 'LIVE') return status || 'UNAVAILABLE';
  const age = (now - stamp) / 1000;
  return Number.isFinite(age) && age >= -5 && age <= 15 ? 'LIVE' : 'STALE';
}

function validCandles(items?: DiscoveryCandle[]) {
  return Array.isArray(items) ? items.filter(item => typeof item.time === 'string' && Number.isFinite(Date.parse(item.time)) && [item.open, item.high, item.low, item.close].every(value => number(value) && value > 0) && number(item.volume) && item.volume >= 0 && item.low <= Math.min(item.open, item.close) && item.high >= Math.max(item.open, item.close) && item.low <= item.high && (!item.source || item.source === 'toss')) : [];
}

function Analysis({ resource, selected, onClear, reload, now }: { resource: ReturnType<typeof useDiscoveryResource<DiscoveryDetail>>; selected: DiscoveryStock; onClear: () => void; reload: () => void; now: number }) {
  const [interval, setInterval] = useState<'1d' | '5m' | '1m'>('1d');
  const detail = resource.data;
  const trusted = detail?.source === 'toss' && detail.symbol === selected.symbol;
  const candles = useMemo(() => trusted ? validCandles(interval === '1d' ? detail?.chart?.daily : interval === '5m' ? detail?.chart?.five_minute : detail?.chart?.minute) : [], [trusted, detail, interval]);
  const levels = useMemo(() => Object.fromEntries(Object.entries(trusted ? detail?.chart?.levels || {} : {}).filter(([, value]) => number(value) && value > 0)) as Partial<ChartLevels>, [trusted, detail]);
  const status = resource.failed ? 'STALE' : trusted ? detail.status : 'UNAVAILABLE';
  const currentQuote = trusted ? detail?.quote : null;
  const localQuoteStatus = quoteStatus(currentQuote?.status, currentQuote?.quote_at, now, resource.failed);
  const previewCount = candles.filter(candle => candle.status === 'PARTIAL').length;
  const early = trusted ? detail?.earlyEntry : null;
  const entryConfirmed = localQuoteStatus === 'LIVE' && !resource.failed && early?.quality === 'LIVE' && early.eligible === true && (early.state === 'EARLY_ENTRY_READY' || early.state === 'ENTRY_READY' || early.state === 'READY');
  const chartQuality = trusted && typeof detail?.quality === 'object' ? detail.quality[interval === '1d' ? 'daily' : interval === '5m' ? 'five_minute' : 'minute'] : undefined;
  const quote = number(currentQuote?.price) && currentQuote.price > 0 ? `${money(currentQuote.price)}원` : '현재가 미제공';
  return <section className="md-analysis" aria-label={`${selected.name} 실제 종목 분석`}>
    <div className="md-panel-heading"><span className="md-step">04</span><div><h3><BarChart3 size={17} />{selected.name || selected.symbol}<small>{selected.symbol} · 실제 차트·개별 관찰</small></h3><p>선택 종목의 실제 분석 캐시를 5초마다 확인합니다.</p></div><button className="md-text-button" type="button" onClick={onClear}>관찰 종료</button></div>
    <div className="md-analysis-strip" data-testid="discovery-chart-status" data-status={status}><Status status={status} /><strong>{quote}</strong><Status status={localQuoteStatus} /><span>시세 {time(currentQuote?.quote_at)} KST</span><span>분석 기준 {time(detail?.as_of)} KST</span><button className="md-text-button" type="button" onClick={reload} aria-label="선택 종목 분석 다시 확인"><RefreshCw size={12} />다시 확인</button></div>
    {(resource.failed || !trusted || detail?.status !== 'READY') && <div className="md-analysis-notice" role="status"><Clock3 size={16} /><p>{resource.failed ? '분석 자료 갱신에 실패했습니다. 마지막 자료가 있으면 지연 상태로 표시합니다.' : resource.loading || detail?.status === 'PENDING' ? '실제 캔들·기술 분석을 준비하고 있습니다. 시세 수신과 차트 분석 완료는 별도입니다.' : detail?.reason || '검증된 실제 차트 자료가 아직 없습니다.'}</p></div>}
    <div className="md-chart-toolbar"><div><span>KRX+NXT 공급자 자료 <Status status={resource.failed ? 'STALE' : typeof chartQuality === 'string' ? chartQuality : candles.length ? 'ANALYSIS_ONLY' : 'UNAVAILABLE'} /></span><small>분석 캐시와 현재 진입 판단은 별도입니다. 알 수 없는 가격선은 그리지 않습니다.</small></div><div className="md-chart-tabs" role="group" aria-label="시장 탐색 차트 간격">{(['1d', '5m', '1m'] as const).map(value => <button key={value} type="button" aria-pressed={interval === value} onClick={() => setInterval(value)}>{value === '1d' ? '일봉' : value === '5m' ? '5분' : '1분'}</button>)}</div></div>
    {candles.length ? <><PriceChart candles={candles} levels={levels} symbol={selected.symbol} interval={interval} height={310} maPeriod={detail?.technical?.shortPeriod || 5} significantCandleDate={detail?.technical?.significantCandleDate || undefined} /><div className="md-chart-summary"><span>실제 수신 {candles.length}개 봉</span><span>미완성 {previewCount}개 · 전략 확정 근거 제외</span><span>마지막 봉 {interval === '1d' ? candles[candles.length - 1].time : time(candles[candles.length - 1].end || candles[candles.length - 1].time)}</span></div><details className="md-candle-details"><summary>최근 실제 캔들 값 <ChevronDown size={12} /></summary><div className="md-table-scroll"><table className="md-data-table" data-testid="discovery-chart-table"><thead><tr><th>봉 시각</th><th>시가</th><th>고가</th><th>저가</th><th>종가</th><th>거래량</th><th>확정 상태</th></tr></thead><tbody>{candles.slice(-8).reverse().map(candle => <tr key={candle.time}><td>{interval === '1d' ? candle.time : time(candle.start || candle.time)}</td><td>{money(candle.open)}</td><td>{money(candle.high)}</td><td>{money(candle.low)}</td><td>{money(candle.close)}</td><td>{money(candle.volume)}</td><td>{candle.status === 'PARTIAL' ? '미완성 · PREVIEW' : candle.status === 'CLOSED' ? '확정' : '상태 미제공'}</td></tr>)}</tbody></table></div></details></> : <div className="md-chart-empty"><BarChart3 size={34} /><strong>{resource.loading || detail?.status === 'PENDING' ? '실제 차트 분석 대기' : '실제 캔들 미제공'}</strong><p>샘플 차트로 대체하지 않습니다. 다른 간격의 자료가 있으면 간격을 바꾸어 확인할 수 있습니다.</p></div>}
    <div className="md-analysis-bottom"><div><h4><Telescope size={14} />개별 종목 관찰</h4><p>{trusted && detail?.theme ? `${detail.theme.name} 산업 연결을 바탕으로 추가 검증합니다.` : '산업 연결·뉴스 근거를 개별 종목에서 다시 확인합니다.'}</p>{trusted && detail?.watch_score && <div className="md-detail-priority">관찰 우선순위 <strong>{priority(detail.watch_score.score)} / 100</strong><span>WATCH · 수익 확률 아님</span></div>}<ul>{trusted && detail?.reasons?.length ? detail.reasons.slice(0, 5).map((reason, index) => <li key={index}>{analysisText(reason)}</li>) : <li>검증된 개별 판단 근거가 아직 제공되지 않았습니다.</li>}</ul></div><div><h4><ShieldCheck size={14} />위험·선진입 상태 <Status status={resource.failed || localQuoteStatus === 'STALE' ? 'STALE' : early?.quality} /></h4><p>{entryConfirmed ? '서버의 현재 선진입 조건 판정이 제공되었습니다. 가격과 위험 자료를 함께 확인하세요.' : '현재 진입 확정으로 표시하지 않습니다.'}</p><div className="md-early-metrics"><span>MA5_DAILY <strong>{number(early?.MA5_DAILY) ? `${money(early.MA5_DAILY)}원` : '미제공'}</strong></span><span>MA5_5M <strong>{number(early?.MA5_5M) ? `${money(early.MA5_5M)}원` : '이력 부족'}</strong></span><span>첫 저항 손익비 <strong>{number(early?.risk?.first_resistance_reward_risk) ? `1 : ${early.risk.first_resistance_reward_risk.toFixed(2)}` : '미확인'}</strong></span></div>{trusted && detail?.warnings?.length ? <ul>{detail.warnings.slice(0, 4).map((warning, index) => <li key={index}>{analysisText(warning)}</li>)}</ul> : <p>거래정지·스프레드·구조적 손절을 확인한 뒤 판단하세요.</p>}</div></div>
    <div className="md-analysis-footer"><ShieldCheck size={12} />실제 주문 비활성화 · 산업 소속과 개별 수혜, 관찰 우선순위와 진입 적합성은 구분합니다.</div>
  </section>;
}

export default function MarketDiscovery() {
  const [reload, setReload] = useState(0);
  const [detailReload, setDetailReload] = useState(0);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [themeId, setThemeId] = useState('');
  const [unclassified, setUnclassified] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<DiscoveryStock | null>(null);
  const [now, setNow] = useState(Date.now());
  const analysisRef = useRef<HTMLDivElement>(null);
  const stockRef = useRef<HTMLElement>(null);
  useEffect(() => { const timer = window.setTimeout(() => { setQuery(search.trim()); setPage(1); }, 350); return () => window.clearTimeout(timer); }, [search]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1_000); return () => window.clearInterval(timer); }, []);
  const overview = useDiscoveryResource<DiscoveryOverview>('/discovery/overview', OVERVIEW_INTERVAL, reload);
  const parameters = new URLSearchParams({ page: String(page), limit: String(PAGE_SIZE) });
  if (themeId) parameters.set('theme_id', themeId);
  if (query) parameters.set('q', query);
  if (unclassified) parameters.set('unclassified', 'true');
  const stocks = useDiscoveryResource<DiscoveryStockPage>(`/discovery/stocks?${parameters.toString()}`, OVERVIEW_INTERVAL, reload);
  const detailPath = selected ? `/discovery/stocks/${encodeURIComponent(selected.symbol)}${themeId ? `?theme_id=${encodeURIComponent(themeId)}` : ''}` : null;
  const detail = useDiscoveryResource<DiscoveryDetail>(detailPath, DETAIL_INTERVAL, detailReload);
  const data = overview.data?.source === 'toss' ? overview.data : null;
  const themes = data?.themes || [];
  const hypotheses = overview.failed ? [] : data?.theme_hypotheses_top3 || [];
  const observed = overview.failed ? [] : data?.observed_theme_leaders || [];
  const themeOptions = [...new Map([...themes, ...hypotheses, ...observed].map(item => [item.id, item])).values()];
  const rows = stocks.data?.items || [];
  const universe = data?.universe;
  const pages = Math.max(1, Math.ceil((stocks.data?.total || 0) / PAGE_SIZE));
  const selectedTheme = themes.find(item => item.id === themeId) || hypotheses.find(item => item.id === themeId) || observed.find(item => item.id === themeId);
  const newsStatus = typeof data?.news_status === 'string' ? data.news_status : data?.news_status?.status;
  const chooseTheme = (id: string) => { setThemeId(id); setUnclassified(false); setPage(1); setSelected(null); window.setTimeout(() => stockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0); };
  const showUnclassified = () => { setThemeId(''); setUnclassified(true); setPage(1); setSelected(null); window.setTimeout(() => stockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0); };
  const openAnalysis = (item: DiscoveryStock) => { setSelected(item); window.setTimeout(() => analysisRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0); };
  return <section className="market-discovery" data-testid="market-discovery" aria-label="전체 종목 시장 탐색">
    <div className="md-heading"><div><span className="md-eyebrow"><Globe2 size={13} />MARKET DISCOVERY</span><h2>시장 분석에서, 종목의 자리까지.</h2><p>거래 가능 종목과 산업 분류를 탐색하고, 예상 테마를 실제 관측과 다시 비교합니다.</p></div><div className="md-heading-meta"><Status status={overview.failed ? 'STALE' : data?.status} /><span>{time(data?.as_of)} KST</span><button type="button" className="md-icon-button" aria-label="시장 탐색 자료 새로고침" onClick={() => setReload(value => value + 1)}><RefreshCw size={15} className={overview.loading ? 'md-spin' : ''} /></button></div></div>
    <div className="md-flow-guide"><span><b>01</b>시장 범위</span><ChevronRight size={12} /><span><b>02</b>예상·관측 테마</span><ChevronRight size={12} /><span><b>03</b>종목 관찰</span><ChevronRight size={12} /><span><b>04</b>실제 차트</span></div>
    {(overview.failed || stocks.failed) && <div className="md-error" role="status"><Info size={15} /><span>시장 탐색 갱신에 실패했습니다. 기존 자료는 지연 상태이며 최신 순위로 사용하지 않습니다.</span><button type="button" className="md-text-button" onClick={() => setReload(value => value + 1)}>다시 확인</button></div>}
    <section className="md-universe" aria-label="시장 탐색 관측 범위"><div className="md-universe-heading"><div><span className="md-step">01</span><h3>유니버스와 관측 범위</h3><Status status={overview.failed ? 'STALE' : universe?.status} /></div><span>{universe?.markets?.join(' · ') || '시장 범위 미확인'}</span></div><div className="md-coverage-grid"><div><span>목록에서 확인한 대상</span><strong>{count(universe?.expected)}</strong><small>검증된 공급자 종목 목록</small></div><div><span>실제 관측</span><strong>{count(universe?.observed)}</strong><small>최신 시세 {count(universe?.quote_fresh)}</small></div><div><span>산업 분류 연결</span><strong>{count(universe?.classified)}</strong><small>직접 수혜 확인과 별도</small></div><button type="button" onClick={showUnclassified}><span>미분류 종목 <ArrowRight size={12} /></span><strong>{count(universe?.unclassified)}</strong><small>알려진 종목 목록에서 탐색</small></button></div><div className="md-coverage-note"><CircleHelp size={13} /><p>{universe?.scope || 'Toss 거래 가능 종목의 검증된 목록을 기준으로 합니다. 거래소 전체 상장 종목을 모두 관측했다는 뜻이 아닙니다.'}</p><span>누락 {count(universe?.missing)} · 지연 {count(universe?.stale)}{number(universe?.bootstrap_observed) && universe.bootstrap_observed > 0 && <> · 초기 랭킹 관측 {count(universe.bootstrap_observed)} (목록 분모 제외)</>}</span></div></section>
    <div className="md-theme-columns"><ThemeList title="예상 수급 테마 Top 3" subtitle="근거를 바탕으로 세운 관찰 가설 · WATCH" items={hypotheses} observed={false} selected={themeId} onSelect={chooseTheme} loading={overview.loading} unavailable={!data || data.status === 'UNAVAILABLE'} /><ThemeList title="관측 거래대금 선도" subtitle="조회된 랭킹 종목군 · 순매수 아님" items={observed} observed selected={themeId} onSelect={chooseTheme} loading={overview.loading} unavailable={!data || data.status === 'UNAVAILABLE'} /></div>
    <div className="md-news-note"><Info size={13} /><p>뉴스·공시 상태 <Status status={newsStatus} /> 산업 분류만으로 특정 기업의 수혜나 미래 수급을 확정하지 않습니다. 결측 자료는 관찰 점수의 근거로 만들지 않습니다.</p></div>
    {!overview.failed && Boolean(data?.unmapped_leaders?.length) && <section className="md-unmapped-leaders" aria-label="분류 연결 대기 종목"><div><h3><Layers3 size={14} />분류 연결을 기다리는 관측 종목</h3><p>조회된 랭킹 종목군의 일부입니다. 확인되지 않은 산업·테마를 임의로 붙이지 않습니다.</p></div><div className="md-unmapped-chips">{data?.unmapped_leaders.slice(0, 5).map(item => <button type="button" key={item.symbol} onClick={() => openAnalysis(item)} aria-label={`${item.name || item.symbol} 미분류 종목 실제 차트 관찰`}><span>{item.name || item.symbol}<small>{item.symbol}</small></span><strong>{amount(item.observed_turnover_krw ?? item.trading_amount_krw)}</strong><ArrowRight size={12} /></button>)}</div><button type="button" className="md-text-button" onClick={showUnclassified}>미분류 전체 목록 <ArrowRight size={12} /></button></section>}
    <section className="md-stocks" ref={stockRef} aria-label="시장 탐색 종목 목록"><div className="md-panel-heading"><span className="md-step">03</span><div><h3><Telescope size={16} />{selectedTheme ? `${selectedTheme.name} 종목 관찰` : unclassified ? '미분류 종목 탐색' : '전체 종목 탐색'}</h3><p>검색과 페이지는 전체 검증된 목록을 탐색합니다. 최신 시세 수집 범위는 별도입니다.</p></div><Status status={stocks.failed ? 'STALE' : stocks.data?.status} /></div>{selectedTheme && <ThemeStockPicks theme={selectedTheme} onSelect={openAnalysis} />}<div className="md-stock-controls"><label className="md-search"><Search size={15} /><input type="search" aria-label="시장 탐색 종목 검색" placeholder="종목명 또는 코드 검색" value={search} onChange={event => setSearch(event.target.value)} /></label><label className="md-filter"><Layers3 size={13} /><select aria-label="시장 탐색 테마 필터" value={themeId} onChange={event => { setThemeId(event.target.value); setUnclassified(false); setPage(1); setSelected(null); }}><option value="">전체 산업·테마</option>{themeOptions.map(theme => <option key={theme.id} value={theme.id}>{theme.name}</option>)}</select></label><button type="button" className={`md-unclassified-toggle ${unclassified ? 'md-active' : ''}`} aria-pressed={unclassified} onClick={() => { setUnclassified(value => !value); setThemeId(''); setPage(1); setSelected(null); }}>미분류만 보기{unclassified && <Check size={12} />}</button>{(query || themeId || unclassified) && <button type="button" className="md-text-button" onClick={() => { setSearch(''); setQuery(''); setThemeId(''); setUnclassified(false); setPage(1); setSelected(null); }}>필터 해제</button>}</div><div className="md-stock-caption"><span>조회 결과 <strong>{number(stocks.data?.total) ? money(stocks.data.total) : '—'}개</strong> · 페이지당 최대 {PAGE_SIZE}개</span><span>목록 30초 갱신 · 선택 종목 5초 관찰</span></div>{stocks.loading ? <div className="md-empty"><RefreshCw size={22} className="md-spin" /><strong>종목 목록을 확인합니다.</strong></div> : rows.length ? <div className="md-table-scroll"><table className="md-data-table md-stock-table" data-testid="discovery-stock-table"><thead><tr><th>종목·시장</th><th>산업·테마 관계</th><th>현재가·시세 상태</th><th>관측 거래대금</th><th>관찰 우선순위</th><th>개별 분석</th></tr></thead><tbody>{rows.map(item => <tr key={item.symbol} data-testid="discovery-stock-row" data-symbol={item.symbol} data-catalog-member={item.catalog_member} className={selected?.symbol === item.symbol ? 'md-row-selected' : ''}><td><strong>{item.name || item.symbol}</strong><small>{item.symbol} · {item.market || '시장 미확인'}</small>{item.catalog_member === false && <small className="md-bootstrap-label">초기 관측 · 정식 목록 확인 대기</small>}</td><td>{item.theme_refs?.length ? <><span>{item.theme_refs.slice(0, 2).map(theme => theme.name).join(' · ')}</span><small>{item.theme_refs.some(theme => theme.relationship === 'DIRECT_VERIFIED') ? '직접 관계 확인 자료 포함' : '산업·테마 연결 · 직접 수혜 미확인'}{item.theme_refs.length > 2 ? ` 외 ${item.theme_refs.length - 2}개` : ''}</small></> : <><span className="md-unmapped-label">미분류</span><small>임의로 테마를 배정하지 않습니다.</small></>}</td><td><strong>{number(item.price) && item.price > 0 ? `${money(item.price)}원` : '미제공'}</strong><small><Status status={quoteStatus(item.quote_status, item.quote_at, now, stocks.failed)} /> {time(item.quote_at)}</small></td><td><strong>{amount(item.observed_turnover_krw)}</strong><small><Status status={stocks.failed ? 'STALE' : item.turnover_quality} /> 체결 금액</small></td><td><strong className="md-watch-score">{priority(item.watch_score)}<small>/100</small></strong><small>WATCH · 수익 확률 아님</small></td><td><button type="button" className="md-open-stock" aria-label={`${item.name || item.symbol} 실제 차트 관찰`} onClick={() => openAnalysis(item)}>{selected?.symbol === item.symbol ? '관찰 중' : '차트·관찰'}{selected?.symbol === item.symbol ? <Check size={12} /> : <ArrowDown size={12} />}</button><small>{qualityLabels[item.analysis_status || ''] || '분석 상태 미확인'}</small></td></tr>)}</tbody></table></div> : <div className="md-empty"><Search size={25} /><strong>{stocks.failed ? '종목 목록을 가져오지 못했습니다.' : '현재 조건의 검증된 종목이 없습니다.'}</strong><p>{query || themeId || unclassified ? '검색어와 분류 필터를 바꾸어 확인하세요.' : '실제 거래 가능 종목 목록을 수집하면 여기에 표시됩니다.'} 샘플 종목으로 목록을 채우지 않습니다.</p></div>}<div className="md-pagination"><span>{stocks.data?.scope || '공급자에서 확인한 종목 목록'} <small>· {time(stocks.data?.as_of)} KST</small></span><div><button type="button" aria-label="시장 탐색 이전 페이지" disabled={page <= 1 || stocks.loading} onClick={() => { setPage(value => value - 1); setSelected(null); }}><ChevronLeft size={14} /></button><span>{page} / {pages}</span><button type="button" aria-label="시장 탐색 다음 페이지" disabled={page >= pages || stocks.loading} onClick={() => { setPage(value => value + 1); setSelected(null); }}><ChevronRight size={14} /></button></div></div></section>
    <div ref={analysisRef} className="md-analysis-anchor">{selected ? <Analysis key={selected.symbol} resource={detail} selected={selected} onClear={() => setSelected(null)} reload={() => setDetailReload(value => value + 1)} now={now} /> : <div className="md-analysis-placeholder"><BarChart3 size={25} /><div><h3>종목을 선택해 실제 차트를 이어서 확인하세요.</h3><p>미등록 종목도 검증된 목록에서 찾을 수 있습니다. 개별 분석과 시세 상태를 함께 보여드립니다.</p></div><span>04 <ArrowRight size={14} /></span></div>}</div>
    <div className="md-footer"><ShieldCheck size={13} /><p>{data?.measurement_definition || '관측 거래대금은 체결 금액이며 순매수·미래 유입액이 아닙니다. 예상 테마는 근거 기반 관찰 가설입니다.'} 실제 주문은 비활성화되어 있습니다.</p><span>Toss 실제 자료 캐시</span></div>
  </section>;
}
