---
route: /login
audience: public
title: "Sign in"
topic: getting-started
summary: "Log in with your e-mail and password, reset a forgotten password, or find another way in."
starters:
  - "How do I log in?"
  - "I forgot my password. What do I do?"
  - "Why does it say too many tries?"
  - "Why does it say my workspace is paused or waiting?"
---
The sign-in page is where you and your team log in to the dashboard with the e-mail you signed up with. Each person has their own login. If you have never used the dashboard, create an account first, or accept the invitation you were sent.

## What you see

- **Email** and **Password** fields and a **Log in** button.
- **Forgot password?** which starts a reset by e-mail.
- **Keep me signed in on this device**: without it you are signed out when the tab closes. Use it only on a device you trust, never on a shared front-desk computer.
- **New to the product? Create an account** and, when the operator runs closed sign-up, **Talk to us** instead.
- **Use an API key instead (for developers)**, a link to the key sign-in.
- **Ask COB**, a button at the bottom-right (when the operator has it on). See Ask COB below.
- Branding set by the platform operator: the product name, logo and colour.

If you belong to more than one workspace you land in the first and can switch from the account menu.

## Ask COB

Before you sign in, **Ask COB** opens a chat about the engine itself: what it is, whether you can run it on your own server, how to sign up or log in. It answers only from the public help pages. It cannot see any account, change anything or open a signed-in screen, so for questions about your own workspace sign in and ask COB there.

- The chat is not saved. Close it or reload the page and it is gone. The next question carries only the earlier messages of this page view, at most six.
- Personal details you type, such as a PAN or card number, are hidden before COB reads the chat.
- A question that only signed-in help pages can answer gets "Sign in to your workspace and ask me there".
- It is limited per visitor each hour and for the whole engine each day, because the platform operator pays for the answers. If you hit the limit it says how long to wait. If the operator has switched it off, the button is not there.
- The same button is on the sign-up, forgot-password, reset, verify, invitation and workspace-status pages.

## Common tasks

1. **Log in.** Enter your e-mail and password and choose **Log in**. You land on Overview.
2. **Reset a forgotten password.** Choose **Forgot password?**, enter your e-mail and follow the code you receive.
3. **Verify a new account.** If you signed up but did not verify, log in will say "Verify your email first" and offer to send a new code.
4. **Use an API key.** Developers and the platform operator's recovery login can choose the key sign-in instead.

## Troubleshooting

- **"Email or password isn't right"** (`invalid_credentials`): check both, or reset your password.
- **"Too many tries. Login is paused for N minutes"** (`account_locked`) or **`rate_limited`**: wait the time shown; repeated wrong passwords pause the account for a short while to protect it.
- **"Verify your email first"** (`email_not_verified`): choose **Send a new code**.
- **"We'll activate your workspace soon"**: a new workspace waits for the operator to activate it. You will get an e-mail. See Workspace status.
- **"Your workspace is paused"**: the operator has suspended it. Your documents are safe; contact them.
- **"Email sign-in isn't set up on this engine yet"** (`accounts_not_configured`): use an API key instead.
- **`no_workspace`**: your account is not in any workspace. Ask the owner to invite you again.
- **"Can't reach the engine"** (`network`): the engine is down or this address is not allowed.
- **Ask COB says "You've asked a lot of questions"** (`cob_public_rate_limited`): wait the time shown. **"COB has answered a lot of questions today"** means the whole engine reached its daily limit.
- **Ask COB says it is briefly unavailable** (`cob_public_unavailable`): the engine's rate-limit store is down. Try again in a minute.

## Related

- [Create an account](/docs/screens/signup)
- [Forgot password](/docs/screens/forgot)
- [Sign in with a key](/docs/screens/login-key)
- [Workspace status](/docs/screens/status)
