# Synthetic CAS reconciliation

`synthetic`: Every name, identifier and record here is fictional. The in-app **Open the synthetic example** loads September, accepts its labelled synthetic opening evidence, and prepares four unconfirmed match proposals. No credentials are needed.

September account `SYNTH-CHECKING`, USD, September 1–30, 2026:

- Independent bank opening: `5000.00`; activity: `1350.00`; closing: `6350.00`.
- Independent book opening: `5000.00`; activity: `1050.00`; closing: `6050.00`.
- August 31 evidenced synthetic zero-outstanding opening: both `5000.00`.
- Review the receipt, payroll and both separate software occurrences; confirm four matches.
- Classify check 1042 `-300.00` as an outstanding withdrawal and enter the synthetic check schedule as evidence.
- Adjusted bank `6050.00` = adjusted books `6050.00`. Explicit acceptance completes September.

Create the contiguous October 1–31 period for the same account. It inherits check 1042 with its original identity and September’s completed opening anchor. Import `october-bank.csv` with opening `6350.00`, closing `6050.00`, and the empty `october-ledger.csv` with opening/closing `6050.00`. The header-only ledger is an explicitly evidenced no-activity source, not a missing source. Confirm the clearing bank withdrawal against the carried check. Outstanding becomes `0.00`; both adjusted balances remain `6050.00`. It cannot be consumed twice, and a November period will not carry the cleared item.

To start real work, create a separate client workspace. Replace all synthetic evidence with your own independently verified source controls. Never treat these examples as client facts.
