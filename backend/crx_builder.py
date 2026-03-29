"""CRX3 Chrome extension packaging and templating.

Builds a customised Chrome extension (.crx) from templates, signs it with
an RSA key, and generates the update.xml manifest for self-hosted deployment.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import zipfile
from pathlib import Path

from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256

# ── Extension file templates ────────────────────────────────────────────

MANIFEST_TEMPLATE = """\
{{
  "manifest_version": 3,
  "name": "{name}",
  "version": "{version}",
  "description": "{description}",

  "background": {{
    "service_worker": "background.js"
  }},

  "action": {{
    "default_popup": "popup.html",
    "default_icon": {{
      "16":  "icons/icon16.png",
      "48":  "icons/icon48.png",
      "128": "icons/icon128.png"
    }}
  }},

  "icons": {{
    "16":  "icons/icon16.png",
    "48":  "icons/icon48.png",
    "128": "icons/icon128.png"
  }},

  "permissions": [
    "tabs",
    "storage",
    "alarms",
    "identity",
    "identity.email",
    "enterprise.deviceAttributes"
  ],

  "host_permissions": [
    "{server_url}/*"
  ]
}}
"""


def _build_popup_html(config: dict) -> str:
    """Generate popup.html from config values."""
    school_name = _escape_html(config.get("school_name", "Network Monitoring"))
    school_subtitle = _escape_html(config.get("school_subtitle", "School managed device"))
    notice_text = _escape_html(config.get("notice_text",
        "Internet activity is monitored on this school-owned device. "
        "All websites visited are logged in accordance with school policy."))
    it_contact = _escape_html(config.get("it_contact", "For help, contact your school IT department."))
    header_color = config.get("header_color", "#0d3a6e")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BrowserGuardian</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      width: 320px;
      background: #f8f9fa;
      color: #212529;
    }}
    .header {{
      background: {header_color};
      color: #fff;
      padding: 14px 18px;
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .header svg {{ flex-shrink: 0; opacity: 0.9; }}
    .header-title {{ font-size: 14px; font-weight: 600; letter-spacing: 0.02em; }}
    .header-sub   {{ font-size: 11px; opacity: 0.75; margin-top: 2px; }}
    .notice {{
      margin: 14px 14px 0;
      padding: 12px 14px;
      background: #e8eef6;
      border-left: 4px solid {header_color};
      border-radius: 0 6px 6px 0;
      font-size: 12.5px;
      line-height: 1.55;
      color: #1a2e45;
    }}
    .notice strong {{ color: {header_color}; }}
    .footer {{
      padding: 14px 14px 16px;
      font-size: 11px;
      color: #868e96;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="header">
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
      <path d="M12 2L4 5v6c0 5.25 3.4 10.14 8 11.36C16.6 21.14 20 16.25 20 11V5l-8-3z"
            fill="rgba(255,255,255,0.9)"/>
      <path d="M9 12l2 2 4-4" stroke="{header_color}" stroke-width="1.8"
            stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <div>
      <div class="header-title">{school_name}</div>
      <div class="header-sub">{school_subtitle}</div>
    </div>
  </div>

  <div class="notice">
    <strong>{notice_text}</strong>
  </div>

  <div class="footer">
    {it_contact}
  </div>
</body>
</html>
"""


