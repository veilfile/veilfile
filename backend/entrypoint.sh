#!/bin/sh
# Veilfile container entrypoint.
#
# Runs migrations, creates the shared cache table, starts the daily
# housekeeping loop in the background (sweep expired artifacts, clear expired
# sessions and OAuth tokens, prune old funnel events, purge expired cache
# rows; upload traffic also triggers a lazy sweep via maybe_sweep(), so
# expiry is honored on every layer), then serves with gunicorn.
# exec keeps gunicorn as PID 1 for clean signal handling.
set -e

# Migrations run at boot by default. To run them once per deploy instead
# (before the new container takes traffic, so a failed migration fails the
# deploy rather than crash-looping it), set the Render preDeployCommand to
#   python manage.py migrate --noinput
# and set SKIP_MIGRATE=1 on the service.
if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
    python manage.py migrate --noinput
fi

# Rate limits and throttles live in a DatabaseCache table in production
# (config/settings.py CACHES). Idempotent: an existing table is left alone.
python manage.py createcachetable

# Daily housekeeping. The first pass runs right away, alongside gunicorn's
# boot, so it no longer delays binding the port.
(
    while true; do
        python manage.py sweep_expired || true
        python manage.py clearsessions || true
        python manage.py cleartokens || true
        python manage.py prune_funnel_events || true
        python manage.py prune_email_ips || true
        python manage.py purge_expired_cache || true
        sleep 86400
    done
) &

# gthread workers: the worker heartbeat no longer depends on a request
# finishing, so a slow client or a long 25 MiB transfer does not get the
# worker killed at the 30 s default, and each worker serves several requests
# at once. One worker x four threads is the default on the 512 MB instance (two
# idle at about 260 MB), and each in-flight upload is held in memory, so watch the memory
# metrics; raise WEB_CONCURRENCY / GUNICORN_THREADS with the instance size,
# or lower GUNICORN_THREADS if it runs short. (No --max-requests: gunicorn
# 23's gthread worker drops connections it has accepted but not yet served
# when it recycles.)
#
# The access log goes to stdout through config.gunicorn_logging, which
# redacts capability tokens (/a/<token>, upload, verification and reset
# links), query strings and Referer paths before a line is written.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --access-logfile - \
    --logger-class config.gunicorn_logging.RedactingLogger
