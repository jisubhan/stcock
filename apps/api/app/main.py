import os
import re
from copy import deepcopy
from functools import lru_cache
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError
from app.brokers import TossMarketData
from app.fixtures import AS_OF, US_RETURNS, metadata, stock_samples
from app.settings import StrategyConfig, read_strategy, read_themes, save_yaml
from app.strategy.engine import analyze_stock, calculate_themes, candles_with_ma
from app.strategy.backtest import run_backtest
from app.db.store import (mark_notifications, monitor_paper, paper_buy, paper_sell,
                          paper_snapshot, read_notifications, save_backtest)
from app.market.ingestion import IngestionService, analysis_snapshot
from app.radar.routes import router as radar_router
from app.discovery.routes import router as discovery_router
from app.radar.runtime import CatalystWorker


@asynccontextmanager
async def lifespan(application: FastAPI):
    service = IngestionService()
    catalysts = CatalystWorker()
    application.state.ingestion = service
    await service.start()
    await catalysts.start()
    try:
        yield
    finally:
        await catalysts.close()
        await service.close()


app = FastAPI(title='FlowTrade Radar', version='1.2.0', lifespan=lifespan,
              description='Explainable stock radar. Verified complete Toss data or explicit synthetic demo fallback. No order APIs.')
app.add_middleware(CORSMiddleware,
    allow_origins=os.environ.get('CORS_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(','),
    allow_credentials=False, allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['Content-Type'])
app.include_router(radar_router)
app.include_router(discovery_router)


def snapshot():
    context = analysis_snapshot()
    return context.samples, context.themes, context.stocks, context.config


def ingestion_service():
    service = getattr(app.state, 'ingestion', None)
    if service is None:
        service = IngestionService()
        app.state.ingestion = service
    return service


def get_stock(symbol: str, stocks: list[dict]):
    stock = next((s for s in stocks if s['symbol'] == symbol), None)
    if not stock:
        raise HTTPException(404, '종목을 찾을 수 없습니다.')
    return stock


@app.get('/api/health')
async def health():
    context = analysis_snapshot()
    return {'status': 'ok', **context.meta, 'broker': ingestion_service().status(),
            'notice': '검증된 실제 통합 시세로 분석합니다. 실제 주문은 비활성화되어 있습니다.' if context.meta['mode'] == 'live' else
                      '모의 데이터로 실행 중입니다. 실제 시세 검증이 완료되면 전환됩니다. 실제 주문은 비활성화되어 있습니다.'}


@app.get('/api/connection')
async def connection_status():
    context = analysis_snapshot()
    readiness = ingestion_service().status()
    return {**context.meta, **readiness, 'mode': context.meta['mode'], 'broker': readiness,
            'requiredVariables': ['TOSS_CLIENT_ID', 'TOSS_CLIENT_SECRET'],
            'verification': '공식 REST 1.2.24 계약 확인 · 실제 시세와 NXT 샘플 검증은 연결 상태를 확인하세요.',
            'note': '설정값과 OAuth 토큰은 서버에서만 읽습니다. 실주문 경로는 존재하지 않습니다.'}


@app.post('/api/connection/refresh')
async def refresh_connection():
    await ingestion_service().refresh(explicit=True)
    return await connection_status()


def demo_market():
    return {**metadata(), 'session': 'SIMULATED', 'sessionLabel': 'KRX 정규장 · 모의 시나리오',
            'risk': {'level': 'MODERATE', 'label': '보통', 'score': 42},
            'indices': [
                {'name': 'KOSPI', 'value': 3248.56, 'changePct': .84,
                 'sparkline': [3221, 3227, 3224, 3235, 3232, 3243, 3238, 3246, 3244, 3248.56]},
                {'name': 'KOSDAQ', 'value': 912.37, 'changePct': 1.26,
                 'sparkline': [901, 905, 904, 907, 906, 909, 908, 911, 910, 912.37]},
                {'name': 'NASDAQ', 'value': 22148.92, 'changePct': 1.18,
                 'sparkline': [21894, 21915, 21904, 21970, 21955, 22010, 22044, 22030, 22101, 22148.92]},
                {'name': 'USD/KRW', 'value': 1378.5, 'changePct': -.32,
                 'sparkline': [1383, 1382, 1382.5, 1381, 1381.5, 1379, 1380, 1379.5, 1378, 1378.5]}],
            'usMarkets': [{'symbol': symbol, 'name': name, 'changePct': US_RETURNS[symbol]}
                          for symbol, name in [('NVDA', 'NVIDIA'), ('MRVL', 'Marvell'), ('CEG', 'Constellation Energy'), ('FSLR', 'First Solar')]],
            'dataStatus': {'prices': 'simulated', 'flows': 'simulated', 'venue': 'unverified',
                           'calendar': 'simulated', 'officialDocs': 'network_access_blocked'},
            'summary': '광통신과 AI 전력으로 수급이 집중되는 모의 장중 시나리오',
            'note': '고정된 2026-10-07 10:24 KST 합성 스냅샷입니다. 실제 시장 운영 시간이나 시세를 나타내지 않습니다.'}


@app.get('/api/market')
def market():
    context = analysis_snapshot()
    if context.market:
        return {**context.market, **context.meta, 'risk': {'level': 'UNKNOWN', 'label': '미평가', 'score': None},
                'summary': '실제 통합 시세·투자자 수급·미국 전일 수익률을 기준으로 후보를 분석합니다.',
                'dataStatus': {'prices': 'verified', 'flows': 'shares_with_venue_labels', 'venue': 'sample_verified',
                               'calendar': 'official', 'officialDocs': 'verified_v1.2.24'},
                'note': '국내 가격·캔들은 KRX+NXT 통합 기준입니다. 프로그램 수급은 KRX만 포함합니다.'}
    data = demo_market()
    data.update(context.meta)
    data['dataStatus']['officialDocs'] = ingestion_service().status()['schemaStatus']
    return data


@app.get('/api/stocks')
def stocks(theme: str | None = None, q: str | None = None, state: str | None = None,
           recommended: bool = False, limit: int = Query(100, ge=1, le=500)):
    context = analysis_snapshot()
    themes, candidates, config = context.themes, context.stocks, context.config
    result = candidates
    if theme and theme != 'all':
        result = [stock for stock in result if stock['themeId'] == theme]
    if q:
        result = [stock for stock in result if q.casefold() in stock['name'].casefold() or q in stock['symbol']]
    if state and state != 'all':
        result = [stock for stock in result if stock['state'] == state]
    if recommended:
        result = [stock for stock in result if stock['eligible']]
    return {**context.meta, 'items': result[:min(limit, config.screen.candidate_limit)],
            'total': len(result), 'recommended': [s for s in candidates if s['eligible']][:3],
            'candidateCount': len(candidates), 'qualifiedThemeCount': sum(t['qualified'] for t in themes)}


@app.get('/api/stocks/{symbol}')
def stock_detail(symbol: str):
    context = analysis_snapshot()
    samples, themes, stocks, config = context.samples, context.themes, context.stocks, context.config
    stock = get_stock(symbol, stocks)
    raw = get_stock(symbol, samples)
    from datetime import datetime, timedelta
    from app.radar.models import KST
    from app.radar.sessions import calendar_sessions
    from app.radar.storage import read_state
    from app.strategy.intraday import MarketSession, evaluate_legacy_stock_entry
    entry_clock = datetime.fromisoformat(context.meta['asOf']).astimezone(KST) if context.meta['mode'] == 'demo' else datetime.now(KST)
    if context.meta['mode'] == 'demo':
        beginning = entry_clock.replace(hour=8, minute=0, second=0, microsecond=0)
        sessions = [MarketSession('SYNTHETIC_SESSION', beginning, beginning + timedelta(hours=12))]
    else:
        sessions = calendar_sessions(read_state('calendar:' + entry_clock.date().isoformat()) or {})
    entry = evaluate_legacy_stock_entry(raw, stock, entry_clock, sessions, context.meta['mode'])
    return {**context.meta, **stock,
            **entry,
            'candles': candles_with_ma(raw['candles'], config.moving_average.short),
            'intraday': raw['intraday'],
            'related': [s for s in stocks if s['themeId'] == stock['themeId'] and s['symbol'] != symbol],
            'themeDetails': next((t for t in themes if t['id'] == stock['themeId']), None)}


@app.get('/api/themes')
def themes():
    context = analysis_snapshot()
    return {**context.meta, 'items': context.themes}


class ThemeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    memberSymbols: list[str] | None = Field(None, max_length=100)
    usTickers: list[str] | None = Field(None, max_length=50)


class ThemeCreate(ThemeUpdate):
    id: str = Field(min_length=1, max_length=60, pattern=r'^[a-z][a-z0-9_]*$')
    name: str = Field(min_length=1, max_length=50)
    memberSymbols: list[str] = Field(default_factory=list, max_length=100)
    usTickers: list[str] = Field(default_factory=list, max_length=50)


def validate_theme(theme: dict):
    symbols = {s['symbol'] for s in stock_samples()}
    missing = [symbol for symbol in theme['memberSymbols'] if symbol not in symbols]
    if missing:
        raise HTTPException(422, '모의 데이터에 없는 종목 코드: ' + ', '.join(missing))
    if len(set(theme['memberSymbols'])) != len(theme['memberSymbols']):
        raise HTTPException(422, '종목 코드는 중복될 수 없습니다.')
    if any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', ticker) for ticker in theme['usTickers']):
        raise HTTPException(422, '미국 티커는 대문자 기호로 입력하세요.')
    theme['name'] = theme['name'].strip()
    if not theme['name']:
        raise HTTPException(422, '테마 이름이 필요합니다.')


@app.put('/api/themes/{theme_id}')
def update_theme(theme_id: str, update: ThemeUpdate):
    themes = read_themes()
    index = next((i for i, theme in enumerate(themes) if theme['id'] == theme_id), None)
    if index is None:
        raise HTTPException(404, '테마를 찾을 수 없습니다.')
    themes[index].update(update.model_dump(exclude_none=True))
    validate_theme(themes[index])
    save_yaml('themes.yml', {'themes': themes})
    context = analysis_snapshot()
    return {**context.meta, 'item': next(t for t in context.themes if t['id'] == theme_id)}


@app.post('/api/themes', status_code=201)
def create_theme(theme: ThemeCreate):
    themes = read_themes()
    if any(t['id'] == theme.id for t in themes):
        raise HTTPException(409, '이미 존재하는 테마 ID입니다.')
    value = theme.model_dump()
    validate_theme(value)
    themes.append(value)
    save_yaml('themes.yml', {'themes': themes})
    context = analysis_snapshot()
    return {**context.meta, 'item': next(t for t in context.themes if t['id'] == theme.id)}


@app.delete('/api/themes/{theme_id}')
def delete_theme(theme_id: str):
    themes = read_themes()
    remaining = [t for t in themes if t['id'] != theme_id]
    if len(themes) == len(remaining):
        raise HTTPException(404, '테마를 찾을 수 없습니다.')
    save_yaml('themes.yml', {'themes': remaining})
    return {'deleted': theme_id, **analysis_snapshot().meta}


@app.get('/api/strategy')
def strategy():
    return read_strategy().model_dump()


def merge_config(base: dict, patch: dict):
    for key, value in patch.items():
        if key not in base:
            raise HTTPException(422, f'알 수 없는 전략 설정: {key}')
        if isinstance(value, dict) and isinstance(base[key], dict):
            merge_config(base[key], value)
        else:
            base[key] = value
    return base


@app.put('/api/strategy')
def update_strategy(patch: dict):
    try:
        config = StrategyConfig.model_validate(merge_config(read_strategy().model_dump(), patch))
    except ValidationError as error:
        raise HTTPException(422, [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in error.errors()]) from None
    save_yaml('strategy.yml', config.model_dump())
    return config.model_dump()


@lru_cache(maxsize=8)
def cached_backtest(config_json: str, theme_json: str):
    import json
    config = StrategyConfig.model_validate_json(config_json)
    themes = json.loads(theme_json)
    return run_backtest(stock_samples(), config, themes)


def backtest_data():
    import json
    config = read_strategy()
    theme_names = {t['id']: t['name'] for t in read_themes()}
    return {**metadata(), **deepcopy(cached_backtest(config.model_dump_json(), json.dumps(theme_names, sort_keys=True))),
            'notice': '합성 일봉 데이터의 보수적 워크포워드 결과입니다. 실제 과거 성과가 아닙니다.'}


@app.get('/api/backtest')
def backtest():
    return backtest_data()


@app.post('/api/backtest/run')
def rerun_backtest():
    result = backtest_data()
    result['runId'] = save_backtest(read_strategy().model_dump(), result)
    return result


@app.get('/api/paper')
def paper():
    context = analysis_snapshot()
    monitor_paper(context.stocks, mode=context.meta['mode'])
    return {**context.meta, **paper_snapshot(context.stocks, mode=context.meta['mode'])}


class PaperBuy(BaseModel):
    symbol: str = Field(pattern=r'^\d{6}$')
    quantity: int = Field(ge=1, le=10000)


@app.post('/api/paper/buy', status_code=201)
def buy_paper(order: PaperBuy):
    context = analysis_snapshot()
    stocks = context.stocks
    stock = get_stock(order.symbol, stocks)
    if stock['price'] * order.quantity > 100_000_000:
        raise HTTPException(422, '모의 주문 1회 한도는 1억원입니다.')
    trade_id = paper_buy(stock, order.quantity, mode=context.meta['mode'])
    return {**context.meta, 'id': trade_id, **paper_snapshot(stocks, mode=context.meta['mode'])}


@app.post('/api/paper/{trade_id}/sell')
def sell_paper(trade_id: int):
    context = analysis_snapshot()
    stocks = context.stocks
    try:
        paper_sell(trade_id, stocks, mode=context.meta['mode'])
    except LookupError as error:
        raise HTTPException(404, str(error)) from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {**context.meta, **paper_snapshot(stocks, mode=context.meta['mode'])}


@app.get('/api/notifications')
def notifications():
    context = analysis_snapshot()
    stocks = context.stocks
    read = read_notifications()
    labels = {'ENTRY_READY': '진입 관찰 구간 확인', 'BREAKOUT_CONFIRM': '피벗 재돌파 확인',
              'APPROACHING_ENTRY': '진입 관찰 구간 접근'}
    items = []
    for stock in [s for s in stocks if s['eligible']][:5]:
        notification_id = f"{context.meta['mode']}-{stock['symbol']}-{stock['state']}"
        items.append({'id': notification_id, 'symbol': stock['symbol'], 'name': stock['name'],
                      'type': stock['state'], 'title': labels.get(stock['state'], '관찰 후보 업데이트'),
                      'message': f"{stock['name']} · {stock['score']}점 · 진입 {stock['entry']['low']:,}~{stock['entry']['high']:,}원 · 무효화 {stock['risk']['stop']:,}원",
                      'timestamp': context.meta['asOf'], 'read': notification_id in read, 'mode': context.meta['mode']})
    from app.radar.storage import read_notifications as read_radar_notifications
    for row in read_radar_notifications(mode=context.meta['mode']):
        items.append({'id': row['id'], 'symbol': '', 'name': row.get('theme_id', ''),
            'type': row['notification_type'], 'title': row['notification_type'],
            'message': row['explanation'], 'timestamp': row['as_of'], 'read': row['read'],
            'mode': row['mode'], 'source_urls': row.get('source_urls', [])})
    return {**context.meta, 'items': items, 'unread': sum(not item['read'] for item in items)}


class ReadNotifications(BaseModel):
    ids: list[str] = Field(max_length=100)


@app.post('/api/notifications/read')
def notifications_read(request: ReadNotifications):
    known = {item['id'] for item in notifications()['items']}
    mark_notifications([notification_id for notification_id in request.ids if notification_id in known])
    return notifications()
