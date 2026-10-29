# Bank Rec 0.3.0 — local browser tool

This source update adds a direct Xero connection for supported base-currency bank accounts, numeric Excel money cells, truthful review-note entry and clearer connection/setup diagnostics. It retains the single-device local server and browser interface. Python 3.12 or newer is required on a new computer; Python 3.12 is the tested version.

## What changed

- **Xero:** the accounting firm's administrator supplies its approved PKCE app. An authorized Xero user grants access, then staff explicitly confirm the organisation, select an account/period and import supported posted book movements manually. No accounting writeback or automatic Xero polling is included. An independent bank statement and match reviews are still required.
- **Excel:** amounts are numeric cells with two-decimal formatting. Source identities and formula-injection protection are preserved.
- **Review:** future confirmations require entered evidence and reviewer attribution rather than invented “Human reviewed” text. Historical reviews remain intact.
- **Setup:** AI connection setup saves and tests the selected workspace's connection in sequence. Advanced diagnostics identify the running instance and saved-data directory. Opening an existing example resumes it.
- **Launch and updates:** same-data launches reuse the existing instance. Immediate POSIX restart works with browser connections still open, while an occupied listener is still protected. Download, Python setup, safe updating and custom data-directory instructions are in [Quick start](QUICK-START.md).

## Verification and remaining limits

The [verification record](VERIFICATION.md) distinguishes automated fixtures, browser observations and fresh-source checks from live provider access. Xero OAuth/import tests use synthetic responses. A firm-authorized live Xero import, live QuickBooks access, the unresolved live AI setup discrepancy, real OS credential-store persistence and Windows execution remain unverified. Unsupported or incomplete Xero evidence is rejected; CSV remains available.

The older macOS desktop experiment is deferred and is not this download. No hosted service or new software license is included.
