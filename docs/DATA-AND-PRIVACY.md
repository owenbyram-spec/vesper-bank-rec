# Data, credentials and recovery

Financial workspaces live in the operating system's application-data directory, outside the source repository. A custom `--data-dir` can be supplied explicitly. Back up/export data to a location you control. Exported financial files may contain sensitive business information and are your responsibility to protect.

Default locations for the current operating-system account:

- macOS: `~/Library/Application Support/Vesper Bank Rec`
- Windows: `%LOCALAPPDATA%\Vesper Bank Rec` (the home directory is used if `LOCALAPPDATA` is unavailable)
- Linux: `$XDG_DATA_HOME/vesper-bank-rec`, or `~/.local/share/vesper-bank-rec` when `XDG_DATA_HOME` is unset

Moving or replacing the source folder does not move this saved financial data. Export a JSON workspace backup before migrating data. The source folder and browser port do not determine this location. Multiple downloaded copies use the same default data unless given distinct `--data-dir` paths. Current launchers hold a data-directory lock and reopen a verified running instance instead of starting a duplicate. The backend finishes active work before the lock is released during shutdown. Older launchers may not share this lock or expose a runtime identity; stop those before switching builds, including copies on custom ports. Run only one process against each data directory. For existing work, reuse the original directory and select its saved workspace; use an absolute path when supplying `--data-dir` so changing app folders does not change the destination. See [Troubleshooting](TROUBLESHOOTING.md) for separate-instance commands.

The normal backend interface binds to `127.0.0.1`. Xero additionally uses a narrowly scoped `localhost` OAuth callback at the exact URI shown in its connection panel; the main UI remains at `127.0.0.1`. Callback codes/state are removed from the request log path and are never finance-export data. Host, browser origin, session and CSRF checks protect local API requests. This is a single-device utility; it is not hosted team software. Local user access and disk/backups still require normal device protection.

Model keys, Intuit credentials and tokens, and Xero tokens use the operating system credential store when explicitly requested, otherwise session memory. No plaintext persistence fallback is used. Local connection metadata records only hashed names of credential entries known absent; it contains no keys or tokens and is excluded from finance backups. A locked credential store must be unlocked before an existing saved credential can be removed. Do not commit workspaces, exports, `.env` files or credentials. The `.gitignore` is a convenience and is not a substitute for inspecting files before publishing.

Connections are scoped to the selected workspace and resolved data-directory path. Session-only credentials are also scoped to the running process; they do not survive stopping the local server. Keep its launcher window open while working, and press Ctrl+C there when finished. Closing the browser tab alone does not stop the local service. Copying financial data or changing its path does not transfer a working connection. Configure credentials directly through the intended instance's settings rather than copying credential files or stores.

Explicit real assistant requests transmit the current prompt and permitted workflow evidence to the selected model provider. Intuit requests transmit the connection credentials/authorized requests to Intuit. Xero authorization and selected accounting reads contact Xero using the firm's approved PKCE app; the local backend manages the verifier, token exchange and rotation. No shared Xero client secret is supplied. There is no Vesper telemetry, central storage, shared key or silent cross-provider fallback.

Imports, model output and descriptions are untrusted data. They cannot select another workspace, access credentials, execute code or approve proposals. Financial calculations remain deterministic. Review and approval here records a local operator action; typed names do not establish independently verified preparer/approver identities.

The data folder also contains local assistant request receipts with returned evidence, saved response text and usage counts. Raw questions are hashed for request binding rather than stored in these receipts; API keys and provider headers are excluded. A completed identical retry can reuse its receipt without a new model call. Failed or interrupted attempts are not retried automatically; a new live request may incur usage. Treat these receipts as financial data. Deleting a workspace removes its receipts. The separate run log contains hashed workspace identity, status, evidence/proposal identifiers and available usage counts.

Saved assistant review checklists are included in workspace backups and workpaper exports. They execute nothing; their displayed status becomes stale when financial evidence changes. Request replay receipts and the operational run log are not included in financial backups.

Use versioned JSON export/restore for a complete workflow backup and CSV/Excel/printable exports for review. Restoring a backup requires validation and must preserve money, provenance and scope. Keep originals until you verify a restored copy. Secrets are excluded from all backup/export formats.
