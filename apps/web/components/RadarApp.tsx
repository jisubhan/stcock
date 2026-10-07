'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownLeft, ArrowRight, ArrowUpRight, Bell, BookOpen, CalendarDays, Check, CheckCheck, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3, Command, ExternalLink, FlaskConical, Globe2, LayoutDashboard, Menu, Plus, Radio, RefreshCw, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Star, Target, TrendingUp, Wallet, X, Zap } from 'lucide-react';
import { api, compactMoney, money, pct, stateLabel } from '@/lib/api';
import type { Market, NotificationItem, Stock, Theme, View } from '@/lib/types';
import PriceChart from './PriceChart';
import MiniSparkline from './MiniSparkline';
import { WorkspaceViews } from './WorkspaceViews';
import './provenance.css';

const navigation: { view: View; label: string; icon: typeof Activity }[] = [
  { view: 'dashboard', label: '대시보드', icon: LayoutDashboard },
  { view: 'themes', label: '테마 탐색', icon: Globe2 },
  { view: 'watchlist', label: '관심종목', icon: Star },
  { view: 'backtest', label: '전략 백테스트', icon: FlaskConical },
  { view: 'paper', label: '모의매매', icon: Wallet },
];
const themeIcons = [Radio, Zap, Activity, TrendingUp, Globe2];
const viewNames: Record<View, string> = { dashboard: '대시보드', themes: '테마 탐색', watchlist: '관심종목', backtest: '전략 백테스트', paper: '모의매매', settings: '전략 설정' };
const sortOptions = ['추천 점수순', '거래대금순', '상승률순'];

