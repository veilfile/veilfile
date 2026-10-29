---
title: API reference
slug: api
seo_title: API reference | Veilfile docs
description: This page documents the upload, list, revoke, and download endpoints, with request and response shapes and error codes.
order: 5
---

## Auth

If you are an agent, the [llms.txt quickstart](/llms.txt) covers this page in machine-readable form.

Every request carries your API key in a header:

```
Authorization: Bearer vlf_...
```

Keys start with `vlf_` and are shown once at creation. Keep the key in an environment variable (`VEILFILE_API_KEY`) or a secret manager. Never put it in a prompt, a chat message, or a repo.

The `veilfile` command-line client gets you a key without pasting secrets. Install it with `pip install veilfile`, run `veilfile login` to approve in your browser, and use `veilfile logout` to revoke the key and `veilfile status` to check it. The full reference is in the [API docs source](https://github.com/veilfile/veilfile/blob/main/docs/API.md).

## Upload

`POST /api/v1/artifacts`

Multipart form:

| Field | Required | Notes |
|---|---|---|
| `file` | yes | Send the file bytes. The maximum size is 25 MiB. |
| `ttl_days` | no | Set the number of whole days until expiry. The default is the plan max. Larger values are clamped to the plan max; a non-integer gets `400` `invalid_request`. |
| `on_secret` | no | `flag` (default) or `reject`. `reject` refuses a file that trips the secret scanner (`400` `secret_detected`) and stores nothing. |

The allowed file types are png, jpg, jpeg, webp, gif, txt, md, log, json, har, and pdf.

```bash
curl -X POST https://veilfile.com/api/v1/artifacts \
  -H "Authorization: Bearer $VEILFILE_API_KEY" \
  -F "file=@screenshot.png" \
  -F "ttl_days=7"
```

A `201` comes back:

```json
{
  "id": "b3f7c2a1-...",
  "url": "https://veilfile.com/a/4fK9vX2qZt8sW...",
  "expires_at": "2026-10-12T00:25:00Z",
  "size_bytes": 184320,
  "secret_flags": []
}
```

`ttl_adjusted: true` appears when your `ttl_days` was clamped to the plan max. A `warning` string appears when `secret_flags` is non-empty. It reads: "The scan flagged this file as possibly containing a secret; see secret_flags. Ask the user whether to keep or revoke the file before sharing its link."

## List

`GET /api/v1/artifacts`

The endpoint returns your workspace's artifacts, newest first, as a bare JSON array of `{id, filename, mime_type, size_bytes, url, expires_at, secret_flags, created_at}`. Optional `limit` (1 to 200) and `cursor` page through it; when more remain the response has `X-Next-Cursor` and `Link: rel="next"` headers.

## Revoke

`DELETE /api/v1/artifacts/<id>`

The endpoint deletes the artifact at once. Its URL returns `404` immediately. Success is a `204` with an empty body. Unknown ids return `404` with `{"error": "No file with that id exists in this workspace. Check the id.", "code": "not_found"}`.

## Download

`GET /a/<token>`

This endpoint is public and needs no auth header. The token in the URL is the auth. Images, PDFs, and plain text (`.txt`, `.log`, `.md`) are served inline, with the type taken from the file extension. Everything else (JSON, HAR) downloads as an attachment. `404` means unknown, revoked, or expired; `503` with `Retry-After` means storage is briefly unavailable and the link is still valid. A successful download carries `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`.

## Errors

Errors are JSON: `{"error": "<message>", "code": "<code>"}`. Branch on `code`.

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `file_type_not_allowed` | The file type is not in the allowed list. |
| 400 | `secret_detected` | The scan found secret-shaped content and `on_secret` is `reject`, so nothing was stored. |
| 400 | `invalid_request` | A parameter is not valid, for example a `ttl_days` that is not a whole number. |
| 401 | `unauthorized` | The API key is missing or not valid. |
| 402 | `storage_cap_reached` | The workspace has reached its plan's storage cap. Revoke files you no longer need, or upgrade. |
| 404 | `not_found` | The id or token is unknown, or the file is revoked or expired. |
| 413 | `file_too_large` | The file is larger than 25 MiB. |
| 429 | `monthly_cap_reached` | The workspace has reached its plan's monthly upload cap. Uploads work again next month, or you can upgrade. |
| 429 | `rate_limited` | There were too many requests. Wait the number of seconds in `Retry-After`, then try again. |

The rate limit is 100 uploads per hour for each API key or connected app, and REST and MCP uploads share it.

The full technical reference, including MCP and billing endpoints, is [API.md](https://github.com/veilfile/veilfile/blob/main/docs/API.md).
