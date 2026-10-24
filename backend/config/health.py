"""Health check for Render: GET /healthz.

Answers 200 {"status": "ok"} after one trivial database round trip
(SELECT 1) and 503 {"status": "unavailable"} when the database cannot be
reached, so Render keeps a broken deploy out of rotation and restarts an
instance that has lost its database. Deliberately cheap: no auth, no
session, no storage call (a bucket probe every few seconds would cost an
R2 request each time), no funnel event.
"""

import logging

from django.db import connection
from django.http import HttpResponseNotAllowed, JsonResponse

logger = logging.getLogger(__name__)

HEALTH_PATHS = ("/healthz", "/healthz/")


def healthz(request):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # any database error means "not ready"
        logger.error("Health check failed: database unavailable (%s: %s)",
                     type(exc).__name__, exc)
        response = JsonResponse({"status": "unavailable"}, status=503)
    else:
        response = JsonResponse({"status": "ok"})
    response["Cache-Control"] = "no-store"
    return response


class HealthCheckMiddleware:
    """Answer /healthz ahead of the rest of the middleware stack.

    Listed first in MIDDLEWARE so the probe skips ALLOWED_HOSTS validation
    (Render sends one of the verified custom domains, or the onrender.com
    host, as Host; a domain verified later must not turn every probe into a
    400), sessions and messages. The URL route in config.urls points at the
    same view and only matters if this middleware is removed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info in HEALTH_PATHS:
            return healthz(request)
        return self.get_response(request)
