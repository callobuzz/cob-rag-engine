"""Transactional e-mail: providers, branded templates and the Celery send path (accounts A2).

Flows call `enqueue_email()` (dispatch.py); the `mailer` service's worker renders the
template with the current branding and sends it through the configured provider.
"""
