"""Regression tests for the MCP endpoint fixes (review cluster: mcp).

Covers: request bodies above Django's 2.5 MiB default (mcp-2, ctr-1),
the shared per-credential upload throttle (mcp-4, ctr-5), safety
containment metadata (mcp-6), JSON-RPC instead of HTML failures (mcp-7,
ctr-7), tool failures as ``isError`` results (ctr-15), the upload tool
description (mcp-9), notifications / ping / protocol-version / Origin
handling (mcp-10), tolerant base64 (mcp-11), ``last_used_at`` (mcp-12) and
the base URL / leftover settings module (mcp-13).
"""
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from drop_core.models import (
    APIKey,
    Artifact,
    BlockedHash,
    PendingUpload,
    SafetyEvent,
    UsageCounter,
    Workspace,
)
from drop_core.plans import PLANS
from drop_core.views import APIKeyUploadThrottle
from drop_mcp import views as mcp_views
from drop_mcp.views import mcp_endpoint

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _png(size):
    return PNG_MAGIC + b'\x00' * (size - len(PNG_MAGIC))


def _b64(data):
    return base64.b64encode(data).decode('ascii')


class McpFixTestBase(TestCase):
    def setUp(self):
        # Throttle history lives in the cache and APIKey pks repeat across
        # tests, so start every test with an empty bucket.
        cache.clear()
        self.tmpdir = tempfile.mkdtemp(prefix='veilfile-mcp-test-')
        self._old_local_dir = os.environ.get('DROP_LOCAL_DIR')
        os.environ['DROP_LOCAL_DIR'] = self.tmpdir
        User = get_user_model()
        self.user = User.objects.create_user(username='mcp-fix-owner', password='x')
        self.workspace = Workspace.objects.create(
            name='ws-fix', plan='scale', owner=self.user
        )
        self.key, self.raw_key = APIKey.create_key(self.workspace, 'agent key')
        self.client = Client(raise_request_exception=False)
        self.factory = RequestFactory()

    def tearDown(self):
        cache.clear()
        if self._old_local_dir is None:
            os.environ.pop('DROP_LOCAL_DIR', None)
        else:
            os.environ['DROP_LOCAL_DIR'] = self._old_local_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- request helpers ----------------------------------------------------
    def rpc_payload(self, method, params=None, rpc_id=1):
        payload = {'jsonrpc': '2.0', 'id': rpc_id, 'method': method}
        if params is not None:
            payload['params'] = params
        return payload

    def post_raw(self, body, raw_key='default', **extra):
        if raw_key == 'default':
            raw_key = self.raw_key
        if raw_key is not None:
            extra.setdefault('HTTP_AUTHORIZATION', f'Bearer {raw_key}')
        if isinstance(body, str):
            body = body.encode('utf-8')
        return self.client.post(
            '/mcp', data=body, content_type='application/json', **extra
        )

    def post(self, payload, raw_key='default', **extra):
        return self.post_raw(json.dumps(payload), raw_key=raw_key, **extra)

    def call(self, name, arguments, **extra):
        return self.post(
            self.rpc_payload('tools/call', {'name': name, 'arguments': arguments}),
            **extra,
        )

    def assertJsonRpc(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:300])
        self.assertTrue(
            response['Content-Type'].startswith('application/json'),
            response['Content-Type'],
        )
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['jsonrpc'], '2.0')
        return data

    def tool_result(self, response):
        """Successful tool result: the parsed JSON text, no isError."""
        data = self.assertJsonRpc(response)
        self.assertNotIn('error', data, data)
        self.assertNotIn('isError', data['result'])
        content = data['result']['content']
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]['type'], 'text')
        return json.loads(content[0]['text'])

    def tool_error(self, response):
        """Tool execution failure: isError result with a JSON text body."""
        data = self.assertJsonRpc(response)
        self.assertNotIn('error', data, data)
        self.assertIs(data['result']['isError'], True)
        content = data['result']['content']
        self.assertEqual(content[0]['type'], 'text')
        body = json.loads(content[0]['text'])
        self.assertIn('error', body)
        self.assertIn('code', body)
        return body


