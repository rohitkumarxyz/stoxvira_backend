import asyncio
import json
from datetime import date
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.services.market.feed import MarketFeed, get_market_feed
from app.services.market.schemas import (
    Instrument,
    InstrumentSearchResult,
    Quote,
    QuotesResponse,
)
from app.services.market.service import MarketService, build_quote, get_market_service

router = APIRouter(prefix="/market", tags=["market"])

MAX_SYMBOLS = 200

# Without traffic, proxies and load balancers close an idle SSE stream. A
# comment line keeps it alive and costs nothing — the browser ignores it.
HEARTBEAT_SECONDS = 15
MAX_SEARCH_RESULTS = 20
HISTORY_INTERVALS = {
    "1": ("minutes", 1),
    "3": ("minutes", 3),
    "5": ("minutes", 5),
    "10": ("minutes", 10),
    "15": ("minutes", 15),
    "30": ("minutes", 30),
    "60": ("hours", 1),
    "1D": ("days", 1),
}


@router.get("/history")
async def history(
    symbol: str = Query(min_length=1, max_length=80),
    resolution: str = Query(default="1D"),
    to: date = Query(),
    from_date: date = Query(alias="from"),
    service: MarketService = Depends(get_market_service),
) -> dict:
    """OHLCV candles in the shape expected by the TradingView datafeed."""
    interval = HISTORY_INTERVALS.get(resolution)
    if interval is None:
        raise HTTPException(status_code=400, detail="Unsupported chart resolution.")
    if from_date > to:
        raise HTTPException(status_code=400, detail="Invalid chart date range.")

    unit, amount = interval
    instrument, candles = await service.get_historical_candles(
        symbol,
        unit,
        str(amount),
        to.isoformat(),
        from_date.isoformat(),
    )
    return {
        "symbol": instrument.trading_symbol,
        "name": instrument.name,
        "instrument_key": instrument.instrument_key,
        "candles": candles,
    }


@router.get("/search", response_model=list[InstrumentSearchResult])
async def search(
    q: str = Query(min_length=1, max_length=80, description="Company or ticker"),
    service: MarketService = Depends(get_market_service),
) -> list[InstrumentSearchResult]:
    """Search cash equities and indices by company name or trading symbol."""
    if not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a company name or ticker.",
        )
    return await service.search_instruments(q, MAX_SEARCH_RESULTS)


@router.get("/quotes", response_model=QuotesResponse)
async def quotes(
    symbols: str = Query(
        description="Comma separated, e.g. INFY,SBIN,NIFTY 50",
        examples=["INFY,SBIN,NIFTY 50"],
    ),
    service: MarketService = Depends(get_market_service),
) -> QuotesResponse:
    """Live prices for equities and indices.

    Symbols that cannot be mapped to an Upstox instrument come back in
    `unresolved` rather than failing the whole request.
    """
    return await service.get_quotes(_parse_symbols(symbols))


@router.get("/stream")
async def stream(
    request: Request,
    symbols: str = Query(description="Comma separated, e.g. INFY,NIFTY 50"),
    service: MarketService = Depends(get_market_service),
    feed: MarketFeed = Depends(get_market_feed),
) -> StreamingResponse:
    """Server-sent events, one per price change.

    Upstox allows only a couple of websocket connections per user, so every
    listener here shares the single socket held by MarketFeed.
    """
    wanted = _parse_symbols(symbols)
    resolved, unresolved = await service.resolve(wanted)
    by_key = {i.instrument_key: (symbol, i) for symbol, i in resolved.items()}

    return StreamingResponse(
        _events(request, feed, by_key, unresolved),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _events(
    request: Request,
    feed: MarketFeed,
    by_key: dict[str, tuple[str, Instrument]],
    unresolved: list[str],
) -> AsyncIterator[str]:
    keys = list(by_key)

    yield _sse("meta", {"subscribed": len(keys), "unresolved": unresolved})

    # Anything the feed already knows goes out immediately, so a browser that
    # connects mid-session does not stare at nothing until the next tick.
    snapshot = feed.snapshot(keys)
    if snapshot:
        yield _sse("quotes", _to_quotes(snapshot, by_key))

    updates = feed.listen(keys)
    try:
        while True:
            try:
                update = await asyncio.wait_for(
                    updates.__anext__(), timeout=HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break

            yield _sse("quotes", _to_quotes(update, by_key))
    finally:
        await updates.aclose()


def _to_quotes(
    update: dict[str, dict], by_key: dict[str, tuple[str, Instrument]]
) -> dict:
    quotes: list[Quote] = []
    for key, ltpc in update.items():
        entry = by_key.get(key)
        if entry is None:
            continue
        symbol, instrument = entry
        quotes.append(
            build_quote(
                symbol=symbol,
                instrument=instrument,
                last_price=ltpc.get("ltp") or 0.0,
                prev_close=ltpc.get("cp") or None,
            )
        )
    return {"quotes": [q.model_dump() for q in quotes]}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _parse_symbols(symbols: str) -> list[str]:
    wanted = [s.strip() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pass at least one symbol, e.g. ?symbols=INFY,NIFTY 50",
        )
    if len(wanted) > MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_SYMBOLS} symbols per request, got {len(wanted)}.",
        )
    return wanted
