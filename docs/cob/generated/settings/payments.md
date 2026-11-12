---
title: Payments settings
audience: admin
topic: settings
summary: Environment variables in the Payments group of the engine's .env file.
---
Environment variables in the **Payments** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_PAYMENTS_PROVIDER`

razorpay or none; empty = use the dashboard setting.

Default: empty.

## `RAG_RAZORPAY_KEY_ID`

Overrides the stored key id (rzp_test_... / rzp_live_...).

Default: empty.

## `RAG_RAZORPAY_KEY_SECRET`

Overrides the stored Razorpay key secret.

Default: secret, no default.

## `RAG_RAZORPAY_WEBHOOK_SECRET`

Overrides the stored Razorpay webhook secret.

Default: secret, no default.
