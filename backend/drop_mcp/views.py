"""Veilfile MCP server: hosted MCP endpoint.

Plain JSON-RPC 2.0 over HTTP POST (no SSE for MVP). A single request object
gets a single response object; notifications (and client responses) get
202 with no body. Dependency-free: stdlib only, no MCP SDK.

Wire-up: add 'drop_mcp' to INSTALLED_APPS and include drop_mcp.urls
(e.g. ``path('', include('drop_mcp.urls'))``) in the project URLconf so the
endpoint is served at POST /mcp.
"""
import base64
import binascii
import json
import logging
import math
import mimetypes
import re
import uuid as uuid_module
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse, UnreadablePostError
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from drop_core import funnel
from drop_core.email_verification import client_ip
from drop_core.models import APIKey, Artifact
from drop_core.oauth import resolve_oauth_credential
from drop_core.plans import PLANS
from drop_core.secrets import SECRET_FLAGS_WARNING
from drop_core.services import (
    ALLOWED_EXTENSIONS,
    REVOKE_PENDING_DELETE,
    StorageError,
    mint_upload_url,
    revoke_artifact,
    upload_artifact,
)
from drop_core.views import ERROR_MESSAGES, APIKeyUploadThrottle

logger = logging.getLogger(__name__)

MAX_DECODED_BYTES = 25 * 1024 * 1024  # 25 MiB, matches the REST upload cap
# Largest request body the endpoint reads: a 25 MiB file as base64 (about
# 33.3 MiB), with room for line-wrapped base64 and the JSON envelope. The
# body is read through a bounded stream, never request.body, so Django's
# 2.5 MiB DATA_UPLOAD_MAX_MEMORY_SIZE does not apply here.
MAX_BODY_BYTES = 36 * 1024 * 1024
# Bodies up to this size are parsed with no further checks: it is the
# 2.5 MiB DATA_UPLOAD_MAX_MEMORY_SIZE default that capped this endpoint
# before. A larger body is parsed only when it is shaped like an upload,
# because json.loads can turn 36 MiB into more memory than the instance
# has: many small values become ~24x their size in Python objects, and one
# character above U+00FF makes the decoded text and its string 2-4x wider.
PLAIN_BODY_BYTES = 2621440
# JSON structural bytes ({ [ , :) allowed in a larger body. Every JSON
# value but the first follows one, so this bounds the number of values;
# a real upload_artifact call has a few dozen.
MAX_LARGE_BODY_STRUCTURE = 1000
# Unauthenticated requests are answered 401 before the body is parsed; a
# body up to this size is parsed only to echo the request id.
MAX_UNAUTHENTICATED_BODY_BYTES = 64 * 1024
# An authenticated body over MAX_BODY_BYTES is read and thrown away up to
# this declared size, so the client sees the 413 (and its create_upload_url
# hint) instead of a reset; anything larger is refused unread.
MAX_DRAIN_BYTES = 2 * MAX_BODY_BYTES
# Chunk size for reading and discarding a body that will not be parsed.
_DRAIN_CHUNK_BYTES = 64 * 1024
# APIKey.last_used_at is written at most this often per key.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=1)
# Latest wire protocol this server speaks. The handshake only uses the
# initialize / tools flows, which are unchanged between these versions, so
# both are supported and negotiated honestly below.
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED_PROTOCOL_VERSIONS = ('2024-11-05', '2025-06-18')
SERVER_NAME = 'veilfile-mcp'
SERVER_VERSION = '0.1.0'

# JSON-RPC error codes
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_UNAUTHORIZED = -32001  # server-defined: bad/missing API key

# The order the approved words list the extensions in. Which extensions are
# listed comes from ALLOWED_EXTENSIONS, so the words follow the code.
_EXTENSION_ORDER = ('png', 'jpg', 'jpeg', 'webp', 'gif', 'txt', 'md', 'log', 'json', 'har', 'pdf')


def _allowed_extensions_text():
    """The allowed extensions as words, e.g. "png, jpg and pdf", from drop_core.services."""
    names = [e for e in _EXTENSION_ORDER if e in ALLOWED_EXTENSIONS]
    names += sorted(ALLOWED_EXTENSIONS - set(_EXTENSION_ORDER))
    return ', '.join(names[:-1]) + ' and ' + names[-1]


