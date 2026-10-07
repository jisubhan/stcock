"""Official read-only ingestion and the all-or-nothing live/demo boundary.

An OAuth IP/credential refusal is latched: background tasks never repeat it.
Only an explicit refresh action can retry after the user fixes external settings.
No normalized snapshot contains credentials, tokens, or raw broker responses.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from statistics import mean
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.fixtures import metadata as demo_metadata, stock_samples
from app.settings import ROOT, StrategyConfig, read_strategy, read_themes, runtime_dir
from app.strategy.engine import analyze_stock, calculate_themes
from .live_bundle import LiveBundleError, load_live_bundle, save_live_bundle


KST = ZoneInfo('Asia/Seoul')
SESSION_LABELS = {'preMarket': ('NXT_PRE_MARKET', 'NXT 프리마켓'),
                  'regularMarket': ('KRX_REGULAR', 'KRX 정규장'),
                  'afterMarket': ('NXT_AFTER_MARKET', 'NXT 애프터마켓')}


def safe_json(name: str) -> dict:
    try:
        data = json.loads((runtime_dir() / name).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('timezone missing')
    return result


def number(value: Any, *, positive=False, nonnegative=False) -> float:
    if isinstance(value, bool):
        raise ValueError('not a number')
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError('invalid number')
    return result


def session_for(calendar: dict, now: datetime) -> tuple[str, str, bool]:
    today = calendar['today']
    if today['date'] != now.astimezone(KST).date().isoformat():
        raise ValueError('calendar day is not current')
    for key, times in (today.get('integrated') or {}).items():
        if key in SESSION_LABELS and times and timestamp(times['startTime']) <= now < timestamp(times['endTime']):
            name, label = SESSION_LABELS[key]
            return name, label, True
    return 'CLOSED', '휴장 / 장 종료', False


def normalized_candles(rows: list[dict], interval: str) -> list[dict]:
    output = []
    for row in rows:
        if row.get('currency') != 'KRW':
            raise ValueError('domestic candle currency mismatch')
        observed = timestamp(row['timestamp'])
        output.append({'time': observed.astimezone(KST).date().isoformat() if interval == '1d' else observed.isoformat(),
                       'open': number(row['openPrice'], positive=True), 'high': number(row['highPrice'], positive=True),
                       'low': number(row['lowPrice'], positive=True), 'close': number(row['closePrice'], positive=True),
                       'volume': number(row['volume'], nonnegative=True), 'interval': interval,
                       'source': 'toss', 'integrated': True, 'venue': 'integrated'})
    return sorted(output, key=lambda candle: timestamp(candle['time']) if interval == '1m' else candle['time'])


@dataclass
class AnalysisSnapshot:
    samples: list[dict]
    themes: list[dict]
    stocks: list[dict]
    config: StrategyConfig
    meta: dict
    market: dict | None


def analysis_snapshot() -> AnalysisSnapshot:
    config, configured_themes = read_strategy(), read_themes()
    requested_us = {ticker for theme in configured_themes for ticker in theme['usTickers']}
    quality = load_live_bundle(config, required_us_tickers=requested_us)
    allowed = os.environ.get('TOSS_DATA_MODE', 'auto').lower() != 'demo'
    auth_blocked = safe_json('toss-probe.json').get('authentication', {}).get('condition') in ('ip_not_allowed', 'invalid_client', 'access_denied')
    if allowed and quality.ready and not auth_blocked:
        bundle = quality.bundle
        assert bundle is not None
        samples = bundle.stock_dicts()
        themes = calculate_themes(samples, configured_themes, config, us_returns=bundle.usReturns)
        meta = {'mode': 'live', 'source': 'toss', 'asOf': bundle.asOf.isoformat(),
                'venueLabel': 'Toss KRX+NXT 통합 시세 · 공식 문서와 실제 샘플 검증',
                'liveTrading': False, 'stale': False, 'degraded': False,
                'dataQuality': {'status': 'ready', 'reason': None}}
        market = bundle.market.model_dump(mode='json')
    else:
        samples = stock_samples()
        themes = calculate_themes(samples, configured_themes, config)
        meta = {**demo_metadata(), 'stale': quality.status == 'stale',
                'degraded': allowed and bool(os.environ.get('TOSS_CLIENT_ID') or os.environ.get('TOSS_CLIENT_SECRET')),
                'dataQuality': {'status': quality.status, 'reason': quality.error}}
        market = None
    stocks = sorted((analyze_stock(sample, themes, config) for sample in samples),
                    key=lambda stock: (stock['eligible'], stock['score'], stock['tradedValue']), reverse=True)
    for rank, stock in enumerate(stocks, 1):
        stock['rank'] = rank
    return AnalysisSnapshot(samples, themes, stocks, config, meta, market)


class IngestionService:
    """One in-process OAuth owner, REST candidate stage, bounded quote refresh."""
    def __init__(self, client=None):
        self.client = client
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._blocked = False
        self._last_error: str | None = None

    def status(self) -> dict:
        report = safe_json('toss-probe.json')
        auth = report.get('authentication', {})
        venue = report.get('venue', safe_json('toss-venue-evidence.json'))
        quality = load_live_bundle(read_strategy(), required_us_tickers={t for theme in read_themes() for t in theme['usTickers']})
        docs_present = (ROOT / 'config' / 'toss-openapi-contract.json').exists()
        condition = auth.get('condition')
        messages = {'ip_not_allowed': '토스증권이 현재 호출 IP를 허용하지 않아 OAuth 인증을 거부했습니다. 토스 개발자 설정에서 이 환경의 토스 호출 IP를 등록한 뒤 다시 확인하세요.',
                    'invalid_client': '토스증권이 서버 인증 설정을 거부했습니다.',
                    'missing_credentials': '서버의 Toss 인증 설정이 필요합니다.'}
        blockers = []
        if condition in messages:
            blockers.append(messages[condition])
        if self._last_error:
            blockers.append(self._last_error)
        if not docs_present:
            blockers.append('현재 공식 API 응답 계약을 확인할 수 없습니다.')
        if not venue.get('verified'):
            blockers.append('KRX/NXT 가격·일봉 및 프리마켓·애프터마켓 실제 샘플 검증이 완료되지 않았습니다.')
        if not quality.ready:
            blockers.append(quality.error)
        credentials = {name: bool(os.environ.get(name)) for name in ('TOSS_CLIENT_ID', 'TOSS_CLIENT_SECRET', 'TOSS_ACCESS_TOKEN')}
        if not (credentials['TOSS_CLIENT_ID'] and credentials['TOSS_CLIENT_SECRET']):
            if messages['missing_credentials'] not in blockers:
                blockers.insert(0, messages['missing_credentials'])
        safe_auth = {key: auth[key] for key in ('status', 'http_status', 'condition', 'error_code', 'schema_status') if key in auth}
        # Only enumerated diagnostic fields are exposed. No raw response or
        # exception string ever crosses this boundary.
        safe_samples = {name: {key: item[key] for key in ('status', 'http_status', 'condition', 'schema_status', 'count') if key in item}
                        for name, item in report.get('samples', {}).items() if isinstance(item, dict)}
        auth_blocked = self._blocked or condition in ('ip_not_allowed', 'invalid_client', 'access_denied')
        return {'ready': quality.ready and not auth_blocked, 'brokerMode': 'live' if quality.ready and not auth_blocked else 'blocked',
                'credentials': credentials, 'manifest_present': docs_present,
                'venue_verified': bool(venue.get('verified')), 'live_orders_enabled': False,
                'blockers': [message for message in blockers if message],
                'authStatus': auth.get('status', 'not_attempted'), 'schemaStatus': 'documented' if docs_present else 'not_checked',
                'sampleStatus': 'verified' if venue.get('verified') else 'pending',
                'verificationStatus': 'verified' if venue.get('verified') else 'pending',
                'dataQuality': {'status': quality.status, 'reason': messages.get(condition) or quality.error},
                'lastProbe': {'checkedAt': report.get('checked_at'), 'documentVersion': report.get('document_version'),
                              'calls': report.get('calls', 0), 'authentication': safe_auth, 'samples': safe_samples},
                'httpStatus': auth.get('http_status'), 'errorCode': auth.get('error_code'),
                'condition': condition, 'callerIp': None, 'automaticRetryBlocked': self._blocked or condition == 'ip_not_allowed'}

    async def start(self):
        report = safe_json('toss-probe.json')
        self._blocked = report.get('authentication', {}).get('condition') in ('ip_not_allowed', 'invalid_client', 'access_denied')
        if os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
            return
        credentials_present = bool(os.environ.get('TOSS_CLIENT_ID') and os.environ.get('TOSS_CLIENT_SECRET'))
        if credentials_present and not self._blocked:
            self._task = asyncio.create_task(self._run())

    async def _run(self, initial_delay=0):
        if initial_delay:
            await asyncio.sleep(initial_delay)
        while True:
            await self.refresh()
            if self._blocked:
                return
            await asyncio.sleep(30)

    async def close(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.aclose()

    async def cached(self, key: str, ttl: float, factory):
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        value = await factory()
        self._cache[key] = (time.monotonic(), value)
        return value

    async def refresh(self, *, explicit=False):
        if os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
            return self.status()
        if self._lock.locked() or (self._blocked and not explicit):
            return self.status()
        async with self._lock:
            if explicit:
                self._blocked = False
                self._last_error = None
                self._cache.clear()
            if not self.client:
                from app.brokers.toss.documented import DocumentedTossClient
                self.client = DocumentedTossClient()
            elif explicit and hasattr(self.client, 'reset_auth_failure'):
                self.client.reset_auth_failure()
            try:
                from app.brokers.toss.documented import run_probe, save_probe
                previous = safe_json('toss-probe.json')
                if explicit or not previous.get('venue', {}).get('verified'):
                    report = await run_probe(self.client)
                    save_probe(report)
                    previous = report.to_dict()
                auth = previous.get('authentication', {})
                if auth.get('status') not in ('success', 'ready', 'passed'):
                    self._blocked = auth.get('condition') in ('ip_not_allowed', 'invalid_client', 'access_denied')
                    return self.status()
                if not previous.get('venue', {}).get('verified'):
                    self._last_error = '실제 NXT 프리마켓·애프터마켓 샘플 근거가 부족해 모의 모드를 유지합니다.'
                    return self.status()
                await asyncio.wait_for(self.ingest(previous['venue']), timeout=50)
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except LiveBundleError as error:
                self._last_error = error.message
            except Exception as error:
                condition = getattr(error, 'condition', None)
                self._blocked = condition in ('ip_not_allowed', 'invalid_client', 'access_denied')
                self._last_error = '실제 시세 조회 또는 정규화 검증에 실패해 모의 모드를 유지합니다.'
            if explicit and not self._blocked and (not self._task or self._task.done()):
                self._task = asyncio.create_task(self._run(initial_delay=30))
            return self.status()

    async def ingest(self, venue: dict):
        client, config, themes = self.client, read_strategy(), read_themes()
        now = datetime.now(timezone.utc)
        calendar = await self.cached('calendar_kr', 300, lambda: client.calendar('KR'))
        session, session_label, is_open = session_for(calendar, now)
        ranked_rows = await client.rankings('KR', count=min(100, config.screen.candidate_limit))
        requested = {symbol for theme in themes for symbol in theme['memberSymbols']}
        ranked = {row['symbol']: row for row in ranked_rows if row['symbol'] in requested}
        symbols = list(ranked)[:30]
        if len(symbols) < config.theme.min_constituents:
            raise LiveBundleError('invalid', '현재 거래대금 랭킹에서 분석에 필요한 테마 구성 종목을 충분히 관측하지 못했습니다.')
        quotes = {row['symbol']: row for row in await client.prices(symbols)}
        master = {row['symbol']: row for row in await self.cached('master:' + ','.join(symbols), 3600, lambda: client.stock_master(symbols))}
        stocks = []
        for symbol in symbols:
            quote, info, row = quotes[symbol], master[symbol], ranked[symbol]
            if quote['currency'] != 'KRW' or info['status'] != 'ACTIVE':
                raise LiveBundleError('invalid', '분석 대상 국내 종목의 통화 또는 거래 상태가 올바르지 않습니다.')
            daily_rows = await self.cached(f'daily:{symbol}', 600, lambda symbol=symbol: client.candles(symbol, '1d', count=90))
            intra_rows = await client.candles(symbol, '1m', count=200)
            daily, intra = normalized_candles(daily_rows, '1d'), normalized_candles(intra_rows, '1m')
            if info['currency'] != 'KRW' or daily[-1]['time'] < calendar['previousBusinessDay']['date']:
                raise LiveBundleError('stale', '종목 통화 또는 최근 거래일 일봉 이력이 확인되지 않아 분석을 보류합니다.')
            investor = await self.cached(f'investor:{symbol}', 60, lambda symbol=symbol: client.investor(symbol, count=2))
            program = await self.cached(f'program:{symbol}', 60, lambda symbol=symbol: client.program(symbol, count=2))
            investor_row, program_row = max(investor, key=lambda item: item['date']), max(program, key=lambda item: item['date'])
            day = calendar['today']['date']
            if is_open and (investor_row['date'] != day or program_row['date'] != day):
                raise LiveBundleError('invalid', '당일 투자자 또는 프로그램 수급을 관측하지 못해 추천 계산을 보류합니다.')
            prior = [candle for candle in daily if candle['time'] < day][-20:]
            baseline = mean(candle['volume'] for candle in prior)
            traded_volume = number(row['tradingVolume'], nonnegative=True)
            change = row['price'].get('changeRate')
            if change is None:
                # Official ranking basePrice allows an exact deterministic rate
                # computation; this is not a made-up missing return.
                change_pct = (number(quote['lastPrice'], positive=True) / number(row['price']['basePrice'], positive=True) - 1) * 100
            else:
                change_pct = number(change) * 100
            stocks.append({'symbol': symbol, 'name': info['name'], 'price': number(quote['lastPrice'], positive=True),
                           'changePct': change_pct, 'tradedValue': number(row['tradingAmount'], nonnegative=True),
                           'volume': traded_volume, 'volumeRatio': traded_volume / baseline,
                           'volumeAcceleration': intra[-1]['volume'] / max(1., mean(c['volume'] for c in intra[-6:-1])),
                           'flow': {'foreign': number(investor_row['foreigner']['netBuyVolume']),
                                    'institution': number(investor_row['institution']['netBuyVolume']),
                                    'program': number(program_row['arbitrage']['netBuyVolume']) + number(program_row['nonArbitrage']['netBuyVolume'])},
                           'flowUnit': 'shares', 'flowVenue': {'foreign': 'KRX+NXT · 등록외국인', 'institution': 'KRX+NXT', 'program': 'KRX only'},
                           'flowAsOf': {'investor': investor_row['date'], 'program': program_row['date']},
                           'timestamp': quote['timestamp'], 'candles': daily, 'intraday': intra,
                           'source': 'toss', 'integrated': True, 'venue': 'integrated'})
        us_symbols = sorted({ticker for theme in themes for ticker in theme['usTickers']})
        us_returns = {}
        us_calendar = await self.cached('calendar_us', 300, lambda: client.calendar('US'))
        completed = [day for day in (us_calendar['previousBusinessDay'], us_calendar['today'])
                     if day.get('regularMarket') and timestamp(day['regularMarket']['endTime']) <= now]
        closed_day = max(day['date'] for day in completed)
        # Also exercise the real US watchlist price endpoint, without substituting
        # its intraday price for the deliberately completed overnight return.
        us_quotes = {quote['symbol']: quote for quote in await self.cached('us_quotes', 300, lambda: client.prices(us_symbols))}
        for symbol in us_symbols:
            rows = await self.cached(f'us_daily:{symbol}', 900, lambda symbol=symbol: client.candles(symbol, '1d', count=5))
            if any(row.get('currency') != 'USD' for row in rows):
                raise LiveBundleError('invalid', '미국 종목 일봉의 실제 통화가 일치하지 않습니다.')
            final = sorted([row for row in rows if timestamp(row['timestamp']).date().isoformat() <= closed_day], key=lambda row: row['timestamp'])
            if len(final) < 2 or symbol not in us_quotes or us_quotes[symbol]['currency'] != 'USD':
                raise LiveBundleError('invalid', '미국 테마의 실제 전일 수익률 또는 현재가를 충분히 관측하지 못했습니다.')
            us_returns[symbol] = (number(final[-1]['closePrice'], positive=True) / number(final[-2]['closePrice'], positive=True) - 1) * 100
        indices = []
        for quote in await self.cached('index_prices', 30, lambda: client.index_prices(['KOSPI', 'KOSDAQ'])):
            rows = await self.cached('index_daily:' + quote['symbol'], 600, lambda symbol=quote['symbol']: client.index_candles(symbol, '1d', count=25))
            past = sorted([row for row in rows if timestamp(row['timestamp']).astimezone(KST).date().isoformat() < calendar['today']['date']], key=lambda row: row['timestamp'])
            if past:
                value = number(quote['lastPrice'], positive=True)
                indices.append({'name': quote['symbol'], 'value': value,
                                'changePct': (value / number(past[-1]['closePrice'], positive=True) - 1) * 100,
                                'sparkline': [number(row['closePrice'], positive=True) for row in past[-20:]] + [value], 'source': 'toss'})
        data = {'source': 'toss', 'mode': 'live', 'asOf': datetime.now(timezone.utc).isoformat(),
                'stocks': stocks, 'usReturns': us_returns, 'venue': venue,
                'market': {'source': 'toss', 'session': session, 'sessionLabel': session_label, 'isOpen': is_open,
                           'tradingDate': calendar['today']['date'], 'previousBusinessDate': calendar['previousBusinessDay']['date'],
                           'indices': indices, 'usMarkets': [{'symbol': symbol, 'name': symbol, 'changePct': us_returns[symbol], 'source': 'toss'} for symbol in us_symbols[:6]]}}
        save_live_bundle(data, config, required_us_tickers=us_symbols, required_symbols=symbols)
