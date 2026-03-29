"""Chrome Extension builder — customises, packages, and hosts the .crx extension.

Configuration is stored in the app_settings table (key = "extension_config"),
following the same pattern as LDAP config in ldap_auth.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppSetting

logger = logging.getLogger("browser_guardian")

SETTINGS_KEY = "extension_config"

_DEFAULT_CONFIG = {
    "school_name": "",
    "accent_color": "#0d3a6e",
    "notice_text": "",
    "contact_info": "",
    "server_url": "",
    "tracking_start_time": "00:00",
    "tracking_end_time": "23:59",
    "tracking_all_day": True,
    "monitored_emails": [],
    "current_version": "1.0.0",
    "logo_filename": "",
    "extension_id": "",
    "last_built_at": "",
}


# ── Config persistence (same pattern as ldap_auth.py) ────────────────────


async def load_extension_config(db: AsyncSession) -> dict:
    """Load extension config from DB, falling back to defaults."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        try:
            saved = json.loads(row.value)
            merged = {**_DEFAULT_CONFIG, **saved}
            return merged
        except (json.JSONDecodeError, TypeError):
            pass
    return dict(_DEFAULT_CONFIG)


async def save_extension_config(db: AsyncSession, config: dict) -> None:
    """Save extension config to DB (upsert)."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    json_value = json.dumps(config)
    if row:
        row.value = json_value
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(AppSetting(key=SETTINGS_KEY, value=json_value))
    await db.commit()


# ── PEM key management ───────────────────────────────────────────────────


def get_or_create_pem_key(data_dir: str) -> str:
    """Return path to the PEM key, generating one if it doesn't exist."""
    pem_path = os.path.join(data_dir, "extension.pem")
    if os.path.exists(pem_path):
        logger.info("Using existing PEM key: %s", pem_path)
        return pem_path

    # Generate a new RSA 2048-bit key
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    logger.warning("Generating new extension PEM key at %s — keep this safe!", pem_path)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(pem_path, "wb") as f:
        f.write(pem_bytes)
    return pem_path


def compute_extension_id(pem_path: str) -> str:
    """Derive the 32-char Chrome extension ID from a PEM private key.

    Algorithm: SHA-256 of the DER-encoded public key, first 16 bytes,
    each byte mapped to a-p (0->a, 1->b, ..., 15->p).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(pem_path, "rb") as f:
        private_key = load_pem_private_key(f.read(), password=None)

    der_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(der_public).hexdigest()
    # Map first 32 hex chars: 0-9a-f -> a-p
    ext_id = ""
    for ch in digest[:32]:
        ext_id += chr(ord("a") + int(ch, 16))
    return ext_id


# ── Icon generation ──────────────────────────────────────────────────────


def generate_icons(logo_path: str, output_dir: str) -> None:
    """Resize an uploaded logo to 16/48/128px PNGs for the extension."""
    from PIL import Image

    os.makedirs(output_dir, exist_ok=True)
    img = Image.open(logo_path).convert("RGBA")

    for size in (16, 48, 128):
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(os.path.join(output_dir, f"icon{size}.png"), "PNG")

    logger.info("Generated extension icons from %s", logo_path)


def copy_default_icons(template_dir: str, output_dir: str) -> None:
    """Copy default icons from the extension template."""
    src_icons = os.path.join(template_dir, "icons")
    os.makedirs(output_dir, exist_ok=True)
    for fname in ("icon16.png", "icon48.png", "icon128.png"):
        src = os.path.join(src_icons, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(output_dir, fname))


# ── Template injection ───────────────────────────────────────────────────


def inject_customizations(template_dir: str, build_dir: str, config: dict) -> None:
    """Copy extension template to build dir and inject all customizations."""
    # Copy template files to build directory
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    shutil.copytree(template_dir, build_dir)

    # Remove DEPLOY.md — not needed in the packaged extension
    deploy_md = os.path.join(build_dir, "DEPLOY.md")
    if os.path.exists(deploy_md):
        os.remove(deploy_md)

    _inject_background_js(build_dir, config)
    _inject_manifest_json(build_dir, config)
    _inject_popup_html(build_dir, config)


def _inject_background_js(build_dir: str, config: dict) -> None:
    """Inject server URL, tracking hours, and monitored emails into background.js."""
    bg_path = os.path.join(build_dir, "background.js")
    with open(bg_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace server URL
    server_url = config.get("server_url", "http://browserguardian:8000")
    content = re.sub(
        r"const SERVER_URL\s*=\s*'[^']*'",
        f"const SERVER_URL = '{server_url}'",
        content,
    )

    # Inject tracking hours and monitored emails after the existing constants
    tracking_code = _build_tracking_code(config)
    # Insert after the DEDUP_WINDOW_MS constant
    content = content.replace(
        "// In-memory dedup cache",
        f"{tracking_code}\n// In-memory dedup cache",
    )

    # Inject the time check into the tab listener
    if not config.get("tracking_all_day", True):
        # Add time check at the start of the tab onUpdated handler
        time_check = """
  // Tracking hours check
  if (!isWithinTrackingHours()) return;
