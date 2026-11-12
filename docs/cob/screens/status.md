---
route: /status
audience: public
title: "Workspace status"
topic: getting-started
summary: "Shown when your workspace is waiting for activation or has been paused, with what to do next."
starters:
  - "Why does it say my workspace is paused?"
  - "How long until my new workspace is activated?"
  - "Are my documents safe while the workspace is paused?"
  - "Who do I contact about a paused workspace?"
---
You see this page when you have signed in successfully but your workspace cannot be used yet. There are two cases: it is **waiting for activation**, or it has been **paused** (suspended) by the platform operator.

## What you see

- **Waiting for activation**: "We'll activate your workspace soon." The text explains that your e-mail is verified and the team activates new workspaces by hand; you will be e-mailed the moment it is ready. A line asks "Questions or payment?" with the operator's contact.
- **Paused**: "Your workspace is paused." Until it is reactivated, your assistants, website widget and API keys are switched off. It states that your documents and settings are safe. A line says "To reactivate, contact ..." with the operator's support e-mail or contact link.
- **Check again** (it reports "Still waiting" if nothing changed) and, if you belong to other workspaces, **Switch workspace**.
- When you came in with an API key whose workspace is not active, the page explains that the key is valid but the dashboard cannot open the workspace.

## Common tasks

1. **Wait for activation.** Nothing to do. You get an e-mail when you are in. Choose **Check again** to see now.
2. **Get a paused workspace reactivated.** Contact the operator using the e-mail or link on the page. Reactivation restores logins, assistants, the widget and keys at once.
3. **Use another workspace.** If you have one, choose **Switch workspace**.

## Troubleshooting

- **"Still waiting"** for a long time: ask the operator; they may need to confirm payment first.
- **The widget on your website stopped answering**: the workspace is paused (`workspace_not_active`); it resumes when reactivated.
- **Apps get "workspace not active"**: same cause; their keys work again after reactivation.
- **Your documents**: nothing is deleted while a workspace is paused.
- **No contact shown**: the operator has not set a support e-mail; use the address you signed up through.

## Related

- [Sign in](/docs/screens/login)
- [Create an account](/docs/screens/signup)
- [Sign in with a key](/docs/screens/login-key)
