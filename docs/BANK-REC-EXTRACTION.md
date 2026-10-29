# Bank-only feature extraction

This application keeps its own browser interface, local SQLite workspaces, launchers and connection settings. Bank reconciliation algorithms and assistant contracts are adapted into that architecture. It does not require a separate platform, login service, database server or background worker.

The comparison uses owner-supplied internal bank-reconciliation source at commit `6667bc5b73173d83400a8d269c23f30942e4f00a`. The reference modules are `bank_recon/match.py`, `group_match.py`, `diagnostics.py`, `agent_ops.py` and the bank tools. No source platform files or repository history are bundled.

## Implemented in the standalone tool

| Behavior | Standalone implementation |
| --- | --- |
| Exact matching | Same signed decimal amount, within four days; suggestions require review. |
| Duplicate occurrences | Date-connected repeated-amount groups pair positionally by date and stable identity. Each pair is within four days; confirmed identities remain reserved. Group leftovers are not recycled into a distant fuzzy match. |
| Memo/date matching | Same signed amount, within sixteen days, normalized memo similarity at least sixty percent. Oversized candidate sets are flagged for manual review instead of silently inspecting a partial set. |
| Honest unmatched diagnostics | Empty source sides, opposite signs, missing amounts, date/memo limits, zero amounts, reserved candidates and duplicate remainders receive deterministic reasons. Totals cover all rows; per-row detail is explicitly bounded. |
| Manual and selected review | Choose a same-amount pair manually, or explicitly select up to forty proposals. Selected review validates every pair before saving any confirmation. A note is required; names remain self-reported. |
| Scoped assistant tools | Deterministic tie-out and source reads, signed continuation pages of at most forty records, single/bulk match drafts and saved manual review checklists. The model cannot confirm, approve, complete, select a workspace or post accounting entries. |
| Bounded bulk drafting | One distinct proposal page per assistant request. Continuation requires another user request; replaying the same intent does not append duplicate drafts. |
| Durable requests | A repeated request identifier with identical scope/question/mode reuses its completed response. An interrupted or failed request never retries paid work automatically. A new live question may incur usage. |
| Review checklists | Validated, scoped and sealed checklists persist with the period. Changed financial evidence marks their displayed status stale. They execute no steps and confer no approval. Backups retain their provenance when restoring into a new workspace. |
| Readable evidence | Financial figures are copied from verified tool strings; source identities, draft status and audit evidence remain visible. Late responses cannot appear under another workspace. |

Existing source controls, opening anchors, outstanding continuity, classifications, acceptance history and export calculations stay authoritative. New matching only affects future suggestions. Existing confirmed pairs, review notes, completion results and source identities are not rewritten.

## Deliberate compatibility boundaries

The standalone tool keeps exact source-control equality, exact signed one-to-one review and its existing adjusted-bank/adjusted-books tie-out. It does not silently adopt a one-cent import tolerance, unequal manual pairs or a different completion formula. Those changes would alter accepted financial semantics and need a separately designed migration.

Repeated-amount grouping produces distinct one-to-one suggestions. It does not implement arbitrary split or many-to-one matching. Checklist seals detect changes; they are not a cryptographic signature or an independently verified reviewer identity.

## Bank-specific work still outstanding

This is a selective extraction, not a claim of complete feature parity. The reference's PDF/OCR statement review and resumable import jobs, offset-account inference and balanced journal-entry approval pipeline, virtualized large-file navigation and generated certificate service are not yet present. Book adjustments continue to require external posting followed by a controlled ledger refresh. The existing printable report can be saved as PDF through the browser.

Real model explanations, firm-authorized accounting API reads and real credential-store behavior require separate live verification. Synthetic provider fixtures and local rehearsals do not establish those results. See [Verification](VERIFICATION.md) for executed checks and [Model setup](MODEL-SETUP.md) for connection instructions.
