# Bank Rec release notes

## 0.4.0 — standalone bank reconciliation — 2026-10-29

This source update brings additional bank matching and assistant workflows into the existing standalone Bank Rec tool. It keeps the local server/browser interface, separate SQLite workspaces, exact financial calculations and explicit operator review. Python **3.12 or newer** remains required on a new local host; Python 3.12 is the tested version.

### What changed

- **Matching:** exact signed amounts within four days, positional pairing of duplicate-amount occurrences by date/stable identity, and same-amount memo comparisons within sixteen days. Grouping produces distinct one-to-one proposals; it does not add split or many-to-one matching.
- **Diagnostics:** deterministic reasons explain unmatched rows, including opposite signs, absent amounts, date/memo limits, reserved candidates and duplicate remainders. Oversized candidate sets require manual review instead of a silent partial search.
- **Review:** manual pairing and explicit selection of up to forty proposals share the existing controls. Selected review validates every pair before saving any confirmation and requires a truthful evidence/reviewer note. Existing confirmed pairs, historical notes and completed results are preserved.
- **Assistant evidence and drafts:** scoped continuation pages contain at most forty records. The assistant can read verified financial evidence and save a single page of match drafts per question; another page requires another explicit user request. It cannot confirm matches, approve or complete a reconciliation, change workspace or post accounting entries.
- **Review checklists:** saved manual checklists retain their scope and provenance through backup/restore. Changed evidence makes their displayed status stale. A checklist executes no actions and grants no approval.
- **Request replay:** identical completed requests reuse saved receipts and drafts. Failed or interrupted requests do not automatically repeat paid provider work. Financial figures continue to come from deterministic tool output.

Source controls, opening anchors, duplicate identities, outstanding-item continuity, acceptance history, secret handling and exact-session exports remain authoritative. The [extraction record](BANK-REC-EXTRACTION.md) documents deliberate compatibility boundaries and outstanding bank-specific work, including PDF/OCR statement review, resumable import jobs, journal-entry approval and large-file navigation. This release does not claim complete feature parity.

### Verification and remaining limits

The **2026-10-28 preparation evidence** records 478 Python tests, 49 Node UI checks, clean Ruff/syntax checks, selected duplicate review, request replay, stale checklist/restore behavior and an actual numeric workbook. Those are earlier observations, not newly run checks or proof of live provider access. Publication and fresh-download checks for 0.4.0 are tracked separately in [the release checklist](../RELEASE-CHECKLIST.md); [Verification](VERIFICATION.md) retains the detailed evidence chronology.

The complete 478-test suite and 49 UI checks were also newly rerun on **2026-10-29** for this release, with Ruff, syntax, source-inventory and whitespace checks passing. Fresh public-download verification is recorded separately from these source checks.

A real embedded AI explanation remains unverified, including the previously reported setup discrepancy. Firm-authorized accounting API reads, real OS credential-store persistence, Windows execution and normal Finder launch behavior also remain unverified. Tests use synthetic records and injected provider responses. Existing optional QuickBooks Online and bounded Xero reads retain their documented limits.

The local-server workflow remains the supported delivery path. The older desktop experiment is deferred; no Python-free app, hosted service, accounting writeback or software license is added.

## 0.3.0 — local browser tool — 2026-10-28 (historical)

This source update adds a direct Xero connection for supported base-currency bank accounts, numeric Excel money cells, truthful review-note entry and clearer connection/setup diagnostics. It retains the single-device local server and browser interface. Python 3.12 or newer is required on a new computer; Python 3.12 is the tested version.

### What changed

- **Xero:** the accounting firm's administrator supplies its approved PKCE app. An authorized Xero user grants access, then staff explicitly confirm the organisation, select an account/period and import supported posted book movements manually. No accounting writeback or automatic Xero polling is included. An independent bank statement and match reviews are still required.
- **Excel:** amounts are numeric cells with two-decimal formatting. Source identities and formula-injection protection are preserved.
- **Review:** future confirmations require entered evidence and reviewer attribution rather than invented “Human reviewed” text. Historical reviews remain intact.
- **Setup:** AI connection setup saves and tests the selected workspace's connection in sequence. Advanced diagnostics identify the running instance and saved-data directory. Opening an existing example resumes it.
- **Launch and updates:** same-data launches reuse the existing instance. Immediate POSIX restart works with browser connections still open, while an occupied listener is still protected. Download, Python setup, safe updating and custom data-directory instructions are in [Quick start](QUICK-START.md).

### Verification and remaining limits

The [verification record](VERIFICATION.md) distinguishes automated fixtures, browser observations and fresh-source checks from live provider access. Xero OAuth/import tests use synthetic responses. A firm-authorized live Xero import, live QuickBooks access, the unresolved live AI setup discrepancy, real OS credential-store persistence and Windows execution remain unverified. Unsupported or incomplete Xero evidence is rejected; CSV remains available.

The older macOS desktop experiment is deferred and is not this download. No hosted service or new software license is included.