export default function RadarApp({ initialSymbol }: { initialSymbol?: string }) {
  const [view, setView] = useState<View>('dashboard');
  const [market, setMarket] = useState<Market | null>(null);
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [selected, setSelected] = useState<Stock | null>(null);
  const [detail, setDetail] = useState<Stock | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailSymbol, setDetailSymbol] = useState<string | null>(initialSymbol || null);
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [watchHydrated, setWatchHydrated] = useState(false);
  const [search, setSearch] = useState('');
  const [searchFocus, setSearchFocus] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [allCandidates, setAllCandidates] = useState(false);
  const [sort, setSort] = useState(0);
  const [activeTheme, setActiveTheme] = useState<string | null>(null);
  const [interval, setIntervalValue] = useState<'1d' | '5m' | '1m'>('1d');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toastText, setToastText] = useState('');
  const [guideOpen, setGuideOpen] = useState(false);
  const [usOpen, setUsOpen] = useState(false);
  const [browserAlerts, setBrowserAlerts] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const unread = notifications.filter(n => !n.read).length;

  const toast = useCallback((message: string) => setToastText(message), []);
  useEffect(() => { if (toastText) { const t = setTimeout(() => setToastText(''), 3500); return () => clearTimeout(t); } }, [toastText]);
  const refresh = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const [m, s, t, n] = await Promise.all([api<Market>('/market'), api<{ items: Stock[] }>('/stocks'), api<{items:Theme[]}>('/themes'), api<{items:NotificationItem[]}>('/notifications')]);
      setMarket(m); setStocks(s.items); setThemes(t.items); setNotifications(n.items); setError('');
      if (manual) toast(m.mode === 'demo' ? '샘플 시장 데이터를 새로 불러왔습니다.' : m.mode === 'live' ? '토스증권 시장 데이터를 새로 불러왔습니다.' : '데이터 상태를 새로 확인했습니다.');
    } catch (e) { setError(e instanceof Error ? e.message : '데이터를 불러올 수 없습니다.'); }
    finally { setLoading(false); setRefreshing(false); }
  }, [toast]);
  useEffect(() => { refresh(); const t = setInterval(() => refresh(), 30000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => {
    try { const saved = localStorage.getItem('flowtrade-watchlist'); setWatchlist(saved ? JSON.parse(saved) : ['010170', '000660', '034020']); } catch { setWatchlist([]); }
    setWatchHydrated(true);
    const queryView = new URLSearchParams(window.location.search).get('view') as View;
    if (queryView && queryView in viewNames) setView(queryView);
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); searchRef.current?.focus(); } if (e.key === 'Escape') { const closeButton = document.querySelector<HTMLButtonElement>('[aria-label="종목 상세 닫기"]'); if (closeButton) closeButton.click(); else setDetailSymbol(null); setGuideOpen(false); setAlertsOpen(false); setMobileOpen(false); setSearchFocus(false); } };
    const onPop = () => { const s = window.location.pathname.match(/\/stocks\/([^/]+)/)?.[1]; setDetailSymbol(s || null); const v = new URLSearchParams(window.location.search).get('view') as View; setView(v && v in viewNames ? v : 'dashboard'); };
    window.addEventListener('keydown', onKey); window.addEventListener('popstate', onPop);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('popstate', onPop); };
  }, []);
  useEffect(() => { if (watchHydrated) localStorage.setItem('flowtrade-watchlist', JSON.stringify(watchlist)); }, [watchlist, watchHydrated]);
  useEffect(() => {
    if (!detailSymbol && !guideOpen) return;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = 'hidden';
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    dialog?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialog) return;
      const elements = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(el => el.offsetParent !== null);
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', trap);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', trap); previousFocus?.focus({ preventScroll: true }); };
  }, [detailSymbol, guideOpen]);

  const toggleWatch = (symbol: string) => { setWatchlist(previous => previous.includes(symbol) ? previous.filter(s => s !== symbol) : [...previous, symbol]); };
  useEffect(() => {
    const symbol = detailSymbol || selected?.symbol || stocks[0]?.symbol;
    if (!symbol) return;
    let cancelled = false;
    api<Stock>(`/stocks/${symbol}`).then(s => { if (!cancelled) { if (detailSymbol) { setDetail(s); setDetailError(''); } else setSelected(s); } }).catch(e => { if (!cancelled) { if (detailSymbol) setDetailError(e.message); else toast(e.message); } });
    return () => { cancelled = true; };
  }, [detailSymbol, selected?.symbol, stocks, toast]);
  const navigate = (next: View) => { setView(next); setDetailSymbol(null); setMobileOpen(false); window.history.pushState({}, '', next === 'dashboard' ? '/' : `/?view=${next}`); };
  const openStock = (symbol: string) => { setDetail(null); setDetailError(''); setDetailSymbol(symbol); setSearch(''); setSearchFocus(false); setAlertsOpen(false); window.history.pushState({}, '', `/stocks/${symbol}`); };
  const closeStock = () => { setDetailSymbol(null); window.history.pushState({}, '', view === 'dashboard' ? '/' : `/?view=${view}`); };
  const markRead = async () => { try { await api('/notifications/read', {method:'POST',body:JSON.stringify({ids:notifications.filter(n=>!n.read).map(n=>n.id)})}); setNotifications(previous=>previous.map(n=>({...n,read:true}))); toast('모든 알림을 읽음으로 표시했습니다.'); } catch(e) { toast((e as Error).message); } };
  const enableBrowser = async () => {
    if (!('Notification' in window)) return toast('이 브라우저에서는 시스템 알림을 지원하지 않습니다.');
    const permission = await Notification.requestPermission();
    setBrowserAlerts(permission === 'granted');
    toast(permission === 'granted' ? market?.mode === 'live' ? '브라우저 알림을 켰습니다.' : '브라우저 알림을 켰습니다. 체험 모드 알림입니다.' : '알림 권한이 허용되지 않았습니다. 앱 안에서 확인할 수 있습니다.');
    if (permission === 'granted') new Notification('FlowTrade Radar', {body:market?.mode === 'live' ? '토스증권 시세 기반 신호 알림이 활성화되었습니다. 실제 주문은 비활성화되어 있습니다.' : '체험 모드 알림이 활성화되었습니다. 실제 시세 알림은 API 연결 후 제공됩니다.', icon:'/favicon.svg'});
  };
  const visibleStocks = [...stocks].filter(s => (allCandidates || s.eligible) && (!activeTheme || s.themeId === activeTheme)).sort((a,b)=>sort===1 ? b.tradedValue-a.tradedValue : sort===2 ? b.changePct-a.changePct : b.score-a.score);
  const recommended = visibleStocks.slice(0, allCandidates ? 6 : 3);
  const searchResults = stocks.filter(s => `${s.name} ${s.symbol} ${s.theme}`.toLowerCase().includes(search.toLowerCase())).slice(0,6);
  const isLive = market?.mode === 'live';
  const isDemo = market?.mode === 'demo';
  const modeLabel = isLive ? '토스증권 실시간 시세' : isDemo ? '샘플 데이터 체험 모드' : '데이터 상태 확인 중';
  const qualityValues = Object.values(market?.dataStatus || {});
  const stale = market?.stale === true || market?.dataStatus?.stale === true || qualityValues.includes('stale');
  const degraded = market?.degraded === true || market?.dataStatus?.degraded === true || qualityValues.includes('degraded');
  const snapshotTime = formatTime(market?.asOf || '');
  const snapshotDate = formatSnapshotDate(market?.asOf || '');
  const venueLabel = market?.venueLabel || '가격 출처 확인 중';


  return <div className="app-shell">
    {mobileOpen && <div className="sidebar-backdrop" onClick={()=>setMobileOpen(false)}/>}
    <aside className={`sidebar ${mobileOpen?'is-open':''}`}>
      <button className="brand" onClick={()=>navigate('dashboard')} aria-label="FlowTrade Radar 홈"><span className="brand-mark"><TrendingUp size={24} strokeWidth={2.4}/></span><span>FlowTrade<span className="brand-sub">RADAR</span></span></button>
      <div className="workspace-pill"><span className="workspace-icon">F</span><div>나의 트레이딩 워크스페이스<small>개인 워크스페이스</small></div><ChevronDown size={14}/></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="주 메뉴">{navigation.map(item=><button key={item.view} className={`nav-item ${view===item.view?'active':''}`} onClick={()=>navigate(item.view)}><item.icon size={19}/><span>{item.label}</span>{item.view==='watchlist' && <span className="nav-count">{watchlist.length}</span>}{item.view==='dashboard' && <span className="nav-active-dot"/>}</button>)}</nav>
      <div className="nav-divider"/><div className="nav-label">PREFERENCES</div>
      <button className={`nav-item ${view==='settings'?'active':''}`} onClick={()=>navigate('settings')}><Settings2 size={19}/><span>전략 설정</span></button>
      <button className="nav-item" onClick={()=>setGuideOpen(true)}><CircleHelp size={19}/><span>이용 가이드</span><ExternalLink size={13} className="nav-end"/></button>
      <div className="sidebar-grow"/>
      <div className="sidebar-note"><div className="note-orbit"><Activity size={23}/></div><span className="eyebrow">BETTER TIMING, BETTER TRADE</span><h3>좋은 종목을<br/>좋은 가격에.</h3><p>시장의 흐름에서<br/>나만의 기회를 발견하세요.</p><button onClick={()=>setGuideOpen(true)}>Radar 활용하기 <ArrowUpRight size={14}/></button></div>
      <div className="sidebar-bottom"><span className="status-dot"/> {isLive ? '토스증권 시세 연결' : isDemo ? '샘플 데이터 체험 중' : '데이터 연결 확인 중'} <span className="version">v0.1</span></div>
    </aside>
    <div className="main-shell">
      <header className="topbar">
        <div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="메뉴 열기" onClick={()=>setMobileOpen(true)}><Menu size={20}/></button><span>워크스페이스</span><ChevronRight size={13}/><strong>{viewNames[view]}</strong></div>
        <div className="header-actions">
          <div className="global-search"><Search size={16}/><input ref={searchRef} aria-label="종목 검색" placeholder="종목명 또는 코드 검색" value={search} onChange={e=>setSearch(e.target.value)} onFocus={()=>setSearchFocus(true)} onBlur={()=>setTimeout(()=>setSearchFocus(false),150)}/><kbd><Command size={10}/> K</kbd>
            {searchFocus && search && <div className="search-results">{searchResults.length ? searchResults.map(s=><button key={s.symbol} onClick={()=>openStock(s.symbol)}><span className={`stock-avatar color-${s.rank%5}`}>{s.name.slice(0,1)}</span><span><strong>{s.name}</strong><small>{s.symbol} · {s.theme}</small></span><span className={s.changePct>=0?'positive':'negative'}>{pct(s.changePct)}</span></button>) : <div className="empty-search">검색 결과가 없습니다.</div>}</div>}
          </div>
          <span className={`demo-pill ${isLive ? 'live-pill' : ''}`}><span/> {isLive ? 'LIVE' : isDemo ? 'DEMO' : 'CONNECTING'}</span>
          <div className="notifications-anchor"><button className={`icon-button ${alertsOpen?'selected':''}`} onClick={()=>setAlertsOpen(!alertsOpen)} aria-label={`알림 ${unread}개`}><Bell size={19}/>{unread>0 && <i className="bell-dot"/>}</button>{alertsOpen && <div className="notifications-popover"><div className="panel-header"><strong>알림 센터 <span className="number-badge">{unread}</span></strong><button className="text-button" onClick={markRead}>모두 읽음 <CheckCheck size={13}/></button></div><p className="popover-caption">{isLive ? '토스증권 시세 기반 신호 알림' : isDemo ? '샘플 시그널 알림 · 실제 시장 알림 아님' : '신호 데이터 출처 확인 중'}</p>{notifications.length ? notifications.slice(0,6).map(n=><button className={`notification-item ${n.read?'read':''}`} key={n.id} onClick={()=>openStock(n.symbol)}><span className="notification-icon"><Zap size={16}/></span><span><strong>{n.title || n.name}</strong><p>{n.message}</p><small>{formatTime(n.timestamp)}</small></span>{!n.read&&<i/>}</button>) : <p className="empty-state">새로운 알림이 없습니다.</p>}<button className="popover-footer" onClick={enableBrowser}><Bell size={14}/>{browserAlerts?'브라우저 알림 활성화됨':'브라우저 알림 켜기'}</button></div>}</div>
          <div className="topbar-divider"/><button className="profile-avatar" title="나의 워크스페이스 설정" aria-label="워크스페이스 설정" onClick={()=>navigate('settings')}>JS</button>
        </div>
      </header>
      <main className="main-content">{(stale || degraded) && <div className="data-quality-banner" role="status"><Clock3 size={16}/><div><strong>{stale ? '시세 갱신이 지연되고 있어요' : '일부 시장 데이터를 가져오지 못했어요'}</strong><span>{market?.staleReason || market?.notice || `마지막 확인 ${snapshotTime} KST · 일부 분석이 제한될 수 있습니다.`}</span></div><button className="text-button" onClick={()=>refresh(true)}>다시 확인 <RefreshCw size={13}/></button></div>}
        {view==='dashboard' ? <>
          <section className="page-heading"><div><div className="eyebrow">YOUR EDGE IN THE MARKET <span className="tiny-line"/></div><h1>시장의 흐름을, <span>기회로.</span></h1><p>오늘의 주도 테마와 수급을 따라, 좋은 종목을 좋은 가격에.</p></div><div className="heading-right"><span className="date-label"><CalendarDays size={15}/>{snapshotDate.date} <span>{snapshotDate.weekday}</span></span><button className="secondary-button refresh-button" onClick={()=>refresh(true)} disabled={refreshing}><RefreshCw size={14} className={refreshing?'spin':''}/>새로고침</button></div></section>
          <div className="demo-caption"><span className="status-dot"/> {isLive ? '토스증권 시세' : isDemo ? '체험 모드' : '연결 확인 중'} <span>·</span> {market?.sessionLabel || market?.session || '세션 확인 중'} <span>·</span> {snapshotTime} KST {isDemo ? '스냅샷' : '기준'} <span>·</span> {isDemo ? '실제 시세 아님' : venueLabel} <button onClick={()=>navigate('settings')}>데이터 연결 안내 <ArrowRight size={12}/></button></div>
          {error && <div className="error-banner" role="alert">데이터를 불러오지 못했습니다. API 서버 상태를 확인해주세요. <span>{error}</span><button onClick={()=>refresh(true)}>다시 시도</button></div>}
          {loading ? <div className="loading-grid">{[1,2,3,4].map(n=><div className="skeleton metric-card" key={n}/>)}</div> : <>
            <section className="market-grid" aria-label="시장 지표">{!market?.indices.length && <div className="metric-card market-data-empty"><Activity size={23}/><div><h3>지수 데이터를 아직 수신하지 못했어요</h3><p>현재 연결에서 제공되지 않은 지표는 표시하지 않습니다.</p></div></div>}{market?.indices.slice(0,4).map((index,i)=><div className="metric-card" key={index.name}><div className="metric-heading"><span className="metric-icon">{{KOSPI:'KR',KOSDAQ:'KQ',NASDAQ:'US','USD/KRW':'FX'}[index.name] || 'IDX'}</span><span>{index.name}</span><span className="metric-market">{index.name === 'USD/KRW' ? '환율' : ['KOSPI','KOSDAQ'].includes(index.name) ? '국내' : index.name === 'NASDAQ' ? '미국' : '지표'}</span></div><div className="metric-body"><div><strong>{index.value.toLocaleString('ko-KR',{minimumFractionDigits:2,maximumFractionDigits:2})}</strong><span className={index.changePct == null ? 'muted' : index.changePct>=0?'positive':'negative'}>{index.changePct != null && (index.changePct>=0?<ArrowUpRight size={13}/>:<ArrowDownLeft size={13}/>)} {index.changePct == null ? '데이터 없음' : pct(index.changePct)} <small>전일 대비</small></span></div><MiniSparkline values={index.sparkline} color={index.changePct != null && index.changePct>=0?'#e97179':'#3e93a3'} height={45} width={104}/></div></div>)}</section>
            <div className="market-context"><span className="context-icon"><Activity size={16}/></span><strong>오늘의 시장 흐름</strong><span className="context-copy">{market?.summary || '시장 요약 데이터를 확인하고 있어요.'}</span><span className="risk-badge"><ShieldCheck size={12}/>위험도 {market?.risk.label || '확인 필요'}</span><button onClick={()=>setUsOpen(!usOpen)}>미국장 요약 <ChevronRight size={14}/></button></div>
            {usOpen && <div className="us-summary panel"><div className="panel-header"><strong><Globe2 size={16}/> 미국장 선행 테마{isDemo ? ' · 샘플' : ''}</strong><button className="icon-button" aria-label="미국장 요약 닫기" onClick={()=>setUsOpen(false)}><X size={16}/></button></div><div className="us-grid">{!market?.usMarkets.length && <p className="muted">미국장 선행 종목 데이터가 아직 수신되지 않았어요.</p>}{market?.usMarkets.map(u=><div key={u.symbol}><span>{u.name} <small>{u.symbol}</small></span><strong className={u.changePct == null ? 'muted' : u.changePct>=0?'positive':'negative'}>{u.changePct == null ? '데이터 없음' : pct(u.changePct)}</strong></div>)}</div><p className="muted">미국장 강세만으로 추천하지 않습니다. 국내 테마의 3개 이상 종목 동조를 함께 확인합니다.</p></div>}
            <section className="theme-section"><div className="section-heading"><div><h2>오늘의 주도 테마 <span className="count-label">TOP 5</span></h2><p>돈이 모이는 곳에서 기회를 찾아보세요.</p></div><button className="text-button" onClick={()=>navigate('themes')}>전체 테마 탐색 <ArrowRight size={14}/></button></div><div className="theme-grid">{themes.slice(0,5).map((theme,i)=>{ const Icon=themeIcons[i%5]; return <button key={theme.id} className={`theme-card ${i===0?'theme-leader':''} ${activeTheme===theme.id?'theme-selected':''}`} onClick={()=>setActiveTheme(activeTheme===theme.id?null:theme.id)} aria-pressed={activeTheme===theme.id}><div className="theme-card-top"><span className={`theme-symbol theme-color-${i}`}><Icon size={18}/></span><span className="theme-rank">{i===0?<><span/> LEADING</>:`0${i+1}`}</span></div><div className="theme-name">{theme.name}</div><div className="theme-score"><strong>{Math.round(theme.score)}</strong><span>테마 강도</span><span className="theme-change">{pct(theme.changePct)}</span></div><div className="strength-track"><span style={{width:`${theme.score}%`}}/></div><div className="theme-card-bottom"><span>상승 종목 <strong>{Math.round(theme.breadthPct)}%</strong></span><span>{compactMoney(theme.tradedValue)}</span></div></button>})}</div>{activeTheme&&<div className="filter-caption">{themes.find(t=>t.id===activeTheme)?.name} 테마로 추천 후보를 필터링했습니다.<button onClick={()=>setActiveTheme(null)}>필터 해제 <X size={12}/></button></div>}</section>
            <div className="dashboard-columns"><div className="dashboard-primary">
              <section className="recommend-section"><div className="section-heading"><div><h2><Sparkles size={18} className="purple"/>추천 레이더 <span className="count-label purple-label">TOP {allCandidates?'6':'3'}</span></h2><p>수급, 차트, 리스크까지. 근거가 있는 진입 후보.</p></div><button className="text-button" onClick={()=>setAllCandidates(!allCandidates)}>{allCandidates?'A등급 이상 보기':'전체 후보 보기'} <ArrowRight size={14}/></button></div><div className="recommend-toolbar"><div className="radar-live"><span className="pulse-dot"/> {isDemo ? '샘플 시그널' : isLive ? '시세 기반 시그널' : '시그널 확인 중'} <span className="toolbar-divider"/> <span className="toolbar-note">{allCandidates?'모든 등급':'A등급 이상'} · {visibleStocks.length}개 후보</span></div><label className="sort-select"><SlidersHorizontal size={13}/><select aria-label="추천 정렬" value={sort} onChange={e=>setSort(Number(e.target.value))}>{sortOptions.map((s,i)=><option value={i} key={s}>{s}</option>)}</select><ChevronDown size={11}/></label></div><div className="recommend-grid">{recommended.map((stock,i)=><RecommendationCard key={stock.symbol} stock={stock} rank={i+1} watched={watchlist.includes(stock.symbol)} onWatch={()=>toggleWatch(stock.symbol)} onOpen={()=>openStock(stock.symbol)}/>)}</div>{recommended.length===0&&<div className="panel empty-state"><Target size={30}/><h3>조건을 충족하는 후보가 없습니다</h3><p>전체 후보를 확인하거나 다른 테마를 선택해보세요.</p><button className="secondary-button" onClick={()=>{setAllCandidates(true);setActiveTheme(null);}}>전체 후보 보기</button></div>}</section>
              <section className="panel chart-panel"><div className="panel-header"><div><h2>차트 인사이트 <span className="subtle-tag">{selected?.mode === 'demo' || isDemo ? '샘플 통합 캔들' : selected?.venueLabel || venueLabel}</span></h2><p>가격보다 중요한, 지금의 위치를 확인하세요.</p></div><select className="stock-select" aria-label="차트 종목 선택" value={selected?.symbol || stocks[0]?.symbol || ''} onChange={e=>{setSelected(stocks.find(s=>s.symbol===e.target.value)||null);}}>{stocks.map(s=><option value={s.symbol} key={s.symbol}>{s.name}</option>)}</select></div>{selected && <><div className="chart-heading"><div className="chart-stock"><span className={`stock-avatar color-${selected.rank%5}`}>{selected.name.slice(0,1)}</span><div><button onClick={()=>openStock(selected.symbol)}>{selected.name} <ArrowUpRight size={13}/></button><small>{selected.symbol} · {selected.theme}</small></div><strong>{money(selected.price)}<small>원</small></strong><span className={selected.changePct>=0?'positive':'negative'}>{pct(selected.changePct)}</span></div><div className="segmented">{(['1d','5m','1m'] as const).map(v=><button key={v} className={interval===v?'active':''} onClick={()=>setIntervalValue(v)}>{v==='1d'?'일봉':v==='5m'?'5분':'1분'}</button>)}</div></div><div className="chart-legend"><span><i className="legend-lilac"/>{selected.technical.shortPeriod || 5}일 이동평균</span><span><i className="legend-teal"/>진입 관찰 구간</span><span><i className="legend-red"/>손절 기준</span><span><i className="legend-gold"/>목표가</span></div><PriceChart candles={interval==='1d'?(selected.candles||[]):aggregateCandles(selected.intraday||[],interval)} symbol={selected.symbol} significantCandleDate={selected.technical.significantCandleDate || undefined} maPeriod={selected.technical.shortPeriod || 5} interval={interval} height={260} levels={chartLevels(selected)}/><div className="chart-footer"><span><ShieldCheck size={13}/> {selected?.venueLabel || venueLabel}{isDemo ? ' · 실제 시장 데이터 아님' : ''}</span><button className="text-button" onClick={()=>openStock(selected.symbol)}>분석 자세히 보기 <ArrowRight size={13}/></button></div></>}</section>
            </div><aside className="dashboard-secondary">
              <section className="panel watch-panel"><div className="panel-header"><h2>나의 관심종목 <span className="number-badge">{watchlist.length}</span></h2><button className="icon-button" aria-label="관심종목 관리" onClick={()=>navigate('watchlist')}><Plus size={17}/></button></div><div className="watch-list">{stocks.filter(s=>watchlist.includes(s.symbol)).slice(0,5).map(stock=><button className="watch-row" key={stock.symbol} onClick={()=>openStock(stock.symbol)}><span className={`stock-avatar color-${stock.rank%5}`}>{stock.name.slice(0,1)}</span><span><strong>{stock.name}</strong><small>{stock.theme}</small></span><span className="watch-price"><strong>{money(stock.price)}</strong><small className={stock.changePct>=0?'positive':'negative'}>{pct(stock.changePct)}</small></span></button>)}{watchlist.length===0 && <p className="empty-state">추천 카드의 별을 눌러<br/>관심종목을 추가하세요.</p>}</div><button className="watch-footer" onClick={()=>navigate('watchlist')}>관심종목 모두 보기 <ArrowRight size={13}/></button></section>
              <section className="insight-card"><div className="insight-label"><Sparkles size={15}/> RADAR INSIGHT</div><h3>상승을 쫓기보다,<br/><span>좋은 자리를 기다리세요.</span></h3><p>강한 테마의 종목이 5일선에 가까워질 때,<br/>수급과 지지가 함께 확인되는지 살펴보세요.</p><div className="insight-steps"><span>테마 동조</span><ChevronRight size={12}/><span>수급 확인</span><ChevronRight size={12}/><span>눌림 관찰</span></div><button onClick={()=>setGuideOpen(true)}>오늘의 전략 체크포인트 <ArrowUpRight size={15}/></button><div className="insight-decoration"><TrendingUp size={78} strokeWidth={1}/></div></section>
              <section className="panel signals-panel"><div className="panel-header"><h2>최근 시그널</h2><span className="subtle-tag">{isDemo ? '샘플' : isLive ? '시세 기반' : '출처 확인 중'}</span></div>{notifications.slice(0,3).map((n,i)=><button className="signal-item" key={n.id} onClick={()=>openStock(n.symbol)}><div className={`signal-dot signal-${i}`}>{i===0?<Target size={13}/>:i===1?<TrendingUp size={13}/>:<Activity size={13}/>}</div><div><strong>{n.name}<span>{formatTime(n.timestamp)}</span></strong><p>{n.title}</p><span className={`signal-type ${i===0?'signal-ready':''}`}>{n.type==='ENTRY_READY'?'진입 조건 충족':n.type==='BREAKOUT_CONFIRM'?'돌파 확인':'관찰 중'}</span></div></button>)}<div className="signal-caption"><Clock3 size={12}/> {isDemo ? '실제 시장 알림은 API 연결 후 제공됩니다.' : isLive ? '토스증권 시세 기반으로 분석한 신호입니다.' : '신호 데이터 연결 상태를 확인하고 있어요.'}</div></section>
            </aside></div>
          </>}
        </> : <>{error&&<div className="error-banner">{error}</div>}<WorkspaceViews market={market} view={view} stocks={stocks} themes={themes} watchlist={watchlist} toggleWatch={toggleWatch} onOpenStock={openStock} onRefresh={()=>refresh()} toast={toast}/></>}
        <footer className="page-footer"><span><Activity size={12}/> FlowTrade Radar <span>·</span> 데이터가 보여주는 더 나은 타이밍</span><span>{modeLabel} <i/> 실주문 비활성화</span></footer>
      </main>
    </div>
    {detailSymbol && <div className="modal-overlay" onClick={closeStock}><section className="stock-detail-modal" role="dialog" aria-modal="true" aria-label="종목 상세 분석" onClick={e=>e.stopPropagation()}><div className="detail-topbar"><span className="eyebrow">STOCK ANALYSIS <span className="subtle-tag">{detail?.mode === 'live' ? '토스증권 시세' : detail?.mode === 'demo' || isDemo ? '샘플 데이터' : '출처 확인 중'}</span></span><button className="icon-button" aria-label="종목 상세 닫기" onClick={closeStock}><X size={21}/></button></div>{detail ? <StockDetail market={market} stock={detail} watched={watchlist.includes(detail.symbol)} onWatch={()=>toggleWatch(detail.symbol)} toast={toast}/> : detailError ? <div className="empty-state"><Search size={28}/><h3>분석을 불러올 수 없습니다</h3><p>{detailError}</p><button className="secondary-button" onClick={closeStock}>대시보드로 돌아가기</button></div> : <div className="detail-loading"><RefreshCw size={24} className="spin"/> 분석을 불러오는 중…</div>}</section></div>}
    {guideOpen && <div className="modal-overlay" onClick={()=>setGuideOpen(false)}><section className="guide-modal panel" role="dialog" aria-modal="true" aria-label="레이더 이용 가이드" onClick={e=>e.stopPropagation()}><div className="panel-header"><span className="eyebrow"><BookOpen size={16}/> RADAR GUIDE</span><button className="icon-button" aria-label="가이드 닫기" onClick={()=>setGuideOpen(false)}><X size={20}/></button></div><h2>좋은 종목을, 좋은 가격에.</h2><p className="muted">레이더는 세 가지 질문에 답합니다.</p><div className="guide-step"><span>01</span><div><h3>지금 돈은 어디로 가나요?</h3><p>테마 강도와 3개 이상 종목의 동반 상승, 거래대금, 미국장 선행 흐름을 함께 확인합니다.</p></div></div><div className="guide-step"><span>02</span><div><h3>어디에서 기다려야 하나요?</h3><p>5일선과 박스, 기준봉이 겹치는 진입 구간에서 지지와 재돌파를 확인합니다.</p></div></div><div className="guide-step"><span>03</span><div><h3>어디에서 판단이 틀렸나요?</h3><p>기술적 무효화 가격, 예상 손실과 손익비를 확인합니다. 5일선 이탈만으로 판단하지 않습니다.</p></div></div><div className="guide-note"><ShieldCheck size={18}/><p>{isDemo ? '현재 화면은 샘플 데이터입니다. 실제 시세가 아니며 자동매매 기능은 제공하지 않습니다.' : isLive ? '토스증권 시세를 수치 기반 엔진으로 분석합니다. 실제 주문은 비활성화되어 있습니다.' : '시장 데이터 출처를 확인하고 있습니다. 실제 주문은 비활성화되어 있습니다.'} 점수와 가격은 수치 기반 엔진에서 계산합니다.</p></div><button className="primary-button" onClick={()=>setGuideOpen(false)}>레이더 둘러보기 <ArrowRight size={15}/></button></section></div>}
    {toastText && <div className="toast" role="status"><Check size={16}/>{toastText}<button aria-label="메시지 닫기" onClick={()=>setToastText('')}><X size={14}/></button></div>}
  </div>;
}

