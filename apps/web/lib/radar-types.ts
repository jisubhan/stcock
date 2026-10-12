export type RadarQuality = 'LIVE' | 'DELAYED' | 'STALE' | 'PARTIAL' | 'UNAVAILABLE' | 'DEMO' | 'REPLAY';
export interface RadarFeedHealth { status: RadarQuality; provider?: string | string[]; last_success_at?: string | null; latency_seconds?: number | null; coverage?: string | Record<string, unknown>; reason?: string | null; }
export interface RadarCoverage { symbols_seen?: number; symbols_expected?: number; ratio?: number | null; covered_universe_symbols?: number; expected_universe_symbols?: number; universe_ratio?: number | null; venues?: string[]; denominator_definition?: string; scope?: string; }
export interface RadarMetrics {
  as_of?: string; window_minutes?: number; turnover_krw?: number | null; raw_turnover_krw?: number | null;
  turnover_velocity_krw_per_min?: number | null; relative_turnover?: number | null; history_sessions?: number;
  breadth_up_ratio?: number | null; concentration_top1_ratio?: number | null; covered_universe_share?: number | null; covered_universe_share_ratio?: number | null;
  confirmed_investor_netbuy?: { unit?: string; values?: Record<string, number | null>; quality?: RadarQuality; as_of?: string; provider?: string } | null;
  coverage?: RadarCoverage; strength_type?: string; quality?: RadarQuality;
  previous_metrics?: RadarMetrics; previous?: RadarMetrics; changes?: Record<string, {previous: number | null; current: number | null}>;
  [key: string]: unknown;
}
export interface RadarRankItem {
  theme_id?: string; theme_name?: string; name?: string; symbol?: string; rank?: number; score?: number; state?: string; mode?: string; as_of?: string;
  metrics?: RadarMetrics; explanation?: string; reasons?: string[]; reason_codes?: string[]; source_urls?: string[]; catalyst_event_ids?: string[];
  entry_low?: number; entry_high?: number; stop?: number; target?: number; reward_risk?: number; riskReward?: number;
  rr_to_first_resistance?: number; structural_stop?: number; price?: number;
  entry?: { low?: number; high?: number }; risk?: { stop?: number; stopLossPct?: number; first_resistance_reward_risk?: number; reward_risk?: number }; targets?: number[];
}
export interface RadarList { status: RadarQuality; items: RadarRankItem[]; reason?: string; reason_codes?: string[]; }
export interface RadarCatalyst {
  event_id: string; provider: string; source_url: string; title: string; published_at: string; first_seen_at: string; ingested_at?: string;
  relationship: 'DIRECT_VERIFIED' | 'NEWS_REPORTED' | 'THEMATIC_ASSOCIATION' | 'UNVERIFIED'; status?: string; event_type?: string;
  tickers?: string[]; theme_ids?: string[]; confidence?: number; coverage_status?: RadarQuality; mode?: string;
  ticker_relations?: {symbol: string; relationship: RadarCatalyst['relationship']; confidence?: number; evidence_indices?: number[]}[];
}
export interface RadarTransition {
  transition_id?: string; as_of: string; theme_id: string; theme_name?: string; previous_state?: string; state?: string; transition?: string;
  previous_rank?: number | null; rank?: number | null; from_rank?: number | null; to_rank?: number | null; previous_score?: number | null; score?: number | null;
  explanation?: string; reasons?: string[]; reason_codes?: string[]; metrics?: RadarMetrics; measurements?: RadarMetrics; previous_measurements?: RadarMetrics;
}
export interface OpeningOverview {
  version?: string; mode: 'live' | 'demo' | 'replay' | 'unavailable'; quality?: RadarQuality; as_of: string; session_label?: string; provider?: string | string[];
  measurement_definition?: string; coverage?: RadarCoverage; data_freshness?: Record<string, RadarFeedHealth>;
  premarket_hypothesis_top3: RadarList; live_opening_flow_top3: RadarList; entry_ready_top3: RadarList;
  catalysts?: RadarCatalyst[]; rotation_history?: RadarTransition[];
  replay?: { available?: boolean; session?: string; snapshot_times?: string[]; snapshot_time?: string; scenario?: string };
  reason_codes?: string[]; notice?: string; metrics?: Record<string, unknown>;
}
