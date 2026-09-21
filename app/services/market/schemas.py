from pydantic import BaseModel, ConfigDict


class Instrument(BaseModel):
    """One tradable thing, as Upstox describes it."""

    model_config = ConfigDict(extra="ignore")

    instrument_key: str
    trading_symbol: str
    name: str
    segment: str
    instrument_type: str


class InstrumentSearchResult(BaseModel):
    instrument_key: str
    trading_symbol: str
    name: str


class Quote(BaseModel):
    """A price, normalised for the frontend.

    Numbers stay numbers — the UI decides how to render the rupee sign and
    the commas.
    """

    symbol: str
    name: str
    instrument_key: str
    last_price: float
    prev_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    up: bool = True


class QuotesResponse(BaseModel):
    quotes: list[Quote]
    unresolved: list[str] = []
    """Symbols we could not map to an Upstox instrument key."""
