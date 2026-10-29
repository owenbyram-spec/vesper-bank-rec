# Vesper & Co — Bank Rec

A standalone local finance tool designed for **CAS teams**. Reconcile one bank account and statement period, review match proposals, and export the exact reconciliation.

The assistant is built into the app. Financial calculations are deterministic; AI reads scoped results and drafts proposals for explicit human review.

## Run locally in your browser

Download the [Bank Rec source ZIP](https://github.com/owenbyram-spec/vesper-bank-rec/archive/refs/heads/main.zip), then **extract it into a permanent folder before starting**. This download runs a local server and opens its interface in your browser; Python is required on the computer running it.

If Bank Rec is already running, open [http://127.0.0.1:8765](http://127.0.0.1:8765) and select your saved workspace and period.

To start it, open **start.command** on macOS or **start.bat** on Windows in the extracted repository folder. Keep that launcher window open while using Bank Rec. Close the server with **Ctrl+C** in its launcher when finished; closing the browser tab alone leaves it running.

A new computer needs **Python 3.12 or newer** from [python.org](https://www.python.org/downloads/). Python 3.12 was tested; other permitted versions have not all been tested. First launch downloads pinned packages into this folder's private environment; later launches reuse it. An already prepared environment does not need to be reinstalled. See [Quick start](docs/QUICK-START.md) for the normal workflow and [Troubleshooting](docs/TROUBLESHOOTING.md) for launch issues.

When updating an existing copy, export a JSON workspace backup, finish active requests and stop its server before starting the new folder. Preserve the original saved-data directory and any custom `--data-dir` option. Keep financial data, exports and credentials separate from the source download. [Quick start](docs/QUICK-START.md#updating-an-existing-copy) explains how to return to saved work.

Begin with the labelled synthetic example; it needs no credentials. To add AI, choose **Connect AI**, enter your own OpenAI API key in the masked field, authorize the data flow and choose **Connect assistant**. The app saves the key and makes a small billable check. Choose **Finish** to return to the assistant. [AI setup](docs/MODEL-SETUP.md) covers billing and storage choices.

## Your first useful result

1. Open the synthetic example and inspect its sources and controls. Returning to the example resumes saved work; starting a fresh sample is a separate action.
2. Ask the connected assistant to explain the tie-out or remaining items. Financial calculations and exports also work without AI.
3. Review exact pairs, record truthful review notes and evidence every outstanding item.
4. Accept the completed reconciliation and export the result.
5. When ready, create a separate client workspace and import your own CSVs. QuickBooks Online and Xero are optional connections using developer apps approved and managed by the accounting firm; [accounting connections](docs/ACCOUNTING-CONNECTIONS.md) covers the supported paths.

## Setup and controls

- [What's new in 0.3.0](docs/RELEASE-NOTES.md)
- [Quick start](docs/QUICK-START.md)
- [Model API-key setup](docs/MODEL-SETUP.md)
- [Accounting connections and CSV alternatives](docs/ACCOUNTING-CONNECTIONS.md)
- [QuickBooks Online authorization and refresh](docs/QUICKBOOKS-SETUP.md)
- [Xero authorization, organisation confirmation and manual import](docs/XERO-SETUP.md)
- [Data formats and financial controls](docs/DATA-FORMAT.md)
- [Data flow, credentials and recovery](docs/DATA-AND-PRIVACY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Verification and limitations](docs/VERIFICATION.md)
- [Source provenance](docs/PROVENANCE.md)
- [Design notes](docs/ARCHITECTURE.md)

## Boundaries

This is a single-device utility with separate local workspaces. It has no shared team login, accounting writeback, Vesper-hosted backend or automatic model-provider fallback. Optional QuickBooks automatic refresh runs only while the local process is running; Xero imports are manual. Model usage is billed to your own provider account.

Live provider access and Windows execution remain unverified; check the verification record for this build. Source repository: [vesper-bank-rec](https://github.com/owenbyram-spec/vesper-bank-rec). No software license has been added at this stage; [the release checklist](RELEASE-CHECKLIST.md) records publication verification.

## Development

Terminal alternative: `python3.12 run.py` on macOS or `py -3.12 run.py` on Windows, from the repository folder. Current launchers verify and reuse a running instance for the same data directory. Stop an older launcher before switching builds, and preserve any custom data-directory option. Advanced port/data-directory commands are in [Troubleshooting](docs/TROUBLESHOOTING.md).

For development and tests:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Windows uses `.venv\Scripts\python.exe` in the same commands. Tests use synthetic records and injected credentials, not live client data.

The [optional desktop experiment](docs/DESKTOP-RELEASE.md) is retained as historical development evidence. It is not required or recommended for the current local-server workflow.

Vesper & Co builds tailored finance workflows. Use your existing Vesper point of contact to discuss broader workflows or integrations; this repository does not collect leads or transmit financial data to Vesper.
