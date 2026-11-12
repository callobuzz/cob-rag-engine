---
route: /set-password
audience: public
title: "Set your password"
topic: getting-started
summary: "The first sign-in of the operator's own admin account: choose a password from the link the engine prints or e-mails."
starters:
  - "How do I set the admin password for the first time?"
  - "My set-password link is incomplete or expired. What now?"
  - "Where do I find the set-password link?"
  - "What are the password rules?"
---
The first time the engine starts it creates the operator's super-admin account for the e-mail the operator configured. This page is where that person chooses a password. It is reached from a link, not from the menu.

## What you see

- "Set your admin password" and the address the password is for.
- **New password** with a strength meter and the same rules as everywhere (10 to 128 characters, not your e-mail name, not a very common password).
- **Set password**.
- After success, "Password set. Log in with your new password."
- If the link is incomplete the page says to open the whole link from the e-mail or the server log.

The link carries its secret after a `#`, so it is never sent to a server by the browser. A set-password link is valid once.

## For the platform operator
<!-- audience: admin -->
If the e-mail never arrived or the link has expired, print a fresh one on the server:

```bash
docker compose exec app python -m callobuzz_rag.accounts.bootstrap --resend --print-link
```

Without SMTP the engine prints the link to the console at first start (`docker compose logs app`). `make bootstrap ARGS=--print-link` does the same on a native install.

## Common tasks

1. **Set the password.** Open the full link, choose a password, **Set password**, then log in.
2. **Open the whole link.** Copy the entire address from the e-mail or the log, including the part after `#`.
3. **Ask for a new link.** If it expired, ask whoever runs the server to print a new one.

## Troubleshooting

- **"This link is incomplete"**: the part after `#` was cut off. Copy the whole link.
- **"Ask your operator for a new link"**: the link expired or was used.
- **`weak_password`**: pick a longer or less common password.
- **`code_expired`** or **`invalid_code`**: same cause as an expired link.
- **Already set**: log in at the sign-in page; use Forgot password if you do not remember it.

## Related

- [Sign in](/docs/screens/login)
- [Forgot password](/docs/screens/forgot)
- [Sign in with a key](/docs/screens/login-key)
