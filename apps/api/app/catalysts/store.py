"""Append-only event revisions; current rows are a disposable read projection."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from .models import CatalystEvent, ProviderCursor, ProviderHealth, aware


CATALYST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS catalyst_events (
 event_id TEXT PRIMARY KEY, story_key TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
 published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 event_json TEXT NOT NULL, mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalyst_revisions (
 revision_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES catalyst_events(event_id),
 provider TEXT NOT NULL, provider_item_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 available_at TEXT NOT NULL, published_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
 event_json TEXT NOT NULL, UNIQUE(provider,provider_item_id,content_hash));
CREATE INDEX IF NOT EXISTS catalyst_revision_time ON catalyst_revisions(event_id,available_at);
CREATE INDEX IF NOT EXISTS catalyst_provider_item ON catalyst_revisions(provider,provider_item_id);
CREATE TABLE IF NOT EXISTS catalyst_evidence (
 evidence_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES catalyst_events(event_id),
 revision_id TEXT NOT NULL REFERENCES catalyst_revisions(revision_id),
 source_url TEXT NOT NULL, claim TEXT NOT NULL, strength REAL NOT NULL, source_kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalyst_cursors (
 provider TEXT PRIMARY KEY, cursor_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalyst_provider_health (
 provider TEXT PRIMARY KEY, health_json TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CatalystStore:
    def __init__(self, connection_factory: Callable | None = None):
        self.connection_factory = connection_factory

    def connection(self):
        if self.connection_factory is not None:
            return self.connection_factory()
        # Root migration imports CATALYST_SCHEMA_SQL; defer the reverse import.
        from app.radar.storage import connection
        return connection()

    def cursor(self, provider: str) -> ProviderCursor:
        with self.connection() as conn:
            row = conn.execute("SELECT cursor_json FROM catalyst_cursors WHERE provider=?", (provider,)).fetchone()
        return ProviderCursor.model_validate_json(row["cursor_json"]) if row else ProviderCursor()

    def record_health(self, health: ProviderHealth, now: datetime):
        with self.connection() as conn:
            conn.execute("INSERT INTO catalyst_provider_health VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET health_json=excluded.health_json,updated_at=excluded.updated_at",
                         (health.provider, health.model_dump_json(), aware(now).isoformat()))

    def current(self, story_key: str, provider: str, item_id: str, revises_item: str | None = None) -> CatalystEvent | None:
        with self.connection() as conn:
            # Correction IDs may change. Explicit predecessor references win.
            row = conn.execute("SELECT e.event_json FROM catalyst_revisions r JOIN catalyst_events e ON e.event_id=r.event_id WHERE r.provider=? AND r.provider_item_id=? ORDER BY r.rowid DESC LIMIT 1",
                               (provider, revises_item or item_id)).fetchone()
            if row is None:
                row = conn.execute("SELECT event_json FROM catalyst_events WHERE story_key=?", (story_key,)).fetchone()
        return CatalystEvent.model_validate_json(row["event_json"]) if row else None

    def seen_content(self, provider: str, item_id: str, content_hash: str) -> bool:
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM catalyst_revisions WHERE provider=? AND provider_item_id=? AND content_hash=?", (provider, item_id, content_hash)).fetchone() is not None

    def save_batch(self, rows: list[tuple[CatalystEvent, str, str, str, str]], provider: str,
                   cursor: ProviderCursor, now: datetime):
        """The cursor advances in the same transaction as all accepted events."""
        with self.connection() as conn:
            for event, story_key, raw_provider, content_hash, raw_item_id in rows:
                if conn.execute("SELECT 1 FROM catalyst_revisions WHERE revision_id=?", (event.revision_id,)).fetchone():
                    continue
                payload = event.model_dump_json()
                conn.execute("INSERT INTO catalyst_events VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET provider=excluded.provider,published_at=excluded.published_at,first_seen_at=excluded.first_seen_at,updated_at=excluded.updated_at,event_json=excluded.event_json",
                             (event.event_id, story_key, event.provider, event.published_at.isoformat(), event.first_seen_at.isoformat(), event.updated_at.isoformat(), payload, event.mode))
                conn.execute("INSERT INTO catalyst_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                             (event.revision_id, event.event_id, raw_provider, raw_item_id, content_hash,
                              event.revision_available_at.isoformat(), event.revision_published_at.isoformat(), event.ingested_at.isoformat(), payload))
                for index, evidence in enumerate(event.evidence):
                    conn.execute("INSERT INTO catalyst_evidence VALUES(?,?,?,?,?,?,?)",
                                 (digest(event.revision_id + ':' + str(index)), event.event_id, event.revision_id,
                                  evidence.source_url, evidence.claim, evidence.strength, evidence.source_kind.value))
            conn.execute("INSERT INTO catalyst_cursors VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor_json=excluded.cursor_json,updated_at=excluded.updated_at",
                         (provider, cursor.model_dump_json(), aware(now).isoformat()))

    def events(self, *, since: datetime | None = None, as_of: datetime | None = None,
               verified: bool | None = None, mode: str = "live") -> list[CatalystEvent]:
        cutoff = aware(as_of or datetime.now(timezone.utc))
        lower = aware(since) if since is not None else None
        with self.connection() as conn:
            rows = conn.execute("SELECT r.event_json FROM catalyst_revisions r JOIN catalyst_events e ON e.event_id=r.event_id WHERE e.mode=? AND r.available_at<=? ORDER BY r.available_at,r.rowid", (mode, cutoff.isoformat())).fetchall()
        selected = {}
        for row in rows:
            event = CatalystEvent.model_validate_json(row["event_json"])
            if max(event.published_at, event.first_seen_at, event.revision_published_at, event.revision_available_at) <= cutoff:
                selected[event.event_id] = event
        result = list(selected.values())
        if lower:
            result = [event for event in result if max(event.published_at, event.first_seen_at, event.revision_available_at) >= lower]
        if verified is not None:
            result = [event for event in result if (event.status.value in {"VERIFIED", "CORRECTED"}
                      and event.source_kind.value != "UNVERIFIED"
                      and event.relationship.value in {"DIRECT_VERIFIED", "NEWS_REPORTED"}) == verified]
        return sorted(result, key=lambda event: (event.revision_available_at, event.event_id), reverse=True)

    def revisions(self, event_id: str, as_of: datetime | None = None) -> list[CatalystEvent]:
        cutoff = aware(as_of or datetime.now(timezone.utc))
        with self.connection() as conn:
            rows = conn.execute("SELECT event_json FROM catalyst_revisions WHERE event_id=? AND available_at<=? ORDER BY available_at,rowid", (event_id, cutoff.isoformat())).fetchall()
        return [CatalystEvent.model_validate_json(row["event_json"]) for row in rows]
