"""Exercise the normalized boundary without API calls or claimed broker data."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat

import pytest

from app.market.live_bundle import (
    LiveBundleError, load_live_bundle, save_live_bundle, validate_live_bundle,
)
from app.settings import StrategyConfig
from app.strategy.engine import analyze_stock


NOW = datetime(2030, 5, 16, 1, 24, tzinfo=timezone.utc)


@pytest.fixture
def bundle_data():
    """Invented observations only for validation tests, never used as live data."""
    candles = [{
        "time": (NOW.date() - timedelta(days=39 - index)).isoformat(),
        "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0,
        "volume": 1000.0, "integrated": True, "source": "toss", "venue": "integrated",
    } for index in range(40)]
    intraday = [{
        "time": (NOW - timedelta(minutes=5 - index)).isoformat(),
        "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0,
        "volume": 1000.0, "integrated": True, "source": "toss", "venue": "integrated",
        "interval": "1m",
    } for index in range(6)]
    stock = {
        "symbol": "000001", "name": "테스트 종목", "price": 101.0,
        "changePct": 1.0, "tradedValue": 10_000_000_000.0, "volume": 100_000.0,
        "volumeRatio": 1.2, "volumeAcceleration": 1.0,
        "flow": {"foreign": 100.0, "institution": -5.0, "program": 0.0},
        "flowUnit": "shares",
        "flowVenue": {"foreign": "KRX+NXT · 등록외국인", "institution": "KRX+NXT", "program": "KRX only"},
        "flowAsOf": {"investor": NOW.date().isoformat(), "program": NOW.date().isoformat()},
        "candles": candles, "intraday": intraday, "timestamp": NOW.isoformat(),
        "source": "toss", "integrated": True, "venue": "integrated",
    }
    stocks = [deepcopy(stock) for _ in range(3)]
    for index, item in enumerate(stocks, 1):
        item["symbol"] = f"{index:06d}"
    return {
        "source": "toss", "mode": "live", "asOf": NOW.isoformat(), "stocks": stocks,
        "usReturns": {"NVDA": 2.0, "MRVL": -1.0},
        "market": {
            "source": "toss", "session": "REGULAR", "sessionLabel": "KRX 정규장",
            "isOpen": True,
            "indices": [{"name": "KOSPI", "value": 3200.0, "changePct": 1.0, "source": "toss"}],
            "usMarkets": [{"symbol": "NVDA", "name": "NVIDIA", "changePct": 2.0, "source": "toss"}],
        },
        "venue": {
            "verified": True, "price_basis": "integrated", "daily_candle_basis": "integrated",
            "includes_nxt": True,
            "evidence": {key: f"test-verification:{key}" for key in
                         ("quote_venue", "daily_ohlc", "nxt_premarket", "nxt_aftermarket")},
        },
    }


def validate(data, **options):
    return validate_live_bundle(data, StrategyConfig(), now=NOW, **options)


def test_complete_live_bundle_is_engine_compatible(bundle_data):
    bundle = validate(bundle_data, required_us_tickers=["NVDA", "MRVL"], required_symbols=["000001", "000002", "000003"])
    assert bundle.source == "toss"
    assert bundle.mode == "live"
    assert bundle.asOf == NOW
    stock = bundle.stock_dicts()[0]
    result = analyze_stock(stock, [], StrategyConfig())
    assert result["symbol"] == "000001"
    assert result["price"] == 101.0


@pytest.mark.parametrize("field,value", [
    ("price", 0), ("price", -1), ("price", float("nan")),
    ("price", float("inf")), ("changePct", float("-inf")),
    ("volume", -1), ("tradedValue", -1), ("volumeRatio", float("nan")),
    ("volumeAcceleration", float("inf")), ("price", True),
])
def test_nonfinite_invalid_stock_numbers_rejected(bundle_data, field, value):
    bundle_data["stocks"][0][field] = value
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "invalid"


@pytest.mark.parametrize("field,value", [
    ("open", 0), ("close", -1), ("high", 99), ("low", 102),
    ("close", float("nan")), ("volume", float("inf")),
])
def test_invalid_candle_values_rejected(bundle_data, field, value):
    bundle_data["stocks"][0]["candles"][-1][field] = value
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


@pytest.mark.parametrize("section", ["candles", "intraday"])
@pytest.mark.parametrize("problem", ["duplicate", "reverse", "synthetic", "krx", "implicit_true"])
def test_candle_order_and_provenance_are_required(bundle_data, section, problem):
    candles = bundle_data["stocks"][0][section]
    if problem == "duplicate":
        candles[-1]["time"] = candles[-2]["time"]
    elif problem == "reverse":
        candles.reverse()
    elif problem == "synthetic":
        candles[-1]["source"] = "synthetic"
    elif problem == "krx":
        candles[-1]["integrated"] = False
    else:
        candles[-1]["integrated"] = 1
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


def test_missing_flows_are_not_replaced_with_zero(bundle_data):
    del bundle_data["stocks"][0]["flow"]["program"]
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


def test_flow_units_venues_and_observation_dates_preserved(bundle_data):
    stock = validate(bundle_data).stock_dicts()[0]
    assert stock["flowUnit"] == "shares"
    assert stock["flowVenue"] == {
        "foreign": "KRX+NXT · 등록외국인", "institution": "KRX+NXT", "program": "KRX only",
    }
    assert stock["flowAsOf"] == {"investor": NOW.date().isoformat(), "program": NOW.date().isoformat()}
    del bundle_data["stocks"][0]["flowUnit"]
    assert validate(bundle_data).stock_dicts()[0]["flowUnit"] == "shares"


@pytest.mark.parametrize("field,value", [
    ("flowUnit", "KRW"),
    ("flowVenue", {"foreign": "KRX+NXT · 등록외국인", "institution": "KRX+NXT", "program": "KRX+NXT"}),
    ("flowVenue", {"foreign": "KRX+NXT · 등록외국인", "institution": "KRX+NXT"}),
    ("flowAsOf", {"investor": NOW.date().isoformat()}),
    ("flowAsOf", {"investor": NOW.isoformat(), "program": NOW.date().isoformat()}),
    ("flowAsOf", {"investor": (NOW.date() + timedelta(days=1)).isoformat(), "program": NOW.date().isoformat()}),
])
def test_flow_provenance_cannot_claim_undocumented_integration(bundle_data, field, value):
    bundle_data["stocks"][0][field] = value
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


def test_missing_indices_are_explicit_without_fake_observations(bundle_data):
    bundle_data["market"]["indices"] = []
    assert validate(bundle_data).market.indices == []
    bundle_data["stocks"][0]["seedThemeId"] = "actual-configured-theme"
    assert validate(bundle_data).stock_dicts()[0]["seedThemeId"] == "actual-configured-theme"


@pytest.mark.parametrize("field", ["source", "integrated", "timestamp"])
def test_missing_stock_provenance_rejected(bundle_data, field):
    del bundle_data["stocks"][0][field]
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


@pytest.mark.parametrize("kind", ["total", "daily", "intraday", "configured_days", "configured_count", "volume"])
def test_insufficient_observations_rejected(bundle_data, kind):
    config = StrategyConfig()
    if kind == "total":
        bundle_data["stocks"].pop()
    elif kind == "daily":
        bundle_data["stocks"][0]["candles"] = bundle_data["stocks"][0]["candles"][-29:]
    elif kind == "intraday":
        bundle_data["stocks"][0]["intraday"].pop()
    elif kind == "configured_days":
        config.moving_average.medium = 60
    elif kind == "configured_count":
        config.theme.min_constituents = 4
    else:
        for candle in bundle_data["stocks"][0]["candles"]:
            candle["volume"] = 0
    with pytest.raises(LiveBundleError):
        validate_live_bundle(bundle_data, config, now=NOW)


@pytest.mark.parametrize("field", ["quote_venue", "daily_ohlc", "nxt_premarket", "nxt_aftermarket"])
def test_every_venue_evidence_reference_required(bundle_data, field):
    bundle_data["venue"]["evidence"][field] = "   "
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


@pytest.mark.parametrize("field,value", [
    ("verified", False), ("verified", 1), ("includes_nxt", False),
    ("price_basis", "krx_only"), ("daily_candle_basis", "unknown"),
])
def test_unverified_venue_rejected(bundle_data, field, value):
    bundle_data["venue"][field] = value
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


def test_requested_universe_cannot_be_partially_filled(bundle_data):
    with pytest.raises(LiveBundleError, match="미국"):
        validate(bundle_data, required_us_tickers=["NVDA", "ABSENT"])
    with pytest.raises(LiveBundleError, match="국내"):
        validate(bundle_data, required_symbols=["999999"])
    bundle_data["usReturns"]["NVDA"] = float("nan")
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


def test_metadata_cannot_mix_synthetic_or_inconsistent_returns(bundle_data):
    bundle_data["market"]["usMarkets"][0]["changePct"] = 99.0
    with pytest.raises(LiveBundleError):
        validate(bundle_data)
    bundle_data["market"]["usMarkets"][0]["changePct"] = 2.0
    bundle_data["market"]["indices"][0]["source"] = "synthetic"
    with pytest.raises(LiveBundleError):
        validate(bundle_data)
    bundle_data["market"]["indices"][0]["source"] = "toss"
    bundle_data["market"]["session"] = "SIMULATED"
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


@pytest.mark.parametrize("field", ["asOf", "timestamp", "intraday"])
def test_timestamps_require_timezones(bundle_data, field):
    naive = NOW.replace(tzinfo=None).isoformat()
    if field == "asOf":
        bundle_data["asOf"] = naive
    elif field == "timestamp":
        bundle_data["stocks"][0]["timestamp"] = naive
    else:
        bundle_data["stocks"][0]["intraday"][-1]["time"] = naive
    with pytest.raises(LiveBundleError):
        validate(bundle_data)


@pytest.mark.parametrize("kind", ["snapshot", "quote", "intraday", "closed_quote"])
def test_stale_observations_are_explicit_even_when_closed(bundle_data, kind):
    if kind == "snapshot":
        bundle_data["asOf"] = (NOW - timedelta(seconds=151)).isoformat()
    elif kind in ("quote", "closed_quote"):
        bundle_data["stocks"][0]["timestamp"] = (NOW - timedelta(seconds=301)).isoformat()
        if kind == "closed_quote":
            bundle_data["market"].update(session="CLOSED", sessionLabel="휴장", isOpen=False)
    else:
        for candle in bundle_data["stocks"][0]["intraday"]:
            candle["time"] = (datetime.fromisoformat(candle["time"]) - timedelta(seconds=301)).isoformat()
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "stale"


@pytest.mark.parametrize("days_old", [11, 3650])
def test_fresh_quotes_cannot_hide_stale_daily_history(bundle_data, days_old):
    # Quote and intraday timestamps remain fresh; the history alone is stale.
    for candle in bundle_data["stocks"][0]["candles"]:
        candle["time"] = (datetime.fromisoformat(candle["time"]).date() - timedelta(days=days_old)).isoformat()
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "stale"


def test_daily_history_must_reach_verified_previous_business_date(bundle_data):
    bundle_data["market"]["tradingDate"] = NOW.date().isoformat()
    bundle_data["market"]["previousBusinessDate"] = (NOW.date() - timedelta(days=1)).isoformat()
    for candle in bundle_data["stocks"][0]["candles"]:
        candle["time"] = (datetime.fromisoformat(candle["time"]).date() - timedelta(days=2)).isoformat()
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "stale"
    bundle_data["market"]["previousBusinessDate"] = (NOW.date() - timedelta(days=2)).isoformat()
    assert validate(bundle_data).mode == "live"


@pytest.mark.parametrize("calendar", [
    {"tradingDate": (NOW.date() + timedelta(days=1)).isoformat()},
    {"previousBusinessDate": (NOW.date() + timedelta(days=1)).isoformat()},
    {"tradingDate": (NOW.date() - timedelta(days=2)).isoformat(), "previousBusinessDate": NOW.date().isoformat()},
    {"tradingDate": NOW.isoformat()},
])
def test_market_calendar_dates_are_real_and_ordered(bundle_data, calendar):
    bundle_data["market"].update(calendar)
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "invalid"


def test_freshness_limits_are_configurable_and_clock_injected(bundle_data):
    bundle_data["asOf"] = (NOW - timedelta(seconds=200)).isoformat()
    for stock in bundle_data["stocks"]:
        stock["timestamp"] = bundle_data["asOf"]
        for candle in stock["intraday"]:
            candle["time"] = (datetime.fromisoformat(candle["time"]) - timedelta(seconds=200)).isoformat()
    validate(bundle_data, max_snapshot_age_seconds=201, max_quote_age_seconds=400)
    with pytest.raises(LiveBundleError):
        validate(bundle_data, max_snapshot_age_seconds=0)
    with pytest.raises(LiveBundleError):
        validate_live_bundle(bundle_data, StrategyConfig(), now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("kind", ["snapshot", "quote", "intraday", "daily"])
def test_future_observations_rejected(bundle_data, kind):
    if kind == "snapshot":
        bundle_data["asOf"] = (NOW + timedelta(seconds=31)).isoformat()
    elif kind == "quote":
        bundle_data["stocks"][0]["timestamp"] = (NOW + timedelta(seconds=31)).isoformat()
    elif kind == "intraday":
        bundle_data["stocks"][0]["intraday"][-1]["time"] = (NOW + timedelta(seconds=31)).isoformat()
    else:
        bundle_data["stocks"][0]["candles"][-1]["time"] = (NOW.date() + timedelta(days=1)).isoformat()
    with pytest.raises(LiveBundleError) as caught:
        validate(bundle_data)
    assert caught.value.code == "invalid"


def test_mutated_model_is_revalidated(bundle_data):
    model = validate(bundle_data)
    model.stocks[0].price = -1
    with pytest.raises(LiveBundleError):
        validate(model)


def test_snapshot_is_atomic_private_and_revalidated_each_read(bundle_data, tmp_path):
    path = tmp_path / "data" / "live_snapshot.json"
    config = StrategyConfig()
    result = load_live_bundle(config, path=path, now=NOW)
    assert result.status == "missing" and result.bundle is None
    save_live_bundle(bundle_data, config, path=path, now=NOW)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))
    result = load_live_bundle(config, path=path, now=NOW)
    assert result.ready and result.bundle.source == "toss"
    assert load_live_bundle(config, path=path, now=NOW + timedelta(seconds=151)).status == "stale"
    stale = load_live_bundle(config, path=path, now=NOW + timedelta(seconds=151))
    assert stale.bundle is None
    saved = json.loads(path.read_text())
    saved["stocks"][0]["candles"][-1]["source"] = "synthetic"
    path.write_text(json.dumps(saved))
    invalid = load_live_bundle(config, path=path, now=NOW)
    assert invalid.status == "invalid" and invalid.bundle is None


def test_failed_validation_preserves_prior_snapshot(bundle_data, tmp_path):
    path = tmp_path / "live_snapshot.json"
    config = StrategyConfig()
    save_live_bundle(bundle_data, config, path=path, now=NOW)
    original = path.read_bytes()
    bundle_data["stocks"][0]["price"] = -1
    with pytest.raises(LiveBundleError):
        save_live_bundle(bundle_data, config, path=path, now=NOW)
    assert path.read_bytes() == original


def test_failed_atomic_replace_preserves_snapshot_and_cleans_temp(bundle_data, tmp_path, monkeypatch):
    path = tmp_path / "live_snapshot.json"
    config = StrategyConfig()
    save_live_bundle(bundle_data, config, path=path, now=NOW)
    original = path.read_bytes()

    def fail_replace(self, target):
        raise OSError("sensitive value that must not escape")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(LiveBundleError) as caught:
        save_live_bundle(bundle_data, config, path=path, now=NOW)
    assert "sensitive" not in str(caught.value)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_storage_rejects_secret_fields_without_echoing_value(bundle_data, tmp_path):
    path = tmp_path / "live_snapshot.json"
    bundle_data["TOSS_ACCESS_TOKEN"] = "test-secret-value-must-never-be-shown"
    path.write_text(json.dumps(bundle_data))
    result = load_live_bundle(StrategyConfig(), path=path, now=NOW)
    assert result.status == "invalid"
    assert "test-secret-value" not in result.error
    path.write_text("{broken json with test-secret-value")
    result = load_live_bundle(StrategyConfig(), path=path, now=NOW)
    assert result.status == "invalid" and "test-secret-value" not in result.error


def test_default_snapshot_path_uses_runtime_dir(bundle_data, monkeypatch, tmp_path):
    monkeypatch.setenv("API_DATA_DIR", str(tmp_path / "runtime"))
    save_live_bundle(bundle_data, StrategyConfig(), now=NOW)
    assert (tmp_path / "runtime" / "live_snapshot.json").exists()
    assert load_live_bundle(StrategyConfig(), now=NOW).ready
