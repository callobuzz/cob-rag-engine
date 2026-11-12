"""Online payments (P1): the provider, orders, capture, events, refunds and reconciliation.

Razorpay only moves money; the ledger decides access. Money arrives only through
`capture.apply_captured`.
"""
