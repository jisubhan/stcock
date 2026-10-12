'use client';

import { useEffect, useState } from 'react';
import { Activity, Clock3, Info, Radio, RefreshCw } from 'lucide-react';
import { api, money } from '@/lib/api';
import './live-quotes.css';

type QuoteStatus = 'LIVE' | 'PARTIAL' | 'STALE' | 'UNAVAILABLE';
interface QuoteObservation {
  symbol: string;
  name?: string | null;
  price: number;
  change_pct?: number | null;
  quote_at: string;
  first_seen_at: string;
  status: 'LIVE' | 'STALE';
  latency_seconds?: number | null;
  nxt_supported?: boolean | null;
}
interface QuotesSnapshot {
  as_of: string;
  source: string;
  provider: string;
  status: QuoteStatus;
  session_id?: string | null;
  session_label?: string | null;
  is_open?: boolean | null;
  poll_interval_seconds?: number;
  items: QuoteObservation[];
  reason?: string | null;
  measurement_definition?: string;
}

const POLL_MILLISECONDS = 5_000;
const MAX_QUOTE_AGE_SECONDS = 15;

function timestamp(value: unknown): number | null {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function quoteTime(value?: string | null) {
  if (!value || timestamp(value) === null) return '미제공';
  return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value));
}
function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function ageSeconds(observation: QuoteObservation, now: number) {
  const quoteAt = timestamp(observation.quote_at);
  if (quoteAt === null) return null;
  const localAge = (now - quoteAt) / 1_000;
  // A future timestamp cannot establish freshness on the browser's clock.
  if (localAge < -5) return null;
  return Math.max(0, localAge, finite(observation.latency_seconds) ? observation.latency_seconds : 0);
}
function ageLabel(value: number | null) {
  if (value === null) return '시각 확인 필요';
  if (value < 60) return `${Math.floor(value)}초 경과`;
  if (value < 3_600) return `${Math.floor(value / 60)}분 경과`;
  if (value < 86_400) return `${Math.floor(value / 3_600)}시간 경과`;
  return `${Math.floor(value / 86_400)}일 경과`;
}
function StatusBadge({ status }: { status: QuoteStatus }) { return <span className={`lq-status lq-status-${status.toLowerCase()}`}>{status}</span>; }

