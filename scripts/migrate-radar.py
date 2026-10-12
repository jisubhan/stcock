#!/usr/bin/env python3
"""Apply the additive schema, or back up SQLite before explicit v1.2 rollback."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api'))
from app.settings import runtime_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--down', action='store_true', help='Remove only v1.2 tables after creating a SQLite backup.')
    args = parser.parse_args()
    database = runtime_dir() / 'radar.sqlite3'
    if not args.down:
        from app.radar.storage import connection
        with connection() as conn:
            assert conn.execute("SELECT 1 FROM schema_migrations WHERE version='radar_v12_001'").fetchone()
            assert conn.execute("SELECT 1 FROM schema_migrations WHERE version='quote_monitor_002'").fetchone()
            assert conn.execute("SELECT 1 FROM schema_migrations WHERE version='market_discovery_003'").fetchone()
        print('radar_v12_001, quote_monitor_002 and market_discovery_003 additive migrations applied; legacy tables retained.')
        return
    if not database.exists():
        parser.error('The database does not exist; there is nothing to roll back.')
    directory = runtime_dir() / 'backups'
    directory.mkdir(exist_ok=True)
    name = 'radar-before-v12-rollback-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3'
    with sqlite3.connect(database) as source, sqlite3.connect(directory / name) as destination:
        source.backup(destination)
        source.executescript((ROOT / 'apps/api/app/db/migrations/003_discovery_down.sql').read_text())
        source.executescript((ROOT / 'apps/api/app/db/migrations/002_quote_monitor_down.sql').read_text())
        source.executescript((ROOT / 'apps/api/app/db/migrations/001_radar_v12_down.sql').read_text())
    print('v1.2 tables rolled back; legacy data retained. Backup created in API_DATA_DIR/backups/.')


if __name__ == '__main__':
    main()
