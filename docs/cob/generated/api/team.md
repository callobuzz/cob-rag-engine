---
title: Team API
audience: customer
topic: api
summary: Every team route of the engine API, with its parameters and a curl example.
---
The **team** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/admin/tenants/{tenant_id}/invitations`
<!-- audience: admin -->

**Admin Invite.** Invite someone to any tenant. Admin only.

The response carries `accept_url`: send it yourself if the engine has no SMTP (the
e-mail goes out too when it does), e.g. to onboard a customer's owner in closed mode.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, InviteRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string (email) | yes |  |
| `role` | "owner" \| "member" | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/invitations" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "role": "owner"}'
```

## `DELETE /api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}`
<!-- audience: admin -->

**Admin Revoke Invitation.** Revoke a tenant's open invitation. Admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `invitation_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/invitations/<invitation_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}/resend`
<!-- audience: admin -->

**Admin Resend Invitation.** A new link and 7 more days for a tenant's open invitation (the old link stops
working). Admin only; like the admin invitation, the response carries `accept_url` and
the e-mail goes out too when the engine has SMTP.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `invitation_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/invitations/<invitation_id>/resend" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/tenants/{tenant_id}/members`
<!-- audience: admin -->

**Admin List Team.** A tenant's members and open invitations. Admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/members" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/tenants/{tenant_id}/members/{user_id}`
<!-- audience: admin -->

**Admin Change Role.** Change a member's role in any tenant. Admin only; the last owner stays an owner.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `user_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RoleChangeRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `role` | "owner" \| "member" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/members/<user_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role": "owner"}'
```

## `DELETE /api/v1/admin/tenants/{tenant_id}/members/{user_id}`
<!-- audience: admin -->

**Admin Remove Member.** Remove someone from any tenant (e.g. the owner left the company, after promoting
someone else). Admin only; the last owner cannot be removed.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `user_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/members/<user_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/invitations/accept`

**Accept Invitation.** Accept an invitation.

- Signed in (`Authorization: Bearer`) as the invited address: joins the workspace and
  answers `{"accepted": true, "tenant": ...}` (then `POST /auth/switch-tenant`).
- Not signed in, no account yet: send `name` and `password`; the account is created
  (already verified), joins, and the answer is a sign-in (`TokenResponse`).
- Not signed in, account exists: 409 `account_exists` (sign in, then accept).

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Request body** (`application/json`, AcceptInvitationRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string or null | no | Your name (new accounts only). |
| `password` | string or null | no | Your password (new accounts only). |
| `token` | string | yes | The inv_... token. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/invitations/accept" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"token": "example"}'
```

## `POST /api/v1/invitations/view`

**View Invitation.** What the accept page shows: workspace, role, address, and whether that address
already has an account (then sign in to accept). 404 `invitation_invalid` for an
unknown, expired, revoked or used link.

**Who can call it:** No credential.

**Request body** (`application/json`, InvitationTokenRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The inv_... token. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/invitations/view" \
  -H "Content-Type: application/json" \
  -d '{"token": "example"}'
```

## `DELETE /api/v1/tenants/me/invitations/{invitation_id}`

**Revoke Own Invitation.** Revoke an open invitation: its link stops working. Owners only.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `invitation_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/tenants/me/invitations/<invitation_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/tenants/me/invitations/{invitation_id}/resend`

**Resend Own Invitation.** E-mail the invitation again with a new link and 7 more days. Owners only.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `invitation_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/tenants/me/invitations/<invitation_id>/resend" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/tenants/me/members`

**List Own Team.** The workspace's members and open invitations. Any member of the workspace.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/tenants/me/members" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/tenants/me/members`

**Invite Member.** Invite someone by e-mail as `owner` or `member`. Owners only.

They get a link valid 7 days. Inviting an address that has an open invitation
replaces it (the old link stops working). The link is only ever e-mailed.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Request body** (`application/json`, InviteRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string (email) | yes |  |
| `role` | "owner" \| "member" | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/tenants/me/members" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "role": "owner"}'
```

## `PATCH /api/v1/tenants/me/members/{user_id}`

**Change Own Member Role.** Make someone an owner or a member. Owners only; the last owner stays an owner.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `user_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RoleChangeRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `role` | "owner" \| "member" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/tenants/me/members/<user_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role": "owner"}'
```

## `DELETE /api/v1/tenants/me/members/{user_id}`

**Remove Own Member.** Remove someone from the workspace (owners), or leave it (anyone, with their own
user id). Their sessions in this workspace end; the account stays. The last owner
cannot leave.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `user_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/tenants/me/members/<user_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
