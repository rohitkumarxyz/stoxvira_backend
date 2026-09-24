import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.ai import (
    MAX_PREDICTION_DAYS,
    MIN_PREDICTION_DAYS,
    AiAnalysisError,
    analyze_stock,
    predict_stock,
    stream_stock_analysis,
)
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


class PredictionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    days: int = Field(default=1, ge=MIN_PREDICTION_DAYS, le=MAX_PREDICTION_DAYS)


class PredictionDay(BaseModel):
    day: int
    date: str
    predicted_price: float
    change_percent: float
    confidence: str
    note: str = ""


class PredictionResponse(BaseModel):
    symbol: str
    name: str
    last_price: float
    trend: str
    summary: str
    days: list[PredictionDay]
    disclaimer: str


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


@router.post("/predict", response_model=PredictionResponse)
async def predict(
    body: PredictionRequest,
    settings: Settings = Depends(get_settings),
    market: MarketService = Depends(get_market_service),
) -> PredictionResponse:
    try:
        result = await predict_stock(
            settings=settings,
            market=market,
            symbol=body.symbol.strip().upper(),
            name=body.name.strip(),
            days=body.days,
        )
    except AiAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return PredictionResponse(**result)


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