class McpRequestSizeTests(McpFixTestBase):
    """mcp-2 / ctr-1 (MCP side): bodies above 2.5 MiB, JSON-RPC on overflow."""

    def test_three_mib_upload_succeeds_through_full_stack(self):
        data = _png(3 * 1024 * 1024)
        response = self.call(
            'upload_artifact', {'filename': 'shot.png', 'content_base64': _b64(data)}
        )
        result = self.tool_result(response)
        self.assertEqual(result['size_bytes'], len(data))
        self.assertTrue(Artifact.objects.filter(id=result['id']).exists())

    def test_twenty_five_mib_upload_succeeds(self):
        data = _png(mcp_views.MAX_DECODED_BYTES)
        response = self.call(
            'upload_artifact', {'filename': 'big.png', 'content_base64': _b64(data)}
        )
        result = self.tool_result(response)
        self.assertEqual(result['size_bytes'], mcp_views.MAX_DECODED_BYTES)

    def test_decoded_over_limit_is_file_too_large_tool_error(self):
        data = _png(mcp_views.MAX_DECODED_BYTES + 1)
        response = self.call(
            'upload_artifact', {'filename': 'big.png', 'content_base64': _b64(data)}
        )
        body = self.tool_error(response)
        self.assertEqual(body['code'], 'file_too_large')
        self.assertFalse(Artifact.objects.exists())

    def test_declared_body_over_cap_is_jsonrpc_413_without_reading(self):
        # Declared past the drain ceiling too: refused without a byte read.
        request = self.factory.post(
            '/mcp', data=b'{}', content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.raw_key}',
            CONTENT_LENGTH=str(mcp_views.MAX_DRAIN_BYTES + 1),
        )
        response = mcp_endpoint(request)
        data = self.assertJsonRpc(response, status=413)
        self.assertEqual(data['error']['code'], -32600)
        self.assertIsNone(data['id'])
        self.assertEqual(data['error']['data']['code'], 'file_too_large')
        self.assertIn('create_upload_url', data['error']['message'])
        self.assertEqual(request.read(2), b'{}')  # still unread

    def test_body_over_cap_is_drained_so_the_413_reaches_the_client(self):
        # gunicorn closes the socket after the response; an unread body
        # turns the 413 into a broken pipe on the client's side.
        body = json.dumps(self.rpc_payload('tools/call', {
            'name': 'upload_artifact',
            'arguments': {'filename': 'a.txt', 'content_base64': 'QUJD' * 4096},
        })).encode()
        request = self.factory.post(
            '/mcp', data=body, content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.raw_key}',
        )
        with mock.patch.object(mcp_views, 'MAX_BODY_BYTES', 4096), \
                mock.patch.object(mcp_views, '_parse_json') as parse:
            response = mcp_endpoint(request)
        data = self.assertJsonRpc(response, status=413)
        self.assertEqual(data['error']['data']['code'], 'file_too_large')
        parse.assert_not_called()
        self.assertEqual(request.read(), b'')  # nothing left unread

    def test_body_over_cap_through_full_stack_is_jsonrpc_413(self):
        with mock.patch.object(mcp_views, 'MAX_BODY_BYTES', 1024):
            response = self.call(
                'upload_artifact',
                {'filename': 'a.txt', 'content_base64': _b64(b'x' * 4096)},
            )
        data = self.assertJsonRpc(response, status=413)
        self.assertEqual(data['error']['data']['code'], 'file_too_large')

    def test_body_cap_admits_a_max_size_file_as_wrapped_base64(self):
        # The cap must leave room for a 25 MiB file sent as 76-column
        # CRLF-wrapped base64 inside a JSON envelope.
        encoded = base64.encodebytes(b'\x00' * mcp_views.MAX_DECODED_BYTES)
        wrapped = encoded.decode('ascii').replace('\n', '\r\n')
        envelope = json.dumps(self.rpc_payload('tools/call', {
            'name': 'upload_artifact',
            'arguments': {'filename': 'x' * 255 + '.png', 'content_base64': wrapped},
        }))
        self.assertLess(len(envelope.encode('utf-8')), mcp_views.MAX_BODY_BYTES)