def _build_background_js(config: dict) -> str:
    """Generate background.js with server URL and time restrictions."""
    server_url = config.get("server_url", "http://browserguardian:8000").rstrip("/")
    start_time = config.get("reporting_start_time", "00:00")
    end_time = config.get("reporting_end_time", "23:59")
    active_days = config.get("reporting_days", [0, 1, 2, 3, 4, 5, 6])

    # Convert HH:MM to minutes since midnight
    start_minutes = _time_to_minutes(start_time)
    end_minutes = _time_to_minutes(end_time)

    # Build the time restriction check
    has_restrictions = (start_minutes != 0 or end_minutes != 1439
                        or len(active_days) != 7)

    time_check = ""
    if has_restrictions:
        days_json = json.dumps(sorted(active_days))
        time_check = f"""
// ─── Time restrictions ──────────────────────────────────────────────────────
const ACTIVE_DAYS = {days_json};       // 0=Sun … 6=Sat
const REPORT_START_MIN = {start_minutes};  // {start_time}
const REPORT_END_MIN   = {end_minutes};  // {end_time}

function isWithinReportingWindow() {{
  const now = new Date();
  const day = now.getDay();
  if (!ACTIVE_DAYS.includes(day)) return false;
  const mins = now.getHours() * 60 + now.getMinutes();
  return mins >= REPORT_START_MIN && mins <= REPORT_END_MIN;
}}
"""
    else:
        time_check = """
function isWithinReportingWindow() { return true; }
"""

    return f"""\
/**
 * BrowserGuardian - Background Service Worker
 * Collects page visits and batches them to the BrowserGuardian server.
 */

const SERVER_URL = '{server_url}';
const REPORT_ENDPOINT = `${{SERVER_URL}}/api/reports/data`;

// Send any queued visits every 1 minute
const ALARM_NAME = 'browserguardian_send';
const ALARM_INTERVAL_MINUTES = 1;

// Also flush immediately if the queue reaches this size
const QUEUE_FLUSH_THRESHOLD = 50;

/**
 * Deduplication window in milliseconds.
 * If the same normalised URL is seen again within this window (per tab),
 * the duplicate is silently dropped.
 */
const DEDUP_WINDOW_MS = 30_000; // 30 seconds

// In-memory dedup cache: Map<tabId, {{ url: string, ts: number }}>
const lastSeenByTab = new Map();
{time_check}
// ─── Initialisation ──────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(initialise);
chrome.runtime.onStartup.addListener(initialise);

function initialise() {{
  chrome.alarms.clear(ALARM_NAME, () => {{
    chrome.alarms.create(ALARM_NAME, {{
      periodInMinutes: ALARM_INTERVAL_MINUTES,
    }});
  }});

  getUserInfo();
}}

// ─── Tab tracking ─────────────────────────────────────────────────────────────

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {{
  if (changeInfo.status !== 'complete' || !tab.url) return;

  // Check time restrictions
  if (!isWithinReportingWindow()) return;

  const normalisedUrl = normaliseUrl(tab.url);

  const now = Date.now();
  const last = lastSeenByTab.get(tabId);
  if (last && last.url === normalisedUrl && now - last.ts < DEDUP_WINDOW_MS) {{
    return;
  }}
  lastSeenByTab.set(tabId, {{ url: normalisedUrl, ts: now }});

  recordVisit(normalisedUrl, tab.title || normalisedUrl);
}});

function normaliseUrl(rawUrl) {{
  try {{
    const u = new URL(rawUrl);
    u.hash = '';
    return u.toString();
  }} catch {{
    return rawUrl;
  }}
}}

async function recordVisit(url, title) {{
  const computerName = await getDeviceName();

  const visit = {{
    Url: url,
    Title: title,
    VisitTime: Date.now(),
    ComputerName: computerName,
  }};

  const {{ visitQueue = [] }} = await chrome.storage.local.get('visitQueue');
  visitQueue.push(visit);
  await chrome.storage.local.set({{ visitQueue }});

  if (visitQueue.length >= QUEUE_FLUSH_THRESHOLD) {{
    await sendVisits();
  }}
}}

// ─── Periodic send ────────────────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener((alarm) => {{
  if (alarm.name === ALARM_NAME) {{
    sendVisits();
  }}
}});

// ─── Sending logic ────────────────────────────────────────────────────────────

async function sendVisits() {{
  const {{ visitQueue = [] }} = await chrome.storage.local.get('visitQueue');
  if (visitQueue.length === 0) return;

  const {{ email, displayName }} = await getUserInfo();
  const deviceName = await getDeviceName();

  const payload = {{
    Username: email,
    UserInfo: {{
      Username: email,
      DisplayName: displayName,
      Email: email,
    }},
    Visits: visitQueue,
  }};

  try {{
    const response = await fetch(REPORT_ENDPOINT, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload),
    }});

    if (response.ok) {{
      await chrome.storage.local.set({{ visitQueue: [] }});
      console.log(
        `[BrowserGuardian] Sent ${{visitQueue.length}} visits for ${{email}} from ${{deviceName}}.`
      );
    }} else {{
      const errorText = await response.text();
      console.warn(`[BrowserGuardian] Server returned ${{response.status}}: ${{errorText}}`);
    }}
  }} catch (err) {{
    console.warn('[BrowserGuardian] Could not reach server, will retry:', err.message);
  }}
}}

// ─── Identity helpers ─────────────────────────────────────────────────────────

async function getUserInfo() {{
  const result = await new Promise((resolve) => {{
    chrome.identity.getProfileUserInfo({{ accountStatus: 'ANY' }}, (userInfo) => {{
      if (chrome.runtime.lastError || !userInfo.email) {{
        console.warn('[BrowserGuardian] getProfileUserInfo failed:',
          chrome.runtime.lastError?.message || 'empty email');
        resolve({{
          email: 'unknown@schools.vic.edu.au',
          displayName: 'unknown',
          source: 'fallback',
        }});
        return;
      }}
      resolve({{
        email: userInfo.email,
        displayName: userInfo.email.split('@')[0],
        source: 'chrome_identity',
      }});
    }});
  }});

  await chrome.storage.local.set({{ resolvedIdentity: result }});
  console.log(`[BrowserGuardian] Identity: ${{result.source}} → ${{result.email}}`);
  return result;
}}

function getDeviceName() {{
  return new Promise((resolve) => {{
    chrome.storage.local.get('cachedDeviceName', ({{ cachedDeviceName }}) => {{
      if (cachedDeviceName) {{
        resolve(cachedDeviceName);
        return;
      }}

      if (chrome.enterprise && chrome.enterprise.deviceAttributes) {{
        chrome.enterprise.deviceAttributes.getDeviceHostname((hostname) => {{
          if (chrome.runtime.lastError || !hostname) {{
            chrome.enterprise.deviceAttributes.getDeviceSerialNumber((serial) => {{
              const name = serial || 'chromebook';
              chrome.storage.local.set({{ cachedDeviceName: name }});
              resolve(name);
            }});
          }} else {{
            chrome.storage.local.set({{ cachedDeviceName: hostname }});
            resolve(hostname);
          }}
        }});
      }} else {{
        resolve('chromebook');
      }}
    }});
  }});
}}
"""


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def _time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ── CRX3 packaging ─────────────────────────────────────────────────────

