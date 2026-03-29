"""Extension builder endpoints: customize, build, and host the Chrome extension."""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AppSetting
from ..crx_builder import build_extension, generate_rsa_key, compute_extension_id
from .deps import require_admin

from Crypto.PublicKey import RSA

logger = logging.getLogger("browser_reporter")
router = APIRouter()

templates: Jinja2Templates = None  # type: ignore[assignment]

STATIC_EXT_DIR: str = ""

CONFIG_KEY = "extension_config"
SIGNING_KEY_KEY = "extension_signing_key"
BUILD_STATUS_KEY = "extension_build_status"

DEFAULT_CONFIG = {
    "school_name": "Network Monitoring",
    "school_subtitle": "School managed device",
    "it_contact": "For help, contact your school IT department.",
    "notice_text": "Internet activity is monitored on this school-owned device. "
                   "All websites visited are logged in accordance with school policy.",
    "header_color": "#0d3a6e",
    "server_url": "http://browserreporter:8000",
    "reporting_start_time": "00:00",
    "reporting_end_time": "23:59",
    "reporting_days": [0, 1, 2, 3, 4, 5, 6],
    "version": "1.0.0",
}


def configure(tpl: Jinja2Templates, static_ext_dir: str) -> None:
    global templates, STATIC_EXT_DIR
    templates = tpl
    STATIC_EXT_DIR = static_ext_dir


# ── Helper: read/write AppSetting ────────────────────────────────────────

