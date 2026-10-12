"""Offline tests for actual prices independent of turnover/recommendation gates."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.radar.models import KST
from app.radar.storage import save_provider_health, save_state
from app.settings import runtime_dir


AS_OF = datetime(2026, 10, 12, 8, 0, tzinfo=KST)
SYMBOLS = ["000001", "000002", "000003"]


def quote(symbol=SYMBOLS[0], **overrides):
    values = {"symbol": symbol, "lastPrice": "100", "currency": "KRW", "timestamp": AS_OF.isoformat()}
    values.update(overrides)
    return values


def proof(authentication=None, venue=None, **extras):
    value = {
        "authentication": {"status": "passed"} if authentication is None else authentication,
        "venue": {"verified": True} if venue is None else venue,
        **extras,
    }
    (runtime_dir() / "toss-probe.json").write_text(json.dumps(value))


def calendar(day=AS_OF.date()):
    stamp = AS_OF.replace(year=day.year, month=day.month, day=day.day)
    return {"today": {"date": day.isoformat(), "integrated": {
        "preMarket": {"startTime": stamp.replace(hour=8).isoformat(), "endTime": stamp.replace(hour=8, minute=50).isoformat()},
        "regularMarket": {"startTime": stamp.replace(hour=9).isoformat(), "endTime": stamp.replace(hour=15, minute=30).isoformat()},
        "afterMarket": {"startTime": stamp.replace(hour=15, minute=40).isoformat(), "endTime": stamp.replace(hour=20).isoformat()},
    }}}


@pytest.fixture
def quotes(monkeypatch):
    import app.radar.quotes as module

    monkeypatch.setenv("TOSS_DATA_MODE", "live")
    proof()
    save_state("calendar:" + AS_OF.date().isoformat(), calendar(), AS_OF - timedelta(minutes=1))
    return module


def test_nxt_eight_am_quotes_are_live_without_turnover_or_recommendation_bundle(quotes):
    save_provider_health("TOSS_TURNOVER", {"status": "UNAVAILABLE"}, AS_OF)
    quotes.save_quotes([quote(symbol) for symbol in SYMBOLS], received_at=AS_OF, requested_symbols=SYMBOLS)
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "LIVE"
    assert len(data["items"]) == 3
    assert all(item["status"] == "LIVE" for item in data["items"])
    assert all(item["price"] == 100 for item in data["items"])
    assert all(item["quote_at"] == AS_OF.isoformat() for item in data["items"])
    assert all(item["first_seen_at"] == AS_OF.isoformat() for item in data["items"])
    assert all(item["source"] for item in data["items"])


@pytest.mark.parametrize("age,status", [(15, "LIVE"), (15.001, "STALE")])
def test_quote_age_uses_provider_timestamp_with_fifteen_second_boundary(quotes, age, status):
    received = AS_OF + timedelta(seconds=age)
    quotes.save_quotes([quote()], received_at=received, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(received)
    assert data["status"] == status
    assert data["items"][0]["status"] == status
    assert data["items"][0]["latency_seconds"] == pytest.approx(age)


def test_read_rechecks_age_instead_of_reusing_previous_live_health(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    assert quotes.quotes_snapshot(AS_OF)["status"] == "LIVE"
    later = quotes.quotes_snapshot(AS_OF + timedelta(seconds=16))
    assert later["status"] == "STALE"
    assert later["items"][0]["latency_seconds"] == 16


def test_missing_requested_symbol_keeps_partial_coverage_explicit(quotes):
    quotes.save_quotes([quote(symbol) for symbol in SYMBOLS[:2]], received_at=AS_OF, requested_symbols=SYMBOLS)
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "PARTIAL"
    assert {item["symbol"] for item in data["items"]} == set(SYMBOLS[:2])
    assert all(item["status"] == "LIVE" for item in data["items"])


@pytest.mark.parametrize("closed_clock", [
    AS_OF.replace(hour=8, minute=55),
    AS_OF.replace(hour=20, minute=1),
])
def test_cached_actual_prices_are_stale_during_calendar_gap_or_offhours(quotes, closed_clock):
    quotes.save_quotes([quote(timestamp=closed_clock.isoformat())], received_at=closed_clock,
                       requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(closed_clock)
    assert data["status"] == "STALE"
    assert data["items"][0]["price"] == 100
    assert data["items"][0]["status"] == "STALE"


def test_midnight_does_not_make_yesterdays_cached_quote_live(quotes):
    midnight = AS_OF.replace(hour=0, minute=0, second=5)
    previous_quote = midnight - timedelta(seconds=7)
    quotes.save_quotes([quote(timestamp=previous_quote.isoformat())], received_at=midnight,
                       requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(midnight)
    assert data["status"] == "STALE"
    assert data["items"][0]["quote_at"] == previous_quote.isoformat()
    assert data["items"][0]["price"] == 100


def test_utc_timestamp_is_normalized_to_current_korean_provider_date(quotes):
    utc_stamp = AS_OF.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    assert utc_stamp.startswith("2026-10-11")
    quotes.save_quotes([quote(timestamp=utc_stamp)], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "LIVE"
    assert data["items"][0]["quote_at"] == AS_OF.isoformat()


def test_failed_auth_immediately_marks_cached_fresh_actual_prices_stale(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    proof(authentication={"status": "failed", "http_status": 403, "condition": "ip_not_allowed"})
    data = quotes.quotes_snapshot(AS_OF + timedelta(seconds=1))
    assert data["status"] == "STALE"
    assert data["items"][0]["status"] == "STALE"
    assert data["items"][0]["price"] == 100


def test_unverified_venue_keeps_cached_actual_prices_stale(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    proof(venue={"verified": False})
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "STALE"
    assert data["items"][0]["price"] == 100


def test_network_failure_immediately_marks_cached_fresh_actual_prices_stale(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    quotes.mark_quotes_unavailable(AS_OF + timedelta(seconds=1), "Official quote request failed.")
    data = quotes.quotes_snapshot(AS_OF + timedelta(seconds=1))
    assert data["status"] == "STALE"
    assert data["items"][0]["status"] == "STALE"
    assert data["items"][0]["price"] == 100


def test_explicit_demo_never_labels_actual_cached_prices_live(quotes, monkeypatch):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    monkeypatch.setenv("TOSS_DATA_MODE", "demo")
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "STALE"
    assert all(item["status"] == "STALE" for item in data["items"])


def test_nullable_provider_time_retains_actual_price_with_unknown_latency(quotes):
    quotes.save_quotes([quote(timestamp=None)], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "STALE"
    assert data["items"][0]["price"] == 100
    assert data["items"][0]["quote_at"] is None
    assert data["items"][0]["latency_seconds"] is None
    assert data["items"][0]["status"] == "STALE"


@pytest.mark.parametrize("invalid", [
    {"lastPrice": "0"}, {"lastPrice": "-1"}, {"lastPrice": "nan"},
    {"lastPrice": "inf"}, {"lastPrice": "not-a-price"}, {"currency": "USD"},
    {"timestamp": (AS_OF + timedelta(seconds=1)).isoformat()},
    {"lastPrice": True}, {"lastPrice": None},
    {"timestamp": "2026-10-12T08:00:00"}, {"timestamp": "not-a-timestamp"},
])
def test_invalid_nonkrw_or_future_quotes_do_not_fabricate_display_values(quotes, invalid):
    quotes.save_quotes([quote(**invalid)], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "UNAVAILABLE"
    assert data["items"] == []


def test_repeated_identical_quote_preserves_original_first_seen(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    later = AS_OF + timedelta(seconds=5)
    quotes.save_quotes([quote()], received_at=later, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(later)
    assert len(data["items"]) == 1
    assert data["items"][0]["first_seen_at"] == AS_OF.isoformat()
    assert data["items"][0]["latency_seconds"] == 5


def test_point_in_time_price_revision_keeps_earlier_observation_immutable(quotes):
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    revised_at = AS_OF + timedelta(seconds=10)
    quotes.save_quotes([quote(lastPrice="101")], received_at=revised_at, requested_symbols=[SYMBOLS[0]])
    old = quotes.quotes_snapshot(AS_OF + timedelta(seconds=5))
    new = quotes.quotes_snapshot(revised_at)
    assert old["items"][0]["price"] == 100
    assert old["items"][0]["first_seen_at"] == AS_OF.isoformat()
    assert new["items"][0]["price"] == 101
    assert new["items"][0]["first_seen_at"] == revised_at.isoformat()


def test_future_first_seen_price_is_absent_from_past_snapshot(quotes):
    later = AS_OF + timedelta(seconds=10)
    quotes.save_quotes([quote()], received_at=later, requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(AS_OF + timedelta(seconds=5))
    assert data["items"] == []
    assert data["status"] == "UNAVAILABLE"


def test_snapshot_without_stored_quotes_is_unavailable(quotes):
    data = quotes.quotes_snapshot(AS_OF)
    assert data["status"] == "UNAVAILABLE"
    assert data["items"] == []


def test_quote_health_is_separate_from_turnover_provider_health(quotes):
    from app.radar.storage import read_provider_health

    turnover = {"status": "UNAVAILABLE", "reason": "No cumulative counter observations."}
    save_provider_health("TOSS_TURNOVER", turnover, AS_OF)
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    health = read_provider_health()
    assert health["TOSS_TURNOVER"] == turnover
    assert health["TOSS_QUOTES"]["status"] == "LIVE"


def test_unrequested_provider_rows_are_not_displayed_as_monitored_quotes(quotes):
    quotes.save_quotes([quote(), quote(SYMBOLS[1], lastPrice="200")], received_at=AS_OF,
                       requested_symbols=[SYMBOLS[0]])
    data = quotes.quotes_snapshot(AS_OF)
    assert [item["symbol"] for item in data["items"]] == [SYMBOLS[0]]
    assert data["coverage"]["symbols_expected"] == 1


def test_public_quotes_get_reads_cache_without_http_oauth_or_secret_output(quotes, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.brokers.toss.documented import OfficialTossClient
    from app.radar.routes import router

    secret_marker = "DO_NOT_EXPOSE_CONSTRUCTED_QUOTE_SECRET"
    proof(authentication={"status": "passed", "access_token": secret_marker},
          venue={"verified": True, "raw_response": secret_marker})
    monkeypatch.setenv("TOSS_CLIENT_SECRET", secret_marker)
    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append("network_or_auth")
        raise AssertionError("Cached quote GET must not authenticate or send HTTP")
    monkeypatch.setattr(OfficialTossClient, "authenticate", forbidden)
    monkeypatch.setattr(OfficialTossClient, "_send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return AS_OF.astimezone(tz) if tz is not None else AS_OF.replace(tzinfo=None)
    monkeypatch.setattr(quotes, "datetime", FrozenDateTime)
    api = FastAPI()
    api.include_router(router)
    with TestClient(api) as client:
        response = client.get("/api/market/quotes")
    assert response.status_code == 200
    assert response.json()["status"] == "LIVE"
    assert response.json()["items"][0]["price"] == 100
    assert calls == []
    assert secret_marker not in response.text


def test_read_with_no_quote_tables_returns_unavailable_without_api_calls(monkeypatch):
    import app.radar.quotes as module
    from app.brokers.toss.documented import OfficialTossClient

    monkeypatch.setenv("TOSS_DATA_MODE", "live")
    proof()
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append("auth")
        raise AssertionError("Read-only quote snapshot attempted authentication")
    monkeypatch.setattr(OfficialTossClient, "authenticate", forbidden)
    data = module.quotes_snapshot(AS_OF)
    assert data["items"] == []
    assert data["status"] == "UNAVAILABLE"
    assert calls == []


def test_current_requested_symbol_is_not_displaced_by_two_hundred_old_cached_symbols(quotes, monkeypatch):
    old_symbols = [f"{index:06d}" for index in range(200)]
    quotes.save_quotes([quote(symbol) for symbol in old_symbols], received_at=AS_OF,
                       requested_symbols=old_symbols)
    later = AS_OF + timedelta(seconds=5)
    new_symbol = "999999"
    quotes.save_quotes([quote(new_symbol, lastPrice="110", timestamp=later.isoformat())],
                       received_at=later, requested_symbols=[new_symbol])
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz) if tz is not None else later.replace(tzinfo=None)
    monkeypatch.setattr(quotes, "datetime", FrozenDateTime)
    current = quotes.quotes_snapshot()
    assert current["status"] == "LIVE"
    assert current["coverage"]["symbols_expected"] == 1
    assert [(item["symbol"], item["price"], item["status"]) for item in current["items"]] == [
        (new_symbol, 110, "LIVE"),
    ]
    earlier = quotes.quotes_snapshot(AS_OF)
    assert {item["symbol"] for item in earlier["items"]} == set(old_symbols)
    assert all(item["price"] == 100 for item in earlier["items"])


def test_missing_time_quote_preserves_known_provider_price_in_current_cache(quotes, monkeypatch):
    from app.radar.storage import connection

    quotes.save_quotes([quote()], received_at=AS_OF, requested_symbols=[SYMBOLS[0]])
    later = AS_OF + timedelta(seconds=5)
    quotes.save_quotes([quote(lastPrice="110", timestamp=None)], received_at=later,
                       requested_symbols=[SYMBOLS[0]])
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz) if tz is not None else later.replace(tzinfo=None)
    monkeypatch.setattr(quotes, "datetime", FrozenDateTime)
    current = quotes.quotes_snapshot()
    assert current["status"] == "STALE"
    assert current["items"][0]["price"] == 100
    assert current["items"][0]["quote_at"] == AS_OF.isoformat()
    assert current["items"][0]["first_seen_at"] == AS_OF.isoformat()
    assert current["items"][0]["status"] == "STALE"
    with connection() as conn:
        rows = conn.execute("SELECT payload_json FROM quote_observations WHERE symbol=?", (SYMBOLS[0],)).fetchall()
    history = [json.loads(row["payload_json"]) for row in rows]
    assert len(history) == 2
    assert any(item["price"] == 110 and item["quote_at"] is None
               and item["first_seen_at"] == later.isoformat() for item in history)
