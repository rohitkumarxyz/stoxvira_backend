import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

import websockets
from websockets.exceptions import WebSocketException

from app.core.config import get_settings
from app.services.market.proto import MarketDataFeedV3_pb2 as pb
from app.services.upstox.exceptions import UpstoxTokenExpired
from app.services.upstox.service import get_upstox_auth_service

logger = logging.getLogger(__name__)

# How long a listener's backlog can grow before we start dropping its oldest
# updates. A browser that stops reading must not stall the shared feed.
LISTENER_BACKLOG = 32

RECONNECT_DELAYS = (1, 2, 5, 10, 30)


class MarketFeed:
    """One Upstox websocket for the whole process, fanned out to many listeners.

    Upstox allows only two concurrent websocket connections per user, so this
    cannot be per-request or per-browser. One socket is opened lazily on the
    first listener and shared by everyone; each listener gets its own queue.
    """

    def __init__(self, url: str, mode: str = "ltpc") -> None:
        self._url = url
        self._mode = mode
        self._wanted: set[str] = set()
        self._latest: dict[str, dict] = {}
        self._listeners: set[asyncio.Queue] = set()
        self._socket: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def snapshot(self, instrument_keys: list[str]) -> dict[str, dict]:
        """Last known price per key, for seeding a new listener immediately."""
        return {k: self._latest[k] for k in instrument_keys if k in self._latest}

    async def listen(
        self, instrument_keys: list[str]
    ) -> AsyncIterator[dict[str, dict]]:
        """Yield {instrument_key: ltpc} whenever any of these keys ticks."""
        wanted = set(instrument_keys)
        queue: asyncio.Queue = asyncio.Queue(maxsize=LISTENER_BACKLOG)

        await self._add_listener(queue, wanted)
        try:
            while True:
                update = await queue.get()
                relevant = {k: v for k, v in update.items() if k in wanted}
                if relevant:
                    yield relevant
        finally:
            await self._remove_listener(queue)

    async def _add_listener(self, queue: asyncio.Queue, wanted: set[str]) -> None:
        async with self._lock:
            self._listeners.add(queue)
            new_keys = wanted - self._wanted
            self._wanted |= wanted

            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run())
            elif new_keys:
                await self._send_subscribe(new_keys)

    async def _remove_listener(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._listeners.discard(queue)
            # The socket stays open with its existing subscriptions. Upstox
            # charges nothing for idle keys, and the next visitor reuses them.

    async def stop(self) -> None:
        """Close the socket. Called on application shutdown."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected.clear()

    async def _run(self) -> None:
        attempt = 0
        while True:
            try:
                await self._connect_and_read()
                attempt = 0
            except asyncio.CancelledError:
                if self._socket:
                    await self._socket.close()
                raise
            except UpstoxTokenExpired:
                # Nothing to retry against until someone logs in again.
                logger.warning("Market feed stopped: Upstox token expired.")
                self._connected.clear()
                return
            except (WebSocketException, OSError) as exc:
                logger.warning("Market feed dropped (%s); reconnecting.", exc)
            finally:
                self._connected.clear()
                self._socket = None

            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def _connect_and_read(self) -> None:
        token = await get_upstox_auth_service().get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "*/*"}
        async with websockets.connect(
            self._url, additional_headers=headers, max_size=None
        ) as socket:
            self._socket = socket
            self._connected.set()
            logger.info("Market feed connected.")

            if self._wanted:
                await self._send_subscribe(self._wanted)

            async for raw in socket:
                update = _decode(raw)
                if update:
                    self._latest.update(update)
                    self._broadcast(update)

    async def _send_subscribe(self, keys: set[str]) -> None:
        if not self._socket or not keys:
            return
        request = {
            "guid": uuid.uuid4().hex,
            "method": "sub",
            "data": {"mode": self._mode, "instrumentKeys": sorted(keys)},
        }
        await self._socket.send(json.dumps(request).encode("utf-8"))

    def _broadcast(self, update: dict[str, dict]) -> None:
        for queue in self._listeners:
            if queue.full():
                # Slow consumer: drop its oldest update rather than block
                # every other listener behind it.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(update)


@lru_cache
def get_market_feed() -> MarketFeed:
    """One feed per process — see the class docstring for why that matters."""
    settings = get_settings()
    return MarketFeed(url=settings.upstox_feed_url, mode=settings.upstox_feed_mode)


def _decode(raw: bytes | str) -> dict[str, dict]:
    """Protobuf frame -> {instrument_key: {"ltp": float, "cp": float}}.

    Market status frames carry no prices and come back empty.
    """
    if isinstance(raw, str):
        return {}

    message = pb.FeedResponse()
    try:
        message.ParseFromString(raw)
    except Exception:  # noqa: BLE001 - a malformed frame must not kill the feed
        logger.warning("Could not decode a market feed frame; skipping it.")
        return {}

    out: dict[str, dict] = {}
    for key, feed in message.feeds.items():
        ltpc = _extract_ltpc(feed)
        if ltpc is None:
            continue
        out[key] = {"ltp": ltpc.ltp, "cp": ltpc.cp}
    return out


def _extract_ltpc(feed) -> object | None:
    """LTPC sits in a different place depending on the subscription mode."""
    which = feed.WhichOneof("FeedUnion")
    if which == "ltpc":
        return feed.ltpc
    if which == "fullFeed":
        inner = feed.fullFeed.WhichOneof("FullFeedUnion")
        if inner == "marketFF":
            return feed.fullFeed.marketFF.ltpc
        if inner == "indexFF":
            return feed.fullFeed.indexFF.ltpc
    if which == "firstLevelWithGreeks":
        return feed.firstLevelWithGreeks.ltpc
    return None
