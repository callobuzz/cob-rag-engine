---
title: Storage settings
audience: admin
topic: settings
summary: Environment variables in the Storage group of the engine's .env file.
---
Environment variables in the **Storage** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_STORAGE_PROVIDER`

local | s3. `local` keeps files in a folder on this server (Docker: the `files` volume); nothing else to run. Use `s3` for a managed service or when several servers share the files.

Default: `local`.

## `RAG_STORAGE_LOCAL_ROOT`

Folder for `local`. Native runs use data/files under the project; Docker Compose sets its own.

Default: `data/files`.

## `RAG_S3_BUCKET`

Bucket name (also the folder name under the files folder for `local`).

Default: `rag-documents`.

## `RAG_S3_ENDPOINT`

Only when RAG_STORAGE_PROVIDER=s3: any S3-compatible service (Cloudflare R2, AWS S3, Supabase, Backblaze B2, DigitalOcean Spaces, or an S3 server you run yourself). Set the endpoint and keys, and RAG_S3_ENDPOINT_CONTAINER to the same address: R2:       endpoint https://<ACCOUNT_ID>.r2.cloudflarestorage.com, region auto AWS S3:   leave the endpoint empty, region <REGION>, addressing virtual Supabase: endpoint https://<PROJECT>.supabase.co/storage/v1/s3, addressing path B2 / Vultr / DO Spaces: addressing path Move existing files into the folder (or back) with: python -m callobuzz_rag.storage.copy --from s3 --to local --apply Where this machine reaches the S3 service.

Default: empty.

## `RAG_S3_ENDPOINT_CONTAINER`

Where the containers reach it.

Default: empty. Read by Docker Compose, not the engine.

## `RAG_S3_ACCESS_KEY`

Access key and secret key of your S3 service.

Default: secret, no default.

## `RAG_S3_SECRET_KEY`

Access key and secret key of your S3 service.

Default: secret, no default.

## `RAG_S3_REGION`

Region: R2 needs `auto`; AWS needs the bucket's real region.

Default: `us-east-1`.

## `RAG_S3_ADDRESSING_STYLE`

path | virtual | auto. Leave auto unless your provider serves only one style.

Default: `auto`.

## `RAG_S3_AUTO_CREATE_BUCKET`

true | false. Create the bucket on start if it is missing. Usually false with a scoped cloud key.

Default: `false`.
