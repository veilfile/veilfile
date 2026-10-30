# Veilfile — private, ephemeral artifact hosting for AI agents

When a coding agent in CI or a cloud session produces a screenshot, log bundle,
or HAR file for review, it needs a private URL to put it at — not a public
repo. Veilfile is the sanctioned place: upload via API key (or MCP), get back an
unguessable, expiring URL. Uploads are scanned for secret shapes and flagged
(blocked only when the caller passes `on_secret=reject`; file contents are
never logged).

- **API:** `POST /api/v1/artifacts` (multipart, `Authorization: Bearer vlf_…`) →
  `{id, url, expires_at, size_bytes, secret_flags[]}`. `GET /a/<token>` serves
  the file (the 256-bit token *is* the auth). List (bare JSON array) and
  revoke endpoints included.
- **MCP:** hosted server at `POST /mcp` (JSON-RPC over HTTP; tools
  `upload_artifact`, `create_upload_url`, `list_artifacts`, `revoke_artifact`),
  authenticated with a `vlf_` key or an OAuth token.
- **CLIs:** `veilfile` on PyPI (`cli/`) and npm (`npm/`): `login` (device
  flow), `logout`, `status`, `push`, `pull`.
- **Dashboard:** signed-in pages at `/app/`, Django views on the same shell as
  the rest of the site: Overview (usage vs plan caps), Files (secret-flag notes
  + revoke), Keys (a new key is shown once), Connected apps and Billing (plan
  switching and the Stripe billing portal).
- **Landing page:** `/` (public). **Agent docs:** `/llms.txt`, `docs/API.md`,
  `/docs/`.
- **Agents working on this repo:** read [AGENTS.md](AGENTS.md).

See [SPEC.md](SPEC.md) for the product spec.

## Plans

| Plan | Price | Uploads/mo | Max TTL | Storage | API keys |
|------|-------|------------|---------|---------|----------|
| Free | $0 | 100 | 7 days | 1 GB | 1 |
| Team | $4/mo | 2,000 | 90 days | 25 GB | 5 |
| Team Plus | $6/mo | 4,000 | 90 days | 40 GB | 10 |
| Scale | $12/mo | 10,000 | 365 days | 100 GB | unlimited |

The source of truth is `backend/drop_core/plans.py` (Stripe prices live in
Stripe and the `STRIPE_PRICE_*` variables). `backend/drop_core/tests_fix_docs.py`
fails when a doc or template states a different price or cap.

## Quickstart (local dev)

```bash
# Backend
python3 -m venv venv && ./venv/bin/pip install -r backend/requirements.txt
export DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key
./venv/bin/python backend/manage.py migrate
./venv/bin/python backend/manage.py createsuperuser   # dashboard login
./venv/bin/python backend/manage.py runserver          # :8000
```

Sign up at `http://localhost:8000/accounts/signup/` (or log in), open
`/app/`, create an API key, then:

```bash
curl -H "Authorization: Bearer vlf_..." \
     -F "file=@screenshot.png" \
     http://localhost:8000/api/v1/artifacts/
```

## Environment variables

