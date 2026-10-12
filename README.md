# Square Drive Connector

Small Windows/Python receiver for a manually run iPhone Shortcut which uploads Square Golf CSV exports over a trusted home LAN. It archives originals, parses them into SQLite, and publishes analysis-ready CSV/JSON files to an existing Google Drive for desktop folder. It has no Square API, Bluetooth capture, Google API, OAuth, dashboard, or cloud-status detection.

## Security and boundaries

The token is bearer authentication, **not encryption**. Use this only on a trusted private LAN. Do not port-forward the receiver or expose it publicly. If you have an appropriately trusted HTTPS reverse proxy already, use that; this app does not weaken TLS validation or create certificates. Configure a DHCP reservation/stable LAN address for the mini-PC, then use that address in the Shortcut—never `localhost`.

`LOCAL_DATA_DIR` holds `square.sqlite3`, raw originals, temporary uploads, and logs; keep it outside all sync folders. `DRIVE_OUTPUT_DIR` must be an already-existing folder under your real Google Drive for desktop **My Drive** location (usually create `Golf Performance\Square` there yourself). The program deliberately will not manufacture a lookalike Drive location.

## Install on Windows

From this repository in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
Copy-Item .env.example .env
square-drive init
notepad .env
```

`init` creates a strong token in `.env` only when `UPLOAD_TOKEN` is blank, and creates the local data layout. It never prints the token. Set `DRIVE_OUTPUT_DIR` to the existing actual Drive location; `square-drive doctor` checks it. Retrieve the token locally with `Get-Content .env` (do not paste it into chat).

Run:

```powershell
square-drive doctor
square-drive serve
```

Rebuild all published output from SQLite, without importing anything:

```powershell
square-drive rebuild-outputs
square-drive status
square-drive reconcile --list
```

`reconcile --list` shows ambiguous overlapping exports. Reconciliation is intentionally a manual/safe review step: inspect the raw files and use `square-drive reconcile --accept FILE_ID` only when you have established that it contains a new non-overlapping session. This action adds its parsed shots to publication.

## iPhone Shortcut contract

After deployment, `BASE_URL` is `http://<MINI-PC-STABLE-LAN-IP>:8765`; for example, `http://192.168.1.42:8765` (replace with the real reserved address). The Shortcut must make this raw-file request—not JSON, Base64, or multipart:

```text
POST /api/square/upload
Authorization: Bearer <token from this PC's .env>
Content-Type: text/csv
X-Filename: <URL-encoded original filename>
Body: File
```

Authenticated troubleshooting endpoint: `GET /api/status` with the same Authorization header.

PowerShell smoke test (run locally only; it reads but does not print the token):

```powershell
$token = ((Get-Content .env | Where-Object { $_ -match '^UPLOAD_TOKEN=' }) -replace '^UPLOAD_TOKEN=','')
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/square/upload' -Headers @{Authorization="Bearer $token"; 'X-Filename'='square.csv'} -ContentType 'text/csv' -InFile .\sample.csv
```

Example successful response (IDs vary):

```json
{"ok":true,"status":"imported","message":"Original preserved and records committed.","file_id":"f_abc123","session_id":"s_def456","shot_count":42,"drive_status":"written_to_sync_folder"}
```

`written_to_sync_folder` means a local, atomic write to the selected Drive sync folder. It does **not** prove Google has uploaded it. Confirm that separately in Drive on the web.

## Output layout and semantics

The output folder receives `README.md`, `manifest.json`, `sessions.csv`, `all_shots.csv`, `club_summary.csv`, `quality_warnings.csv`, `raw/<sha256>.csv`, and `sessions/<session_id>/{metadata.json,shots.csv}`. Builds are staged then atomically replaced; the manifest is written last. Unrelated output-folder files are untouched.

Originals are byte-for-byte copies. Shot rows retain source row number, original column labels, and raw values. Directional Square values are normalized as numerical values with right positive and left negative; this is independent of handedness. Zeros remain zeros. Zero ClubSpeed/SmashFactor are flagged as potential placeholders rather than converted. Session time comes from Square's metadata and carries the configured timezone assumption; upload time is stored separately; no per-shot timestamps are invented.

## Recovery, firewall, and startup

Publication jobs survive restarts. The server attempts publications after imports and on startup; unavailable/locked Drive destinations remain `pending` and can be retried with `rebuild-outputs`. The iPhone never waits for cloud sync. Operational logs are at `LOCAL_DATA_DIR\logs\square-drive.log`; they record IDs/statuses but never request headers or tokens.

Allow only the configured TCP port on the **Private** profile, substituting the configured port:

```powershell
New-NetFirewallRule -DisplayName 'Square Drive Connector (Private LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
```

Do not add a public-profile rule or router port-forwarding. In Task Scheduler, create a task under the same Windows account that runs Google Drive for desktop, choose **Run only when user is logged on**, trigger **At log on**, and use `C:\path\to\repo\.venv\Scripts\square-drive.exe serve` with the repo as **Start in**. Drive for desktop generally runs per user session; pre-sign-in publishing has not been proven and should not be assumed. Verify after every restart with the authenticated status endpoint and a real file.

## Standalone executable

`build_exe.ps1` packages the receiver with PyInstaller so it runs without the virtual environment:

```powershell
python -m pip install -e ".[build]"
.\build_exe.ps1
```

The build emits `dist\square-drive.exe` and stages `dist\install\`. Copy the executable and your configured `.env` to a stable location outside the repository—`C:\SquareDriveConnector` works—and point Task Scheduler there. **Never schedule the copy under `dist`**, which every rebuild overwrites.

Because the app resolves `.env` relative to the current directory, the scheduled task must set **Start in** to the folder holding `.env`, or it exits reporting a missing `UPLOAD_TOKEN`. To avoid a console window at every logon, run the executable through a one-line `wscript` launcher that sets the working directory and starts it hidden.

Two runtime notes for a one-file build: the bundle runs as a bootloader process plus the real server, so stop it with `Get-Process square-drive | Stop-Process -Force` rather than by the launching PID; and the task shows **Ready** rather than Running once the launcher exits, so confirm the service by the listening port instead.

## Tests

```powershell
pytest -q
```

Tests use synthetic fixtures only; a complete real export and the final iPhone/Drive-web flow remain user-side validation steps.

## CI

A Windows build artifact is published via the GitHub Actions workflow. To download the latest successful build:

1. Go to the **Actions** tab of this repository.
2. Click the most recent workflow run named **Windows Build**.
3. In the **Artifacts** section, download **square-drive-windows-x64**. The ZIP contains `square-drive.exe` and `.env.example` from the staged `dist\install\` bundle.

Extract the bundle to a stable local folder, copy `.env.example` to `.env`, and configure it there. The `.env` file stays local and must never be committed.