def generate_rsa_key() -> str:
    """Generate a new RSA 2048-bit key and return as PEM string."""
    key = RSA.generate(2048)
    return key.export_key("PEM").decode("utf-8")


def compute_extension_id(public_key_der: bytes) -> str:
    """Compute Chrome extension ID from the DER-encoded public key.

    The ID is the first 16 bytes of SHA-256(public_key), each byte mapped
    to the range a–p (base-16 with offset 'a').
    """
    digest = hashlib.sha256(public_key_der).digest()[:16]
    return "".join(chr(ord("a") + (b >> 4)) + chr(ord("a") + (b & 0xF))
                   for b in digest)


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    if value == 0:
        return b"\x00"
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_length_delimited(field_number: int, data: bytes) -> bytes:
    """Encode a protobuf length-delimited field."""
    tag = (field_number << 3) | 2  # wire type 2 = length-delimited
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _build_crx3_header(public_key_der: bytes, signature: bytes,
                       crx_id: bytes) -> bytes:
    """Build the CRX3 protobuf header.

    CRX3 header schema (simplified):
        message CrxFileHeader {
            repeated AsymmetricKeyProof sha256_with_rsa = 2;
            bytes signed_header_data = 10000;
        }
        message AsymmetricKeyProof {
            bytes public_key = 1;
            bytes signature = 2;
        }
        message SignedData {
            bytes crx_id = 1;
        }
    """
    # SignedData { crx_id = 1 }
    signed_data = _encode_length_delimited(1, crx_id)

    # AsymmetricKeyProof { public_key = 1, signature = 2 }
    proof = (_encode_length_delimited(1, public_key_der)
             + _encode_length_delimited(2, signature))

    # CrxFileHeader { sha256_with_rsa = 2, signed_header_data = 10000 }
    header = (_encode_length_delimited(2, proof)
              + _encode_length_delimited(10000, signed_data))

    return header


