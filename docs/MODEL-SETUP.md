# Connect the assistant

Bank Rec runs on your computer. The optional assistant uses your own OpenAI API account: your questions and the selected reconciliation evidence are sent to OpenAI when you ask for live assistance. Financial calculations remain in deterministic local code, and you confirm proposed changes yourself.

## Connect and ask your first question

1. Open a sample or select the client workspace and period you want to use.
2. Choose **Connect AI** beside the assistant. You can also open **Connections & settings** at the top of the app.
3. Enter your OpenAI API key in the masked field. Choose whether to use it for this session or remember it securely, then acknowledge the data flow. The default session choice keeps the key only until the app stops. Remembering it uses the operating system credential store; there is no plaintext fallback.
4. Choose **Connect assistant**. The app saves your connection and sends a small billable check containing no financial evidence. Wait for **Connected — ready to ask**. The key field clears after saving; that is expected.
5. Choose **Finish** to return to the assistant. Ask, for example: “Explain this reconciliation and the outstanding item.” Leave **Synthetic rehearsal** unchecked for a real AI response. Rehearsal is separately labelled, uses scripted narration and makes no model call.

A connection check confirms a small provider response; asking about the reconciliation also exercises the assistant's evidence tools. The assistant can explain results, draft a page of match proposals or save a review checklist. Review each pair with a truthful note, individually or using selected review. Checklists execute no actions. See [Verification](VERIFICATION.md) for what has actually been observed against a live account; automated tests use injected provider responses.

Use **Explain the tie-out**, **Plan remaining work** or **Draft match suggestions** to prepare a question, then choose **Ask**. Drafting stops after a page of up to forty pairs. Request another page explicitly if needed. If a network interruption leaves a question in the box, retrying it in the same page reuses its request receipt. A failed or unfinished request will not run paid work again automatically; review any saved drafts before starting a new question.

## Get an API key

Use your organization's approved account on the [OpenAI API platform](https://platform.openai.com/). Create a project key on the [API keys page](https://platform.openai.com/api-keys) with permission to make Responses requests. Enter it only in the app's masked connection field, never in chat, CSVs, screenshots or GitHub.

API usage is billed separately from a ChatGPT subscription. Enable API billing if needed and review [current pricing](https://developers.openai.com/api/docs/pricing), usage and available [spending controls](https://developers.openai.com/api/docs/guides/production-best-practices). Do not assume a spending alert is an enforced cap. Review your organization's policy and the provider's current terms before sending client information.

## If the connection needs attention

The app distinguishes **Connected**, saved settings whose connection check failed, and **Not connected**. If the check fails after saving, read the message and retry after addressing it; saved financial work is preserved. If a connection is already saved, choose **Check connection again** or **Try connection again** without entering the key. To replace the key, enter the new one in the same masked field and connect again. Remove it through connection settings; revoke a compromised key through the provider's dashboard.

Open **Advanced: model choice** only if you need to change the model, or **Troubleshooting details** to investigate an inconsistent connection status. The initial model is `gpt-5.6-terra`; availability depends on your API account. Another compatible Responses/function-calling model can be entered there. The app does not silently switch providers or models. [Troubleshooting](TROUBLESHOOTING.md) covers multiple app copies, saved connections and diagnostic details.

Implementation references: [model reference](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [function calling](https://developers.openai.com/api/docs/guides/function-calling) and [API key security](https://developers.openai.com/api/reference/overview). Keys are used by the local backend and are never returned to browser storage, exports or logs.