function RecommendationCard({stock, rank, watched, onWatch, onOpen}: {stock:Stock;rank:number;watched:boolean;onWatch:()=>void;onOpen:()=>void}) {
  const spark = stock.sparkline || [];
  return <article className={`recommend-card ${rank===1?'first-pick':''}`}><div className="recommend-card-top"><span className="pick-rank"><span>#{rank}</span> {rank===1?'TOP PICK':'RADAR PICK'}</span><button className={`star-button ${watched?'is-watched':''}`} onClick={onWatch} aria-label={`${stock.name} 관심종목 ${watched?'해제':'추가'}`}><Star size={17} fill={watched?'currentColor':'none'}/></button></div><button className="recommend-name" onClick={onOpen}><span>{stock.name}</span><ArrowUpRight size={16}/></button><div className="recommend-subtitle">{stock.symbol}<i/>{stock.theme}</div><div className="recommend-price"><strong>{money(stock.price)}<small>원</small></strong><span className={stock.changePct>=0?'positive':'negative'}>{pct(stock.changePct)}</span></div><div className="recommend-spark"><MiniSparkline values={spark} color="#9577d9" width={200} height={38} fill={true}/><span className="grade"><strong>{stock.grade}</strong><span>{stock.score}<small>점</small></span></span></div><div className={`state-badge state-${stock.state.toLowerCase()}`}><span/>{stateLabel[stock.state]||stock.state}</div><div className="entry-zone"><span><Target size={12}/> 진입 관찰가</span><strong>{money(stock.entry.low)} <small>~</small> {money(stock.entry.high)}</strong></div><div className="price-levels"><div><span>손절 기준</span><strong className="negative">{money(stock.risk.stop)}</strong></div><div><span>1차 목표</span><strong>{money(stock.targets[0])}</strong></div><div><span>예상 손익비</span><strong className="purple">1 : {stock.riskReward.toFixed(1)}</strong></div></div><div className="recommend-reasons">{stock.reasons.slice(0,2).map((r,i)=><p key={i}><Check size={12}/>{r}</p>)}</div><button className="card-detail-button" onClick={onOpen}>추천 근거 확인 <ArrowRight size={13}/></button></article>;
}

