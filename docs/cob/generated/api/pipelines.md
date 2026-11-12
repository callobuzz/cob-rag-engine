---
title: Pipelines API
audience: customer
topic: api
summary: Every pipelines route of the engine API, with its parameters and a curl example.
---
The **pipelines** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/pipelines`
<!-- audience: admin -->

**List Profiles.** Every pipeline profile, the default first, with how many plans and businesses use each
and what its blank fields mean right now.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/pipelines" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/pipelines`
<!-- audience: admin -->

**Create Profile.** Create a profile. 422 `invalid_profile` names the rule when the settings do not fit
together; 409 `profile_name_taken` when the name is in use.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CreateProfileRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `config` | object | no | The profile's settings (see ProfileConfig). A field left out or null follows the knowledge base's own setting, then the engine's. Replaces the whole config on update. |
| `description` | string or null | no |  |
| `label` | string | yes |  |
| `name` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/pipelines" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "example", "name": "example"}'
```

## `GET /api/v1/admin/pipelines/lanes`
<!-- audience: admin -->

**Get Lanes State.** The answer lanes right now: slots and how many are taken, by priority. Best effort:
when Redis cannot be read the pools show as idle.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/pipelines/lanes" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/pipelines/tenants/{tenant_id}`
<!-- audience: admin -->

**Get Tenant Pipeline.** The profile a business answers with, and where it comes from (its own override, its
plan's, or the default).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/pipelines/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/pipelines/tenants/{tenant_id}`
<!-- audience: admin -->

**Set Tenant Pipeline.** Give a business a profile of its own, or send `profile_id: null` to go back to its
plan's.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetTenantPipelineRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `profile_id` | string (uuid) or null | no | A profile for this business; null goes back to its plan's |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/pipelines/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/pipelines/{profile_id}`
<!-- audience: admin -->

**Get Profile.**

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `profile_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/pipelines/<profile_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/pipelines/{profile_id}`
<!-- audience: admin -->

**Update Profile.** Change the fields sent; `config` replaces the whole config.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `profile_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateProfileRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `config` | object or null | no | The profile's settings (see ProfileConfig). A field left out or null follows the knowledge base's own setting, then the engine's. Replaces the whole config on update. |
| `description` | string or null | no |  |
| `label` | string or null | no |  |
| `name` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/pipelines/<profile_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/pipelines/{profile_id}`
<!-- audience: admin -->

**Delete Profile.** Delete a profile no plan or business uses (409 `profile_in_use` otherwise); never the
default (409 `profile_is_default`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `profile_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/pipelines/<profile_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/pipelines/{profile_id}/clone`
<!-- audience: admin -->

**Clone Profile.** A copy of a profile under a new name: never the default, never a built-in.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `profile_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CloneProfileRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string or null | no | Default: the source's |
| `name` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/pipelines/<profile_id>/clone" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `POST /api/v1/admin/pipelines/{profile_id}/default`
<!-- audience: admin -->

**Make Default.** Make this the default profile: plans that pick none answer with it. The old default
stops being one in the same step.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `profile_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/pipelines/<profile_id>/default" \
  -H "X-API-Key: $COB_RAG_KEY"
```