"""
        content = content.replace(
            "  // Only record once the page has fully loaded and we have a URL",
            f"{time_check}\n  // Only record once the page has fully loaded and we have a URL",
        )

    # Inject email filter into sendVisits
    emails = config.get("monitored_emails", [])
    if emails:
        email_check = """
  // Monitored email filter
  if (MONITORED_EMAILS.length > 0 && !MONITORED_EMAILS.includes(email.toLowerCase())) {
    console.log('[BrowserGuardian] User not in monitored list, skipping send.');
    await chrome.storage.local.set({ visitQueue: [] });
    return;
  }
"""
        content = content.replace(
            "  const deviceName = await getDeviceName();",
            f"{email_check}\n  const deviceName = await getDeviceName();",
        )

    with open(bg_path, "w", encoding="utf-8") as f:
        f.write(content)


def _build_tracking_code(config: dict) -> str:
    """Generate JS constants for tracking hours and monitored emails."""
    lines = []

    # Tracking hours
    tracking_all_day = config.get("tracking_all_day", True)
    start = config.get("tracking_start_time", "00:00")
    end = config.get("tracking_end_time", "23:59")

    start_h, start_m = (int(x) for x in start.split(":"))
    end_h, end_m = (int(x) for x in end.split(":"))

    lines.append(f"const TRACKING_ALL_DAY = {'true' if tracking_all_day else 'false'};")
    lines.append(f"const TRACKING_START = {start_h * 60 + start_m}; // {start} as minutes")
    lines.append(f"const TRACKING_END = {end_h * 60 + end_m}; // {end} as minutes")
    lines.append("")
    lines.append("function isWithinTrackingHours() {")
    lines.append("  if (TRACKING_ALL_DAY) return true;")
    lines.append("  const now = new Date();")
    lines.append("  const mins = now.getHours() * 60 + now.getMinutes();")
    lines.append("  return mins >= TRACKING_START && mins <= TRACKING_END;")
    lines.append("}")
    lines.append("")

    # Monitored emails
    emails = config.get("monitored_emails", [])
    email_list = json.dumps([e.lower().strip() for e in emails if e.strip()])
    lines.append(f"const MONITORED_EMAILS = {email_list};")
    lines.append("")

    return "\n".join(lines)


def _inject_manifest_json(build_dir: str, config: dict) -> None:
    """Update version and host_permissions in manifest.json."""
    manifest_path = os.path.join(build_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["version"] = config.get("current_version", "1.0.0")

    server_url = config.get("server_url", "http://browserguardian:8000")
    # Ensure trailing /* for host permission
    base = server_url.rstrip("/")
    manifest["host_permissions"] = [f"{base}/*"]

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _inject_popup_html(build_dir: str, config: dict) -> None:
    """Inject branding customizations into popup.html."""
    popup_path = os.path.join(build_dir, "popup.html")
    with open(popup_path, "r", encoding="utf-8") as f:
        content = f.read()

    accent = config.get("accent_color", "#0d3a6e")
    school_name = config.get("school_name", "")
    notice_text = config.get("notice_text", "")
    contact_info = config.get("contact_info", "")
    has_logo = bool(config.get("logo_filename", ""))

    # Replace accent colour (all occurrences)
    content = content.replace("#0d3a6e", accent)

    # Replace school name in header
    if school_name:
        content = content.replace("Network Monitoring", school_name)
        content = content.replace("School managed device", "Managed device")

    # Replace notice text
    if notice_text:
        content = re.sub(
            r'(<strong>)[^<]*(</strong>[^<]*<)',
            f'\\1{_html_escape(notice_text)}\\2',
            content,
            count=1,
        )
        # Also replace the full notice paragraph after <strong>
        content = re.sub(
            r'<strong>Internet activity is monitored</strong>[^<]*',
            f'<strong>{_html_escape(notice_text)}</strong>',
            content,
        )

    # Replace footer contact info
    if contact_info:
        content = re.sub(
            r'For help, contact your school IT department\.',
            _html_escape(contact_info),
            content,
        )

    # Replace inline SVG with logo image if uploaded
    if has_logo:
        svg_pattern = r'<svg[^>]*>.*?</svg>'
        logo_img = '<img src="icon48.png" width="26" height="26" style="border-radius: 4px;">'
        content = re.sub(svg_pattern, logo_img, content, flags=re.DOTALL)

    with open(popup_path, "w", encoding="utf-8") as f:
        f.write(content)


def _html_escape(text: str) -> str:
    """Basic HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


