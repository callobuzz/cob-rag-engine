---
route: /reset
audience: public
title: "Reset password"
topic: getting-started
summary: "Enter the e-mailed code and choose a new password; it signs you out on every other device."
starters:
  - "What are the rules for a new password?"
  - "Why am I signed out of my other devices?"
  - "My reset code says it expired. What do I do?"
  - "Where do I enter the code?"
---
This is the second step of a password reset. You enter the code that was e-mailed to you and a new password. The page may arrive with the address filled in from the link in the e-mail.

## What you see

- "Choose a new password" and a line saying where the code was sent.
- A **Verification code** field.
- **New password** and **Repeat new password**, with a strength meter, a list of what is still needed, and a live message "Passwords match" or "Passwords don't match". The rules: 10 to 128 characters, not your e-mail name, not a very common password (checked when you save).
- A reminder that changing your password signs you out on every other device.
- **Save new password**.

Links from e-mails put secret values after the `#` in the address, which the browser never sends to a server, so a link in your history does not expose a code.

## Common tasks

1. **Reset.** Type the code, the new password twice, then **Save new password**. You are taken to log in with a note that your other devices were signed out.
2. **Start over.** If the code is stale, go back to Forgot password and request a new one.

## Troubleshooting

- **"That code isn't right"** (`invalid_code`): use the latest e-mail.
- **"That code has expired or was used up"** (`code_expired`): codes last 10 minutes and 5 tries; request a new one.
- **`weak_password`**: the message lists why. Choose a longer or less common password.
- **"Passwords don't match"**: retype the second field.
- **`rate_limited`**: wait the time shown.
- **After resetting you were logged out everywhere**: that is intended, to protect the account.

## Related

- [Forgot password](/docs/screens/forgot)
- [Sign in](/docs/screens/login)
- [Profile & sessions](/docs/screens/profile)
