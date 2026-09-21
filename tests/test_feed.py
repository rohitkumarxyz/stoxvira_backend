import asyncio

import pytest

from app.services.market.feed import MarketFeed, _decode
from app.services.market.proto import MarketDataFeedV3_pb2 as pb


def ltpc_frame(**prices: tuple[float, float]) -> bytes:
    """Build a real protobuf frame: {key: (ltp, cp)}."""
    message = pb.FeedResponse(type=pb.live_feed)
    for key, (ltp, cp) in prices.items():
        message.feeds[key].ltpc.ltp = ltp
        message.feeds[key].ltpc.cp = cp
    return message.SerializeToString()


def index_full_frame(key: str, ltp: float, cp: float) -> bytes:
    """`full` mode nests LTPC under fullFeed.indexFF instead."""
    message = pb.FeedResponse(type=pb.live_feed)
    message.feeds[key].fullFeed.indexFF.ltpc.ltp = ltp
    message.feeds[key].fullFeed.indexFF.ltpc.cp = cp
    return message.SerializeToString()


def test_decode_reads_ltpc_mode():
    frame = ltpc_frame(**{"NSE_EQ|INE009A01021": (1051.4, 1058.6)})

    assert _decode(frame) == {"NSE_EQ|INE009A01021": {"ltp": 1051.4, "cp": 1058.6}}


def test_decode_reads_index_full_mode():
    """A 'full' subscription buries the price two levels deeper."""
    frame = index_full_frame("NSE_INDEX|Nifty 50", 23346.4, 23270.6)

    assert _decode(frame) == {
        "NSE_INDEX|Nifty 50": {"ltp": 23346.4, "cp": 23270.6}
    }


def test_decode_ignores_market_status_frames():
    """The first frame after connecting carries no prices."""
    message = pb.FeedResponse(type=pb.market_info)
    message.marketInfo.segmentStatus["NSE_EQ"] = pb.NORMAL_CLOSE

    assert _decode(message.SerializeToString()) == {}


def test_decode_survives_a_corrupt_frame():
    """A bad frame must not take the whole feed down."""
    assert _decode(b"\xff\xff\xff not protobuf") == {}


def test_decode_ignores_text_frames():
    assert _decode("some text") == {}


async def test_listener_receives_only_its_own_keys():
    feed = MarketFeed(url="wss://example.invalid", mode="ltpc")
    # Pretend the socket is already up so no connection is attempted.
    feed._task = asyncio.create_task(asyncio.sleep(3600))

    stream = feed.listen(["NSE_EQ|INFY"])
    consumer = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)  # let the listener register

    feed._broadcast(
        {
            "NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6},
            "NSE_EQ|SBIN": {"ltp": 996.2, "cp": 988.7},
        }
    )

    received = await asyncio.wait_for(consumer, timeout=1)
    assert received == {"NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6}}

    await stream.aclose()
    feed._task.cancel()


async def test_one_broadcast_reaches_every_listener():
    feed = MarketFeed(url="wss://example.invalid", mode="ltpc")
    feed._task = asyncio.create_task(asyncio.sleep(3600))

    streams = [feed.listen(["NSE_EQ|INFY"]) for _ in range(3)]
    consumers = [asyncio.create_task(s.__anext__()) for s in streams]
    await asyncio.sleep(0)

    feed._broadcast({"NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6}})

    results = await asyncio.wait_for(asyncio.gather(*consumers), timeout=1)
    assert all(r == {"NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6}} for r in results)

    for stream in streams:
        await stream.aclose()
    feed._task.cancel()


async def test_a_stalled_listener_does_not_block_the_feed():
    """A browser that stops reading must lose updates, not jam everyone else."""
    feed = MarketFeed(url="wss://example.invalid", mode="ltpc")
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    feed._listeners.add(queue)

    for price in (1.0, 2.0, 3.0, 4.0):
        feed._broadcast({"K": {"ltp": price, "cp": 1.0}})

    assert queue.qsize() == 2
    # The oldest were dropped, so what survives is the most recent pair.
    assert queue.get_nowait()["K"]["ltp"] == 3.0
    assert queue.get_nowait()["K"]["ltp"] == 4.0


async def test_snapshot_returns_last_known_prices():
    feed = MarketFeed(url="wss://example.invalid", mode="ltpc")
    feed._latest = {
        "NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6},
        "NSE_EQ|SBIN": {"ltp": 996.2, "cp": 988.7},
    }

    assert feed.snapshot(["NSE_EQ|INFY", "NSE_EQ|UNKNOWN"]) == {
        "NSE_EQ|INFY": {"ltp": 1051.4, "cp": 1058.6}
    }


async def test_stop_is_safe_when_never_started():
    await MarketFeed(url="wss://example.invalid", mode="ltpc").stop()