# ── Version management ───────────────────────────────────────────────────


def increment_version(current: str) -> str:
    """Bump the patch component: 1.0.0 -> 1.0.1."""
    parts = current.split(".")
    if len(parts) != 3:
        return "1.0.1"
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts[2] = "1"
    return ".".join(parts)


# ── CRX packaging ────────────────────────────────────────────────────────


def build_crx(build_dir: str, pem_path: str, output_path: str) -> None:
    """Package the build directory into a signed .crx file."""
    from crx3.creator import create_crx_file

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    create_crx_file(build_dir, pem_path, output_path)
    logger.info("Built CRX: %s", output_path)


def generate_update_xml(
    extension_id: str, version: str, crx_url: str, output_path: str
) -> None:
    """Write the Chrome update manifest XML."""
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='{extension_id}'>
    <updatecheck codebase='{crx_url}' version='{version}'/>
  </app>
</gupdate>
"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)
    logger.info("Generated update.xml for %s v%s", extension_id, version)


# ── Main build orchestrator ──────────────────────────────────────────────


async def build_extension(
    db: AsyncSession,
    data_dir: str,
    static_dir: str,
    template_dir: str,
    server_url: str,
) -> dict:
    """Full build pipeline: customise, package, host.

    Returns dict with extension_id, version, crx_url, update_url, etc.
    """
    config = await load_extension_config(db)

    # Use provided server_url if config doesn't have one
    if not config.get("server_url"):
        config["server_url"] = server_url

    # Increment version (skip on first ever build)
    if config.get("last_built_at"):
        config["current_version"] = increment_version(
            config.get("current_version", "1.0.0")
        )

    # PEM key
    pem_path = get_or_create_pem_key(data_dir)
    extension_id = compute_extension_id(pem_path)
    config["extension_id"] = extension_id

    # Build in a temp directory
    build_dir = tempfile.mkdtemp(prefix="ext_build_")
    try:
        inject_customizations(template_dir, build_dir, config)

        # Generate or copy icons
        logo_path = os.path.join(data_dir, config.get("logo_filename", ""))
        icons_dir = os.path.join(build_dir, "icons")
        if config.get("logo_filename") and os.path.exists(logo_path):
            generate_icons(logo_path, icons_dir)
        else:
            copy_default_icons(template_dir, icons_dir)

        # Package as CRX
        crx_output = os.path.join(static_dir, "extension", "extension.crx")
        build_crx(build_dir, pem_path, crx_output)

        # Generate update.xml
        base_url = config["server_url"].rstrip("/")
        crx_url = f"{base_url}/static/extension/extension.crx"
        update_url = f"{base_url}/static/extension/update.xml"
        xml_output = os.path.join(static_dir, "extension", "update.xml")
        generate_update_xml(extension_id, config["current_version"], crx_url, xml_output)

    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    # Save updated config
    config["last_built_at"] = datetime.now(timezone.utc).isoformat()
    await save_extension_config(db, config)

    return {
        "extension_id": extension_id,
        "version": config["current_version"],
        "crx_url": crx_url,
        "update_url": update_url,
        "last_built_at": config["last_built_at"],
    }