def _plan_ttl_text():
    """The plan maximums as words: "7 days on Free, 90 on Team and 365 on Scale"."""
    free, team, scale = (PLANS[key]['ttl_days'] for key in ('free', 'team', 'scale'))
    return '%d days on Free, %d on Team and %d on Scale' % (free, team, scale)


TOOLS = [
    {
        'name': 'upload_artifact',
        'title': 'Upload a file',
        'description': 'Upload a file from base64 content and return its id, its private share link as url, expires_at, size_bytes and secret_flags. The link stops working when the file expires. The decoded file can be up to 25 MiB; for larger files, create_upload_url takes the bytes by PUT instead. The secret scan reads the first 1 MiB of the file, and secret_flags names each kind of secret-shaped content it finds. With on_secret "flag", the default, a flagged file is stored and the result adds a warning and secret_flag_locations. With on_secret "reject", a flagged file is not stored and the call returns the secret_detected error.',
        'annotations': {
            'title': 'Upload a file',
            'readOnlyHint': False,
            'destructiveHint': False,
            'idempotentHint': False,
            'openWorldHint': False,
        },
        'inputSchema': {
            'type': 'object',
            'properties': {
                'filename': {
                    'type': 'string',
                    'description': ('This is the file name, including its extension, such as screenshot.png. The allowed extensions are ' + _allowed_extensions_text() + ", and the file's contents must match its extension."),
                },
                'content_base64': {
                    'type': 'string',
                    'description': "This is the file's content as standard base64. Line breaks and a data:<mime>;base64, prefix are accepted. The decoded file can be up to 25 MiB.",
                },
                'mime_type': {
                    'type': 'string',
                    'description': "The server does not use this value; it takes the file's type from the extension in filename. If you send it, it must be a string.",
                },
                'ttl_days': {
                    'type': 'integer',
                    'description': ("This is the number of days until the link expires, as a whole number of 1 or more. If you leave it out, the link expires at the plan's maximum, which is " + _plan_ttl_text() + ". A larger value is lowered to the plan's maximum, and the result then includes ttl_adjusted: true. A value of zero or less is rejected. The result's expires_at is the applied expiry."),
                },
                'on_secret': {
                    'type': 'string',
                    'description': 'The value is either "flag" or "reject", and the default is "flag". With "flag", a file with secret-shaped content is stored, and the result lists secret_flags and adds a warning and secret_flag_locations. With "reject", that file is not stored and the call returns the secret_detected error.',
                },
            },
            'required': ['filename', 'content_base64'],
        },
    },
    {
        'name': 'create_upload_url',
        'title': 'Create an upload URL',
        'description': "Create a one-time upload URL for a file too large to send as base64 through upload_artifact. The result has upload_url, expires_at, max_size_bytes (25 MiB), ttl_days and filename. The URL is valid for 15 minutes and needs no API key. A PUT of the file's raw bytes to it, for example with curl --upload-file, runs the same type check and secret scan as upload_artifact and answers 201 with the same fields that upload_artifact returns. The first successful PUT uses up the URL. A PUT that fails can be retried until the URL expires, except one blocked as content_not_allowed. The URL also stops working if the API key that created it is revoked or the connected app that created it is disconnected.",
        'annotations': {
            'title': 'Create an upload URL',
            'readOnlyHint': False,
            'destructiveHint': False,
            'idempotentHint': False,
            'openWorldHint': False,
        },
        'inputSchema': {
            'type': 'object',
            'properties': {
                'filename': {
                    'type': 'string',
                    'description': ('This is the file name, including its extension, such as screenshot.png. The extension is checked when the URL is created, and the allowed extensions are ' + _allowed_extensions_text() + '.'),
                },
                'ttl_days': {
                    'type': 'integer',
                    'description': ("This is the number of days until the link expires, as a whole number of 1 or more. If you leave it out, the link expires at the plan's maximum, which is " + _plan_ttl_text() + ". A larger value is lowered to the plan's maximum, and the result then includes ttl_adjusted: true. A value of zero or less is rejected. The days count from the PUT, and the result's ttl_days is the applied value."),
                },
                'on_secret': {
                    'type': 'string',
                    'description': 'The value is either "flag" or "reject", and the default is "flag". With "reject", a PUT of a flagged file fails with the secret_detected error, the file is not stored and the URL stays usable until it expires. With "flag", a flagged file is stored and the result lists its secret_flags and adds a warning and secret_flag_locations.',
                },
            },
            'required': ['filename'],
        },
    },
    {
        'name': 'list_artifacts',
        'title': 'List files',
        'description': 'List the files in this workspace, newest first. Each entry has id, filename, mime_type, size_bytes, url, expires_at, secret_flags and created_at. Expired files that are not yet deleted are included. The tool changes nothing.',
        'annotations': {
            'title': 'List files',
            'readOnlyHint': True,
            'destructiveHint': False,
            'idempotentHint': True,
            'openWorldHint': False,
        },
        'inputSchema': {
            'type': 'object',
            'properties': {
                'limit': {
                    'type': 'integer',
                    'description': 'This is the maximum number of files to return. The range is 1 to 200, the default is 50, and a value outside the range is moved to the nearest end of it. Files beyond the limit are not returned, and there is no paging.',
                    'default': 50,
                    'minimum': 1,
                    'maximum': 200,
                },
            },
        },
    },
    {
        'name': 'revoke_artifact',
        'title': 'Revoke a file',
        'description': 'Revoke a file by its id. Its link stops working at once and the file is deleted. The result is revoked: true, or revoked: false when this workspace has no file with that id. If storage could not be reached, the result adds storage_deleted: false and a later sweep finishes the deletion. Repeating the call is harmless.',
        'annotations': {
            'title': 'Revoke a file',
            'readOnlyHint': False,
            'destructiveHint': True,
            'idempotentHint': True,
            'openWorldHint': False,
        },
        'inputSchema': {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'string',
                    'description': 'This is the id of the file to revoke, a UUID. Both upload_artifact and list_artifacts return it.',
                },
            },
            'required': ['id'],
        },
    },
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _rpc_result(request_id, result):
    return JsonResponse({'jsonrpc': '2.0', 'id': request_id, 'result': result})


