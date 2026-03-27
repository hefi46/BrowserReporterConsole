"""Chrome and Edge browsing history extraction.

Reads the SQLite History database from each browser's user profile.
The DB is copied to a temp file first since browsers hold a lock on it.
"""

import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple

logger = logging.getLogger("browser_reporter_agent")

# Chrome/Edge use WebKit timestamps: microseconds since 1601-01-01 00:00:00 UTC
_WEBKIT_EPOCH_DIFF = 11644473600  # seconds between 1601-01-01 and 1970-01-01


class Visit(NamedTuple):
    url: str
    title: str
    visit_time_webkit: int  # raw WebKit timestamp for state tracking
    visit_time_epoch_ms: int  # epoch milliseconds for API


def webkit_to_epoch_ms(webkit_ts: int) -> int:
    """Convert Chrome WebKit timestamp to Unix epoch milliseconds."""
    return int((webkit_ts / 1_000_000 - _WEBKIT_EPOCH_DIFF) * 1000)


def epoch_ms_to_webkit(epoch_ms: int) -> int:
    """Convert Unix epoch milliseconds to Chrome WebKit timestamp."""
    return int((epoch_ms / 1000 + _WEBKIT_EPOCH_DIFF) * 1_000_000)


def _max_history_webkit(max_days: int) -> int:
    """Return the WebKit timestamp for max_days ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    epoch_s = cutoff.timestamp()
    return int((epoch_s + _WEBKIT_EPOCH_DIFF) * 1_000_000)


def _find_history_paths(browser_data_dir: str) -> List[str]:
    """Find all History database files across browser profiles."""
    paths = []
    if not os.path.isdir(browser_data_dir):
        return paths

    # Default profile
    default = os.path.join(browser_data_dir, "Default", "History")
    if os.path.isfile(default):
        paths.append(default)

    # Additional profiles (Profile 1, Profile 2, etc.)
    for entry in os.listdir(browser_data_dir):
        if entry.startswith("Profile "):
            p = os.path.join(browser_data_dir, entry, "History")
            if os.path.isfile(p):
                paths.append(p)

    return paths


def _query_history(db_path: str, since_webkit: int) -> List[Visit]:
    """Copy the History DB to temp and query visits since the given timestamp."""
    visits: List[Visit] = []
    tmp_path = None
    try:
        # Copy to temp — browser locks the original
        fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        shutil.copy2(db_path, tmp_path)

        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        try:
            cursor = conn.execute(
                """
                SELECT urls.url, urls.title, visits.visit_time
                FROM visits
                INNER JOIN urls ON visits.url = urls.id
                WHERE visits.visit_time > ?
                ORDER BY visits.visit_time ASC
                """,
                (since_webkit,),
            )
            for url, title, vt in cursor.fetchall():
                visits.append(Visit(
                    url=url or "",
                    title=title or "",
                    visit_time_webkit=vt,
                    visit_time_epoch_ms=webkit_to_epoch_ms(vt),
                ))
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to read history from %s: %s", db_path, e)
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return visits


def collect_visits(
    browser: str,
    since_webkit: int,
    max_history_days: int,
) -> List[Visit]:
    """Collect visits from a browser since the given WebKit timestamp.

    Args:
        browser: "chrome" or "edge"
        since_webkit: WebKit timestamp of last sent visit (0 for all)
        max_history_days: Maximum age of visits to collect
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        logger.error("LOCALAPPDATA not set")
        return []

    if browser == "chrome":
        data_dir = os.path.join(local_app_data, "Google", "Chrome", "User Data")
    elif browser == "edge":
        data_dir = os.path.join(local_app_data, "Microsoft", "Edge", "User Data")
    else:
        logger.error("Unknown browser: %s", browser)
        return []

    # Use the more recent of since_webkit and max_history_days cutoff
    cutoff = _max_history_webkit(max_history_days)
    effective_since = max(since_webkit, cutoff)

    history_paths = _find_history_paths(data_dir)
    if not history_paths:
        logger.info("No %s history databases found", browser)
        return []

    all_visits: List[Visit] = []
    for path in history_paths:
        logger.info("Reading %s history: %s", browser, path)
        all_visits.extend(_query_history(path, effective_since))

    logger.info("Collected %d visits from %s", len(all_visits), browser)
    return all_visits