class McpUploadThrottleTests(McpFixTestBase):
    """mcp-4 / ctr-5: MCP uploads share the REST per-credential budget."""

    def rest_upload(self):
        upload = SimpleUploadedFile('note.txt', b'hello', content_type='text/plain')
        return self.client.post(
            '/api/v1/artifacts/', {'file': upload},
            HTTP_AUTHORIZATION=f'Bearer {self.raw_key}',
        )

    def mcp_upload(self):
        return self.call(
            'upload_artifact', {'filename': 'note.txt', 'content_base64': _b64(b'hello')}
        )

    def test_rest_and_mcp_share_one_bucket(self):
        with mock.patch.object(APIKeyUploadThrottle, 'rate', '3/hour'):
            self.assertEqual(self.rest_upload().status_code, 201)
            self.assertEqual(self.rest_upload().status_code, 201)
            self.tool_result(self.mcp_upload())
            body = self.tool_error(self.mcp_upload())
            self.assertEqual(body['code'], 'rate_limited')
            self.assertGreater(body['retry_after'], 0)
            # ...and REST is now throttled too: the budget is shared.
            self.assertEqual(self.rest_upload().status_code, 429)
        self.assertEqual(Artifact.objects.count(), 3)

    def test_mcp_uploads_alone_are_throttled(self):
        with mock.patch.object(APIKeyUploadThrottle, 'rate', '2/hour'):
            self.tool_result(self.mcp_upload())
            self.tool_result(self.mcp_upload())
            body = self.tool_error(self.mcp_upload())
        self.assertEqual(body['code'], 'rate_limited')
        self.assertEqual(Artifact.objects.count(), 2)

    def test_oauth_credential_shares_the_bucket_with_rest(self):
        from oauth2_provider.models import get_access_token_model, get_application_model

        from drop_core.oauth import OAUTH_SCOPE_MCP_USE

        from drop_core.models import EmailVerification

        # OAuth tokens only authorize for users with a verified email.
        EmailVerification.objects.create(
            user=self.user,
            email=self.user.email,
            is_verified=True,
            verified_at=timezone.now(),
        )
        Application = get_application_model()
        app = Application.objects.create(
            name='mcp client',
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris='http://127.0.0.1:33418/callback',
            user=self.user,
        )
        token = get_access_token_model().objects.create(
            user=self.user,
            application=app,
            token='oauth-test-token-0123456789',
            expires=timezone.now() + timedelta(hours=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )
        with mock.patch.object(APIKeyUploadThrottle, 'rate', '2/hour'):
            for _ in range(2):
                self.tool_result(self.call(
                    'upload_artifact',
                    {'filename': 'n.txt', 'content_base64': _b64(b'hi')},
                    HTTP_AUTHORIZATION=f'Bearer {token.token}',
                ))
            body = self.tool_error(self.call(
                'upload_artifact',
                {'filename': 'n.txt', 'content_base64': _b64(b'hi')},
                HTTP_AUTHORIZATION=f'Bearer {token.token}',
            ))
            self.assertEqual(body['code'], 'rate_limited')
            upload = SimpleUploadedFile('note.txt', b'hello', content_type='text/plain')
            rest = self.client.post(
                '/api/v1/artifacts/', {'file': upload},
                HTTP_AUTHORIZATION=f'Bearer {token.token}',
            )
        self.assertEqual(rest.status_code, 429)
        # The API key's own bucket is untouched.
        with mock.patch.object(APIKeyUploadThrottle, 'rate', '2/hour'):
            self.tool_result(self.mcp_upload())

    def test_create_upload_url_counts_against_the_bucket(self):
        with mock.patch.object(APIKeyUploadThrottle, 'rate', '2/hour'):
            self.tool_result(self.call('create_upload_url', {'filename': 'a.png'}))
            self.tool_result(self.call('create_upload_url', {'filename': 'b.png'}))
            body = self.tool_error(self.call('create_upload_url', {'filename': 'c.png'}))
            self.assertEqual(body['code'], 'rate_limited')
            # Listing is never throttled.
            listed = self.tool_result(self.call('list_artifacts', {}))
        self.assertEqual(listed['artifacts'], [])
        self.assertEqual(PendingUpload.objects.count(), 2)


class McpSafetyContainmentTests(McpFixTestBase):
    """mcp-6: the MCP upload path records uploader IP / UA and suspends the key."""

    def test_enforce_block_records_ip_and_user_agent(self):
        content = b'known bad bytes'
        BlockedHash.objects.create(
            sha256=hashlib.sha256(content).hexdigest(), reason='test'
        )
        with mock.patch.dict(os.environ, {'VEILFILE_SAFETY_MODE': 'enforce'}):
            response = self.call(
                'upload_artifact',
                {'filename': 'bad.txt', 'content_base64': _b64(content)},
                REMOTE_ADDR='203.0.113.9',
                HTTP_USER_AGENT='evil-agent/1.0',
            )
        body = self.tool_error(response)
        self.assertEqual(body['code'], 'content_not_allowed')
        event = SafetyEvent.objects.get()
        self.assertEqual(event.uploader_ip, '203.0.113.9')
        self.assertEqual(event.user_agent, 'evil-agent/1.0')
        self.assertEqual(event.api_key_id, self.key.pk)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertFalse(Artifact.objects.exists())


class McpErrorHandlingTests(McpFixTestBase):
    """mcp-7 / ctr-7 / ctr-15: JSON-RPC everywhere, tool failures as isError."""

    def test_storage_outage_is_retryable_tool_error(self):
        failing = mock.Mock()
        failing.save.side_effect = OSError('r2 down')
        with mock.patch('drop_core.services.get_storage', return_value=failing), \
                self.assertLogs('drop_core.services', level='ERROR'):
            response = self.call(
                'upload_artifact',
                {'filename': 'a.txt', 'content_base64': _b64(b'hello')},
            )
        body = self.tool_error(response)
        self.assertEqual(body['code'], 'storage_unavailable')
        self.assertIn('retry', body['error'].lower())
        self.assertFalse(Artifact.objects.exists())

    def test_unhashable_tool_name_is_invalid_params(self):
        response = self.post(
            self.rpc_payload('tools/call', {'name': ['x'], 'arguments': {}})
        )
        data = self.assertJsonRpc(response)
        self.assertEqual(data['error']['code'], -32602)

    def test_unexpected_exception_is_jsonrpc_internal_error(self):
        def boom(*args, **kwargs):
            raise RuntimeError('unexpected')

        with mock.patch.dict(mcp_views._TOOL_HANDLERS, {'list_artifacts': boom}), \
                self.assertLogs('drop_mcp', level='ERROR'), \
                self.assertLogs('django.request', level='ERROR'):
            response = self.call('list_artifacts', {})
        data = self.assertJsonRpc(response, status=500)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['error']['code'], -32603)
        self.assertNotIn('unexpected', data['error']['message'])

    def test_unauthenticated_deeply_nested_json_is_401_not_500(self):
        for body in ('[' * 200000, '[' * 50000, '{"a":' * 50000):
            request = self.factory.post(
                '/mcp', data=body.encode(), content_type='application/json'
            )
            response = mcp_endpoint(request)
            data = self.assertJsonRpc(response, status=401)
            self.assertEqual(data['error']['code'], -32001)

    def test_unauthenticated_small_request_echoes_id(self):
        response = self.post(self.rpc_payload('tools/list', rpc_id=42), raw_key=None)
        data = self.assertJsonRpc(response, status=401)
        self.assertEqual(data['id'], 42)
        self.assertIn('WWW-Authenticate', response)

    def test_authenticated_deeply_nested_json_is_parse_error(self):
        response = self.post_raw('[' * 50000)
        data = self.assertJsonRpc(response, status=400)
        self.assertEqual(data['error']['code'], -32700)

    def test_monthly_cap_is_tool_error_with_stable_code(self):
        UsageCounter.objects.create(
            workspace=self.workspace,
            year_month=timezone.now().strftime('%Y-%m'),
            uploads=PLANS['scale']['uploads'],
        )
        body = self.tool_error(self.call(
            'upload_artifact', {'filename': 'a.txt', 'content_base64': _b64(b'hi')}
        ))
        self.assertEqual(body['code'], 'monthly_cap_reached')
        self.assertIn('Monthly upload cap', body['error'])

    def test_storage_cap_is_tool_error(self):
        with mock.patch('drop_core.services.workspace_storage_bytes',
                        return_value=PLANS['scale']['storage_gb'] * 1024**3):
            body = self.tool_error(self.call(
                'upload_artifact', {'filename': 'a.txt', 'content_base64': _b64(b'hi')}
            ))
        self.assertEqual(body['code'], 'storage_cap_reached')

    def test_disallowed_type_is_tool_error_with_detail(self):
        body = self.tool_error(self.call(
            'upload_artifact', {'filename': 'evil.exe', 'content_base64': _b64(b'MZ')}
        ))
        self.assertEqual(body['code'], 'file_type_not_allowed')
        self.assertIn('.exe', body['detail'])
        self.assertIn('png', body['detail'])

    def test_create_upload_url_disallowed_type_is_tool_error(self):
        body = self.tool_error(self.call('create_upload_url', {'filename': 'evil.exe'}))
        self.assertEqual(body['code'], 'file_type_not_allowed')
        self.assertIn('.exe', body['detail'])

    def test_secret_reject_is_tool_error(self):
        secret = b'token "ghp_' + b'a' * 36 + b'"'
        body = self.tool_error(self.call('upload_artifact', {
            'filename': 'leak.txt', 'content_base64': _b64(secret), 'on_secret': 'reject',
        }))
        self.assertEqual(body['code'], 'secret_detected')
        self.assertIn('github_pat', body['detail'])
        self.assertFalse(Artifact.objects.exists())

    def test_invalid_arguments_stay_protocol_errors(self):
        data = self.assertJsonRpc(self.call('upload_artifact', {'filename': 'a.txt'}))
        self.assertEqual(data['error']['code'], -32602)
        data = self.assertJsonRpc(self.call('no_such_tool', {}))
        self.assertEqual(data['error']['code'], -32602)


