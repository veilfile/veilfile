'use strict';

const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const cli = require('../veilfile.js');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let tmpHome;
let realFetch;
let realEnv;
let realDeps;

function mockFetch(handler) {
  global.fetch = async (url, init) => handler(url, init || {});
}

function jsonResponse(status, body, headers = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers(headers),
    text: async () => JSON.stringify(body),
    json: async () => body,
    arrayBuffer: async () => new TextEncoder().encode(JSON.stringify(body)).buffer,
  };
}

function captureOutput() {
  const out = [];
  const err = [];
  const origLog = console.log;
  const origErr = process.stderr.write.bind(process.stderr);
  console.log = (...a) => out.push(a.join(' '));
  process.stderr.write = (s) => { err.push(String(s)); return true; };
  return {
    out, err,
    restore() {
      console.log = origLog;
      process.stderr.write = origErr;
    },
  };
}

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'veilfile-test-'));
  realEnv = { ...process.env };
  // Every platform's config location points into the temp dir, so a test
  // run never touches the developer's real credentials (Windows: APPDATA).
  process.env.XDG_CONFIG_HOME = tmpHome;
  process.env.APPDATA = tmpHome;
  process.env.HOME = tmpHome;
  process.env.USERPROFILE = tmpHome;
  delete process.env.VEILFILE_API_KEY;
  delete process.env.VEILFILE_BASE_URL;
  realFetch = global.fetch;
  // Never launch a real browser, and do not really wait between polls.
  realDeps = { ...cli.__test.deps };
  cli.__test.deps.openBrowser = async () => false;
  cli.__test.deps.sleep = async () => {};
});

afterEach(() => {
  global.fetch = realFetch;
  Object.assign(cli.__test.deps, realDeps);
  process.env = realEnv;
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Arg parsing
// ---------------------------------------------------------------------------

test('parseArgs: login with --print-key', () => {
  const opts = cli.parseArgs(['login', '--print-key']);
  assert.equal(opts.command, 'login');
  assert.equal(opts.printKey, true);
  assert.equal(opts.baseUrl, 'https://veilfile.com');
});

test('parseArgs: --base-url global flag and push --ttl-days', () => {
  const opts = cli.parseArgs(['--base-url', 'http://localhost:8000', 'push', 'f.png', '--ttl-days', '30']);
  assert.equal(opts.baseUrl, 'http://localhost:8000');
  assert.equal(opts.command, 'push');
  assert.deepEqual(opts.rest, ['f.png']);
  assert.equal(opts.ttlDays, '30');
});

test('parseArgs: VEILFILE_BASE_URL env fallback', () => {
  process.env.VEILFILE_BASE_URL = 'http://example.test';
  const opts = cli.parseArgs(['status']);
  assert.equal(opts.baseUrl, 'http://example.test');
});

// ---------------------------------------------------------------------------
// Key storage
// ---------------------------------------------------------------------------

test('key storage round-trips with 0600 permissions', () => {
  const p = cli.writeStoredKey('vlf_testkey123');
  const st = fs.statSync(p);
  assert.equal((st.mode & 0o777), 0o600);
  const { key, source } = cli.readStoredKey();
  assert.equal(key, 'vlf_testkey123');
  assert.equal(source, 'file');
  assert.equal(cli.deleteStoredKey(), true);
  assert.equal(cli.readStoredKey().source, 'none');
});

test('VEILFILE_API_KEY takes precedence over the file', () => {
  cli.writeStoredKey('vlf_fromfile');
  process.env.VEILFILE_API_KEY = 'vlf_fromenv';
  const { key, source } = cli.readStoredKey();
  assert.equal(key, 'vlf_fromenv');
  assert.equal(source, 'environment');
});

test('formatBytes', () => {
  assert.equal(cli.formatBytes(0), '0.0 B');
  assert.equal(cli.formatBytes(1536), '1.5 KB');
  assert.equal(cli.formatBytes(25 * 1024 * 1024), '25.0 MB');
  assert.equal(cli.formatBytes('nope'), '–');
});

// ---------------------------------------------------------------------------
// login
// ---------------------------------------------------------------------------

test('login: device flow stores the key and never prints it by default', async () => {
  const calls = [];
  let polls = 0;
  mockFetch(async (url, init) => {
    calls.push(url);
    if (url.endsWith('/api/v1/device/code')) {
      return jsonResponse(200, {
        device_code: 'dc123', user_code: 'ABCD-EFGH',
        verification_uri: 'https://veilfile.com/device',
        verification_uri_complete: 'https://veilfile.com/device?user_code=ABCD-EFGH',
        interval: 0, expires_in: 600,
      });
    }
    if (url.endsWith('/api/v1/device/token')) {
      polls += 1;
      if (polls < 2) return jsonResponse(400, { error: 'authorization_pending' });
      return jsonResponse(200, { api_key: 'vlf_newkey999' });
    }
    if (url.endsWith('/api/v1/usage')) {
      return jsonResponse(200, { email: 'a@b.c', plan: 'Free' });
    }
    throw new Error(`unexpected ${url}`);
  });

  const cap = captureOutput();
  try {
    const code = await cli.cmdLogin({ baseUrl: 'https://veilfile.com', printKey: false });
    assert.equal(code, 0);
  } finally {
    cap.restore();
  }
  assert.equal(polls, 2);
  const { key } = cli.readStoredKey();
  assert.equal(key, 'vlf_newkey999');
  assert.ok(!cap.out.join('\n').includes('vlf_newkey999'), 'key must not be printed');
  assert.ok(cap.out.join('\n').includes('ABCD-EFGH'), 'user code shown');
});

test('login: --print-key prints the key when explicitly asked', async () => {
  mockFetch(async (url) => {
    if (url.endsWith('/api/v1/device/code')) {
      return jsonResponse(200, { device_code: 'dc', interval: 0, expires_in: 600 });
    }
    if (url.endsWith('/api/v1/device/token')) {
      return jsonResponse(200, { api_key: 'vlf_printme' });
    }
    return jsonResponse(200, {});
  });
  const cap = captureOutput();
  try {
    await cli.cmdLogin({ baseUrl: 'https://veilfile.com', printKey: true });
  } finally {
    cap.restore();
  }
  assert.ok(cap.out.join('\n').includes('vlf_printme'));
});

test('login: access_denied stores nothing', async () => {
  mockFetch(async (url) => {
    if (url.endsWith('/api/v1/device/code')) {
      return jsonResponse(200, { device_code: 'dc', interval: 0, expires_in: 600 });
    }
    return jsonResponse(400, { error: 'access_denied' });
  });
  const cap = captureOutput();
  let code;
  // The poll loop reports through the return value; it must not exit.
  const origExit = process.exit;
  process.exit = (c) => { throw new Error(`process.exit(${c}) called`); };
  try {
    code = await cli.cmdLogin({ baseUrl: 'https://veilfile.com', printKey: false });
  } finally {
    process.exit = origExit;
    cap.restore();
  }
  assert.equal(code, 1);
  assert.equal(cli.readStoredKey().source, 'none');
  assert.ok(cap.err.join('').includes('denied'));
});

// ---------------------------------------------------------------------------
// logout
// ---------------------------------------------------------------------------

test('logout: revokes the matched key and deletes the local copy', async () => {
  cli.writeStoredKey('vlf_abcdefghijklmnop');
  const deleted = [];
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/keys') && init.method === 'GET') {
      return jsonResponse(200, [
        { id: 7, key_prefix: 'vlf_abcdefgh', is_active: true },
        { id: 8, key_prefix: 'vlf_otherkey0', is_active: true },
      ]);
    }
    if (url.endsWith('/api/v1/keys/7') && init.method === 'DELETE') {
      deleted.push(7);
      return jsonResponse(204, {});
    }
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdLogout({ baseUrl: 'https://veilfile.com' });
  } finally {
    cap.restore();
  }
  assert.equal(code, 0);
  assert.deepEqual(deleted, [7]);
  assert.equal(cli.readStoredKey().source, 'none');
  assert.ok(cap.out.join('\n').includes('Logged out.'));
});

