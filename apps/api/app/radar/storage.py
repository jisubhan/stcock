"""Additive SQLite storage with explicit namespaces and idempotent writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from app.db.store import connection as legacy_connection

SQL_PATH = Path(__file__).resolve().parents[1] / 'db' / 'migrations' / '001_radar_v12.sql'
QUOTE_SQL_PATH = SQL_PATH.with_name('002_quote_monitor.sql')
DISCOVERY_SQL_PATH = SQL_PATH.with_name('003_discovery.sql')


def identity(*parts) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def encoded(value) -> str:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


@contextmanager
def connection():
    with legacy_connection() as conn:
        conn.executescript(SQL_PATH.read_text())
        conn.executescript(QUOTE_SQL_PATH.read_text())
        conn.executescript(DISCOVERY_SQL_PATH.read_text())
        # Catalyst tables are maintained by their source boundary. Import lazily
        # so source adapters can themselves use this connection safely.
        from app.catalysts.store import CATALYST_SCHEMA_SQL
        conn.executescript(CATALYST_SCHEMA_SQL)
        conn.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)',
                     ('radar_v12_001', datetime.now(timezone.utc).isoformat()))
        conn.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)',
                     ('quote_monitor_002', datetime.now(timezone.utc).isoformat()))
        conn.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)',
                     ('market_discovery_003', datetime.now(timezone.utc).isoformat()))
        yield conn


def save_observations(observations):
    with connection() as conn:
        for item in observations:
            payload = item.model_dump(mode='json')
            conn.execute('''INSERT OR IGNORE INTO minute_symbol_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (identity(item.observation_id, item.symbol, item.provider, item.venue, item.interval_start,
                                   item.interval_end, item.mode, payload),
                          item.symbol, item.session_id, item.interval_end.date().isoformat(), item.observed_at.isoformat(),
                          item.first_seen_at.isoformat(), item.interval_start.isoformat(), item.interval_end.isoformat(),
                          item.venue, item.provider, item.mode, encoded(payload)))


def read_observations(as_of, session_id, mode='live', previous=False):
    from app.radar.models import FlowObservation
    from app.radar.models import aware
    as_of = aware(as_of)
    with connection() as conn:
        comparison = '<' if previous else '='
        rows = conn.execute(f'''SELECT payload_json FROM minute_symbol_metrics
            WHERE mode=? AND session_id=? AND session_date {comparison} ?
            ORDER BY interval_end''', (mode, session_id, as_of.date().isoformat())).fetchall()
    return [item for row in rows if (item := FlowObservation.model_validate_json(row['payload_json'])).first_seen_at <= as_of
            and item.observed_at <= as_of and item.interval_end <= as_of]


def save_relations(relations, mode='live'):
    with connection() as conn:
        for item in relations:
            conn.execute('INSERT OR IGNORE INTO ticker_theme_relations VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (identity(item.theme_id, item.symbol, item.effective_from, item.source_url, mode, item.model_dump(mode='json')),
                          item.symbol, item.theme_id, item.effective_from.isoformat(), item.first_seen_at.isoformat(),
                          mode, encoded(item)))


def read_relations(as_of, mode='live'):
    from app.radar.models import ThemeRelation
    from app.radar.models import aware
    as_of = aware(as_of)
    with connection() as conn:
        rows = conn.execute('SELECT payload_json FROM ticker_theme_relations WHERE mode=?', (mode,)).fetchall()
    return [item for row in rows if (item := ThemeRelation.model_validate_json(row['payload_json'])).first_seen_at <= as_of
            and item.effective_from <= as_of]


def save_rotation(result, snapshots, engine_state, state_key):
    date = result.as_of.date().isoformat()
    with connection() as conn:
        for snapshot in snapshots:
            conn.execute('INSERT OR IGNORE INTO theme_flow_snapshots VALUES (?, ?, ?, ?, ?, ?)',
                         (identity(snapshot.theme_id, snapshot.as_of, snapshot.mode), snapshot.theme_id,
                          snapshot.as_of.isoformat(), date, snapshot.mode, encoded(snapshot)))
        for rank in result.ranked_themes:
            conn.execute('INSERT OR IGNORE INTO theme_rank_history VALUES (?, ?, ?, ?, ?, ?)',
                         (identity(rank.theme_id, rank.as_of, rank.mode), rank.theme_id, rank.as_of.isoformat(),
                          date, rank.mode, encoded(rank)))
        for rank, candidate in enumerate(result.entry_ready_top3, 1):
            symbol = candidate.get('symbol')
            if symbol:
                conn.execute('INSERT OR IGNORE INTO symbol_rank_history VALUES (?, ?, ?, ?, ?, ?)',
                             (identity(symbol, result.as_of, result.mode), symbol, result.as_of.isoformat(), date,
                              result.mode, encoded({**candidate, 'rank': rank})))
        for transition in result.transitions:
            conn.execute('INSERT OR IGNORE INTO recommendation_audit VALUES (?, ?, ?, ?, ?, ?)',
                         (transition.transition_id, transition.theme_id, transition.as_of.isoformat(), date,
                          transition.mode, encoded(transition)))
        for notification in result.notifications:
            conn.execute('INSERT OR IGNORE INTO radar_notifications VALUES (?, ?, ?, ?, ?, ?)',
                         (notification.fingerprint, notification.theme_id, notification.notification_type,
                          notification.as_of.isoformat(), notification.mode, encoded(notification)))
        conn.execute('INSERT INTO radar_state VALUES (?, ?, ?) ON CONFLICT(state_key) DO UPDATE SET as_of=excluded.as_of,payload_json=excluded.payload_json',
                     (state_key, result.as_of.isoformat(), encoded(engine_state)))
        conn.execute('INSERT OR IGNORE INTO radar_state_revisions VALUES (?, ?, ?, ?)',
                     (identity(state_key, result.as_of, engine_state), state_key, result.as_of.isoformat(), encoded(engine_state)))


def save_state(state_key, payload, as_of):
    from app.radar.models import aware
    as_of = aware(as_of)
    with connection() as conn:
        conn.execute('INSERT INTO radar_state VALUES (?, ?, ?) ON CONFLICT(state_key) DO UPDATE SET as_of=excluded.as_of,payload_json=excluded.payload_json',
                     (state_key, as_of.isoformat(), encoded(payload)))
        conn.execute('INSERT OR IGNORE INTO radar_state_revisions VALUES (?, ?, ?, ?)',
                     (identity(state_key, as_of, payload), state_key, as_of.isoformat(), encoded(payload)))


def read_state(state_key, as_of=None):
    if as_of is not None:
        from app.radar.models import aware
        as_of = aware(as_of)
    with connection() as conn:
        if as_of is None:
            row = conn.execute('SELECT payload_json FROM radar_state WHERE state_key=?', (state_key,)).fetchone()
        else:
            row = conn.execute('SELECT payload_json FROM radar_state_revisions WHERE state_key=? AND as_of<=? ORDER BY as_of DESC,rowid DESC LIMIT 1',
                               (state_key, as_of.isoformat())).fetchone()
    return json.loads(row['payload_json']) if row else None


def rotation_history(date, mode='live', as_of=None):
    with connection() as conn:
        rows = conn.execute('SELECT payload_json FROM recommendation_audit WHERE session_date=? AND mode=? ORDER BY as_of',
                            (date, mode)).fetchall()
    items = [json.loads(row['payload_json']) for row in rows]
    return [item for item in items if as_of is None or datetime.fromisoformat(item['as_of']) <= as_of]


def read_notifications(mode='live'):
    with connection() as conn:
        rows = conn.execute('''SELECT payload_json, fingerprint, (SELECT read_at FROM notification_read
          WHERE id=radar_notifications.fingerprint) AS read_at FROM radar_notifications
          WHERE mode=? ORDER BY as_of DESC LIMIT 50''', (mode,)).fetchall()
    return [{**json.loads(row['payload_json']), 'id': row['fingerprint'], 'read': bool(row['read_at'])} for row in rows]


def save_catalyst_notifications(events, after):
    with connection() as conn:
        for event in events:
            if event.get('mode') != 'live':
                continue
            available = datetime.fromisoformat(event.get('revision_available_at', event['first_seen_at']))
            if available < after or event.get('novelty_score', 0) <= 0:
                continue
            kind = 'NEW_DOMESTIC_CATALYST'
            fingerprint = identity(event['event_id'], kind, 'live')
            message = f"{event['title']} · {event.get('status', 'UNCONFIRMED')} · {event.get('relationship', 'UNVERIFIED')} · 자료 상태 {event.get('coverage_status', 'UNAVAILABLE')}"
            payload = {'fingerprint': fingerprint, 'theme_id': next(iter(event.get('theme_ids', [])), None),
                       'notification_type': kind, 'as_of': available.isoformat(), 'explanation': message,
                       'source_urls': [event['source_url']], 'mode': 'live', 'published_at': event['published_at'],
                       'first_seen_at': event['first_seen_at']}
            conn.execute('INSERT OR IGNORE INTO radar_notifications VALUES (?, ?, ?, ?, ?, ?)',
                         (fingerprint, payload['theme_id'], kind, available.isoformat(), 'live', encoded(payload)))


def save_provider_health(provider, health, as_of):
    with connection() as conn:
        conn.execute('INSERT INTO market_data_provider_health VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET updated_at=excluded.updated_at,payload_json=excluded.payload_json',
                     (provider, as_of.isoformat(), encoded(health)))


def read_provider_health():
    with connection() as conn:
        rows = conn.execute('SELECT provider,payload_json FROM market_data_provider_health').fetchall()
    return {row['provider']: json.loads(row['payload_json']) for row in rows}
