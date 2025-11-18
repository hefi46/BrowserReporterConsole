# Windows Browser Reporter Client - Comprehensive Requirements

## Project Overview

Create a Windows 10/11 24H2 compatible browser monitoring client that runs silently in the background (no system tray UI) to collect Chrome and Edge browsing history and report it to a centralized FastAPI server. The application must run at user login via domain policy deployment, operate with user-level privileges, and use encrypted configuration with Active Directory integration for authorization.

---

## 1. Technical Stack & Deployment

### Technology Requirements
- **Language**: C# with .NET 8
- **Deployment**: Self-contained single executable with embedded runtime
- **Privilege Level**: User-level (no admin rights required)
- **Target OS**: Windows 10/11 version 24H2
- **Domain Environment**: Must work in Active Directory domain environments

### Build Requirements
- Single-file self-contained EXE (no external dependencies)
- Embed .NET 8 runtime for zero-dependency deployment
- Target: `win-x64` platform
- Executable size optimization acceptable (100-150MB is fine for single-file deployment)

### Deployment Method
- Deploy via Group Policy startup script or logon script
- Copy executable to a shared network location or local machine
- Execute at user login silently in the background
- No installer required (simple executable copy)

---

## 2. Server API Integration

### Base Server Details
- **Server URL**: Configurable via encrypted config file
- **Default**: `http://localhost:8000` (development)
- **Production**: User-specified server address in domain environment

### API Endpoints

#### 2.1 Configuration Download
- **Endpoint**: `GET /secureconfig.json`
- **Authentication**: None (public endpoint)
- **Purpose**: Download encrypted configuration on startup
- **Response Format**:
```json
{
  "version": "1.0",
  "encrypted_data": "base64_encoded_encrypted_json",
  "iv": "base64_encoded_initialization_vector",
  "checksum": "sha256_hex_of_plaintext",
  "created_at": 1234567890
}
```

#### 2.2 Data Ingestion
- **Endpoint**: `POST /api/reports/data`
- **Authentication**: None required
- **Content-Type**: `application/json`
- **Request Body Schema**:
```json
{
  "Username": "string (AD username)",
  "UserInfo": {
    "Username": "string (same as above)",
    "DisplayName": "string|null (full name)",
    "FirstName": "string|null",
    "LastName": "string|null",
    "Department": "string|null (maps to homegroup in server)",
    "Email": "string|null"
  },
  "Visits": [
    {
      "Url": "string (full URL)",
      "Title": "string (page title)",
      "VisitTime": 1234567890123,  // epoch milliseconds
      "ComputerName": "string (hostname)"
    }
  ]
}
```

**Important Notes**:
- `VisitTime` must be in **epoch milliseconds** (JavaScript Date.now() format)
- `Department` field in `UserInfo` is mapped to `homegroup` in the server database
- Server requires at least one visit in the `Visits` array (will reject empty arrays)

#### 2.3 Expected Server Response
- **Success**: `{"success": true}` with HTTP 200
- **Error**: HTTP 4xx/5xx with error details

---

## 3. Configuration System

### 3.1 Encryption Specification

The client must decrypt AES-256-CBC encrypted configuration using:

**Master Key**: `BrowserReporter2024!MasterKey` (hard-coded in client)
**Derivation**: SHA-256 hash of master key = 32-byte AES key
**Mode**: AES-256-CBC with PKCS7 padding
**IV**: 16 bytes, provided in config JSON

**Decryption Process**:
1. Download encrypted config from server
2. Base64 decode `encrypted_data` and `iv`
3. Derive AES key: `SHA256(master_key_bytes)`
4. Decrypt using AES-256-CBC with decoded IV
5. Remove PKCS7 padding
6. Verify SHA-256 checksum matches `checksum` field
7. Parse JSON to get plain configuration

### 3.2 Configuration Schema

The decrypted JSON contains:

```json
{
  "server_url": "http://server.domain.local:8000",
  "sync_interval_minutes": 5,
  "max_history_age_hours": 24,
  "monitored_users_group": "CN=BrowserMonitoring,OU=Groups,DC=domain,DC=local",
  "monitored_users": ["user1", "user2"],  // Empty array = all users in group
  "monitored_hours": {
    "start": "00:00",  // HH:MM format
    "end": "23:59"
  },
  "browsers": ["chrome", "edge"],
  "log_max_mb": 5,
  "log_roll_count": 3
}
```

