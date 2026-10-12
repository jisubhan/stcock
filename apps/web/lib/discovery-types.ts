export type DiscoveryQuality = 'LIVE' | 'DELAYED' | 'STALE' | 'PARTIAL' | 'UNAVAILABLE' | 'ANALYSIS_ONLY' | 'READY' | 'PENDING' | 'WATCH';

export interface DiscoveryUniverse {
  expected?: number | null;
  observed?: number | null;
  classified?: number | null;
  unclassified?: number | null;
  quote_fresh?: number | null;
  missing?: number | null;
  stale?: number | null;
  bootstrap_observed?: number | null;
  status?: string;
  scope?: string;
  markets?: string[];
}

export interface DiscoveryEvidence {
  kind?: string;
  title?: string;
  source_url?: string | null;
  first_seen_at?: string | null;
  provider_at?: string | null;
  published_at?: string | null;
  relationship?: string;
}

export interface DiscoveryQuote {
  price?: number | null;
  quote_at?: string | null;
  first_seen_at?: string | null;
  status?: string;
  source?: string;
  change_pct?: number | null;
  latency_seconds?: number | null;
}

export interface DiscoveryThemeMember {
  symbol: string;
  name?: string;
  price?: number | null;
  quote?: DiscoveryQuote | null;
  association_kind?: string;
  source_url?: string | null;
  watch_score?: number | { score?: number | null; coverage?: number | null; available_max_points?: number; factors?: Record<string, unknown> } | null;
  technical_quality?: string;
}

export interface DiscoveryTheme {
  id: string;
  sector_id?: string;
  name: string;
  state?: string;
  quality?: string;
  score?: number | null;
  watch_score?: number | null;
  score_definition?: string;
  confidence?: number | string | null;
  coverage?: {
    members_expected?: number | null;
    members_observed?: number | null;
    quote_fresh_count?: number | null;
    ratio?: number | null;
    turnover_scope?: string;
  };
  factors?: Record<string, { value?: number | null; points?: number | null; max_points?: number; quality?: string }>;
  evidence?: DiscoveryEvidence[];
  members?: DiscoveryThemeMember[];
  recommended_stocks?: DiscoveryThemeMember[];
  symbols?: string[];
  observed_turnover_krw?: number | null;
  turnover_krw?: number | null;
  metrics?: {
    turnover_krw?: number | null;
    turnover_velocity_krw_per_min?: number | null;
    breadth_up_ratio?: number | null;
    covered_universe_share?: number | null;
  };
  flow_confirmed?: boolean;
  observation_kind?: string;
  provider_at?: string | null;
  first_seen_at?: string | null;
  expected_timing?: string | null;
  factor_coverage?: number | null;
  available_max_points?: number | null;
  turnover?: {
    allocated_trading_amount_krw?: number | null;
    raw_member_trading_amount_krw?: number | null;
    observed_unique_denominator_krw?: number | null;
    allocated_share?: number | null;
    allocation_definition?: string;
  };
  reason_codes?: string[];
}

export interface DiscoveryStock {
  symbol: string;
  name: string;
  market?: string;
  catalog_member?: boolean;
  price: number | null;
  quote_at?: string | null;
  quote_status?: string;
  sector_ids?: string[];
  theme_refs?: { id: string; name: string; relationship?: string; source_url?: string | null }[];
  watch_score?: number | null;
  observed_turnover_krw?: number | null;
  turnover_quality?: string;
  analysis_status?: string;
  trading_amount_krw?: number | null;
  quote?: DiscoveryQuote | null;
  quality?: string;
}

export interface DiscoveryOverview {
  as_of: string;
  source: string;
  status: string;
  universe: DiscoveryUniverse;
  theme_hypotheses_top3: DiscoveryTheme[];
  observed_theme_leaders: DiscoveryTheme[];
  themes: DiscoveryTheme[];
  unmapped_leaders: DiscoveryStock[];
  reason_codes?: string[];
  measurement_definition?: string;
  news_status?: string | { status: string; reason?: string };
}

export interface DiscoveryStockPage {
  items: DiscoveryStock[];
  total: number;
  page: number;
  limit: number;
  as_of: string;
  status: string;
  scope?: string;
}

export interface DiscoveryCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma5?: number;
  ma5_5m?: number | null;
  start?: string;
  end?: string;
  status?: 'CLOSED' | 'PARTIAL';
  source?: string;
  venue?: string;
}

export interface DiscoveryDetail {
  symbol: string;
  name: string;
  as_of: string;
  source: string;
  status: 'READY' | 'PENDING' | 'UNAVAILABLE';
  reason?: string;
  quote?: DiscoveryQuote | null;
  theme?: DiscoveryTheme | null;
  chart?: {
    daily?: DiscoveryCandle[];
    minute?: DiscoveryCandle[];
    five_minute?: DiscoveryCandle[];
    levels?: Partial<Record<'ma5' | 'pivot' | 'boxHigh' | 'boxLow' | 'stop' | 'entryLow' | 'entryHigh' | 'target1' | 'target2', number | null>>;
  } | null;
  technical?: { ma5?: number | null; ma20?: number | null; significantCandleDate?: string | null; shortPeriod?: number; [key: string]: unknown } | null;
  earlyEntry?: {
    state?: string;
    quality?: string;
    eligible?: boolean;
    preview?: boolean;
    MA5_DAILY?: number | null;
    MA5_5M?: number | null;
    support_dwell_seconds?: number | null;
    reason_codes?: string[];
    risk?: { first_resistance_reward_risk?: number | null } | null;
  } | null;
  watch_score?: { score?: number | null; coverage?: number | null; definition?: string } | null;
  quality?: string | Record<string, unknown>;
  reasons?: string[];
  warnings?: string[];
  orders_enabled: false;
}
