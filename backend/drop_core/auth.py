"""API-key authentication for the agent-facing v1 API, plus the parallel
OAuth 2.0 credential (Phase 2: MCP-spec OAuth).

Both credentials resolve to the same Workspace principal: an OAuth bearer
with the ``mcp:use`` scope gets the agent surface of a ``vlf_`` key
(artifacts, usage, MCP); /api/v1/keys refuses OAuth callers. Neither
resolves for a deactivated user, and OAuth also requires a verified email.
The ``vlf_`` path is checked first; OAuth is only attempted when the
fast-path rejects."""

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from . import oauth as oauth_layer
from .models import APIKey


class APIKeyAuthentication(BaseAuthentication):
    """Parses ``Authorization: Bearer <credential>`` and resolves it to an
    APIKey or an OAuthCredentialAdapter.

    Returns ``(None, credential)`` on success so ``request.auth`` carries the
    credential while ``request.user`` stays anonymous.
    """

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None  # no attempt -> permission layer turns this into 401
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthenticationFailed("Invalid Authorization header.")
        credential = parts[1]
        # vlf_ fast-path (unchanged behavior).
        api_key = APIKey.verify(credential)
        if api_key is not None:
            api_key.last_used_at = timezone.now()
            api_key.save(update_fields=["last_used_at"])
            return (None, api_key)
        # Parallel OAuth credential (mcp:use scope).
        adapter = oauth_layer.resolve_oauth_credential(credential)
        if adapter is None:
            raise AuthenticationFailed("Invalid API key.")
        return (None, adapter)

    def authenticate_header(self, request):
        return "Bearer"


def _has_credential(auth):
    """True for any workspace credential: APIKey or OAuth adapter."""
    return auth is not None and getattr(auth, "workspace", None) is not None


class IsAPIKeyAuthenticated(BasePermission):
    """Grants access when the request was authenticated with an API key or
    an OAuth token (identical permission surface)."""

    def has_permission(self, request, view):
        return _has_credential(request.auth)


class IsAPIKeyOrSessionAuthenticated(BasePermission):
    """Grants access to API-key/OAuth callers OR session-authenticated
    dashboard users."""

    def has_permission(self, request, view):
        if _has_credential(request.auth):
            return True
        return bool(request.user and request.user.is_authenticated)
