"""Incremental catalog scanner using the ingestion owner's one official client.

Cold price sweeps, classification and bounded chart enrichment have independent
cadences. Provider capability failures cannot disable healthy hot quote reads.
"""
import asyncio
from datetime import datetime, timedelta
import inspect
import json
import math
import os
import time

from app.radar.config import read_v12_config
from app.radar.models import KST, aware
from app.radar.sessions import parsed_time
from app.settings import runtime_dir
from . import storage


class _AuthStopped(Exception):
    pass


def normalized_candles(rows, interval, *, as_of=None):
    """Preserve provider minute END times; never derive turnover from OHLCV."""
    result = []
    for row in rows:
        if row.get('currency') != 'KRW':
            raise ValueError('Domestic candle currency is not KRW')
        stamp = parsed_time(row['timestamp'])
        if as_of is not None:
            clock = aware(as_of)
            if (interval == '1d' and stamp.date() > clock.date()) or (interval == '1m' and stamp > clock + timedelta(minutes=1)):
                raise ValueError('Future provider candle outside the current forming bar')
        values = {name: float(row[source]) for name, source in
                  [('open', 'openPrice'), ('high', 'highPrice'), ('low', 'lowPrice'), ('close', 'closePrice'), ('volume', 'volume')]}
        if any(not math.isfinite(value) for value in values.values()) or values['volume'] < 0 or min(values[name] for name in ('open', 'high', 'low', 'close')) <= 0:
            raise ValueError('Invalid observed candle value')
        if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
            raise ValueError('Invalid observed candle range')
        result.append({'time': stamp.date().isoformat() if interval == '1d' else stamp.isoformat(), **values,
                       'source': 'toss', 'venue': 'CONSOLIDATED', 'integrated': True})
        if interval == '1m' and as_of is not None and stamp > aware(as_of):
            result[-1]['status'] = 'PARTIAL'
    by_time = {}
    for row in result:
        if row['time'] in by_time and row != by_time[row['time']]:
            raise ValueError('Conflicting candle revisions in one response')
        by_time[row['time']] = row
    return [by_time[key] for key in sorted(by_time)]


