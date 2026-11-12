---
route: /admin/email
audience: admin
title: "Email (SMTP)"
topic: operations
summary: "Mail server status, a test e-mail and the list of e-mails the engine sends."
starters:
  - "Why am I not receiving sign-up codes?"
  - "How do I set up e-mail on the engine?"
  - "How do I send a test e-mail?"
  - "What e-mails does the engine send?"
---
Sign-up codes, password resets, invitations and activation e-mails go out through your own mail server. This screen shows whether it is set up and lets you test it. Only the platform operator sees it. The settings are read from the engine's `.env` file, so you change them there and restart.

## What you see

- **Mail server**: a status badge: **SMTP configured**, **Development log** (codes and invitations are printed to the server log, for development only) or **Not configured**. Below it, the host, port, security mode and sender, with "set (never shown)" for the username and password. A warning appears when links in e-mails point somewhere other than this dashboard; set `RAG_DASHBOARD_URL` if that is wrong.
- **Send a test e-mail**: enter an address and choose **Send test email**. It is sent at once with your branding. With the log provider the result says the test was written to the server log.
- **Emails the engine sends**: verification code (valid 10 minutes), password reset code, team invitation (a link that works once, for 7 days), new sign-up (to you), workspace activated, workspace paused, sign-up declined, password changed, "someone tried to sign up with your e-mail", set your password and the test e-mail.

## Common tasks

1. **Set up SMTP.** In `.env` set `RAG_SMTP_HOST`, `RAG_SMTP_PORT` (587 for STARTTLS, 465 for SSL), `RAG_SMTP_SECURITY`, `RAG_SMTP_USERNAME`, `RAG_SMTP_PASSWORD` and `RAG_SMTP_FROM`, then restart the engine and the mailer.
2. **Send a test.** Enter your own address and **Send test email**. Check spam if it does not arrive.
3. **Read codes in development.** With no SMTP, open the mailer's log (`docker compose logs mailer`) and look for the line after "DEV EMAIL".
4. **Hand over an invitation without mail.** Invitations show the accept link on screen so you can send it yourself.

## Troubleshooting

- **`email_not_configured`**: set `RAG_SMTP_*` in `.env` and restart the engine.
- **`smtp_test_failed`**: the mail server refused the test; the message includes its code. Check the host, port, security mode and credentials. For Gmail use an app password.
- **`email_unavailable`**: the e-mail could not be queued. Check that Redis and the mailer service are running, then retry.
- **Links in e-mails open the wrong site**: fix `RAG_DASHBOARD_URL`.
- **In production the log provider is refused**: set a real SMTP host.

## Related

- [Sign-up & branding](/docs/screens/branding)
- [Customers](/docs/screens/customers)
- [Team](/docs/screens/team)
