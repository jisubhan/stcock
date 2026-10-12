"""Additive, read-only v1.2 routes; broker order endpoints do not exist."""
from datetime import date as Date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from app.radar.models import KST
from app.radar.runtime import current_catalysts, current_overview, feed_health
from app.radar.storage import rotation_history, read_notifications
from app.replay.fixtures import SCENARIOS, SNAPSHOT_TIMES
from app.replay.opening_session import replay_session

router = APIRouter(prefix='/api', tags=['radar-v1.2'])


def clock(as_of):
    if as_of is None:
        return None
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise HTTPException(422, 'as_of에는 시간대가 필요합니다.')
    if as_of > datetime.now(KST):
        raise HTTPException(422, '현재 관측 API에 미래 시각을 지정할 수 없습니다. 합성 리플레이를 사용하세요.')
    return as_of.astimezone(KST)


@router.get('/market/overview')
def overview(as_of: datetime | None = None):
    return current_overview(clock(as_of), persist=as_of is None)


@router.get('/catalysts')
def catalysts(since: datetime | None = None, verified: bool | None = None, as_of: datetime | None = None):
    now = clock(as_of) or datetime.now(KST)
    if since is not None and since.tzinfo is None:
        raise HTTPException(422, 'since에는 시간대가 필요합니다.')
    health = feed_health(now, historical=as_of is not None)
    return {'version': '1.2', 'as_of': now.isoformat(), 'items': current_catalysts(now, since, verified, current=as_of is None),
            'data_freshness': {'news': health['news'], 'disclosures': health['disclosures']},
            'provider': health['providers'], 'coverage': 'Configured source contracts only',
            'measurement_definition': 'Published, first-seen and revision-available times are distinct.',
            'explanation_codes': ['NO_DETECTION_DOES_NOT_MEAN_NO_CATALYST']}


@router.get('/themes/opening-flow')
def opening_flow(window: Literal['1m', '5m'] = '5m', session: Literal['KRX', 'NXT', 'CONSOLIDATED'] = 'KRX',
                 as_of: datetime | None = None):
    data = current_overview(clock(as_of), int(window[:-1]), persist=as_of is None and window == '5m')
    matching = session == 'CONSOLIDATED' or (session == 'KRX' and data['session_label'] == 'KRX_REGULAR') or (
        session == 'NXT' and data['session_label'].startswith('NXT'))
    return {'as_of': data['as_of'], 'mode': data['mode'], 'items': data['theme_flow_snapshots'] if matching else [],
            'status': data['quality'] if matching else 'UNAVAILABLE', 'session': session, 'window': window,
            'data_freshness': data['data_freshness'], 'coverage': data['coverage'], 'provider': data['provider'],
            'measurement_definition': 'Exact observed interval turnover, not investor net buying. Integrated data is counted once.',
            'explanation_codes': data['reason_codes'] + ([] if matching else ['REQUESTED_SESSION_NOT_ACTIVE'])}


@router.get('/themes/rotation-history')
def history(date: Date | None = None, as_of: datetime | None = None):
    now = clock(as_of) or datetime.now(KST)
    return {'as_of': now.isoformat(), 'date': (date or now.date()).isoformat(), 'mode': 'live',
            'items': rotation_history((date or now.date()).isoformat(), as_of=now),
            'data_freshness': feed_health(now, historical=as_of is not None), 'coverage': 'Stored observation/audit scope',
            'provider': 'FlowTrade Radar', 'measurement_definition': 'Immutable changes known at or before as_of.',
            'explanation_codes': ['PREMARKET_VS_OBSERVED_VS_ENTRY_ARE_SEPARATE']}


@router.get('/recommendations')
def recommendations(view: Literal['premarket', 'live-flow', 'entry-ready'] = 'live-flow', as_of: datetime | None = None):
    data = current_overview(clock(as_of), persist=as_of is None)
    key = {'premarket': 'premarket_hypothesis_top3', 'live-flow': 'live_opening_flow_top3', 'entry-ready': 'entry_ready_top3'}[view]
    return {**data[key], 'view': view, 'as_of': data['as_of'], 'mode': data['mode'],
            'data_freshness': data['data_freshness'], 'coverage': data['coverage'], 'provider': data['provider'],
            'measurement_definition': data['measurement_definition']}


@router.get('/feeds/health')
def feeds():
    now = datetime.now(KST)
    health = feed_health(now)
    return {'version': '1.2', 'as_of': now.isoformat(), 'data_freshness': health, 'provider': health['providers'],
            'coverage': 'Provider-specific, never all-market coverage',
            'measurement_definition': 'Feed status does not assert that every market item was observed.',
            'explanation_codes': ['NEWS_AND_DISCLOSURES_HAVE_DIFFERENT_COVERAGE']}


@router.get('/radar/notifications')
def radar_notifications():
    now = datetime.now(KST)
    return {'version': '1.2', 'as_of': now.isoformat(), 'mode': 'live', 'items': read_notifications(),
            'data_freshness': feed_health(now), 'coverage': 'Recorded source and rotation events only',
            'provider': 'FlowTrade Radar', 'measurement_definition': 'Idempotent event fingerprints; synthetic notifications are isolated.',
            'explanation_codes': ['ALERT_IS_NOT_AN_ORDER']}


@router.get('/analytics/opening-replay')
def opening_replay(session: str = '2026-10-12', snapshot_time: str = '09:10', scenario: str = 'domestic_rotation',
                   as_of: datetime | None = None):
    try:
        Date.fromisoformat(session)
        if snapshot_time not in SNAPSHOT_TIMES or scenario not in SCENARIOS:
            raise ValueError('Unknown replay configuration.')
        if as_of is not None and (as_of.tzinfo is None or as_of.astimezone(KST).date().isoformat() != session):
            raise ValueError('Replay timestamps must be aware and within the requested synthetic session.')
        return replay_session(session, snapshot_time, scenario, as_of=as_of)
    except ValueError:
        raise HTTPException(422, '지원되는 합성 리플레이 날짜·시각·시나리오를 지정하세요.') from None
