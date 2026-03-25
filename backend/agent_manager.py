"""Windows Agent manager — handles upload, versioning, building, and packaging.

Configuration is stored in the app_settings table (key = "agent_config"),
following the same pattern as extension_builder.py.

Building uses the Docker SDK to spin up a one-shot `cdrx/pyinstaller-windows`
container that cross-compiles the Python agent into a Windows .exe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppSetting

logger = logging.getLogger("browser_reporter")

SETTINGS_KEY = "agent_config"
BUILD_IMAGE = "cdrx/pyinstaller-windows:python3"

_DEFAULT_CONFIG = {
    "current_version": "",
    "last_uploaded_at": "",
    "exe_filename": "",
    "exe_sha256": "",
    "exe_size_bytes": 0,
    "last_build_log": "",
}


async def load_agent_config(db: AsyncSession) -> dict:
    """Load agent config from DB, falling back to defaults."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        try:
            saved = json.loads(row.value)
            return {**_DEFAULT_CONFIG, **saved}
        except (json.JSONDecodeError, TypeError):
            pass
    return dict(_DEFAULT_CONFIG)


async def save_agent_config(db: AsyncSession, config: dict) -> None:
    """Save agent config to DB (upsert)."""
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


def get_agent_dir(static_dir: str | Path) -> Path:
    """Return the directory where agent files are stored."""
    p = Path(static_dir) / "agent"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def save_uploaded_exe(
    db: AsyncSession,
    static_dir: str | Path,
    file_content: bytes,
    version: str,
) -> dict:
    """Save an uploaded agent .exe and update config.

    Returns the updated config dict.
    """
    agent_dir = get_agent_dir(static_dir)
    exe_path = agent_dir / "BrowserReporter.exe"

    # Write .exe
    exe_path.write_bytes(file_content)

    # Compute checksum
    sha256 = hashlib.sha256(file_content).hexdigest()

    # Update config
    config = await load_agent_config(db)
    config["current_version"] = version
    config["last_uploaded_at"] = datetime.now(timezone.utc).isoformat()
    config["exe_filename"] = "BrowserReporter.exe"
    config["exe_sha256"] = sha256
    config["exe_size_bytes"] = len(file_content)
    await save_agent_config(db, config)

    logger.info(
        "Agent .exe uploaded: version=%s, size=%d, sha256=%s",
        version, len(file_content), sha256[:16],
    )
    return config


def get_exe_path(static_dir: str | Path) -> Optional[Path]:
    """Return the path to the agent .exe, or None if not uploaded yet."""
    p = get_agent_dir(static_dir) / "BrowserReporter.exe"
    return p if p.is_file() else None


# ── Docker-based build ────────────────────────────────────────────────


def _bump_version(current: str) -> str:
    """Increment the patch version: 1.0.0 → 1.0.1."""
    if not current:
        return "1.0.0"
    parts = current.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    return ".".join(parts)


