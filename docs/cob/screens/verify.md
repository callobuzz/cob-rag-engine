---
route: /verify
audience: public
title: "Verify your email"
topic: getting-started
summary: "Enter the 6-digit code from your e-mail to confirm your address."
starters:
  - "Where is my 6-digit code?"
  - "How long does the code last?"
  - "How do I get a new code?"
  - "I typed the wrong e-mail address. What now?"
---
After you sign up, the engine sends a 6-digit code to your address to check that it is yours. You enter it here. Until you verify, you cannot log in.

## What you see

- "Check your email" and the address the code was sent to.
- Six boxes for the **Verification code** and the note "The code works for 10 minutes and 5 tries."
- **Verify email**.
- **Resend code**, which counts down ("Resend code in 0:42") before you can use it again.
- **Wrong address? Go back** to start sign-up again.

## Common tasks

1. **Verify.** Type the six digits and choose **Verify email**. You are taken on: straight in, or to the "we'll activate you soon" page if the operator approves workspaces by hand.
2. **Get a new code.** Wait for the countdown, then choose **Resend code**. Use the newest e-mail only.
3. **Fix the address.** Choose **Go back** and sign up again with the right e-mail.
4. **Find the code without e-mail.** If the operator has not set up e-mail, they can read it from the server log; ask them.

## Troubleshooting

- **"That code isn't right. Check the latest email"** (`invalid_code`): use the most recent code; each new code replaces the old.
- **"That code has expired or was used up"** (`code_expired`): it lasts 10 minutes and 5 tries. Choose **Send a new code**.
- **`rate_limited`** ("Too many attempts. Try again in N seconds"): wait, then try again.
- **"We don't know which address to verify. Start again from sign-up."**: you opened this page directly; start from Create an account.
- **No e-mail**: check spam; ask the operator whether e-mail is set up (`email_not_configured`).

## Related

- [Create an account](/docs/screens/signup)
- [Sign in](/docs/screens/login)
- [Workspace status](/docs/screens/status)
