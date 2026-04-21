from fastapi import APIRouter
from fastapi.responses import FileResponse

from lsmfapi.collectors.icon_ch1_eps import _latest_ref_dt
from lsmfapi.collectors.icon_ch2_eps import _latest_ref_dt_ch2
from lsmfapi.database.cache import (
    altitude_winds_cache_detail,
    grid_cache_detail,
    station_cache_detail,
)
from lsmfapi.database.collection_state import get_all_states
from lsmfapi.database.telemetry import get_telemetry

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page() -> FileResponse:
    return FileResponse("static/dashboard.html")


@router.get("/api/dashboard")
async def dashboard_stats() -> dict:
    """Return live operational stats: cache state, collection runs, request metrics."""
    station = station_cache_detail()
    altitude_winds = altitude_winds_cache_detail()
    grid = grid_cache_detail()
    collection = get_all_states()
    telemetry = get_telemetry()

    expected_ch1 = _latest_ref_dt().isoformat()
    expected_ch2 = _latest_ref_dt_ch2().isoformat()

    cached_init = station.get("init_time")
    cached_model = station.get("model", "")

    def _is_current(cached_init_iso: str | None, expected_iso: str) -> bool:
        if not cached_init_iso:
            return False
        # Compare just the date+hour — both are UTC ISO strings
        return cached_init_iso[:16] == expected_iso[:16]

    return {
        "station_cache": station,
        "altitude_winds_cache": altitude_winds,
        "grid_cache": grid,
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
        },
        "recent_errors": telemetry["recent_errors"],
    }
