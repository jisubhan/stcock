'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, ArrowUpRight, Check, CheckCircle2, FlaskConical, Info, Link2, LockKeyhole, Pencil, Plus, RefreshCw, ShieldCheck, Star, TriangleAlert, Wallet, X } from 'lucide-react';
import { api, compactMoney, money, pct, stateLabel } from '../lib/api';
import type { Market, Stock, Theme, View } from '../lib/types';
import './workspace-views.css';

interface Props {
  market?: Market | null;
  view: View;
  stocks: Stock[];
  themes: Theme[];
  watchlist: string[];
  toggleWatch: (symbol: string) => void;
  onOpenStock: (symbol: string) => void;
  onRefresh: () => void;
  toast: (message: string) => void;
}

type Strategy = Record<string, Record<string, number>>;
interface BacktestTrade { symbol: string; name?: string; pattern?: string; entryPrice?: number; exitPrice?: number; resultPct?: number; mfe?: number; mae?: number; targetHit?: boolean; stopHit?: boolean; [key: string]: unknown }
interface BacktestResult {
  mode?: string; label?: string; runAt?: string; signalCount?: number; winRate?: number; avgReturn?: number; avgMfe?: number; avgMae?: number; profitFactor?: number; maxLosingStreak?: number;
  summary?: Record<string, number | null>;
  patterns?: Record<string, unknown>[];
  themes?: Record<string, unknown>[];
  trades?: BacktestTrade[];
  [key: string]: unknown;
}
interface PaperPosition { id: string; symbol: string; name: string; quantity: number; entryPrice: number; currentPrice?: number; price?: number; pnl?: number; returnPct?: number; stop?: number; target?: number; status?: string; openedAt?: string; closedAt?: string; exitPrice?: number; [key: string]: unknown }
interface PaperAccount { mode?: string; source?: string; asOf?: string; notice?: string; positions: PaperPosition[]; history?: PaperPosition[]; summary: { invested: number; realizedPnl: number; unrealizedPnl: number; winRate: number; openPositions: number; closedPositions: number }; [key: string]: unknown }
interface ConnectionStatus { mode?: string; source?: string; asOf?: string; notice?: string; note?: string; verification?: string; authStatus?: string; condition?: string; httpStatus?: number; errorCode?: string; automaticRetryBlocked?: boolean; schemaStatus?: string; sampleStatus?: string; verificationStatus?: string; dataQuality?: {status?: string; reason?: string}; blockers?: string[]; ready: boolean; credentials: Record<string, boolean>; venue_verified: boolean; live_orders_enabled: boolean }

function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="wv-error" role="alert"><TriangleAlert size={17} /><span>{message}</span>{retry && <button type="button" className="secondary-button" onClick={retry}>다시 시도</button>}</div>;
}
function LoadingNotice() { return <div className="empty-state wv-loading"><RefreshCw size={22} className="wv-spin" /><p>데이터를 불러오고 있어요.</p></div>; }
function ViewHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="wv-heading"><div><span className="eyebrow">{eyebrow}</span><h1 className="section-title">{title}</h1><p className="muted">{description}</p></div>{action}</div>;
}
function Metric({ label, value, detail, tone }: { label: string; value: string; detail?: string; tone?: string }) {
  return <div className="wv-metric"><span className="muted">{label}</span><strong className={tone || ''}>{value}</strong>{detail && <small className="muted">{detail}</small>}</div>;
}

function WatchlistView({ stocks, watchlist, toggleWatch, onOpenStock, toast }: Props) {
  const followed = stocks.filter(stock => watchlist.includes(stock.symbol));
  const available = stocks.filter(stock => !watchlist.includes(stock.symbol));
  const [selected, setSelected] = useState('');
  return <div className="workspace-view">
    <ViewHeading eyebrow="MY WATCHLIST" title="나만의 관심 종목" description="주목하는 종목의 진입 구간과 위험 기준을 한곳에서 확인하세요." action={<span className="wv-count"><Star size={16} /> {followed.length}개 관심 종목</span>} />
    <div className="panel wv-add-stock"><div><h3>새로운 기회를 놓치지 마세요</h3><p className="muted">추천 종목의 별 아이콘을 누르거나, 아래에서 종목을 추가할 수 있어요.</p></div><form onSubmit={event => { event.preventDefault(); if (selected) { toggleWatch(selected); setSelected(''); toast('관심 종목에 추가했어요.'); } }}><label className="sr-only" htmlFor="watch-stock">관심 종목 추가</label><select id="watch-stock" value={selected} onChange={event => setSelected(event.target.value)}><option value="">종목 선택</option>{available.map(stock => <option key={stock.symbol} value={stock.symbol}>{stock.name} · {stock.symbol}</option>)}</select><button className="primary-button" type="submit" disabled={!selected}><Plus size={16} /> 추가</button></form></div>
    <section className="panel"><div className="panel-header"><div><h2>관심 종목</h2><p className="muted">관심 목록은 이 브라우저에 저장됩니다.</p></div><span className="badge">{followed.length} STOCKS</span></div>
      {followed.length ? <div className="table-wrap"><table className="data-table wv-table"><thead><tr><th>종목</th><th>현재가 / 등락률</th><th>추천 점수</th><th>진입 관찰 구간</th><th>무효화 기준</th><th>현재 상태</th><th><span className="sr-only">관심 종목 관리</span></th></tr></thead><tbody>{followed.map(stock => <tr key={stock.symbol}><td><button className="wv-stock-name" onClick={() => onOpenStock(stock.symbol)}>{stock.name}<span>{stock.symbol} · {stock.theme}</span></button></td><td><strong>{money(stock.price)}원</strong><span className={`wv-cell-sub ${stock.changePct >= 0 ? 'positive' : 'negative'}`}>{pct(stock.changePct)}</span></td><td><span className="wv-score">{stock.grade} <strong>{stock.score}</strong></span></td><td>{money(stock.entry.low)} ~ {money(stock.entry.high)}</td><td>{money(stock.risk.stop)}원<span className="wv-cell-sub muted">회복 실패 시 무효화</span></td><td><span className="wv-state">{stateLabel[stock.state]}</span></td><td><button className="wv-star-button" aria-label={`${stock.name} 관심 종목 해제`} onClick={() => { toggleWatch(stock.symbol); toast('관심 종목에서 해제했어요.'); }}><Star size={18} fill="currentColor" /></button></td></tr>)}</tbody></table></div> : <div className="empty-state wv-empty"><div className="wv-empty-icon"><Star size={27} /></div><h3>아직 관심 종목이 없어요</h3><p className="muted">계속 지켜보고 싶은 종목을 추가해 보세요.</p></div>}
    </section>
    <div className="wv-inline-note"><Info size={16} /><p>추천 신호는 의사결정을 돕는 참고 정보입니다. 진입 전 종목 상세에서 추천 근거와 위험요인을 확인하세요.</p></div>
  </div>;
}

