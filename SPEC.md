# Veilfile — Product Spec v1 (MVP)

Status: shipped and live at https://veilfile.com. This spec is the original
MVP scope, updated to match what was built; the code and `docs/API.md` are
authoritative where they differ. (The service was called "Drop" while in
development; the Render service and some internal names still say `deadrop`.)

## Vision
Private, ephemeral artifact hosting for AI agents. When a coding agent in CI or a
cloud session produces a screenshot, log bundle, or HAR file for review, it needs
a private URL to put it at — not a public repo. Veilfile is the sanctioned place.

## Users
- **Human developer (approver):** signs up, creates a workspace, issues API keys to
  their agents, watches usage. Approves spend once.
- **Agent (caller):** uploads files with an API key, gets back an unguessable,
  expiring URL. No human in the loop per call.

## Core API (v1)
- `POST /api/v1/artifacts` — multipart upload, `Authorization: Bearer <api-key>`.
  Returns `{id, url, expires_at, size_bytes, secret_flags[]}`. Optional `ttl_days`
  param, capped by plan.
- `GET /a/<token>` — serves the file. No auth; the 256-bit token IS the auth.
  Inline for viewable types, attachment otherwise.
- `GET /api/v1/artifacts` — list own artifacts + metadata as a bare JSON array
  (key auth); optional `limit`/`cursor` pagination.
- `DELETE /api/v1/artifacts/<id>` — revoke early (key auth).
- Key management (`/api/v1/keys`, session auth): create / list / revoke. The
  `veilfile login` device flow also mints keys (they count toward the plan cap).

## Guardrails
- Max 25 MiB per file. Allowed types: png, jpeg, webp, gif, txt, md, log, json,
  har, pdf. Executables and archives blocked.
- **Secret scanning on upload:** regex sweep for common secret shapes
  (`ghp_`, `sk-`, `AKIA`, private-key headers, etc.). Flags in the response and
  dashboard; blocks only when the caller passes `on_secret=reject`; never logs
  file contents. This is the security story.
- Rate limits per credential (100 uploads/hour); per-plan monthly upload caps
  with clean 402/429 errors.

## Plans & pricing
- **Free:** 100 uploads/mo, 7-day TTL, 1 GB storage, 1 API key.
- **Team $4/mo:** 2,000 uploads/mo, 90-day TTL, 25 GB storage, 5 keys.
- **Team Plus $6/mo:** 4,000 uploads/mo, 90-day TTL, 40 GB storage, 10 keys.
- **Scale $12/mo:** 10,000 uploads/mo, 365-day TTL, 100 GB storage, unlimited keys.
- **Yearly billing:** Team $40/yr, Team Plus $60/yr, Scale $120/yr. That is ten
  months' price, so two months are free, with the same caps as the monthly plan.
- Storage caps are hard: uploads that would exceed the cap are rejected
  with HTTP 402 (`storage_cap_reached`). Worst-case storage cost per
  customer is bounded by the cap.
- Billing via Stripe subscriptions (live, tax-inclusive $4/$6/$12 a month or $40/$60/$120 a year, Stripe Managed Payments).
  Simple plan gating with monthly counters — no complex metering.

## Architecture
- **Backend:** Django + DRF (proven pattern). Postgres in prod (SQLite dev).
- **Storage:** abstraction with two backends — local disk (dev), S3-compatible
  (prod, Cloudflare R2). Render's disk is ephemeral, so prod REQUIRES R2.
- **Web:** Django templates on one shell, plain CSS and small JavaScript
  modules, with no build step. Dashboard (overview, files, keys, connected apps,
  billing) + honest developer-aimed public pages.
- **MCP server:** hosted at `/mcp` (JSON-RPC over HTTP), tools:
  `upload_artifact`, `create_upload_url`, `list_artifacts`, `revoke_artifact`.
  API key or OAuth auth. This is the distribution lever — also ship llms.txt
  and registry listings at launch.
- **Ops:** daily housekeeping loop in the container entrypoint (TTL sweeper
  and friends; no separate cron), `/healthz` health check. All
  autonomous-friendly.

## MVP scope (week 1)
Upload API, retrieval, TTL sweeper, key management, secret-flagging, plans +
Stripe (live), dashboard, landing page, MCP server, docs + llms.txt.

## Fast follows (not MVP)
x402 per-call USDC payments, SSO, custom domains, team seats, S3-style read API.

## Decisions
1. **Name:** Veilfile (decided; "Drop" was the working title).
2. **R2 account:** done. Cloudflare R2 bucket is the production storage.
3. **Stripe account:** done. Live keys connected; tax-inclusive $4/$6/$12 monthly
   and $40/$60/$120 yearly prices with product tax codes, Stripe Managed Payments.
4. **Plan changes:** subscribers switch between Team, Team Plus and Scale, and between
   monthly and yearly billing, in the dashboard with proration; a second Checkout for a
   subscribed workspace is refused (409).
5. **Dunning:** `past_due` keeps the paid plan while Stripe retries; `unpaid`,
   `incomplete_expired`, `paused` and `canceled` drop the workspace to Free.
6. **Device-flow keys** count toward the plan's API-key cap; at the cap the
   user picks a key to replace.

## Still open
- **Repo/launch identity:** GitHub org or personal repo; launch accounts on MCP
  registries (see `docs/REGISTRIES.md`).
