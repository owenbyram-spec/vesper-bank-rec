# Bank Rec data and controls

Bank and book CSV inputs use these columns (UTF-8; ISO dates; signed decimal amounts):

```csv
date,description,amount,account,currency
2026-10-05,Customer deposit,500.00,checking-001,USD
2026-10-08,Supplier payment,-100.00,checking-001,USD
```

The UI accepts source descriptions and independent opening/closing balances. Optional `external_id` retains a provider/source locator. Column mapping can map vendor headers to the five canonical fields. Date, account and currency must match the selected account/period. Positive means cash increase; negative means cash decrease. Amounts must have no more precision than the supported currency. Zero-value rows, malformed dates/amounts and ambiguous columns are rejected visibly; invalid rows are never silently discarded.

Each side must satisfy **opening + signed period activity = closing**. Bank and book balances are independently sourced. For a new account, provide a reviewed prior reconciled anchor and itemized outstanding checks/deposits, or explicit evidence of a zero-outstanding opening. Unknown opening differences block completion.

Equal-valued repeated transactions remain separate source occurrences. Automatic/AI proposals are one-to-one and do not confirm matches. Split/batched payments are outside this initial matching scope; leave them visible and resolve them externally before importing supported corrected evidence.

The adjusted-bank equation is bank closing + deposits in transit − outstanding withdrawals + evidenced signed bank corrections. Adjusted books are book closing + evidenced signed book adjustments. No free-form plug is accepted. Unposted/unresolved book adjustments cannot be presented as a completed reconciliation. Outstanding items carry their original identities forward; a later clearing consumes each once.

Exports identify exact client workspace, account, session, period, source version and control evidence. CSV/Excel text cells are protected against formula-like content. JSON backup excludes credentials and preserves the exact saved financial state; restore validates the package before importing it as a separate workspace.

The included examples are synthetic. They do not establish a live accounting connection or prove that a client reconciliation is complete.
