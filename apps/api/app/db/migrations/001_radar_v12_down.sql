-- Back up radar.sqlite3 with SQLite's backup API before rollback.
-- Stop ingestion; this removes ONLY v1.2 state, not legacy user data.
DROP TABLE IF EXISTS catalyst_evidence;
DROP TABLE IF EXISTS catalyst_revisions;
DROP TABLE IF EXISTS catalyst_events;
DROP TABLE IF EXISTS catalyst_cursors;
DROP TABLE IF EXISTS catalyst_provider_health;
DROP TABLE IF EXISTS ticker_theme_relations;
DROP TABLE IF EXISTS turnover_samples;
DROP TABLE IF EXISTS minute_symbol_metrics;
DROP TABLE IF EXISTS theme_flow_snapshots;
DROP TABLE IF EXISTS theme_rank_history;
DROP TABLE IF EXISTS symbol_rank_history;
DROP TABLE IF EXISTS recommendation_audit;
DROP TABLE IF EXISTS market_data_provider_health;
DROP TABLE IF EXISTS radar_state;
DROP TABLE IF EXISTS radar_state_revisions;
DROP TABLE IF EXISTS radar_notifications;
DELETE FROM schema_migrations WHERE version = 'radar_v12_001';
