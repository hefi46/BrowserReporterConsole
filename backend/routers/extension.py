"""Chrome Extension builder & management endpoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..extension_builder import (
    build_extension,
    load_extension_config,
    save_extension_config,
    compute_extension_id,
    get_or_create_pem_key,
)
from ..schemas import ExtensionConfigIn
from .deps import require_admin

logger = logging.getLogger("browser_reporter")

router = APIRouter()
templates: Jinja2Templates | None = None

# Resolved paths
_BASE_DIR = Path(__file__).resolve().parent.parent
_TEMPLATE_DIR = _BASE_DIR.parent / "extension"
_STATIC_DIR = _BASE_DIR / "static"
_DATA_DIR = Path(os.getenv("EXTENSION_DATA_DIR", "/app/data"))


def configure(t: Jinja2Templates) -> None:
    global templates
    templates = t


# ── Page route ───────────────────────────────────────────────────────────


@router.get("/chrome-extension", response_class=HTMLResponse)
async def extension_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("extension.html", {"request": request})


# ── Config endpoints ─────────────────────────────────────────────────────


@router.get("/api/admin/extension-config")
async def get_extension_config(request: Request, db: AsyncSession = Depends(get_db)):
    require_admin(request)
    config = await load_extension_config(db)
    return config


@router.post("/api/admin/extension-config")
async def update_extension_config(
    request: Request,
    body: ExtensionConfigIn,
    db: AsyncSession = Depends(get_db),
):
    require_admin(request)
    config = await load_extension_config(db)

    # Merge form fields into existing config (preserving version, extension_id, etc.)
    config["school_name"] = body.school_name
    config["accent_color"] = body.accent_color
    config["notice_text"] = body.notice_text
    config["contact_info"] = body.contact_info
    config["server_url"] = body.server_url
    config["tracking_start_time"] = body.tracking_start_time
    config["tracking_end_time"] = body.tracking_end_time
    config["tracking_all_day"] = body.tracking_all_day
    config["monitored_emails"] = [e.strip() for e in body.monitored_emails if e.strip()]

    await save_extension_config(db, config)
    return {"status": "ok", "message": "Extension config saved"}


# ── Logo upload ──────────────────────────────────────────────────────────


@router.post("/api/admin/extension-logo")
async def upload_extension_logo(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    require_admin(request)

    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image (PNG, JPG, etc.)")

    # Validate size (5MB max)
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 5MB")

    # Validate it's actually an image
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(contents))
        img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")

    # Save to data directory
    os.makedirs(str(_DATA_DIR), exist_ok=True)
    logo_filename = "logo_original.png"
    logo_path = _DATA_DIR / logo_filename

    # Re-open and convert to PNG for consistency
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(contents)).convert("RGBA")
    img.save(str(logo_path), "PNG")

    # Update config with logo filename
    config = await load_extension_config(db)
    config["logo_filename"] = logo_filename
    await save_extension_config(db, config)

    return {"status": "ok", "message": "Logo uploaded", "filename": logo_filename}


@router.delete("/api/admin/extension-logo")
async def remove_extension_logo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_admin(request)

    config = await load_extension_config(db)
    logo_filename = config.get("logo_filename", "")

    if logo_filename:
        logo_path = _DATA_DIR / logo_filename
        if logo_path.exists():
            os.remove(str(logo_path))

    config["logo_filename"] = ""
    await save_extension_config(db, config)
    return {"status": "ok", "message": "Logo removed"}


# ── Build endpoint ───────────────────────────────────────────────────────


@router.post("/api/admin/extension-build")
async def build_extension_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_admin(request)

    # Determine server URL from request if not configured
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8000"))
    fallback_url = f"{scheme}://{host}"

    try:
        os.makedirs(str(_DATA_DIR), exist_ok=True)
        os.makedirs(str(_STATIC_DIR / "extension"), exist_ok=True)

        result = await build_extension(
            db=db,
            data_dir=str(_DATA_DIR),
            static_dir=str(_STATIC_DIR),
            template_dir=str(_TEMPLATE_DIR),
            server_url=fallback_url,
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("Extension build failed")
        raise HTTPException(status_code=500, detail=f"Build failed: {str(e)}")


# ── Download endpoint ────────────────────────────────────────────────────


@router.get("/api/admin/extension-download")
async def download_extension(request: Request):
    require_admin(request)
    crx_path = _STATIC_DIR / "extension" / "extension.crx"
    if not crx_path.exists():
        raise HTTPException(status_code=404, detail="Extension not built yet. Click 'Build Extension' first.")
    return FileResponse(
        str(crx_path),
        media_type="application/x-chrome-extension",
        filename="BrowserReporter.crx",
    )
