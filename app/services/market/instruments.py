import asyncio
import gzip
import json
import time
from pathlib import Path

import httpx

from app.services.market.exceptions import InstrumentsUnavailable
from app.services.market.schemas import Instrument, InstrumentSearchResult

DOWNLOAD_TIMEOUT = 60.0

# Upstox ships everything — futures, options, government bonds — in one file.
# The site only ever quotes cash equities and indices, so we keep just those.
KEPT_TYPES = {"EQ", "INDEX"}


class InstrumentRegistry:
    """Maps a symbol people type ("INFY", "NIFTY 50") to an Upstox instrument key.

    Upstox needs 'NSE_EQ|INE009A01021', not 'INFY'. The mapping lives in a
    3 MB gzipped file they refresh around 6 AM daily, so we cache it on disk
    and only re-download when it goes stale.
    """

    def __init__(self, url: str, cache_path: Path, max_age_seconds: int) -> None:
        self._url = url
        self._cache_path = Path(cache_path)
        self._max_age = max_age_seconds
        self._by_symbol: dict[str, Instrument] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, symbol: str) -> Instrument | None:
        await self._ensure_loaded()
        return self._by_symbol.get(_normalise(symbol))

    async def resolve_many(
        self, symbols: list[str]
    ) -> tuple[dict[str, Instrument], list[str]]:
        """Return (symbol -> instrument) plus the symbols that did not resolve."""
        await self._ensure_loaded()
        found: dict[str, Instrument] = {}
        missing: list[str] = []
        for symbol in symbols:
            instrument = self._by_symbol.get(_normalise(symbol))
            if instrument is None:
                missing.append(symbol)
            else:
                found[symbol] = instrument
        return found, missing

    async def search(self, query: str, limit: int) -> list[InstrumentSearchResult]:
        await self._ensure_loaded()
        needle = _normalise(query)
        matches: dict[str, Instrument] = {}

        for instrument in self._by_symbol.values():
            symbol = _normalise(instrument.trading_symbol)
            name = _normalise(instrument.name)
            if needle in symbol or needle in name:
                matches.setdefault(instrument.instrument_key, instrument)

        ordered = sorted(
            matches.values(),
            key=lambda instrument: (
                not _normalise(instrument.trading_symbol).startswith(needle),
                not _normalise(instrument.name).startswith(needle),
                instrument.trading_symbol,
            ),
        )
        return [
            InstrumentSearchResult(
                instrument_key=instrument.instrument_key,
                trading_symbol=instrument.trading_symbol,
                name=instrument.name,
            )
            for instrument in ordered[:limit]
        ]

    async def _ensure_loaded(self) -> None:
        if self._by_symbol and not self._cache_is_stale():
            return
        async with self._lock:
            # Another coroutine may have loaded it while we waited.
            if self._by_symbol and not self._cache_is_stale():
                return
            if self._cache_is_stale():
                await self._download()
            self._by_symbol = self._read_cache()

    def _cache_is_stale(self) -> bool:
        if not self._cache_path.exists():
            return True
        return (time.time() - self._cache_path.stat().st_mtime) > self._max_age

    async def _download(self) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(self._url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            if self._cache_path.exists():
                return  # Stale data beats no data.
            raise InstrumentsUnavailable(
                f"Could not download the Upstox instrument master: {exc}"
            ) from exc

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(self._cache_path)

    def _read_cache(self) -> dict[str, Instrument]:
        try:
            with gzip.open(self._cache_path, "rt") as handle:
                records = json.load(handle)
        except (OSError, json.JSONDecodeError, EOFError) as exc:
            raise InstrumentsUnavailable(
                f"Could not read the instrument cache at {self._cache_path}: {exc}"
            ) from exc

        table: dict[str, Instrument] = {}
        for record in records:
            if record.get("instrument_type") not in KEPT_TYPES:
                continue
            instrument = Instrument.model_validate(record)
            # An index and an equity can share a name; first one in wins, and
            # the file lists NSE cash equities before anything else.
            table.setdefault(_normalise(instrument.trading_symbol), instrument)
            # Indices are keyed as 'NSE_INDEX|Nifty 50' — let callers use that
            # display name too ("NIFTY 50" and "Nifty 50" both work).
            if instrument.instrument_type == "INDEX":
                table.setdefault(_normalise(instrument.name), instrument)
        return table


def _normalise(symbol: str) -> str:
    """'Nifty 50', 'NIFTY 50' and 'nifty50' must all find the same instrument."""
    return "".join(symbol.upper().split())
