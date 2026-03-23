/**
 * BrowserReporter - Background Service Worker
 * Collects page visits and batches them to the BrowserReporter server.
 */

const SERVER_URL = 'http://browserreporter:8000';
const REPORT_ENDPOINT = `${SERVER_URL}/api/reports/data`;

// Send any queued visits every 1 minute
const ALARM_NAME = 'browserreporter_send';
const ALARM_INTERVAL_MINUTES = 1;

// Also flush immediately if the queue reaches this size
const QUEUE_FLUSH_THRESHOLD = 50;

/**
 * Deduplication window in milliseconds.
 * If the same normalised URL is seen again within this window (per tab),
 * the duplicate is silently dropped. This prevents multiple onUpdated
 * firings for hash-fragment changes (e.g. Google's #sv=... parameter).
 */
const DEDUP_WINDOW_MS = 30_000; // 30 seconds

// In-memory dedup cache: Map<tabId, { url: string, ts: number }>
// Lives only for the lifetime of the service worker activation, which is fine -
// chrome.storage is used for the send queue; this is just a noise filter.
const lastSeenByTab = new Map();

// ─── Initialisation ──────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(initialise);
chrome.runtime.onStartup.addListener(initialise);

function initialise() {
  // Always clear and recreate the alarm so interval changes take effect immediately
  chrome.alarms.clear(ALARM_NAME, () => {
    chrome.alarms.create(ALARM_NAME, {
      periodInMinutes: ALARM_INTERVAL_MINUTES,
    });
  });

  // Resolve and cache identity immediately rather than waiting for the first alarm
  getUserInfo();
}

// ─── Tab tracking ─────────────────────────────────────────────────────────────

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Only record once the page has fully loaded and we have a URL
  if (changeInfo.status !== 'complete' || !tab.url) return;

  // Normalise the URL before any dedup check or recording.
  // Stripping the hash removes noise like Google's #sv=... fragment which
  // causes onUpdated to fire repeatedly for the same logical page.
  const normalisedUrl = normaliseUrl(tab.url);

  // Deduplication: skip if we already recorded this URL on this tab recently
  const now = Date.now();
  const last = lastSeenByTab.get(tabId);
  if (last && last.url === normalisedUrl && now - last.ts < DEDUP_WINDOW_MS) {
    return; // duplicate firing - drop silently
  }
  lastSeenByTab.set(tabId, { url: normalisedUrl, ts: now });

  recordVisit(normalisedUrl, tab.title || normalisedUrl);
});

/**
 * Normalises a URL for deduplication and storage:
 * - Strips the hash fragment (e.g. #sv=... on Google result pages)
 * - Preserves the full path and query string so distinct pages are not merged
 */
function normaliseUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    u.hash = '';
    return u.toString();
  } catch {
    return rawUrl; // not a valid URL - store as-is
  }
}

async function recordVisit(url, title) {
  const computerName = await getDeviceName();

  const visit = {
    Url: url,
    Title: title,
    VisitTime: Date.now(), // epoch milliseconds, as the server expects
    ComputerName: computerName,
  };

  // Append to the persisted queue
  const { visitQueue = [] } = await chrome.storage.local.get('visitQueue');
  visitQueue.push(visit);
  await chrome.storage.local.set({ visitQueue });

  // Flush early if we've accumulated a large batch
  if (visitQueue.length >= QUEUE_FLUSH_THRESHOLD) {
    await sendVisits();
  }
}

// ─── Periodic send ────────────────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    sendVisits();
  }
});

// ─── Sending logic ────────────────────────────────────────────────────────────

async function sendVisits() {
  const { visitQueue = [] } = await chrome.storage.local.get('visitQueue');
  if (visitQueue.length === 0) return;

  const { email, displayName } = await getUserInfo();
  const deviceName = await getDeviceName();

  const payload = {
    Username: email,
    UserInfo: {
      Username: email,
      DisplayName: displayName,
      Email: email,
    },
    Visits: visitQueue,
  };

  try {
    const response = await fetch(REPORT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (response.ok) {
      // Clear the queue only on a confirmed successful delivery
      await chrome.storage.local.set({ visitQueue: [] });
      console.log(
        `[BrowserReporter] Sent ${visitQueue.length} visits for ${email} from ${deviceName}.`
      );
    } else {
      const errorText = await response.text();
      console.warn(`[BrowserReporter] Server returned ${response.status}: ${errorText}`);
    }
  } catch (err) {
    // Network error - keep the queue and retry on the next alarm cycle
    console.warn('[BrowserReporter] Could not reach server, will retry:', err.message);
  }
}

// ─── Identity helpers ─────────────────────────────────────────────────────────

/**
 * Returns { email, displayName, source } for the signed-in user.
 * Uses chrome.identity.getProfileUserInfo() which works reliably on managed
 * Chromebooks for force-installed extensions when the identity.email
 * permission is declared in the manifest.
 */
async function getUserInfo() {
  const result = await new Promise((resolve) => {
    chrome.identity.getProfileUserInfo({ accountStatus: 'ANY' }, (userInfo) => {
      if (chrome.runtime.lastError || !userInfo.email) {
        console.warn('[BrowserReporter] getProfileUserInfo failed:',
          chrome.runtime.lastError?.message || 'empty email');
        resolve({
          email: 'unknown@schools.vic.edu.au',
          displayName: 'unknown',
          source: 'fallback',
        });
        return;
      }
      resolve({
        email: userInfo.email,
        displayName: userInfo.email.split('@')[0],
        source: 'chrome_identity',
      });
    });
  });

  await chrome.storage.local.set({ resolvedIdentity: result });
  console.log(`[BrowserReporter] Identity: ${result.source} → ${result.email}`);
  return result;
}

/**
 * Returns the device hostname via the Chrome Enterprise Device Attributes API.
 * Falls back to a generic label if the API is unavailable (e.g. on unmanaged devices).
 */
function getDeviceName() {
  return new Promise((resolve) => {
    // Cache the device name for the session to avoid repeated API calls
    chrome.storage.local.get('cachedDeviceName', ({ cachedDeviceName }) => {
      if (cachedDeviceName) {
        resolve(cachedDeviceName);
        return;
      }

      if (chrome.enterprise && chrome.enterprise.deviceAttributes) {
        chrome.enterprise.deviceAttributes.getDeviceHostname((hostname) => {
          if (chrome.runtime.lastError || !hostname) {
            // Hostname may be empty on some configurations - try serial number
            chrome.enterprise.deviceAttributes.getDeviceSerialNumber((serial) => {
              const name = serial || 'chromebook';
              chrome.storage.local.set({ cachedDeviceName: name });
              resolve(name);
            });
          } else {
            chrome.storage.local.set({ cachedDeviceName: hostname });
            resolve(hostname);
          }
        });
      } else {
        // Not a managed device or API not available
        resolve('chromebook');
      }
    });
  });
}