test('logout: already-invalid key (401) still clears the local copy', async () => {
  cli.writeStoredKey('vlf_deadkey0000');
  mockFetch(async () => jsonResponse(401, { error: 'Invalid API key.', code: 'unauthorized' }));
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdLogout({ baseUrl: 'https://veilfile.com' });
  } finally {
    cap.restore();
  }
  assert.equal(code, 0);
  assert.equal(cli.readStoredKey().source, 'none');
  assert.ok(cap.out.join('\n').includes('already invalid'));
});

test('logout: unexpected status says so plainly (not "could not reach")', async () => {
  cli.writeStoredKey('vlf_somekey0000');
  mockFetch(async () => jsonResponse(500, { error: 'boom' }));
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdLogout({ baseUrl: 'https://veilfile.com' });
  } finally {
    cap.restore();
  }
  assert.equal(code, 1);
  const errText = cap.err.join('');
  assert.ok(errText.includes('HTTP 500'), 'names the status');
  assert.ok(!errText.toLowerCase().includes('could not reach'), 'must not claim unreachable');
  assert.equal(cli.readStoredKey().source, 'file', 'local copy left in place');
});

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

test('status: prints account summary without the key', async () => {
  cli.writeStoredKey('vlf_statuskey00');
  mockFetch(async (url) => {
    assert.ok(url.endsWith('/api/v1/usage'));
    return jsonResponse(200, {
      email: 'dev@example.com', email_verified: true, plan: 'Team',
      uploads_used: 12, uploads_cap: 2000,
      storage_used_bytes: 1048576, storage_cap_bytes: 25 * 1024 * 1024 * 1024,
    });
  });
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdStatus({ baseUrl: 'https://veilfile.com' });
  } finally {
    cap.restore();
  }
  assert.equal(code, 0);
  const text = cap.out.join('\n');
  assert.ok(text.includes('dev@example.com'));
  assert.ok(text.includes('Team'));
  assert.ok(!text.includes('vlf_statuskey00'));
});

