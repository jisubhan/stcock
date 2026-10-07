export type SignalState = 'SCANNING' | 'WATCH' | 'APPROACHING_ENTRY' | 'ENTRY_READY' | 'BREAKOUT_CONFIRM' | 'TAKE_PROFIT_ZONE' | 'INVALIDATED';
export interface Candle { time: string; open: number; high: number; low: number; close: number; volume: number; ma5?: number; }
export interface Stock {
  symbol: string; name: string; theme: string; themeId: string; score: number; grade: string; state: SignalState;
  price: number; changePct: number; tradedValue: number; volume: number; rank: number; eligible: boolean;
  entry: { low: number; high: number };
  technical: { ma5: number; ma20: number; pivot: number; boxHigh: number; boxLow: number; shortPeriod?: number; mediumPeriod?: number; significantCandleDate: string | null };
  risk: { stop: number; stopLossPct: number }; targets: number[]; riskReward: number;
  reasons: string[]; risks: string[];
  scores: { theme: number; flow: number; chart: number; entry: number; riskReward: number; realtime: number };
  sparkline?: number[]; candles?: Candle[]; intraday?: Candle[]; flow?: { foreign: number; institution: number; program: number };
  mode?: string; source?: string; asOf?: string; venueLabel?: string;
  flowUnit?: 'shares' | 'KRW'; flowVenue?: { foreign?: string; institution?: string; program?: string };
  flowAsOf?: { investor?: string; program?: string };
  stale?: boolean; degraded?: boolean;
}
export interface Theme { id: string; name: string; score: number; breadthPct: number; tradedValue: number; changePct: number; leaders: {symbol:string;name:string}[]; usTickers: string[]; usLeadPct: number | null; memberSymbols: string[]; qualified: boolean; }
export interface Market {
  mode: string; source?: string; asOf: string; session: string; sessionLabel?: string; venueLabel: string; summary?: string; note?: string;
  stale?: boolean; degraded?: boolean; staleReason?: string; notice?: string;
  indices: { name: string; value: number; changePct: number | null; sparkline: number[] }[];
  risk: {level: string;label: string}; usMarkets: {symbol:string;name:string;changePct:number | null}[]; dataStatus: Record<string, unknown>;
}
export interface NotificationItem { id: string; symbol: string; name: string; type: string; title: string; message: string; timestamp: string; read: boolean; }
export type View = 'dashboard' | 'themes' | 'watchlist' | 'backtest' | 'paper' | 'settings';
