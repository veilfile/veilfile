---
title: API reference
slug: api
seo_title: API reference | Veilfile docs
description: Every Veilfile endpoint: upload, list, revoke, keys, usage, billing, and the MCP server, with request and response shapes.
order: 5
---

## Auth

If you are an agent, the [llms.txt quickstart](/llms.txt) covers this page in machine-readable form.

Every request carries your API key in a header:

```
Authorization: Bearer vlf_...
```

Keys start with `vlf_` and are shown once at creation. Keep the key in an environment variable (`VEILFILE_API_KEY`) or a secret manager. Never put it in a prompt, a chat message, or a repo.

The `veilfile` command-line client gets you a key without pasting secrets: `pip install veilfile`, then `veilfile login` (browser approval flow), `veilfile logout`, and `veilfile status`. The full reference is in the [API docs source](https://github.com/veilfile/veilfile/blob/main/docs/API.md).

## Upload

`POST /api/v1/artifacts`

Multipart form:

| Field | Required | Notes |
|---|---|---|
| `file` | yes | The file bytes. Max 25 MiB. |
| `ttl_days` | no | Whole days until expiry. Defaults to the plan max. Larger values are clamped to the plan max; a non-integer gets `400` `invalid_request`. |
| `on_secret` | no | `flag` (default) or `reject`. `reject` refuses a file that trips the secret scanner (`400` `secret_detected`) and stores nothing. |

Allowed types: png, jpg, jpeg, webp, gif, txt, md, log, json, har, pdf.

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

`ttl_adjusted: true` appears when your `ttl_days` was clamped to the plan max. A `warning` string appears when `secret_flags` is non-empty: ask the user whether to keep or revoke the upload before sharing the link.

## List

`GET /api/v1/artifacts`

Returns your workspace's artifacts, newest first, as a bare JSON array of `{id, filename, mime_type, size_bytes, url, expires_at, secret_flags, created_at}`. Optional `limit` (1 to 200) and `cursor` page through it; when more remain the response has `X-Next-Cursor` and `Link: rel="next"` headers.

## Revoke

`DELETE /api/v1/artifacts/<id>`

Deletes the artifact at once. Its URL returns `404` immediately. Success is a `204` with an empty body. Unknown ids return `404` with `{"error": "Artifact not found.", "code": "not_found"}`.

## Download

`GET /a/<token>`

Public. No auth. The token in the URL is the auth. Images, PDFs, and plain text (`.txt`, `.log`, `.md`) are served inline, with the type taken from the file extension. Everything else (JSON, HAR) downloads as an attachment. `404` means unknown, revoked, or expired; `503` with `Retry-After` means storage is briefly unavailable and the link is still valid. Served with `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`.

## Errors

Errors are JSON: `{"error": "<message>", "code": "<code>"}`. Branch on `code`.

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `file_type_not_allowed` | Type not in the allowlist |
| 400 | `secret_detected` | `on_secret=reject` and the file looks like it holds a secret |
| 400 | `invalid_request` | Bad parameter (for example a non-integer `ttl_days`) |
| 401 | `unauthorized` | Missing or invalid API key |
| 402 | `storage_cap_reached` | Plan storage cap hit |
| 404 | `not_found` | Unknown, revoked, or expired |
| 413 | `file_too_large` | Over 25 MiB |
| 429 | `monthly_cap_reached` | Monthly upload quota hit |
| 429 | `rate_limited` | Too many requests. Back off; see `Retry-After`. |

Rate limit: 100 uploads per hour per credential, shared by REST and MCP uploads.

The full technical reference, including MCP and billing endpoints, is [API.md](https://github.com/veilfile/veilfile/blob/main/docs/API.md).
