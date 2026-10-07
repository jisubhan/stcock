"""Validated, normalized live snapshot boundary; no transport or broker schema.

The ingestion layer supplies real observations and independently verified venue
evidence. A complete bundle is the only input accepted by the live analysis
path. ``load_live_bundle`` validates storage on every read and returns no bundle
for missing, invalid or stale data, so its caller can explicitly choose demo.

All persistence fields are enumerated: credentials, raw API responses and
arbitrary metadata are never part of this format. ISO times must carry a zone;
daily candle dates may use YYYY-MM-DD. Times and freshness are checked against
an injectable clock, never a fixed demonstration date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Annotated, Any, Iterable, Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.settings import StrategyConfig, runtime_dir


Finite = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Nonempty = Annotated[str, Field(min_length=1, max_length=500)]
Source = Literal["toss"]
DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 150.0
DEFAULT_QUOTE_MAX_AGE_SECONDS = 300.0
MAX_FUTURE_SKEW_SECONDS = 30.0
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def _aware_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("A timezone-aware ISO timestamp is required") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value


def _candle_time(value: str) -> date | datetime:
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("A valid ISO candle date is required") from None
    return _aware_timestamp(value)


class NormalizedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("source", check_fields=False, mode="before")
    @classmethod
    def actual_source(cls, value: Any) -> str:
        if value != "toss":
            raise ValueError("Only normalized Toss observations are accepted")
        return value

    @field_validator("integrated", "verified", "includes_nxt", check_fields=False, mode="before")
    @classmethod
    def explicit_true(cls, value: Any) -> bool:
        if value is not True:
            raise ValueError("Explicitly verified integrated venue evidence is required")
        return value


class LiveCandle(NormalizedModel):
    time: Nonempty
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: Nonnegative
    source: Source
    integrated: Literal[True]
    venue: Literal["integrated"]
    tradedValue: Nonnegative | None = None
    interval: Nonempty | None = None

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        _candle_time(value)
        return value

    @model_validator(mode="after")
    def valid_ohlc(self) -> LiveCandle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Candle open and close must be inside its low/high range")
        if self.low > self.high:
            raise ValueError("Candle low must not exceed high")
        return self


class LiveFlow(NormalizedModel):
    # Zero is valid only when supplied as an observation, never as a default.
    foreign: Finite
    institution: Finite
    program: Finite


class LiveFlowVenue(NormalizedModel):
    # Foreign/institution observations include NXT, but the documented program
    # flow series covers KRX alone. Never label all flows as integrated.
    foreign: Literal["KRX+NXT · 등록외국인"]
    institution: Literal["KRX+NXT"]
    program: Literal["KRX only"]


class LiveFlowAsOf(NormalizedModel):
    investor: Nonempty
    program: Nonempty

    @field_validator("*")
    @classmethod
    def observed_trading_date(cls, value: str) -> str:
        if len(value) != 10 or isinstance(_candle_time(value), datetime):
            raise ValueError("Flow observation dates must use YYYY-MM-DD")
        return value


class LiveStock(NormalizedModel):
    symbol: Annotated[str, Field(min_length=1, max_length=30)]
    name: Nonempty
    price: Positive
    changePct: Finite
    tradedValue: Nonnegative
    volume: Nonnegative
    volumeRatio: Nonnegative
    volumeAcceleration: Nonnegative
    flow: LiveFlow
    flowUnit: Literal["shares"] = "shares"
    flowVenue: LiveFlowVenue
    flowAsOf: LiveFlowAsOf
    seedThemeId: Nonempty | None = None
    candles: list[LiveCandle] = Field(min_length=30)
    intraday: list[LiveCandle] = Field(min_length=6)
    timestamp: datetime
    source: Source
    integrated: Literal[True]
    venue: Literal["integrated"]

    _timestamp = field_validator("timestamp", mode="before")(_aware_timestamp)

    @field_validator("symbol", "name")
    @classmethod
    def no_blank_labels(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Stock labels must be nonblank and trimmed")
        return value

    @model_validator(mode="after")
    def valid_history(self) -> LiveStock:
        for label, candles in (("daily", self.candles), ("intraday", self.intraday)):
            times = [_candle_time(candle.time) for candle in candles]
            if label == "daily" and any(isinstance(item, datetime) for item in times):
                raise ValueError("Daily candle times must be trading dates")
            if label == "intraday" and any(not isinstance(item, datetime) for item in times):
                raise ValueError("Intraday candle times must include a timezone")
            if any(left >= right for left, right in zip(times, times[1:])):
                raise ValueError("Candle history must be chronological with unique timestamps")
        # The analysis engine divides by these observed volume sums. Entirely
        # inactive windows cannot be analyzed as an adequately observed stock.
        if sum(candle.volume for candle in self.candles[-8:]) <= 0:
            raise ValueError("Recent daily volume history is insufficient for analysis")
        if any(sum(candle.volume for candle in self.candles[index - 20:index]) <= 0
               for index in range(20, len(self.candles))):
            raise ValueError("Daily volume baseline is insufficient for analysis")
        return self


class LiveIndex(NormalizedModel):
    name: Nonempty
    value: Positive
    changePct: Finite
    source: Source
    sparkline: list[Positive] = Field(default_factory=list)


class LiveUSMarket(NormalizedModel):
    symbol: Annotated[str, Field(min_length=1, max_length=30)]
    name: Nonempty
    changePct: Finite
    source: Source


class LiveMarket(NormalizedModel):
    session: Nonempty
    sessionLabel: Nonempty
    isOpen: bool
    source: Source
    tradingDate: Nonempty | None = None
    previousBusinessDate: Nonempty | None = None
    # Missing index observations remain visibly absent, never synthetic values.
    indices: list[LiveIndex]
    usMarkets: list[LiveUSMarket] = Field(min_length=1)

    @field_validator("tradingDate", "previousBusinessDate")
    @classmethod
    def calendar_dates(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 10 or isinstance(_candle_time(value), datetime)):
            raise ValueError("Market calendar dates must use YYYY-MM-DD")
        return value

    @field_validator("session", "sessionLabel")
    @classmethod
    def actual_session(cls, value: str) -> str:
        if not value.strip() or any(word in value.upper() for word in ("SIMULAT", "SYNTHETIC", "DEMO")):
            raise ValueError("Market session must be an observed actual session")
        return value

    @model_validator(mode="after")
    def unique_metadata(self) -> LiveMarket:
        if len({index.name for index in self.indices}) != len(self.indices):
            raise ValueError("Market indices must be unique")
        if len({item.symbol for item in self.usMarkets}) != len(self.usMarkets):
            raise ValueError("US market symbols must be unique")
        if self.tradingDate is not None and self.previousBusinessDate is not None:
            if date.fromisoformat(self.previousBusinessDate) > date.fromisoformat(self.tradingDate):
                raise ValueError("Previous business date must not exceed the trading date")
        return self


class LiveVenueEvidence(NormalizedModel):
    quote_venue: Nonempty
    daily_ohlc: Nonempty
    nxt_premarket: Nonempty
    nxt_aftermarket: Nonempty

    @field_validator("*")
    @classmethod
    def nonblank_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Venue evidence references must be nonblank")
        return value


class LiveVenue(NormalizedModel):
    verified: Literal[True]
    price_basis: Literal["integrated"]
    daily_candle_basis: Literal["integrated"]
    includes_nxt: Literal[True]
    evidence: LiveVenueEvidence


class LiveBundle(NormalizedModel):
    source: Source
    mode: Literal["live"]
    asOf: datetime
    stocks: list[LiveStock] = Field(min_length=3)
    usReturns: dict[str, Finite]
    market: LiveMarket
    venue: LiveVenue

    _timestamp = field_validator("asOf", mode="before")(_aware_timestamp)

    @model_validator(mode="after")
    def complete_metadata(self) -> LiveBundle:
        if len({stock.symbol for stock in self.stocks}) != len(self.stocks):
            raise ValueError("Live stock symbols must be unique")
        if any(not ticker or ticker != ticker.strip() for ticker in self.usReturns):
            raise ValueError("US return ticker keys must be nonblank and trimmed")
        for item in self.market.usMarkets:
            if item.symbol not in self.usReturns or item.changePct != self.usReturns[item.symbol]:
                raise ValueError("US market metadata must match supplied US returns")
        return self

    def stock_dicts(self) -> list[dict[str, Any]]:
        """Return the verified stock observations in engine-compatible form."""
        return [stock.model_dump(mode="json", exclude_none=True) for stock in self.stocks]


class LiveBundleError(ValueError):
    """Safe, fixed text; never incorporates secret-bearing input or exceptions."""

    def __init__(self, code: Literal["invalid", "stale"], message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LiveBundleResult:
    status: Literal["ready", "missing", "invalid", "stale"]
    bundle: LiveBundle | None = None
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.bundle is not None


def validate_live_bundle(
    data: LiveBundle | Mapping[str, Any],
    config: StrategyConfig,
    *,
    required_us_tickers: Iterable[str] = (),
    required_symbols: Iterable[str] = (),
    now: datetime | None = None,
    max_snapshot_age_seconds: float = DEFAULT_SNAPSHOT_MAX_AGE_SECONDS,
    max_quote_age_seconds: float = DEFAULT_QUOTE_MAX_AGE_SECONDS,
) -> LiveBundle:
    """Validate content, configured history, requested universe and freshness.

    ``asOf`` is the actual capture time, while each stock ``timestamp`` is the
    upstream quote time. A freshly fetched old quote still returns stale, even
    during a closed session; neither timestamp is replaced with the wall clock.
    """
    try:
        # Round-tripping also revalidates possibly modified Pydantic instances.
        raw = data.model_dump(mode="python") if isinstance(data, LiveBundle) else data
        bundle = LiveBundle.model_validate(raw)
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise LiveBundleError("invalid", "실시간 스냅샷 형식 또는 필수 관측 데이터 검증에 실패했습니다.") from None
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) or value <= 0
           for value in (max_snapshot_age_seconds, max_quote_age_seconds)):
        raise LiveBundleError("invalid", "실시간 스냅샷 유효 시간 설정이 올바르지 않습니다.")
    try:
        clock = _aware_timestamp(now if now is not None else datetime.now(timezone.utc))
    except ValueError:
        raise LiveBundleError("invalid", "검증 시각에 명시적인 시간대가 필요합니다.") from None
    required_days = max(config.moving_average.medium, config.moving_average.short + 1, 30)
    if len(bundle.stocks) < max(3, config.theme.min_constituents):
        raise LiveBundleError("invalid", "실시간 분석에 필요한 종목 수가 부족합니다.")
    if any(len(stock.candles) < required_days for stock in bundle.stocks):
        raise LiveBundleError("invalid", "전략에 필요한 통합 일봉 이력이 부족합니다.")
    if not set(required_symbols).issubset(stock.symbol for stock in bundle.stocks):
        raise LiveBundleError("invalid", "요청한 국내 종목 범위의 실시간 데이터가 누락되었습니다.")
    if not set(required_us_tickers).issubset(bundle.usReturns):
        raise LiveBundleError("invalid", "요청한 테마의 미국 선행 종목 수익률이 누락되었습니다.")
    age = (clock - bundle.asOf).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise LiveBundleError("invalid", "실시간 스냅샷 시각이 현재 시각보다 앞서 있습니다.")
    if age > max_snapshot_age_seconds:
        raise LiveBundleError("stale", "실시간 스냅샷 유효 시간이 지났습니다.")
    trading_day = clock.astimezone(ZoneInfo("Asia/Seoul")).date()
    calendar_dates = (bundle.market.tradingDate, bundle.market.previousBusinessDate)
    if any(value is not None and date.fromisoformat(value) > trading_day for value in calendar_dates):
        raise LiveBundleError("invalid", "시장 달력에 미래 거래일이 포함되어 있습니다.")
    previous_business_date = (date.fromisoformat(bundle.market.previousBusinessDate)
                              if bundle.market.previousBusinessDate is not None else None)
    for stock in bundle.stocks:
        quote_age = (clock - stock.timestamp).total_seconds()
        if quote_age < -MAX_FUTURE_SKEW_SECONDS or (stock.timestamp - bundle.asOf).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
            raise LiveBundleError("invalid", "실시간 종목 시각이 검증 시각보다 앞서 있습니다.")
        if quote_age > max_quote_age_seconds:
            raise LiveBundleError("stale", "최근 현재가 관측이 오래되어 실시간으로 사용할 수 없습니다.")
        last_daily = _candle_time(stock.candles[-1].time)
        if last_daily > trading_day:
            raise LiveBundleError("invalid", "일봉 이력에 미래 거래일이 포함되어 있습니다.")
        if (trading_day - last_daily).days > 10:
            raise LiveBundleError("stale", "통합 일봉 이력이 오래되어 실시간 분석에 사용할 수 없습니다.")
        if previous_business_date is not None and last_daily < previous_business_date:
            raise LiveBundleError("stale", "통합 일봉 이력이 시장 달력의 최근 영업일까지 갱신되지 않았습니다.")
        if any(date.fromisoformat(value) > trading_day for value in
               (stock.flowAsOf.investor, stock.flowAsOf.program)):
            raise LiveBundleError("invalid", "수급 관측에 미래 거래일이 포함되어 있습니다.")
        last_intraday = _candle_time(stock.intraday[-1].time)
        assert isinstance(last_intraday, datetime)
        intra_age = (clock - last_intraday).total_seconds()
        if intra_age < -MAX_FUTURE_SKEW_SECONDS:
            raise LiveBundleError("invalid", "장중 이력에 미래 시각이 포함되어 있습니다.")
        if intra_age > max_quote_age_seconds:
            raise LiveBundleError("stale", "장중 관측 이력이 오래되어 실시간 분석에 사용할 수 없습니다.")
    return bundle


def save_live_bundle(
    data: LiveBundle | Mapping[str, Any], config: StrategyConfig, *,
    path: Path | str | None = None, **validation_options: Any,
) -> LiveBundle:
    """Validate before an atomic private-file replace; preserve prior valid data."""
    bundle = validate_live_bundle(data, config, **validation_options)
    target = Path(path) if path is not None else runtime_dir() / "live_snapshot.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle.model_dump(mode="json", exclude_none=True), ensure_ascii=False, allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise LiveBundleError("invalid", "실시간 스냅샷이 허용된 저장 크기를 초과합니다.")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".live_snapshot-", suffix=".tmp", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except OSError:
        raise LiveBundleError("invalid", "실시간 스냅샷을 안전하게 저장하지 못했습니다.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return bundle


def load_live_bundle(
    config: StrategyConfig, *, path: Path | str | None = None,
    **validation_options: Any,
) -> LiveBundleResult:
    """Read and fully revalidate; never return stale or partially valid stocks."""
    target = Path(path) if path is not None else runtime_dir() / "live_snapshot.json"
    try:
        if target.stat().st_size > MAX_SNAPSHOT_BYTES:
            return LiveBundleResult("invalid", error="실시간 스냅샷이 허용된 읽기 크기를 초과합니다.")
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LiveBundleResult("missing", error="검증된 실시간 스냅샷이 아직 없습니다.")
    except (OSError, UnicodeError, ValueError):
        return LiveBundleResult("invalid", error="실시간 스냅샷 파일을 읽거나 검증할 수 없습니다.")
    try:
        bundle = validate_live_bundle(raw, config, **validation_options)
    except LiveBundleError as error:
        return LiveBundleResult(error.code, error=error.message)
    return LiveBundleResult("ready", bundle=bundle)
