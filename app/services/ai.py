from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from collections.abc import AsyncIterator
import json
import re
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.services.market.service import MarketService

MIN_PREDICTION_DAYS = 1
MAX_PREDICTION_DAYS = 7


class AiAnalysisError(RuntimeError):
    """Raised when the AI analysis cannot be generated."""


class _PredictedDay(BaseModel):
    """One day of the model's forecast, before it is turned into a price."""

    day: int = Field(ge=1, le=MAX_PREDICTION_DAYS)
    # Expected move versus the previous trading day, not versus today — the
    # backend compounds these itself so the model never has to do multi-step
    # arithmetic, which is where LLM-generated numbers tend to drift.
    change_percent: float = Field(ge=-20, le=20)
    confidence: str = "medium"
    note: str = ""


class _PredictionPayload(BaseModel):
    trend: str = "neutral"
    summary: str
    days: list[_PredictedDay]


async def analyze_stock(
    *,
    settings: Settings,
    market: MarketService,
    symbol: str,
    name: str,
    intent: str,
    holding_period: str,
    question: str,
) -> str:
    if not settings.groq_api_key.get_secret_value():
        raise AiAnalysisError("GROQ_API_KEY is not configured.")

    prompt = await _build_prompt(
        market=market,
        symbol=symbol,
        name=name,
        intent=intent,
        holding_period=holding_period,
        question=question,
    )

    headers = {
        "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.groq_model,
        "temperature": 1,
        "max_completion_tokens": 2048,
        "top_p": 1,
        "reasoning_effort": "medium",
        "messages": [
            {
                "role": "system",
                "content": "Answer in plain text with short paragraphs and bullets.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise AiAnalysisError("Could not reach the AI provider.") from exc

    if response.status_code != httpx.codes.OK:
        raise AiAnalysisError(
            f"AI provider returned {response.status_code}: {_error_detail(response)}"
        )
    body = response.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise AiAnalysisError("AI provider returned an empty response.")
    return content.strip()


async def predict_stock(
    *,
    settings: Settings,
    market: MarketService,
    symbol: str,
    name: str,
    days: int,
) -> dict:
    """A short-horizon, day-by-day forecast built from the same context the
    Buy/Sell analysis uses: live price, recent candles, and headlines.

    This is a model's estimate, not a guarantee — the response always carries
    a disclaimer and the frontend is expected to show it.
    """
    if not settings.groq_api_key.get_secret_value():
        raise AiAnalysisError("GROQ_API_KEY is not configured.")

    days = max(MIN_PREDICTION_DAYS, min(MAX_PREDICTION_DAYS, days))

    quote_response = await market.get_quotes([symbol])
    quote = quote_response.quotes[0] if quote_response.quotes else None
    if quote is None:
        raise AiAnalysisError(f"No current quote is available for {symbol}.")

    today = date.today()
    candles: list[list] = []
    try:
        _, candles = await market.get_historical_candles(
            symbol,
            "days",
            "1",
            today.isoformat(),
            (today - timedelta(days=45)).isoformat(),
        )
    except Exception:
        candles = []

    headlines = await _fetch_headlines(name or symbol)
    context = _build_context(quote, candles, headlines)
    prompt = f"""You are Stoxvira AI, forecasting short-term price movement for an Indian equity.

Stock: {name} ({symbol})
Forecast horizon: {days} trading day{"s" if days != 1 else ""}, starting the next trading session.

Market context:
{context}

Estimate day-by-day movement for each of the {days} trading day(s). For each day give the
expected percentage change versus the PREVIOUS trading day (not versus today), a confidence
level, and a short one-sentence reason. Confidence should generally fall as the horizon
extends further out.

Respond with ONLY a JSON object, no prose before or after it, no markdown fences, matching
exactly this shape:
{{
  "trend": "bullish" | "bearish" | "neutral",
  "summary": "one or two sentences on the overall call and the main risk",
  "days": [
    {{"day": 1, "change_percent": -1.2, "confidence": "low" | "medium" | "high", "note": "short reason"}}
  ]
}}
The "days" array must have exactly {days} entries, "day" numbered 1 to {days} in order.
Keep each change_percent realistic for a single Indian trading session (typically well under 5%).
Do not invent news or numbers, and reflect genuine uncertainty rather than false precision."""

    headers = {
        "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.groq_model,
        "temperature": 0.4,
        "max_completion_tokens": 1536,
        "top_p": 1,
        "reasoning_effort": "medium",
        "messages": [
            {
                "role": "system",
                "content": "Respond with strict JSON only. No markdown, no commentary outside the JSON object.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise AiAnalysisError("Could not reach the AI provider.") from exc

    if response.status_code != httpx.codes.OK:
        raise AiAnalysisError(
            f"AI provider returned {response.status_code}: {_error_detail(response)}"
        )
    body = response.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise AiAnalysisError("AI provider returned an empty response.")

    payload_model = _parse_prediction(content)
    return _build_prediction_response(
        symbol=symbol,
        name=name,
        quote=quote,
        days=days,
        payload=payload_model,
    )


def _parse_prediction(content: str) -> _PredictionPayload:
    text = content.strip()
    # Models sometimes wrap JSON in a code fence despite being told not to.
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            text = text[brace_start : brace_end + 1]

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiAnalysisError("AI provider returned an unparseable forecast.") from exc

    try:
        return _PredictionPayload.model_validate(raw)
    except ValidationError as exc:
        raise AiAnalysisError("AI provider returned a malformed forecast.") from exc


def _build_prediction_response(
    *,
    symbol: str,
    name: str,
    quote,
    days: int,
    payload: _PredictionPayload,
) -> dict:
    ordered = sorted(payload.days, key=lambda item: item.day)[:days]
    if not ordered:
        raise AiAnalysisError("AI provider returned no forecast days.")

    price = quote.last_price
    trading_date = date.today()
    forecast_days: list[dict] = []
    for index, item in enumerate(ordered, start=1):
        trading_date = _next_trading_day(trading_date)
        price = price * (1 + item.change_percent / 100)
        forecast_days.append(
            {
                "day": index,
                "date": trading_date.isoformat(),
                "predicted_price": round(price, 2),
                "change_percent": round(item.change_percent, 2),
                "confidence": item.confidence.lower()
                if item.confidence.lower() in {"low", "medium", "high"}
                else "medium",
                "note": item.note.strip(),
            }
        )

    trend = payload.trend.lower()
    if trend not in {"bullish", "bearish", "neutral"}:
        trend = "neutral"

    return {
        "symbol": symbol,
        "name": name,
        "last_price": round(quote.last_price, 2),
        "trend": trend,
        "summary": payload.summary.strip(),
        "days": forecast_days,
        "disclaimer": (
            "AI-generated estimate based on recent price action and headlines. "
            "Not financial advice — markets can move against any forecast."
        ),
    }


def _next_trading_day(from_date: date) -> date:
    """Next weekday. Approximate — Indian market holidays are not modelled."""
    next_day = from_date + timedelta(days=1)
    while next_day.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        next_day += timedelta(days=1)
    return next_day


async def stream_stock_analysis(
    *,
    settings: Settings,
    market: MarketService,
    symbol: str,
    name: str,
    intent: str,
    holding_period: str,
    question: str,
) -> AsyncIterator[str]:
    if not settings.groq_api_key.get_secret_value():
        raise AiAnalysisError("GROQ_API_KEY is not configured.")

    prompt = await _build_prompt(
        market=market,
        symbol=symbol,
        name=name,
        intent=intent,
        holding_period=holding_period,
        question=question,
    )
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.groq_model,
        "temperature": 1,
        "max_completion_tokens": 2048,
        "top_p": 1,
        "reasoning_effort": "medium",
        "stream": True,
        "messages": [
            {
                "role": "system",
                "content": "Answer in plain text with short paragraphs and bullets.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise AiAnalysisError(
                        f"AI provider returned {response.status_code}: {_error_detail(response)}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        return
                    try:
                        content = json.loads(data).get("choices", [{}])[0].get(
                            "delta", {}
                        ).get("content")
                    except json.JSONDecodeError:
                        continue
                    if isinstance(content, str) and content:
                        yield content
    except httpx.HTTPError as exc:
        raise AiAnalysisError("Could not reach the AI provider.") from exc


async def _build_prompt(
    *,
    market: MarketService,
    symbol: str,
    name: str,
    intent: str,
    holding_period: str,
    question: str,
) -> str:
    quote_response = await market.get_quotes([symbol])
    quote = quote_response.quotes[0] if quote_response.quotes else None
    if quote is None:
        raise AiAnalysisError(f"No current quote is available for {symbol}.")

    today = date.today()
    candles: list[list] = []
    try:
        _, candles = await market.get_historical_candles(
            symbol,
            "days",
            "1",
            today.isoformat(),
            (today - timedelta(days=45)).isoformat(),
        )
    except Exception:
        candles = []

    headlines = await _fetch_headlines(name or symbol)
    context = _build_context(quote, candles, headlines)
    return f"""You are Stoxvira AI, a careful Indian equity market research assistant.

User context:
- Intended action: {intent}
- Stock: {name} ({symbol})
- Holding period: {holding_period}
- Question: {question}

Market context:
{context}

Give a concise, useful analysis. Start with one clear line: "View: BUY", "View: SELL", or "View: WAIT".
Then explain the main reasons using the supplied price, change, trend, and headlines.
Mention uncertainty and risks. Do not claim certainty, do not invent news or numbers, and say when data is unavailable.
This is research information, not guaranteed financial advice."""


def _build_context(quote, candles: list[list], headlines: list[str]) -> str:
    lines = [
        f"Current price: ₹{quote.last_price:.2f}",
        f"Day change: {quote.change_percent:+.2f}%" if quote.change_percent is not None else "Day change: unavailable",
    ]
    if len(candles) >= 2:
        latest = candles[0]
        oldest = candles[-1]
        change = ((latest[4] - oldest[4]) / oldest[4]) * 100 if oldest[4] else 0
        lines.append(f"45-day candle trend: {change:+.2f}% based on available candles")
    else:
        lines.append("Historical trend: unavailable")
    lines.append("Recent headlines:")
    lines.extend(f"- {headline}" for headline in headlines or ["No recent headlines found."])
    return "\n".join(lines)


async def _fetch_headlines(query: str) -> list[str]:
    url = "https://news.google.com/rss/search"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                url,
                params={"q": f"{query} stock", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
            )
        if response.status_code != httpx.codes.OK:
            return []
        root = ElementTree.fromstring(response.text)
    except (httpx.HTTPError, ElementTree.ParseError):
        return []

    headlines: list[str] = []
    for item in root.findall("./channel/item")[:5]:
        title = item.findtext("title")
        published = item.findtext("pubDate")
        if not title:
            continue
        if published:
            try:
                timestamp = parsedate_to_datetime(published).date().isoformat()
                title = f"{title} ({timestamp})"
            except (TypeError, ValueError, OverflowError):
                pass
        headlines.append(title)
    return headlines


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    return str(body.get("error", body)) if isinstance(body, dict) else str(body)
