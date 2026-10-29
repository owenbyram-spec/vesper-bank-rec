# Connect QuickBooks Online, step by step

Bank Rec imports a QuickBooks Online bank account's **posted book ledger**. It does not post entries back to QuickBooks. You still supply a separate bank statement and review the reconciliation yourself. AI is optional and has a separate connection.

First-time setup needs an Intuit developer app and someone authorized to approve access to the intended company. Bank Rec currently accepts credentials and a refresh token from Intuit's authorization tools; it does not have a one-click QuickBooks sign-in flow. Start with a sandbox company containing fictional data. For other systems or CSV import, use [Accounting connections](ACCOUNTING-CONNECTIONS.md).

## 1. Prepare a separate Bank Rec workspace

1. Open the running [local Bank Rec](http://127.0.0.1:8765/). Keep its server running throughout setup.
2. Close settings if open. Choose **New client workspace**, enter a name such as `QuickBooks Sandbox`, choose the account's currency and select **Create workspace**.
3. Check the selected client name. Leave the completed Cedar Studio example in its original workspace; do not replace its ledger with a QuickBooks import.

You can connect a company before creating its account/period. Credentials belong to the selected workspace and this app's data directory.

## 2. Create or select your Intuit developer app

These steps take place on **Intuit's website**, outside Bank Rec.

1. Sign in to the [Intuit Developer Portal](https://developer.intuit.com/).
2. Open **My Hub → App dashboard**. Use the app card with **+** to create an app, or select one your organization already maintains.
3. Follow the portal prompts and select **Accounting** access. Payments access is not needed for this connector.
4. Use a QuickBooks sandbox company for the first connection. Intuit's sandbox companies are separate from Bank Rec's Cedar Studio example.

Portal labels may change. [Intuit's app and sandbox setup guide](https://developer.intuit.com/app/developer/qbo/docs/get-started/start-developing-your-app) is the reference for these steps.

## 3. Find the credentials for the chosen environment

1. In the Intuit app, open **Keys and credentials**.
2. Select **Development** for a sandbox connection and use **Show credentials**.
3. Keep the **Client ID** and **Client secret** available privately for direct entry into Bank Rec. They identify the developer app, not a bank account.

For a real company, use **Production** credentials and a grant authorized for that live company. Intuit requires completion and approval of its production questionnaire before production credentials become available. Development credentials and sandbox grants cannot be substituted for production credentials and grants. Follow [Intuit's credential instructions](https://developer.intuit.com/app/developer/qbo/docs/get-started/get-client-id-and-client-secret).

Never put secrets, tokens or authorization codes in chat, screenshots, CSVs or repository files.

## 4. Authorize the company in Intuit's OAuth Playground

OAuth is the approval process that lets Bank Rec use your developer app's connection to the company.

1. In the Intuit app, open **Settings → Redirect URIs → Add URI** for the sandbox. For a live grant, select **Production** on that page before adding its URI. Register the exact Playground redirect address:

   ```text
   https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl
   ```

2. Open Intuit's [OAuth Playground](https://developer.intuit.com/app/developer/playground). Choose the app and matching environment, then select the **Accounting** scope.
3. Choose **Get authorization code**. Sign in if prompted, select the intended company and approve the connection on Intuit's screen.
4. Check the returned **Realm ID**, also called the company ID. Choose **Get tokens** to exchange the authorization code.
5. Keep the returned **refresh token** private for direct entry into Bank Rec. The access token and authorization code are different values; neither belongs in Bank Rec's Refresh token field.

Follow [Intuit's Playground walkthrough](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0-playground), [redirect settings guide](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/set-redirect-uri) and [Playground redirect help](https://help.developer.intuit.com/s/article/Configuring-the-Oauth-2-0-Playground-for-QuickBooks-Online) if the portal differs. Do not register the Bank Rec address on port 8765 as a callback: this app has no OAuth callback route or hosted authorization service. A production grant must meet Intuit's current requirements; a sandbox exercise does not verify it.

## 5. Save the connection in Bank Rec

Return to the correct Bank Rec workspace. Open **Connections & settings**, then expand **QuickBooks connection (optional)**. The panel also contains AI setup; QuickBooks works independently of it.

| Bank Rec field | Enter this value |
| --- | --- |
| Environment | `Sandbox` for the development sandbox; `Production` for an authorized live company |
| Realm / company ID | The numeric Realm ID returned by Intuit for that company |
| Client ID | The developer app's Client ID for the same environment |
| Client secret | That app's matching Client secret |
| Refresh token | The refresh token from the company's authorization in step 4 |

Leave **Save securely in OS credential store** unchecked for session-only storage, or select it if your organization permits secure persistence on this computer. Session-only credentials disappear when the server stops. There is no plaintext-storage fallback.

Choose **Save QuickBooks credentials**. The private fields clear after saving. The success message asks you to test and confirm the company; saving alone does not complete setup.

## 6. Identify and confirm the company

1. Choose **Test & identify company**.
2. Compare the returned company name and ID with the intended client. If either is wrong, stop before importing and replace the connection with the correct grant.
3. Choose **Confirm company for this workspace** only for the intended company.
4. Under **Troubleshooting details**, the non-secret QuickBooks status should show `configured: true`, the intended environment, `bound: true` and the intended company.

A test without company confirmation does not allow ledger imports. Replacing saved credentials removes the previous binding: test and confirm again after replacement.

## 7. Choose the exact bank account and period

1. In the QuickBooks section, choose **Show available bank account IDs**.
2. Find the account to reconcile. Note its exact ID, displayed name and currency. This list displays information; clicking a row does not select an account.
3. Close settings. Choose **New account / period** in the main workspace.
4. Paste the bank account's exact QuickBooks ID into **Account ID**, enter an **Account display name**, and set **Period start** and **Period end**. Start with one calendar month. Choose **Create reconciliation**.

The workspace currency must match the account/report currency. Use an individual bank account rather than a parent grouping with subaccounts. If an existing reconciliation has the wrong ID, create the correct one before importing.

| ID | What it identifies | Where it belongs |
| --- | --- | --- |
| Client ID | The Intuit developer app | QuickBooks connection form |
| Realm / company ID | The QuickBooks company | QuickBooks connection form |
| Bank account ID | One bank account inside that company | Main reconciliation's **Account ID** field |

A printed bank account number, account nickname or Realm ID cannot replace the QuickBooks bank account ID.

## 8. Import and check the ledger

1. Confirm the correct workspace, account and dates are selected. Use the new, editable reconciliation; a completed reconciliation cannot be refreshed.
2. Open **Connections & settings → QuickBooks connection (optional)**.
3. Leave the five-minute refresh checkbox **off** during the first import and review.
4. Choose **Refresh selected period's ledger** once.
5. On success, the app reports **Controlled QBO book ledger imported**. Close settings and inspect **Book ledger** under **Sources & opening**. Compare the row count, dated evidence, opening balance, signed activity and closing balance with QuickBooks for that account and period. Source controls must pass.
6. Import the independent bank statement through **Import a bank statement or book ledger CSV**, choosing **Independent bank statement**. Supply its own opening/closing balances and dated evidence. Review the prior reconciliation/opening anchor, then continue match and exception review. See [Data and controls](DATA-FORMAT.md).

A successful refresh replaces book-ledger evidence and clears prior match/review decisions for that period. Back up existing work before deliberately refreshing a period under review. An incomplete or rejected report preserves the previous valid ledger and displays an error; do not treat a failed read as an empty ledger.

The connector uses an account-filtered General Ledger report. Ambiguous hierarchy, unsupported layouts or failed exact controls are rejected. Pending bank-feed items in QuickBooks' **For review** queue are outside this Accounting API import; they are not an independent bank statement. [Intuit's bank-feed clarification](https://help.developer.intuit.com/s/question/0D5TR00001ZdteX0AR/matching-capital-on-tap-receipts-to-pending-bank-feed-transactions).

## Return later or disconnect

- Use the same app address/data directory and select the same workspace and period. Re-enter session-only credentials after stopping the server. Bank Rec manages access-token refresh and returned token rotation within the chosen storage mode.
- Enable five-minute refresh only when repeated replacement of that selected period's ledger is intended. It runs while the server is running; changing workspace or disconnecting stops it. Check last-success status instead of assuming the ledger is current.
- **Disconnect & remove credentials** removes the local connection and stops polling. Imported evidence remains saved. Revoke the app grant separately through Intuit's connected-app controls if provider access should also end.

## If a step fails

| What you see | Next check |
| --- | --- |
| Intuit rejects the redirect | Compare the registered Playground URI exactly, including capitalization, with step 4 |
| Authentication or token failure | Match the environment, Client ID/secret and grant; authorize the company again if expired or revoked |
| Company must be confirmed | Test, check the returned company, then explicitly confirm |
| Wrong company | Stop before import and use the intended company's grant |
| Account missing or wrong currency | Check the company and individual bank account; do not substitute a label or account number for its ID |
| Completed period or account/period mismatch | Select/create the intended editable reconciliation; leave the completed example unchanged |
| Report/source controls rejected | Compare dates, account, currency and balances; use a controlled CSV import if the report layout is unsupported |
| Secure storage unavailable | Choose session-only storage; do not save secrets to a text file |

For support, report the step, non-secret error, environment and whether the expected company was returned. Never share secrets, tokens, passwords or raw authorization responses.

Instructions were checked against the implemented controls and public Intuit documentation on **2026-10-28**. Real QuickBooks authorization/import and actual OS credential-store behavior remain unverified; fixtures do not establish live account access. See [Verification](VERIFICATION.md).
