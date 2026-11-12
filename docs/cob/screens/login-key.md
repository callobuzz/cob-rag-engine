---
route: /login/key
audience: public
title: "Sign in with a key"
topic: keys
summary: "For developers and the operator's recovery login: sign in with the admin key or a workspace key."
starters:
  - "Which keys can I use to sign in to the dashboard?"
  - "Why is my knowledge-base key refused?"
  - "What is the Engine URL on this page?"
  - "Is it safe to tick Remember on this device?"
---
Key sign-in is for developers, and for the platform operator's recovery login when e-mail sign-in is not set up. People normally log in with their e-mail instead. A key sign-in has no profile, password or sessions.

## What you see

- **API key** with the placeholder "tk_... or the admin key".
- **Advanced**, which contains the **Engine URL**: where the engine runs. It is self-hosted, so this is the operator's own server.
- **Remember on this device**: otherwise the key is forgotten when the tab closes.
- A warning in red when you enter the admin key: anyone with this browser then gets full control of the engine.
- **Sign in**, and **Log in with email instead**.

Which keys work: a **workspace key** (starts `tk_`) opens that workspace as an owner; the **admin key** opens the operator's view. A **knowledge-base key** (starts `ck_`) is for apps, the widget backend and agents. It is refused here on purpose, and so is a widget key (`pk_`).

## Common tasks

1. **Sign in with a workspace key.** Paste the `tk_` key and choose **Sign in**.
2. **Recover the operator login.** Paste the admin key. Use it only on your own computer and untick Remember.
3. **Point at another engine.** Open Advanced, change the Engine URL, then sign in.
4. **Go back to e-mail.** Choose **Log in with email instead**.

## Troubleshooting

- **"That key was not accepted"** (`invalid_api_key`): check for a missing character at the start or end.
- **"Knowledge-base keys don't open the dashboard"**: use your e-mail, a `tk_` key or the admin key.
- **"This workspace is paused or waiting for activation"**: the key is valid but its workspace is not active. See Workspace status.
- **"Can't reach the engine at ..."** (`network`): check the Engine URL and that the engine is running.
- **Signed out at once**: a rotated or revoked key stops working immediately.

## Related

- [Sign in](/docs/screens/login)
- [Workspace status](/docs/screens/status)
- [Settings](/docs/screens/settings)
