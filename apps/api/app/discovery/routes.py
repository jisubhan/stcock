"""Search actual domestic universe and inspect hypotheses and real charts."""
from fastapi import APIRouter, HTTPException, Query

from app.discovery.runtime import discovery_overview, discovery_stocks, discovery_stock_detail

router = APIRouter(prefix='/api/discovery', tags=['market-discovery'])


@router.get('/overview')
def overview():
    return discovery_overview()


@router.get('/stocks')
def stocks(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
           q: str = Query('', max_length=100), theme_id: str | None = Query(None, max_length=50),
           unclassified: bool = False):
    return discovery_stocks(page=page, limit=limit, query=q, theme_id=theme_id, unclassified=unclassified)


@router.get('/stocks/{symbol}')
def stock_detail(symbol: str, theme_id: str | None = Query(None, max_length=50)):
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum() or symbol.upper() != symbol:
        raise HTTPException(422, '확인 가능한 국내 종목코드가 필요합니다.')
    result = discovery_stock_detail(symbol, theme_id)
    if result is None:
        raise HTTPException(404, '공식 종목 목록에서 아직 확인하지 못한 종목입니다.')
    return result
