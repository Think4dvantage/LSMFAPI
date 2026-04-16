from fastapi import APIRouter
from fastapi.responses import FileResponse

from lsmfapi.config import get_config

router = APIRouter(tags=["accuracy"])


@router.get("/accuracy", include_in_schema=False)
async def accuracy_page() -> FileResponse:
    return FileResponse("static/index.html")


@router.get("/api/meta")
async def meta() -> dict:
    cfg = get_config()
    return {"lenticularis_base_url": cfg.lenticularis.base_url}
