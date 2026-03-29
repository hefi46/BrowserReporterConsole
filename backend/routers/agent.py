"""Windows Agent management & public endpoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..agent_manager import get_agent_version, get_exe_path
from .deps import require_admin

logger = logging.getLogger("browser_guardian")

router = APIRouter()
templates: Jinja2Templates | None = None

_BASE_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = _BASE_DIR / "static"
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


# ── Public endpoints (called by bootstrap.ps1 and agent) ───────────────


@router.get("/api/agent/version")
async def agent_version():
    """Return the current agent version. No auth — called by bootstrap script."""
    version = get_agent_version(str(_STATIC_DIR))
    if not version:
        raise HTTPException(status_code=404, detail="No agent version available")
    return {"version": version}


@router.get("/api/agent/exe")
async def agent_exe_download():
    """Serve the agent .exe. No auth — called by bootstrap script."""
    exe_path = get_exe_path(str(_STATIC_DIR))
    if not exe_path:
        raise HTTPException(status_code=404, detail="Agent .exe not found")
    return FileResponse(
        str(exe_path),
        media_type="application/octet-stream",
        filename="BrowserGuardian.exe",
    )


@router.get("/api/agent/config")
async def agent_config_download():
    """Serve the secureconfig.json. No auth — called by bootstrap script."""
    if not _SECURECONFIG_PATH or not os.path.exists(_SECURECONFIG_PATH):
        raise HTTPException(
            status_code=404,
            detail="secureconfig.json not generated yet. Use Client Config page first.",
        )
    return FileResponse(_SECURECONFIG_PATH, media_type="application/json")