export default function LiveQuotes() {
  const [data, setData] = useState<QuotesSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const readQuotes = async () => {
      if (pending || controller.signal.aborted) return;
      pending = true;
      try {
        const snapshot = await api<QuotesSnapshot>('/market/quotes', { signal: controller.signal });
        if (!controller.signal.aborted) { setData(snapshot); setFailed(false); setNow(Date.now()); }
      } catch {
        if (!controller.signal.aborted) { setFailed(true); setNow(Date.now()); }
      } finally {
        pending = false;
        if (!controller.signal.aborted) { setLoading(false); setRefreshing(false); }
      }
    };
    void readQuotes();
    const poller = setInterval(() => void readQuotes(), POLL_MILLISECONDS);
    const freshnessClock = setInterval(() => setNow(Date.now()), 1_000);
    return () => { controller.abort(); clearInterval(poller); clearInterval(freshnessClock); };
  }, [reload]);

  const trusted = data?.source === 'toss';
  const items = trusted && data.status !== 'UNAVAILABLE' && Array.isArray(data.items)
    ? data.items.filter(item => typeof item.symbol === 'string' && finite(item.price) && item.price > 0 && timestamp(item.quote_at) !== null).slice(0, 15)
    : [];
  const rows = items.map(item => {
    const age = ageSeconds(item, now);
    const status: 'LIVE' | 'STALE' = !failed && data?.is_open === true && data.status !== 'STALE' && item.status === 'LIVE' && age !== null && age <= MAX_QUOTE_AGE_SECONDS ? 'LIVE' : 'STALE';
    return { item, age, status };
  });
  const freshCount = rows.filter(row => row.status === 'LIVE').length;
  const status: QuoteStatus = failed ? rows.length ? 'STALE' : 'UNAVAILABLE'
    : !trusted ? 'UNAVAILABLE'
    : !rows.length ? data?.status === 'STALE' ? 'STALE' : 'UNAVAILABLE'
    : !freshCount ? 'STALE'
    : data?.status === 'PARTIAL' || freshCount < rows.length ? 'PARTIAL' : 'LIVE';
  const closed = data?.is_open === false;
  const sessionLabel = data?.session_label || data?.session_id || '장 세션 확인 대기';
  const reason = failed ? rows.length ? '시세 조회 연결이 끊겨 마지막 수신값을 표시합니다. 모든 수신값의 LIVE 표시를 중지했습니다.' : '실제 시세 조회 연결을 확인할 수 없습니다.'
    : data?.reason || (!trusted ? '현재 공급자의 실제 시세를 확인할 수 없습니다.' : !rows.length ? '등록 종목의 검증된 실제 현재가가 아직 수신되지 않았습니다.' : null);

  return <section className="live-quotes" aria-label="실제 시세 모니터" data-testid="live-quotes" data-status={status}>
    <header className="lq-heading"><div className="lq-heading-copy"><span className="lq-heading-icon"><Radio size={19} /></span><div><h3>실제 시세 모니터 <StatusBadge status={status} /></h3><p>{trusted ? data.provider || 'Toss REST · KRX+NXT 통합' : '토스증권 통합 현재가 연결 확인'} · 등록 종목 조회</p></div></div><button type="button" className="lq-refresh" aria-label="실제 시세 모니터 새로고침" disabled={loading || refreshing} onClick={() => { setRefreshing(true); setReload(value => value + 1); }}><RefreshCw size={14} className={refreshing ? 'lq-spin' : ''} /><span>시세 확인</span></button></header>
    <div className="lq-session"><span className={`lq-session-label ${closed ? 'lq-session-closed' : ''}`}><Clock3 size={12} />{sessionLabel}{closed ? ' · 장 종료' : ''}</span><span>조회 기준 {quoteTime(data?.as_of)} KST</span><span className="lq-fresh-count">{rows.length ? `${freshCount}개 최신 · ${rows.length - freshCount}개 지연` : '시세 수신 대기'}</span></div>
    {reason && <div className={`lq-reason ${failed ? 'lq-reason-failed' : ''}`} role="status"><Info size={14} /><p>{reason}</p></div>}
    {loading ? <div className="lq-empty"><RefreshCw size={19} className="lq-spin" /><strong>등록 종목의 실제 시세를 확인합니다.</strong></div> : rows.length ? <div className="lq-table-wrap"><table className="lq-table"><thead><tr><th>등록 종목</th><th>현재가{closed || failed || !freshCount ? ' · 마지막 수신값' : ''}</th><th>등락률</th><th>공급자 시세 시각 / 첫 수신</th><th>시세 신선도</th><th>NXT 지원</th></tr></thead><tbody>{rows.map(({ item, age, status: rowStatus }) => <tr key={item.symbol} data-testid="live-quote-row" data-status={rowStatus} className={rowStatus === 'STALE' ? 'lq-stale-row' : ''}><td><strong>{item.name || item.symbol}</strong>{item.name && <small>{item.symbol}</small>}</td><td className="lq-price"><strong>{money(item.price)}<small>원</small></strong>{rowStatus === 'STALE' && <span>마지막 수신값</span>}</td><td><span className={finite(item.change_pct) ? item.change_pct > 0 ? 'lq-up' : item.change_pct < 0 ? 'lq-down' : 'lq-neutral' : 'lq-no-value'}>{finite(item.change_pct) ? `${item.change_pct > 0 ? '+' : ''}${item.change_pct.toFixed(2)}%` : '미제공'}</span></td><td className="lq-times"><span>{quoteTime(item.quote_at)} KST</span><small>첫 수신 {quoteTime(item.first_seen_at)} KST</small></td><td><StatusBadge status={rowStatus} /><small className="lq-age">{ageLabel(age)}</small></td><td><span className={`lq-nxt ${item.nxt_supported === true ? 'lq-nxt-supported' : ''}`}>{item.nxt_supported === true ? 'NXT 가능' : item.nxt_supported === false ? 'NXT 미지원' : '확인 대기'}</span></td></tr>)}</tbody></table></div> : <div className="lq-empty"><Activity size={23} /><strong>표시할 실제 시세 수신값이 없습니다.</strong><p>공급자의 현재가와 관측 시각이 확인되면 이 표에 표시합니다.</p></div>}
    <footer className="lq-footer"><span><RefreshCw size={11} />5초마다 읽기 · 시세 시각 15초 초과 시 STALE</span><span>등록 종목 범위 · 실제 주문 비활성화</span></footer>
  </section>;
}
