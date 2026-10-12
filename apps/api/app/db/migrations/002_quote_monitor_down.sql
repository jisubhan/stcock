-- Stop quote collection before rollback; legacy/radar user data are retained.
DROP TABLE IF EXISTS quote_monitor_health;
DROP TABLE IF EXISTS quote_latest;
DROP TABLE IF EXISTS quote_observations;
DELETE FROM schema_migrations WHERE version = 'quote_monitor_002';
