---
title: Tenants API
audience: customer
topic: api
summary: Every tenants route of the engine API, with its parameters and a curl example.
---
The **tenants** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/admin/operator-workspace/restore`
<!-- audience: admin -->

**Restore Operator Workspace.** "Recreate my workspace": bring back the operator's own workspace after it was deleted
on purpose (a deleted one stays deleted across restarts; this clears that and recreates
it, as `bootstrap --restore-operator-workspace` does). Admin key or super-admin session.
409 `operator_workspace_exists` when there is nothing to recreate.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `201` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/operator-workspace/restore" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/tenants`
<!-- audience: admin -->

**List Tenants.** List tenants, newest first, with owners, member and corpus counts. Admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | "pending" \| "active" \| "suspended" or null | no | Only tenants in this status |
| `search` | query | string or null | no | Case-insensitive part of the name |
| `is_operator` | query | boolean or null | no | false: customers only (without the operator's own workspace); true: only the operator workspace |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants`
<!-- audience: admin -->

**Create Tenant.** Create an active tenant with a dedicated API key.

- Generates a unique API key (returned once — store securely)
- On `plan_id` (an active plan) or else the default plan; `max_corpora` overrides the
  plan's knowledge-base limit (omit it to follow the plan)
- Requires admin authentication

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CreateTenantRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string or null | no |  |
| `max_corpora` | integer or null | no | Per-tenant knowledge-base limit overriding the plan's; null follows the plan |
| `name` | string | yes |  |
| `plan_id` | string (uuid) or null | no | An active plan; default: the default plan |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `GET /api/v1/admin/tenants/{tenant_id}`
<!-- audience: admin -->

**Get Tenant.** Tenant detail with owners, member and corpus counts. Admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/tenants/{tenant_id}`
<!-- audience: admin -->

**Update Tenant.** Update name, description, max_corpora; `is_active` suspends or reactivates.

`max_corpora` overrides the plan's knowledge-base limit; `null` removes the override.
Lowering it below the tenant's count blocks new knowledge bases only.

`is_active: false` suspends an active tenant and needs `confirm_name` (its current
name) in the same body; `true` reactivates a suspended one. A value the tenant
already has changes nothing. A pending tenant is activated with `POST .../activate`.
The operator's own workspace cannot be suspended (409 `operator_workspace_protected`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateTenantRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `confirm_name` | string or null | no | The tenant's current name, typed; required with is_active: false |
| `description` | string or null | no |  |
| `is_active` | boolean or null | no | false suspends an active tenant (needs confirm_name), true reactivates a suspended one; a pending tenant is activated with POST .../activate |
| `max_corpora` | integer or null | no | Knowledge-base override; an explicit null removes it (follow the plan) |
| `name` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/tenants/{tenant_id}`
<!-- audience: admin -->

**Delete Tenant.** Delete a tenant and everything it owns: its knowledge bases (with their vectors
and files), members, and the accounts that belonged only to it. Any status.

Requires `?confirm_name=<the tenant's name>`; a call without it is a 422 and
deletes nothing (this route used to deactivate: use `POST .../suspend` for that).
The operator's own workspace (`is_operator`) also needs
`?confirm_operator_workspace=true`; once deleted it is not recreated at the next start.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `confirm_name` | query | string or null | no | The tenant's name, typed exactly (case-sensitive). Required with an API key and with a session alike; GET .../delete-preview says what would be removed. |
| `confirm_operator_workspace` | query | boolean | no | Also required to delete the operator's own workspace (422 operator_confirmation_required without it) |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/activate`
<!-- audience: admin -->

**Activate Tenant.** Activate a pending sign-up on a plan (`plan_id`; default: the plan it has, which
is the default plan unless changed), optionally with a note (e.g. how they paid).
The owners get a "you're in" e-mail naming the plan.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/activate" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/admin/tenants/{tenant_id}/decline`
<!-- audience: admin -->

**Decline Tenant.** Decline a pending sign-up: the tenant and its sign-up account are removed and
the owner gets a polite e-mail (the reason stays in the audit log).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/decline" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/tenants/{tenant_id}/delete-preview`
<!-- audience: admin -->

**Preview Delete Tenant.** What deleting this customer would remove, and the name to confirm with. Changes
nothing.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/delete-preview" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/tenants/{tenant_id}/plan`
<!-- audience: admin -->

**Get Tenant Plan.** A tenant's plan, effective limits and usage this month. Admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/plan" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/plan`
<!-- audience: admin -->

**Set Tenant Plan.** Move a tenant to another active plan (any status). Takes effect on the next
request; a lower limit blocks new additions only and deletes nothing.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetTenantPlanRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `business_agreed` | boolean | no | Default `false`. |
| `plan_id` | string (uuid) | yes |  |
| `reason` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/plan" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "<uuid>"}'
```

## `POST /api/v1/admin/tenants/{tenant_id}/reactivate`
<!-- audience: admin -->

**Reactivate Tenant.** Reactivate a suspended tenant. Its keys work again on their next request.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/reactivate" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/rotate-key`
<!-- audience: admin -->

**Rotate Tenant Key.** Replace the tenant's default key. The old one is immediately invalidated.

Returns the new key (shown only once — store securely). Other named keys
(`/admin/tenants/{id}/keys`) are untouched; with none, this issues the first one.
Requires admin authentication.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/rotate-key" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/suspend`
<!-- audience: admin -->

**Suspend Tenant.** Suspend an active tenant: every API key, widget and sign-in of it stops at once.

`confirm_name` must be the tenant's name exactly (422 `confirm_mismatch`). The
operator's own workspace (`is_operator`) is never suspended: 409
`operator_workspace_protected`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SuspendTenantRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `confirm_name` | string | yes | The tenant's name, typed exactly (case-sensitive) |
| `reason` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/suspend" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"confirm_name": "example"}'
```

## `GET /api/v1/tenants/me`

**Get Own Tenant.** The calling tenant's own record: name, status, limits, corpus count.

Also answers a signed-in user of a pending or suspended workspace (its status page).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/tenants/me" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/tenants/me/plan`

**Get Own Plan.** The calling tenant's plan, its effective limits and usage this month ("412 of
1,000 questions this month"). Owners and members alike.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/tenants/me/plan" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/tenants/me/rotate-key`

**Rotate Own Tenant Key.** Replace the calling tenant's default key. The new key is shown once; the old one stops
now. Owners only. Other named keys (`/tenants/me/keys`) are untouched.

The auth middleware remembers a verified key only together with the hash it
verified against, so replacing the hash ends the old key on its next request.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/tenants/me/rotate-key" \
  -H "X-API-Key: $COB_RAG_KEY"
```
