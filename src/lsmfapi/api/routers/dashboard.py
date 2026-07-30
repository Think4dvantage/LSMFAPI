import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from lsmfapi.collectors.icon_ch1_eps import _latest_ref_dt
from lsmfapi.collectors.icon_ch2_eps import _latest_ref_dt_ch2
from lsmfapi.config import get_config
from lsmfapi.database.cache import (
    altitude_winds_cache_detail,
    grid_cache_detail,
    station_cache_detail,
    thermal_grid_cache_detail,
)
from lsmfapi.database.collection_state import get_all_states
from lsmfapi.database.telemetry import get_telemetry

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page() -> FileResponse:
    return FileResponse("static/dashboard.html")


@router.get("/data", include_in_schema=False)
async def data_inspector_page() -> FileResponse:
    return FileResponse("static/data.html")


@router.get("/api/stations")
async def stations_proxy() -> list:
    """Proxy Lenticularis /api/stations so the browser avoids CORS."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{cfg.lenticularis.base_url}/api/stations")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/dashboard")
async def dashboard_stats() -> dict:
    """Return live operational stats: cache state, collection runs, request metrics."""
    station = station_cache_detail()
    altitude_winds = altitude_winds_cache_detail()
    grid = grid_cache_detail()
    thermal_grid = thermal_grid_cache_detail()
    collection = get_all_states()
    telemetry = get_telemetry()

    expected_ch1 = _latest_ref_dt().isoformat()
    expected_ch2 = _latest_ref_dt_ch2().isoformat()

    def _is_current(cached_init_iso: str | None, expected_iso: str) -> bool:
        if not cached_init_iso:
            return False
        # Compare just the date+hour — both are UTC ISO strings
        return cached_init_iso[:16] == expected_iso[:16]

    return {
        "station_cache": station,
        "altitude_winds_cache": altitude_winds,
        "grid_cache": grid,
        "thermal_grid_cache": thermal_grid,
        "collection": {
            "ch1": {
                **collection["ch1"],
                "expected_ref_dt": expected_ch1,
                "is_current": _is_current(
                    collection["ch1"].get("ref_dt"), expected_ch1
                ),
            },
            "ch2": {
                **collection["ch2"],
                "expected_ref_dt": expected_ch2,
                "is_current": _is_current(
                    collection["ch2"].get("ref_dt"), expected_ch2
                ),
            },
        },
        "requests": {
            "started_at": telemetry["started_at"],
            "total": telemetry["request_count"],
            "error_count": telemetry["error_count"],
            "client_error_count": telemetry["client_error_count"],
        },
        "recent_errors": telemetry["recent_errors"],
    }
