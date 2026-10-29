# Veilfile API Reference

Base URL: `https://YOUR-DEPLOY` (production) or `http://localhost:8000` (local dev).
All request/response bodies are JSON unless noted. Timestamps are ISO-8601 UTC.

## Authentication

Two auth modes, used by different endpoints:

| Mode | Header / mechanism | Used by |
|---|---|---|
| API key | `Authorization: Bearer vlf_...` | All `/api/v1/artifacts` endpoints, `POST /mcp` |
| OAuth token | `Authorization: Bearer <access_token>` | Same agent surface as an API key (scope `mcp:use`), except `/api/v1/keys` |
| Session | Dashboard login cookie | `/api/v1/keys`, `/api/v1/usage`, `/api/v1/billing/*`, `GET`+`DELETE /api/v1/artifacts` |

**Key format.** Every Veilfile API key starts with the prefix `vlf_` (e.g.
`vlf_9f2K...`). The full key is shown **once** at creation. Copy it
immediately; it cannot be retrieved later. Veilfile stores only a hash.

**Rotation.** There is no "edit key". Rotate by creating a new key, updating
your agents to use it, then revoking the old one (`DELETE /api/v1/keys/<id>`).

```bash
curl -s https://YOUR-DEPLOY/api/v1/artifacts \
  -H "Authorization: Bearer vlf_YOUR_KEY_HERE"
```

---

## Artifacts

### `POST /api/v1/artifacts`: upload

Multipart upload. API key auth.

| Part | Required | Description |
|---|---|---|
| `file` | yes | The file bytes. Max **25 MiB**. |
| `ttl_days` | no | Whole days until the link expires. Defaults to the plan's max TTL; a larger number is clamped down, `0` or a negative number up to 1. A non-integer (`0.5`, `1e3`, `abc`) returns `400` `invalid_request`. |
| `on_secret` | no | `flag` (default) or `reject`, case-insensitive; any other value returns `400` `invalid_request`. Also accepted as a query parameter. |

**Allowed types:** `png`, `jpg`, `jpeg`, `webp`, `gif`, `txt`, `md`, `log`, `json`,
`har`, `pdf`. Executables and archives are rejected. Content is verified, not
just the extension (images and PDFs must carry the right magic bytes; text
types must be UTF-8).

**Stored type and name.** The stored `mime_type` is derived from the
extension (`image/png`, `image/jpeg`, `image/webp`, `image/gif`,
`application/pdf`, `text/plain` for `txt`/`log`/`md`, `application/json` for
`json`/`har`); any type you declare is ignored. Filenames are sanitized: only
the last path component is kept, `/ \ % : "` and control characters become
`_`, leading and trailing dots and spaces are stripped, and the name is capped
at 200 UTF-8 bytes.

**Size.** Bodies over 25 MiB are refused with `413` `file_too_large`. A
`Content-Length` above 26 MiB is refused before the body is read.

**Session uploads.** An upload made with a dashboard session (no API key or
OAuth token) needs a verified email: otherwise `403`
`email_verification_required`.

**Secret scanning:** every upload is regex-swept for common secret shapes
(`ghp_`, `sk-`, `AKIA`, 40-char AWS secret access keys, private-key headers, …).
Matches are reported in `secret_flags` and surfaced in the dashboard. Scanning
**never blocks** the upload and **never logs file contents** — unless you opt
into `on_secret=reject` (form field or query param), which rejects a flagged
upload with `400` (`secret_detected`) and stores nothing. Flagged responses
also carry `secret_flag_locations`: `{flag: [{line, offset}, ...]}` with the
1-based line and offset of each hit, at most the first 20 per flag
(`secret_flag_locations_truncated: true` is added when a flag had more).
Only the first 1 MiB of each file is scanned.

```bash
curl -s -X POST https://YOUR-DEPLOY/api/v1/artifacts \
  -H "Authorization: Bearer vlf_YOUR_KEY_HERE" \
  -F "file=@screenshot.png" \
  -F "ttl_days=7"
```

Response `201 Created`:

```json
{
  "id": "b3f7c2a1-4d5e-4f6a-9b1c-2d3e4f5a6b7c",
  "url": "https://YOUR-DEPLOY/a/4fK9vX2qZt8sW...",
  "expires_at": "2026-10-12T00:25:00Z",
  "size_bytes": 184320,
  "secret_flags": []
}
```

`url` is private and unguessable (256-bit token). It needs no auth to fetch.
`secret_flags` is a list like `["github_token"]`; empty when clean.
When the requested `ttl_days` was clamped to the plan maximum, the response
also includes `"ttl_adjusted": true`. When `secret_flags` is non-empty, it
also includes a `warning` string: *"The scan flagged this file as possibly containing a secret; see secret_flags. Ask the user whether to keep or revoke the file before sharing its link."* Both fields are additive and
appear only when applicable.

### `PUT /api/v1/uploads/<token>`: complete a one-time upload

