'use strict';
// `veilfile push --visibility` (team seats PR E3).

const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const cli = require('../veilfile.js');

const URL_ = 'https://veilfile.com/a/TOKEN123';
let tmpHome;
let realFetch;
let realEnv;

function jsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers({ 'content-type': 'application/json' }),
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
  return { out, err, restore() { console.log = origLog; process.stderr.write = origErr; } };
}

// Push a file with `opts`, answering the upload with `answer`. Returns the exit code, the lines printed, and the
// form the CLI sent (null when it sent nothing).
async function push(opts, answer) {
  cli.writeStoredKey('vlf_pushkey0000');
  const f = path.join(tmpHome, 'note.txt');
  fs.writeFileSync(f, 'hello');
  let form = null;
  global.fetch = async (url, init) => { form = init.body; return jsonResponse(201, answer); };
  const cap = captureOutput();
  let code;
  try {
    code = await cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null, ...opts }, f);
  } finally {
    cap.restore();
  }
  return { code, out: cap.out, err: cap.err, form };
}

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'veilfile-test-'));
  realEnv = { ...process.env };
  process.env.XDG_CONFIG_HOME = tmpHome;
  process.env.APPDATA = tmpHome;
  process.env.HOME = tmpHome;
  process.env.USERPROFILE = tmpHome;
  delete process.env.VEILFILE_API_KEY;
  delete process.env.VEILFILE_BASE_URL;
  realFetch = global.fetch;
});

afterEach(() => {
  global.fetch = realFetch;
  process.env = realEnv;
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

test('parseArgs accepts --visibility on push, and refuses it on another command', () => {
  assert.equal(cli.parseArgs(['push', 'f.txt', '--visibility', 'team']).visibility, 'team');
  assert.equal(cli.parseArgs(['push', 'f.txt']).visibility, null);
  assert.throws(() => cli.parseArgs(['status', '--visibility', 'team']), /--visibility does not apply to status\./);
});

test('a bad visibility exits 2 before any request', async () => {
  let asked = false;
  global.fetch = async () => { asked = true; return jsonResponse(201, {}); };
  const cap = captureOutput();
  let code;
  try {
    code = await cli.main(['push', 'f.txt', '--visibility', 'public']);
  } finally {
    cap.restore();
  }
  assert.equal(code, 2);
  assert.equal(asked, false);
  assert.ok(cap.err.join('').includes('Send --visibility as only_me or team.'));
});

test('push sends the visibility field only when it is given', async () => {
  const given = await push({ visibility: 'team' }, { url: URL_, secret_flags: [], visibility: 'team' });
  assert.equal(given.form.get('visibility'), 'team');
  const none = await push({}, { url: URL_, secret_flags: [] });
  assert.equal(none.form.get('visibility'), null);
});

test('push prints who can see the file', async () => {
  for (const [value, sentence] of [['team', 'Visible to: your team.'], ['only_me', 'Visible to: only you.']]) {
    const { code, out } = await push(
      { visibility: value }, { url: URL_, expires_at: '2026-12-01T00:00:00Z', secret_flags: [], visibility: value },
    );
    assert.equal(code, 0);
    assert.deepEqual(out.slice(0, 3), [URL_, 'Expires: 2026-12-01T00:00:00Z', sentence]);
  }
});

test('an answer without visibility says it was not applied, only when the option was given', async () => {
  const given = await push({ visibility: 'team' }, { url: URL_, secret_flags: [] });
  assert.equal(given.code, 0);
  assert.ok(given.out.includes('The server did not apply --visibility.'));
  const none = await push({}, { url: URL_, secret_flags: [] });
  assert.deepEqual(none.out, [URL_]);
});

test('cmdPush without a visibility field behaves as today', async () => {
  const { code, out, form } = await push({}, { url: URL_, expires_at: '2026-12-01T00:00:00Z', secret_flags: [] });
  assert.equal(code, 0);
  assert.deepEqual(out, [URL_, 'Expires: 2026-12-01T00:00:00Z']);
  assert.equal(form.get('visibility'), null);
});