def build_crx3(zip_data: bytes, pem_key: str) -> tuple[bytes, str]:
    """Package a ZIP as a CRX3 file.

    Returns (crx_bytes, extension_id).
    """
    key = RSA.import_key(pem_key)
    public_key_der = key.publickey().export_key("DER")

    # Extension ID
    ext_id = compute_extension_id(public_key_der)
    crx_id = hashlib.sha256(public_key_der).digest()[:16]

    # SignedData protobuf
    signed_data_proto = _encode_length_delimited(1, crx_id)

    # The data to sign: "CRX3 SignedData\x00" + header_size_le32 + signed_data + zip
    # But per spec, the signature covers:
    #   "CRX3 SignedData\x00" || len(signed_header_data) as 4-byte LE || signed_header_data || archive
    signed_header_data = signed_data_proto
    to_sign = (b"CRX3 SignedData\x00"
               + struct.pack("<I", len(signed_header_data))
               + signed_header_data
               + zip_data)

    # Sign with PKCS#1 v1.5
    h = SHA256.new(to_sign)
    signature = pkcs1_15.new(key).sign(h)

    # Build CRX3 header
    header = _build_crx3_header(public_key_der, signature, crx_id)

    # Assemble CRX3 file: magic + version + header_size + header + zip
    crx = (b"Cr24"
           + struct.pack("<I", 3)  # version 3
           + struct.pack("<I", len(header))
           + header
           + zip_data)

    return crx, ext_id


def generate_update_xml(extension_id: str, version: str,
                        crx_url: str) -> str:
    """Generate update.xml for self-hosted extension deployment."""
    return f"""\
<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='{extension_id}'>
    <updatecheck codebase='{crx_url}' version='{version}' />
  </app>
</gupdate>
"""


# ── High-level build orchestration ──────────────────────────────────────

def get_default_icons() -> dict[str, bytes]:
    """Read the default extension icons from the extension/ directory."""
    ext_dir = Path(__file__).parent.parent / "extension" / "icons"
    icons = {}
    for size in (16, 48, 128):
        icon_path = ext_dir / f"icon{size}.png"
        if icon_path.exists():
            icons[f"icon{size}.png"] = icon_path.read_bytes()
    return icons


def resize_icon(icon_data: bytes) -> dict[str, bytes]:
    """Resize an uploaded PNG icon to 16x16, 48x48, and 128x128.

    Requires Pillow.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(icon_data))
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    icons = {}
    for size in (16, 48, 128):
        resized = img.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        icons[f"icon{size}.png"] = buf.getvalue()
    return icons


def build_extension(config: dict, pem_key: str,
                    icon_data: bytes | None = None) -> tuple[bytes, str, str]:
    """Build the complete extension package.

    Args:
        config: Extension configuration dict.
        pem_key: RSA private key in PEM format.
        icon_data: Optional custom icon PNG (128x128+). If None, uses defaults.

    Returns:
        (crx_bytes, extension_id, update_xml)
    """
    version = config.get("version", "1.0.0")
    server_url = config.get("server_url", "http://browserguardian:8000").rstrip("/")

    # Generate extension files
    manifest = MANIFEST_TEMPLATE.format(
        name=config.get("school_name", "BrowserGuardian"),
        version=version,
        description=config.get("notice_text",
            "School internet activity reporting for managed Chromebooks."),
        server_url=server_url,
    )
    popup = _build_popup_html(config)
    background = _build_background_js(config)

    # Icons
    if icon_data:
        icons = resize_icon(icon_data)
    else:
        icons = get_default_icons()

    # Build ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("popup.html", popup)
        zf.writestr("background.js", background)
        for filename, data in icons.items():
            zf.writestr(f"icons/{filename}", data)
    zip_data = zip_buf.getvalue()

    # Build CRX3
    crx_bytes, ext_id = build_crx3(zip_data, pem_key)

    # Generate update.xml
    update_xml = generate_update_xml(
        ext_id, version,
        f"{server_url}/chromebook-extension/extension.crx",
    )

    return crx_bytes, ext_id, update_xml
