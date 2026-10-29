# Connect Xero, step by step

This repository implements a **local, read-only Xero connection** for supported bank-account movements. The accounting firm supplies and manages its Xero developer app. An authorized Xero user connects the intended organisation, explicitly confirms it in Bank Rec, selects an account/period and imports its book ledger. The team still supplies an independent bank statement and reviews every reconciliation decision. AI has a separate, optional connection.

These instructions describe the repository implementation. They do not establish that an already-running server has loaded the new routes or that a real Xero account has been tested. Real Xero authorization/import and actual OS credential-store behavior remain unverified; see [Verification](VERIFICATION.md).

## Who handles setup

The firm's designated setup administrator registers the firm-owned developer app once and provides its public Client ID and registered callback details to the team. Authorized accounting staff then connect each intended organisation/workspace and perform imports. The app registrant and the Xero user approving access can be different people; these are setup responsibilities, not roles enforced by Bank Rec.

Vesper's publisher does not need a personal Xero account or personal API connection for a firm's setup. Bank Rec supplies no shared developer app or credentials.

## Firm setup administrator: prepare a login and Demo Company

If the firm already has an appropriate Xero login and developer app, reuse its approved setup and skip account creation. Otherwise, the firm's designated setup administrator follows these prerequisites before app registration. Creating a Xero login is free, and the Demo Company does not require a paid Xero subscription. Xero's [development accounts guide](https://developer.xero.com/documentation/development-accounts/) distinguishes the Demo Company from a separate trial organisation.

1. Open Xero's [free developer signup](https://www.xero.com/us/signup/developers/). Enter your details, verify your email and set your password directly on Xero's pages; never send passwords or verification links in chat.
2. Sign in and open **My Xero**. If Xero opens an organisation first, click its name and choose **My Xero**.
3. Below the organisation list, choose **Try the Demo Company**. For this USD test, use **Change Country** to select the **United States** if needed, then open the Demo Company. Changing country resets its demo data, so do this before the import test.
4. Confirm you can see the fictional Demo Company before continuing with Bank Rec and app registration below.

The signup page may mention a free trial. A trial organisation and the pre-populated Demo Company are separate options; this test needs the Demo Company, with no paid subscription. See Xero's [Demo Company instructions](https://central.xero.com/0/article/Use-the-demo-company) if it is not visible.

## 1. Prepare a separate workspace

1. Use the current Bank Rec build at its local browser address, normally [http://127.0.0.1:8765](http://127.0.0.1:8765). Keep the server running.
2. Start with Xero's Demo Company or another approved test organisation. Choose an active **BANK** account in that organisation's base currency.
3. In Bank Rec, choose **New client workspace**, enter the client/test name and select that base currency. Choose **Create workspace**. Preserve the completed Cedar Studio example in its own workspace.

## 2. Firm setup administrator: register the firm's Xero PKCE app

The firm's authorized setup administrator completes this one-time app registration and maintains it when callback addresses change. Staff using the approved app do not each need to create a developer app.

1. In Bank Rec, open **Connections & settings → Xero connection (optional)**.
2. Expand **Xero app setup details** and copy the exact displayed **redirect URI**.
3. In the firm's Xero developer account, create or configure an **Auth Code with PKCE** app and register that exact redirect URI. Use Xero's [PKCE guide](https://developer.xero.com/documentation/guides/oauth2/pkce-flow/) for the current registration flow.
4. Provide the app's public **Client ID** and approved callback details to the accounting staff for entry into Bank Rec. The Client ID is an app identifier, not a Xero organisation ID, bank account ID or password. Do not share Xero passwords or tokens.

The callback uses `http://localhost:<running-port>/api/xero/callback`. Copy the displayed value rather than inventing one or substituting `127.0.0.1`. The callback is a narrow authorization-return route; the normal Bank Rec interface remains at its `127.0.0.1` address. If the port changes, update the registered callback to match.

PKCE uses a per-attempt verifier managed by the local backend; no shared client secret is entered in this app. The requested scopes are:

```text
offline_access
accounting.settings.read
accounting.banktransactions.read
accounting.payments.read
accounting.reports.banksummary.read
```

`offline_access` enables token refresh. The accounting scopes are read-only permissions for the implemented data sources. See Xero's [OAuth overview](https://developer.xero.com/documentation/guides/oauth2/overview) and [granular-scope guidance](https://developer.xero.com/faq/granular-scopes); older broad-scope examples should not replace the permissions shown by this build.

## 3. Authorized accounting staff: connect the intended organisation

Complete steps 1 and 3–7 for the intended workspace using the firm's approved app. The Xero user approving access must be authorized for that organisation.

1. In Bank Rec's Xero panel, enter the firm-provided **Xero app Client ID** and check that the displayed callback matches the firm's registered value.
2. Leave **Remember this Xero connection securely on this computer** off for session-only storage, or select it if secure persistence is approved. Session-only credentials disappear when the local server stops. There is no plaintext fallback.
3. Choose **Authorize with Xero**. A separate browser window opens Xero's authorization page. Sign in to Xero there and approve the intended test organisation. Never paste Xero passwords, authorization codes or tokens into Bank Rec, chat or QuickBooks fields.
4. If the browser blocks the new window, use the panel's **open Xero authorization** link.
5. After the local return page reports authorization received, return to Bank Rec and choose **Check authorization**. The panel also checks while authorization is pending.

Authorization alone does not bind an organisation or import financial data. If you cancel, deny access or let the attempt expire, use **Cancel authorization** when needed and authorize again. Do not change workspace or period while approving; changed scope invalidates the attempt.

## 4. Confirm the organisation explicitly

1. Choose **Show authorised organisations**.
2. Select the intended organisation by its name and ID. No organisation is selected automatically.
3. Choose **Confirm organisation for this workspace**.
4. Check the displayed confirmed organisation, ID and base currency. If the workspace currency differs, select/create a workspace in the correct currency before continuing.

The Xero app Client ID, organisation/tenant ID and bank account ID identify different things. Do not substitute one for another.

## 5. Select the account and create its period

1. Choose **Show Xero bank accounts**.
2. Select the intended active bank account and check its name, ID and currency. Only accounts in the confirmed organisation's base currency are supported.
3. Choose **Use this account**. This closes settings and fills the main account/period form; it does not create a reconciliation or import data.
4. Review **Account ID** and **Account display name**, enter the exact **Period start** and **Period end**, then choose **Create reconciliation**. Start with one calendar month; the direct connector accepts periods of at most six months.

If the intended editable reconciliation already exists with that exact Xero account ID, select it instead of creating another. A completed period cannot be refreshed.

## 6. Import the selected book ledger manually

1. Check the selected client, account and dates in the main workspace.
2. Return to **Connections & settings → Xero connection (optional)**. Choose **Show Xero bank accounts** again if needed to load the account list for the current selection.
3. Check the **Selected** account/date line under **Import the selected period**.
4. Read the replacement warning, then choose **Import selected period’s Xero ledger** once.
5. On success, close settings and inspect **Book ledger** under **Sources & opening**. Compare row count, source evidence, opening balance, signed activity and closing balance with Xero. Source controls must pass.

Import replaces this period's book-ledger evidence and clears its match reviews. Back up reviewed work before deliberately replacing it. Failed, incomplete or unsupported reads preserve the previous valid financial evidence and display an error. There is **no automatic Xero polling** in this build; the QuickBooks five-minute option applies only to QuickBooks.

## 7. Import the independent bank source and review

Download the statement and balances from the bank. In the CSV copy, map `account` to the exact Xero bank account ID selected in Bank Rec; retain the original statement and a note of that mapping. Use **Import a bank statement or book ledger CSV → Independent bank statement**, with that source's own opening/closing balances and dated evidence. Each side must satisfy **opening + signed period activity = closing**.

Review the opening reconciliation anchor and prior outstanding items, then review match proposals and exceptions before accepting/exporting. Preserve separate repeated occurrences; an authorization or successful import never confirms a match. See [Data formats and controls](DATA-FORMAT.md).

## Supported coverage and failures

The connector combines posted **BankTransactions** (including supported transfer movements) and **Payments**, using payment `BankAmount` in the bank account's currency. It reads complete pagination twice to detect changes and compares independent **BankSummary** opening, gross cash received, gross cash spent and closing controls before accepting the result. The bank statement remains a separate source.

This is bounded coverage for active base-currency **BANK** accounts, not a universal Journals/General Ledger connector or a claim of a Xero access tier. Unknown movement types, foreign-currency accounts, missing bank-currency payment amounts, changed/incomplete pages, unsupported expense/conversion effects or report/detail disagreements cause rejection. Do not add a balancing plug or discard rows to force acceptance.

| What you see | Next step |
| --- | --- |
| No Xero panel or route unavailable | Confirm the server is running this repository build; a browser reload does not load new backend code |
| Redirect rejected | Compare the registered localhost callback exactly with the value shown by the running app |
| Authorization pending, denied or expired | Return from Xero and check; cancel/retry if needed, preserving the same selected workspace |
| Wrong organisation | Stop before import; explicitly select and confirm the intended organisation |
| Currency mismatch or missing account | Check the organisation and active base-currency BANK account; do not relabel foreign amounts |
| Source coverage/control failure | Compare the period and source reports; use the controlled CSV fallback below for unsupported coverage |
| Secure storage unavailable | Choose session-only storage explicitly and reconnect after restart |

## Return later or disconnect

Use the same data directory and selected workspace. The backend handles access-token refresh and returned token rotation within the chosen storage mode. Session-only storage requires authorization again after the server stops. **Disconnect Xero locally** removes this workspace's local Xero credentials and cancels pending authorization; imported financial data remains saved. Revoke provider access separately through Xero's connected-app controls if needed.

Credentials stay in the backend's session memory or approved OS credential store, scoped to workspace and data directory. They are excluded from finance exports/backups and are not returned to browser storage or application logs. Report non-secret error text and the failing step when seeking help; never share raw authorization responses or tokens.

## CSV fallback

You can use Xero exports without creating an API app or authorizing a connection:

1. In Xero, open **Reporting → All reports → Account Transactions**, select only the intended bank account and exact dates, then **Update**. Retain the original report and independent book opening/closing controls. Xero's [report guide](https://central.xero.com/0/article/Account-Transactions-report-New) and [export guide](https://central.xero.com/0/article/Export-data-out-of-Xero-US) describe the current screens/formats.
2. Prepare a UTF-8 CSV with `date,description,amount,account,currency`. Keep one row per posted movement; money in is positive and money out negative. Preserve duplicates and optional `external_id` source locators. Convert separate debit/credit columns only with preparer review; Bank Rec does not infer signs from two columns.
3. Remove report wrappers and subtotals from the CSV copy while retaining their evidence in the original report. Verify opening + activity = closing; never discard transactions to make totals agree. For CSV only, use a consistent chosen account identifier in the main reconciliation and both source copies, retaining a note of that mapping. Direct API import requires Xero's actual account ID.
4. Follow [Accounting connections → CSV steps](ACCOUNTING-CONNECTIONS.md#use-csv-from-another-system), importing the Xero file as **Book ledger** and the independent bank file separately. For a first trial, use base currency; converted base amounts must not be labelled as original bank-currency amounts.

The official references were reviewed on **2026-10-28**. Implementation and synthetic fixtures do not establish live Xero access, a real export's compatibility or real OS-keychain behavior. Those checks require direct participation by an authorized account owner.
