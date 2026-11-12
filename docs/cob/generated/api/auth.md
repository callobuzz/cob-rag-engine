---
title: Auth API
audience: customer
topic: api
summary: Every auth route of the engine API, with its parameters and a curl example.
---
The **auth** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/auth/change-password`

**Change Password.** Change your password. Every other session ends; this one stays.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Request body** (`application/json`, ChangePasswordRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `current_password` | string | yes |  |
| `new_password` | string | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/change-password" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "example", "new_password": "example"}'
```

## `POST /api/v1/auth/forgot`

**Forgot.** E-mail a password-reset code (use it at /auth/reset). Always the same 202.

**Who can call it:** No credential.

**Request body** (`application/json`, ForgotRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/forgot" \
  -H "Content-Type: application/json" \
  -d '{"email": "example"}'
```

## `POST /api/v1/auth/login`

**Login.** Sign in with e-mail and password. One message for every wrong e-mail or password.

**Who can call it:** No credential.

**Request body** (`application/json`, LoginRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | yes |  |
| `password` | string | yes |  |
| `tenant_id` | string (uuid) or null | no | Workspace to sign in to; default: the earliest you joined. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email": "example", "password": "example"}'
```

## `POST /api/v1/auth/logout`

**Logout.** End the session this refresh token belongs to. 204 whatever the token.

**Who can call it:** No credential.

**Request body** (`application/json`, RefreshRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `refresh_token` | string | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/logout" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "example"}'
```

## `GET /api/v1/auth/me`

**Me.** The signed-in user, the current workspace and every workspace they belong to.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/auth/me" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `PATCH /api/v1/auth/me`

**Patch Me.** Change your display name.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Request body** (`application/json`, ProfilePatch)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/auth/me" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `POST /api/v1/auth/refresh`

**Refresh.** Exchange the refresh token for a new one and a new access token.

Each refresh token works once. Presenting the one just replaced ends the session.

**Who can call it:** No credential.

**Request body** (`application/json`, RefreshRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `refresh_token` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/refresh" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "example"}'
```

## `POST /api/v1/auth/resend`

**Resend.** Send a new verification or reset code, if one applies. Always the same 202.

**Who can call it:** No credential.

**Request body** (`application/json`, ResendRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | yes |  |
| `purpose` | "verify" \| "reset" | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/resend" \
  -H "Content-Type: application/json" \
  -d '{"email": "example", "purpose": "verify"}'
```

## `POST /api/v1/auth/reset`

**Reset Password.** Set a new password with an e-mailed code (reset, or the super admin's first one).

Ends every session of the account.

**Who can call it:** No credential.

**Request body** (`application/json`, ResetRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes | The 6-digit code e-mailed. |
| `email` | string | yes |  |
| `new_password` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/reset" \
  -H "Content-Type: application/json" \
  -d '{"code": "example", "email": "example", "new_password": "example"}'
```

## `GET /api/v1/auth/sessions`

**List Sessions.** Your active sessions, newest first.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/auth/sessions" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/auth/sessions/revoke-others`

**Revoke Others.** End every session except this one.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/sessions/revoke-others" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `DELETE /api/v1/auth/sessions/{session_id}`

**Delete Session.** End one of your sessions (your current one = sign out).

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `session_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/auth/sessions/<session_id>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/auth/signup`

**Signup.** Sign up a new workspace: e-mails a 6-digit code to verify the address.

The same 202 for every address (an existing account gets an e-mail saying so
instead). 403 `signup_closed` in closed mode, with the operator's contact text.

**Who can call it:** No credential.

**Request body** (`application/json`, SignupRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `accept_legal` | AcceptLegal or null | no | The Terms and Privacy versions you agree to; required when the operator has published both pages |
| `business_name` | string | yes | The workspace's name. |
| `email` | string | yes |  |
| `name` | string | yes | Your name. |
| `password` | string | yes |  |
| `plan` | string or null | no | The plan chosen on the pricing page (`?plan=<slug or id>`); only a plan businesses may choose themselves counts, anything else starts on the default plan |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"business_name": "example", "email": "example", "name": "example", "password": "example"}'
```

## `POST /api/v1/auth/switch-admin`

**Switch Admin.** Super admin only: leave the operator workspace and return to the admin (operator)
view. Returns a new access token with no workspace (`tenant: null`). Harmless when
already in the admin view; 403 `not_superadmin` for anyone else.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/switch-admin" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/auth/switch-tenant`

**Switch Tenant.** Act for another workspace you belong to. Returns a new access token for it.

The super admin may enter exactly one workspace this way: the operator's own
(`is_operator`, where they are the owner). The session then acts as that workspace's
owner and nothing more: it is a plain workspace scope and cannot reach admin routes.
`POST /auth/switch-admin` goes back; any other tenant is 403 `not_member`.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Request body** (`application/json`, SwitchTenantRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/switch-tenant" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "<uuid>"}'
```

## `POST /api/v1/auth/verify`

**Verify.** Verify the address with the e-mailed code: creates the workspace and signs in.

`tenant.status` is `active` (open sign-up) or `pending` (waiting for approval).

**Who can call it:** No credential.

**Request body** (`application/json`, VerifyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes | The 6-digit code e-mailed. |
| `email` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/auth/verify" \
  -H "Content-Type: application/json" \
  -d '{"code": "example", "email": "example"}'
```