def _rpc_error(request_id, code, message, http_status=200, data=None):
    error = {'code': code, 'message': message}
    if data is not None:
        error['data'] = data
    return JsonResponse(
        {'jsonrpc': '2.0', 'id': request_id, 'error': error},
        status=http_status,
    )


def _authenticate(request):
    """Return the credential for ``Authorization: Bearer <credential>``.

    Accepts a ``vlf_`` API key exactly as before, or a DOT OAuth access
    token (valid: row exists, unexpired, ``mcp:use`` scope) wrapped in an
    OAuthCredentialAdapter. Both expose ``.workspace``; tools use only
    that, except the services' ``api_key`` FK, which is None for OAuth.
    """
    auth = request.headers.get('Authorization', '')
    scheme, _, credentials = auth.partition(' ')
    if scheme.lower() != 'bearer' or not credentials.strip():
        return None
    credential = credentials.strip()
    key = APIKey.verify(credential)
    if key is not None:
        _touch_last_used(key)
        return key
    return resolve_oauth_credential(credential)


def _touch_last_used(key):
    """Record MCP use on the key, as the REST auth does, but write at most
    once per LAST_USED_WRITE_INTERVAL so a busy agent is not a write per call."""
    now = timezone.now()
    if key.last_used_at is None or now - key.last_used_at >= LAST_USED_WRITE_INTERVAL:
        APIKey.objects.filter(pk=key.pk).update(last_used_at=now)
        key.last_used_at = now


def _origin_of(url):
    try:
        parts = urlsplit(url.strip())
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f'{parts.scheme}://{parts.netloc}'.lower()


def _origin_allowed(request):
    """DNS-rebinding guard (MCP Streamable HTTP): a browser request carries
    an Origin header, and only Veilfile's own origins may use the endpoint.
    Agents and SDK clients send no Origin and are unaffected."""
    origin = request.headers.get('Origin')
    if not origin:
        return True
    allowed = {
        _origin_of(url)
        for url in [settings.DROP_BASE_URL, *getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])]
    }
    allowed.discard(None)
    return _origin_of(origin) in allowed


class _BodyTooLarge(Exception):
    pass


def _declared_length(request):
    try:
        return int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        return 0