test('status: revoked key tells the user to log in again', async () => {
  cli.writeStoredKey('vlf_revoked0000');
  mockFetch(async () => jsonResponse(401, { error: 'Invalid API key.' }));
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdStatus({ baseUrl: 'https://veilfile.com' });
  } finally {
    cap.restore();
  }
  assert.equal(code, 1);
  // An error: stderr, like every other failure.
  assert.ok(cap.err.join('').includes('`veilfile login`'));
});

// ---------------------------------------------------------------------------
// push / pull
// ---------------------------------------------------------------------------

test('push: uploads multipart and prints the URL; warns on secret flags', async () => {
  cli.writeStoredKey('vlf_pushkey0000');
  const f = path.join(tmpHome, 'note.txt');
  fs.writeFileSync(f, 'hello');
  let sentContentType = '';
  mockFetch(async (url, init) => {
    assert.ok(url.endsWith('/api/v1/artifacts'));
    assert.equal(init.method, 'POST');
    assert.equal(init.headers['Authorization'], 'Bearer vlf_pushkey0000');
    sentContentType = init.body && init.body.constructor.name;
    return jsonResponse(201, {
      id: 'abc', url: 'https://veilfile.com/a/TOKEN123',
      expires_at: '2026-10-13T00:00:00Z',
      secret_flags: ['github_token'],
    });
  });
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f);
  } finally {
    cap.restore();
  }
  assert.equal(code, 0);
  assert.equal(sentContentType, 'FormData');
  const text = cap.out.join('\n');
  assert.ok(text.includes('https://veilfile.com/a/TOKEN123'));
  assert.ok(text.toLowerCase().includes('warning'));
});

test('push: 402 storage cap and secret_detected are friendly', async () => {
  cli.writeStoredKey('vlf_pushkey0000');
  const f = path.join(tmpHome, 'a.txt');
  fs.writeFileSync(f, 'x');
  mockFetch(async () => jsonResponse(402, { error: 'cap', code: 'storage_cap_reached' }));
  const cap = captureOutput();
  let code;
  try { code = await cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f); }
  finally { cap.restore(); }
  assert.equal(code, 1);
  assert.ok(cap.err.join('').toLowerCase().includes('storage cap'));

  mockFetch(async () => jsonResponse(400, { error: 'secret', code: 'secret_detected' }));
  const cap2 = captureOutput();
  try { code = await cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f); }
  finally { cap2.restore(); }
  assert.equal(code, 1);
  assert.ok(cap2.err.join('').toLowerCase().includes('secret'));
});

test('push: oversize file rejected client-side', async () => {
  cli.writeStoredKey('vlf_pushkey0000');
  const f = path.join(tmpHome, 'big.bin');
  fs.writeFileSync(f, 'x');
  // fake the size by stubbing statSync
  const orig = fs.statSync;
  fs.statSync = (p) => ({ ...orig(p), isFile: () => true, size: 26 * 1024 * 1024 });
  let fetched = false;
  mockFetch(async () => { fetched = true; return jsonResponse(201, {}); });
  const cap = captureOutput();
  let code;
  try { code = await cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f); }
  finally { cap.restore(); fs.statSync = orig; }
  assert.equal(code, 1);
  assert.equal(fetched, false);
  assert.ok(cap.err.join('').includes('25.0 MB'));
});

test('pull: downloads bytes to --out', async () => {
  const payload = new TextEncoder().encode('file-bytes-here');
  mockFetch(async (url) => {
    assert.ok(String(url).includes('/a/TOKEN'));
    return {
      status: 200, ok: true,
      headers: new Headers({ 'content-disposition': 'attachment; filename="orig.txt"' }),
      arrayBuffer: async () => payload.buffer,
    };
  });
  const out = path.join(tmpHome, 'got.txt');
  const cap = captureOutput();
  let code;
  try { code = await cli.cmdPull({ baseUrl: 'https://veilfile.com', out }, 'https://veilfile.com/a/TOKEN'); }
  finally { cap.restore(); }
  assert.equal(code, 0);
  assert.equal(fs.readFileSync(out, 'utf-8'), 'file-bytes-here');
});

test('pull: 404 is a clean not-found message', async () => {
  mockFetch(async () => ({ status: 404, ok: false, headers: new Headers(), arrayBuffer: async () => new ArrayBuffer(0) }));
  const cap = captureOutput();
  let code;
  try { code = await cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/NOPE'); }
  finally { cap.restore(); }
  assert.equal(code, 1);
  assert.ok(cap.err.join('').toLowerCase().includes('not found'));
});
