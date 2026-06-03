"""Serve the admin MongoDB browser UI."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/admin", tags=["Admin UI"], include_in_schema=False)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "admin"
_IMAGES_DIR = _STATIC_DIR / "images"
_IMAGE_MEDIA = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@router.get("")
@router.get("/")
async def admin_ui_index():
    return FileResponse(_STATIC_DIR / "index.html")


@router.get("/app.js")
async def admin_ui_js():
    return FileResponse(_STATIC_DIR / "app.js", media_type="application/javascript")


@router.get("/styles.css")
async def admin_ui_css():
    return FileResponse(_STATIC_DIR / "styles.css", media_type="text/css")


@router.get("/images/{asset_name}")
async def admin_ui_image(asset_name: str):
    """Serve BC Gov mark, favicon, and other admin UI images."""
    if "/" in asset_name or ".." in asset_name:
        raise HTTPException(status_code=404, detail="Not found")
    path = _IMAGES_DIR / asset_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = _IMAGE_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)
