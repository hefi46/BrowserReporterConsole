# Plan: Rebrand to BrowserGuardian, New Repo, v1.0.0 Release

## Context
The `feature/rebrand-browser-guardian` branch did a comprehensive rename (BrowserReporter → BrowserGuardian) but is 22 commits behind main. Main has since added LDAP bulk enrichment, Go agent improvements, security hardening (no more hardcoded master key), and collection interval changes. We need to merge main's changes in, complete the rebrand on new code, rehouse into a new private repo called **BrowserGuardian**, and ship as v1.0.0.

---

## Step 1: Merge main into the rebrand branch

```bash
git checkout feature/rebrand-browser-guardian
git merge main
```

The merge is clean (no real conflicts — verified via `git merge-tree`). The `windows_agent/` directory was deleted on main but renamed on the branch — accept main's deletion since the Python agent is replaced by the Go agent.

**Key files to watch during merge:**
- `backend/crud.py` — main added `func` import + coalesce logic
- `backend/routers/admin.py` — main added LDAP bulk enrichment endpoint
- `backend/utils.py` — main removed hardcoded master key (eliminates rebrand's key rename)
- `docker-compose.yml` — main added env var passthrough
- `windows_agent_go/*` — main added resilient config, dormant mode, etc.

---

## Step 2: Complete the rebrand on new/changed code

After merge, main's 22 commits reintroduced `BrowserReporter` references in new code. Run a systematic find-and-replace (longer strings first to avoid double-replacement):

1. `BrowserReporterConsole` → `BrowserGuardianConsole`
2. `BrowserReporter` → `BrowserGuardian`
3. `browser_reporter` → `browser_guardian`
4. `browserreporterconsole` → `browserguardianconsole`
5. `browserreporter` → `browserguardian`

**Files needing attention (from main's new code):**
- `backend/crud.py` — logger name
- `backend/routers/admin.py` — logger name, new LDAP enrichment code
- `backend/routers/agent.py` — `BrowserReporter.exe` filename reference
- `backend/agent_manager.py` — logger + exe path
- `backend/ldap_auth.py` — new `enrichment_base_dn` field, logger
- `backend/utils.py` — logger name
- `backend/database.py` — DATABASE_URL default, application_name
- `generate_mock_data.py` — new source tags/browser profile code
- `windows_agent_go/config.go` — new config features with old naming
- `windows_agent_go/main.go` — startup log, new dormant mode code
- `windows_agent_go/bootstrap.ps1` — expanded with new features
- `windows_agent_go/state.go` — directory name references
- `docker-compose.yml` — DB credentials + new env vars
- `.env.example` — new file, check for references
- All templates, scripts, docs, chrome extension files

**Rename binary files:**
- `backend/static/agent/BrowserReporter.exe` → `BrowserGuardian.exe`
- `windows_agent_go/BrowserReporter.exe` → `BrowserGuardian.exe`

**Verify with grep** that zero `BrowserReporter`/`browser_reporter`/`browserreporter` references remain (excluding `.git/` and binary content).

---

## Step 3: Remove development artifacts

Delete these files:
- `review3003.md`
- `WINDOWS_AGENT_PLAN.md`
- `WINDOWS_CLIENT_REQUIREMENTS.md`

---

## Step 4: Set version to 1.0.0

| File | Change |
|------|--------|
| `backend/main.py` | `APP_VERSION = "1.0.0"` |
| `backend/static/agent/version.txt` | `1.0.0` |
| `windows_agent_go/version.go` | Version = `1.0.0` |

---

## Step 5: Update README and CLAUDE.md

- Update project name, description, all internal references
- Update clone URL to new repo
- Ensure DB credentials reference `browser_guardian`
- Document new features from the 22 merged commits (LDAP bulk enrichment, configurable collection intervals, secure config, etc.)

---

## Step 6: Create new GitHub repo and push

```bash
gh repo create hefi46/BrowserGuardian --private --description "Browser Guardian - Web-based browsing activity monitoring dashboard"
git remote add new-origin https://github.com/hefi46/BrowserGuardian.git
git push new-origin feature/rebrand-browser-guardian:main
```

Then update any internal repo URL references (README, docs) to point to `hefi46/BrowserGuardian`.

---

## Step 7: Tag v1.0.0

```bash
git tag -a v1.0.0 -m "BrowserGuardian v1.0.0 - Initial release"
git push new-origin main --tags
```

---

## Verification

1. **Grep check**: `git grep -i "browserreporter"` should return zero results (outside binaries)
2. **Docker build**: `docker compose up -d` should work with new `browser_guardian` DB credentials
3. **Web UI**: Login page should show "Browser Guardian" branding
4. **Agent download**: `/api/agent/exe` should serve `BrowserGuardian.exe`
5. **Version endpoint**: `/api/agent/version` should return `1.0.0`

---

## Critical files to modify
- `backend/main.py`, `backend/crud.py`, `backend/utils.py`, `backend/database.py`
- `backend/routers/admin.py`, `backend/routers/agent.py`, `backend/routers/auth.py`, `backend/routers/deps.py`, `backend/routers/extension.py`, `backend/routers/reports.py`
- `backend/agent_manager.py`, `backend/ldap_auth.py`, `backend/extension_builder.py`, `backend/crx_builder.py`
- `backend/migrations/runner.py`, `backend/migrations/apply_migration.py`
- `backend/scripts/backup_database.sh`, `backend/scripts/maintenance.sh`, `backend/scripts/data_retention.py`, `backend/scripts/crontab.example`
- `backend/templates/*.html`
- `docker-compose.yml`, `Dockerfile`, `.env.example`
- `windows_agent_go/*.go`, `windows_agent_go/bootstrap.ps1`, `windows_agent_go/test-bootstrap.ps1`
- `chrome_extension/*`
- `caddy/generate-certs.sh`
- `generate_mock_data.py`, `tests/conftest.py`, `tests/test_api_auth.py`
- `README.md`, `CLAUDE.md`, `README_DATABASE.md`, `AUTOMATIC_MIGRATIONS.md`
