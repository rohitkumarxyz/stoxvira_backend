import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.ai import AiAnalysisError, analyze_stock, stream_stock_analysis
from app.services.market.service import MarketService, get_market_service

router = APIRouter(prefix="/ai", tags=["ai"])


class AnalysisRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    intent: str = Field(pattern="^(Buy|Sell)$")
    holding_period: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=4000)


class AnalysisResponse(BaseModel):
    answer: str


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(
    body: AnalysisRequest,
    settings: Settings = Depends(get_settings),
    market: MarketService = Depends(get_market_service),
) -> AnalysisResponse:
    try:
        answer = await analyze_stock(
            settings=settings,
            market=market,
            symbol=body.symbol.strip().upper(),
            name=body.name.strip(),
            intent=body.intent,
            holding_period=body.holding_period.strip(),
            question=body.question.strip(),
        )
    except AiAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return AnalysisResponse(answer=answer)


@router.post("/analyze/stream")
async def analyze_stream(
    body: AnalysisRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    market: MarketService = Depends(get_market_service),
) -> StreamingResponse:
    async def events():
        try:
            async for token in stream_stock_analysis(
                settings=settings,
                market=market,
                symbol=body.symbol.strip().upper(),
                name=body.name.strip(),
                intent=body.intent,
                holding_period=body.holding_period.strip(),
                question=body.question.strip(),
            ):
                if await request.is_disconnected():
                    return
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"
        except AiAnalysisError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
