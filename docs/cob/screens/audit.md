---
route: /admin/audit
audience: admin
title: "Audit log"
topic: operations
summary: "Who did what: activations, suspensions, role and plan changes, settings changes and sign-in trouble."
starters:
  - "How do I see who suspended a customer?"
  - "How far back does the audit log go?"
  - "Can an audit entry be deleted?"
  - "How do I filter the audit log by customer?"
---
The audit log records who did what on your engine: activations, suspensions, role changes, plan and settings changes, key changes and sign-in trouble. It is kept for good and nobody can delete an entry. Only the platform operator sees it.

## What you see

- **Filters**: **Action** (grouped, or a single action), **Who** (an e-mail or key), **Customer**, and **From** and **To** dates. **Apply** and **Clear filters**.
- A table with **Time**, **Who** (the kind of actor: Person, Admin key, Workspace key, KB key, System), **What**, **Customer**, **On** (the target), **Details** (expand to read) and **IP**.
- By default the last 90 days. Pick dates to look further back, up to 366 days at a time.

Actions you will see include `tenant.signed_up`, `tenant.activated`, `tenant.suspended`, `tenant.reactivated`, `tenant.declined`, `tenant.deleted`, `tenant.plan_changed`, `member.invited`, `member.role_changed`, `member.removed`, `plan.created`, `plan.updated`, `api_key.created`, `api_key.rotated`, `api_key.revoked`, `assistant.published`, `lead.exported`, `lead.deleted`, `settings.updated`, `user.password_changed` and `user.login_locked`. Starting a COB docs resync is recorded as `cob.resync_requested`; changing who a platform page is for, or deleting one, as `cob.platform_doc_audience` and `cob.platform_doc_deleted`.

## Common tasks

1. **Who suspended this customer?** Filter Action to `tenant.suspended`, or open the customer's **History** link from Customers.
2. **What did one person do?** Type their e-mail in **Who**, **Apply**.
3. **Check a key change.** Filter by the key actions group and read the Details for the key name.
4. **Look back a quarter.** Set From and To up to 366 days apart.

## Troubleshooting

- **"Nothing matches these filters in this time range"**: clear a filter or widen the dates.
- **"Nothing has been recorded in the last 90 days"**: normal on a quiet engine.
- **"Pick a start date on or before the end date"** and **"Pick at most 366 days at a time"**: fix the range.
- **IP shows a proxy address**: set `RAG_TRUST_PROXY_HEADERS` and `RAG_TRUSTED_PROXY_HOPS` so the real address is recorded.
- **"Couldn't load the audit log"**: reload; if it persists check the engine.

## Related

- [Customers](/docs/screens/customers)
- [Customer](/docs/screens/customer-detail)
- [Engine info](/docs/screens/engine)
