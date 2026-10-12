-- Independent actual quotes; no synthetic prices, credentials or OAuth tokens.
CREATE TABLE IF NOT EXISTS quote_observations (
  observation_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  quote_at TEXT,
  first_seen_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quote_observations_time ON quote_observations(symbol, quote_at, first_seen_at);
CREATE TABLE IF NOT EXISTS quote_latest (
  symbol TEXT PRIMARY KEY,
  quote_at TEXT,
  first_seen_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_monitor_health (
  observation_id TEXT PRIMARY KEY,
  observed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quote_monitor_health_time ON quote_monitor_health(observed_at);
