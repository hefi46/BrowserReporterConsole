# Windows Agent for Browser Data Collection

## Context

The BrowserReporterConsole currently has a Chrome extension for Chromebooks that collects browsing data. We need a **Windows agent** (.exe) that does the same thing for domain-joined Windows devices — collecting Chrome/Edge browsing history and reporting it to the server. The agent runs on user login via a scheduled task pointing to a network share, making updates seamless (replace .exe on share, computers pick it up next login).

The server already handles AD attribute enrichment via its existing LDAP integration, so the agent just needs to send the domain username — the server maps department to homegroups and resolves group membership.

## Decisions Made

- **Language**: Python, compiled to .exe with PyInstaller
- **History collection**: Read Chrome/Edge SQLite History databases directly (copy to temp first since browsers lock the file)
- **AD attributes**: Server-side LDAP lookup — agent sends domain username, server enriches
- **Deployment**: Scheduled task on login → runs .exe from network share
- **Version checking**: Agent checks server for latest version; if newer available, logs and exits (next login runs updated .exe from share)
- **Console integration**: Admin management page (upload .exe, download package with config), NOT a builder (PyInstaller can't cross-compile Linux→Windows in Docker)

---

## Phase 1: Windows Agent (`windows_agent/`)

### File Structure
```
windows_agent/
├── agent.py           # Main entry point / orchestrator
├── config.py          # Config loading + AES-256-CBC decryption
├── browsers.py        # Chrome/Edge SQLite history extraction
├── reporter.py        # HTTP POST to server API
├── state.py           # Last-sent timestamp tracking
├── version.py         # __version__ = "1.0.0"
├── requirements.txt   # requests, pycryptodome, pyinstaller
└── agent.spec         # PyInstaller spec (--onefile --noconsole)
```

### `agent.py` — Main Flow (runs once per invocation, not a daemon)
```
1. Load config from secureconfig.json (same directory as .exe)
2. Decrypt config using AES-256-CBC (replicate backend/utils.py logic)
3. Check version: GET /api/agent/version — if newer, log and exit
4. Get Windows username: os.environ["USERNAME"]
5. Get computer name: os.environ["COMPUTERNAME"]
6. Check time window — if outside monitored hours from config, exit
7. For each enabled browser (Chrome/Edge):
   a. Find History SQLite in user profile
   b. shutil.copy2() to %TEMP% (browser locks the DB)
   c. Query visits since last_sent_timestamp
   d. Convert Chrome WebKit timestamps to epoch milliseconds
8. If visits found, POST to /api/reports/data
9. On success, update last_sent_timestamp in state file
10. Clean up temp files and exit
```

### `config.py` — Config Decryption

Must exactly replicate `backend/utils.py:78-121`:
- **Default master key**: `"BrowserReporter2024!MasterKey"` (or from env `ENCRYPTION_MASTER_KEY`)
- **Key derivation**: `hashlib.sha256(master_key.encode()).digest()` → 32-byte AES-256 key
- **Decryption**: AES-256-CBC, base64-decode `encrypted_data` and `iv`, unpad PKCS7, verify SHA-256 checksum
- **Config file location**: `os.path.dirname(sys.executable)` when frozen (PyInstaller), `os.path.dirname(__file__)` in dev

Existing config fields from client_config page:
```json
{
  "server_url": "http://browserreporter:8000",
  "collection_interval_minutes": 5,
  "max_history_days": 30,
  "monitored_start_time": "08:00",
  "monitored_end_time": "23:59",
  "enable_chrome": true,
  "enable_edge": true,
  "log_max_mb": 10,
  "log_roll_count": 3,
  "exit_password": ""
}
```

### `browsers.py` — Browser History Extraction

**SQLite paths:**
- Chrome: `%LOCALAPPDATA%\Google\Chrome\User Data\Default\History`
- Edge: `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History`
- Also scan for `Profile 1`, `Profile 2`, etc.

**Query:**
```sql
SELECT urls.url, urls.title, visits.visit_time
FROM visits
INNER JOIN urls ON visits.url = urls.id
WHERE visits.visit_time > ?
ORDER BY visits.visit_time ASC
```

**Chrome WebKit timestamp conversion** (microseconds since 1601-01-01):
```python
WEBKIT_EPOCH_DIFF = 11644473600  # seconds between 1601-01-01 and 1970-01-01
epoch_ms = int((chrome_timestamp / 1_000_000 - WEBKIT_EPOCH_DIFF) * 1000)
```

**Locked DB handling**: Copy file to `%TEMP%`, open copy read-only, delete after. Retry once on copy failure.

### `reporter.py` — API Communication

POST to `/api/reports/data` matching existing schema (`backend/schemas.py:8-34`):
```json
{
  "Username": "DOMAIN\\username",
  "UserInfo": {
    "Username": "DOMAIN\\username",
    "DisplayName": null,
    "Email": null
  },
  "Visits": [
    {
      "Url": "https://example.com",
      "Title": "Example",
      "VisitTime": 1713500000000,
      "ComputerName": "PC-LAB-01"
    }
  ]
}
```

Server-side LDAP enrichment handles Department→homegroup mapping automatically via existing `crud.py:upsert_user`.

### `state.py` — Persistence

State file at `%LOCALAPPDATA%\BrowserReporter\state.json`:
```json
{
  "last_sent_chrome": 13350000000000000,
  "last_sent_edge": 13350000000000000
}
```
Timestamps stored in Chrome WebKit format for direct comparison in SQLite queries.

### `version.py`
```python
__version__ = "1.0.0"
```

### Build Command
```bash
pyinstaller --onefile --noconsole --name BrowserReporter agent.py
```
Produces `dist/BrowserReporter.exe`.

---

## Phase 2: Server Endpoints

### `backend/routers/agent.py` — New Router

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/windows-agent` | admin | Management page (HTML) |
| POST | `/api/admin/agent-upload` | admin | Upload pre-built .exe |
| GET | `/api/admin/agent-download` | admin | Download .exe + secureconfig.json as zip |
| GET | `/api/agent/version` | public | Returns `{"version": "1.0.0"}` for agent version check |
| GET | `/api/agent/exe` | public | Download latest .exe (for network share refresh) |

### `backend/agent_manager.py` — Business Logic

Following `backend/extension_builder.py` pattern:
- Store agent metadata in `app_settings` table with key `"agent_config"`
- Config: `{"current_version": "1.0.0", "last_uploaded_at": "...", "exe_filename": "BrowserReporter.exe", "exe_sha256": "..."}`
- `.exe` stored at `backend/static/agent/BrowserReporter.exe`
- Package function: creates zip of .exe + secureconfig.json (generated from current client config)

### `backend/main.py` — Registration

Add to imports and router registration:
```python
from .routers import agent
agent.configure(templates)
app.include_router(agent.router)
```

Add "Windows Agent" link to the sidebar/navbar in relevant templates.

---

## Phase 3: Console UI

### `backend/templates/windows_agent.html`

Admin page with:
1. **Status card**: Current agent version, upload timestamp, SHA-256 checksum
2. **Upload section**: Drag-and-drop or file picker for .exe upload
3. **Download section**: "Download Agent Package" button → zip with .exe + secureconfig.json
4. **Link to client config**: "Configure Agent Settings" button → `/client-config` page
5. **Deployment instructions**: Collapsible section with GPO scheduled task setup steps
6. **Version endpoint display**: Shows the URL clients check for updates

---

## Phase 4: Build & Test

1. Build .exe on a Windows machine: `cd windows_agent && pip install -r requirements.txt && pyinstaller agent.spec`
2. Upload to console via `/windows-agent` page
3. Download package, place on network share
4. Create test GPO scheduled task pointing to share
5. Login on a domain-joined Windows PC, verify:
   - Agent runs and exits cleanly
   - Browsing data appears in console
   - Username is enriched via server LDAP (department → homegroup)
   - Logs written to `%LOCALAPPDATA%\BrowserReporter\agent.log`
   - Version check works against `/api/agent/version`

---

## Key Files to Reference During Implementation

| File | Why |
|------|-----|
| `backend/utils.py:35-121` | AES encryption/decryption the agent must replicate exactly |
| `backend/schemas.py:8-34` | ReportIn/VisitIn/UserInfoIn — the API payload format |
| `backend/crud.py` | `upsert_user` handles LDAP enrichment server-side |
| `backend/extension_builder.py` | Pattern for agent_manager.py (config in app_settings, version mgmt) |
| `backend/routers/extension.py` | Pattern for agent router (upload, download, config endpoints) |
| `backend/templates/extension.html` | Pattern for windows_agent.html UI |
| `backend/templates/client_config.html` | Existing config page — agent management page links here |
| `backend/routers/admin.py:304-318` | Existing `/api/admin/secureconfig` endpoint for config generation |

## Implementation Order

1. `windows_agent/config.py` + `version.py`
2. `windows_agent/state.py`
3. `windows_agent/browsers.py`
4. `windows_agent/reporter.py`
5. `windows_agent/agent.py` (main orchestrator)
6. `windows_agent/requirements.txt` + `agent.spec`
7. `backend/agent_manager.py`
8. `backend/routers/agent.py`
9. `backend/main.py` updates (register router)
10. `backend/templates/windows_agent.html`
11. Navbar updates to add Windows Agent link
12. Test end-to-end