**Field Descriptions**:
- `server_url`: Base URL for API endpoints
- `sync_interval_minutes`: How often to sync browsing data (run continuous loop)
- `max_history_age_hours`: Only collect history from last N hours
- `monitored_users_group`: AD security group DN for authorization check
- `monitored_users`: Whitelist of usernames (empty = allow all group members)
- `monitored_hours`: Only collect data during this time window (24-hour format)
- `browsers`: Array of browsers to monitor (`chrome`, `edge`, or both)
- `log_max_mb`: Maximum log file size before rotation
- `log_roll_count`: Number of rotated log files to keep

### 3.3 Configuration Fallback

If `/secureconfig.json` download fails:
- Log error to local file
- Exit application silently (no retry on same login session)
- Will retry on next login when user logs in again

---

## 4. Active Directory Integration

### 4.1 Required User Information

Retrieve the following from Active Directory for current user:

- **Username**: `sAMAccountName` (e.g., "jsmith")
- **DisplayName**: `displayName` or `cn` (e.g., "John Smith")
- **FirstName**: `givenName`
- **LastName**: `sn` (surname)
- **Email**: `mail`
- **Department**: `department` field (maps to homegroup on server)
- **Security Groups**: All group memberships for authorization check

### 4.2 LDAP Query Implementation

**Method 1 (Recommended)**: Use `System.DirectoryServices.AccountManagement`
```csharp
using System.DirectoryServices.AccountManagement;

// Get current user principal
using (var context = new PrincipalContext(ContextType.Domain))
using (var userPrincipal = UserPrincipal.FindByIdentity(context, IdentityType.SamAccountName, Environment.UserName))
{
    // Access properties: DisplayName, GivenName, Surname, EmailAddress
    // Get group memberships via GetAuthorizationGroups()
}
```

**Method 2**: Use `System.DirectoryServices` for direct LDAP queries
```csharp
using System.DirectoryServices;

// Query for department and other attributes not in AccountManagement
```

### 4.3 Authorization Logic

**Process**:
1. Retrieve all security groups for current user (including nested groups)
2. Check if user is member of `monitored_users_group` (compare DNs)
3. If `monitored_users` array is not empty, verify username is in the list
4. Check if current time is within `monitored_hours` window

**Authorization Outcomes**:
- ✅ **Authorized**: Continue with browser scanning
- ❌ **Not Authorized**: Exit silently (no logging, no reporting to server)

**Edge Cases**:
- If AD query fails: Exit silently
- If `monitored_users_group` is empty string: Skip group check (allow all users)
- If time is outside monitored hours: Sleep until next sync interval, then re-check

---

## 5. Browser History Collection

### 5.1 Supported Browsers

**Google Chrome**:
- History Location: `%LOCALAPPDATA%\Google\Chrome\User Data\Default\History`
- Database: SQLite3
- Table: `urls` joined with `visits`

**Microsoft Edge (Chromium)**:
- History Location: `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History`
- Database: SQLite3 (same schema as Chrome)
- Table: `urls` joined with `visits`

### 5.2 SQLite Database Schema

**Relevant Tables**:
```sql
-- urls table
CREATE TABLE urls (
  id INTEGER PRIMARY KEY,
  url TEXT,
  title TEXT,
  visit_count INTEGER,
  typed_count INTEGER,
  last_visit_time INTEGER,  -- WebKit/Chrome timestamp
  hidden INTEGER
);

-- visits table
CREATE TABLE visits (
  id INTEGER PRIMARY KEY,
  url INTEGER,  -- Foreign key to urls.id
  visit_time INTEGER,  -- WebKit/Chrome timestamp
  from_visit INTEGER,
  transition INTEGER
);
```

**Query to Execute**:
```sql
SELECT
  urls.url,
  urls.title,
  visits.visit_time
FROM visits
INNER JOIN urls ON visits.url = urls.id
WHERE visits.visit_time >= ?  -- Cutoff timestamp
ORDER BY visits.visit_time DESC;
```

