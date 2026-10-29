# Start here

Bank Rec runs on your computer and opens in your browser. The normal address is [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Download and first setup

1. Download the [Bank Rec source ZIP](https://github.com/owenbyram-spec/vesper-bank-rec/archive/refs/heads/main.zip). **Extract the ZIP into a permanent folder** before opening any launcher.
2. Install **Python 3.12 or newer** from [python.org](https://www.python.org/downloads/) if needed. Python 3.12 was tested; other permitted versions have not all been tested. On Windows, enable the Python launcher when offered.
3. In the extracted folder, open **start.command** on macOS or **start.bat** on Windows. Keep its window open. First launch creates the app's private environment and downloads pinned packages; later launches reuse it. This source download runs a local server with a browser interface; it is not a packaged desktop app.

If the browser does not open, use [http://127.0.0.1:8765](http://127.0.0.1:8765), or the address printed by the launcher. See [Troubleshooting](TROUBLESHOOTING.md) for Python selection, opening the launcher, a port already in use or another app copy. Windows execution remains unverified. No hosted deployment is part of this setup.

## Open and use Bank Rec

1. If Bank Rec is already running, open its browser address. Otherwise open **start.command** on macOS or **start.bat** on Windows in your extracted repository folder, and keep that launcher window open.
2. Select your existing client workspace and saved period to resume work. For a first look, open the labelled synthetic example; it contains fictional records and needs no accounting connection. **Resume example** returns to a saved sample. If several samples exist, select the one you want; **Start a separate example** creates a fresh workspace.
3. To use AI, choose **Connect AI** beside the assistant. Enter your OpenAI API key in the masked field, acknowledge the data flow and choose **Connect assistant**. This saves the connection and sends a small billable test. When the app reports **Connected — ready to ask**, choose **Finish** and ask: “Explain this reconciliation and the outstanding item.” Follow [AI setup](MODEL-SETUP.md) for account and billing details. Calculations and exports work without AI.
4. Review each proposed match, enter a truthful evidence/review note, resolve the remaining items and explicitly accept the result before exporting.
5. When ready for your own records, create a separate workspace for each client/entity and import CSVs. [Accounting connections](ACCOUNTING-CONNECTIONS.md) explains the optional QuickBooks Online and Xero connectors and CSV alternatives. For Xero, the firm's administrator sets up its developer app and an authorized Xero user grants access. Staff then follow [Connect Xero](XERO-SETUP.md) to confirm the organisation, choose a base-currency bank account/period and import manually. The running server must use the current build; a real Xero connection remains unverified.

## Updating an existing copy

1. Export a JSON workspace backup and keep it separate from the source folder. Finish active assistant requests, connection checks and accounting refreshes, then press **Ctrl+C** in the old launcher to stop its server.
2. Download and extract the new source ZIP into a new permanent folder. Preserve the old folder and saved data until you verify the updated copy.
3. Start the new copy using the **same saved-data directory**. Default application data stays outside the source folder. If you previously used `--data-dir`, reuse that exact option with its original absolute path; see [Troubleshooting](TROUBLESHOOTING.md#existing-work-ports-and-multiple-copies). A new folder or port does not create an isolated data directory.
4. Select the original workspace and saved period, then verify its sources and history before continuing. Enter session-only credentials directly in the intended instance again after restart.

Keep financial data, backups, exports and credentials separate from any source ZIP you distribute. An already prepared private environment can be reused in its existing app folder; a newly extracted folder prepares its own environment.

## Stop and return

Keep the launcher open while using the app or automatic refresh. Finish any assistant request, connection check or accounting refresh, then press **Ctrl+C** in that launcher to stop the local server. Closing the browser tab alone leaves the server running.

Saved financial work stays in the operating system's application-data folder, outside the repository. Return through the same app folder, preserve any custom data-directory option and select the saved workspace and period. If the API key was saved for this session only, enter it again after stopping the server; remembered connections remain specific to their workspace.

This is a single-device utility. Concurrent team logins and enforced preparer/approver identity are outside this build. Verify results against source evidence before using them in professional work.
