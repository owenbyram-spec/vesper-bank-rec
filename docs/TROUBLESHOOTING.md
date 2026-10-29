# Troubleshooting

## Assistant connection

- **Not connected:** choose **Connect AI**, enter the key in the masked field, acknowledge the data flow and choose **Connect assistant**. This saves the connection and makes a small billable check. The selected workspace needs its own connection. See [AI setup](MODEL-SETUP.md) for API account and billing details.
- **Settings saved, but the check failed:** read the app's message. Check API account access, billing and whether the selected model is available. Retry the saved connection after addressing the message; you do not need to enter the key again unless replacing it. Financial work remains saved.
- **The connection worked before stopping the app:** Session-only storage keeps credentials only while that app process runs. Connect again after restarting. Use the secure remember option if you want persistence and the operating system credential store is available.
- **Secure storage unavailable:** use the session option and connect again after restarting. The app never falls back to plaintext storage.
- **A passed test is followed by a setup notice:** return to the same client workspace and app window first. If the status is still inconsistent, open **Troubleshooting details** in connection settings. The technical comparison below can help identify which saved connection is being used. Do not paste keys into chat or copy credential files between app copies.

## Xero connection

- **No Xero panel or route unavailable:** the server may predate the direct Xero implementation. Verify the repository build before restarting; a browser reload does not load new backend routes. Keep existing data and session-only credential needs in mind.
- **Redirect mismatch:** ask the firm's setup administrator to register the exact callback from **Xero app setup details** on the firm's approved PKCE app. The callback uses `localhost` and the running port; the main Bank Rec interface uses `127.0.0.1`. Do not use the QuickBooks redirect or paste a token into its form.
- **Authorization pending or popup blocked:** use **open Xero authorization**, return from Xero and choose **Check authorization**. Cancel and retry an expired/denied attempt. Keep the same workspace/period while authorizing.
- **Authorised but not ready to import:** choose **Show authorised organisations**, select the intended one and **Confirm organisation for this workspace**. Then show/select its bank account, **Use this account**, review dates and **Create reconciliation**. Reopen the Xero panel and load its account list for the selected editable period before importing.
- **Currency or source controls rejected:** use an active BANK account in the organisation's base currency. Unsupported movement types, foreign-currency/expense/conversion effects, missing bank-currency payment amounts, unstable pages or report disagreements block import and preserve prior evidence. Use controlled CSV evidence if the API coverage is unsuitable; do not force a balance.
- **Refresh expectation:** Xero import is manual only. The five-minute QuickBooks checkbox does not refresh Xero. See [Connect Xero](XERO-SETUP.md) for exact steps and remaining live-account limits.

## Launch and Python

The local server and supplied shell/batch launchers require Python 3.12 or newer. An existing working private environment can be reused; it does not need to be reinstalled for each launch. Python 3.12 was used in recorded verification, and other permitted versions have not all been tested. Install from [python.org](https://www.python.org/downloads/) and reopen the launcher after installation.

On macOS, `start.command` prefers `python3.12`, then falls back to `python3`. That fallback may be an older system Python. On Windows, `start.bat` prefers `py -3`, then `python`. Check the interpreter you installed with `python3.12 --version` on macOS or `py -3.12 --version` on Windows. If you installed another supported version, substitute its command, such as `python3.13`.

If Finder treats the script as text or cannot launch it, open Terminal, change into the extracted app folder and run:

```sh
python3.12 run.py
```

Windows terminal equivalent:

```powershell
py -3.12 run.py
```

Only run scripts from the source you reviewed. Windows execution remains unverified. If package setup fails, check network/proxy access to PyPI and rerun; saved application data is separate.

The `.venv` belongs to this app folder and retains the interpreter that created it. Installing Python does not upgrade it. If the environment needs replacement, stop this app and rename only its `.venv` folder before rerunning with the intended supported interpreter. Preserve financial data and exports. No system Python is modified.

## Existing work, ports and multiple copies

The normal local-server address is [http://127.0.0.1:8765](http://127.0.0.1:8765). If it is already running, open that address and select the saved workspace. Keep its launcher window open. To stop the server, finish active requests and press Ctrl+C in that launcher.

The current launcher holds a data-directory lock and verifies an existing instance before reusing it. If a different data directory or unrelated process holds the requested port, it reports a port-in-use error. It does not select another port or stop unrelated processes.

An older public copy may have no runtime identity. When found at the normal port 8765, launch stops safely and asks you to close that copy; its data folder cannot be verified. Older copies on unknown custom ports cannot all be discovered. Close older source launchers yourself before switching builds, including any started on a custom port.

All copies on the same operating-system account use the same default [application-data directory](DATA-AND-PRIVACY.md), unless launched with `--data-dir`. A new source folder or port alone does not isolate financial data. Run only one process against each data directory.

If work appears missing, check the app folder, browser address, original data-directory option and selected client/period before creating another workspace. **Resume example** returns to a saved sample; when several samples exist, select one. **Start a separate example** creates a fresh sample with a separate connection. Preserve existing directories and backups while identifying them.

For an intentionally separate local-server copy, use a free port and a distinct absolute data directory. These are advanced setup commands; the normal workflow uses one launcher and the existing address. From the extracted app folder on macOS:

```sh
python3.12 run.py --port 8775 --data-dir "$HOME/Vesper Bank Rec Demo Data"
```

Windows PowerShell equivalent:

```powershell
py -3.12 run.py --port 8775 --data-dir "$env:USERPROFILE\Vesper Bank Rec Demo Data"
```

Open the address printed by that launcher. Record the app folder, address and full data path together, and reuse the exact command to resume. Relative `--data-dir` paths resolve from the app folder during normal launch, so the same relative option in another copy can open different data. Copying finance data does not copy a connection.

## Advanced connection diagnosis

Under **Troubleshooting details**, compare the port, workspace ID, resolved source/data folders, running instance ID and timestamped test receipt. Connection settings belong to the selected workspace and data directory; session-only credentials also belong to the process. No credentials need to be shared for this comparison.

The setup notice is raised before any provider request when the workspace has no usable connection. Model availability, billing and provider outages produce different errors. An empty/incomplete test response cannot record success; changing the workspace or saved settings during the check invalidates it. Saving, removing or restarting clears the test receipt.

The 2026-10-28 follow-up reproduced the setup notice by saving a connection in sample A and creating sample B. That does not establish the exact cause of the user's earlier reported save/test successes. A real assistant explanation remains subject to the live-account evidence recorded in [Verification](VERIFICATION.md). Do not inspect credential stores or copy credentials between workspaces while diagnosing this issue.

## Other recovery

- **Browser session expired:** reopen the current local root address and retry. Keep its launcher running.
- **QuickBooks mismatch:** distinguish sandbox/production credentials, review the returned company and explicitly confirm it. Reconnect revoked/expired authorizations through Intuit. QuickBooks is optional and requires your own developer app; follow [QuickBooks setup](QUICKBOOKS-SETUP.md). Failed or incomplete imports cannot become zero financial data.
- **Bank statement will not pass:** compare account, period, currency, source rows and opening/activity/closing controls against independent evidence. Do not force a balancing plug.

See [Verification](VERIFICATION.md) for checks actually run on this build, including the remaining Windows, live-provider and credential-store limits.

The optional desktop experiment is retained in the [historical desktop record](DESKTOP-RELEASE.md). It is not required or recommended for this local-server workflow.