**Timestamp Conversion**:
- Chrome/Edge stores timestamps in **WebKit format**: Microseconds since January 1, 1601 UTC
- Convert to Unix epoch milliseconds for server:
  ```
  unix_epoch_ms = (webkit_timestamp / 1000) - 11644473600000
  ```
  Where `11644473600000` = milliseconds between 1601 and 1970

### 5.3 Database Access Challenges

**Problem**: Chrome/Edge locks the History database while browser is running

**Solution**: Copy database to temporary location before reading

**Implementation**:
1. Create temp directory: `Path.GetTempPath() + Guid.NewGuid()`
2. Copy `History` file to temp location (use `File.Copy` with retry logic)
3. Open and query the copied database
4. Delete temp database after reading
5. If copy fails (locked), skip this browser for current sync cycle

**Retry Logic**:
```csharp
int maxRetries = 3;
for (int i = 0; i < maxRetries; i++)
{
    try
    {
        File.Copy(sourcePath, tempPath, overwrite: true);
        break;
    }
    catch (IOException)
    {
        if (i == maxRetries - 1)
        {
            // Log and skip this browser
            return new List<Visit>();
        }
        Thread.Sleep(500);  // Wait 500ms before retry
    }
}
```

### 5.4 Deduplication Strategy

**Goal**: Prevent reporting the same visits multiple times

**Implementation**: Local SQLite cache database

**Cache Database Schema**:
```sql
CREATE TABLE reported_visits (
  url_hash TEXT PRIMARY KEY,
  visit_time INTEGER,
  reported_at INTEGER
);

-- Index for cleanup queries
CREATE INDEX idx_reported_at ON reported_visits(reported_at);
```

**Hash Calculation**:
```csharp
string urlHash = SHA256(url + "|" + visit_time_ms);
```

**Deduplication Process**:
1. Before reporting, check if `urlHash` exists in cache
2. If exists, skip this visit
3. After successful server report, insert `urlHash` into cache
4. Periodically clean cache (keep last 7 days of records)

**Cache Location**: `%LOCALAPPDATA%\BrowserReporter\cache.db`

---

## 6. Application Lifecycle

### 6.1 Startup Sequence