No secrets are committed to this repo. Copy the names below into a local
`.env` (gitignored) or your host's env config. Billing is **live** in
production (`sk_live_...`, live price IDs, Stripe Managed Payments); use a
Stripe sandbox/test key only on a local or staging copy.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `DJANGO_SECRET_KEY` | prod | — | Required when `DJANGO_DEBUG` is false |
| `DJANGO_DEBUG` | no | `False` | Set `True` for local dev and for tests |
| `DJANGO_ALLOWED_HOSTS` | prod | — | Comma-separated, e.g. `veilfile.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | no | derived from `DJANGO_ALLOWED_HOSTS` | Comma-separated origins |
| `DATABASE_URL` | prod | sqlite (dev only) | Postgres URL. Production refuses to start without it, or with a `sqlite://` URL, unless `DJANGO_ALLOW_SQLITE=True`. User, password and name are percent-decoded; `?sslmode=...` is passed to the driver |
| `DJANGO_ALLOW_SQLITE` | no | — | `True` lets production mode run on SQLite on purpose (local `docker run`, image build) |
| `DJANGO_CONN_MAX_AGE` | no | `60` | Persistent DB connection lifetime (seconds) |
| `DROP_BASE_URL` | prod | `http://localhost:8000` | Public URL; artifact links are built from it |
| `DROP_STORAGE` | no | `local` | `local` (dev) or `s3` (prod) |
| `DROP_LOCAL_DIR` | no | `backend/media/artifacts` | Local storage root |
| `AWS_S3_ENDPOINT_URL` | prod (s3) | — | R2 endpoint, e.g. `https://<acct>.r2.cloudflarestorage.com` |
| `AWS_S3_BUCKET` | prod (s3) | — | R2 bucket name |
| `AWS_ACCESS_KEY_ID` | prod (s3) | — | R2 API token (access key) |
| `AWS_SECRET_ACCESS_KEY` | prod (s3) | — | R2 API token (secret) |
| `STRIPE_SECRET_KEY` | billing | — | Live secret key (`sk_live_…`) in production |
| `STRIPE_WEBHOOK_SECRET` | billing | — | Signing secret (`whsec_…`) from the same Stripe account and mode as the secret key |
| `STRIPE_PRICE_TEAM` | billing | — | Price ID for the $4/mo Team plan (tax-inclusive) |
| `STRIPE_PRICE_TEAMPLUS` | billing | — | Price ID for the $6/mo Team Plus plan (tax-inclusive). The name has no underscore between TEAM and PLUS |
| `STRIPE_PRICE_SCALE` | billing | — | Price ID for the $12/mo Scale plan (tax-inclusive) |
| `EMAIL_BACKEND` | prod | console | Set `django.core.mail.backends.smtp.EmailBackend`; without SMTP no new user can verify their email, mint keys or approve CLI logins (`veilfile.W001`) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | prod | — | SMTP settings (the live service uses Gmail SMTP) |
| `DEFAULT_FROM_EMAIL` | prod | — | From address, e.g. `support@veilfile.com` |
| `EMAIL_TIMEOUT` | no | `10` | SMTP timeout (seconds) |
| `VEILFILE_SAFETY_MODE` | no | `off` | `off`, `log` or `enforce`: SHA-256 blocklist screening of uploads. Unset is reported as `veilfile.W002` |
| `VEILFILE_CLIENT_IP_HEADER` | no | `CF-Connecting-IP` | Header carrying the visitor IP, set (and overwritten) by Cloudflare. Used for rate limits when it holds a valid IP and the request came from a Cloudflare edge address; otherwise the `X-Forwarded-For` hop logic below applies. Empty disables it |
| `VEILFILE_TRUSTED_PROXY_HOPS` | no | `2` | Trusted public proxy hops in front of the app. The client IP used for rate limits is the `X-Forwarded-For` entry that many positions from the right, after trailing private-network hops are skipped, when the last of them is a Cloudflare edge address. A request without one did not come through Cloudflare, and the last entry (the one the platform's proxy appended) is used. Confirm against real traffic before changing |
| `DJANGO_SECURE_HSTS_SECONDS` | no | `31536000` | HSTS max-age on HTTPS responses |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`, `DJANGO_SECURE_HSTS_PRELOAD` | no | `False` | Turn on only after every subdomain serves HTTPS |
| `PORT` | no | `8000` | Set by the host (Render); the entrypoint falls back to 8000 |
| `WEB_CONCURRENCY` | no | `1` | gunicorn workers (gthread) |
| `GUNICORN_THREADS` | no | `4` | Threads per worker. Each in-flight upload is held in memory; lower this on small instances |
| `GUNICORN_TIMEOUT`, `GUNICORN_GRACEFUL_TIMEOUT` | no | `120`, `30` | gunicorn timeouts (seconds) |
| `SKIP_MIGRATE` | no | unset | `1` skips `migrate` at boot (use with a Render pre-deploy command) |
| `VEILFILE_FUNNEL_RETENTION_DAYS` | no | `365` | How long `prune_funnel_events` keeps funnel rows |
| `VEILFILE_ALERT_DIR` | no | unset | Directory where CRITICAL safety alerts are also written as files, for an external alert bus; they always go to the log |
| `DJANGO_TEST_ALLOW_REMOTE_DB` | no | — | Lets `manage.py test` run against a non-local database server (CI with a service container) |

Without Stripe vars, checkout/switch/portal return `503 billing_not_configured` and the
dashboard shows a friendly note; everything else works.

## Tests

Tests need `DJANGO_DEBUG=True` (the test runner refuses `False`) and a local
database; it also forces local storage and strips `STRIPE_*`/`AWS_*`.

```bash
# Backend (the count changes; run it rather than trusting a number here)
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key \
  ../venv/bin/python manage.py test
../venv/bin/python manage.py makemigrations --check --dry-run   # must report no changes

# CLIs
cd npm && node --test
cd cli && ../venv/bin/python -m unittest discover -s tests -t .
```

CI (`.github/workflows/ci.yml`) runs all of the above plus
`manage.py check --deploy`, a production-settings `collectstatic`, and the
browser checks in `site-tests/` (jobs `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`; see AGENTS.md, "Site checks")
on every pull request to `main` (the browser checks skip a pull request that changes no page), and the backend and CLI checks on every push to it. It runs on GitHub-hosted runners, one machine for each job, with the jobs side by side (AGENTS.md, "Where CI runs").

## Ops

- **Deploy:** Render Docker web service (serves veilfile.com), auto-deploys every commit to `main` after its CI checks pass, with a paid Postgres. `render.yaml` mirrors it. The container runs as a non-root
  user. Boot sequence (`backend/entrypoint.sh`): `migrate --noinput` (skipped
  when `SKIP_MIGRATE=1`), `createcachetable`, a background daily housekeeping
  loop, then gunicorn.
- **Health check:** `GET /healthz` returns `200 {"status":"ok"}` after a
  `SELECT 1`, or `503`. Set it as the Render Health Check Path.
- **Daily housekeeping:** the entrypoint loop runs `sweep_expired`,
  `clearsessions`, `cleartokens`, `prune_funnel_events`, `prune_email_ips`
  (clears send-log IPs after 90 days) and `purge_expired_cache` once a day. No separate cron job is needed. Uploads
  also trigger a small lazy sweep.
- **TTL sweeper:** `python backend/manage.py sweep_expired` deletes expired
  artifacts (files + rows). Idempotent. Prints `swept=N bytes=N failed=N` and
  exits non-zero when any storage delete failed (those rows are kept for the
  next run); `--limit N` / `--time-budget SECONDS` bound a run.
- **Orphaned files:** `python backend/manage.py reconcile_storage` lists stored
  objects with no artifact row; `--delete` removes them. Run it by hand, dry
  run first: `--delete` refuses when the database looks wrong (empty artifact
  table, more than 20% orphans, or R2 storage without PostgreSQL) unless you
  pass `--force` after checking the `db=` / `storage=` it prints.
- **Billing resync:** `python backend/manage.py sync_stripe_billing` (dry run;
  `--apply` to write) rebuilds workspace plans from Stripe. The Stripe setup steps are in the private ops repository.
- **Deploy checks:** `manage.py check --deploy` reports `veilfile.W001`-`W006`
  misconfigurations (also logged at ERROR by every worker at boot).
- **Local container:** `docker run` needs `DATABASE_URL=sqlite:////tmp/db.sqlite3`
  together with `DJANGO_ALLOW_SQLITE=True` (production mode otherwise refuses
  SQLite); `/app/backend` is read-only to the non-root user.
- **Storage:** Render's disk is ephemeral. **Production must use
  `DROP_STORAGE=s3`** (Cloudflare R2).
- **MCP registries:** launch checklist in `docs/REGISTRIES.md`.

## Project layout

```
backend/
  config/          Django settings/URLs (env-driven)
  drop_core/       Workspace, APIKey (vlf_), Artifact, plans, storage,
                   secret scan, agent API, key mgmt, device login, OAuth
                   glue, sweeper
  drop_billing/    Stripe checkout/switch/portal/status/webhook (live,
                   tax-inclusive, Managed Payments)
  drop_mcp/        MCP server (POST /mcp)
  blog/, docs/     Markdown-rendered Journal posts (and their feed) and Docs pages
  legal/           Terms, privacy policy, abuse reporting (Markdown)
  pages/           public-page registry, sitemap, component catalog, content checks
  static/          the new shell's CSS, JS, fonts and images
  dashboard/       the signed-in dashboard at /app/ (views, URLs, tests)
  templates/       shell.html, layouts/, chapters/ and components/, the Journal, Docs and legal pages, the form pages (sign in, device, OAuth), the dashboard and the error pages
cli/, npm/         Python and Node `veilfile` command-line clients
plugin/            Claude Code plugin (share-artifact skill)
docs/              API.md, llms.txt (served at /llms.txt), REGISTRIES.md,
                   superpowers/ (design specs and build plans)
site-tests/        browser checks for the new shell (CI jobs `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog`, `site-dashboard`)
tools/             one-off font and image generators, run by hand
Dockerfile         one Python stage, WhiteNoise + gunicorn (the site has no build step)
render.yaml        Render deploy blueprint (mirrors the live service)
AGENTS.md          Rules and commands for agents working on this repo
```

## License and contributions

The code is published so that anyone can read it and check what Veilfile does.
All rights are reserved (LICENSE), except for the command-line clients in `cli/` and `npm/`, which
are under the MIT License. This repository does not accept pull requests from outside contributors. Issues are
welcome, and a security problem should be reported privately with "Report a vulnerability" on the
repository's Security tab.

## Launch checklist

1. Create Cloudflare R2 bucket + API token (S3-compatible).
2. Create Stripe products/prices (Team $4, Team Plus $6, Scale $12, tax-inclusive) with product tax codes;
   note the price IDs.
3. Deploy on Render (see `render.yaml`); set env vars incl. `DROP_BASE_URL`,
   `DATABASE_URL` and the `EMAIL_*` SMTP settings; set the Health Check Path
   to `/healthz`.
4. Point the Stripe webhook at `https://<host>/api/v1/billing/webhook`;
   set `STRIPE_WEBHOOK_SECRET`.
5. Decide `VEILFILE_SAFETY_MODE` (`log` or `enforce`).
6. List the MCP server per `docs/REGISTRIES.md`.

Expired artifacts are swept daily by the entrypoint loop; no cron job is needed.
