"""Persistent state tracking for last-sent timestamps.

State is stored per-browser in %LOCALAPPDATA%\\BrowserGuardian\\state.json
using Chrome WebKit timestamps (microseconds since 1601-01-01) for direct
comparison in SQLite queries.
"""

import json
import logging
import os

logger = logging.getLogger("browser_guardian_agent")

_STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BrowserGuardian")
_STATE_FILE = os.path.join(_STATE_DIR, "state.json")


def _ensure_dir() -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)


def load_state() -> dict:
    """Load state from disk. Returns empty dict if not found."""
    if not os.path.isfile(_STATE_FILE):
        return {}
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not load state: %s", e)
        return {}


def save_state(state: dict) -> None:
    """Persist state to disk."""
    _ensure_dir()
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error("Could not save state: %s", e)


def get_last_sent(browser: str) -> int:
    """Get the last-sent WebKit timestamp for a browser. Returns 0 if none."""
    state = load_state()
    return state.get(f"last_sent_{browser}", 0)


def set_last_sent(browser: str, webkit_ts: int) -> None:
    """Update the last-sent WebKit timestamp for a browser."""
    state = load_state()
    state[f"last_sent_{browser}"] = webkit_ts
    save_state(state)
