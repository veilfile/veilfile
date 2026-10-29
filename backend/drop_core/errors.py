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

import math
import re

from rest_framework import exceptions
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


_METHOD_RE = re.compile(r'Method "(.*)" not allowed\.', re.DOTALL)
_MEDIA_TYPE_RE = re.compile(r'Unsupported media type "(.*)" in request\.', re.DOTALL)
_JSON_PARSE_PREFIX = "JSON parse error - "
_MULTIPART_PARSE_PREFIX = "Multipart form parse error - "
_CSRF_PREFIX = "CSRF Failed: "


def _throttled_message(exc):
    wait = getattr(exc, "wait", None)
    if wait is None:
        return "Too many uploads in the past hour. Try again later."
    seconds = math.ceil(wait)
    if seconds == 1:
        return "Too many uploads in the past hour. Wait 1 second, then try again."
    return f"Too many uploads in the past hour. Wait {seconds} seconds, then try again."


def _veilfile_text(exc):
    """The approved words for a text the library wrote, or None.

    Only the library's own default texts are replaced, chosen by exception
    class; a message a view passed on purpose goes through as it is. Status
    codes and error codes are not touched.
    """
    if not isinstance(exc, exceptions.APIException):
        return None
    detail = exc.detail
    if not isinstance(detail, str):
        return None
    text = str(detail)
    if isinstance(exc, exceptions.Throttled):
        if text.startswith(str(exc.default_detail)):
            return _throttled_message(exc)
        return None
    if isinstance(exc, exceptions.NotAuthenticated):
        if text == str(exc.default_detail):
            return (
                "The request has no API key or sign-in. Send an API key as "
                "Authorization: Bearer <API key>. Endpoints that need a "
                "sign-in are used from the dashboard."
            )
        return None
    if isinstance(exc, exceptions.PermissionDenied):
        if text == str(exc.default_detail):
            return (
                "This request is not allowed for the API key, connected app "
                "or sign-in that made it. The API documentation says which "
                "ones can make it."
            )
        if text.startswith(_CSRF_PREFIX):
            reason = text[len(_CSRF_PREFIX):]
            return f"The CSRF check failed: {reason} Reload the page, then try again."
        return None
    if isinstance(exc, exceptions.ParseError):
        if text == str(exc.default_detail):
            return "The request could not be parsed. Check that the body is well formed."
        if text.startswith(_JSON_PARSE_PREFIX):
            reason = text[len(_JSON_PARSE_PREFIX):]
            return (
                f"The request body is not valid JSON ({reason}). "
                "Fix the JSON and send the request again."
            )
        if text.startswith(_MULTIPART_PARSE_PREFIX):
            reason = text[len(_MULTIPART_PARSE_PREFIX):]
            return (
                f"The multipart form body could not be read ({reason}). "
                "Send a well-formed multipart form."
            )
        return None
    if isinstance(exc, exceptions.NotFound):
        if text == str(exc.default_detail):
            return "Nothing exists at this URL. Check the path."
        return None
    if isinstance(exc, exceptions.MethodNotAllowed):
        match = _METHOD_RE.fullmatch(text)
        if match:
            return (
                f"The method {match.group(1)} is not allowed for this URL. "
                "The Allow response header lists the methods it accepts."
            )
        return None
    if isinstance(exc, exceptions.UnsupportedMediaType):
        match = _MEDIA_TYPE_RE.fullmatch(text)
        if match:
            return (
                f'The content type "{match.group(1)}" is not accepted here. '
                "Set the Content-Type header to one the endpoint accepts, "
                "such as application/json."
            )
        return None
    if isinstance(exc, exceptions.NotAcceptable):
        if text == str(exc.default_detail):
            return (
                "The API cannot send a response in the format the Accept "
                "header asks for. Ask for application/json, or leave the "
                "header out."
            )
        return None
    return None


def veilfile_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    data = response.data
    if isinstance(data, dict) and "error" not in data and "detail" in data:
        response.data = {
            "error": _veilfile_text(exc) or _message(data["detail"]),
            "code": _CODE_BY_STATUS.get(response.status_code, "request_failed"),
        }
    return response


def answer_response(answer):
    """The DRF Response for an ``Answer``: its status, its body and its headers. A 204 has no body."""
    headers = dict(answer.headers)
    if answer.status == 204:
        return Response(status=204, headers=headers)
    return Response(answer.body, status=answer.status, headers=headers)