function ThemeEditor({ theme, stocks, onSaved, toast }: { theme: Theme; stocks: Stock[]; onSaved: () => void; toast: Props['toast'] }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(theme.name);
  const [members, setMembers] = useState(theme.memberSymbols.join(', '));
  const [tickers, setTickers] = useState(theme.usTickers.join(', '));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const memberStocks = stocks.filter(stock => theme.memberSymbols.includes(stock.symbol));
  const reset = () => { setName(theme.name); setMembers(theme.memberSymbols.join(', ')); setTickers(theme.usTickers.join(', ')); setError(''); setEditing(false); };
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setPending(true); setError('');
    try { await api(`/themes/${theme.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim(), memberSymbols: members.split(',').map(item => item.trim()).filter(Boolean), usTickers: tickers.split(',').map(item => item.trim().toUpperCase()).filter(Boolean) }) }); setEditing(false); onSaved(); toast('테마 매핑을 저장했어요.'); }
    catch (err) { setError(err instanceof Error ? err.message : '테마 저장에 실패했어요.'); }
    finally { setPending(false); }
  };
  return <article className="panel wv-theme-card"><div className="wv-theme-top"><div className={`wv-theme-icon wv-theme-${theme.id}`}><Link2 size={22} /></div><button className="wv-icon-button" aria-label={`${theme.name} 매핑 ${editing ? '닫기' : '수정'}`} aria-expanded={editing} onClick={() => editing ? reset() : setEditing(true)}>{editing ? <X size={18} /> : <Pencil size={16} />}</button></div><div className="wv-theme-title"><h3>{theme.name}</h3><span>{theme.score}<small> / 100</small></span></div><div className="wv-strength-track"><div style={{ width: `${Math.min(theme.score, 100)}%` }} /></div><div className="wv-theme-stats"><div><span className="muted">상승 종목 비율</span><strong>{theme.breadthPct.toFixed(0)}%</strong></div><div><span className="muted">총 거래대금</span><strong>{compactMoney(theme.tradedValue)}</strong></div><div><span className="muted">평균 등락률</span><strong className={theme.changePct >= 0 ? 'positive' : 'negative'}>{pct(theme.changePct)}</strong></div></div>
    <div className="wv-theme-members"><span className="muted">국내 구성 종목</span><div>{memberStocks.map(stock => <span className="wv-chip" key={stock.symbol}>{stock.name}</span>)}{theme.memberSymbols.filter(symbol => !stocks.some(stock => stock.symbol === symbol)).map(symbol => <span className="wv-chip" key={symbol}>{symbol}</span>)}</div></div>
    <div className="wv-us-mapping"><span>미국 선행 테마</span><strong className={theme.usLeadPct == null ? 'muted' : theme.usLeadPct >= 0 ? 'positive' : 'negative'}>{theme.usLeadPct == null ? '데이터 없음' : pct(theme.usLeadPct)}</strong><div>{theme.usTickers.map(ticker => <span key={ticker}>{ticker}</span>)}</div></div>
    {editing && <form className="wv-theme-form" onSubmit={save}><h4>테마 매핑 수정</h4><label className="field">테마 이름<input value={name} required maxLength={50} onChange={event => setName(event.target.value)} /></label><label className="field">국내 종목 코드<input value={members} required onChange={event => setMembers(event.target.value)} placeholder="010170, 054220, 138080" /><small>쉼표로 구분한 6자리 종목 코드</small></label><label className="field">미국 종목 티커<input value={tickers} onChange={event => setTickers(event.target.value)} placeholder="MRVL, CIEN, COHR" /><small>쉼표로 구분해 입력하세요.</small></label>{error && <ErrorNotice message={error} />}<div className="wv-form-actions"><button className="secondary-button" type="button" onClick={reset}>취소</button><button className="primary-button" disabled={pending} type="submit"><Check size={15} />{pending ? '저장 중…' : '매핑 저장'}</button></div></form>}
  </article>;
}
function ThemesView(props: Props) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [members, setMembers] = useState<string[]>([]);
  const [tickers, setTickers] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const create = async (event: React.FormEvent) => {
    event.preventDefault(); setPending(true); setError('');
    try {
      await api('/themes', { method: 'POST', body: JSON.stringify({ id: `custom_${Date.now()}`, name: name.trim(), memberSymbols: members, usTickers: tickers.split(',').map(item => item.trim().toUpperCase()).filter(Boolean) }) });
      setAdding(false); setName(''); setMembers([]); setTickers(''); props.onRefresh(); props.toast('새 테마를 추가했어요.');
    } catch (err) { setError(err instanceof Error ? err.message : '테마를 추가하지 못했어요.'); }
    finally { setPending(false); }
  };
  return <div className="workspace-view"><ViewHeading eyebrow="THEME EXPLORER" title="돈이 모이는 테마" description="미국장의 흐름과 국내 종목의 동반 상승을 함께 살펴보세요." action={<div className="wv-form-actions"><button className="secondary-button" onClick={() => { props.onRefresh(); props.toast('테마 분석을 새로 불러옵니다.'); }}><RefreshCw size={16} /> 새로고침</button><button className="primary-button" onClick={() => { setAdding(current => !current); setError(''); }} aria-expanded={adding}><Plus size={16} /> 새 테마</button></div>} /><div className="wv-explainer"><div className="wv-explainer-icon"><Link2 size={23} /></div><div><h3>해외의 흐름이 국내의 기회로</h3><p>미국 선행 섹터의 움직임 → 국내 테마 동조 확인 → 거래대금과 수급이 좋은 종목 분석</p></div><span className="badge">US → KR</span></div>{adding && <form className="panel wv-create-theme" onSubmit={create}><div className="wv-theme-top"><h3>새 테마 만들기</h3><button className="wv-icon-button" type="button" aria-label="새 테마 만들기 닫기" onClick={() => setAdding(false)}><X size={17} /></button></div><div className="form-grid"><label className="field">테마 이름<input value={name} onChange={event => setName(event.target.value)} maxLength={50} required placeholder="예: AI 인프라" /></label><label className="field">미국 선행 종목<input value={tickers} onChange={event => setTickers(event.target.value)} placeholder="NVDA, MRVL, CEG" /><small>미국 티커를 쉼표로 구분하세요.</small></label></div><fieldset className="wv-stock-picker"><legend>국내 구성 종목</legend><p className="muted">동조 분석을 위해 3개 이상의 종목을 권장합니다.</p><div>{props.stocks.map(stock => <label key={stock.symbol}><input type="checkbox" checked={members.includes(stock.symbol)} onChange={event => setMembers(current => event.target.checked ? [...current, stock.symbol] : current.filter(symbol => symbol !== stock.symbol))} /><span>{stock.name}</span><small>{stock.symbol}</small></label>)}</div></fieldset>{error && <ErrorNotice message={error} />}<div className="wv-form-actions"><button className="secondary-button" type="button" onClick={() => setAdding(false)}>취소</button><button className="primary-button" type="submit" disabled={pending || !name.trim() || members.length === 0}><Plus size={16} />{pending ? '저장 중…' : '테마 추가'}</button></div></form>}<div className="wv-theme-grid">{props.themes.map(theme => <ThemeEditor key={`${theme.id}-${theme.name}-${theme.memberSymbols.join('-')}-${theme.usTickers.join('-')}`} theme={theme} stocks={props.stocks} onSaved={props.onRefresh} toast={props.toast} />)}</div><div className="wv-inline-note"><Info size={16} /><p>테마 점수에는 상승 종목 비율, 거래대금, 수급, 미국 선행 섹터가 반영됩니다. 연필 버튼에서 국내·미국 종목 매핑을 수정할 수 있어요.</p></div></div>;
}

const patternLabels: Record<string, string> = { MA5_BOUNCE: '5일선 눌림 반등', PIVOT_RECLAIM: '피벗 재돌파', BOX_RETEST: '박스 돌파 후 지지', BREAKOUT: '돌파 확인', SUPPORT_RECLAIM: '지지선 회복' };
function BacktestView({ onOpenStock, toast }: Props) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const fetchResult = async () => { setLoading(true); setError(''); try { setResult(await api<BacktestResult>('/backtest')); } catch (err) { setError(err instanceof Error ? err.message : '분석 결과를 불러오지 못했어요.'); } finally { setLoading(false); } };
  useEffect(() => { void fetchResult(); }, []);
  const run = async () => { setRunning(true); setError(''); try { setResult(await api<BacktestResult>('/backtest/run', { method: 'POST', body: JSON.stringify({}) })); toast('샘플 캔들 기반 검증을 완료했어요.'); } catch (err) { setError(err instanceof Error ? err.message : '분석 실행에 실패했어요.'); } finally { setRunning(false); } };
  const summary = result?.summary || result || {};
  const readMetric = (camel: string, snake: string) => Number(summary[camel] ?? summary[snake] ?? 0);
  const trades = result?.trades || [];
  const patterns = result?.patterns || [];
  const thresholdResults = [1, 2, 3, 5, -1, -2].map(threshold => {
    const hitCount = trades.filter(trade => Boolean((trade.thresholds as Record<string, boolean> | undefined)?.[String(threshold)])).length;
    return { threshold, hitCount, rate: trades.length ? hitCount / trades.length * 100 : 0 };
  });
  return <div className="workspace-view"><ViewHeading eyebrow="STRATEGY LAB" title="전략 검증" description="매매 패턴의 성과와 손실 구간을 데이터로 확인하세요." action={<button className="primary-button" onClick={run} disabled={running || loading}><FlaskConical size={17} />{running ? '검증 중…' : '샘플 검증 실행'}</button>} /><div className="wv-demo-note"><Info size={18} /><div><strong>샘플 데이터 기반 시뮬레이션</strong><p>예제 캔들과 고정된 규칙으로 계산합니다. 실제 시장의 과거 성과나 미래 수익률을 나타내지 않습니다.</p></div><span className="badge">DEMO</span></div>{error && <ErrorNotice message={error} retry={fetchResult} />}{loading ? <LoadingNotice /> : result && <><div className="wv-metrics-grid"><Metric label="검증 신호" value={`${readMetric('signalCount', 'signal_count')}건`} detail="샘플 캔들에서 감지" /><Metric label="승률" value={`${readMetric('winRate', 'win_rate').toFixed(1)}%`} detail="종료된 신호 기준" tone="wv-violet" /><Metric label="평균 수익률" value={pct(readMetric('averageReturn', 'average_return'))} detail="수수료·슬리피지 반영" tone={readMetric('averageReturn', 'average_return') >= 0 ? 'positive' : 'negative'} /><Metric label="손익 비율" value={summary.profitFactor == null ? '—' : readMetric('profitFactor', 'profit_factor').toFixed(2)} detail="총 이익 / 총 손실" /></div><div className="wv-backtest-columns"><section className="panel"><div className="panel-header"><div><h2>진입 패턴별 성과</h2><p className="muted">같은 기준으로 비교하는 샘플 결과</p></div><FlaskConical size={20} className="muted" /></div><div className="wv-pattern-list">{patterns.length ? patterns.map((item, index) => { const name = String(item.pattern ?? item.name ?? ''); const winRate = Number(item.winRate ?? item.win_rate ?? 0); return <div className="wv-pattern-row" key={name || index}><div><strong>{patternLabels[name] || name}</strong><span className="muted">{Number(item.count ?? item.signalCount ?? item.signal_count ?? 0)}개 신호</span></div><div className="wv-pattern-bar"><div style={{ width: `${Math.min(100, Math.max(0, winRate))}%` }} /></div><strong>{winRate.toFixed(1)}%</strong></div>; }) : <div className="empty-state"><p className="muted">감지된 패턴이 없어요.</p></div>}</div></section><section className="panel"><div className="panel-header"><div><h2>변동과 위험</h2><p className="muted">신호 발생 후 최대 가격 변동</p></div><ShieldCheck size={20} className="muted" /></div><div className="wv-risk-metrics"><div><span className="muted">평균 최대 상승폭 (MFE)</span><strong className="positive">{pct(readMetric('averageMfe', 'average_mfe'))}</strong></div><div><span className="muted">평균 최대 하락폭 (MAE)</span><strong className="negative">{pct(readMetric('averageMae', 'average_mae'))}</strong></div><div><span className="muted">최대 연속 손실</span><strong>{readMetric('maxLosingStreak', 'max_losing_streak')}회</strong></div></div><p className="wv-panel-footnote">다음 일봉 시가로 진입합니다. 한 봉에서 목표·손절을 모두 터치하면 손절로 계산합니다. 0건 패턴은 분봉 재생 데이터가 필요합니다.</p></section></div><div className="wv-backtest-columns"><section className="panel"><div className="panel-header"><div><h2>가격 변동 구간 도달률</h2><p className="muted">신호 발생 후 종료까지, 캔들 고가·저가 기준</p></div></div><div className="wv-threshold-grid">{thresholdResults.map(item => <div key={item.threshold}><span className={item.threshold > 0 ? 'positive' : 'negative'}>{item.threshold > 0 ? '+' : ''}{item.threshold}% 도달</span><strong>{item.rate.toFixed(1)}<small>%</small></strong><span className="muted">{item.hitCount} / {trades.length}개 신호</span></div>)}</div><p className="wv-panel-footnote">종료 봉 내 가격 순서는 알 수 없으므로 도달률이 실제 체결 수익률을 의미하지 않습니다.</p></section><section className="panel"><div className="panel-header"><div><h2>테마별 성과</h2><p className="muted">합성 데이터 내 테마별 승률</p></div></div><div className="wv-theme-performance">{result.themes?.length ? result.themes.map((item, index) => <div key={String(item.theme || index)}><span>{String(item.theme)}</span><small className="muted">{Number(item.signalCount || 0)}건</small><strong className="wv-violet">{Number(item.winRate || 0).toFixed(1)}%</strong></div>) : <p className="muted">테마별 검증 신호가 없어요.</p>}</div></section></div><section className="panel"><div className="panel-header"><div><h2>신호별 결과</h2><p className="muted">진입 이후 목표·손절 도달 여부와 최대 변동폭</p></div><span className="badge">{trades.length} SIGNALS</span></div><div className="table-wrap"><table className="data-table wv-table"><thead><tr><th>종목 / 패턴</th><th>진입가</th><th>종료가</th><th>수익률</th><th>MFE / MAE</th><th>결과</th></tr></thead><tbody>{trades.map((trade, index) => <tr key={`${trade.symbol}-${index}`}><td><button className="wv-stock-name" onClick={() => onOpenStock(trade.symbol)}>{trade.name || trade.symbol}<span>{patternLabels[String(trade.pattern)] || trade.pattern || '규칙 기반 신호'}</span></button></td><td>{money(Number(trade.entryPrice ?? trade.entry_price ?? 0))}원</td><td>{money(Number(trade.exitPrice ?? trade.exit_price ?? 0))}원</td><td className={Number(trade.resultPct ?? trade.result_pct ?? 0) >= 0 ? 'positive' : 'negative'}>{pct(Number(trade.resultPct ?? trade.result_pct ?? 0))}</td><td><span className="positive">{pct(Number(trade.mfe ?? trade.mfe_pct ?? 0))}</span> / <span className="negative">{pct(Number(trade.mae ?? trade.mae_pct ?? 0))}</span></td><td><span className={`wv-state ${trade.stopHit || trade.stop_hit ? 'wv-state-neutral' : ''}`}>{trade.stopHit || trade.stop_hit ? '손절 도달' : trade.targetHit || trade.target_hit ? '목표 도달' : '기간 종료'}</span></td></tr>)}</tbody></table>{!trades.length && <div className="empty-state"><p className="muted">검증 신호가 없어요. 샘플 검증을 실행해 보세요.</p></div>}</div></section></>}</div>;
}

function PaperView({ stocks, onOpenStock, toast, market }: Props) {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [symbol, setSymbol] = useState(stocks[0]?.symbol || '');
  const [quantity, setQuantity] = useState('1');
  const [tab, setTab] = useState<'positions' | 'history'>('positions');
  const selected = stocks.find(stock => stock.symbol === symbol);
  useEffect(() => { if ((!symbol || !stocks.some(stock => stock.symbol === symbol)) && stocks[0]) setSymbol(stocks[0].symbol); }, [stocks, symbol]);
  const fetchAccount = async () => { setLoading(true); setError(''); try { setAccount(await api<PaperAccount>('/paper')); } catch (err) { setError(err instanceof Error ? err.message : '모의 계좌를 불러오지 못했어요.'); } finally { setLoading(false); } };
  useEffect(() => { void fetchAccount(); }, []);
  const buy = async (event: React.FormEvent) => { event.preventDefault(); setPending('buy'); setError(''); try { const updated = await api<PaperAccount>('/paper/buy', { method: 'POST', body: JSON.stringify({ symbol, quantity: Number(quantity) }) }); if (updated.positions) setAccount(updated); else setAccount(await api<PaperAccount>('/paper')); toast('가상 매수를 기록했어요. 실제 주문은 전송하지 않습니다.'); } catch (err) { setError(err instanceof Error ? err.message : '가상 매수에 실패했어요.'); } finally { setPending(''); } };
  const sell = async (position: PaperPosition) => { setPending(String(position.id)); setError(''); try { const updated = await api<PaperAccount>(`/paper/${position.id}/sell`, { method: 'POST', body: JSON.stringify({}) }); if (updated.positions) setAccount(updated); else setAccount(await api<PaperAccount>('/paper')); toast(`${position.name} 가상 매도를 기록했어요.`); } catch (err) { setError(err instanceof Error ? err.message : '가상 매도에 실패했어요.'); } finally { setPending(''); } };
  const openPositions = (account?.positions || []).filter(position => !position.status || position.status.toUpperCase() === 'OPEN');
  const history = account?.history || (account?.positions || []).filter(position => position.status && position.status.toUpperCase() !== 'OPEN');
  const accountPnl = Number(account?.summary?.realizedPnl || 0) + Number(account?.summary?.unrealizedPnl || 0);
  const paperMode = account?.mode || market?.mode;
  const paperIsLive = paperMode === 'live';
  const paperIsDemo = paperMode === 'demo';
  return <div className="workspace-view"><ViewHeading eyebrow="PAPER TRADING" title="가상으로 먼저, 신중하게" description="실제 자금 없이 진입과 청산을 기록하며 전략을 검증하세요." action={<span className="wv-safe-pill"><ShieldCheck size={16} /> 실주문 비활성화</span>} /><div className="wv-demo-note"><Wallet size={19} /><div><strong>{paperIsLive ? '토스증권 시세로 기록하는 모의 계좌' : paperIsDemo ? '데모 시세로 운영되는 모의 계좌' : '시세 출처 확인 중인 모의 계좌'}</strong><p>매수·매도는 가상 기록이며 증권사로 주문을 전송하지 않습니다. 목표·손절 도달 여부를 기록해 경험을 쌓아보세요.</p></div><span className="badge">PAPER</span></div>{error && <ErrorNotice message={error} retry={fetchAccount} />}{loading ? <LoadingNotice /> : account && <><div className="wv-metrics-grid"><Metric label="보유 평가 금액" value={`${money(Number(account.summary?.invested || 0) + Number(account.summary?.unrealizedPnl || 0))}원`} detail="보유 가상 포지션의 현재 평가액" /><Metric label="가상 매수 금액" value={`${money(Number(account.summary?.invested || 0))}원`} detail="현재 보유 포지션의 매수 원금" /><Metric label="누적 가상 손익" value={`${accountPnl >= 0 ? '+' : ''}${money(accountPnl)}원`} detail="실현 손익 + 미실현 손익" tone={accountPnl >= 0 ? 'positive' : 'negative'} /><Metric label="보유 종목" value={`${openPositions.length}개`} detail={`종료된 포지션 ${history.length}개`} /></div><section className="panel"><div className="panel-header"><div><h2>가상 매수</h2><p className="muted">{paperIsDemo ? '샘플 현재가' : paperIsLive ? '수신된 현재가' : '표시된 가격'}에 매수 기록을 남깁니다.</p></div><Wallet size={21} className="muted" /></div><form className="wv-buy-form" onSubmit={buy}><label className="field">종목<select value={symbol} required onChange={event => setSymbol(event.target.value)}>{stocks.map(stock => <option key={stock.symbol} value={stock.symbol}>{stock.name} · {stock.symbol}</option>)}</select></label><label className="field">수량 (주)<input type="number" min="1" max="10000" step="1" value={quantity} required onChange={event => setQuantity(event.target.value)} /></label><div className="wv-estimated"><span className="muted">예상 가상 매수 금액</span><strong>{money((selected?.price || 0) * Math.max(0, Number(quantity)))}원</strong></div><button className="primary-button" type="submit" disabled={Boolean(pending) || !selected || !Number.isInteger(Number(quantity)) || Number(quantity) < 1}><Plus size={17} />{pending === 'buy' ? '기록 중…' : '가상 매수'}</button></form>{selected && <div className="wv-buy-levels"><span>현재가 <strong>{money(selected.price)}원</strong></span><span>손절 기준 <strong>{money(selected.risk.stop)}원</strong></span><span>1차 목표 <strong>{money(selected.targets[0])}원</strong></span><button onClick={() => onOpenStock(selected.symbol)}>추천 근거 확인 <ArrowUpRight size={14} /></button></div>}</section><section className="panel"><div className="panel-header"><div className="wv-tab-buttons"><button aria-pressed={tab === 'positions'} className={tab === 'positions' ? 'active' : ''} onClick={() => setTab('positions')}>보유 포지션 <span>{openPositions.length}</span></button><button aria-pressed={tab === 'history'} className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>거래 기록 <span>{history.length}</span></button></div><button className="wv-icon-button" aria-label="모의 계좌 새로고침" onClick={() => void fetchAccount()}><RefreshCw size={17} /></button></div>{tab === 'positions' ? openPositions.length ? <div className="table-wrap"><table className="data-table wv-table"><thead><tr><th>종목</th><th>수량</th><th>매수 단가</th><th>평가 가격</th><th>평가 손익</th><th>손절 / 목표</th><th>가상 청산</th></tr></thead><tbody>{openPositions.map(position => { const price = position.currentPrice ?? position.price ?? position.entryPrice; const pnl = position.pnl ?? (price - position.entryPrice) * position.quantity; return <tr key={position.id}><td><button className="wv-stock-name" onClick={() => onOpenStock(position.symbol)}>{position.name}<span>{position.symbol}</span></button></td><td>{money(position.quantity)}주</td><td>{money(position.entryPrice)}원</td><td>{money(price)}원</td><td className={pnl >= 0 ? 'positive' : 'negative'}>{pnl >= 0 ? '+' : ''}{money(pnl)}원<span className="wv-cell-sub">{pct(position.returnPct ?? (price / position.entryPrice - 1) * 100)}</span></td><td>{money(Number(position.stop || 0))} / {money(Number(position.target || 0))}</td><td><button className="secondary-button" onClick={() => void sell(position)} disabled={Boolean(pending)}>{pending === String(position.id) ? '기록 중…' : '전량 가상 매도'}<ArrowRight size={14} /></button></td></tr>; })}</tbody></table></div> : <div className="empty-state wv-empty"><div className="wv-empty-icon"><Wallet size={26} /></div><h3>첫 가상 매수를 시작해 보세요</h3><p className="muted">추천 근거와 손절 기준을 확인한 후 위에서 매수를 기록하세요.</p></div> : history.length ? <div className="table-wrap"><table className="data-table wv-table"><thead><tr><th>종목</th><th>수량</th><th>매수 단가</th><th>매도 단가</th><th>실현 손익</th><th>종료 시각</th></tr></thead><tbody>{history.map(position => { const pnl = position.pnl ?? (Number(position.exitPrice || position.entryPrice) - position.entryPrice) * position.quantity; return <tr key={position.id}><td><button className="wv-stock-name" onClick={() => onOpenStock(position.symbol)}>{position.name}<span>{position.symbol}</span></button></td><td>{money(position.quantity)}주</td><td>{money(position.entryPrice)}원</td><td>{money(Number(position.exitPrice || position.currentPrice || 0))}원</td><td className={pnl >= 0 ? 'positive' : 'negative'}>{pnl >= 0 ? '+' : ''}{money(pnl)}원</td><td className="muted">{position.closedAt ? new Date(position.closedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}</td></tr>; })}</tbody></table></div> : <div className="empty-state wv-empty"><h3>종료된 거래가 없어요</h3><p className="muted">가상 매도 후 거래 결과를 이곳에서 확인할 수 있어요.</p></div>}</section></>}</div>;
}

type StrategyField = { key: string; label: string; unit: string; step: string; min: number; max: number };
const numericField = (key: string, label: string, unit: string, min: number, max: number, step = '1'): StrategyField => ({ key, label, unit, min, max, step });
const strategyFields: { key: string; title: string; description: string; fields: StrategyField[] }[] = [
  { key: 'screen', title: '후보 종목 필터', description: '충분한 거래대금이 있는 종목을 우선 분석합니다.', fields: [numericField('min_traded_value_krw', '최소 거래대금', '원', 100000000, 10000000000000, '100000000'), numericField('candidate_limit', '최대 후보 종목 수', '개', 1, 500), numericField('min_recommend_score', '최소 추천 점수', '점', 0, 100)] },
  { key: 'significant_candle', title: '기준봉 탐지', description: '거래량이 동반된 의미 있는 상승 일봉을 찾습니다.', fields: [numericField('min_gain_pct', '최소 당일 상승률', '%', .1, 30, '.1'), numericField('min_volume_ratio_20d', '20일 평균 대비 거래량', '배', .1, 20, '.1'), numericField('close_position_min', '봉 내 최소 종가 위치', '0 ~ 1', 0, 1, '.05'), numericField('lookback_days', '기준봉 탐색 기간', '일', 1, 30), numericField('holding_volume_ratio', '횡보 최대 거래량 비율', '0 ~ 1', .01, 1, '.01'), numericField('holding_price_ratio', '횡보 최소 가격 유지 비율', '0 ~ 1', .01, 1, '.01')] },
  { key: 'moving_average', title: '이동평균선', description: '중기 기간은 단기 기간보다 길게 설정하세요.', fields: [numericField('short', '단기 이동평균', '일', 2, 20), numericField('medium', '중기 이동평균', '일', 5, 60)] },
  { key: 'box', title: '박스권 탐지', description: '반복적으로 지지와 저항을 받은 가격대를 찾습니다.', fields: [numericField('lookback_days', '박스 탐색 기간', '일', 5, 30), numericField('touch_tolerance_pct', '지지·저항 터치 오차', '%', .1, 5, '.1'), numericField('min_touches', '최소 반복 터치', '회', 2, 10)] },
  { key: 'entry', title: '진입 확인', description: '지지선 접근과 피벗 회복을 확인한 후 후보를 표시합니다.', fields: [numericField('ma5_distance_pct', '단기 이동평균 접근 범위', '%', .1, 10, '.1'), numericField('pivot_reclaim_confirm_pct', '피벗 재돌파 확인 폭', '%', 0, 3, '.05'), numericField('confirmation_minutes', '지지 확인 시간', '분', 1, 30), numericField('volume_acceleration', '최소 거래량 가속', '배', .1, 20, '.1'), numericField('zone_width_pct', '진입 구간 폭', '%', .1, 5, '.1'), numericField('support_tolerance_pct', '지지선 겹침 허용 범위', '%', .1, 5, '.1')] },
  { key: 'risk', title: '손실과 손익비', description: '추천 후보의 위험과 기대 수익을 함께 제한합니다.', fields: [numericField('max_stop_loss_pct', '최대 허용 손절 폭', '%', .1, 20, '.1'), numericField('min_risk_reward', '최소 기대 손익비', '배', 1.5, 10, '.1'), numericField('stop_buffer_pct', '기술적 손절 여유 폭', '%', .1, 5, '.1'), numericField('target_r_multiple', '목표 수익 대비 위험 배수', '배', 1.5, 10, '.1')] },
  { key: 'theme', title: '테마 동조 기준', description: '개별 급등보다 같은 테마의 동반 상승을 확인합니다.', fields: [numericField('min_constituents', '최소 구성 종목', '개', 3, 50), numericField('min_breadth_pct', '최소 상승 종목 비율', '%', 0, 100), numericField('min_strength', '최소 테마 강도', '점', 0, 100)] },
  { key: 'weights', title: '추천 점수 가중치', description: '여섯 항목의 가중치 합계를 100점으로 설정하세요.', fields: [numericField('theme_strength', '테마 강도', '점', 0, 100), numericField('money_flow', '자금 흐름', '점', 0, 100), numericField('chart_structure', '차트 구조', '점', 0, 100), numericField('support_confluence', '지지선 겹침', '점', 0, 100), numericField('risk_reward', '손익비', '점', 0, 100), numericField('realtime_confirmation', '실시간 확인', '점', 0, 100)] },
  { key: 'backtest', title: '샘플 검증 조건', description: '실행 비용과 신호 이후 관찰 기간을 반영합니다.', fields: [numericField('forward_bars', '최대 관찰 기간', '봉', 1, 30), numericField('fee_pct', '편도 수수료', '%', 0, 2, '.001'), numericField('slippage_pct', '편도 체결 오차', '%', 0, 2, '.01')] },
];

function SettingsView({ onRefresh, toast, market }: Props) {
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [saved, setSaved] = useState<Strategy | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const dirty = JSON.stringify(strategy) !== JSON.stringify(saved);
  const fetchSettings = async () => {
    setLoading(true); setError('');
    const [settingsResult, connectionResult] = await Promise.allSettled([api<Strategy>('/strategy'), api<ConnectionStatus>('/connection')]);
    if (settingsResult.status === 'fulfilled') { setStrategy(settingsResult.value); setSaved(settingsResult.value); }
    else { setError(settingsResult.reason instanceof Error ? settingsResult.reason.message : '설정을 불러오지 못했어요.'); }
    if (connectionResult.status === 'fulfilled') setConnection(connectionResult.value);
    else setConnection(null);
    setLoading(false);
  };
  useEffect(() => { void fetchSettings(); }, []);
  const refreshConnection = async () => {
    try { setConnection(await api<ConnectionStatus>('/connection/refresh', { method: 'POST', body: JSON.stringify({}) })); onRefresh(); toast('시세 연결 상태를 확인했어요.'); }
    catch { setError('시세 연결 상태를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.'); }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); if (!strategy) return;
    if (Object.values(strategy).some(group => Object.values(group).some(value => !Number.isFinite(value)))) { setError('모든 설정에 올바른 숫자를 입력해 주세요.'); return; }
    if (strategy.moving_average.medium <= strategy.moving_average.short) { setError('중기 이동평균 기간은 단기 기간보다 길어야 합니다.'); return; }
    if (Math.abs(Object.values(strategy.weights).reduce((sum, value) => sum + value, 0) - 100) > .001) { setError('추천 점수 가중치의 합계는 100점이어야 합니다.'); return; }
    setPending(true); setError('');
    try { const updated = await api<Strategy>('/strategy', { method: 'PUT', body: JSON.stringify(strategy) }); setStrategy(updated); setSaved(updated); onRefresh(); toast('전략 설정을 저장하고 추천을 다시 계산했어요.'); }
    catch (err) { setError(err instanceof Error ? err.message : '설정 저장에 실패했어요.'); }
    finally { setPending(false); }
  };
  const credentialsPresent = connection && (connection.credentials.TOSS_ACCESS_TOKEN || (connection.credentials.TOSS_CLIENT_ID && connection.credentials.TOSS_CLIENT_SECRET));
  const activeMode = market?.mode || connection?.mode;
  const isLive = activeMode === 'live';
  const isDemo = activeMode === 'demo';
  const authPassed = ['passed', 'ready'].includes(connection?.authStatus || '');
  const authFailed = ['failed', 'blocked'].includes(connection?.authStatus || '');
  const sampleVerified = ['verified', 'ready'].includes(connection?.sampleStatus || '');
  const connectionMessages = [connection?.dataQuality?.reason, ...(connection?.blockers || []), connection?.verification].filter((value): value is string => typeof value === 'string' && Boolean(value));
  const ipBlockedReason = connectionMessages.find(message => /IP.*(?:not allowed|허용|allowlist|미등록)|(?:not allowed|미등록).*IP/i.test(message));
  const ipBlocked = connection?.condition === 'ip_not_allowed' || Boolean(ipBlockedReason);
  const connectionReason = ipBlockedReason || (ipBlocked ? '토스증권이 현재 호출 IP를 허용하지 않아 OAuth 인증을 거부했습니다.' : connectionMessages[0]) || '실제 시세 응답과 KRX·NXT 통합 가격 기준을 확인해야 합니다.';
  return <div className="workspace-view"><ViewHeading eyebrow="RADAR PREFERENCES" title="나에게 맞는 분석 기준" description="진입 조건과 위험 한도를 조정해 나만의 전략을 만들어 보세요." /><section className="panel wv-connection"><div className="panel-header"><div><h2>시세 연결 상태</h2><p className="muted">연동 상태와 실제 주문 사용 여부</p></div><div className="wv-form-actions"><span className="badge">{isLive ? 'LIVE DATA' : isDemo ? 'DEMO MODE' : 'CONNECTION STATUS'}</span><button className="wv-icon-button" type="button" aria-label="시세 연결 상태 새로고침" onClick={() => void refreshConnection()}><RefreshCw size={15} /></button></div></div><div className="wv-connection-grid"><div><div className="wv-connection-icon"><FlaskConical size={20} /></div><div><strong>{isLive ? '토스증권 시세 사용 중' : isDemo ? '데모 시세 사용 중' : '시세 출처 확인 중'}</strong><p className="muted">{isLive ? market?.venueLabel || '검증된 시세 데이터로 분석합니다.' : isDemo ? '샘플 데이터로 분석과 기능을 체험합니다.' : '서버에서 데이터 상태를 확인합니다.'}</p></div><CheckCircle2 size={19} className="wv-violet" /></div><div><div className="wv-connection-icon"><Link2 size={20} /></div><div><strong>{connection ? isLive ? '토스증권 시세 연결됨' : authPassed ? '토스증권 인증 확인 완료' : authFailed ? ipBlocked ? '서버 IP 등록 필요' : '토스증권 인증 요청 차단' : credentialsPresent ? '토스증권 인증 설정됨' : '토스증권 API 연결 대기' : '시세 연결 상태 확인 필요'}</strong><p className="muted">{connection ? isLive ? '서버 인증과 통합 시세를 확인했습니다.' : authFailed ? '서버 인증 설정이 있으나 요청이 거절되었습니다.' : credentialsPresent ? authPassed ? '인증 확인됨 · 통합 시세 데이터 확인 대기' : '인증 설정됨 · 실제 연결 확인 대기' : '서버 인증 설정과 통합 시세 검증이 필요합니다.' : '연결 상태를 불러오지 못했어요. 새로고침해 주세요.'}</p></div>{connection?.ready && isLive ? <CheckCircle2 size={19} className="wv-violet" /> : <span className="wv-pending-dot" />}</div><div><div className="wv-connection-icon"><LockKeyhole size={20} /></div><div><strong>실제 주문 비활성화</strong><p className="muted">모든 매매 기록은 가상 계좌에서만 처리합니다.</p></div><ShieldCheck size={19} className="wv-violet" /></div></div>{!isLive && connection && <div className="wv-connection-detail"><strong>{ipBlocked ? '토스증권 허용 IP 등록이 필요해요' : '실시간 연결 전 확인할 사항'}</strong><p>{connectionReason}{connection.httpStatus ? ` (HTTP ${connection.httpStatus}${connection.errorCode ? ` · ${connection.errorCode}` : ''})` : ''}</p>{ipBlocked && <p>토스증권 Open API 설정에서 이 서버의 외부 IP를 허용한 뒤 연결 상태를 다시 확인하세요.</p>}<p>현재 인증 {authPassed ? '확인 완료' : authFailed ? '요청 차단' : credentialsPresent ? '설정됨 · 확인 대기' : '설정 필요'} · 시세 응답 {sampleVerified ? '확인 완료' : connection.sampleStatus === 'blocked' ? '요청 차단' : '확인 대기'} · 통합 시세 {connection.venue_verified ? '검증 완료' : '검증 대기'}</p></div>}</section>{error && <ErrorNotice message={error} retry={dirty ? undefined : fetchSettings} />}{loading ? <LoadingNotice /> : strategy && <form onSubmit={save}><div className="wv-settings-header"><div><h2>추천 전략 설정</h2><p className="muted">변경 사항을 저장하면 추천 분석에 반영됩니다.</p></div><div className="wv-form-actions"><button className="secondary-button" type="button" disabled={!dirty || pending} onClick={() => { setStrategy(saved); setError(''); }}>변경 취소</button><button className="primary-button" type="submit" disabled={!dirty || pending}><Check size={16} />{pending ? '저장 중…' : '설정 저장'}</button></div></div><div className="wv-settings-grid">{strategyFields.map(group => <section className="panel wv-settings-group" key={group.key}><div className="wv-settings-group-header"><h3>{group.title}</h3><p className="muted">{group.description}</p></div><div className="form-grid">{group.fields.map(field => <label className="field" key={field.key}>{field.label}<div className="wv-unit-input"><input type="number" required min={field.min} max={field.max} step={field.step} value={Number.isNaN(strategy[group.key]?.[field.key]) ? '' : strategy[group.key]?.[field.key] ?? ''} onChange={event => setStrategy(current => current ? ({ ...current, [group.key]: { ...current[group.key], [field.key]: event.target.value === '' ? Number.NaN : Number(event.target.value) } }) : current)} /><span>{field.unit}</span></div></label>)}</div></section>)}</div><div className="wv-settings-bottom"><span className="muted">{dirty ? '아직 저장하지 않은 변경 사항이 있어요.' : '설정은 서버에 저장됩니다.'}</span><button className="primary-button" type="submit" disabled={!dirty || pending}><Check size={16} />{pending ? '저장 중…' : '설정 저장'}</button></div></form>}<div className="wv-inline-note"><Info size={16} /><p>API 인증 정보는 서버 환경변수로만 관리합니다. 추천 숫자는 전략 엔진에서 계산하며, 설정 변경이 수익을 보장하지 않습니다.</p></div></div>;
}

export function WorkspaceViews(props: Props) {
  if (props.view === 'watchlist') return <WatchlistView {...props} />;
  if (props.view === 'themes') return <ThemesView {...props} />;
  if (props.view === 'backtest') return <BacktestView {...props} />;
  if (props.view === 'paper') return <PaperView {...props} />;
  if (props.view === 'settings') return <SettingsView {...props} />;
  return null;
}
