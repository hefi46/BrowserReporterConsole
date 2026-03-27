# BrowserReporter Extension - Deployment Guide

This guide covers packaging the extension and deploying it to managed Chromebooks via the Google Admin Console.

---

## 1. Package the Extension

Chrome requires a `.crx` file (signed package) for self-hosted deployment. The easiest way is to use Chrome itself.

1. Open Chrome and go to `chrome://extensions`
2. Enable **Developer mode** (toggle, top-right)
3. Click **Pack extension**
4. Under "Extension root directory", browse to the `extension/` folder
5. Leave "Private key file" empty on first pack - Chrome will generate a `extension.pem` key file
6. Click **Pack Extension**

Chrome will produce two files alongside the `extension/` folder:
- `extension.crx` - the installable package
- `extension.pem` - your private key (keep this safe - you need it to publish updates)

> **For updates:** Pack again using the same `.pem` file so the extension ID stays the same.

---

## 2. Host the Extension on Your Web Server

The `.crx` file and an `update.xml` manifest must be accessible via HTTP from your network.

### 2a. Create the update manifest

Create a file named `update.xml` with the following content. Replace `EXT_ID` with the actual extension ID shown on `chrome://extensions` after packing, and update the version and URL as needed.

```xml
<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='EXT_ID'>
    <updatecheck codebase='http://browserreporter:8000/extension/extension.crx' version='1.0.0' />
  </app>
</gupdate>
```

### 2b. Serve the files

Place both files in a location accessible to all Chromebooks on your network. A simple option is to serve them directly from the BrowserReporter server by copying them into the backend's static folder:

```
BrowserReporterConsole/backend/static/extension/
  extension.crx
  update.xml
```

They will then be available at:
- `http://browserreporter:8000/static/extension/extension.crx`
- `http://browserreporter:8000/static/extension/update.xml`

Update the `codebase` URL in `update.xml` to match.

---

## 3. Force-Install via Google Admin Console

1. Go to [admin.google.com](https://admin.google.com)
2. Navigate to **Devices > Chrome > Apps & extensions**
3. Select the relevant **Organisational Unit** (OU) that contains your student Chromebooks
4. Click the **+** button and choose **Add Chrome app or extension by ID**
5. Paste the extension ID (from step 1)
6. Set the **Installation policy** to **Force install**
7. In the **Update URL** field, enter the URL to your `update.xml` file:
   ```
   http://browserreporter:8000/static/extension/update.xml
   ```
8. **Important - set the managed policy** (see step 3a below)
9. Click **Save**

Chrome will push the extension to all Chromebooks in that OU within 24 hours, or immediately on next sign-in.

### 3a. Set the Managed Storage Policy (required for user identity)

This step is what allows the extension to know who is logged in. Google Admin Console injects the signed-in user's email and name automatically using policy variables.

In the same Apps & extensions entry for BrowserReporter, find the **Policy for extensions** field (sometimes shown as a JSON editor or text box) and paste the following:

```json
{
  "username": "${USER_EMAIL}",
  "display_name": "${USER_FULL_NAME}"
}
```

Google will substitute `${USER_EMAIL}` with the actual signed-in user's email (e.g. `SLAIX@schools.vic.edu.au`) and `${USER_FULL_NAME}` with their display name when the policy is delivered to each device. The extension reads these values to identify the user in reports.

> **Note:** If the Policy for extensions field is not visible, look for a pencil/edit icon next to the extension entry, or check under the extension's settings panel - it may be labelled "Policy JSON".

---

## 4. Verify Deployment

On a managed Chromebook:
1. Sign in with a `@schools.vic.edu.au` account
2. Open `chrome://extensions` - the BrowserReporter extension should appear as **Installed by your administrator** with no option to remove it
3. Browse a few pages, then check the BrowserReporter dashboard to confirm visits are appearing

---

## 5. Publishing Updates

1. Make changes to the extension source files
2. **Re-pack** using the original `extension.pem` key (same extension ID is preserved)
3. Replace `extension.crx` on the server
4. Update the `version` in `update.xml` (e.g. `1.0.1`)
5. Chrome will automatically update the extension on all managed devices

---

## Configuration Notes

The server URL is currently hardcoded as `http://browserreporter:8000` in `background.js`.

If you need to change the server URL:
- Edit `background.js` - update the `SERVER_URL` constant at the top
- Repack and redeploy the `.crx`

Alternatively, for more flexible deployments, the URL could be moved to Chrome managed storage policy - ask your IT team if this is needed.
