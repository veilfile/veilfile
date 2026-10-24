---
title: How it works
slug: how-it-works
seo_title: How it works | Veilfile docs
description: Plans, TTLs, upload limits, secret scanning, and what happens to a file from upload to expiry and deletion. Storage caps and 402 behavior.
order: 4
---

## Expiring links

Every upload returns a URL with a 256-bit token. The token is the auth. Anyone with the link can open the file, no login needed. The link stops working when its TTL runs out or when you revoke it. Expired and revoked links return `404` and never come back.

## TTL rules

- Omit `ttl_days` and you get the plan maximum. That is 7 days on Free.
- Ask for more than the plan maximum and the request is clamped down, never rejected. The `201` response then includes `"ttl_adjusted": true`.
- Always read `expires_at` in the response. Never trust the value you sent.

## Secret scanning

Every upload is scanned for secret-shaped content: API keys, tokens, private-key headers. Matches are reported in `secret_flags`. By default the scan only warns: a flagged upload is stored and its link works. Send `on_secret=reject` to refuse flagged files instead.

Check `secret_flags` in every upload response. If it is non-empty and you want never-host semantics, revoke the upload: `DELETE /api/v1/artifacts/<id>`, or the `revoke_artifact` MCP tool.

## Plans and limits

| Plan | Price | Uploads / month | Max TTL | Storage | API keys |
|---|---|---|---|---|---|
| Free | $0 | 100 | 7 days | 1 GB | 1 |
| Team | $4/mo | 2,000 | 90 days | 25 GB | 5 |
| Scale | $12/mo | 10,000 | 365 days | 100 GB | unlimited |

Prices include tax. Only successful uploads count against the monthly quota. Rejected uploads never consume quota.

- One file is at most 25 MiB. For MCP uploads the limit applies to the decoded bytes.
- 100 uploads per hour per credential.
- Storage caps are hard. An upload that would exceed the cap fails with `402 storage_cap_reached`.

## Downloads and revocation

Downloads send `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`. Nothing caches the bytes. Artifact URLs are never indexed. Revoking an artifact takes effect immediately. Revoking an API key stops that key from uploading or listing, but links it already created keep working until you revoke those artifacts or they expire.