class McpToolDescriptionTests(McpFixTestBase):
    """mcp-9: the upload tool says what the secret scan does and what each on_secret value returns."""

    def test_upload_description_states_what_the_secret_scan_does(self):
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        tools = {t['name']: t for t in data['result']['tools']}
        desc = tools['upload_artifact']['description']
        self.assertNotIn('instead of pasting secrets', desc)
        self.assertIn('on_secret', desc)
        self.assertIn('reject', desc)
        self.assertIn('secret_flags', desc)
        self.assertNotIn('ask the user', desc.lower())
        on_secret = tools['upload_artifact']['inputSchema']['properties']['on_secret']
        self.assertIn('secret_detected', on_secret['description'])
        self.assertNotIn('recommended', on_secret['description'].lower())

    def test_every_tool_has_the_same_title_twice_and_the_approved_hints(self):
        expected = {
            'upload_artifact': ('Upload a file', False, False, False, False),
            'create_upload_url': ('Create an upload URL', False, False, False, False),
            'list_artifacts': ('List files', True, False, True, False),
            'revoke_artifact': ('Revoke a file', False, True, True, False),
        }
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        tools = {t['name']: t for t in data['result']['tools']}
        self.assertEqual(set(tools), set(expected))
        for name, (title, read_only, destructive, idempotent, open_world) in expected.items():
            self.assertEqual(tools[name]['title'], title)
            self.assertEqual(tools[name]['annotations'], {
                'title': title,
                'readOnlyHint': read_only,
                'destructiveHint': destructive,
                'idempotentHint': idempotent,
                'openWorldHint': open_world,
            })

    def test_ttl_days_descriptions_name_the_plan_maximums_from_plans(self):
        from drop_core.plans import PLANS
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        tools = {t['name']: t for t in data['result']['tools']}
        maximums = 'which is %d days on Free, %d on Team and %d on Scale.' % (
            PLANS['free']['ttl_days'], PLANS['team']['ttl_days'], PLANS['scale']['ttl_days'])
        for name in ('upload_artifact', 'create_upload_url'):
            desc = tools[name]['inputSchema']['properties']['ttl_days']['description']
            self.assertIn(maximums, desc)

    def test_filename_descriptions_list_the_allowed_extensions(self):
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        tools = {t['name']: t for t in data['result']['tools']}
        for name in ('upload_artifact', 'create_upload_url'):
            desc = tools[name]['inputSchema']['properties']['filename']['description']
            self.assertIn('png, jpg, jpeg, webp, gif, txt, md, log, json, har and pdf', desc)


