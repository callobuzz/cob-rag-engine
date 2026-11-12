---
route: /forgot
audience: public
title: "Forgot password"
topic: getting-started
summary: "Enter your e-mail and get a code to reset your password."
starters:
  - "How do I reset my password?"
  - "Why didn't I get a reset e-mail?"
  - "Does the reset code expire?"
  - "Will resetting log me out of my other devices?"
---
If you cannot remember your password, enter your e-mail here. The engine sends a code you use on the next screen to choose a new one.

## What you see

- "Reset your password" and a single **Email** field.
- **Send code**.
- A line explaining privacy: if there is no account for that address nothing is sent, and the page does not say either way. This stops other people from checking who has an account.
- A link back to log in.

## Common tasks

1. **Ask for a code.** Enter the e-mail you signed up with and choose **Send code**. The page then moves to Reset password.
2. **Check your inbox.** The e-mail has a 6-digit code. It works for 10 minutes. Check spam.
3. **Try again.** You can ask for a new code; the newest one is the only one that works.

## Troubleshooting

- **No e-mail arrives**: either there is no account for that address, or the engine's e-mail is not set up (`email_not_configured`). Check spelling, check spam, and ask the platform operator.
- **`rate_limited`**: "Too many attempts. Try again in N minutes."
- **`rate_limiter_unavailable`**: "Sign-in is briefly unavailable. Try again in a minute."
- **"Email isn't set up on this engine, so codes can't be sent"**: the operator has to configure SMTP; they can also reset it for you.
- **You do not remember which e-mail you used**: try your work addresses one by one, or ask the person who invited you. Each sign-up uses one address, so the code only goes to the address that holds the account.

## Related

- [Reset password](/docs/screens/reset)
- [Sign in](/docs/screens/login)
- [Verify your e-mail](/docs/screens/verify)