def _read_body(request, limit):
    """Read the request body, refusing anything over ``limit`` bytes.

    Checks the declared Content-Length first so an oversized body is never
    read, then reads at most ``limit + 1`` bytes from the stream.
    """
    if _declared_length(request) > limit:
        raise _BodyTooLarge
    body = request.read(limit + 1)
    while len(body) <= limit:
        chunk = request.read(limit + 1 - len(body))
        if not chunk:
            break
        body += chunk
    if len(body) > limit:
        raise _BodyTooLarge
    return body


def _discard_body(request, ceiling):
    """Read and throw away the rest of a body declared at most ``ceiling``
    bytes, in fixed-size chunks (bandwidth, no memory).

    Answering before the client has sent its whole body reaches it as a
    connection reset (gunicorn closes the socket) instead of the response,
    e.g. the 401 an OAuth client needs to refresh an expired token.
    """
    if _declared_length(request) > ceiling:
        return
    try:
        while request.read(_DRAIN_CHUNK_BYTES):
            pass  # bounded by the declared length (Django's LimitedStream)
    except UnreadablePostError:
        pass


_WIDE_UTF8 = re.compile(rb'[\xc4-\xff]')  # UTF-8 lead byte of U+0100 and above
_WIDE_ESCAPE = re.compile(rb'\\u(?!00)')  # a JSON \u escape above U+00FF


def _large_body_is_plain(body):
    """True when a body over PLAIN_BODY_BYTES is safe to hand to json.loads:
    a bounded number of JSON values and no characters above U+00FF.

    Byte scans only (no copy of the body); 0.1-0.2 s on 36 MiB. Bytes
    inside strings count too, which only ever refuses more: base64 has
    none of them.
    """
    structure = 0
    for byte in (b'{', b'[', b',', b':'):
        structure += body.count(byte)
        if structure > MAX_LARGE_BODY_STRUCTURE:
            return False
    if not body.isascii() and _WIDE_UTF8.search(body):
        return False
    return _WIDE_ESCAPE.search(body) is None


_PARSE_FAILED = object()


def _parse_json(body):
    """Parse a JSON body (UTF-8 bytes or decoded text), or return
    _PARSE_FAILED. Deeply nested input (RecursionError in the decoder) is
    a parse error, not a 500."""
    try:
        if isinstance(body, bytes):
            body = body.decode('utf-8')
        return json.loads(body)
    except (ValueError, RecursionError):
        return _PARSE_FAILED