class McpProtocolTests(McpFixTestBase):
    """mcp-10: notifications, ping, request validation, header + Origin checks."""

    def test_any_notification_gets_202_and_no_body(self):
        for method in ('notifications/cancelled', 'notifications/progress',
                       'notifications/initialized', 'something/unknown'):
            response = self.post({'jsonrpc': '2.0', 'method': method,
                                  'params': {'requestId': 3}})
            self.assertEqual(response.status_code, 202, method)
            self.assertEqual(response.content, b'', method)

    def test_notification_with_bad_params_is_still_202(self):
        response = self.post({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
                              'params': 'nope'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')

    def test_client_response_message_is_accepted_with_202(self):
        response = self.post({'jsonrpc': '2.0', 'id': 7, 'result': {}})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')

    def test_ping_returns_empty_result(self):
        data = self.assertJsonRpc(self.post(self.rpc_payload('ping', rpc_id='p-1')))
        self.assertEqual(data['id'], 'p-1')
        self.assertEqual(data['result'], {})

    def test_missing_method_is_invalid_request(self):
        response = self.post({'jsonrpc': '2.0', 'id': 4})
        data = self.assertJsonRpc(response, status=400)
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['id'], 4)

    def test_wrong_jsonrpc_version_is_invalid_request(self):
        response = self.post({'jsonrpc': '1.0', 'id': 5, 'method': 'tools/list'})
        data = self.assertJsonRpc(response, status=400)
        self.assertEqual(data['error']['code'], -32600)

    def test_invalid_id_type_is_invalid_request(self):
        for bad_id in ('{"x": 1}', 'true', 'NaN', '[1]'):
            response = self.post_raw(
                '{"jsonrpc": "2.0", "id": %s, "method": "tools/list"}' % bad_id
            )
            data = self.assertJsonRpc(response, status=400)
            self.assertEqual(data['error']['code'], -32600, bad_id)
            self.assertIsNone(data['id'])

    def test_unsupported_protocol_version_header_is_400(self):
        response = self.post(self.rpc_payload('tools/list'),
                             HTTP_MCP_PROTOCOL_VERSION='1999-01-01')
        data = self.assertJsonRpc(response, status=400)
        self.assertEqual(data['error']['code'], -32600)
        self.assertIn('MCP-Protocol-Version', data['error']['message'])

    def test_supported_protocol_version_headers_are_accepted(self):
        for version in mcp_views.SUPPORTED_PROTOCOL_VERSIONS:
            data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list'),
                                                HTTP_MCP_PROTOCOL_VERSION=version))
            self.assertIn('tools', data['result'])

    def test_initialize_is_exempt_from_protocol_version_header(self):
        data = self.assertJsonRpc(self.post(
            self.rpc_payload('initialize', {'protocolVersion': '2099-01-01'}),
            HTTP_MCP_PROTOCOL_VERSION='2099-01-01',
        ))
        self.assertEqual(data['result']['protocolVersion'], mcp_views.PROTOCOL_VERSION)

    def test_foreign_origin_is_rejected_with_403(self):
        response = self.post(self.rpc_payload('tools/list'),
                             HTTP_ORIGIN='https://evil.example')
        data = self.assertJsonRpc(response, status=403)
        self.assertIsNone(data['id'])
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_null_and_malformed_origins_are_rejected(self):
        for origin in ('null', 'http://[::1', 'evil.example'):
            response = self.post(self.rpc_payload('tools/list'), HTTP_ORIGIN=origin)
            self.assertJsonRpc(response, status=403)

    def test_own_origin_and_missing_origin_are_allowed(self):
        own = settings.DROP_BASE_URL.rstrip('/')
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list'), HTTP_ORIGIN=own))
        self.assertIn('tools', data['result'])
        data = self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        self.assertIn('tools', data['result'])

    @override_settings(CSRF_TRUSTED_ORIGINS=['https://www.veilfile.example'])
    def test_csrf_trusted_origin_is_allowed(self):
        data = self.assertJsonRpc(self.post(
            self.rpc_payload('tools/list'), HTTP_ORIGIN='https://www.veilfile.example'
        ))
        self.assertIn('tools', data['result'])


