from fastapi import APIRouter, HTTPException, Query

from lsmfapi.database.cache import get_grid_forecast, get_station_altitude_winds, get_station_forecast
from lsmfapi.models.forecast import AltitudeWindsResponse, ForecastResponse, GridResponse

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/station", response_model=ForecastResponse)
async def station_forecast(
    station_id: str = Query(...),
    hours: int = Query(120, ge=1, le=120),
) -> ForecastResponse:
    data = get_station_forecast(station_id)
    if data is None:
        raise HTTPException(status_code=503, detail="Forecast not yet available — cache warming in progress")
    if hours < 120:
        data = data.model_copy(update={"hours": data.hours[:hours]})
    return data


@router.get("/altitude-winds", response_model=AltitudeWindsResponse)
async def altitude_winds(
    station_id: str = Query(...),
    hours: int = Query(120, ge=1, le=120),
) -> AltitudeWindsResponse:
    data = get_station_altitude_winds(station_id)
    if data is None:
        raise HTTPException(status_code=503, detail="Altitude wind forecast not yet available — cache warming in progress")
    if hours < 120:
        data = data.model_copy(update={"hours": data.hours[:hours]})
    return data


@router.get("/wind-grid", response_model=GridResponse)
async def wind_grid(
    date: str = Query(..., description="YYYY-MM-DD"),
    level_m: int = Query(...),
) -> GridResponse:
    data = get_grid_forecast(date, level_m)
    if data is None:
        raise HTTPException(status_code=503, detail="Grid forecast not yet available — cache warming in progress")
    return data