def _prepare_build_source(
    agent_source_dir: str | Path,
    build_dir: str | Path,
    new_version: str,
) -> Path:
    """Prepare the build staging directory with flattened source.

    Copies agent source, rewrites relative imports to absolute, injects
    the version, and creates the PyInstaller spec — all needed for the
    cdrx/pyinstaller-windows container to build a standalone .exe.

    Returns the src_path within build_dir.
    """
    build_path = Path(build_dir)
    src_path = build_path / "src"

    # Clean previous build artifacts
    if src_path.exists():
        shutil.rmtree(str(src_path))
    src_path.mkdir(parents=True, exist_ok=True)

    # Copy agent source to build staging area
    agent_src = Path(agent_source_dir)
    for item in agent_src.iterdir():
        if item.name.startswith(("__pycache__", ".")) or item.suffix == ".pyc":
            continue
        if item.is_file():
            shutil.copy2(str(item), str(src_path / item.name))

    # Inject the new version
    (src_path / "version.py").write_text(
        f'"""BrowserReporter Windows Agent version."""\n\n__version__ = "{new_version}"\n',
        encoding="utf-8",
    )

    # Create flat entry-point for PyInstaller (no relative imports)
    (src_path / "entry_point.py").write_text(
        '"""PyInstaller entry point."""\n'
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "from agent import main\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    # Rewrite relative imports to absolute for flat build
    _rewrite_imports = {
        "agent.py": [
            ("from .config import", "from config import"),
            ("from .version import", "from version import"),
            ("from .state import", "from state import"),
            ("from .browsers import", "from browsers import"),
            ("from .reporter import", "from reporter import"),
        ],
        "reporter.py": [
            ("from .browsers import", "from browsers import"),
            ("from .version import", "from version import"),
        ],
    }
    for filename, replacements in _rewrite_imports.items():
        fp = src_path / filename
        if fp.exists():
            txt = fp.read_text(encoding="utf-8")
            for old, new in replacements:
                txt = txt.replace(old, new)
            fp.write_text(txt, encoding="utf-8")

    # Write PyInstaller spec
    (src_path / "agent_build.spec").write_text(
        "# Auto-generated PyInstaller spec for Docker build\n"
        "a = Analysis(\n"
        "    ['entry_point.py'],\n"
        "    pathex=['/src'],\n"
        "    binaries=[],\n"
        "    datas=[],\n"
        "    hiddenimports=[\n"
        "        'config', 'version', 'state', 'browsers', 'reporter', 'agent',\n"
        "        'Crypto.Cipher.AES', 'Crypto.Util.Padding',\n"
        "    ],\n"
        "    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,\n"
        ")\n"
        "pyz = PYZ(a.pure)\n"
        "exe = EXE(\n"
        "    pyz, a.scripts, a.binaries, a.datas, [],\n"
        "    name='BrowserReporter', debug=False, strip=False, upx=True,\n"
        "    runtime_tmpdir=None, console=False, icon=None,\n"
        ")\n",
        encoding="utf-8",
    )

    return src_path


def _find_agent_build_volume() -> str:
    """Find the Docker named volume used for agent builds.

    The backend's /app/agent_build is a named volume in docker-compose.
    We need its actual name to mount it in the build container.
    """
    import docker

    client = docker.from_env()

    # Inspect our own container to find the volume name
    hostname = os.environ.get("HOSTNAME", "")
    if hostname:
        try:
            self_container = client.containers.get(hostname)
            for mount in self_container.attrs.get("Mounts", []):
                if mount.get("Destination") == "/app/agent_build":
                    return mount["Name"]
        except Exception as e:
            logger.warning("Could not inspect own container: %s", e)

    # Fallback: search for volumes matching the pattern
    for vol in client.volumes.list():
        if "agent_build" in vol.name:
            return vol.name

    raise RuntimeError(
        "Cannot find the agent_build Docker volume. "
        "Ensure docker-compose.yml defines the agent_build volume."
    )


def _run_docker_build(src_path: Path) -> tuple[int, str]:
    """Run the Docker build container (blocking). Returns (exit_code, log).

    Uses the shared agent_build named volume so both the backend and
    build containers can access the same files (avoids DinD path issues).
    """
    import docker

    client = docker.from_env()
    client.ping()

    # Pull image if needed
    logger.info("Ensuring build image %s is available...", BUILD_IMAGE)
    try:
        client.images.get(BUILD_IMAGE)
    except docker.errors.ImageNotFound:
        logger.info("Pulling %s (first time — may take several minutes)...", BUILD_IMAGE)
        client.images.pull(BUILD_IMAGE)

    # Find the shared named volume
    volume_name = _find_agent_build_volume()
    logger.info("Using Docker volume: %s", volume_name)

    # The cdrx/pyinstaller-windows image uses Wine + Python 3.7.
    # We override the entrypoint and upgrade pip + pyinstaller for compat.
    build_cmd = (
        "cd /build/src && "
        "python -m pip install --upgrade pip && "
        "pip install --only-binary :all: requests pycryptodome && "
        "pip install --upgrade pyinstaller && "
        "pyinstaller agent_build.spec "
        "--distpath /build/src/dist --workpath /tmp/build"
    )

    logger.info("Starting Docker build container...")
    container = client.containers.run(
        BUILD_IMAGE,
        entrypoint="/bin/bash",
        command=["-c", build_cmd],
        volumes={volume_name: {"bind": "/build", "mode": "rw"}},
        working_dir="/build/src",
        detach=True,
        remove=False,
    )

    try:
        result = container.wait(timeout=600)
        build_log = container.logs(tail=300).decode("utf-8", errors="replace")
        exit_code = result.get("StatusCode", -1)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

    return exit_code, build_log


async def build_agent_exe(
    db: AsyncSession,
    static_dir: str | Path,
    agent_source_dir: str | Path,
    build_dir: str | Path,
) -> dict:
    """Build the Windows agent .exe using Docker (cdrx/pyinstaller-windows).

    Blocking Docker operations run in a thread to avoid freezing the event loop.

    Returns dict with: version, exe_size_bytes, exe_sha256, build_log
    """
    import asyncio

    try:
        import docker  # noqa: F401
    except ImportError:
        raise RuntimeError("Docker SDK not installed. Run: pip install docker")

    # Verify Docker connectivity (quick, blocking but fast)
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        raise RuntimeError(
            f"Cannot connect to Docker daemon. Is the Docker socket mounted? Error: {e}"
        )

    # Determine version from DB (async)
    config = await load_agent_config(db)
    new_version = _bump_version(config.get("current_version", ""))

    # Prepare build source (fast, file I/O)
    src_path = _prepare_build_source(agent_source_dir, build_dir, new_version)

    # Run blocking Docker build in a thread
    logger.info("Building agent v%s via Docker...", new_version)
    exit_code, build_log = await asyncio.to_thread(_run_docker_build, src_path)

    if exit_code != 0:
        logger.error("Agent build failed (exit %d):\n%s", exit_code, build_log[-2000:])
        raise RuntimeError(
            f"Build failed (exit code {exit_code}).\n\nBuild log:\n{build_log[-2000:]}"
        )

    # Check output
    exe_path = src_path / "dist" / "BrowserReporter.exe"
    if not exe_path.is_file():
        raise RuntimeError(
            f"Build completed but .exe not found.\n\nBuild log:\n{build_log[-1000:]}"
        )

    # Save to static dir and DB (async)
    exe_content = exe_path.read_bytes()
    final_config = await save_uploaded_exe(db, str(static_dir), exe_content, new_version)

    final_config["last_build_log"] = build_log[-2000:]
    await save_agent_config(db, final_config)

    logger.info("Agent build complete: v%s, %d bytes", new_version, len(exe_content))

    return {
        "version": new_version,
        "exe_size_bytes": len(exe_content),
        "exe_sha256": final_config["exe_sha256"],
        "build_log": build_log[-2000:],
    }
