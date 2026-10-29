# Design notes

This repository contains a standalone Bank Rec tool for one bank account and statement period at a time. It runs a local server with a browser interface, separate saved workspaces, an embedded assistant and optional read connections to accounting systems.

AI retrieves current evidence, explains and drafts through a closed tool set. Exact decimal calculations remain in deterministic code. A model cannot select a client, approve a proposal, confirm a match, read a key, execute SQL/shell, or post to accounting. Financial figures are rendered from verified tool output; unsupported model numerical prose is rejected.

Every consequential request carries the scope shown in the browser. A server-side active selection alone cannot authorize an old tab to act on a new client. Storage rechecks identity and revision atomically before financial writes. Network reads capture context before calling the provider and validate it before saving.

A source read is usable only when its controls establish coverage. Missing data stays missing; it does not become a zero balance. Multi-request vendor reads record observation windows and are never labelled atomic vendor snapshots. Bank and book sources stay independent, and duplicate amounts retain distinct transaction identities.

Credentials have explicit persistence choices. Session-only rotation does not depend on a working keychain. Deleting an existing saved entry requires confirmed removal; new generated workspaces can record only the nonsecret fact that no entry has been created. Finance backups never include credentials.

Local names and acceptance notes are operator inputs. This utility does not establish shared team identity or enforced preparer/approver separation. Polling exists only while the process is running.

Bank-only matching and assistant behavior is adapted into these existing modules; [the extraction record](BANK-REC-EXTRACTION.md) identifies implemented contracts and remaining gaps. Suggestions use exact/group/fuzzy passes and deterministic unmatched diagnostics. Signed continuation pages bind the full evidence state and displayed scope. Match drafts and sealed manual checklists remain review artifacts; only explicit finance routes record operator confirmations.

Assistant request receipts are saved before provider work begins. A completed identical retry replays its saved result; failed or interrupted attempts require an explicit new request. Receipts contain response evidence and usage, never keys, raw prompts or provider headers. They are separate from financial backups and are removed when their workspace is deleted.