No auth — the unguessable token in the path is the auth. PUT the raw file
bytes (mint the URL first via the MCP `create_upload_url` tool). The body
runs the full upload pipeline: extension and magic-byte verification,
secret scan, monthly/storage caps, quota. Up to 25 MiB; a larger body gets
`413` `file_too_large`.

The URL is used up only by a `201` or by a `400` `content_not_allowed` safety
block. Any other rejection (`400` `file_type_not_allowed`, `secret_detected` or
`invalid_request`; `413`; `429`; `402`; `502` storage) leaves it usable, so
you can fix the problem and retry until it expires. Replays of a used URL
`404`; expired, unknown and orphaned tokens `404` identically. URLs expire 15
minutes after minting, and stop working if the key (or OAuth grant) that
minted them is revoked. The `on_secret` chosen when minting is enforced;
`?on_secret=reject` on the PUT can tighten `flag` to `reject` but never loosen
a minted `reject`.

```bash
curl -s --upload-file screenshot.png "https://YOUR-DEPLOY/api/v1/uploads/<token>"
```

Response `201 Created`: same body as `POST /api/v1/artifacts`.

### `GET /a/<token>`: download

Public. **No auth.** The 256-bit token in the URL *is* the auth. Keep URLs
out of public places.

- `Content-Type` comes from the stored filename's extension. PNG, JPEG, WebP,
  GIF, PDF and plain text (`.txt`, `.log`, `.md`) are served **inline**
  (text with `; charset=utf-8`); everything else, including JSON and HAR, is
  served as an **attachment** (download).
- Returns `404` when the token is unknown, the artifact was revoked, or its
  TTL has expired. Expired/revoked URLs never come back.
- Returns `503` (with `Retry-After`) when the storage backend is
  temporarily unavailable. The link is still valid; retry later.

```bash
curl -s -o screenshot.png "https://YOUR-DEPLOY/a/4fK9vX2qZt8sW..."
```

### `GET /api/v1/artifacts`: list

API key or session auth. Returns the calling key's workspace artifacts, newest first,
as a bare JSON array (not wrapped in an object). Rows past their `expires_at`
that the sweeper has not deleted yet may still appear.

Optional pagination: `limit` (1 to 200; 50 when only `cursor` is given) and
`cursor`. Without them you get the first page as before. When more items
remain, the response carries an `X-Next-Cursor` header and a
`Link: <...>; rel="next"` header; the body stays a bare array. A non-integer
`limit` or a malformed `cursor` returns `400` `invalid_request`.

```bash
curl -s https://YOUR-DEPLOY/api/v1/artifacts \
  -H "Authorization: Bearer vlf_YOUR_KEY_HERE"
```

Response `200 OK`:

```json
[
  {
    "id": "b3f7c2a1-4d5e-4f6a-9b1c-2d3e4f5a6b7c",
    "filename": "screenshot.png",
    "mime_type": "image/png",
    "size_bytes": 184320,
    "url": "https://YOUR-DEPLOY/a/4fK9vX2qZt8sW...",
    "expires_at": "2026-10-12T00:25:00Z",
    "secret_flags": [],
    "created_at": "2026-10-05T00:25:00Z"
  }
]
```

### `DELETE /api/v1/artifacts/<id>`: revoke

API key or session auth. Deletes the artifact immediately; its URL starts
returning `404` at once.

```bash
curl -s -X DELETE https://YOUR-DEPLOY/api/v1/artifacts/b3f7c2a1-4d5e-4f6a-9b1c-2d3e4f5a6b7c \
  -H "Authorization: Bearer vlf_YOUR_KEY_HERE"
```

Response `204 No Content` on success. Unknown ids (or ids in another
workspace) return `404` with `{"error": "No file with that id exists in this workspace. Check the id.", "code": "not_found"}`.
If the storage backend is unreachable, the URL still dies at once and the
file is deleted by the next expiry sweep; the artifact shows as expired in
the list until then.

---

## API keys (session auth)

Manage keys from the dashboard or these endpoints. Creating a key needs a
logged-in session: an API key or OAuth token gets `403`
`{"code": "session_required"}`. An API key may list keys and revoke
itself (this is how `veilfile logout` revokes its own key); revoking any
other key needs the dashboard (`403 session_required`). OAuth tokens
cannot list or revoke keys (`403 session_required`).

### `POST /api/v1/keys`: create

```bash
curl -s -X POST https://YOUR-DEPLOY/api/v1/keys \
  -b cookies.txt -H "Content-Type: application/json" \
  -d '{"name": "ci-runner"}'
```

Response `201 Created`: `api_key` is the full `vlf_...` secret, shown once:

```json
{
  "id": 42,
  "name": "ci-runner",
  "key_prefix": "vlf_9f2K9x1Q",
  "api_key": "vlf_9f2K..."
}
```

