from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BrokerUnavailable(RuntimeError):
    """A safe, credential-free explanation of an unavailable data source."""


class MarketModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("timestamp", check_fields=False)
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Market timestamps must include a timezone")
        return value


class Quote(MarketModel):
    symbol: str = Field(min_length=1, max_length=30)
    price: float = Field(gt=0)
    change_pct: float
    volume: float = Field(ge=0)
    traded_value: float = Field(ge=0)
    timestamp: datetime
    venue: Literal["integrated"] = "integrated"


class Candle(MarketModel):
    symbol: str = Field(min_length=1, max_length=30)
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    traded_value: float | None = Field(default=None, ge=0)
    interval: str = Field(min_length=1)
    integrated: Literal[True] = True

    @model_validator(mode="after")
    def valid_ohlc(self) -> Candle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC prices are inconsistent")
        if self.low > self.high:
            raise ValueError("Candle low must not exceed high")
        return self


@runtime_checkable
class BrokerMarketData(Protocol):
    async def get_prices(self, symbols: list[str]) -> list[Quote]: ...
    async def get_orderbook(self, symbol: str) -> Any: ...
    async def get_trades(self, symbol: str) -> Any: ...
    async def get_candles(self, symbol: str, interval: str) -> list[Candle]: ...
    async def get_investor_trading(self, symbol: str) -> Any: ...
    async def get_program_trades(self, symbol: str) -> Any: ...
    async def get_rankings(self, ranking_type: str) -> Any: ...
    async def get_market_calendar(self) -> Any: ...
