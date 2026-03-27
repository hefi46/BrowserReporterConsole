"""Optional LDAP / Active Directory authentication.

LDAP settings can be configured in two ways (DB takes priority):
  1. Admin panel → LDAP Configuration section (stored in app_settings table)
  2. Environment variables (fallback when DB config is absent or disabled)

When neither source has LDAP enabled, all functions are no-ops and the
existing local auth continues to work unchanged.

Environment variables (used as fallback):
    LDAP_ENABLED             = true
    LDAP_SERVER              = ldap://dc01.school.local  (or ldaps:// for TLS)
    LDAP_BASE_DN             = DC=school,DC=local
    LDAP_BIND_DN             = CN=svc-ldap,OU=ServiceAccounts,DC=school,DC=local
    LDAP_BIND_PASSWORD       = <service-account-password>
    LDAP_USER_SEARCH_FILTER  = (&(objectClass=user)(sAMAccountName={username}))
    LDAP_USE_SSL             = false
    LDAP_ADMIN_GROUP_DN      = CN=BrowserReporter-Admins,OU=Groups,DC=school,DC=local
    LDAP_DEFAULT_ROLE        = user
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("browser_reporter")

# ── Default / empty config ───────────────────────────────────────────────

_DEFAULT_LDAP_CONFIG = {
    "enabled": False,
    "server": "",
    "base_dn": "",
    "bind_dn": "",
    "bind_password": "",
    "user_search_filter": "(&(objectClass=user)(sAMAccountName={username}))",
    "use_ssl": False,
    "admin_group_dn": "",
    "default_role": "user",
    "enrichment_enabled": False,
    "homegroup_attribute": "department",
}


def get_env_ldap_config() -> dict:
    """Build an LDAP config dict from environment variables."""
    return {
        "enabled": os.getenv("LDAP_ENABLED", "").lower() == "true",
        "server": os.getenv("LDAP_SERVER", ""),
        "base_dn": os.getenv("LDAP_BASE_DN", ""),
        "bind_dn": os.getenv("LDAP_BIND_DN", ""),
        "bind_password": os.getenv("LDAP_BIND_PASSWORD", ""),
        "user_search_filter": os.getenv(
            "LDAP_USER_SEARCH_FILTER",
            "(&(objectClass=user)(sAMAccountName={username}))",
        ),
        "use_ssl": os.getenv("LDAP_USE_SSL", "false").lower() == "true",
        "admin_group_dn": os.getenv("LDAP_ADMIN_GROUP_DN", ""),
        "default_role": os.getenv("LDAP_DEFAULT_ROLE", "user"),
        "enrichment_enabled": os.getenv("LDAP_ENRICHMENT_ENABLED", "").lower() == "true",
        "homegroup_attribute": os.getenv("LDAP_HOMEGROUP_ATTRIBUTE", "department"),
    }


def get_default_config() -> dict:
    """Return a copy of the default (empty/disabled) config."""
    return dict(_DEFAULT_LDAP_CONFIG)


# ── DB helpers (called from routes with an existing session) ─────────────

async def load_ldap_config_from_db(db) -> dict | None:
    """Load LDAP config from the app_settings table.  Returns None if not set."""
    from sqlalchemy import select
    from .models import AppSetting

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == "ldap_config")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupt ldap_config in app_settings — ignoring")
        return None


async def save_ldap_config_to_db(db, config: dict) -> None:
    """Upsert LDAP config into the app_settings table."""
    from datetime import datetime, timezone
    from sqlalchemy import select, update
    from .models import AppSetting

    now = datetime.now(timezone.utc)
    json_value = json.dumps(config)

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == "ldap_config")
    )
    existing = result.scalar_one_or_none()

    if existing:
        await db.execute(
            update(AppSetting)
            .where(AppSetting.key == "ldap_config")
            .values(value=json_value, updated_at=now)
        )
    else:
        db.add(AppSetting(key="ldap_config", value=json_value, updated_at=now))


async def get_effective_ldap_config(db) -> dict:
    """Return the LDAP config to use: DB first, then env vars, then defaults."""
    db_config = await load_ldap_config_from_db(db)
    if db_config is not None:
        return db_config
    env_config = get_env_ldap_config()
    if env_config["enabled"]:
        return env_config
    return get_default_config()


def ldap_enabled() -> bool:
    """Quick check using env vars only (for template rendering hints).

    The authoritative check happens at login time via get_effective_ldap_config().
    """
    return os.getenv("LDAP_ENABLED", "").lower() == "true"


# ── Core LDAP authentication ────────────────────────────────────────────

def authenticate_ldap(username: str, password: str, config: dict | None = None) -> dict | None:
    """Try to authenticate *username* against Active Directory.

    *config* is an LDAP config dict.  If ``None`` is passed, env vars are used
    as a legacy fallback (but the preferred path is to pass the DB config).

    Returns a dict with user attributes on success, ``None`` on failure.
    """
    if config is None:
        config = get_env_ldap_config()

    if not config.get("enabled"):
        return None

    # Import here so ldap3 is only required when LDAP is actually used.
    from ldap3 import Server, Connection, ALL, SUBTREE  # noqa: WPS433

    server_url = config.get("server", "")
    base_dn = config.get("base_dn", "")
    if not server_url or not base_dn:
        logger.error("LDAP enabled but server / base_dn not configured")
        return None

    try:
        server = Server(server_url, use_ssl=config.get("use_ssl", False), get_info=ALL)

        # Step 1 — bind with service account to search for the user DN
        svc_conn = Connection(
            server,
            user=config.get("bind_dn", ""),
            password=config.get("bind_password", ""),
            auto_bind=True,
        )
        search_filter = config.get(
            "user_search_filter",
            "(&(objectClass=user)(sAMAccountName={username}))",
        ).replace("{username}", username)

        svc_conn.search(
            base_dn,
            search_filter,
            search_scope=SUBTREE,
            attributes=["distinguishedName", "sAMAccountName", "displayName", "mail", "memberOf"],
        )

        if not svc_conn.entries:
            logger.info("LDAP: user '%s' not found in directory", username)
            svc_conn.unbind()
            return None

        entry = svc_conn.entries[0]
        user_dn = str(entry.distinguishedName)
        display_name = str(entry.displayName) if entry.displayName else username
        email = str(entry.mail) if entry.mail else ""
        member_of = [str(g) for g in entry.memberOf] if entry.memberOf else []
        svc_conn.unbind()

        # Step 2 — bind as the actual user (validates their password)
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()

        # Determine role
        role = config.get("default_role", "user")
        admin_group = config.get("admin_group_dn", "")
        if admin_group and admin_group in member_of:
            role = "admin"

        logger.info("LDAP: authenticated '%s' (role=%s)", username, role)
        return {
            "username": username,
            "display_name": display_name,
            "email": email,
            "role": role,
        }

    except Exception:
        logger.info("LDAP: authentication failed for '%s'", username)
        return None


def lookup_user_details(username: str, config: dict) -> dict | None:
    """Look up a user's details from AD using the service account.

    Returns a dict with givenName, sn, department, displayName, mail,
    sAMAccountName on success, ``None`` if not found or LDAP is disabled.
    No password validation — this is a read-only directory lookup.
    """
    if not config.get("enabled") and not config.get("enrichment_enabled"):
        return None

    from ldap3 import Server, Connection, ALL, SUBTREE  # noqa: WPS433

    server_url = config.get("server", "")
    base_dn = config.get("base_dn", "")
    if not server_url or not base_dn:
        return None

    homegroup_attr = config.get("homegroup_attribute", "department")

    try:
        server = Server(server_url, use_ssl=config.get("use_ssl", False), get_info=ALL)
        svc_conn = Connection(
            server,
            user=config.get("bind_dn", ""),
            password=config.get("bind_password", ""),
            auto_bind=True,
        )

        search_filter = config.get(
            "user_search_filter",
            "(&(objectClass=user)(sAMAccountName={username}))",
        ).replace("{username}", username)

        # Build attribute list, including the configured homegroup attribute
        search_attrs = ["sAMAccountName", "givenName", "sn", "displayName", "mail"]
        if homegroup_attr not in search_attrs:
            search_attrs.append(homegroup_attr)

        svc_conn.search(
            base_dn,
            search_filter,
            search_scope=SUBTREE,
            attributes=search_attrs,
        )

        if not svc_conn.entries:
            svc_conn.unbind()
            return None

        entry = svc_conn.entries[0]

        # Read the configured homegroup attribute safely
        homegroup_value = None
        try:
            attr_val = getattr(entry, homegroup_attr, None)
            if attr_val:
                homegroup_value = str(attr_val)
        except Exception:
            pass

        result = {
            "sAMAccountName": str(entry.sAMAccountName) if entry.sAMAccountName else username,
            "first_name": str(entry.givenName) if entry.givenName else None,
            "last_name": str(entry.sn) if entry.sn else None,
            "department": homegroup_value,
            "display_name": str(entry.displayName) if entry.displayName else None,
            "email": str(entry.mail) if entry.mail else None,
        }
        svc_conn.unbind()

        logger.info("LDAP: looked up details for '%s' — homegroup(%s)=%s", username, homegroup_attr, homegroup_value)
        return result

    except Exception:
        logger.warning("LDAP: lookup failed for '%s'", username, exc_info=True)
        return None


def test_ldap_connection(config: dict) -> dict:
    """Test an LDAP connection with the given config.

    Returns ``{"success": True, "message": ...}`` or
    ``{"success": False, "message": ...}``.
    """
    from ldap3 import Server, Connection, ALL  # noqa: WPS433

    server_url = config.get("server", "")
    if not server_url:
        return {"success": False, "message": "No LDAP server URL configured"}

    try:
        server = Server(server_url, use_ssl=config.get("use_ssl", False), get_info=ALL)
        conn = Connection(
            server,
            user=config.get("bind_dn", ""),
            password=config.get("bind_password", ""),
            auto_bind=True,
        )
        server_info = str(server.info) if server.info else "connected"
        conn.unbind()
        return {"success": True, "message": f"Successfully connected to {server_url}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