Plan key limits apply (Free: 1 key, Team: 5, Scale: unlimited). Every active
key counts, including keys issued by `veilfile login`. Exceeding the limit
returns `402` `key_limit_reached`. An unverified email returns `403`
`email_verification_required`. `name` is optional; a non-string, a name over 255
characters, or a name containing control characters returns `400`
`{"code": "invalid_request"}`.

### `GET /api/v1/keys`: list

Returns `[{id, name, key_prefix, created_at, last_used_at, is_active}]`.
Full key material is never returned.

### `DELETE /api/v1/keys/<id>`: revoke

Immediately invalidates the key. In-flight uploads with it fail closed.
Revoking a key revokes only that key: OAuth-connected apps are unaffected
(disconnect them under Connected apps, below). With API-key auth, `<id>`
must be the calling key's own id; any other key in the workspace returns
`403` `{"code": "session_required"}`, and a key from another workspace
returns `404`. Artifact links that were already issued keep working: to
kill a link, revoke the artifact itself (`DELETE /api/v1/artifacts/<id>`).

---

## Usage

### `GET /api/v1/usage`

API key or session auth. Current billing-period usage for the key's
workspace (agents: read this to anticipate `402`/`429` before uploading).
`keys_used` counts every active key, device-issued ones included; `keys_cap`
is `null` on Scale:

```json
{
  "plan": "team",
  "uploads_used": 312,
  "uploads_cap": 2000,
  "ttl_days": 90,
  "keys_used": 2,
  "keys_cap": 5,
  "storage_used_bytes": 1073741824,
  "storage_cap_bytes": 26843545600,
  "email": "you@example.com",
  "email_verified": true
}
```

---

## Device flow (`veilfile login`)

Let an agent sign in from a terminal without pasting secrets. RFC 8628-style
device authorization: the terminal starts a grant, the user approves it in a
browser, the terminal polls until it receives its API key.

**Terminal UX sequence:**

1. Terminal: `POST /api/v1/device/code` → shows the `user_code` and the
   `verification_uri` (or the `verification_uri_complete` link).
2. User: opens the link in a browser, logs in, checks that the code matches
   the terminal, confirms they started the login themselves, and approves.
   Approval requires a **verified email address** — unverified users get a
   `403` page pointing them at email verification.
3. Terminal: polls `POST /api/v1/device/token` every `interval` seconds until
   it receives `{"api_key": "vlf_..."}` (or an error). The key is created by
   the first poll after approval, so an approval that is never collected
   creates no key.

The issued credential is a regular `vlf_` API key named "Terminal login",
flagged as device-issued. Device-issued keys **count toward the plan key cap**
like any other key, and are listed and revoked in the dashboard like any
other key (`DELETE /api/v1/keys/<id>`). When the workspace is at its cap, the
consent page asks you to pick an existing key to replace; that key is revoked
when the terminal receives its new key. Codes expire after 10 minutes.

### `POST /api/v1/device/code`: start a grant

Unauthenticated. Rate-limited to 30/hour per IP (per /64 for IPv6), plus a
site-wide ceiling on sign-ins in progress (`429` with `Retry-After`). The
requesting IP and User-Agent are shown on the consent page. Grants are deleted
an hour after they expire, or a day after if they issued a key.

```json
{
  "device_code": "secret-for-polling-only",
  "user_code": "XXXX-XXXX",
  "verification_uri": "https://YOUR-DEPLOY/device",
  "verification_uri_complete": "https://YOUR-DEPLOY/device?user_code=XXXX-XXXX",
  "expires_in": 600,
  "interval": 5
}
```

### `POST /api/v1/device/token`: poll for the key

Unauthenticated. Body (JSON object or form-encoded): `{"grant_type":
"urn:ietf:params:oauth:grant-type:device_code", "device_code": "..."}`.

Poll errors are HTTP `400` with `{"error": ...}`, sometimes with an
`error_description`:

| Error | Meaning |
|---|---|
| `authorization_pending` | The user has not approved the request yet, so keep polling. |
| `slow_down` | The client polled faster than `interval`, so add 5 seconds to the interval. |
| `access_denied` | The user denied the request, or the plan's key cap no longer allows the key (for example, the plan changed after approval). |
| `expired_token` | The code expired, 10 minutes after it was issued. |
| `invalid_grant` | The code is unknown, or its key was already delivered. |
| `invalid_request` | The body is not an object, or a field is missing or not a string. |
| `unsupported_grant_type` | The `grant_type` is not the device-code grant. |

Success (`200`): `{"api_key": "vlf_..."}`. The key is delivered exactly once, so
polling again returns `invalid_grant`.

### Browser pages

- `GET /device` — enter the code (or open with `?user_code=XXXX-XXXX` to go
  straight to the consent page). Login required.
- `POST /device/approve` — approve (requires `confirm=yes`, plus
  `replace_key=<id>` at the key cap). The terminal's next poll mints the key.
- `POST /device/deny` — refuse the request.

Codes that match no live request are limited to 10 per account and 30 per IP
(per /64 for IPv6) per 10 minutes; past that, the pages answer `429`.

