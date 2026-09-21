from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.upstox.service import calculate_expiry

IST = ZoneInfo("Asia/Kolkata")


def ist(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    "issued, expected",
    [
        # Evening token -> dies 3:30 AM the next day.
        (ist(2026, 9, 15, 20, 0), ist(2026, 9, 16, 3, 30)),
        # Early-morning token, before 3:30 -> dies 3:30 the same day.
        (ist(2026, 9, 16, 2, 30), ist(2026, 9, 16, 3, 30)),
        # Exactly 3:30 counts as already past -> next day.
        (ist(2026, 9, 16, 3, 30), ist(2026, 9, 17, 3, 30)),
        # One minute before the cutoff -> same day.
        (ist(2026, 9, 16, 3, 29), ist(2026, 9, 16, 3, 30)),
        # Month rollover.
        (ist(2026, 9, 30, 23, 59), ist(2026, 10, 1, 3, 30)),
    ],
)
def test_expiry_lands_on_next_0330_ist(issued, expected):
    assert calculate_expiry(issued) == expected


def test_expiry_is_returned_in_utc():
    expiry = calculate_expiry(ist(2026, 9, 15, 20, 0))
    assert expiry.tzinfo == timezone.utc


def test_utc_input_is_converted_before_comparing():
    """22:00 UTC is 03:30 IST the next day — the IST clock is what matters."""
    issued = datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc)  # 03:30 IST on the 16th
    assert calculate_expiry(issued) == ist(2026, 9, 17, 3, 30)
