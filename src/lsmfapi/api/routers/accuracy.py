import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from lsmfapi.config import get_config

router = APIRouter(tags=["accuracy"])


@router.get("/accuracy", include_in_schema=False)
async def accuracy_page() -> FileResponse:
    return FileResponse("static/index.html")


@router.get("/data", include_in_schema=False)
async def data_inspector_page() -> FileResponse:
    return FileResponse("static/data.html")


@router.get("/api/meta")
async def meta() -> dict:
    cfg = get_config()
    return {"lenticularis_base_url": cfg.lenticularis.base_url}


@router.get("/api/stations")
async def stations_proxy() -> list:
    """Proxy Lenticularis /api/stations so the browser avoids CORS."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        try:
            resp = await client.get(f"{cfg.lenticularis.base_url}/api/stations")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