class McpBase64Tests(McpFixTestBase):
    """mcp-11: line-wrapped base64 and data: URIs are accepted."""

    def upload(self, content_base64, filename='notes.txt'):
        return self.call(
            'upload_artifact', {'filename': filename, 'content_base64': content_base64}
        )

    def stored_bytes(self, artifact_id):
        from drop_core.storage import get_storage

        artifact = Artifact.objects.get(id=artifact_id)
        with get_storage().open(artifact.token) as fh:
            return fh.read()

    def test_line_wrapped_base64_is_accepted(self):
        content = b'line of log output\n' * 200
        wrapped = base64.encodebytes(content).decode('ascii')
        self.assertIn('\n', wrapped)
        result = self.tool_result(self.upload(wrapped))
        self.assertEqual(self.stored_bytes(result['id']), content)

    def test_crlf_and_space_wrapped_base64_is_accepted(self):
        content = b'hello veilfile ' * 50
        encoded = _b64(content)
        wrapped = '\r\n'.join(encoded[i:i + 64] for i in range(0, len(encoded), 64))
        result = self.tool_result(self.upload('  ' + wrapped + '\n'))
        self.assertEqual(self.stored_bytes(result['id']), content)

    def test_data_uri_prefix_is_accepted(self):
        data = _png(256)
        result = self.tool_result(
            self.upload('data:image/png;base64,' + _b64(data), filename='s.png')
        )
        self.assertEqual(self.stored_bytes(result['id']), data)

    def test_data_uri_with_parameters_is_accepted(self):
        content = b'plain text body'
        result = self.tool_result(
            self.upload('DATA:text/plain;charset=utf-8;base64,' + _b64(content))
        )
        self.assertEqual(self.stored_bytes(result['id']), content)

    def test_invalid_characters_are_still_rejected(self):
        for bad in ('!!!not-base64!!!', 'aGVsbG8*', 'aGVs bG8_', 'aGVsbG8é',
                    'data:text/plain,hello', 'data:;base64,'):
            data = self.assertJsonRpc(self.upload(bad))
            self.assertEqual(data['error']['code'], -32602, bad)
            self.assertIn('base64', data['error']['message'], bad)
        self.assertFalse(Artifact.objects.exists())


class McpLastUsedTests(McpFixTestBase):
    """mcp-12: MCP use updates APIKey.last_used_at (at most once a minute)."""

    def test_mcp_call_sets_last_used_at(self):
        self.assertIsNone(self.key.last_used_at)
        self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_last_used_write_is_rate_limited(self):
        recent = timezone.now() - timedelta(seconds=20)
        APIKey.objects.filter(pk=self.key.pk).update(last_used_at=recent)
        self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        self.key.refresh_from_db()
        self.assertEqual(self.key.last_used_at, recent)

        stale = timezone.now() - timedelta(minutes=5)
        APIKey.objects.filter(pk=self.key.pk).update(last_used_at=stale)
        self.assertJsonRpc(self.post(self.rpc_payload('tools/list')))
        self.key.refresh_from_db()
        self.assertGreater(self.key.last_used_at, stale)