### Command-line clients

Two CLIs implement this flow so you never paste a secret into the terminal —
a Python one (in `cli/`, single file, standard library only) and a Node one
(in `npm/`, single file, zero dependencies, Node 18+). Same commands, same
behavior; install only one (see `npm/README.md` for the PATH-shadowing note).

```sh
pip install veilfile        # or: npm install -g veilfile

veilfile login    # browser approval flow; stores the API key locally
veilfile logout   # revoke the key on the server; delete the local copy
veilfile status   # confirm the stored key is valid (never prints it)
veilfile push <file>   # upload; print the share URL
veilfile pull <url>    # download a shared file
```

`veilfile login` prints a code, opens the verification page, and polls until
you approve — then stores the key in a `0600` credentials file
(`~/.config/veilfile` on macOS/Linux, `%APPDATA%\veilfile` on Windows). The
raw key is never printed unless you pass `--print-key`. Setting
`VEILFILE_API_KEY` overrides the file, matching the MCP setup docs.

---

## OAuth 2.0 authorization server (MCP clients)

MCP-spec OAuth for clients that authenticate with OAuth instead of an API
key (e.g. Claude Code): the authorization-code flow with PKCE, dynamic
client registration (RFC 7591), and RFC 8414 / RFC 9728 discovery metadata.