1. **Initialize Logging**
   - Create log directory: `%LOCALAPPDATA%\BrowserReporter\logs\`
   - Initialize rolling file logger with configured size/count limits

2. **Download Configuration**
   - Fetch `GET /secureconfig.json` from server
   - Decrypt and validate configuration
   - If fails: Log error and exit

3. **Retrieve AD User Information**
   - Query Active Directory for current user details
   - If fails: Log error and exit

4. **Authorization Check**
   - Verify user is in required security group
   - Check username against whitelist (if configured)
   - If unauthorized: Exit silently (no logging)

5. **Time Window Check**
   - Verify current time is within `monitored_hours`
   - If outside window: Sleep until next sync interval

6. **Enter Main Loop**
   - Execute sync process
   - Sleep for `sync_interval_minutes`
   - Repeat

### 6.2 Main Sync Loop

**Per Sync Cycle**:
1. Check time window (exit sync if outside hours)
2. For each enabled browser:
   - Calculate cutoff time (`now - max_history_age_hours`)
   - Copy browser history database to temp location
   - Query visits since cutoff time
   - Filter out already-reported visits (check cache)
3. Combine visits from all browsers
4. If visits list is not empty:
   - Construct JSON payload with UserInfo and Visits
   - POST to `/api/reports/data`
   - On success: Add visit hashes to local cache
   - On failure: Log error (don't cache)
5. Sleep for `sync_interval_minutes` minutes
6. Repeat loop

### 6.3 Error Handling

**Network Errors**:
- Log to local file
- Do not cache failed submissions
- Continue operation (retry on next sync cycle)

**Browser Database Errors**:
- Log error
- Skip that browser for current cycle
- Continue with other browsers

**AD Query Errors**:
- Log error
- Exit application (will retry on next login)

**Fatal Errors**:
- Configuration decryption failure → Exit
- AD user not authorized → Exit silently
- Cannot create log directory → Exit

### 6.4 Graceful Shutdown

The application should run indefinitely until:
- User logs out (process killed by Windows)
- System shutdown (process killed)
- Fatal error occurs

**No manual shutdown mechanism required** (no UI, runs silently)

---

## 7. Logging Requirements

### 7.1 Log Configuration

- **Location**: `%LOCALAPPDATA%\BrowserReporter\logs\BrowserReporter.log`
- **Format**: Rolling file logger
- **Max Size**: From config `log_max_mb` (default: 5 MB)
- **Rotation Count**: From config `log_roll_count` (default: 3 files)
- **Rotated Names**: `BrowserReporter.1.log`, `BrowserReporter.2.log`, etc.

### 7.2 Log Levels & Content

**INFO Level**:
- Application start/stop
- Successful configuration download
- Successful sync to server (with visit count)
- Authorization success

**WARN Level**:
- Browser database locked (will retry next cycle)
- Outside monitored hours (skipping sync)
- Network timeouts (will retry next cycle)

**ERROR Level**:
- Configuration download failure
- Configuration decryption failure
- AD query failure
- Server API error responses
- Database corruption

**DEBUG Level** (optional, for troubleshooting):
- Detailed AD attributes retrieved
- Number of visits found per browser
- Deduplication statistics

### 7.3 Log Entry Format

```
2024-01-15 14:30:45.123 [INFO] Application started (Version 1.0.0)
2024-01-15 14:30:46.456 [INFO] Configuration downloaded and decrypted successfully
2024-01-15 14:30:47.789 [INFO] User authorized: jsmith (Department: IT)
2024-01-15 14:30:50.123 [INFO] Sync completed: 47 visits reported (Chrome: 32, Edge: 15)
2024-01-15 14:35:51.456 [WARN] Chrome database locked, skipping this cycle
2024-01-15 14:40:52.789 [ERROR] Server API error: HTTP 500 Internal Server Error
```

### 7.4 Sensitive Data Handling

**DO NOT LOG**:
- Full URLs visited by user
- Page titles
- Passwords or credentials
- Raw configuration data (especially if API keys added later)

**DO LOG**:
- Visit counts
- Success/failure status
- Error messages
- User department/username (for troubleshooting)

---

## 8. Security Considerations

### 8.1 Data in Transit

- **Default**: HTTP (unencrypted) for LAN deployment
- **Production Recommendation**: HTTPS with SSL certificate
- Client should support both HTTP and HTTPS based on `server_url` config

### 8.2 Local Data Protection

- **Cache Database**: Stored in user's `%LOCALAPPDATA%` (accessible only to user)
- **Logs**: Stored in user's `%LOCALAPPDATA%` (accessible only to user)
- **No Credential Storage**: Application does not store passwords or authentication tokens

### 8.3 Encryption Key Security

- **Master Key**: Hard-coded in executable (same as old version)
- **Note**: This provides obfuscation, not strong security
- **Threat Model**: Prevents casual config viewing, not determined attackers
- **Acceptable**: For internal domain deployment with physical security

### 8.4 Process Isolation

- Runs under user context (no privilege escalation)
- Cannot access other users' data on shared machines
- Windows user profile isolation protects data

---

## 9. Performance & Resource Usage

### 9.1 Performance Targets

- **Startup Time**: < 5 seconds from launch to first sync
- **Sync Duration**: < 10 seconds for typical workload (1000 visits)
- **Memory Usage**: < 100 MB during operation
- **CPU Usage**: < 5% average, < 30% during sync operations
- **Disk I/O**: Minimize file operations (cache database writes)

### 9.2 Optimization Strategies

**Database Queries**:
- Use indexed queries on `visit_time` column
- Limit result set size (already filtered by time window)
- Use prepared statements for cache lookups

**Network Operations**:
- Set HTTP timeout: 30 seconds
- Use connection pooling (HttpClient singleton)
- Compress JSON payloads if large (>100KB)

**Threading**:
- Single-threaded main loop (no concurrency needed)
- Avoid blocking calls during sleep intervals
- Use async/await for network operations

---

## 10. Testing Requirements

### 10.1 Unit Tests

Test coverage for:
- Configuration decryption with known test vectors
- Timestamp conversion (WebKit → Unix epoch)
- URL hash calculation for deduplication
- Time window validation logic
- AD group membership checking

### 10.2 Integration Tests

- End-to-end sync with test server
- Chrome history database reading
- Edge history database reading
- Cache database operations
- Rolling log file rotation

### 10.3 Manual Testing Scenarios

1. **Fresh Installation**: First run on clean machine
2. **Authorization Failure**: User not in security group
3. **Outside Hours**: Sync during non-monitored hours
4. **Browser Locked**: Database file in use
5. **Network Failure**: Server unreachable
6. **Large History**: 10,000+ visits in browser
7. **Multiple Browsers**: Both Chrome and Edge installed
8. **Single Browser**: Only Chrome or only Edge

---

## 11. Code Structure Recommendations

### 11.1 Project Organization

```
BrowserReporterClient/
├── Program.cs                    // Entry point
├── Configuration/
│   ├── ConfigManager.cs          // Download & decrypt config
│   ├── ConfigModel.cs            // Configuration POCO
│   └── Encryption.cs             // AES decryption utilities
├── ActiveDirectory/
│   ├── AdUserService.cs          // LDAP queries
│   └── AuthorizationService.cs   // Group membership checks
├── BrowserHistory/
│   ├── BrowserScanner.cs         // Main orchestrator
│   ├── ChromeScanner.cs          // Chrome-specific logic
│   ├── EdgeScanner.cs            // Edge-specific logic
│   └── VisitModel.cs             // Visit data model
├── Cache/
│   ├── DeduplicationCache.cs     // SQLite cache operations
│   └── CacheModel.cs             // Cache data model
├── Sync/
│   ├── ServerClient.cs           // HTTP API client
│   ├── SyncService.cs            // Main sync loop
│   └── ReportModel.cs            // API request/response models
└── Utilities/
    ├── Logger.cs                 // Logging implementation
    └── TimestampConverter.cs     // WebKit → Unix conversion
