"""The library texts the error envelope rewrites into the approved words (PR 8, C26 to C36).

Each test raises the library's own exception the way the library does and
checks the whole "error" text, the status and the code, which stay as they were.
"""

from django.test import SimpleTestCase
from rest_framework import exceptions

from drop_core.errors import veilfile_exception_handler


def handled(exc):
    response = veilfile_exception_handler(exc, {})
    return response.status_code, response.data


class LibraryTextTests(SimpleTestCase):
    def check(self, exc, status, code, error):
        got_status, body = handled(exc)
        self.assertEqual(got_status, status)
        self.assertEqual(body, {"error": error, "code": code})

    def test_not_authenticated(self):
        self.check(
            exceptions.NotAuthenticated(), 401, "unauthorized",
            "The request has no API key or sign-in. Send an API key as Authorization: "
            "Bearer <API key>. Endpoints that need a sign-in are used from the dashboard.",
        )

    def test_permission_denied(self):
        self.check(
            exceptions.PermissionDenied(), 403, "forbidden",
            "This request is not allowed for the API key, connected app or sign-in that "
            "made it. The API documentation says which ones can make it.",
        )

    def test_csrf_failed(self):
        self.check(
            exceptions.PermissionDenied("CSRF Failed: CSRF token missing."), 403, "forbidden",
            "The CSRF check failed: CSRF token missing. Reload the page, then try again.",
        )

    def test_throttled_variants(self):
        base = "Too many uploads in the past hour."
        self.check(exceptions.Throttled(wait=60), 429, "rate_limited", f"{base} Wait 60 seconds, then try again.")
        self.check(exceptions.Throttled(wait=1), 429, "rate_limited", f"{base} Wait 1 second, then try again.")
        self.check(exceptions.Throttled(wait=0.4), 429, "rate_limited", f"{base} Wait 1 second, then try again.")
        self.check(exceptions.Throttled(), 429, "rate_limited", f"{base} Try again later.")

    def test_parse_errors(self):
        self.check(
            exceptions.ParseError(), 400, "invalid_request",
            "The request could not be parsed. Check that the body is well formed.",
        )
        self.check(
            exceptions.ParseError("JSON parse error - Expecting value: line 1 column 2 (char 1)"),
            400, "invalid_request",
            "The request body is not valid JSON (Expecting value: line 1 column 2 (char 1)). "
            "Fix the JSON and send the request again.",
        )
        self.check(
            exceptions.ParseError("Multipart form parse error - Invalid boundary in multipart: None"),
            400, "invalid_request",
            "The multipart form body could not be read (Invalid boundary in multipart: None). "
            "Send a well-formed multipart form.",
        )

    def test_not_found(self):
        self.check(exceptions.NotFound(), 404, "not_found", "Nothing exists at this URL. Check the path.")

    def test_method_not_allowed(self):
        self.check(
            exceptions.MethodNotAllowed("PATCH"), 405, "method_not_allowed",
            "The method PATCH is not allowed for this URL. The Allow response header lists "
            "the methods it accepts.",
        )

    def test_unsupported_media_type(self):
        self.check(
            exceptions.UnsupportedMediaType("text/xml"), 415, "request_failed",
            'The content type "text/xml" is not accepted here. Set the Content-Type header to '
            "one the endpoint accepts, such as application/json.",
        )

    def test_not_acceptable(self):
        self.check(
            exceptions.NotAcceptable(), 406, "request_failed",
            "The API cannot send a response in the format the Accept header asks for. "
            "Ask for application/json, or leave the header out.",
        )

    def test_a_message_a_view_chose_goes_through(self):
        self.check(exceptions.NotFound("No such key."), 404, "not_found", "No such key.")
        self.check(exceptions.PermissionDenied("Nope."), 403, "forbidden", "Nope.")
