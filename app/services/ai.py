from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from collections.abc import AsyncIterator
import json
from xml.etree import ElementTree

import httpx

from app.core.config import Settings
from app.services.market.service import MarketService


class AiAnalysisError(RuntimeError):
    """Raised when the AI analysis cannot be generated."""


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
