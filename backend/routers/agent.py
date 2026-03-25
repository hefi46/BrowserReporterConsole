"""Windows Agent management & public endpoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..agent_manager import (
    load_agent_config,
    save_uploaded_exe,
    get_exe_path,
    build_agent_exe,
)
from .deps import require_admin

logger = logging.getLogger("browser_reporter")

router = APIRouter()
templates: Jinja2Templates | None = None

_BASE_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = _BASE_DIR / "static"
_AGENT_SOURCE_DIR = _BASE_DIR.parent / "windows_agent"
_BUILD_DIR = Path(os.getenv("AGENT_BUILD_DIR", "/app/agent_build"))
_SECURECONFIG_PATH: str = ""


def configure(t: Jinja2Templates, secureconfig_path: str) -> None:
    global templates, _SECURECONFIG_PATH
    templates = t
    _SECURECONFIG_PATH = secureconfig_path


# ── Page route ───────────────────────────────────────────────────────────


@router.get("/windows-agent", response_class=HTMLResponse)
async def windows_agent_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("windows_agent.html", {"request": request})


# ── Admin endpoints ─────────────────────────────────────────────────────


@router.get("/api/admin/agent-config")
async def get_agent_config(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return await load_agent_config(db)


@router.post("/api/admin/agent-upload")
async def upload_agent_exe(
    request: Request,
    version: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a pre-built agent .exe. Admin only."""
    await require_admin(request, db)

    if not file.filename or not file.filename.lower().endswith(".exe"):
        raise HTTPException(status_code=400, detail="File must be a .exe")

    contents = await file.read()
    if len(contents) < 1024:
        raise HTTPException(status_code=400, detail="File too small to be a valid .exe")
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 100MB limit")

    config = await save_uploaded_exe(db, str(_STATIC_DIR), contents, version)
    return {"status": "ok", "message": f"Agent v{version} uploaded", "config": config}


@router.get("/api/admin/agent-download")
async def download_agent_package(request: Request, db: AsyncSession = Depends(get_db)):
    """Download the agent .exe. Admin only (use /api/agent/exe for public)."""
    await require_admin(request, db)
    exe_path = get_exe_path(str(_STATIC_DIR))
    if not exe_path:
        raise HTTPException(status_code=404, detail="No agent uploaded yet")
    return FileResponse(
        str(exe_path),
        media_type="application/octet-stream",
        filename="BrowserReporter.exe",
    )


@router.post("/api/admin/agent-build")
async def build_agent_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Build the Windows agent .exe using Docker. Admin only.

    Uses the cdrx/pyinstaller-windows Docker image to cross-compile
    the Python agent source into a Windows executable.
    First build may take several minutes to pull the image.
    """
    await require_admin(request, db)

    if not _AGENT_SOURCE_DIR.is_dir():
        raise HTTPException(
            status_code=500,
            detail=f"Agent source directory not found: {_AGENT_SOURCE_DIR}",
        )

    try:
        result = await build_agent_exe(
            db=db,
            static_dir=str(_STATIC_DIR),
            agent_source_dir=str(_AGENT_SOURCE_DIR),
            build_dir=str(_BUILD_DIR),
        )
        return {"status": "ok", "message": f"Agent v{result['version']} built successfully", **result}
    except RuntimeError as e:
        logger.exception("Agent build failed")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Agent build unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


# ── Public endpoints (called by bootstrap.ps1 and agent) ───────────────


@router.get("/api/agent/version")
async def agent_version(db: AsyncSession = Depends(get_db)):
    """Return the current agent version. No auth — called by bootstrap script."""
    config = await load_agent_config(db)
    version = config.get("current_version", "")
    if not version:
        raise HTTPException(status_code=404, detail="No agent version available")
    return {"version": version}


@router.get("/api/agent/exe")
async def agent_exe_download(db: AsyncSession = Depends(get_db)):
    """Serve the agent .exe. No auth — called by bootstrap script."""
    exe_path = get_exe_path(str(_STATIC_DIR))
    if not exe_path:
        raise HTTPException(status_code=404, detail="No agent uploaded yet")
    return FileResponse(
        str(exe_path),
        media_type="application/octet-stream",
        filename="BrowserReporter.exe",
    )


@router.get("/api/agent/config")
async def agent_config_download():
    """Serve the secureconfig.json. No auth — called by bootstrap script.

    This is the same file as /secureconfig.json but under the /api/agent/ prefix
    for consistency with the bootstrap script.
    """
    if not _SECURECONFIG_PATH or not os.path.exists(_SECURECONFIG_PATH):
        raise HTTPException(
            status_code=404,
            detail="secureconfig.json not generated yet. Use Client Config page first.",
        )
    return FileResponse(_SECURECONFIG_PATH, media_type="application/json")
