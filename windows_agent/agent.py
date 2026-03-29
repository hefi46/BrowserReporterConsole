"""BrowserGuardian Windows Agent — main entry point.

Runs once per invocation (not a daemon). Designed to be launched by a
GPO scheduled task on user login via the PowerShell bootstrap script.
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

try:
    from .config import load_config
    from .version import __version__
    from .state import get_last_sent, set_last_sent
    from .browsers import collect_visits
    from .reporter import check_version, send_visits
except ImportError:
    from config import load_config
    from version import __version__
    from state import get_last_sent, set_last_sent
    from browsers import collect_visits
    from reporter import check_version, send_visits

_LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "BrowserGuardian",
)


def _setup_logging(config: dict) -> None:
    """Configure rotating file + stderr logging."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    log_file = os.path.join(_LOG_DIR, "agent.log")
    max_bytes = config.get("log_max_mb", 10) * 1024 * 1024
    backup_count = config.get("log_roll_count", 3)

    handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root = logging.getLogger("browser_guardian_agent")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Also log to stderr when running interactively
    if sys.stderr and sys.stderr.isatty():
        root.addHandler(logging.StreamHandler(sys.stderr))


def _in_time_window(config: dict) -> bool:
    """Check if the current time is within the configured monitoring window."""
    start_str = config.get("monitored_start_time", "00:00")
    end_str = config.get("monitored_end_time", "23:59")

    try:
        now = datetime.now().time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        if start <= end:
            return start <= now <= end
        else:
            # Wraps midnight (e.g. 22:00 → 06:00)
            return now >= start or now <= end
    except ValueError:
        return True  # If parsing fails, assume always active


def main() -> None:
    logger = logging.getLogger("browser_guardian_agent")

    # 1. Load config
    config = load_config()
    _setup_logging(config)

    logger.info("BrowserGuardian Agent v%s starting", __version__)

    server_url = config.get("server_url", "http://localhost:8000").rstrip("/")

    # 2. Version check
    remote_version = check_version(server_url)
    if remote_version and remote_version != __version__:
        logger.info(
            "Update available: local=%s, remote=%s. "
            "The bootstrap script will pull the new version on next login.",
            __version__, remote_version,
        )

    # 3. Check time window
    if not _in_time_window(config):
        logger.info("Outside monitoring window, exiting")
        return

    # 4. Get identity
    username = os.environ.get("USERNAME", "unknown")
    computer_name = os.environ.get("COMPUTERNAME", "unknown")
    logger.info("User: %s, Computer: %s", username, computer_name)

    # 5. Collect browser history
    max_days = config.get("max_history_days", 30)
    browsers_to_check = []
    if config.get("enable_chrome", True):
        browsers_to_check.append("chrome")
    if config.get("enable_edge", True):
        browsers_to_check.append("edge")

    all_visits = []
    max_webkit_by_browser = {}  # type: Dict[str, int]

    for browser in browsers_to_check:
        since = get_last_sent(browser)
        visits = collect_visits(browser, since, max_days)
        if visits:
            all_visits.extend(visits)
            max_webkit_by_browser[browser] = max(v.visit_time_webkit for v in visits)

    if not all_visits:
        logger.info("No new visits to report")
        return

    logger.info("Total visits to send: %d", len(all_visits))

    # 6. Send to server
    if send_visits(server_url, username, computer_name, all_visits):
        # 7. Update state only on success
        for browser, max_ts in max_webkit_by_browser.items():
            set_last_sent(browser, max_ts)
        logger.info("Done — state updated")
    else:
        logger.warning("Send failed — will retry on next run")


if __name__ == "__main__":
    main()