```

### 11.2 Dependency Packages

**NuGet Packages Needed**:
- `Microsoft.Data.Sqlite` - SQLite database access
- `System.DirectoryServices.AccountManagement` - AD integration
- `System.DirectoryServices` - LDAP queries
- `Newtonsoft.Json` or `System.Text.Json` - JSON serialization
- `Serilog` or `NLog` - Structured logging with rolling files

### 11.3 Key Interfaces

```csharp
public interface IConfigManager
{
    Task<ConfigModel> DownloadAndDecryptAsync(string serverUrl);
}

public interface IAdUserService
{
    UserInfo GetCurrentUserInfo();
    bool IsUserInGroup(string groupDn);
}

public interface IBrowserScanner
{
    List<Visit> GetRecentVisits(DateTime cutoff);
}

public interface IDeduplicationCache
{
    bool IsReported(string urlHash);
    void MarkAsReported(string urlHash, long visitTime);
    void Cleanup(DateTime cutoff);
}

public interface IServerClient
{
    Task<bool> SubmitReportAsync(ReportModel report);
}
```

---

## 12. Deployment Instructions (For IT Team)

### 12.1 Build Instructions

```bash
dotnet publish -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true /p:IncludeNativeLibrariesForSelfExtract=true
```

Output: Single EXE file in `bin/Release/net8.0/win-x64/publish/`

### 12.2 Domain Deployment Steps

**Option 1: Group Policy Logon Script**
1. Copy `BrowserReporterClient.exe` to network share (e.g., `\\domain\NETLOGON\BrowserReporter\`)
2. Create GPO: User Configuration → Windows Settings → Scripts → Logon
3. Add script: `\\domain\NETLOGON\BrowserReporter\BrowserReporterClient.exe`
4. Link GPO to target OUs

**Option 2: Startup Task (Task Scheduler)**
1. Copy executable to local path via GPO file deployment
2. Create scheduled task via GPO
3. Trigger: At log on of any user
4. Action: Start program `C:\Program Files\BrowserReporter\BrowserReporterClient.exe`
5. Run whether user is logged on or not: No (must run as logged-in user)

### 12.3 Server Configuration

1. Admin logs into web dashboard at `http://server:8000`
2. Navigate to Client Config page
3. Configure settings:
   - Server URL: `http://server.domain.local:8000`
   - Security Group: Browse AD and select monitoring group
   - Sync interval, hours, browsers, etc.
4. Click "Generate Config" - server creates `/secureconfig.json`
5. Clients will automatically download on next login/sync