async def _get_setting(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        db.add(AppSetting(key=key, value=value))


async def _get_config(db: AsyncSession) -> dict:
    raw = await _get_setting(db, CONFIG_KEY)
    if raw:
        saved = json.loads(raw)
        config = {**DEFAULT_CONFIG, **saved}
        return config
    return dict(DEFAULT_CONFIG)


async def _get_or_create_signing_key(db: AsyncSession) -> str:
    pem = await _get_setting(db, SIGNING_KEY_KEY)
    if pem:
        return pem
    pem = generate_rsa_key()
    await _set_setting(db, SIGNING_KEY_KEY, pem)
    return pem


# ── Admin API endpoints ──────────────────────────────────────────────────

@router.get("/chromebook-extension", response_class=HTMLResponse)
async def extension_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("extension.html", {"request": request})


@router.get("/api/admin/extension/config")
async def get_extension_config(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current extension configuration."""
    await require_admin(request, db)
    config = await _get_config(db)
    # Don't send icon_base64 in config response (too large), send a flag instead
    has_icon = bool(config.get("icon_base64"))
    config.pop("icon_base64", None)
    config["has_custom_icon"] = has_icon
    return config


@router.post("/api/admin/extension/config")
async def save_extension_config(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Save extension configuration."""
    await require_admin(request, db)

    # Validate required fields
    allowed_fields = {
        "school_name", "school_subtitle", "it_contact", "notice_text",
        "header_color", "server_url", "reporting_start_time",
        "reporting_end_time", "reporting_days", "version",
    }

    current = await _get_config(db)

    for field in allowed_fields:
        if field in body:
            current[field] = body[field]

    # Validate time format
    for time_field in ("reporting_start_time", "reporting_end_time"):
        val = current.get(time_field, "")
        if val and ":" in val:
            parts = val.split(":")
            if len(parts) == 2:
                try:
                    h, m = int(parts[0]), int(parts[1])
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError
                except ValueError:
                    raise HTTPException(400, f"Invalid time format for {time_field}")

    # Validate days
    days = current.get("reporting_days", [])
    if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        raise HTTPException(400, "reporting_days must be a list of integers 0-6")

    # Validate color
    color = current.get("header_color", "")
    if color and not (color.startswith("#") and len(color) in (4, 7)):
        raise HTTPException(400, "header_color must be a valid hex color")

    await _set_setting(db, CONFIG_KEY, json.dumps(current))
    await db.commit()

    logger.info("Extension config updated: school=%s, server=%s",
                current.get("school_name"), current.get("server_url"))
    return {"success": True, "config": current}


@router.post("/api/admin/extension/icon")
async def upload_extension_icon(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom extension icon (PNG, minimum 128x128)."""
    await require_admin(request, db)

    if not file.content_type or not file.content_type.startswith("image/png"):
        raise HTTPException(400, "Icon must be a PNG image")

    icon_data = await file.read()
    if len(icon_data) > 2 * 1024 * 1024:  # 2MB limit
        raise HTTPException(400, "Icon file too large (max 2MB)")

    # Validate it's a valid PNG and at least 128x128
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(icon_data))
        if img.size[0] < 128 or img.size[1] < 128:
            raise HTTPException(400, "Icon must be at least 128x128 pixels")
    except ImportError:
        pass  # Skip validation if Pillow not available
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid PNG image")

    # Store as base64 in config
    config = await _get_config(db)
    config["icon_base64"] = base64.b64encode(icon_data).decode("utf-8")
    await _set_setting(db, CONFIG_KEY, json.dumps(config))
    await db.commit()

    return {"success": True, "message": "Icon uploaded successfully"}


@router.delete("/api/admin/extension/icon")
async def delete_extension_icon(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove custom icon, reverting to default."""
    await require_admin(request, db)
    config = await _get_config(db)
    config.pop("icon_base64", None)
    await _set_setting(db, CONFIG_KEY, json.dumps(config))
    await db.commit()
    return {"success": True, "message": "Custom icon removed"}


@router.post("/api/admin/extension/build")
async def build_extension_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Build the .crx extension and update.xml from current config."""
    await require_admin(request, db)

    config = await _get_config(db)
    pem_key = await _get_or_create_signing_key(db)

    # Get custom icon if uploaded
    icon_data = None
    icon_b64 = config.get("icon_base64")
    if icon_b64:
        icon_data = base64.b64decode(icon_b64)

    # Auto-increment version
    version = config.get("version", "1.0.0")
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    new_version = ".".join(parts)
    config["version"] = new_version

    try:
        crx_bytes, ext_id, update_xml = build_extension(config, pem_key, icon_data)
    except Exception as e:
        logger.exception("Extension build failed")
        raise HTTPException(500, f"Build failed: {e}")

    # Ensure output directory exists
    os.makedirs(STATIC_EXT_DIR, exist_ok=True)

    # Write files
    crx_path = os.path.join(STATIC_EXT_DIR, "extension.crx")
    xml_path = os.path.join(STATIC_EXT_DIR, "update.xml")

    with open(crx_path, "wb") as f:
        f.write(crx_bytes)
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(update_xml)

    # Save updated config (with new version) and build status
    await _set_setting(db, CONFIG_KEY, json.dumps(config))

    build_status = {
        "last_build_at": datetime.now(timezone.utc).isoformat(),
        "version": new_version,
        "extension_id": ext_id,
        "crx_size_bytes": len(crx_bytes),
    }
    await _set_setting(db, BUILD_STATUS_KEY, json.dumps(build_status))
    await db.commit()

    logger.info("Extension built: id=%s, version=%s, size=%d bytes",
                ext_id, new_version, len(crx_bytes))

    return {
        "success": True,
        "extension_id": ext_id,
        "version": new_version,
        "crx_size_bytes": len(crx_bytes),
    }


@router.post("/api/admin/extension/reset")
async def reset_extension(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reset version to 1.0.0 and generate a new signing key (new extension ID)."""
    await require_admin(request, db)

    # Generate new signing key → new extension ID
    new_pem = generate_rsa_key()
    await _set_setting(db, SIGNING_KEY_KEY, new_pem)

    # Reset version
    config = await _get_config(db)
    config["version"] = "1.0.0"
    await _set_setting(db, CONFIG_KEY, json.dumps(config))

    # Clear old build status
    await _set_setting(db, BUILD_STATUS_KEY, "")

    # Remove old .crx and update.xml
    for fname in ("extension.crx", "update.xml"):
        fpath = os.path.join(STATIC_EXT_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)

    await db.commit()

    # Compute new extension ID for response
    key = RSA.import_key(new_pem)
    new_ext_id = compute_extension_id(key.publickey().export_key("DER"))

    logger.info("Extension reset: new signing key generated, new id=%s", new_ext_id)
    return {
        "success": True,
        "new_extension_id": new_ext_id,
        "version": "1.0.0",
    }


@router.get("/api/admin/extension/status")
async def get_extension_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current build status and extension ID."""
    await require_admin(request, db)

    status_raw = await _get_setting(db, BUILD_STATUS_KEY)
    if not status_raw:
        # Check if files exist on disk
        crx_exists = os.path.exists(os.path.join(STATIC_EXT_DIR, "extension.crx"))
        return {
            "built": False,
            "crx_exists": crx_exists,
        }

    status = json.loads(status_raw)
    crx_exists = os.path.exists(os.path.join(STATIC_EXT_DIR, "extension.crx"))
    xml_exists = os.path.exists(os.path.join(STATIC_EXT_DIR, "update.xml"))

    # Also compute current extension ID from signing key
    pem_key = await _get_setting(db, SIGNING_KEY_KEY)
    ext_id = None
    if pem_key:
        key = RSA.import_key(pem_key)
        ext_id = compute_extension_id(key.publickey().export_key("DER"))

    return {
        "built": True,
        "crx_exists": crx_exists,
        "xml_exists": xml_exists,
        "extension_id": ext_id or status.get("extension_id"),
        **status,
    }


# ── Public endpoints (no auth — Chromebooks need these) ──────────────────

@router.get("/chromebook-extension/update.xml")
async def serve_update_xml():
    """Serve update.xml for Chrome extension auto-updates."""
    xml_path = os.path.join(STATIC_EXT_DIR, "update.xml")
    if not os.path.exists(xml_path):
        raise HTTPException(404, "Extension not built yet. Build it from the admin panel.")
    return FileResponse(xml_path, media_type="application/xml")


@router.get("/chromebook-extension/extension.crx")
async def serve_extension_crx():
    """Serve the packaged .crx extension file."""
    crx_path = os.path.join(STATIC_EXT_DIR, "extension.crx")
    if not os.path.exists(crx_path):
        raise HTTPException(404, "Extension not built yet. Build it from the admin panel.")
    return FileResponse(
        crx_path,
        media_type="application/x-chrome-extension",
        filename="extension.crx",
    )


@router.get("/api/admin/extension/icon/preview")
async def preview_icon(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the current custom icon as PNG for preview."""
    await require_admin(request, db)
    config = await _get_config(db)
    icon_b64 = config.get("icon_base64")
    if not icon_b64:
        # Serve default icon
        default_icon = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "chrome_extension", "icons", "icon128.png"
        )
        if os.path.exists(default_icon):
            return FileResponse(default_icon, media_type="image/png")
        raise HTTPException(404, "No icon available")

    icon_data = base64.b64decode(icon_b64)
    return Response(content=icon_data, media_type="image/png")


# ── HTTPS / CA certificate endpoints ────────────────────────────────────

CA_CERT_PATH = "/certs/ca.crt"


@router.get("/api/admin/extension/ca-cert")
async def download_ca_cert(request: Request, db: AsyncSession = Depends(get_db)):
    """Download the CA certificate for upload to Google Admin Console."""
    await require_admin(request, db)
    if not os.path.exists(CA_CERT_PATH):
        raise HTTPException(404, "CA certificate not found. Ensure the cert-init container has run.")
    return FileResponse(
        CA_CERT_PATH,
        media_type="application/x-pem-file",
        filename="BrowserReporter-CA.crt",
    )


@router.get("/api/admin/extension/ca-cert/status")
async def ca_cert_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Check if the CA certificate exists."""
    await require_admin(request, db)
    exists = os.path.exists(CA_CERT_PATH)
    info = {}
    if exists:
        import subprocess
        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", CA_CERT_PATH, "-noout",
                 "-subject", "-enddate", "-fingerprint", "-sha256"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("subject="):
                        info["subject"] = line.split("=", 1)[1].strip()
                    elif line.startswith("notAfter="):
                        info["expires"] = line.split("=", 1)[1].strip()
                    elif "Fingerprint" in line:
                        info["fingerprint"] = line.split("=", 1)[1].strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # openssl not available in container — just report existence
            pass
    return {"exists": exists, **info}