class McpBaseUrlTests(McpFixTestBase):
    """mcp-13: URLs come from settings.DROP_BASE_URL; no broken leftovers."""

    @override_settings(DROP_BASE_URL='https://files.example.invalid')
    def test_urls_use_settings_base_url(self):
        uploaded = self.tool_result(self.call(
            'upload_artifact', {'filename': 'a.txt', 'content_base64': _b64(b'hi')}
        ))
        self.assertTrue(uploaded['url'].startswith('https://files.example.invalid/a/'))
        listed = self.tool_result(self.call('list_artifacts', {}))
        self.assertTrue(
            listed['artifacts'][0]['url'].startswith('https://files.example.invalid/a/')
        )
        minted = self.tool_result(self.call('create_upload_url', {'filename': 'a.png'}))
        self.assertTrue(minted['upload_url'].startswith(
            'https://files.example.invalid/api/v1/uploads/'
        ))

    def test_broken_integration_settings_module_is_gone(self):
        self.assertIsNone(importlib.util.find_spec('drop_mcp.integration_settings'))


class McpLargeBodyShapeTests(McpFixTestBase):
    """Review round 2: a body over PLAIN_BODY_BYTES is parsed only when it is
    shaped like an upload, so json.loads cannot amplify it into an OOM."""

    def parse_spy(self):
        return mock.patch.object(mcp_views, '_parse_json', wraps=mcp_views._parse_json)

    def assertTooComplex(self, response):
        data = self.assertJsonRpc(response, status=413)
        self.assertIsNone(data['id'])
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['error']['data']['code'], 'request_too_complex')
        self.assertIn('create_upload_url', data['error']['message'])
        return data

    def test_many_small_values_are_refused_without_parsing(self):
        # ~3 MiB of {} items: about 25x that once json.loads builds objects.
        items = b'{},' * (mcp_views.PLAIN_BODY_BYTES // 3 + 1000)
        body = (b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"x":['
                + items.rstrip(b',') + b']}}')
        self.assertGreater(len(body), mcp_views.PLAIN_BODY_BYTES)
        with self.parse_spy() as spy:
            response = self.post_raw(body)
        self.assertTooComplex(response)
        spy.assert_not_called()

    def test_wide_characters_in_a_large_body_are_refused_without_parsing(self):
        # One character above U+00FF makes the decoded text and the string
        # holding it 2-4x wider; raw UTF-8 and \u escapes alike.
        padding = 'a' * 8192
        for wide in ('\U0001F600', '\\ud83d\\ude00', '\u4e2d', '\\u4e2d', '\u0100'):
            body = ('{"jsonrpc":"2.0","id":1,"method":"tools/list",'
                    '"params":{"x":"%s%s"}}' % (padding, wide))
            with mock.patch.object(mcp_views, 'PLAIN_BODY_BYTES', 4096), \
                    self.parse_spy() as spy:
                response = self.post_raw(body)
            self.assertTooComplex(response)
            spy.assert_not_called()

    def test_small_bodies_are_parsed_as_before(self):
        # Up to PLAIN_BODY_BYTES nothing changes: many values and non-Latin
        # filenames still work.
        data = self.assertJsonRpc(self.post({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list',
            'params': {'x': [{}] * 5000},
        }))
        self.assertIn('tools', data['result'])
        result = self.tool_result(self.call('upload_artifact', {
            'filename': '\u622a\u56fe\U0001F600.png',
            'content_base64': _b64(_png(64 * 1024)),
        }))
        self.assertEqual(result['size_bytes'], 64 * 1024)

    def test_large_upload_with_non_latin_filename_is_refused_with_guidance(self):
        response = self.call('upload_artifact', {
            'filename': '\u622a\u56fe.png',
            'content_base64': _b64(_png(3 * 1024 * 1024)),
        })
        self.assertTooComplex(response)
        self.assertFalse(Artifact.objects.exists())

    def test_max_size_wrapped_data_uri_upload_with_latin1_filename_passes(self):
        # The real worst case for a legitimate client: 25 MiB as CRLF-wrapped
        # base64 in a data: URI, a Latin-1 filename sent raw and escaped,
        # extra arguments and _meta.
        data = _png(mcp_views.MAX_DECODED_BYTES)
        wrapped = base64.encodebytes(data).decode('ascii').replace('\n', '\r\n')
        del data
        for ensure_ascii in (False, True):
            Artifact.objects.all().delete()
            body = json.dumps(self.rpc_payload('tools/call', {
                'name': 'upload_artifact',
                '_meta': {'progressToken': 'p-1'},
                'arguments': {
                    'filename': 'r\u00e9sum\u00e9, final: v2 [draft].png',
                    'content_base64': 'data:image/png;base64,' + wrapped,
                    'mime_type': 'image/png',
                    'ttl_days': 7,
                    'on_secret': 'reject',
                },
            }), ensure_ascii=ensure_ascii).encode('utf-8')
            self.assertGreater(len(body), mcp_views.PLAIN_BODY_BYTES)
            result = self.tool_result(self.post_raw(body))
            self.assertEqual(result['size_bytes'], mcp_views.MAX_DECODED_BYTES)

    def test_shape_check_boundaries(self):
        plain = mcp_views._large_body_is_plain
        limit = mcp_views.MAX_LARGE_BODY_STRUCTURE
        self.assertTrue(plain(b'"' + b',' * limit + b'"'))
        self.assertFalse(plain(b'"' + b',' * limit + b':"'))
        self.assertTrue(plain(b'{"a":"\\u00e9 \\u00ff \\n \\/"}'))
        self.assertTrue(plain('{"a":"\u00e9\u00ff"}'.encode('utf-8')))
        self.assertFalse(plain(b'{"a":"\\u0100"}'))
        self.assertFalse(plain(b'{"a":"\\uD83D\\uDE00"}'))
        self.assertFalse(plain('{"a":"\u0100"}'.encode('utf-8')))
        self.assertFalse(plain('{"a":"\u20ac"}'.encode('utf-8')))


class McpReviewRoundTwoTests(McpFixTestBase):
    """Review round 2: X-Forwarded-For clamp, unauthenticated drain,
    id-bearing notifications/* methods."""

    def test_overlong_forwarded_for_still_contains_the_key(self):
        # SafetyEvent.uploader_ip is varchar(64). On Postgres an unclamped
        # 100-char X-Forwarded-For failed the event insert before the key
        # was suspended; SQLite does not enforce the width, so assert it.
        content = b'known bad bytes'
        BlockedHash.objects.create(
            sha256=hashlib.sha256(content).hexdigest(), reason='test'
        )
        forwarded = '1.2.3.4' + 'x' * 93
        with mock.patch.dict(os.environ, {'VEILFILE_SAFETY_MODE': 'enforce'}):
            response = self.call(
                'upload_artifact',
                {'filename': 'bad.txt', 'content_base64': _b64(content)},
                HTTP_X_FORWARDED_FOR=forwarded,
            )
        self.assertEqual(self.tool_error(response)['code'], 'content_not_allowed')
        event = SafetyEvent.objects.get()
        max_length = SafetyEvent._meta.get_field('uploader_ip').max_length
        self.assertLessEqual(len(event.uploader_ip), max_length)
        # client_ip() ignores a value that is not an IP address and falls
        # back to the socket peer.
        self.assertEqual(event.uploader_ip, '127.0.0.1')
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)

    def test_unauthenticated_large_body_is_drained_before_401(self):
        body = json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list',
                           'params': {'pad': 'x' * (200 * 1024)}}).encode()
        request = self.factory.post('/mcp', data=body, content_type='application/json')
        response = mcp_endpoint(request)
        data = self.assertJsonRpc(response, status=401)
        self.assertIsNone(data['id'])  # too big to parse for the id
        self.assertIn('WWW-Authenticate', response)
        self.assertEqual(request.read(), b'')  # nothing left unread

    def test_unauthenticated_body_over_cap_is_not_read(self):
        payload = b'{"id": 1}'
        request = self.factory.post(
            '/mcp', data=payload, content_type='application/json',
            CONTENT_LENGTH=str(mcp_views.MAX_BODY_BYTES + 1),
        )
        response = mcp_endpoint(request)
        self.assertJsonRpc(response, status=401)
        # Still unread. (The fake stream only holds the short payload.)
        self.assertEqual(request.read(len(payload)), payload)

    def test_notification_method_with_an_id_is_answered(self):
        response = self.post({'jsonrpc': '2.0', 'id': 9,
                              'method': 'notifications/cancelled',
                              'params': {'requestId': 3}})
        data = self.assertJsonRpc(response)
        self.assertEqual(data['id'], 9)
        self.assertEqual(data['error']['code'], -32601)

    def test_initialized_with_an_id_keeps_its_original_202(self):
        response = self.post({'jsonrpc': '2.0', 'id': 2,
                              'method': 'notifications/initialized'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')
