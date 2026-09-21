import time
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.services.market.client import MarketDataClient
from app.services.market.instruments import InstrumentRegistry
from app.services.market.schemas import (
    Instrument,
    InstrumentSearchResult,
    Quote,
    QuotesResponse,
)
from app.services.upstox.service import get_upstox_auth_service


class MarketService:
    """Turns a list of symbols into normalised quotes."""

    def __init__(
        self,
        client: MarketDataClient,
        registry: InstrumentRegistry,
        cache_seconds: int,
    ) -> None:
        self._client = client
        self._registry = registry
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Quote]] = {}

    async def resolve(
        self, symbols: list[str]
    ) -> tuple[dict[str, Instrument], list[str]]:
        """Symbol -> instrument, plus whatever did not map. Used by the stream."""
        return await self._registry.resolve_many(_dedupe(symbols))

    async def search_instruments(
        self, query: str, limit: int
    ) -> list[InstrumentSearchResult]:
        return await self._registry.search(query.strip(), limit)

    async def get_historical_candles(
        self,
        symbol: str,
        unit: str,
        interval: str,
        to_date: str,
        from_date: str,
    ) -> tuple[Instrument, list[list]]:
        resolved = await self._registry.resolve(symbol)
        if resolved is None:
            raise ValueError(f"Unknown market symbol: {symbol}")
        token = await get_upstox_auth_service().get_access_token()
        candles = await self._client.historical_candles(
            resolved.instrument_key,
            unit,
            interval,
            to_date,
            from_date,
            token,
        )
        return resolved, candles

    async def get_quotes(self, symbols: list[str]) -> QuotesResponse:
        symbols = _dedupe(symbols)
        resolved, unresolved = await self._registry.resolve_many(symbols)

        fresh, stale = self._split_by_cache(resolved)
        if stale:
            fresh.update(await self._fetch(stale))

        quotes = [fresh[s] for s in symbols if s in fresh]
        return QuotesResponse(quotes=quotes, unresolved=unresolved)

    def _split_by_cache(
        self, resolved: dict[str, Instrument]
    ) -> tuple[dict[str, Quote], dict[str, Instrument]]:
        """Serve anything quoted in the last few seconds from memory.

        The tape polls every 30s per visitor; without this, ten visitors mean
        ten Upstox calls for the same seven numbers.
        """
        now = time.monotonic()
        fresh: dict[str, Quote] = {}
        stale: dict[str, Instrument] = {}
        for symbol, instrument in resolved.items():
            entry = self._cache.get(instrument.instrument_key)
            if entry and (now - entry[0]) < self._cache_seconds:
                fresh[symbol] = entry[1]
            else:
                stale[symbol] = instrument
        return fresh, stale

    async def _fetch(self, wanted: dict[str, Instrument]) -> dict[str, Quote]:
        token = await get_upstox_auth_service().get_access_token()
        keys = [i.instrument_key for i in wanted.values()]
        raw = await self._client.ltp(keys, token)

        # Upstox echoes keys back with a colon ("NSE_INDEX:Nifty 50") but the
        # payload carries the original pipe form, so match on that.
        by_key = {
            payload.get("instrument_token"): payload
            for payload in raw.values()
            if isinstance(payload, dict)
        }

        now = time.monotonic()
        quotes: dict[str, Quote] = {}
        for symbol, instrument in wanted.items():
            payload = by_key.get(instrument.instrument_key)
            if payload is None:
                continue
            quote = _to_quote(symbol, instrument, payload)
            quotes[symbol] = quote
            self._cache[instrument.instrument_key] = (now, quote)
        return quotes


def build_quote(
    symbol: str,
    instrument: Instrument,
    last_price: float,
    prev_close: float | None,
) -> Quote:
    """Shared by the REST path and the websocket feed.

    Both must produce the identical shape, or the frontend would see a quote
    change form the moment the live stream takes over from the first fetch.
    """
    change = change_percent = None
    if prev_close:
        change = round(last_price - prev_close, 2)
        change_percent = round((change / prev_close) * 100, 2)

    return Quote(
        symbol=symbol,
        name=instrument.name,
        instrument_key=instrument.instrument_key,
        last_price=last_price,
        prev_close=prev_close,
        change=change,
        change_percent=change_percent,
        up=(change or 0) >= 0,
    )


def _to_quote(symbol: str, instrument: Instrument, payload: dict) -> Quote:
    prev_close = payload.get("cp")
    return build_quote(
        symbol=symbol,
        instrument=instrument,
        last_price=float(payload.get("last_price") or 0.0),
        prev_close=float(prev_close) if prev_close else None,
    )


def _dedupe(symbols: list[str]) -> list[str]:
    """Keep the caller's order, drop repeats and blanks."""
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        cleaned = symbol.strip()
        if cleaned and cleaned.upper() not in seen:
            seen.add(cleaned.upper())
            out.append(cleaned)
    return out


def build_market_service(settings: Settings) -> MarketService:
    registry = InstrumentRegistry(
        url=settings.upstox_instruments_url,
        cache_path=settings.instruments_cache_path,
        max_age_seconds=settings.instruments_max_age_seconds,
    )
    return MarketService(
        client=MarketDataClient(settings.upstox_market_base_url),
        registry=registry,
        cache_seconds=settings.quote_cache_seconds,
    )


@lru_cache
def get_market_service() -> MarketService:
    return build_market_service(get_settings())
