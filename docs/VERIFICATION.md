# Bank Rec verification — 2026-10-28

`synthetic`: The browser journey loaded independent September bank/book controls, confirmed four reviewed one-to-one pairs (including duplicate software payments), evidenced a $300 outstanding withdrawal, and completed at adjusted bank = adjusted books = **$6,050.00**, difference **$0.00**. The saved completed result survived process restart. Reopening, prior-period continuity, pending-book-adjustment blocking, two-account export isolation and validated backup/restore are reproduced in API/domain tests.

`synthetic`: The exact-session Excel file was downloaded through the browser and reopened with openpyxl: 52 rows, expected exact values and no formula cells. CSV, Excel, HTML and JSON recovery are also exercised by tests. The printable browser report was inspected and its numeric columns corrected so amounts stay readable. A missing-connection refresh displayed a recoverable message and retained the completed session.

`repository-observation`: The final full suite passed **81 tests**, exit 0. After the printable-layout-only change, all **38 affected domain/API tests** passed again, exit 0; Ruff remained clean.

## Runtime and API boundaries

`repository-observation`: Both tools are independent FastAPI/SQLite applications, with exact-money domain code and static browser UI. Initial macOS launch installed the hash-locked dependencies into each repository's own `.venv`; actual `start.command` launch and repeat launch were exercised. An occupied-port launch exited 1 with a recovery message and stopped no process. Normal test-process shutdown exited 0. No original platform services were needed.

`synthetic`: Assistant tests use provider fixtures and actual scoped tool dispatch. Browser rehearsal uses real local handlers with explicitly scripted narration and zero model calls. QBO tests use synthetic HTTP/token/report fixtures, including incomplete reads, paging, binding, rotation, revoked tokens and throttling. These checks do not establish behavior against a real Intuit company or a real model account.

`repository-observation`: Local Host/Origin/session/CSRF protections, displayed-workspace request binding, stale requests, proposal authority, credential redaction, explicit session-only fallback, locked-store recovery and polling controls are covered. Financial writes validate the captured scope inside the storage lock. The fresh critical review identified stale-tab actions and session-only token rotation issues; regression tests reproduce their corrected behavior. No real credential-store entry was read, saved or deleted during verification.

## Commands and results

Commands ran from each repository root using a separate isolated Python 3.12.13 verification environment with the pinned app/dev dependencies. The application launchers separately installed and ran the production lock in each app's own `.venv`.

```sh
python -m pytest -q -p no:cacheprovider
ruff check . --no-cache --output-format concise
sh -n start.command
./start.command --no-install --no-browser --testing --data-dir <isolated-test-directory>
```

`repository-observation`: Ruff, Python source parsing, macOS shell syntax, document links, independent Git roots, source-path imports and the authored-file allowlist passed (exit 0). The Windows launcher has real CRLF line endings and was inspected, but it has not been executed on Windows. A pattern scan found no high-confidence provider keys/private keys in the source candidates; this is a bounded scan, not a universal absence guarantee. The only test warning is the dependency's Starlette/httpx TestClient deprecation.

## External gates still open

`open`: Real model response and account access; real QBO sandbox/production authorization and report acquisition; actual OS-keychain behavior with a user's credentials; Windows execution; broad real-world accounting formats. Do not treat the local commits, tests or screenshots as production, compliance, market or ownership proof.

## Public-source publication

`primary-record`: Owen authorized public publication under `owenbyram-spec` on 2026-10-28 without adding a license at this stage. Destination: [vesper-bank-rec](https://github.com/owenbyram-spec/vesper-bank-rec).

`open`: Public visibility, default branch and uploaded source contents must be verified after upload and recorded in [the release checklist](../RELEASE-CHECKLIST.md). Local test results alone do not establish that publication succeeded.
