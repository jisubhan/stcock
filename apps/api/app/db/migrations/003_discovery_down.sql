-- Stop discovery workers before rollback; legacy/radar/quotes remain intact.
DROP TABLE IF EXISTS discovery_analysis_queue;
DROP TABLE IF EXISTS discovery_latest;
DROP TABLE IF EXISTS discovery_records;
DELETE FROM schema_migrations WHERE version='market_discovery_003';