class WholeMarketScanner:
    def __init__(self, owner, config=None, *, clock=None):
        self.owner = owner
        raw = config if config is not None else read_v12_config()
        self.config = raw.get('flowtrade_v1_2', raw)
        self.options = self.config.get('discovery', {})
        self.interval = float(self.options.get('scan_batch_seconds', 2))
        self.batch_size = min(100, int(self.options.get('quote_batch_size', 100)))
        if self.interval < 1 or not math.isfinite(self.interval) or self.batch_size < 1:
            raise ValueError('Invalid bounded discovery cadence')
        self.clock = clock or (lambda: datetime.now(KST))
        self._task, self._closed = None, False
        self._lock = asyncio.Lock()
        self._blocked_until = {}
        self._capabilities = {}
        self._metadata_day = None
        self._catalog_queue = list(storage.MARKETS)
        self._catalog_retry = []
        self._member_queue = []
        self._master_queue = []
        self._sweep_symbols, self._offset = [], 0
        self._sweep_started, self._sweep_finished = None, None
        self._last_rankings = 0.
        self._last_sector_rankings = 0.
        self._last_predictions = None
        self._sector_queue = [(country, duration) for country in ('KR', 'US') for duration in ('1d', '1w')]

    async def start(self):
        if os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
            return
        if self._task is None or self._task.done():
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
        # The HTTP client/token belongs to the ingestion owner alone.

    def _guard(self):
        if self._closed or getattr(self.owner, '_closing', False) or os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo' or self.owner._blocked:
            return False
        if getattr(self.owner, '_verification_in_progress', False) or self.owner.client is None:
            return False
        try:
            report = json.loads((runtime_dir() / 'toss-probe.json').read_text())
            return report.get('authentication', {}).get('status') in ('passed', 'success', 'ready') and report.get('venue', {}).get('verified') is True
        except (OSError, ValueError, TypeError):
            return False

    async def _run(self):
        while not self._closed:
            began = time.monotonic()
            await self.poll_once()
            if self.owner._blocked:
                return
            await asyncio.sleep(max(.05, self.interval - (time.monotonic() - began)))

    async def _call(self, capability, operation):
        if time.monotonic() < self._blocked_until.get(capability, 0):
            return None
        try:
            value = await operation()
            self._capabilities[capability] = {'status': 'AVAILABLE'}
            return value
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status, condition = getattr(error, 'http_status', None), getattr(error, 'condition', None)
            code = getattr(error, 'error_code', None)
            # A NEW endpoint's generic permission 403/404 is not proof that the
            # already-working quote token/client failed. Never reissue for it.
            if status == 401 or condition in ('ip_not_allowed', 'invalid_client', 'access_denied', 'authentication_rejected') or code in ('invalid_client', 'access_denied'):
                self.owner._blocked = True
                hook = getattr(self.owner, 'latch_auth_failure', None)
                if callable(hook):
                    try:
                        result = hook(error)
                        if inspect.isawaitable(result):
                            await result
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass  # The in-memory owner latch still stops all loops.
                raise _AuthStopped() from None
            retry = 86400 if status in (403, 404) else 30
            retry_after = getattr(error, 'retry_after_seconds', None)
            if isinstance(retry_after, (int, float)) and math.isfinite(retry_after) and retry_after >= 0:
                retry = max(retry, min(300, retry_after))
            self._blocked_until[capability] = time.monotonic() + retry
            self._capabilities[capability] = {'status': 'UNAVAILABLE' if status in (403, 404) else 'DELAYED',
                'http_status': status if status in (400, 403, 404, 429, 500, 502, 503, 504) else None,
                'reason': 'Provider capability unavailable or temporarily delayed; no fabricated data.', 'retry_after_seconds': retry}
            return None

    async def _store(self, function, *args, **kwargs):
        # Large daily catalogs/relations cannot block the five-second hot loop.
        return await asyncio.to_thread(function, *args, **kwargs)

    async def _invoke(self, capability, method, *args, **kwargs):
        if not callable(method):
            self._capabilities[capability] = {'status': 'UNAVAILABLE', 'reason': 'The shared client does not expose this verified provider capability.'}
            self._blocked_until[capability] = time.monotonic() + 86400
            return None
        return await self._call(capability, lambda: method(*args, **kwargs))

    async def _metadata(self, client, now):
        day = now.date().isoformat()
        if self._metadata_day != day:
            self._metadata_day = day
            self._catalog_queue = list(storage.MARKETS)
            self._catalog_retry = []
            self._member_queue, self._master_queue = [], []
            self._sectors_pending = True
            self._sector_queue = [(country, duration) for country in ('KR', 'US') for duration in ('1d', '1w')]
        due = [market for market in self._catalog_retry if time.monotonic() >= self._blocked_until.get('catalog:' + market, 0)]
        if not self._catalog_queue and due:
            self._catalog_queue = due
            self._catalog_retry = [market for market in self._catalog_retry if market not in due]
        if self._catalog_queue:
            market = self._catalog_queue.pop(0)
            method = getattr(client, 'stock_universe', None)
            rows = await self._invoke('catalog:' + market, method, market, status='ACTIVE', security_type='STOCK', common_share=True)
            if rows is not None:
                await self._store(storage.save_catalog, rows, aware(self.clock()), market=market)
                self._master_queue += [row['symbol'] for row in rows if storage._symbol(row.get('symbol'))]
            elif callable(method):
                self._catalog_retry.append(market)
            return
        if self._sectors_pending and time.monotonic() >= self._blocked_until.get('sectors', 0):
            method = getattr(client, 'sectors', None)
            rows = await self._invoke('sectors', method)
            if rows is not None:
                self._sectors_pending = False
                await self._store(storage.save_sectors, rows, aware(self.clock()))
                maximum_depth = int(self.options.get('membership_max_depth', 1))
                self._member_queue = [row['sectorId'] for row in rows if row.get('depth', 3) <= maximum_depth]
            return
        if self._master_queue and time.monotonic() >= self._blocked_until.get('master', 0):
            symbols = self._master_queue[:100]
            rows = await self._invoke('master', getattr(client, 'stock_master', None), symbols)
            if rows is not None:
                self._master_queue = self._master_queue[len(symbols):]
                await self._store(storage.save_stock_master, rows, aware(self.clock()))
            return
        if self._member_queue:
            due = next((index for index, sector in enumerate(self._member_queue)
                        if time.monotonic() >= self._blocked_until.get('sector_members:' + sector, 0)), None)
            if due is None:
                return
            sector = self._member_queue[due]
            method = getattr(client, 'sector_stocks', None)
            rows = await self._invoke('sector_members:' + sector, method, sector, 'KR')
            if rows is not None:
                self._member_queue.pop(due)
                await self._store(storage.save_memberships, sector, rows, aware(self.clock()), country='KR')

    async def _analysis(self, client, now):
        budget = max(0, min(2, int(self.options.get('analysis_per_cycle', 2))))
        jobs = [job for job in storage.pending_analysis(limit=200)
                if time.monotonic() >= self._blocked_until.get('candles:' + job['symbol'], 0)][:budget]
        for job in jobs:
            symbol = job['symbol']
            daily = await self._call('candles:' + symbol, lambda: client.candles(symbol, '1d', count=90))
            daily_seen = aware(self.clock())
            minute = await self._call('candles:' + symbol, lambda: client.candles(symbol, '1m', count=200))
            minute_seen = aware(self.clock())
            normalized, reasons = {}, []
            for key, rows, interval, receipt in [('daily', daily, '1d', daily_seen), ('minute', minute, '1m', minute_seen)]:
                try:
                    normalized[key] = normalized_candles(rows, interval, as_of=receipt) if rows is not None else []
                except (ValueError, KeyError, TypeError):
                    normalized[key] = []
                if not normalized[key]:
                    reasons.append(key.upper() + '_UNAVAILABLE')
            reason = '; '.join(reasons) or None
            if any(normalized.values()):
                await self._store(storage.save_analysis_inputs, symbol, normalized['daily'], normalized['minute'], daily_seen, minute_seen, reason=reason)
                storage.finish_analysis(symbol, status='PARTIAL' if reasons else 'READY', reason=reason)
            else:
                storage.finish_analysis(symbol, status='UNAVAILABLE', reason='Official candle data unavailable or invalid; no synthetic chart fallback.')

    async def poll_once(self):
        if self._lock.locked() or not self._guard():
            return {'status': 'UNAVAILABLE', 'reason': 'Shared authentication/venue verification required; discovery makes no OAuth request.'}
        async with self._lock:
            now, client = aware(self.clock()), self.owner.client
            try:
                calendar = await self._call('calendar:KR', lambda: self.owner.cached('calendar_kr', 300, lambda: client.calendar('KR')))
                if calendar is not None and calendar.get('today', {}).get('date') != aware(self.clock()).date().isoformat():
                    cache = getattr(self.owner, '_cache', None)
                    if isinstance(cache, dict):
                        cache.pop('calendar_kr', None)
                    # A verified previous-day calendar is never reused to label
                    # the new KST day's quotes LIVE, even inside the 300s TTL.
                    calendar = await self._call('calendar:KR', lambda: client.calendar('KR'))
                if calendar is not None:
                    await self._store(storage.save_calendar, 'KR', calendar, aware(self.clock()))
                if not hasattr(self, '_us_calendar_at') or time.monotonic() - self._us_calendar_at >= 300:
                    us_calendar = await self._call('calendar:US', lambda: client.calendar('US'))
                    if us_calendar is not None:
                        await self._store(storage.save_calendar, 'US', us_calendar, aware(self.clock()))
                    self._us_calendar_at = time.monotonic()
                await self._metadata(client, now)
                if self._sector_queue:
                    country, duration = self._sector_queue.pop(0)
                    method = getattr(client, 'sector_rankings', None)
                    rows = await self._invoke(f'sector_ranking:{country}:{duration}', method, country, duration=duration)
                    if rows is not None:
                        await self._store(storage.save_sector_rankings, country, rows, aware(self.clock()), duration=duration)
                elif time.monotonic() - self._last_sector_rankings >= 60:
                    self._sector_queue = [(country, duration) for country in ('KR', 'US') for duration in ('1d', '1w')]
                    self._last_sector_rankings = time.monotonic()
                if time.monotonic() - self._last_rankings >= 30 or self._last_rankings == 0:
                    method = getattr(client, 'rankings_snapshot', None)
                    ranking = await self._invoke('global_rankings', method, 'KR', count=100)
                    if ranking is not None:
                        await self._store(storage.save_global_rankings, ranking, aware(self.clock()))
                        for row in ranking.get('rankings', [])[:30]:
                            await self._store(storage.enqueue_analysis, row['symbol'], priority=10, as_of=aware(self.clock()))
                    self._last_rankings = time.monotonic()
                snapshot = await asyncio.to_thread(storage.discovery_snapshot, aware(self.clock()))
                available = [row['symbol'] for row in snapshot['stocks']]
                if not self._sweep_symbols or self._offset >= len(self._sweep_symbols):
                    self._sweep_symbols, self._offset = available, 0
                    self._sweep_started = now.isoformat()
                batch = self._sweep_symbols[self._offset:self._offset + self.batch_size]
                if batch:
                    rows = await self._call('scan_quotes', lambda: client.prices(batch))
                    if rows is not None:
                        await self._store(storage.save_market_quotes, rows, aware(self.clock()), expected_symbols=batch)
                        self._offset += len(batch)
                        if self._offset >= len(self._sweep_symbols):
                            self._sweep_finished = aware(self.clock()).isoformat()
                await self._analysis(client, now)
                health = {'status': 'PARTIAL' if any(row['status'] != 'AVAILABLE' for row in self._capabilities.values()) else 'SCANNING',
                    'provider': 'Toss official catalog / REST prices', 'scope': storage.SCOPE,
                    'started_at': self._sweep_started, 'finished_at': self._sweep_finished,
                    'attempted_in_sweep': self._offset, 'sweep_symbols': len(self._sweep_symbols),
                    'batch_size': self.batch_size, 'batch_interval_seconds': self.interval,
                    'metadata_day': self._metadata_day, 'capabilities': self._capabilities,
                    'membership_remaining': len(self._member_queue), 'reason': 'Per-symbol provider freshness is reported; a completed price sweep does not imply all quotes are LIVE.'}
                await self._store(storage.save_scan_health, health, aware(self.clock()))
                if self._last_predictions is None or time.monotonic() - self._last_predictions >= 60:
                    try:
                        from .runtime import refresh_discovery_predictions
                        await asyncio.to_thread(refresh_discovery_predictions, as_of=aware(self.clock()))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Missing prediction evidence never changes quote or
                        # catalog availability and cannot manufacture a theme.
                        health['prediction_refresh_status'] = 'UNAVAILABLE'
                    self._last_predictions = time.monotonic()
                return health
            except _AuthStopped:
                health = {'status': 'BLOCKED', 'reason': 'Official shared authentication was refused; explicit owner recovery required.'}
                await self._store(storage.save_scan_health, health, aware(self.clock()))
                return health
            except asyncio.CancelledError:
                raise
            except Exception:
                health = {'status': 'PARTIAL', 'reason': 'Discovery validation or storage failed; observed data remain separately labelled.'}
                await self._store(storage.save_scan_health, health, aware(self.clock()))
                return health