### 12.4 Verification

**Check client is running**:
```powershell
Get-Process BrowserReporterClient
```

**Check logs**:
```powershell
Get-Content "$env:LOCALAPPDATA\BrowserReporter\logs\BrowserReporter.log" -Tail 50
```

**Check database for submissions**:
- Log into web dashboard
- View user activity reports
- Verify recent visits appearing

---

## 13. Differences from Old BrowserReporterService

### What to REMOVE:
- ❌ System tray icon and UI
- ❌ Context menu (manual sync, view logs, exit)
- ❌ User permission prompts
- ❌ MSI installer with WiX
- ❌ "Exit password" protection
- ❌ Status indicators (green/yellow/red icons)
- ❌ Manual sync button
- ❌ Log viewer window

### What to KEEP:
- ✅ AES-256-CBC configuration encryption (same master key)
- ✅ LDAP/AD integration for user info
- ✅ Security group authorization
- ✅ Chrome/Edge history scanning
- ✅ SQLite deduplication cache
- ✅ Rolling file logging
- ✅ Time window monitoring
- ✅ Configurable sync intervals

### What to CHANGE:
- 🔄 Run silently (no UI) instead of system tray
- 🔄 Deploy as simple EXE copy instead of MSI installer
- 🔄 No user interaction required (fully automated)
- 🔄 Exit on authorization failure (no warning/retry UI)

---

## 14. Error Codes & Exit Conditions

Use standard exit codes for troubleshooting:

- `0` - Normal operation (should never exit in normal operation)
- `1` - Configuration download failed
- `2` - Configuration decryption failed
- `3` - AD user information retrieval failed
- `4` - User not authorized (silent exit)
- `5` - Cannot create log directory
- `10` - Unhandled exception

Log all exits (except code 4 - unauthorized) before terminating.

---

## 15. Success Criteria

The application is considered successful when:

1. ✅ Runs silently without user interaction or visible windows
2. ✅ Successfully downloads and decrypts server configuration
3. ✅ Retrieves accurate AD user information including department
4. ✅ Correctly authorizes users based on security group membership
5. ✅ Collects Chrome and Edge browsing history without errors
6. ✅ Prevents duplicate submissions via local cache
7. ✅ Successfully submits data to server API
8. ✅ Respects time windows and sync intervals
9. ✅ Maintains rolling log files within size limits
10. ✅ Operates continuously from login until logout
11. ✅ Handles browser database locks gracefully
12. ✅ Works in domain environment with minimal resource usage

---

## 16. Additional Notes

### Compatibility with Server

The server expects:
- `Department` field to populate `homegroup` (important for filtering in dashboard)
- Timestamps in **epoch milliseconds** (not seconds, not WebKit format)
- At least one visit in array (validation will reject empty)
- All fields in UserInfo (nulls are acceptable)

### Browser Profile Support

Currently targets only the `Default` profile:
- Chrome: `User Data\Default\History`
- Edge: `User Data\Default\History`

If multiple profiles needed in future:
- Scan `User Data\Profile *\History` directories
- Combine visits from all profiles
- Track profile names in cache to avoid duplicates

### Performance Considerations

- Large history databases (100k+ entries): Query takes 2-3 seconds
- Network latency: Typically <100ms on LAN
- Cache lookups: <1ms per visit with proper indexing
- Bottleneck is usually browser database copy operation (500ms - 2s)

### Future Enhancements (Out of Scope)

- Firefox support (different database schema)
- macOS/Linux support
- Real-time monitoring (file watchers)
- Central management console for client status
- Encrypted HTTPS communication
- API key authentication
- Differential sync (only changed records)

---

## 17. Reference Links

- **Server Repository**: `/home/hefi/BrowserReporterConsole/`
- **Old Client Reference**: https://github.com/hefi46/BrowserReporterService
- **Server API Documentation**: See `backend/main.py` lines 136-141
- **Encryption Implementation**: See `backend/utils.py` lines 46-74
- **Database Models**: See `backend/models.py` and `backend/schemas.py`

---

**END OF REQUIREMENTS DOCUMENT**

This document provides complete specifications for building the Windows browser monitoring client. All questions should be asked before beginning implementation to ensure alignment with requirements.