function StockDetail({stock,watched,onWatch,toast,market}:{stock:Stock;watched:boolean;onWatch:()=>void;toast:(m:string)=>void;market:Market|null}) {
  const [interval,setIntervalValue]=useState<'1d'|'5m'|'1m'>('1d');
  const [quantity,setQuantity]=useState(1);
  const [buying,setBuying]=useState(false);
  const isLive = stock.mode === 'live';
  const isDemo = stock.mode === 'demo' || (!stock.mode && market?.mode === 'demo');
  const priceLabel = isLive ? '토스증권 시세' : isDemo ? '모의 시세' : '출처 확인 중';
  const flowLabel = stock.flowUnit === 'shares' ? '순매수 수량' : stock.flowUnit === 'KRW' || isDemo ? '순매수 금액' : '단위 확인 필요';

  const buy=async()=>{setBuying(true);try{await api('/paper/buy',{method:'POST',body:JSON.stringify({symbol:stock.symbol,quantity})});toast(`${stock.name} ${quantity}주를 가상 포트폴리오에 추가했습니다.`);}catch(e){toast((e as Error).message);}finally{setBuying(false);}};
  return <><div className="detail-heading"><span className={`stock-avatar large-avatar color-${stock.rank%5}`}>{stock.name.slice(0,1)}</span><div><h2>{stock.name}<span className="subtle-tag">{stock.theme}</span></h2><p>{stock.symbol} · KRW · {priceLabel}</p></div><button className={`secondary-button ${watched?'purple':''}`} onClick={onWatch}><Star size={15} fill={watched?'currentColor':'none'}/>{watched?'관심종목':'관심종목 추가'}</button></div><div className="detail-price"><strong>{money(stock.price)}<small>원</small></strong><span className={stock.changePct>=0?'positive':'negative'}>{pct(stock.changePct)}</span><span className={`state-badge state-${stock.state.toLowerCase()}`}><span/>{stateLabel[stock.state]}</span><span className="detail-grade">{stock.grade} <strong>{stock.score}</strong>점</span></div><div className="detail-stats"><div><span>거래대금</span><strong>{compactMoney(stock.tradedValue)}</strong></div><div><span>거래량</span><strong>{money(stock.volume)}주</strong></div><div><span>{stock.technical.shortPeriod || 5}일 이동평균</span><strong>{money(stock.technical.ma5)}원</strong></div><div><span>돌파 확인선</span><strong>{money(stock.technical.pivot)}원</strong></div></div><div className="detail-chart-controls"><span><i className="legend-lilac"/>MA{stock.technical.shortPeriod || 5} <i className="legend-teal"/>진입구간 <i className="legend-red"/>손절 <i className="legend-gold"/>목표</span><div className="segmented">{(['1d','5m','1m'] as const).map(v=><button key={v} className={interval===v?'active':''} onClick={()=>setIntervalValue(v)}>{v==='1d'?'일봉':v==='5m'?'5분':'1분'}</button>)}</div></div><PriceChart candles={interval==='1d'?(stock.candles||[]):aggregateCandles(stock.intraday||[],interval)} levels={chartLevels(stock)} interval={interval} significantCandleDate={stock.technical.significantCandleDate || undefined} maPeriod={stock.technical.shortPeriod || 5} symbol={stock.symbol} height={330}/><div className="detail-grid"><div><section className="detail-section"><h3><Target size={16}/> 진입 시나리오</h3><div className="detail-entry"><span>진입 관찰가</span><strong>{money(stock.entry.low)} ~ {money(stock.entry.high)}원</strong></div><div className="detail-level-grid"><div><span>기술적 손절</span><strong className="negative">{money(stock.risk.stop)}원</strong><small>예상 손실 {stock.risk.stopLossPct.toFixed(2)}%</small></div><div><span>1차 목표</span><strong>{money(stock.targets[0])}원</strong><small>{pct((stock.targets[0]/((stock.entry.low+stock.entry.high)/2)-1)*100)}</small></div><div><span>2차 목표</span><strong>{money(stock.targets[1])}원</strong><small>손익비 1 : {stock.riskReward.toFixed(1)}</small></div></div><p className="invalidation-text"><ShieldCheck size={14}/>{money(stock.risk.stop)}원 하회 후 지지 회복 실패 시 진입 논리 무효화</p><div className="detail-box"><span>박스 하단 <b>{money(stock.technical.boxLow)}</b></span><ArrowRight size={14}/><span>박스 상단 <b>{money(stock.technical.boxHigh)}</b></span><span>기준봉 <b>{stock.technical.significantCandleDate||'없음'}</b></span></div></section><section className="detail-section"><h3><CheckCheck size={16}/> 추천 근거</h3>{stock.reasons.map((reason,i)=><p className="detail-reason" key={i}><span><Check size={13}/></span>{reason}</p>)}</section><section className="detail-section"><h3><ShieldCheck size={16}/> 확인해야 할 위험</h3>{stock.risks.length ? stock.risks.map((risk,i)=><p className="risk-reason" key={i}>• {risk}</p>) : <p className="risk-reason">{isDemo ? '샘플 데이터 분석입니다. 실제 유동성·시장 이벤트를 반영하지 않습니다.' : '시장 이벤트와 체결 상황을 함께 확인하세요.'}</p>}<p className="risk-reason">{stock.venueLabel || market?.venueLabel || '가격 출처 확인 중'}{isDemo ? ' · 실제 Toss 시세의 KRX/NXT 포함 여부는 아직 검증되지 않았습니다.' : ''}</p></section></div><div><section className="detail-section score-section"><h3><Activity size={16}/> 추천 신뢰도</h3>{Object.entries(stock.scores).map(([key,value])=><div className="score-row" key={key}><span>{{theme:'테마',flow:'수급',chart:'차트',entry:'지지 중첩',riskReward:'손익비',realtime:'진입 확인'}[key]}</span><div><span style={{width:`${value}%`}}/></div><strong>{Math.round(value)}</strong></div>)}<p className="muted">테마 25 · 수급 20 · 차트 25 · 지지 15 · 손익비 10 · 확인 5점의 가중 합계</p></section><section className="detail-section"><h3><TrendingUp size={16}/> 투자자 수급 <span className="subtle-tag">{isDemo ? '샘플' : flowLabel}</span></h3>{stock.flow && Object.entries(stock.flow).map(([key,value])=><div className="flow-row" key={key}><span>{{foreign:'외국인',institution:'기관',program:'프로그램'}[key]}{stock.flowVenue?.[key as keyof NonNullable<Stock['flowVenue']>] && <small className="flow-venue-label">{flowVenueLabel(stock.flowVenue[key as keyof NonNullable<Stock['flowVenue']>])}</small>}</span><strong className={value>=0?'positive':'negative'}>{value>=0?'+':''}{stock.flowUnit === 'shares' ? `${money(value)}주` : stock.flowUnit === 'KRW' || isDemo ? compactMoney(value) : `${money(value)} (단위 확인 필요)`}</strong></div>)}{stock.flowAsOf && <p className="flow-date-note">외국인·기관 {stock.flowAsOf.investor || '일자 확인 중'} · 프로그램 {stock.flowAsOf.program || '일자 확인 중'}</p>}</section><section className="paper-quick"><h3><Wallet size={17}/> 모의 포트폴리오에 담기</h3><p>{isDemo ? '샘플 가격' : isLive ? '수신된 시세' : '표시된 가격'}으로 가상 매수하며 실제 주문은 발생하지 않습니다.</p><label>수량 <input aria-label="모의 매수 수량" type="number" min="1" max="10000" value={quantity} onChange={e=>setQuantity(Math.max(1,Number(e.target.value)))}/><span>주</span></label><button className="primary-button" onClick={buy} disabled={buying}>{buying?'추가 중…':'가상 매수'} <Plus size={15}/></button></section></div></div></>;
}
function formatTime(value:string) { try{return new Intl.DateTimeFormat('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Seoul'}).format(new Date(value));}catch{return '—';} }
function flowVenueLabel(value?: string) { if (value === 'INTEGRATED') return 'KRX+NXT'; if (value === 'KRX' || value === 'KRX only') return 'KRX만'; if (value === 'NXT') return 'NXT만'; return value || '시장 확인 필요'; }
function formatSnapshotDate(value: string) { try { const date = new Date(value); return { date: new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date).replace(/\. /g, '.').replace(/\.$/, ''), weekday: new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', weekday: 'long' }).format(date) }; } catch { return { date: '확인 중', weekday: '' }; } }
function chartLevels(s:Stock) {return {ma5:s.technical.ma5,pivot:s.technical.pivot,boxHigh:s.technical.boxHigh,boxLow:s.technical.boxLow,stop:s.risk.stop,entryLow:s.entry.low,entryHigh:s.entry.high,target1:s.targets[0],target2:s.targets[1]};}
function aggregateCandles(candles:NonNullable<Stock['candles']>,interval:'5m'|'1m') {
  if(interval==='1m')return candles;
  const result:NonNullable<Stock['candles']>=[];
  for(let i=0;i<candles.length;i+=5){const group=candles.slice(i,i+5);result.push({time:group[0].time,open:group[0].open,high:Math.max(...group.map(c=>c.high)),low:Math.min(...group.map(c=>c.low)),close:group[group.length-1].close,volume:group.reduce((sum,c)=>sum+c.volume,0)});}return result;
}