**Scope.** One scope: `mcp:use` ("Use Veilfile MCP tools and the agent API
(upload, list, revoke artifacts; read plan usage and the account email)").
An OAuth bearer with `mcp:use` has the agent surface of a `vlf_` API key:
the artifact endpoints, `/api/v1/usage`, the MCP endpoint, and the upload
throttle. It cannot create, list or revoke API keys (`403
session_required`). OAuth credentials **never count against the plan key
cap** — caps count `APIKey` rows only, and OAuth tokens live in separate
tables. Tokens of a user who is deactivated, or whose email is not
verified, never authorize. While a user is deactivated, `/o/token/` also
refuses their authorization codes and refresh tokens (`invalid_grant`).

**Endpoints** (base `https://YOUR-DEPLOY`):

| Endpoint | Purpose |
|---|---|
| `POST /o/register/` | RFC 7591 dynamic client registration. Anonymous; 30/hour per IP (an IPv6 client counts by its /64; over it, `429` with `Retry-After`). The body is at most 16 KiB, `grant_types` may only contain `authorization_code` and `refresh_token`, and no value may contain a NUL character or a lone surrogate (`\ud800` without its pair): each of these is `400 invalid_client_metadata`, as is JSON nested too deeply to parse. `PUT /o/register/<client_id>/` (RFC 7592, with the registration access token) takes the same checks, and a body over 16 KiB is refused before the token is looked at. |
| `GET /o/authorize/` | Authorization request; login required. |
| `POST /o/authorize/` | Approve/deny the grant (consent page). |
| `POST /o/token/` | Token exchange (authorization code, refresh token; any other `grant_type` gets `400 unsupported_grant_type`). 300/hour per IP (IPv6: per /64). |
| `POST /o/revoke_token/` | RFC 7009 token revocation. 300/hour per IP (IPv6: per /64; over it, `429` with `Retry-After`, its own budget apart from `/o/token/`'s). |
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata. |
| `/.well-known/oauth-protected-resource` | RFC 9728 metadata (`resource: https://YOUR-DEPLOY/mcp`, `scopes_supported: ["mcp:use"]`, `bearer_methods_supported: ["header"]`). |

**Client sequence:**

1. Register: `POST /o/register/` with
   `{"client_name": "...", "redirect_uris": ["http://127.0.0.1:PORT/callback"],
   "grant_types": ["authorization_code"], "token_endpoint_auth_method": "none"}`
   → `201` with `client_id` (public/native client). Redirect URIs must be
   `https`, except `http` for literal loopback IPs (`127.0.0.1`, `::1`) per
   RFC 8252 — `localhost` names, private IPs, whitespace, and other schemes
   are rejected with `invalid_redirect_uri`, and so is a host whose last label
   is a number (`127.1`, `10.1`, `0x7f.1`, `0177.0.0.1`: browsers read these as
   IPv4 addresses).
2. Authorize: open
   `GET /o/authorize/?response_type=code&client_id=...&redirect_uri=...&scope=mcp:use&state=...&code_challenge=...&code_challenge_method=S256`
   in a browser. The user logs in, sees the consent page — the client name
   is shown as an *unverified* third-party app (attacker-controlled, so it
   is never trusted), with the redirect host displayed — and approves.
   **Verified email required**: unverified users get `403` and no grant is
   created. A request with an unknown `client_id`, or a `redirect_uri` the
   client did not register, gets `400` and an error page, never a consent
   page, and the browser is not sent to that `redirect_uri`.
3. Exchange: `POST /o/token/` with
   `grant_type=authorization_code&code=...&redirect_uri=...&client_id=...&code_verifier=...`
   → `200` with `access_token` (1 hour), `refresh_token` (14 days,
   rotating), `scope: "mcp:use"`. Authorization codes are single-use and
   expire after 10 minutes.
4. Use it: `Authorization: Bearer <access_token>` on `/mcp` or any
   `/api/v1/*` agent endpoint.

**PKCE.** S256 only; the deprecated `plain` method is refused. PKCE is
required for public clients.

**Revocation.** API keys and OAuth grants are revoked separately:
revoking an API key (including `veilfile logout`) does not touch OAuth
apps.

- `GET /api/v1/oauth/grants/` (session only) lists the user's connected
  apps: `[{id, name, client_id, redirect_host, last_authorized_at}]`. The
  dashboard shows this as **Connected apps**.
- `DELETE /api/v1/oauth/grants/<id>/` (session only, CSRF) disconnects one
  app: its access and refresh tokens and pending authorization codes are
  revoked. `204`, or `404` when the user has no tokens or pending codes
  for it.
- `POST /api/v1/oauth/revoke-grants/` revokes all of the user's OAuth
  tokens and grants (session auth). Called with an OAuth token, it
  disconnects only that token's own app. An API key gets `403`
  `{"code": "session_required"}`.
- Password reset and password change revoke all of the user's OAuth
  tokens (API keys are kept).

DCR client rows are kept in every case, so the client can re-authorize.

A revoked token can never authorize again: access tokens are deleted on
revoke, and validation looks up the live row.

**Unauthenticated `/mcp`.** Returns `401` with a
`WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource"`
challenge (MCP spec) alongside the JSON-RPC error body.

---

## Billing (session auth)

Billing runs on Stripe **subscriptions** (live mode, Stripe Managed Payments,
tax-inclusive prices). Plans are per workspace. Every billing endpoint needs a
dashboard session with CSRF (API keys and OAuth tokens get `403`). The plan
only ever changes from Stripe-verified state (the webhook, or a read-back from
Stripe), never from a client request. Billing errors use the standard
`{"error", "code"}` envelope unless noted. Stripe failures are `502`
`stripe_error`; missing Stripe configuration is `503`
`{"error": "billing_not_configured"}` (no `code`).

### `POST /api/v1/billing/checkout`: subscribe

For workspaces with no live subscription. `plan` is `"team"` or `"scale"`
(else `400` `invalid_plan`). Response `200 OK`:

```json
{"checkout_url": "https://checkout.stripe.com/c/pay/cs_live_..."}
```

Redirect the workspace owner there to complete payment. If Stripe already
holds a live subscription for the workspace (`active`, `trialing`, `past_due`,
`unpaid`, `incomplete`, `paused`), the answer is `409`
`{"error": ..., "code": "already_subscribed", "subscription_status": ...}`:
change plans with `/billing/switch` or the portal instead.

### `POST /api/v1/billing/switch`: change plan

For existing subscribers. Body `{"plan": "team" | "scale"}`. The price on the
subscription is swapped in place with proration: an upgrade is invoiced
immediately and applies once the invoice is paid; a downgrade is credited.

| Status | Body |
|---|---|
| `200` | `{"plan", "status": "switched", "subscription_status"}` |
| `200` | `{"plan", "status": "unchanged"}` (already on that plan) |
| `200` | `{"plan", "status": "pending_cancelled"}` (switching back to the current plan voids a switch that was still waiting for payment) |
| `402` | `{"error", "code": "payment_required", "plan", "invoice_url"}`: the payment failed, the plan is unchanged, and `invoice_url` can still be paid |
| `409` | `no_subscription`, `subscription_past_due` or `multiple_subscriptions` |
| `400` | `invalid_plan` |
| `502` / `503` | `stripe_error` / `billing_not_configured` |

### `POST /api/v1/billing/portal`: manage subscription

Response `200 OK`: `{"portal_url": "https://billing.stripe.com/p/session/..."}`.
Redirect the owner there to update the card, view invoices, or cancel. A
workspace with no Stripe customer gets `400` `{"error": "no_billing_account"}`.

### `GET /api/v1/billing/status`: current plan and caps

No Stripe call; reflects local state kept in sync by the webhook.

```json
{
  "plan": "team",
  "caps": {"uploads_per_month": 2000, "ttl_days": 90, "keys": 5, "storage_gb": 25},
  "storage_used_bytes": 1073741824,
  "active_keys": 2,
  "over_plan_limits": [],
  "price_usd": 4,
  "has_billing_account": true,
  "has_subscription": true,
  "subscription_status": "active",
  "current_period_end": "2026-11-05T00:00:00+00:00"
}
```

`caps.keys` is `null` on Scale. `over_plan_limits` lists `"keys"`,
`"storage"` or `"ttl"` for a workspace that is above its current plan after a
downgrade. `subscription_status` follows Stripe (`active`, `past_due`,
`canceled`, ...) and is `""` when there is no subscription.

### `POST /api/v1/billing/webhook`

Stripe only (signature verified). Not for clients. The plan follows Stripe's
current subscriptions: `past_due` keeps the paid plan while Stripe retries
payment; `unpaid`, `incomplete_expired`, `paused` and `canceled` drop the
workspace to Free.

---

## MCP endpoint

Hosted MCP server at **`POST /mcp`**: plain JSON-RPC 2.0 over HTTP.
Single request object in, single response object out, no SSE.
`Content-Type: application/json`. Auth is the same API key scheme:
`Authorization: Bearer vlf_...` (or an OAuth access token). Credentials are
checked before the body is parsed, so an unauthenticated request always gets
`401` (`-32001`), even with malformed JSON.

The server exposes **four tools**: `upload_artifact`, `create_upload_url`,
`list_artifacts`, `revoke_artifact`.

Methods:

| Method | Description |
|---|---|
| `initialize` | Handshake. The server negotiates: it answers with the client's requested `protocolVersion` when supported (currently `2024-11-05` or `2025-06-18`), otherwise its latest (`2025-06-18`). Capabilities are `{tools: {}}`; `serverInfo` is `{name: "veilfile-mcp", version: "0.1.0"}`. |
| `notifications/initialized` | Client ready → `202` with empty body |
| `ping` | Returns `{}` |
| `tools/list` | The tools below with their `inputSchema`s |
| `tools/call` | Dispatch by `params.name`. An unknown tool gets JSON-RPC `-32602` |

A message with no `id` is a notification: a valid one gets `202` with an empty
body whatever its method. A JSON-RPC response object sent by a client also
gets `202`. Requests must carry `"jsonrpc": "2.0"` and a string `method`.

Tools:

Every tool has a `title` and `annotations`. The titles are "Upload a file", "Create an upload URL", "List files" and "Revoke a file". `list_artifacts` is read-only and idempotent. `revoke_artifact` is destructive and idempotent. `upload_artifact` and `create_upload_url` are not read-only, not destructive and not idempotent. No tool reaches outside Veilfile, so `openWorldHint` is `false` on all four.

**`upload_artifact`** (title "Upload a file"): *"Upload a file from base64 content and return its id, its private share link as url, expires_at, size_bytes and secret_flags. The link stops working when the file expires. The decoded file can be up to 25 MiB; for larger files, create_upload_url takes the bytes by PUT instead. The secret scan reads the first 1 MiB of the file, and secret_flags names each kind of secret-shaped content it finds. With on_secret "flag", the default, a flagged file is stored and the result adds a warning and secret_flag_locations. With on_secret "reject", a flagged file is not stored and the call returns the secret_detected error."*

```json
{
  "type": "object",
  "required": ["filename", "content_base64"],
  "properties": {
    "filename":       {"type": "string",  "description": "This is the file name, including its extension, such as screenshot.png. The allowed extensions are png, jpg, jpeg, webp, gif, txt, md, log, json, har and pdf, and the file's contents must match its extension."},
    "content_base64": {"type": "string",  "description": "This is the file's content as standard base64. Line breaks and a data:<mime>;base64, prefix are accepted. The decoded file can be up to 25 MiB."},
    "mime_type":      {"type": "string",  "description": "The server does not use this value; it takes the file's type from the extension in filename. If you send it, it must be a string."},
    "ttl_days":       {"type": "integer", "description": "This is the number of days until the link expires, as a whole number of 1 or more. If you leave it out, the link expires at the plan's maximum, which is 7 days on Free, 90 on Team and 365 on Scale. A larger value is lowered to the plan's maximum, and the result then includes ttl_adjusted: true. A value of zero or less is rejected. The result's expires_at is the applied expiry."},
    "on_secret":      {"type": "string",  "description": "The value is either \"flag\" or \"reject\", and the default is \"flag\". With \"flag\", a file with secret-shaped content is stored, and the result lists secret_flags and adds a warning and secret_flag_locations. With \"reject\", that file is not stored and the call returns the secret_detected error."}
  }
}
```

Result (in `result.content[0].text` as JSON):
`{id, url, expires_at, size_bytes, secret_flags}` (plus `ttl_adjusted: true` when clamping changed your value; plus `warning` and `secret_flag_locations` (`{flag: [{line, offset}]}`) when flags are non-empty).

**`create_upload_url`** (title "Create an upload URL"): *"Create a one-time upload URL for a file too large to send as base64 through upload_artifact. The result has upload_url, expires_at, max_size_bytes (25 MiB), ttl_days and filename. The URL is valid for 15 minutes and needs no API key. A PUT of the file's raw bytes to it, for example with curl --upload-file, runs the same type check and secret scan as upload_artifact and answers 201 with the same fields that upload_artifact returns. The first successful PUT uses up the URL. A PUT that fails can be retried until the URL expires, except one blocked as content_not_allowed. The URL also stops working if the API key that created it is revoked or the connected app that created it is disconnected."*

```json
{
  "type": "object",
  "required": ["filename"],
  "properties": {
    "filename": {"type": "string", "description": "This is the file name, including its extension, such as screenshot.png. The extension is checked when the URL is created, and the allowed extensions are png, jpg, jpeg, webp, gif, txt, md, log, json, har and pdf."},
    "ttl_days": {"type": "integer", "description": "This is the number of days until the link expires, as a whole number of 1 or more. If you leave it out, the link expires at the plan's maximum, which is 7 days on Free, 90 on Team and 365 on Scale. A larger value is lowered to the plan's maximum, and the result then includes ttl_adjusted: true. A value of zero or less is rejected. The days count from the PUT, and the result's ttl_days is the applied value."},
    "on_secret": {"type": "string", "description": "The value is either \"flag\" or \"reject\", and the default is \"flag\". With \"reject\", a PUT of a flagged file fails with the secret_detected error, the file is not stored and the URL stays usable until it expires. With \"flag\", a flagged file is stored and the result lists its secret_flags and adds a warning and secret_flag_locations."}
  }
}
```

Result (in `result.content[0].text` as JSON):
`{upload_url, expires_at, max_size_bytes, ttl_days, filename}` (plus `ttl_adjusted: true` when clamping changed your value). Then:

```bash
curl -s --upload-file screenshot.png "<upload_url>"
```

→ `201` with the same body as `POST /api/v1/artifacts`. The URL is single-use (replays `404`), expires 15 minutes after minting, and carries no API key.

**`list_artifacts`** (title "List files"): *"List the files in this workspace, newest first. Each entry has id, filename, mime_type, size_bytes, url, expires_at, secret_flags and created_at. Expired files that are not yet deleted are included. The tool changes nothing."*
The optional `limit` is described as: *"This is the maximum number of files to return. The range is 1 to 200, the default is 50, and a value outside the range is moved to the nearest end of it. Files beyond the limit are not returned, and there is no paging."* Result:
`{artifacts: [{id, filename, mime_type, size_bytes, url, expires_at, secret_flags, created_at}]}`.

**`revoke_artifact`** (title "Revoke a file"): *"Revoke a file by its id. Its link stops working at once and the file is deleted. The result is revoked: true, or revoked: false when this workspace has no file with that id. If storage could not be reached, the result adds storage_deleted: false and a later sweep finishes the deletion. Repeating the call is harmless."*
The required `id` is described as: *"This is the id of the file to revoke, a UUID. Both upload_artifact and list_artifacts return it."* Result: `{revoked: true|false}`.
When storage was unreachable the result also carries `storage_deleted: false`:
the URL is already dead and the file is deleted by the next expiry sweep.

**Tool failures** are tool results, not JSON-RPC errors: HTTP `200` with
`result.isError: true` and `result.content[0].text` holding JSON
`{"error": "...", "code": "...", "detail"?: "..."}`. The codes match REST:
`file_too_large`, `file_type_not_allowed`, `content_not_allowed`,
`secret_detected`, `monthly_cap_reached`, `storage_cap_reached`,
`storage_unavailable` and `rate_limited` (with `retry_after` seconds).
Successful results have no `isError` key. `upload_artifact` and
`create_upload_url` share the 100 uploads/hour/credential limit with REST.

Example: upload via the MCP endpoint:

```bash
B64=$(base64 -w0 screenshot.png)
curl -s -X POST https://YOUR-DEPLOY/mcp \
  -H "Authorization: Bearer vlf_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d "{
    \"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\",
    \"params\": {
      \"name\": \"upload_artifact\",
      \"arguments\": {\"filename\": \"screenshot.png\", \"content_base64\": \"$B64\", \"ttl_days\": 7}
    }
  }"
```

Non-POST requests get `405` with a JSON `hint`: *"Send a POST request with a JSON-RPC 2.0 body and the header Content-Type: application/json."* Invalid JSON gets `-32700`.
A request that is not valid JSON-RPC (a missing `jsonrpc` or `method`, or an object, array or boolean `id`) gets
`-32600` with HTTP `400`. An unknown method gets `-32601`. Malformed arguments or
an unknown tool get `-32602`. Missing or invalid credentials get `-32001` with HTTP
`401`. An unexpected server error gets HTTP `500` with `-32603`.

Request limits:

- Bodies up to 36 MiB are accepted. A larger declared body gets HTTP `413`,
  `-32600`, id `null`, `data.code: "file_too_large"`.
- A body over 2.5 MiB is parsed only when it is a plain upload (few JSON
  structural characters and no characters above U+00FF); otherwise `413`,
  `-32600`, `data.code: "request_too_complex"`. In practice, an `upload_artifact`
  over about 1.9 MiB decoded whose filename has characters above U+00FF (CJK,
  emoji, curly quotes) is refused: use an ASCII filename or `create_upload_url`.
- An `MCP-Protocol-Version` header other than `2024-11-05` or `2025-06-18`
  gets `400` `-32600` on every method except `initialize`.
- A request with an `Origin` header that is not this deployment's origin gets
  `403`. Requests without an `Origin` header (all SDK and CLI clients) are
  unaffected.

---

## Errors

All API errors are JSON: `{"error": "<human message>", "code": "<stable code>"}`.
Branch on `code`, not on `error`. Auth failures, throttles, and validation
errors use the same envelope; HTTP status codes are unchanged. (A few billing
refusals carry only `error`, as noted in the Billing section.) The upload rate
limiter (100/hour per credential) answers `429` with `code: "rate_limited"`
and a `Retry-After` header (seconds). Rejections that know why add a `detail`
string: `file_type_not_allowed` names the detected content kind, the file
extension, and the allowed list.

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `secret_detected` | The scan found secret-shaped content and `on_secret` is `reject`, so nothing was stored. |
| 400 | `invalid_request` | The request is malformed, for example bad JSON, a bad `ttl_days`, a bad `on_secret` or a bad cursor. |
| 400 | `file_type_not_allowed` | The extension is not in the allowed list, or the content does not match it. |
| 400 | `content_not_allowed` | The file's content is not allowed. Do not retry the same bytes. |
| 400 | `file_missing` | The multipart body has no `file` part. |
| 401 | `unauthorized` | The credentials are missing or not valid. MCP answers with JSON-RPC `-32001`. |
| 402 | `storage_cap_reached` | The workspace has reached its plan's storage cap. Revoke files you no longer need, or upgrade. |
| 402 | `key_limit_reached` | The workspace has reached its plan's API key limit (`POST /api/v1/keys`). |
| 403 | `forbidden` | The caller is signed in but not permitted, or a session call failed the CSRF check. |
| 403 | `session_required` | The endpoint needs a dashboard session, not an API key or an OAuth token. |
| 403 | `email_verification_required` | The account email must be verified first. |
| 404 | `not_found` | The id or token is unknown, or the file is revoked or expired. |
| 405 | `method_not_allowed` | The HTTP method is not allowed for this URL. The `Allow` header lists the ones that are. |
| 413 | `file_too_large` | The file, or the decoded base64, is larger than 25 MiB. |
| 429 | `monthly_cap_reached` | The workspace has reached its plan's monthly upload cap. Uploads work again next month, or you can upgrade. |
| 429 | `rate_limited` | There were too many requests. Wait the number of seconds in `Retry-After`, then try again. |
| 502 | `storage_unavailable` | Storage failed during an upload, so nothing was stored or counted. Try again in a minute. |

## Rate limits

- **100 uploads/hour per credential.** Applies to API keys and OAuth tokens
  alike (each has its own throttle identity), to REST `POST /api/v1/artifacts`
  and to the MCP `upload_artifact` and `create_upload_url` tools. Exceeding it
  returns `429` with a `Retry-After` header (seconds).
- Only successful uploads (`201`) count against the monthly cap. Rejected
  uploads (`4xx`) never consume quota.
- Listing, downloads, and revokes are not upload-counted.

## Plans

| Plan | Price | Uploads / month | Max TTL | Storage | API keys |
|---|---|---|---|---|---|
| Free | $0 | 100 | 7 days | 1 GB | 1 |
| Team | $4/mo | 2,000 | 90 days | 25 GB | 5 |
| Scale | $12/mo | 10,000 | 365 days | 100 GB | unlimited |

`ttl_days` above the plan max is clamped down, never rejected. Storage
caps are hard: an upload that would exceed the cap fails with HTTP 402
(`storage_cap_reached`).

---

## Implementation notes

- Artifact list/revoke accept **both** API-key auth (`Authorization: Bearer vlf_…`)
  and session auth (dashboard). Uploads via the dashboard UI are allowed and
  count against the workspace's monthly cap like any other upload.
- Secret-flag names are defined in `backend/drop_core/secrets.py`
  (`github_pat`, `github_token`, `openai_key`, `anthropic_key`, `stripe_key`,
  `aws_access_key`, `aws_secret_key`, `private_key`, `slack_token`,
  `google_api_key`, `generic_secret`). Flags are advisory only. Uploads are
  never blocked and file contents are never logged.
- Expired artifacts are deleted by the `sweep_expired` management command,
  which prints `swept=N bytes=N failed=N` and exits non-zero when any storage
  delete failed (those rows are kept and retried). The web service also
  sweeps lazily on uploads, in small time-boxed batches (at most 50 rows or
  about 2 s per upload, at most once every 24h unless a backlog remains),
  so no cron job is required on the current deploy.
- Deleting artifacts by any other path (Django admin, deleting a user or
  workspace) removes their stored files after the transaction commits.
  `manage.py reconcile_storage` lists stored objects with no artifact row
  (`--delete` removes them) as a backstop after storage outages or a
  database restore. `--delete` refuses to run against a non-PostgreSQL
  database when storage is S3/R2, and (unless `--force`) when the artifact
  table is empty or more than 20% of the objects have no row; check the
  `db=` and `storage=` fields of the dry run first.