def _valid_id(value):
    """JSON-RPC ids are strings or numbers (null tolerated)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return math.isfinite(value)  # json.loads accepts NaN / Infinity
    return value is None or isinstance(value, (str, int))


def _peek_request_id(request):
    """Best-effort request id for a 401.

    Only a body of at most MAX_UNAUTHENTICATED_BODY_BYTES is kept and
    parsed, so an unauthenticated caller never makes the server buffer or
    parse much. A larger body within MAX_BODY_BYTES is read and thrown away
    in chunks first, so the client sees the 401 (see _discard_body).
    """
    try:
        body = _read_body(request, MAX_UNAUTHENTICATED_BODY_BYTES)
    except _BodyTooLarge:
        _discard_body(request, MAX_BODY_BYTES)
        return None
    except UnreadablePostError:
        return None
    payload = _parse_json(body)
    if isinstance(payload, dict) and _valid_id(payload.get('id')):
        return payload.get('id')
    return None


def _service_api_key(key):
    """The services' ``api_key`` FK: the APIKey row for vlf_ keys, None for
    OAuth (artifacts minted over OAuth simply have no key row)."""
    return key if isinstance(key, APIKey) else None


def _unauthenticated(request_id):
    """401 with the JSON-RPC error body plus the MCP-spec WWW-Authenticate
    challenge pointing at the protected-resource metadata document."""
    response = _rpc_error(
        request_id,
        ERR_UNAUTHORIZED,
        'The request has no valid API key or connected app access. Send a current '
        'API key as Authorization: Bearer <API key>, or reconnect the app.',
        http_status=401,
    )
    metadata_url = (
        settings.DROP_BASE_URL.rstrip('/')
        + '/.well-known/oauth-protected-resource'
    )
    response["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata_url}"'
    return response


def _artifact_url(artifact):
    base = settings.DROP_BASE_URL.rstrip('/')
    return f'{base}/a/{artifact.token}'


def _artifact_json(artifact):
    return {
        'id': str(artifact.id),
        'filename': artifact.filename,
        'mime_type': artifact.mime_type,
        'size_bytes': artifact.size_bytes,
        'url': _artifact_url(artifact),
        'expires_at': artifact.expires_at.isoformat() if artifact.expires_at else None,
        'secret_flags': list(artifact.secret_flags or []),
        'created_at': artifact.created_at.isoformat() if artifact.created_at else None,
    }


class ToolError(Exception):
    """A tool execution failure: caps, type rejection, secret rejection,
    throttling, storage outage.

    Per the MCP spec these go back as a tool result with ``isError: true``
    (so the model sees them), not as JSON-RPC protocol errors. The text is
    the REST error envelope: ``{"error", "code"}`` plus optional ``detail``
    and extras such as ``retry_after``.
    """

    def __init__(self, code, message, detail='', **extra):
        super().__init__(code)
        self.code = code
        self.message = message
        self.detail = detail
        self.extra = extra

    def body(self):
        body = {'error': self.message, 'code': self.code}
        if self.detail:
            body['detail'] = self.detail
        body.update(self.extra)
        return body


def _service_failure(exc):
    """Map an upload-service failure to a ToolError with the REST code."""
    if isinstance(exc, StorageError):
        return ToolError(
            exc.code,
            'Storage is temporarily unavailable, so the file was not stored. Try again in a minute.',
        )
    code = getattr(exc, 'code', '') or str(exc)
    return ToolError(
        code,
        ERROR_MESSAGES.get(code, 'The upload failed. Try again, and check the file and its parameters if it fails again.'),
        detail=getattr(exc, 'detail', ''),
    )


def _check_upload_throttle(request, key):
    """Charge one upload to the credential's REST upload budget (100/hour).

    Runs drop_core's own APIKeyUploadThrottle, so REST and MCP uploads
    draw from one cache bucket per credential.
    """
    throttle = APIKeyUploadThrottle()
    request.auth = key  # the throttle keys its bucket on request.auth
    if throttle.allow_request(request, None):
        return
    wait = throttle.wait()
    retry_after = max(1, math.ceil(wait if wait is not None else throttle.duration))
    raise ToolError(
        'rate_limited',
        f'Too many uploads in the past hour. The limit is {throttle.rate} for each '
        f'API key or connected app, and the REST API shares it. Wait {retry_after} '
        'seconds, then try again.',
        retry_after=retry_after,
    )


_B64_WHITESPACE = b' \t\n\r\f\v'
_INVALID_BASE64 = (
    'content_base64 is not valid base64. Send standard base64 (A-Z, a-z, 0-9, + and /) '
    'with = padding; line breaks and a data:<mime>;base64, prefix are allowed.'
)


def _decode_content(content_b64):
    """Decode ``content_base64``, tolerating line wrapping (GNU ``base64``
    output) and a ``data:<mime>;base64,`` prefix. Any other character
    outside the standard alphabet is still rejected (ValueError)."""
    try:
        raw = content_b64.encode('ascii')
    except UnicodeEncodeError:
        raise ValueError(_INVALID_BASE64)
    raw = raw.translate(None, _B64_WHITESPACE)
    if raw[:5].lower() == b'data:':
        comma = raw.find(b',')  # one slice: no extra body-sized copy
        if comma == -1 or not raw[5:comma].lower().endswith(b';base64'):
            raise ValueError(_INVALID_BASE64)
        raw = raw[comma + 1:]
    if not raw:
        raise ValueError(_INVALID_BASE64)
    # Refuse oversized input before decoding it.
    if len(raw) > (MAX_DECODED_BYTES + 2) // 3 * 4:
        raise ToolError('file_too_large', ERROR_MESSAGES['file_too_large'])
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(_INVALID_BASE64)
    if len(data) > MAX_DECODED_BYTES:
        raise ToolError('file_too_large', ERROR_MESSAGES['file_too_large'])
    return data


# --------------------------------------------------------------------------
# tool implementations (raise ValueError -> JSON-RPC -32602 invalid params;
# raise ToolError -> tool result with isError: true)
# --------------------------------------------------------------------------

def _tool_upload_artifact(key, args, request):
    filename = args.get('filename')
    content_b64 = args.get('content_base64')
    mime_type = args.get('mime_type')
    ttl_days = args.get('ttl_days')

    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('Send filename as a non-empty string.')
    if not isinstance(content_b64, str) or not content_b64:
        raise ValueError('Send content_base64 as a non-empty base64 string.')
    if mime_type is not None and not isinstance(mime_type, str):
        raise ValueError('Send mime_type as a string.')
    if ttl_days is not None and (
        isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days <= 0
    ):
        raise ValueError('Send ttl_days as a whole number of days, 1 or more.')
    on_secret = args.get('on_secret', 'flag')
    if on_secret not in ('flag', 'reject'):
        raise ValueError('Send on_secret as "flag" or "reject".')

    data = _decode_content(content_b64)

    filename = filename.strip()
    if not mime_type:
        # The tool contract promises guessing from the filename when omitted;
        # do it here so behavior is identical regardless of drop_core defaults.
        mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    try:
        artifact = upload_artifact(
            workspace=key.workspace,
            api_key=_service_api_key(key),
            filename=filename,
            data=data,
            mime_type=mime_type,
            ttl_days=ttl_days,
            # Same uploader metadata as REST, for safety-block containment.
            # X-Forwarded-For is client-controlled: clamp to the column
            # width (SafetyEvent.uploader_ip, 64) so an over-long value
            # cannot fail the event insert before the key is suspended.
            uploader_ip=(client_ip(request) or '')[:64],
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            on_secret=on_secret,
        )
    except (ValueError, StorageError) as exc:
        raise _service_failure(exc) from exc
    upload_number = Artifact.objects.filter(workspace=key.workspace).count()
    funnel.record_workspace_event(
        key.workspace, funnel.UPLOAD_COMPLETED, upload_number=upload_number
    )
    flags = list(artifact.secret_flags or [])
    result = {
        'id': str(artifact.id),
        'url': _artifact_url(artifact),
        'expires_at': artifact.expires_at.isoformat() if artifact.expires_at else None,
        'size_bytes': artifact.size_bytes,
        'secret_flags': flags,
        **({'ttl_adjusted': True} if getattr(artifact, 'ttl_adjusted', False) else {}),
    }
    # Advisory, never blocking: a flagged upload IS stored. Tell the
    # agent to involve the human before sharing the link.
    if flags:
        result['warning'] = SECRET_FLAGS_WARNING
        result['secret_flag_locations'] = getattr(
            artifact, 'secret_flag_locations', {}
        )
        # Additive only: locations are capped per flag.
        if getattr(artifact, 'secret_flag_locations_truncated', False):
            result['secret_flag_locations_truncated'] = True
    return result


def _tool_list_artifacts(key, args, request):
    limit = args.get('limit', 50)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError('Send limit as a whole number, such as 50.')
    limit = max(1, min(200, limit))
    artifacts = (
        Artifact.objects.filter(workspace=key.workspace)
        .order_by('-created_at')[:limit]
    )
    return {'artifacts': [_artifact_json(a) for a in artifacts]}


def _tool_create_upload_url(key, args, request):
    filename = args.get('filename')
    ttl_days = args.get('ttl_days')
    on_secret = args.get('on_secret', 'flag')

    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('Send filename as a non-empty string.')
    if ttl_days is not None and (
        isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days <= 0
    ):
        raise ValueError('Send ttl_days as a whole number of days, 1 or more.')
    if on_secret not in ('flag', 'reject'):
        raise ValueError('Send on_secret as "flag" or "reject".')

    # OAuth-minted URLs remember the client application, so the PUT stops
    # working once the user's grants for it are revoked (API-key URLs
    # follow the key row instead; an APIKey has no application_id).
    try:
        pending = mint_upload_url(
            workspace=key.workspace,
            api_key=_service_api_key(key),
            filename=filename.strip(),
            ttl_days=ttl_days,
            on_secret=on_secret,
            oauth_application_id=getattr(key, 'application_id', None),
        )
    except ValueError as exc:
        # RejectedUpload: a tool error carrying its code and detail.
        raise _service_failure(exc) from exc

    base = settings.DROP_BASE_URL.rstrip('/')
    result = {
        'upload_url': f'{base}/api/v1/uploads/{pending.token}',
        'expires_at': pending.expires_at.isoformat(),
        'max_size_bytes': MAX_DECODED_BYTES,
        'ttl_days': pending.ttl_days,
        'filename': pending.filename,
    }
    if pending.ttl_adjusted:
        result['ttl_adjusted'] = True
    return result


def _tool_revoke_artifact(key, args, request):
    artifact_id = args.get('id')
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ValueError('Send id as the id of the file to revoke, a non-empty string.')
    artifact_id = artifact_id.strip()
    try:
        uuid_module.UUID(artifact_id)
    except ValueError:
        # Not a uuid at all: treat as unknown rather than letting the
        # service raise on the malformed lookup.
        return {'revoked': False}
    revoked = revoke_artifact(workspace=key.workspace, artifact_id=artifact_id)
    result = {'revoked': bool(revoked)}
    if revoked == REVOKE_PENDING_DELETE:
        # Additive only: the URL is already dead; storage was unreachable,
        # so the file is deleted on the sweeper's retry.
        result['storage_deleted'] = False
    return result


_TOOL_HANDLERS = {
    'upload_artifact': _tool_upload_artifact,
    'create_upload_url': _tool_create_upload_url,
    'list_artifacts': _tool_list_artifacts,
    'revoke_artifact': _tool_revoke_artifact,
}


# Tools that store bytes or mint upload URLs. Each call is charged to the
# same per-credential budget as a REST upload.
_UPLOAD_TOOLS = frozenset({'upload_artifact', 'create_upload_url'})


def _handle_tools_call(request, request_id, key, params):
    name = params.get('name')
    arguments = params.get('arguments') or {}
    if not isinstance(name, str):
        return _rpc_error(request_id, ERR_INVALID_PARAMS, 'Send params.name as a string.')
    if not isinstance(arguments, dict):
        return _rpc_error(request_id, ERR_INVALID_PARAMS, 'Send params.arguments as an object.')
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _rpc_error(request_id, ERR_INVALID_PARAMS, f'No tool named {name} exists. The tools are upload_artifact, create_upload_url, list_artifacts and revoke_artifact.')
    try:
        if name in _UPLOAD_TOOLS:
            _check_upload_throttle(request, key)
        result = handler(key, arguments, request)
    except ToolError as exc:
        return _rpc_result(request_id, {
            'content': [{'type': 'text', 'text': json.dumps(exc.body())}],
            'isError': True,
        })
    except ValueError as exc:
        # Malformed arguments: a protocol error, per the MCP spec.
        message = getattr(exc, "detail", "") or str(exc) or "The arguments are not valid for this tool; check them against its inputSchema."
        return _rpc_error(request_id, ERR_INVALID_PARAMS, message)
    return _rpc_result(request_id, {'content': [{'type': 'text', 'text': json.dumps(result)}]})


# --------------------------------------------------------------------------
# endpoint
# --------------------------------------------------------------------------

@csrf_exempt
def mcp_endpoint(request):
    if request.method != 'POST':
        return JsonResponse(
            {
                'error': 'method_not_allowed',
                'hint': 'Send a POST request with a JSON-RPC 2.0 body and the header Content-Type: application/json.',
            },
            status=405,
        )
    try:
        return _handle_post(request)
    except Exception:
        # Last resort: never Django's HTML error page on the MCP endpoint.
        logger.exception('mcp: unhandled error')
        return _rpc_error(None, ERR_INTERNAL, 'The server could not complete the request. Try again later.', http_status=500)


def _handle_post(request):
    if not _origin_allowed(request):
        return _rpc_error(
            None, ERR_INVALID_REQUEST,
            'This Origin is not allowed. Send requests from a Veilfile origin, or without an Origin header.', http_status=403,
        )

    # Authenticate from the header before reading the body, so anonymous
    # callers never make the server buffer or parse a large request.
    key = _authenticate(request)
    if key is None:
        return _unauthenticated(_peek_request_id(request))

    try:
        body = _read_body(request, MAX_BODY_BYTES)
    except _BodyTooLarge:
        _discard_body(request, MAX_DRAIN_BYTES)
        return _rpc_error(
            None, ERR_INVALID_REQUEST,
            f'The request is over the body limit of {MAX_BODY_BYTES} bytes, which fits '
            'a 25 MiB file as base64 plus the JSON envelope. Call create_upload_url '
            'and PUT the raw bytes to the upload URL it returns. Files over 25 MiB '
            'are not accepted.',
            http_status=413,
            data={'code': 'file_too_large', 'max_body_bytes': MAX_BODY_BYTES},
        )
    except UnreadablePostError:
        return _rpc_error(None, ERR_PARSE, 'The request body could not be read. Send the request again.', http_status=400)
    if len(body) > PLAIN_BODY_BYTES and not _large_body_is_plain(body):
        return _rpc_error(
            None, ERR_INVALID_REQUEST,
            f'The request is too complex. A body over {PLAIN_BODY_BYTES} bytes is accepted '
            f'only as a plain tools/call with at most {MAX_LARGE_BODY_STRUCTURE} JSON '
            'structural characters and no characters above U+00FF, such as a '
            'non-Latin file name. Use an ASCII file name, or call create_upload_url '
            'and PUT the raw bytes.',
            http_status=413,
            data={'code': 'request_too_complex', 'max_plain_body_bytes': PLAIN_BODY_BYTES},
        )
    # Decode, then drop the raw bytes before parsing: one less body-sized
    # copy at the parse peak.
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        text = None
    del body
    payload = _PARSE_FAILED if text is None else _parse_json(text)
    del text
    if payload is _PARSE_FAILED:
        return _rpc_error(None, ERR_PARSE, 'The request body is not valid JSON. Send UTF-8 encoded JSON.', http_status=400)
    if isinstance(payload, list) or not isinstance(payload, dict):
        return _rpc_error(None, ERR_INVALID_REQUEST, 'The request body must be a single JSON-RPC object; this server does not accept batches.', http_status=400)

    request_id = payload.get('id')
    if not _valid_id(request_id):
        return _rpc_error(None, ERR_INVALID_REQUEST, 'Send id as a string or a number.', http_status=400)
    method = payload.get('method')
    if method is None and ('result' in payload or 'error' in payload):
        # A client's response to a server request. This server sends none;
        # accept and ignore it, as Streamable HTTP requires.
        return HttpResponse(status=202)
    if payload.get('jsonrpc') != '2.0' or not isinstance(method, str):
        return _rpc_error(
            request_id, ERR_INVALID_REQUEST,
            'Send "jsonrpc": "2.0" and a string "method".',
            http_status=400,
        )
    if method != 'initialize':
        # Sent by clients after the handshake; initialize negotiates in-band.
        version = request.headers.get('MCP-Protocol-Version')
        if version and version.strip() not in SUPPORTED_PROTOCOL_VERSIONS:
            return _rpc_error(
                request_id, ERR_INVALID_REQUEST,
                f'This MCP-Protocol-Version is not supported; send one of '
                f'{", ".join(SUPPORTED_PROTOCOL_VERSIONS)}.',
                http_status=400,
            )
    if 'id' not in payload:
        # A notification: never answered (JSON-RPC); Streamable HTTP wants
        # 202 with no body, whatever the notification is. A message with an
        # id is a request and gets a response, even for a notifications/*
        # method (method not found), as before.
        return HttpResponse(status=202)

    try:
        return _dispatch(request, request_id, key, method, payload.get('params'))
    except Exception:
        logger.exception('mcp: unhandled error in %r', method)
        return _rpc_error(request_id, ERR_INTERNAL, 'The server could not complete the request. Try again later.', http_status=500)


def _dispatch(request, request_id, key, method, params):
    params = params or {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, ERR_INVALID_PARAMS, 'Send params as an object.')

    if method == 'initialize':
        # Negotiate honestly: answer with the client's requested version when
        # we speak it, otherwise fall back to our latest. Never claim a
        # version whose wire protocol we don't implement.
        requested = params.get('protocolVersion')
        negotiated = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        )
        return _rpc_result(request_id, {
            'protocolVersion': negotiated,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
        })
    if method == 'notifications/initialized':
        # Unchanged since the first release, which accepted it with or
        # without an id; any other id-bearing notifications/* is not found.
        return HttpResponse(status=202)
    if method == 'ping':
        return _rpc_result(request_id, {})
    if method == 'tools/list':
        return _rpc_result(request_id, {'tools': TOOLS})
    if method == 'tools/call':
        return _handle_tools_call(request, request_id, key, params)
    return _rpc_error(request_id, ERR_METHOD_NOT_FOUND, f'The method {method} is not supported; use initialize, ping, tools/list or tools/call.')
