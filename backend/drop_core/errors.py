"""Unified error envelope for the DRF API.

Every API error answers ``{"error": "<human message>", "code": "<stable code>"}``.
DRF's default handler answers auth/throttle/validation failures with
``{"detail": ...}``; this exception handler rewrites those into the envelope
while keeping HTTP status codes and headers (``WWW-Authenticate``,
``Retry-After``) unchanged. View-raised ``{"error", "code"}`` responses pass
through untouched. The MCP endpoint is plain JSON-RPC, not DRF, and is
unaffected.

``answer_response`` sends an ``Answer`` of ``drop_core.account`` or
``drop_billing.account`` (the functions the dashboard calls too) as the
Response the JSON endpoint has always sent.
"""

from rest_framework.response import Response
from rest_framework.views import exception_handler

_CODE_BY_STATUS = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limited",
}


def _message(detail):
    if isinstance(detail, dict):
        parts = []
        for field, errors in detail.items():
            if isinstance(errors, (list, tuple)):
                errors = ", ".join(str(e) for e in errors)
            parts.append(f"{field}: {errors}")
        return "; ".join(parts)
    if isinstance(detail, (list, tuple)):
        return "; ".join(str(d) for d in detail)
    return str(detail)


def veilfile_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    data = response.data
    if isinstance(data, dict) and "error" not in data and "detail" in data:
        response.data = {
            "error": _message(data["detail"]),
            "code": _CODE_BY_STATUS.get(response.status_code, "request_failed"),
        }
    return response


def answer_response(answer):
    """The DRF Response for an ``Answer``: its status, its body and its headers. A 204 has no body."""
    headers = dict(answer.headers)
    if answer.status == 204:
        return Response(status=204, headers=headers)
    return Response(answer.body, status=answer.status, headers=headers)
