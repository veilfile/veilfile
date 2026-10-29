"""Tests for the Veilfile MCP endpoint (drop_mcp.views.mcp_endpoint).

Uses Django's RequestFactory to call the view directly. Workspace + APIKey
fixtures are created through drop_core's models, and JSON-RPC payloads carry the raw
`vlf_` key in the Authorization header.
"""
import base64
import json
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from drop_core.models import APIKey, Artifact, Workspace
from drop_mcp.views import mcp_endpoint


def _rpc(method, params=None, rpc_id=1):
    payload = {'jsonrpc': '2.0', 'id': rpc_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return payload


def _issue_key(workspace, name):
    """Issue a raw vlf_ key via drop_core (stub: APIKey.issue, real: APIKey.create_key)."""
    issue = getattr(APIKey, 'issue', None) or getattr(APIKey, 'create_key')
    return issue(workspace, name)


def _make_workspace(owner, name, plan='free'):
    """Create a workspace; the real drop_core requires an owner, the stub does not."""
    try:
        return Workspace.objects.create(name=name, plan=plan, owner=owner)
    except TypeError:
        return Workspace.objects.create(name=name, plan=plan)


# Sentinel distinguishing "use the default key" from "send no key at all".
_USE_DEFAULT_KEY = object()


class McpEndpointTests(TestCase):
    def setUp(self):
        # MCP uploads share the REST upload throttle's cache bucket, and key
        # pks repeat across tests: start each test with no throttle history.
        cache.clear()
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(username='agent-owner', password='x')
        self.other_user = User.objects.create_user(username='other-owner', password='x')
        self.workspace = _make_workspace(self.user, 'ws-one')
        _, self.raw_key = _issue_key(self.workspace, 'agent key')
        # A second workspace to prove key-scoping.
        self.other_workspace = _make_workspace(self.other_user, 'ws-two')
        _, self.other_raw_key = _issue_key(self.other_workspace, 'other agent key')

    # -- request helper ---------------------------------------------------
    def post(self, payload, raw_key=_USE_DEFAULT_KEY, method='post'):
        if raw_key is _USE_DEFAULT_KEY:
            raw_key = self.raw_key
        headers = {}
        if raw_key is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {raw_key}'
        if method == 'get':
            request = self.factory.get('/mcp', **headers)
        else:
            body = json.dumps(payload).encode('utf-8') if payload is not None else b''
            request = getattr(self.factory, method)(
                '/mcp', data=body, content_type='application/json', **headers
            )
        response = mcp_endpoint(request)
        try:
            data = json.loads(response.content.decode('utf-8')) if response.content else None
        except ValueError:
            data = None
        return response, data

    # -- handshake ----------------------------------------------------------
    def test_initialize_handshake(self):
        response, data = self.post(_rpc('initialize', {}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['jsonrpc'], '2.0')
        self.assertEqual(data['id'], 1)
        result = data['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(result['serverInfo']['name'], 'veilfile-mcp')
        self.assertEqual(result['serverInfo']['version'], '0.1.0')

    def test_initialize_negotiates_requested_new_version(self):
        response, data = self.post(
            _rpc('initialize', {'protocolVersion': '2025-06-18'})
        )
        self.assertEqual(data['result']['protocolVersion'], '2025-06-18')

    def test_initialize_negotiates_requested_old_version(self):
        response, data = self.post(
            _rpc('initialize', {'protocolVersion': '2024-11-05'})
        )
        self.assertEqual(data['result']['protocolVersion'], '2024-11-05')

    def test_initialize_unknown_version_falls_back_to_latest(self):
        response, data = self.post(
            _rpc('initialize', {'protocolVersion': '1999-01-01'})
        )
        self.assertEqual(data['result']['protocolVersion'], '2025-06-18')

    def test_notifications_initialized_returns_202_empty(self):
        response, data = self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')

    def test_get_returns_405_with_hint(self):
        response, data = self.post(_rpc('tools/list'), method='get')
        self.assertEqual(response.status_code, 405)
        self.assertIn('hint', data)

    def test_unknown_method_returns_method_not_found(self):
        response, data = self.post(_rpc('nope/nothing'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['error']['code'], -32601)

    def test_invalid_json_body(self):
        request = self.factory.post(
            '/mcp', data=b'{not json', content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.raw_key}',
        )
        response = mcp_endpoint(request)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['error']['code'], -32700)

    # -- auth ---------------------------------------------------------------
    def test_missing_key_returns_401_unauthorized(self):
        response, data = self.post(_rpc('tools/list'), raw_key=None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data['error']['code'], -32001)

    def test_bad_key_returns_401_unauthorized(self):
        response, data = self.post(_rpc('tools/list'), raw_key='vlf_this-key-does-not-exist')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data['error']['code'], -32001)
        self.assertIn('unauthorized', data['error']['message'])

    def test_wrong_scheme_returns_401(self):
        request = self.factory.post(
            '/mcp', data=json.dumps(_rpc('tools/list')).encode(),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.raw_key}',
        )
        response = mcp_endpoint(request)
        self.assertEqual(response.status_code, 401)

    def test_revoked_key_returns_401(self):
        key_obj = APIKey.verify(self.raw_key)
        key_obj.is_active = False
        key_obj.save()
        response, data = self.post(_rpc('tools/list'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data['error']['code'], -32001)

    # -- tools/list -----------------------------------------------------------
    def test_tools_list_shape(self):
        response, data = self.post(_rpc('tools/list'))
        self.assertEqual(response.status_code, 200)
        tools = {t['name']: t for t in data['result']['tools']}
        self.assertEqual(set(tools), {'upload_artifact', 'create_upload_url', 'list_artifacts', 'revoke_artifact'})
        for tool in tools.values():
            self.assertIn('description', tool)
            self.assertEqual(tool['inputSchema']['type'], 'object')
        upload_schema = tools['upload_artifact']['inputSchema']
        self.assertEqual(set(upload_schema['required']), {'filename', 'content_base64'})
        self.assertIn('ttl_days', upload_schema['properties'])
        self.assertEqual(tools['revoke_artifact']['inputSchema']['required'], ['id'])
        self.assertEqual(tools['create_upload_url']['inputSchema']['required'], ['filename'])

    # -- tools/call -----------------------------------------------------------
    def call_tool(self, name, arguments, raw_key=_USE_DEFAULT_KEY):
        return self.post(_rpc('tools/call', {'name': name, 'arguments': arguments}), raw_key=raw_key)

    def call_result(self, name, arguments, raw_key=_USE_DEFAULT_KEY):
        response, data = self.call_tool(name, arguments, raw_key=raw_key)
        self.assertEqual(response.status_code, 200, data)
        self.assertNotIn('error', data, data)
        content = data['result']['content']
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]['type'], 'text')
        return json.loads(content[0]['text'])

    def tool_error(self, data):
        """A tool execution failure: an isError result, not a JSON-RPC error."""
        self.assertNotIn('error', data, data)
        self.assertIs(data['result']['isError'], True)
        return json.loads(data['result']['content'][0]['text'])

    def test_upload_list_revoke_roundtrip(self):
        raw_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64  # tiny fake png
        payload_b64 = base64.b64encode(raw_bytes).decode('ascii')

        uploaded = self.call_result('upload_artifact', {
            'filename': 'screenshot.png',
            'content_base64': payload_b64,
            'mime_type': 'image/png',
            'ttl_days': 3,
        })
        self.assertEqual(uploaded['size_bytes'], len(raw_bytes))
        self.assertTrue(uploaded['url'].startswith(settings.DROP_BASE_URL + '/a/'))
        self.assertIsNotNone(uploaded['expires_at'])
        self.assertEqual(uploaded['secret_flags'], [])
        artifact_id = uploaded['id']

        stored = Artifact.objects.get(id=artifact_id)
        self.assertEqual(stored.workspace, self.workspace)
        self.assertEqual(stored.size_bytes, len(raw_bytes))

        listed = self.call_result('list_artifacts', {})
        self.assertEqual(len(listed['artifacts']), 1)
        item = listed['artifacts'][0]
        self.assertEqual(item['id'], artifact_id)
        self.assertEqual(item['filename'], 'screenshot.png')
        self.assertEqual(item['mime_type'], 'image/png')
        self.assertEqual(item['url'], uploaded['url'])
        self.assertIn('created_at', item)

        revoked = self.call_result('revoke_artifact', {'id': artifact_id})
        self.assertEqual(revoked, {'revoked': True})
        self.assertFalse(Artifact.objects.filter(id=artifact_id).exists())

        listed_again = self.call_result('list_artifacts', {})
        self.assertEqual(listed_again['artifacts'], [])

    def test_list_is_newest_first_and_scoped_to_workspace(self):
        for name in ('a.txt', 'b.txt', 'c.txt'):
            self.call_result('upload_artifact', {
                'filename': name,
                'content_base64': base64.b64encode(b'x').decode('ascii'),
            })
        listed = self.call_result('list_artifacts', {})
        self.assertEqual(
            [a['filename'] for a in listed['artifacts']],
            ['c.txt', 'b.txt', 'a.txt'],
        )
        # The other workspace's key must not see these artifacts.
        other_listed = self.call_result('list_artifacts', {}, raw_key=self.other_raw_key)
        self.assertEqual(other_listed['artifacts'], [])
        # ...nor revoke them.
        victim_id = listed['artifacts'][0]['id']
        other_revoke = self.call_result('revoke_artifact', {'id': victim_id}, raw_key=self.other_raw_key)
        self.assertEqual(other_revoke, {'revoked': False})
        self.assertTrue(Artifact.objects.filter(id=victim_id).exists())

    def test_list_limit_default_and_max(self):
        for i in range(5):
            self.call_result('upload_artifact', {
                'filename': f'f{i}.txt',
                'content_base64': base64.b64encode(b'x').decode('ascii'),
            })
        limited = self.call_result('list_artifacts', {'limit': 2})
        self.assertEqual(len(limited['artifacts']), 2)
        clamped = self.call_result('list_artifacts', {'limit': 5000})
        self.assertLessEqual(len(clamped['artifacts']), 200)
        response, data = self.call_tool('list_artifacts', {'limit': 'many'})
        self.assertEqual(data['error']['code'], -32602)

    def test_upload_text_file_mime_guessed(self):
        uploaded = self.call_result('upload_artifact', {
            'filename': 'notes.txt',
            'content_base64': base64.b64encode(b'hello veilfile').decode('ascii'),
        })
        self.assertEqual(uploaded['size_bytes'], len(b'hello veilfile'))
        item = self.call_result('list_artifacts', {})['artifacts'][0]
        self.assertEqual(item['mime_type'], 'text/plain')

    def test_flagged_upload_includes_warning(self):
        content = b'my token = "ghp_' + b'a' * 36 + b'" // do not share'
        uploaded = self.call_result('upload_artifact', {
            'filename': 'leak.txt',
            'content_base64': base64.b64encode(content).decode('ascii'),
        })
        self.assertTrue(uploaded['secret_flags'])
        self.assertIn('warning', uploaded)
        self.assertIn('secret_flags', uploaded['warning'])

    def test_clean_upload_has_no_warning(self):
        uploaded = self.call_result('upload_artifact', {
            'filename': 'notes.txt',
            'content_base64': base64.b64encode(b'hello veilfile').decode('ascii'),
        })
        self.assertEqual(uploaded['secret_flags'], [])
        self.assertNotIn('warning', uploaded)

    def test_upload_tool_description_names_the_secret_fields(self):
        response, data = self.post(_rpc('tools/list'))
        tools = {t['name']: t for t in data['result']['tools']}
        desc = tools['upload_artifact']['description']
        self.assertIn('secret_flags', desc)
        self.assertIn('secret_flag_locations', desc)
        self.assertNotIn('ask the user', desc.lower())

    def test_upload_invalid_base64(self):
        response, data = self.call_tool('upload_artifact', {
            'filename': 'x.txt', 'content_base64': '!!!not-base64!!!',
        })
        self.assertEqual(data['error']['code'], -32602)

    def test_upload_missing_filename(self):
        response, data = self.call_tool('upload_artifact', {
            'content_base64': base64.b64encode(b'x').decode('ascii'),
        })
        self.assertEqual(data['error']['code'], -32602)

    def test_upload_on_secret_reject(self):
        response, data = self.call_tool('upload_artifact', {
            'filename': 'leak.txt',
            'content_base64': base64.b64encode(b'token "ghp_' + b'a' * 36 + b'"').decode('ascii'),
            'on_secret': 'reject',
        })
        # A tool error with the REST stable code plus the human detail.
        body = self.tool_error(data)
        self.assertEqual(body['code'], 'secret_detected')
        self.assertIn('github_pat', body['detail'])
        self.assertIn('nothing stored', body['detail'])

    def test_create_upload_url_roundtrip(self):
        result = self.call_result('create_upload_url', {'filename': 'shot.png'})
        self.assertIn('/api/v1/uploads/', result['upload_url'])
        self.assertEqual(result['ttl_days'], 7)  # free plan max
        self.assertNotIn('ttl_adjusted', result)
        self.assertEqual(result['max_size_bytes'], 25 * 1024 * 1024)

    def test_create_upload_url_clamps_ttl(self):
        result = self.call_result(
            'create_upload_url', {'filename': 'shot.png', 'ttl_days': 9999}
        )
        self.assertEqual(result['ttl_days'], 7)
        self.assertTrue(result['ttl_adjusted'])

    def test_create_upload_url_bad_extension(self):
        response, data = self.call_tool('create_upload_url', {'filename': 'evil.exe'})
        body = self.tool_error(data)
        self.assertEqual(body['code'], 'file_type_not_allowed')
        self.assertIn('.exe', body['detail'])

    def test_create_upload_url_requires_filename(self):
        response, data = self.call_tool('create_upload_url', {})
        self.assertEqual(data['error']['code'], -32602)

    def test_create_upload_url_requires_auth(self):
        response, data = self.call_tool(
            'create_upload_url', {'filename': 'shot.png'}, raw_key=None
        )
        self.assertEqual(data['error']['code'], -32001)

    def test_tools_list_includes_create_upload_url(self):
        response, data = self.post(_rpc('tools/list', {}))
        names = [t['name'] for t in data['result']['tools']]
        self.assertIn('create_upload_url', names)

    def test_upload_disallowed_file_type(self):
        response, data = self.call_tool('upload_artifact', {
            'filename': 'evil.exe',
            'content_base64': base64.b64encode(b'MZ').decode('ascii'),
        })
        body = self.tool_error(data)
        self.assertEqual(body['code'], 'file_type_not_allowed')
        # The detail names the extension and the allowlist.
        self.assertIn('.exe', body['detail'])
        self.assertIn('png', body['detail'])

    def test_upload_bad_ttl(self):
        response, data = self.call_tool('upload_artifact', {
            'filename': 'x.txt',
            'content_base64': base64.b64encode(b'x').decode('ascii'),
            'ttl_days': -2,
        })
        self.assertEqual(data['error']['code'], -32602)

    def test_upload_ttl_clamped_sets_ttl_adjusted(self):
        uploaded = self.call_result('upload_artifact', {
            'filename': 'notes.txt',
            'content_base64': base64.b64encode(b'hello veilfile').decode('ascii'),
            'ttl_days': 999,
        })
        self.assertTrue(uploaded.get('ttl_adjusted'))

    def test_upload_ttl_within_max_has_no_adjusted_flag(self):
        uploaded = self.call_result('upload_artifact', {
            'filename': 'notes.txt',
            'content_base64': base64.b64encode(b'hello veilfile').decode('ascii'),
            'ttl_days': 3,
        })
        self.assertNotIn('ttl_adjusted', uploaded)

    def test_unknown_tool(self):
        response, data = self.call_tool('delete_everything', {})
        self.assertEqual(data['error']['code'], -32602)
        self.assertIn('unknown tool', data['error']['message'])

    def test_revoke_unknown_id_returns_false(self):
        result = self.call_result('revoke_artifact', {'id': str(uuid.uuid4())})
        self.assertEqual(result, {'revoked': False})

    def test_revoke_malformed_id_returns_false_not_500(self):
        result = self.call_result('revoke_artifact', {'id': 'not-a-uuid'})
        self.assertEqual(result, {'revoked': False})

    def test_revoke_missing_id(self):
        response, data = self.call_tool('revoke_artifact', {})
        self.assertEqual(data['error']['code'], -32602)
