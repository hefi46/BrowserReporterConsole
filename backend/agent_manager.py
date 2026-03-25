"""Windows Agent manager — serves the pre-built agent from static/agent/.

The agent .exe and version.txt are baked into the Docker image at build time.
No upload or DB config needed.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("browser_reporter")


def get_agent_dir(static_dir: str | Path) -> Path:
    """Return the directory where agent files are stored."""
    return Path(static_dir) / "agent"


def get_exe_path(static_dir: str | Path) -> Optional[Path]:
    """Return the path to the agent .exe, or None if not present."""
    p = get_agent_dir(static_dir) / "BrowserReporter.exe"
    return p if p.is_file() else None


def get_agent_version(static_dir: str | Path) -> str:
    """Read agent version from version.txt next to the .exe."""
    version_file = get_agent_dir(static_dir) / "version.txt"
    if version_file.is_file():
        return version_file.read_text().strip()
    return ""


def get_agent_info(static_dir: str | Path) -> dict:
    """Return basic info about the shipped agent."""
    exe = get_exe_path(static_dir)
    version = get_agent_version(static_dir)
    if not exe:
        return {"available": False, "version": ""}
    return {
        "available": True,
        "version": version,
        "size_bytes": exe.stat().st_size,
        "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
    }
