-- Additive v1.2 migration. Existing paper/notification/backtest tables survive.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticker_theme_relations (
  relation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, theme_id TEXT NOT NULL,
  effective_from TEXT NOT NULL, first_seen_at TEXT NOT NULL, mode TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relations_point_in_time
  ON ticker_theme_relations(mode, first_seen_at, effective_from);
CREATE TABLE IF NOT EXISTS turnover_samples (
  sample_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, session_id TEXT NOT NULL,
  session_date TEXT NOT NULL, as_of TEXT NOT NULL, first_seen_at TEXT NOT NULL,
  mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turnover_session
  ON turnover_samples(mode, session_date, session_id, symbol, as_of);
CREATE TABLE IF NOT EXISTS minute_symbol_metrics (
  observation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, session_id TEXT NOT NULL,
  session_date TEXT NOT NULL, as_of TEXT NOT NULL, first_seen_at TEXT NOT NULL,
  interval_start TEXT NOT NULL, interval_end TEXT NOT NULL, venue TEXT NOT NULL,
  provider TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_minute_metrics_session
  ON minute_symbol_metrics(mode, session_date, session_id, interval_end);
CREATE TABLE IF NOT EXISTS theme_flow_snapshots (
  snapshot_id TEXT PRIMARY KEY, theme_id TEXT NOT NULL, as_of TEXT NOT NULL,
  session_date TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_rank_history (
  history_id TEXT PRIMARY KEY, theme_id TEXT NOT NULL, as_of TEXT NOT NULL,
  session_date TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_theme_rank_date
  ON theme_rank_history(mode, session_date, as_of);
CREATE TABLE IF NOT EXISTS symbol_rank_history (
  history_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, as_of TEXT NOT NULL,
  session_date TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendation_audit (
  audit_id TEXT PRIMARY KEY, theme_id TEXT, as_of TEXT NOT NULL,
  session_date TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_data_provider_health (
  provider TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radar_state (
  state_key TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radar_state_revisions (
  revision_id TEXT PRIMARY KEY, state_key TEXT NOT NULL, as_of TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_radar_state_available ON radar_state_revisions(state_key, as_of);
CREATE TABLE IF NOT EXISTS radar_notifications (
  fingerprint TEXT PRIMARY KEY, theme_id TEXT, notification_type TEXT NOT NULL,
  as_of TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL
);
