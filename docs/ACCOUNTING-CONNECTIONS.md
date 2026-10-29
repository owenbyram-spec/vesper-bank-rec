# Connect accounting data

**QuickBooks Online and Xero have direct accounting connectors in this repository build.** They read the selected account's supported posted book ledger. You still need an independent bank statement for reconciliation. The connection does not import pending bank-feed items or write changes back to accounting.

| Your source | Available path |
| --- | --- |
| QuickBooks Online | Follow the [step-by-step QuickBooks Online guide](QUICKBOOKS-SETUP.md) to authorize your own Intuit developer app, confirm the company and refresh the selected ledger. |
| Xero | The firm's administrator registers and manages its PKCE app. An authorized Xero user connects the organisation; staff then confirm it, select an active base-currency BANK account/period and import manually. Follow [Connect Xero, step by step](XERO-SETUP.md). CSV remains a fallback. |
| QuickBooks Desktop, Sage, NetSuite or another system | Export the book ledger to CSV and use the steps below. Direct connectors for these systems are not implemented. |
| A generic accounting API URL | No generic API-URL connection is implemented. Identify the accounting product and version before new adapter work begins. |

Xero coverage is bounded by BankTransactions/Payments and independent BankSummary controls; foreign-currency or unsupported/incomplete movement reads are rejected. No Xero polling is implemented. Live provider authorization/import remains unverified; see [Verification](VERIFICATION.md).

The **AI connection is separate and optional**. An OpenAI key does not connect an accounting system. CSV imports, calculations and exports work without an AI key or accounting API credentials.

## Use CSV from another system

1. Create or select the client workspace and the exact account/period in Bank Rec. Use a separate client workspace for your own records rather than the synthetic example.
2. Export that account's posted book ledger for the selected period from the accounting system. Separately obtain the bank statement CSV and its opening/closing balances from the bank. Retain both original sources.
3. Prepare each file as UTF-8 CSV with these fields, or use **CSV column mapping** to map its headers:

   ```csv
   date,description,amount,account,currency
   2026-10-05,Customer deposit,500.00,checking-001,USD
   2026-10-08,Supplier payment,-100.00,checking-001,USD
   ```

   Dates use `YYYY-MM-DD` and must fall within the selected period. `account` must equal the selected account ID and `currency` must match the workspace. Inflows are positive and outflows negative. Keep repeated transactions as separate rows, including equal amounts on the same date; do not deduplicate or merge them. An optional `external_id` column retains a source locator while Bank Rec preserves distinct row occurrences.
4. Open **Import a bank statement or book ledger CSV**. Choose **Book ledger** for the accounting export or **Independent bank statement** for the bank file. Enter that source's own opening balance, closing balance and dated evidence description.
5. Choose **Preview & check source**. Each side must satisfy **opening + signed period activity = closing**. Review the account, currency, rows and any errors before choosing **Accept & import this source**. Repeat for the other side. Replacing source evidence resets match reviews.
6. Review the opening reconciliation anchor: provide prior reconciled evidence and outstanding items, or evidence of zero opening outstanding. Then review match proposals and remaining exceptions before accepting the reconciliation.

See [Data formats and controls](DATA-FORMAT.md) for precision, rejected rows, outstanding items and export identity requirements. A direct connection to another accounting system requires a new adapter; name the system and cloud/desktop version before that work is scoped. Do not paste its credentials into chat or into another provider's connection form.
