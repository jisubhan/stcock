CREATE TABLE IF NOT EXISTS discovery_records (
  record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, record_key TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS discovery_record_pit ON discovery_records(kind,record_key,first_seen_at);
CREATE TABLE IF NOT EXISTS discovery_latest (
  kind TEXT NOT NULL, record_key TEXT NOT NULL, first_seen_at TEXT NOT NULL,
  payload_json TEXT NOT NULL, PRIMARY KEY(kind,record_key)
);
CREATE TABLE IF NOT EXISTS discovery_analysis_queue (
  symbol TEXT PRIMARY KEY, requested_at TEXT NOT NULL, priority INTEGER NOT NULL,
  status TEXT NOT NULL, completed_at TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS discovery_queue_priority ON discovery_analysis_queue(status,priority,requested_at);
