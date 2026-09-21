import gzip
import json

import httpx
import pytest
import respx

from app.services.market.client import MarketDataClient
from app.services.market.exceptions import MarketDataError
from app.services.market.instruments import InstrumentRegistry
from app.services.market.service import MarketService

INSTRUMENTS_URL = "https://assets.upstox.com/instruments/complete.json.gz"
MARKET_BASE = "https://api.upstox.com/v3"
LTP_URL = f"{MARKET_BASE}/market-quote/ltp"

INSTRUMENT_RECORDS = [
    {
        "segment": "NSE_EQ",
        "name": "INFOSYS LIMITED",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE009A01021",
        "trading_symbol": "INFY",
    },
    {
        "segment": "NSE_EQ",
        "name": "ETERNAL LIMITED",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE758T01015",
        "trading_symbol": "ETERNAL",
    },
    {
        "segment": "NSE_INDEX",
        "name": "Nifty 50",
        "exchange": "NSE",
        "instrument_type": "INDEX",
        "instrument_key": "NSE_INDEX|Nifty 50",
        "trading_symbol": "NIFTY 50",
    },
    {
        # Futures and bonds must be filtered out, or they shadow real symbols.
        "segment": "NSE_FO",
        "name": "INFOSYS 25 SEP FUT",
        "exchange": "NSE",
        "instrument_type": "FUT",
        "instrument_key": "NSE_FO|12345",
        "trading_symbol": "INFY",
    },
]


@pytest.fixture
def instruments_file(tmp_path):
    path = tmp_path / "instruments.json.gz"
    with gzip.open(path, "wt") as handle:
        json.dump(INSTRUMENT_RECORDS, handle)
    return path


@pytest.fixture
def registry(instruments_file):
    return InstrumentRegistry(
        url=INSTRUMENTS_URL, cache_path=instruments_file, max_age_seconds=3600
    )


@pytest.fixture
def market(registry):
    return MarketService(
        client=MarketDataClient(MARKET_BASE), registry=registry, cache_seconds=10
    )


@pytest.fixture(autouse=True)
def stub_token(monkeypatch):
    """The market service asks the auth service for a token; fake it."""

    class _Auth:
        async def get_access_token(self):
            return "test-token"

    monkeypatch.setattr(
        "app.services.market.service.get_upstox_auth_service", lambda: _Auth()
    )


def ltp_payload(**entries) -> dict:
    return {"status": "success", "data": entries}


async def test_registry_resolves_equity_and_index(registry):
    infy = await registry.resolve("INFY")
    nifty = await registry.resolve("NIFTY 50")

    assert infy.instrument_key == "NSE_EQ|INE009A01021"
    assert nifty.instrument_key == "NSE_INDEX|Nifty 50"


async def test_registry_ignores_case_and_spacing(registry):
    assert (await registry.resolve("infy")).trading_symbol == "INFY"
    assert (await registry.resolve("nifty50")).trading_symbol == "NIFTY 50"
    assert (await registry.resolve("Nifty 50")).trading_symbol == "NIFTY 50"


async def test_registry_skips_derivatives(registry):
    """A futures contract shares the INFY symbol — the equity must win."""
    assert (await registry.resolve("INFY")).instrument_key == "NSE_EQ|INE009A01021"


async def test_registry_reports_unknown_symbols(registry):
    found, missing = await registry.resolve_many(["INFY", "TATAMOTORS", "ZOMATO"])

    assert list(found) == ["INFY"]
    assert missing == ["TATAMOTORS", "ZOMATO"]


@respx.mock
async def test_quotes_compute_change_from_previous_close(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1612.0,
                        "cp": 1583.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    }
                }
            ),
        )
    )

    result = await market.get_quotes(["INFY"])
    quote = result.quotes[0]

    assert quote.symbol == "INFY"
    assert quote.last_price == 1612.0
    assert quote.change == 29.0
    assert quote.change_percent == 1.83
    assert quote.up is True


@respx.mock
async def test_falling_price_is_marked_down(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1500.0,
                        "cp": 1583.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    }
                }
            ),
        )
    )

    quote = (await market.get_quotes(["INFY"])).quotes[0]

    assert quote.change == -83.0
    assert quote.change_percent == -5.24
    assert quote.up is False


@respx.mock
async def test_unresolved_symbols_do_not_fail_the_request(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1612.0,
                        "cp": 1583.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    }
                }
            ),
        )
    )

    result = await market.get_quotes(["INFY", "TATAMOTORS"])

    assert [q.symbol for q in result.quotes] == ["INFY"]
    assert result.unresolved == ["TATAMOTORS"]


@respx.mock
async def test_second_call_is_served_from_cache(market):
    route = respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1612.0,
                        "cp": 1583.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    }
                }
            ),
        )
    )

    await market.get_quotes(["INFY"])
    await market.get_quotes(["INFY"])

    assert route.call_count == 1


@respx.mock
async def test_caller_order_is_preserved(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_INDEX:Nifty 50": {
                        "last_price": 23346.4,
                        "cp": 23270.6,
                        "instrument_token": "NSE_INDEX|Nifty 50",
                    },
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1612.0,
                        "cp": 1583.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    },
                }
            ),
        )
    )

    result = await market.get_quotes(["INFY", "NIFTY 50"])

    assert [q.symbol for q in result.quotes] == ["INFY", "NIFTY 50"]


@respx.mock
async def test_upstox_failure_becomes_market_data_error(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            401, json={"errors": [{"message": "Invalid token"}]}
        )
    )

    with pytest.raises(MarketDataError, match="Invalid token"):
        await market.get_quotes(["INFY"])


@respx.mock
async def test_missing_previous_close_leaves_change_blank(market):
    respx.get(LTP_URL).mock(
        return_value=httpx.Response(
            200,
            json=ltp_payload(
                **{
                    "NSE_EQ:INE009A01021": {
                        "last_price": 1612.0,
                        "instrument_token": "NSE_EQ|INE009A01021",
                    }
                }
            ),
        )
    )

    quote = (await market.get_quotes(["INFY"])).quotes[0]

    assert quote.change is None
    assert quote.change_percent is None


async def test_duplicate_symbols_are_collapsed(market):
    from app.services.market.service import _dedupe

    assert _dedupe(["INFY", "infy", " INFY ", "", "SBIN"]) == ["INFY", "SBIN"]
