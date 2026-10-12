"""Guarded REST turnover loop sharing the ingestion owner's OAuth client.

This is polling, not a WebSocket execution stream. It never constructs a client,
authenticates while waiting for verification, or infers turnover from candles.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import inspect
import json
import math
import os
import time

from app.settings import read_themes, runtime_dir
from app.radar.collector import collect_rankings
from app.radar.config import read_v12_config
from app.radar.models import KST, aware
from app.radar.sessions import current_session, parsed_time
from app.radar.storage import save_provider_health
from app.radar.quotes import save_quotes, mark_quotes_unavailable


def _probe():
    try:
        value = json.loads((runtime_dir() / "toss-probe.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class FastTurnoverCollector:
    def __init__(self, owner, interval=None, config=None, *, clock=None):
        self.owner = owner
        self.config = config if config is not None else read_v12_config()
        self.config = self.config.get("flowtrade_v1_2", self.config)
        self.interval = float(interval if interval is not None else self.config.get("refresh", {}).get("quotes_seconds", 5))
        if not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("Turnover polling requires a positive finite interval")
        self.clock = clock or (lambda: datetime.now(KST))
        self._task = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._next_allowed = 0.
        self._quotes_next_allowed = 0.
        self._quote_failures = 0
        self._failures = 0
        self._last_overview = None
        self._health = {"status": "UNAVAILABLE", "provider": "Toss REST · integrated turnover",
                        "reason": "Verified authentication and venue samples are required before collection.",
                        "last_success_at": None, "latency_seconds": None, "coverage": None,
                        "poll_interval_seconds": self.interval, "measurement_definition": "Exact changes in provider cumulative turnover; not investor net buying"}

    def status(self):
        return json.loads(json.dumps(self._health))

    async def start(self):
        """Start one waiting task; no second client or discovery OAuth request."""
        if os.environ.get("TOSS_DATA_MODE", "auto").lower() == "demo":
            self._health.update(status="UNAVAILABLE", reason="Explicit DEMO mode disables all turnover HTTP collection.")
            return
        if self._task is not None and not self._task.done():
            return
        self._closed = False
        self._task = asyncio.create_task(self._run())

    async def close(self):
        self._closed = True
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # The ingestion owner alone closes its shared HTTP client.

    def _guard(self):
        if self._closed or getattr(self.owner, '_closing', False):
            return "Turnover collection is closed."
        if os.environ.get("TOSS_DATA_MODE", "auto").lower() == "demo":
            return "Explicit DEMO mode disables all turnover HTTP collection."
        if self.owner._blocked:
            return "Authentication is blocked; explicit owner recovery is required."
        if getattr(self.owner, "_verification_in_progress", False):
            return "The owner is verifying its bounded official sample contract; turnover polling waits."
        report = _probe()
        if report.get("authentication", {}).get("status") not in ("passed", "success", "ready"):
            return "Verified authentication is unavailable; no automatic OAuth request is made by this collector."
        if report.get("venue", {}).get("verified") is not True:
            return "KRX/NXT actual venue sample verification is incomplete."
        if self.owner.client is None or not callable(getattr(self.owner.client, "prices", None)):
            return "The shared official quote client is unavailable."
        if not self.config.get("feature_flags", {}).get("opening_flow_rotation", True):
            return "Opening flow rotation is disabled by configuration."
        return None

    async def _run(self):
        while not self._closed:
            cycle_start = time.monotonic()
            await self.poll_once()
            if self.owner._blocked:
                return
            # Start-to-start cadence, bounded by request duration and backoff.
            delay = max(0.05, self.interval - (time.monotonic() - cycle_start), self._quotes_next_allowed - time.monotonic())
            await asyncio.sleep(delay)

    def _record(self, status, reason, clock, **extras):
        self._health = {**self._health, "status": status, "reason": reason,
                        "last_success_at": None, "latency_seconds": None, **extras}
        save_provider_health("TOSS_TURNOVER", self._health, clock)
        return self.status()

    async def _prices(self, client, requested):
        quotes = {}
        for start in range(0, len(requested), 100):
            batch = requested[start:start + 100]
            values, conflicts = {}, set()
            for quote in await client.prices(batch):
                if not isinstance(quote, dict) or quote.get('symbol') not in batch:
                    continue
                symbol = quote['symbol']
                if symbol in values and values[symbol] != quote:
                    conflicts.add(symbol)
                values[symbol] = quote
            quotes.update({symbol: quote for symbol, quote in values.items() if symbol not in conflicts})
        return quotes

    async def _mirror_discovery(self, quotes, received, requested, calendar):
        """Hot selected stocks also update the independent discovery warehouse.

        This does not change either collector's universe or provider-health
        scope. A warehouse fault cannot suppress a successful quote monitor.
        """
        try:
            from app.discovery.storage import save_calendar, save_market_quotes
            await asyncio.to_thread(save_calendar, 'KR', calendar, received)
            await asyncio.to_thread(save_market_quotes, list(quotes.values()), received, expected_symbols=requested)
            self._health['discovery_mirror_status'] = 'READY'
        except asyncio.CancelledError:
            raise
        except Exception:
            self._health['discovery_mirror_status'] = 'UNAVAILABLE'

    async def poll_once(self):
        if self._lock.locked():
            return self.status()
        guard = self._guard()
        if guard:
            self._health.update(status="UNAVAILABLE", reason=guard, last_success_at=None)
            mark_quotes_unavailable(aware(self.clock()), guard, poll_interval_seconds=self.interval)
            return self.status()
        if time.monotonic() < self._quotes_next_allowed:
            return self.status()
        async with self._lock:
            phase = 'prices'
            try:
                client = self.owner.client
                calendar = await self.owner.cached("calendar_kr", 300, lambda: client.calendar("KR"))
                received = aware(self.clock())
                if calendar.get('today', {}).get('date') != received.date().isoformat():
                    # A cached previous-day calendar must not delay the first
                    # quote of a new KST day or require a service restart.
                    cache = getattr(self.owner, '_cache', None)
                    if isinstance(cache, dict):
                        cache.pop('calendar_kr', None)
                        calendar = await self.owner.cached('calendar_kr', 300, lambda: client.calendar('KR'))
                    else:
                        calendar = await client.calendar('KR')
                from app.radar.runtime import current_catalysts, current_overview, on_verified_calendar
                on_verified_calendar(calendar, received)
                registered = sorted({symbol for theme in read_themes() for symbol in theme["memberSymbols"]})
                news_symbols = sorted({symbol for event in current_catalysts(as_of=received)
                                       for symbol in event.get("tickers", []) if isinstance(symbol, str)})
                from app.discovery.storage import current_hot_symbols
                selected = await asyncio.to_thread(current_hot_symbols, 200, received)
                # Reserve another 100 slots for the freshly observed ranking.
                # Old manual chart requests cannot crowd out new market leaders.
                watch_candidates = list(dict.fromkeys(registered[:30] + news_symbols[:30] + selected + registered[30:] + news_symbols[30:]))
                requested = watch_candidates[:100]
                quotes = await self._prices(client, requested)
                received = aware(self.clock())
                save_quotes(list(quotes.values()), received, requested, self.interval)
                await self._mirror_discovery(quotes, received, requested, calendar)
                self._quotes_next_allowed, self._quote_failures = 0., 0
                # Ranking nulls, stale clocks, backoff and history insufficiency
                # never suppress independently received actual watchlist prices.
                phase = 'turnover'
                if time.monotonic() < self._next_allowed:
                    return self.status()
                if not callable(getattr(client, 'rankings_snapshot', None)):
                    return self._record('UNAVAILABLE', 'The typed ranking metadata method is unavailable; quote monitoring remains independent.', received)
                metadata = await client.rankings_snapshot("KR", count=100)
                received = aware(self.clock())
                if not isinstance(metadata, dict) or not metadata.get("rankedAt"):
                    return self._record("UNAVAILABLE", "Official ranking timestamp is unavailable; receipt time is not substituted.", received)
                ranked_at = parsed_time(metadata["rankedAt"])
                max_age = float(self.config.get("data", {}).get("max_live_quote_age_seconds", 15))
                age = (received - ranked_at).total_seconds()
                if age < 0 or age > max_age:
                    return self._record("UNAVAILABLE" if age < 0 else "STALE",
                                        "Ranking provider time is in the future." if age < 0 else "Ranking provider time exceeds the live freshness limit.", received,
                                        latency_seconds=max(0, age))
                session = current_session(calendar, ranked_at)
                if session is None:
                    return self._record("UNAVAILABLE", "No verified active market session matches the provider time.", received)
                try:
                    from app.discovery.storage import save_global_rankings
                    await asyncio.to_thread(save_global_rankings, metadata, received, country='KR')
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._health['discovery_ranking_mirror_status'] = 'UNAVAILABLE'
                rows = metadata.get("rankings")
                if not isinstance(rows, list):
                    return self._record("UNAVAILABLE", "Official ranking rows are unavailable.", received)
                ranking_symbols = list(dict.fromkeys(row["symbol"] for row in rows if isinstance(row, dict) and isinstance(row.get("symbol"), str)))[:100]
                extras = [symbol for symbol in ranking_symbols if symbol not in requested][:max(0, 200 - len(requested))]
                remaining = [symbol for symbol in watch_candidates if symbol not in requested and symbol not in extras]
                extras += remaining[:max(0, 200 - len(requested) - len(extras))]
                requested += extras
                if not requested:
                    return self._record("UNAVAILABLE", "No observed ranking or registered symbols are available.", received)
                quotes.update(await self._prices(client, extras))
                # Timestamp freshness is checked after the complete HTTP batch;
                # a slow response never inherits its earlier request time.
                received = aware(self.clock())
                if extras:
                    save_quotes(list(quotes.values()), received, requested, self.interval)
                    await self._mirror_discovery(quotes, received, requested, calendar)
                age = (received - ranked_at).total_seconds()
                if age < 0 or age > max_age:
                    return self._record("STALE", "Ranking became stale while quotes were fetched.", received, latency_seconds=max(0, age))
                fresh_quotes = {}
                for symbol, quote in quotes.items():
                    quote_at = parsed_time(quote["timestamp"]) if quote.get("timestamp") else None
                    if quote.get("currency") == "KRW" and quote_at and 0 <= (received - quote_at).total_seconds() <= max_age:
                        fresh_quotes[symbol] = quote
                if not any(symbol in fresh_quotes for symbol in ranking_symbols):
                    return self._record("STALE" if quotes else "UNAVAILABLE",
                                        "No fresh KRW quote covers the observed ranking universe; no turnover interval is recorded.", received,
                                        coverage={"symbols_seen": 0, "symbols_expected": len(ranking_symbols),
                                                  "fresh_quotes_seen": len(fresh_quotes), "quotes_requested": len(requested),
                                                  "scope": "Queried ranking universe, not the entire market"})
                observations = collect_rankings(metadata, fresh_quotes, calendar, received)
                self._failures, self._next_allowed = 0, 0.
                coverage = {"symbols_seen": len(observations), "symbols_expected": len(ranking_symbols),
                            "fresh_quotes_seen": len(fresh_quotes), "quotes_requested": len(requested),
                            "registered_symbols": len(set(registered) & set(requested)),
                            "news_linked_symbols": len(set(news_symbols) & set(requested)),
                            "scope": "Queried KR trading-amount ranking universe; extra quotes do not supply turnover; not the entire market"}
                status = "LIVE" if observations else "PARTIAL"
                reason = "Exact integrated turnover differences observed; not investor net buying." if observations else "BASELINE_FIRST_OBSERVATION: two distinct provider counters are required; no interval turnover is invented."
                result = self._record(status, reason, received, last_success_at=ranked_at.isoformat(), latency_seconds=age,
                                      coverage=coverage, calendar=calendar, session_start=session.start.isoformat(), session_id=session.session_id,
                                      retry_after_seconds=None, http_status=None, condition=None)
                if observations and (self._last_overview is None or time.monotonic() - self._last_overview >= self.config.get("refresh", {}).get("theme_snapshots_seconds", 60)):
                    # Synchronous engine/storage execution cannot overlap another
                    # event-loop read; its own point-in-time lock also protects it.
                    try:
                        current_overview(as_of=received, persist=True)
                        self._health.update(audit_status="READY", audit_reason=None)
                    except Exception:
                        self._health.update(audit_status="UNAVAILABLE", audit_reason="Point-in-time rotation audit could not be updated; turnover observations remain separate.")
                    self._last_overview = time.monotonic()
                    save_provider_health("TOSS_TURNOVER", self._health, received)
                    result = self.status()
                return result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                received = aware(self.clock())
                condition = getattr(error, "condition", None)
                status = getattr(error, "http_status", None)
                code = getattr(error, "error_code", None)
                if status in (401, 403) or condition in ("ip_not_allowed", "invalid_client", "access_denied", "authentication_rejected") or code in ("invalid_client", "access_denied"):
                    self.owner._blocked = True
                    self.owner._last_error = "Official turnover authentication was refused; explicit connection recovery is required."
                    mark_quotes_unavailable(received, 'Official quote authentication was refused; automatic retries are stopped.',
                                            http_status=status if status in (401, 403) else None, poll_interval_seconds=self.interval)
                    hook = getattr(self.owner, "latch_auth_failure", None)
                    if callable(hook):
                        try:
                            result = hook(error)
                            if inspect.isawaitable(result):
                                await result
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass  # In-memory latch still stops both owner loops.
                    return self._record("UNAVAILABLE", "Official turnover authentication was refused; automatic retries are stopped.", received,
                                        http_status=status if status in (401, 403) else None,
                                        condition=condition if condition in ("ip_not_allowed", "invalid_client", "access_denied", "authentication_rejected") else "authentication_rejected")
                if phase == 'prices':
                    self._quote_failures += 1
                    failures = self._quote_failures
                else:
                    self._failures += 1
                    failures = self._failures
                retry = max(self.interval, min(300., 30. * 2 ** min(failures - 1, 3)))
                retry_after = getattr(error, "retry_after_seconds", None)
                if isinstance(retry_after, (int, float)) and math.isfinite(retry_after) and retry_after >= 0:
                    retry = max(retry, min(300., retry_after))
                if phase == 'prices':
                    self._quotes_next_allowed = time.monotonic() + retry
                    mark_quotes_unavailable(received, 'Official quote collection failed; cached actual prices are not LIVE.',
                                            poll_interval_seconds=self.interval, retry_after_seconds=retry)
                else:
                    self._next_allowed = time.monotonic() + retry
                return self._record("DELAYED", "Official provider rate limited requests; collection is backed off." if status == 429 else "Official turnover collection failed; bounded retry backoff is active.", received,
                                    retry_after_seconds=retry, http_status=status if status in (429, 500, 502, 503, 504) else None)
