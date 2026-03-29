"""HTTP reporter — sends collected visits to the BrowserGuardianConsole API."""

import logging
from typing import List, Optional

import requests

try:
    from .browsers import Visit
    from .version import __version__
except ImportError:
    from browsers import Visit
    from version import __version__

logger = logging.getLogger("browser_guardian_agent")

_TIMEOUT = 30  # seconds


def check_version(server_url: str) -> Optional[str]:
    """Check the latest agent version from the server.

    Returns the remote version string, or None on failure.
    """
    try:
        resp = requests.get(f"{server_url}/api/agent/version", timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("version")
    except Exception as e:
        logger.warning("Version check failed: %s", e)
        return None


def send_visits(
    server_url: str,
    username: str,
    computer_name: str,
    visits: List[Visit],
) -> bool:
    """POST visits to /api/reports/data.

    Returns True on success, False on failure.
    """
    payload = {
        "Username": username,
        "UserInfo": {
            "Username": username,
            "DisplayName": None,
            "Email": None,
        },
        "Visits": [
            {
                "Url": v.url,
                "Title": v.title,
                "VisitTime": v.visit_time_epoch_ms,
                "ComputerName": computer_name,
            }
            for v in visits
        ],
    }

    url = f"{server_url}/api/reports/data"
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        logger.info("Sent %d visits successfully", len(visits))
        return True
    except Exception as e:
        logger.error("Failed to send visits: %s", e)
        return False
