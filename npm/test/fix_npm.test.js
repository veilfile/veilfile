'use strict';

// Regression tests for the npm CLI review findings (ids in test names).

const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const cli = require('../veilfile.js');

const t = cli.__test;
const CLI_PATH = path.join(__dirname, '..', 'veilfile.js');
const POSIX = process.platform !== 'win32';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let tmp;
let home;
let realFetch;
let realEnv;
let realDeps;
let realCwd;
let opened;

function mockFetch(handler) {
  global.fetch = async (url, init) => handler(String(url), init || {});
}

function jsonResponse(status, body, headers = {}) {
  return rawResponse(status, JSON.stringify(body), headers);
}

function rawResponse(status, text, headers = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers(headers),
    text: async () => text,
    json: async () => JSON.parse(text),
    arrayBuffer: async () => new TextEncoder().encode(text).buffer,
  };
}

/** A download response whose body is a stream of the given chunks. */
function fileResponse(chunks, headers = {}, status = 200) {
  const list = (Array.isArray(chunks) ? chunks : [chunks]).map(
    (c) => (typeof c === 'string' ? new TextEncoder().encode(c) : c)
  );
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers(headers),
    body: (async function* () { for (const c of list) yield c; })(),
    // For clients that buffer the whole body instead of streaming it.
    arrayBuffer: async () => {
      const all = new Uint8Array(list.reduce((n, c) => n + c.length, 0));
      let at = 0;
      for (const c of list) { all.set(c, at); at += c.length; }
      return all.buffer;
    },
  };
}

async function capture(fn) {
  const out = [];
  const err = [];
  const origLog = console.log;
  const origErr = process.stderr.write;
  console.log = (...a) => out.push(a.join(' '));
  process.stderr.write = (s) => { err.push(String(s)); return true; };
  try {
    const code = await fn();
    return { code, out: out.join('\n'), err: err.join('') };
  } finally {
    console.log = origLog;
    process.stderr.write = origErr;
  }
}

/** Every file and directory under dir, relative, sorted. */
function tree(dir) {
  const found = [];
  const walk = (d) => {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, entry.name);
      found.push(path.relative(dir, p));
      if (entry.isDirectory()) walk(p);
    }
  };
  walk(dir);
  return found.sort();
}

function hermeticEnv(extra = {}) {
  const env = { ...process.env, ...extra };
  for (const k of ['XDG_CONFIG_HOME', 'APPDATA', 'HOME', 'USERPROFILE']) env[k] = home;
  delete env.VEILFILE_API_KEY;
  delete env.VEILFILE_BASE_URL;
  return env;
}

function runCli(args, { cwd, env } = {}) {
  return new Promise((resolve) => {
    childProcess.execFile(
      process.execPath, [CLI_PATH, ...args],
      { cwd: cwd || tmp, env: env || hermeticEnv(), timeout: 30000 },
      (error, stdout, stderr) => {
        resolve({ code: error ? error.code : 0, stdout, stderr });
      }
    );
  });
}

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, '127.0.0.1', () => {
      resolve({ server, base: `http://127.0.0.1:${server.address().port}` });
    });
  });
}

/** Device-flow responses for a login that gets `apiKey` on the first poll. */
function deviceFlow(apiKey, extra = {}) {
  return async (url, init) => {
    if (url.endsWith('/api/v1/device/code')) {
      return jsonResponse(200, { device_code: 'dc', user_code: 'ABCD-EFGH', interval: 0, expires_in: 600, ...extra });
    }
    if (url.endsWith('/api/v1/device/token')) return jsonResponse(200, { api_key: apiKey });
    if (url.endsWith('/api/v1/usage')) return jsonResponse(200, { email: 'a@b.c', plan: 'Free' });
    throw new Error(`unexpected ${init.method} ${url}`);
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'veilfile-fix-'));
  home = path.join(tmp, 'home');
  fs.mkdirSync(home);
  realEnv = { ...process.env };
  for (const k of ['XDG_CONFIG_HOME', 'APPDATA', 'HOME', 'USERPROFILE']) process.env[k] = home;
  delete process.env.VEILFILE_API_KEY;
  delete process.env.VEILFILE_BASE_URL;
  realFetch = global.fetch;
  realDeps = { ...t.deps };
  opened = [];
  t.deps.openBrowser = async (url) => { opened.push(url); return true; };
  t.deps.sleep = async () => {};
  realCwd = process.cwd();
});

afterEach(() => {
  process.chdir(realCwd);
  global.fetch = realFetch;
  Object.assign(t.deps, realDeps);
  process.env = realEnv;
  fs.rmSync(tmp, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// pull: filenames (G1, G3, npm-1, ctr-2)
// ---------------------------------------------------------------------------

test('G3: Content-Disposition parsing prefers filename*, unescapes quoted-pairs, decodes only filename*', () => {
  const p = t.parseContentDisposition;
  assert.deepEqual(p('attachment; filename=report.pdf'), { filename: 'report.pdf', filenameStar: null });
  assert.equal(p('attachment; filename="a \\"b\\" \\\\c.txt"').filename, 'a "b" \\c.txt');
  assert.equal(p('attachment; filename="..%2Fx.json"').filename, '..%2Fx.json');
  assert.equal(p("inline; filename*=utf-8''r%C3%A9sum%C3%A9.pdf").filenameStar, 'résumé.pdf');
  assert.equal(p("attachment; filename*=iso-8859-1'en'%E9t%E9.txt").filenameStar, 'été.txt');
  assert.equal(p("attachment; filename*=utf-8''%E0%A4%A.txt").filenameStar, null);
  assert.equal(p('attachment; filename="x"; filename="y"').filename, 'x');
  assert.deepEqual(p('attachment'), { filename: null, filenameStar: null });

  const name = (cd) => t.filenameFromResponse('https://veilfile.com/a/TOKEN', new Headers({ 'content-disposition': cd }));
  assert.equal(name(`attachment; filename="plain.txt"; filename*=UTF-8''fancy%20name.txt`), 'fancy name.txt');
  assert.equal(name('attachment; filename=report.pdf'), 'report.pdf');
  assert.equal(name('attachment; filename=".."'), 'TOKEN');
  assert.equal(t.filenameFromResponse('https://veilfile.com/', new Headers()), 'download');
});

test('G1/npm-1: sanitizeFilename keeps one safe path component', () => {
  const cases = [
    ['../../PWNED.txt', 'PWNED.txt'],
    ['..\\..\\win.txt', 'win.txt'],
    ['/etc/cron.d/evil', 'evil'],
    ['C:\\Windows\\System32\\evil.dll', 'evil.dll'],
    ['C:evil.txt', 'C_evil.txt'],
    ['report.pdf:hidden', 'report.pdf_hidden'],
    ['.bashrc', 'bashrc'],
    ['.claude/settings.json', 'settings.json'],
    ['..', ''],
    ['.', ''],
    ['', ''],
    [' . ', ''],
    ['CON', '_CON'],
    ['nul.txt', '_nul.txt'],
    ['console.txt', 'console.txt'],
    ['a\u0000b\r\n\u001b[31m.txt', 'ab[31m.txt'],
    ['invoice\u202Efdp.exe', 'invoicefdp.exe'],
    ['trailing. ', 'trailing'],
    ['ok name (1).png', 'ok name (1).png'],
  ];
  for (const [input, expected] of cases) {
    assert.equal(t.sanitizeFilename(input), expected, JSON.stringify(input));
  }
  assert.ok(t.sanitizeFilename(`${'a'.repeat(400)}.json`).length <= 200);
  assert.ok(t.sanitizeFilename(`${'a'.repeat(400)}.json`).endsWith('.json'));
  assert.equal(t.sanitizeFilename(null), '');
  // Line separators, zero-width and bidi marks are dropped too.
  assert.equal(t.sanitizeFilename(`a${String.fromCharCode(0x2028, 0x200b, 0x061c, 0x2066)}b.txt`), 'ab.txt');
  assert.equal(t.sanitizeFilename(`COM${String.fromCharCode(0xb9)}.txt`), `_COM${String.fromCharCode(0xb9)}.txt`);
});

test('G1: long names are cut by UTF-8 bytes, keep the extension and never end in a space or dot', () => {
  // 250 CJK characters are 750 bytes: past the 255-byte name limit on disk.
  const kanji = String.fromCharCode(0x6f22);
  const cjk = t.sanitizeFilename(`${kanji.repeat(250)}.txt`);
  assert.ok(Buffer.byteLength(cjk) <= 200, `${Buffer.byteLength(cjk)} bytes`);
  assert.ok(cjk.startsWith(kanji) && cjk.endsWith('.txt'), cjk);
  // A cut that lands just after a space must not leave a trailing space.
  assert.equal(t.sanitizeFilename(`${'x'.repeat(199)} y`), 'x'.repeat(199));
  assert.equal(t.sanitizeFilename(`${'x'.repeat(199)}.${'y'.repeat(300)}`), `${'x'.repeat(184)}.${'y'.repeat(15)}`);
  // Device names followed by spaces before the extension are devices too,
  // including when the cut produces one.
  assert.equal(t.sanitizeFilename('CON .txt'), '_CON .txt');
  assert.equal(t.sanitizeFilename('nul  .tar.gz'), '_nul  .tar.gz');
  assert.equal(t.sanitizeFilename('console .txt'), 'console .txt');
  const cut = t.sanitizeFilename(`CON${' '.repeat(300)}x.txt`);
  assert.ok(cut.startsWith('_CON ') && cut.endsWith('.txt'), JSON.stringify(cut));
});

const HOSTILE = [
  ['attachment; filename="../../PWNED.txt"', 'PWNED.txt'],
  ['attachment; filename="..%2F..%2Fescaped.json"', '%2F..%2Fescaped.json'],
  ["attachment; filename*=UTF-8''..%2F..%2Fescaped.json", 'escaped.json'],
  ['inline; filename=".claude%2Fsettings.json"', 'claude%2Fsettings.json'],
  ["inline; filename*=utf-8''.claude%2Fsettings.json", 'settings.json'],
  ['attachment; filename="..\\\\..\\\\win.txt"', 'win.txt'],
  ['attachment; filename="/etc/cron.d/evil"', 'evil'],
  ['attachment; filename="C:\\\\Windows\\\\evil.dll"', 'evil.dll'],
  ['attachment; filename=".."', 'TOKEN'],
];

test('G1/npm-1/ctr-2: hostile Content-Disposition names stay inside the target directory', async () => {
  const root = path.join(tmp, 'work');
  const cwd = path.join(root, 'a', 'b', 'cwd');
  fs.mkdirSync(cwd, { recursive: true });
  process.chdir(cwd);
  for (const [header, expected] of HOSTILE) {
    mockFetch(async () => fileResponse('payload', { 'content-disposition': header }));
    const r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/TOKEN'));
    assert.equal(r.code, 0, `${header}: ${r.err}`);
    assert.ok(r.out.includes(path.join(cwd, expected)), `${header} -> ${r.out}`);
    assert.equal(fs.readFileSync(path.join(cwd, expected), 'utf-8'), 'payload');
    fs.unlinkSync(path.join(cwd, expected));
  }
  // Nothing was created anywhere else, not even a .claude directory.
  assert.deepEqual(tree(root), ['a', path.join('a', 'b'), path.join('a', 'b', 'cwd')]);
});

test('G1: --out DIR uses the sanitized server name inside DIR', async () => {
  const outDir = path.join(tmp, 'downloads');
  fs.mkdirSync(outDir);
  mockFetch(async () => fileResponse('x', { 'content-disposition': "attachment; filename*=UTF-8''..%2F..%2Fup.json" }));
  const r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: outDir }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 0, r.err);
  assert.deepEqual(fs.readdirSync(outDir), ['up.json']);
  assert.deepEqual(fs.readdirSync(tmp).sort(), ['downloads', 'home']);
});

// ---------------------------------------------------------------------------
// pull: overwrite, size cap, hosts (npm-2, S1)
// ---------------------------------------------------------------------------

test('npm-2: pull refuses to overwrite an existing file unless --force', async () => {
  const cwd = path.join(tmp, 'repo');
  fs.mkdirSync(cwd);
  fs.writeFileSync(path.join(cwd, 'package.json'), '{"name":"victim"}');
  process.chdir(cwd);
  mockFetch(async () => fileResponse('{"scripts":{"preinstall":"evil"}}', {
    'content-disposition': 'attachment; filename="package.json"',
  }));
  let r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 1);
  assert.match(r.err, /Refusing to overwrite .*package\.json.*--force/);
  assert.equal(r.out, '');
  assert.equal(fs.readFileSync(path.join(cwd, 'package.json'), 'utf-8'), '{"name":"victim"}');
  assert.deepEqual(fs.readdirSync(cwd), ['package.json'], 'no partial file left');

  r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null, force: true }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 0, r.err);
  assert.match(fs.readFileSync(path.join(cwd, 'package.json'), 'utf-8'), /preinstall/);
  assert.deepEqual(fs.readdirSync(cwd), ['package.json']);
});

test('npm-2: an explicit --out file is not replaced without --force either', async () => {
  const out = path.join(tmp, 'got.txt');
  fs.writeFileSync(out, 'mine');
  let fetched = false;
  mockFetch(async () => { fetched = true; return fileResponse('theirs'); });
  const r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 1);
  assert.equal(fetched, false);
  assert.equal(fs.readFileSync(out, 'utf-8'), 'mine');
});

test('S1: pull refuses bodies over the size cap, declared or streamed, and leaves no file', async () => {
  const cwd = path.join(tmp, 'cwd');
  fs.mkdirSync(cwd);
  process.chdir(cwd);
  mockFetch(async () => fileResponse('x', {
    'content-length': String(t.MAX_DOWNLOAD_BYTES + 1),
    'content-disposition': 'attachment; filename="big.bin"',
  }));
  let r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 1);
  assert.match(r.err, /Refusing to save more than/);

  const mib = new Uint8Array(1024 * 1024);
  let produced = 0;
  mockFetch(async () => ({
    status: 200,
    ok: true,
    headers: new Headers({ 'content-disposition': 'attachment; filename="big.bin"' }),
    body: (async function* () { for (;;) { produced += mib.length; yield mib; } })(),
  }));
  r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 1);
  assert.match(r.err, /Refusing to save more than/);
  assert.ok(produced <= t.MAX_DOWNLOAD_BYTES + 2 * mib.length, 'stopped reading at the cap');
  assert.deepEqual(fs.readdirSync(cwd), []);
});

test('pull: a short body (size mismatch) is not saved', async () => {
  const cwd = path.join(tmp, 'cwd');
  fs.mkdirSync(cwd);
  process.chdir(cwd);
  mockFetch(async () => fileResponse('abc', {
    'content-length': '10',
    'content-disposition': 'attachment; filename="a.txt"',
  }));
  const r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/T'));
  assert.equal(r.code, 1);
  assert.match(r.err, /incomplete/);
  assert.deepEqual(fs.readdirSync(cwd), []);
});

test('ctr-2: pull only fetches from the base URL host, and never follows a redirect elsewhere', async () => {
  let fetched = 0;
  mockFetch(async () => { fetched += 1; return fileResponse('x'); });
  let r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://attacker.example/a/x'));
  assert.equal(r.code, 1);
  assert.equal(fetched, 0);
  assert.match(r.err, /Refusing to download from attacker\.example/);

  const cwd = path.join(tmp, 'cwd');
  fs.mkdirSync(cwd);
  process.chdir(cwd);
  const seen = [];
  mockFetch(async (url, init) => {
    seen.push(url);
    assert.equal(init.redirect, 'manual');
    assert.ok(init.signal, 'download has a timeout signal');
    if (url.endsWith('/a/one')) return rawResponse(302, '', { location: '/a/two' });
    if (url.endsWith('/a/two')) return rawResponse(302, '', { location: 'https://evil.example/x' });
    throw new Error(`unexpected ${url}`);
  });
  r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/one'));
  assert.equal(r.code, 1);
  assert.deepEqual(seen, ['https://veilfile.com/a/one', 'https://veilfile.com/a/two']);
  assert.match(r.err, /redirect to https:\/\/evil\.example/);
  assert.deepEqual(fs.readdirSync(cwd), []);
});

test('pull: a redirect loop on the same server is reported as too many redirects', async () => {
  const cwd = path.join(tmp, 'cwd');
  fs.mkdirSync(cwd);
  process.chdir(cwd);
  let fetched = 0;
  mockFetch(async () => { fetched += 1; return rawResponse(302, '', { location: '/a/loop' }); });
  const r = await capture(() => cli.cmdPull({ baseUrl: 'https://veilfile.com', out: null }, 'https://veilfile.com/a/loop'));
  assert.equal(r.code, 1);
  assert.match(r.err, /Too many redirects/);
  assert.ok(!/Refusing to follow a redirect/.test(r.err), 'the CLI\'s own server is not called foreign');
  assert.equal(fetched, 6);
  assert.deepEqual(fs.readdirSync(cwd), []);
});

// ---------------------------------------------------------------------------
// pull end to end against a real HTTP server
// ---------------------------------------------------------------------------

test('npm-1/ctr-2 e2e: real server, Django header formats, nothing lands outside the cwd', async () => {
  // Header strings exactly as Django's content_disposition_header and the
  // current inline path produce them for uploader-chosen names.
  const headers = {
    '/a/django-quoted': 'attachment; filename="..%2F..%2Fescaped.json"',
    '/a/django-star': "attachment; filename*=utf-8''..%2F..%2F%C3%A9scaped.json",
    '/a/inline-quoted': 'inline; filename="..%252F..%252Finline.json"',
    '/a/claude': "inline; filename*=utf-8''.claude%2Fsettings.json",
    '/a/backslash': 'attachment; filename="..\\\\..\\\\win.txt"',
    '/a/dotdot': 'attachment; filename="../../PWNED.txt"',
    '/a/readme': 'inline; filename="CLAUDE.md"',
  };
  const { server, base } = await listen((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Disposition': headers[req.url] });
    res.end('{"attacker":true}');
  });
  try {
    const root = path.join(tmp, 'work');
    const cwd = path.join(root, 'a', 'b', 'cwd');
    fs.mkdirSync(cwd, { recursive: true });
    fs.writeFileSync(path.join(cwd, 'CLAUDE.md'), 'MY OWN INSTRUCTIONS');
    const expected = {
      '/a/django-quoted': '%2F..%2Fescaped.json',
      '/a/django-star': 'éscaped.json',
      '/a/inline-quoted': '%252F..%252Finline.json',
      '/a/claude': 'settings.json',
      '/a/backslash': 'win.txt',
      '/a/dotdot': 'PWNED.txt',
    };
    for (const [urlPath, name] of Object.entries(expected)) {
      const r = await runCli(['pull', `${base}${urlPath}`, '--base-url', base], { cwd });
      assert.equal(r.code, 0, `${urlPath}: ${r.stderr}`);
      assert.equal(fs.readFileSync(path.join(cwd, name), 'utf-8'), '{"attacker":true}');
    }
    const r = await runCli(['--base-url', base, 'pull', `${base}/a/readme`], { cwd });
    assert.equal(r.code, 1);
    assert.match(r.stderr, /Refusing to overwrite/);
    assert.equal(r.stdout, '');
    assert.equal(fs.readFileSync(path.join(cwd, 'CLAUDE.md'), 'utf-8'), 'MY OWN INSTRUCTIONS');

    const inside = ['CLAUDE.md', ...Object.values(expected)].map((n) => path.join('a', 'b', 'cwd', n));
    assert.deepEqual(tree(root), ['a', path.join('a', 'b'), path.join('a', 'b', 'cwd'), ...inside].sort());
  } finally {
    server.close();
  }
});

test('pull e2e: a server that trickles bytes forever hits the overall deadline and leaves nothing', async () => {
  // A byte every 20 ms never trips the 30 s idle timeout.
  const timers = new Set();
  const { server, base } = await listen((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="slow.bin"' });
    res.write('x');
    const timer = setInterval(() => res.write('x'), 20);
    timers.add(timer);
    res.on('close', () => { clearInterval(timer); timers.delete(timer); });
  });
  const cwd = path.join(tmp, 'cwd');
  fs.mkdirSync(cwd);
  process.chdir(cwd);
  t.deps.pullDeadlineMs = 400;
  try {
    const started = Date.now();
    const r = await capture(() => cli.cmdPull({ baseUrl: base, out: null }, `${base}/a/T`));
    assert.equal(r.code, 1);
    assert.match(r.err, /Timed out: the download took longer than \d+s in total\./);
    assert.ok(Date.now() - started < 10000, 'gave up at the deadline');
    assert.deepEqual(fs.readdirSync(cwd), [], 'no partial file left');
  } finally {
    for (const timer of timers) clearInterval(timer);
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

// ---------------------------------------------------------------------------
// Browser opener (G5, npm-3, dev-6, F1)
// ---------------------------------------------------------------------------

test('G5/npm-3: the opener runs without a shell on every platform', async () => {
  const url = 'https://veilfile.com/device?user_code=$(touch%20x)&a=b';
  const realSpawn = childProcess.spawn;
  const realPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  const calls = [];
  childProcess.spawn = (command, args, options) => {
    calls.push({ command, args, options });
    const child = new EventEmitter();
    child.unref = () => {};
    process.nextTick(() => child.emit('spawn'));
    return child;
  };
  try {
    for (const [platform, command, args] of [
      ['darwin', 'open', [url]],
      ['linux', 'xdg-open', [url]],
      ['win32', 'rundll32', ['url.dll,FileProtocolHandler', url]],
    ]) {
      Object.defineProperty(process, 'platform', { value: platform, configurable: true });
      assert.equal(await realDeps.openBrowser(url), true);
      const call = calls.pop();
      assert.equal(call.command, command);
      assert.deepEqual(call.args, args);
      assert.ok(!call.options.shell, 'no shell');
      assert.equal(call.options.detached, true, 'does not tie the CLI to the browser');
    }
    childProcess.spawn = () => {
      const child = new EventEmitter();
      process.nextTick(() => child.emit('error', new Error('ENOENT')));
      return child;
    };
    assert.equal(await realDeps.openBrowser(url), false);
  } finally {
    childProcess.spawn = realSpawn;
    Object.defineProperty(process, 'platform', realPlatform);
  }
});

test('dev-6: only an https page on the base URL origin is opened', async () => {
  const cases = [
    ['https://veilfile.com/device?user_code=ABCD', 'https://veilfile.com/device?user_code=ABCD'],
    ['https://evil.example/device', null],
    ['http://veilfile.com/device', null],
    ['javascript:alert(1)', null],
    ['file:///etc/passwd', null],
    ['https://user:pw@veilfile.com/device', null],
    ['not a url', null],
  ];
  for (const [uri, expected] of cases) {
    assert.equal(t.browserUrl(uri, 'https://veilfile.com'), expected, uri);
  }
  assert.equal(t.browserUrl('http://127.0.0.1:8000/device', 'http://127.0.0.1:8000'), 'http://127.0.0.1:8000/device');

  for (const uri of ['https://evil.example/device?user_code=X', 'javascript:alert(1)']) {
    opened = [];
    mockFetch(deviceFlow('vlf_k_000000001', { verification_uri_complete: uri }));
    const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
    assert.equal(r.code, 0);
    assert.deepEqual(opened, [], uri);
    assert.match(r.out, /Not opened automatically/);
    cli.deleteStoredKey();
  }
});

test('npm-3/dev-6 e2e: a hostile verification URL never reaches a shell', { skip: !POSIX }, async () => {
  const marker = path.join(tmp, 'PWNED');
  const stubs = path.join(tmp, 'bin');
  const log = path.join(tmp, 'opener.log');
  fs.mkdirSync(stubs);
  for (const name of ['open', 'xdg-open']) {
    fs.writeFileSync(path.join(stubs, name), `#!/bin/sh\nprintf '%s\\n' "$@" >> '${log}'\n`, { mode: 0o755 });
  }
  const { server, base } = await listen((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/v1/device/code') {
      return res.end(JSON.stringify({
        device_code: 'dc', user_code: 'ABCD-EFGH', interval: 0, expires_in: 60,
        verification_uri_complete: `http://${req.headers.host}/device?user_code=$(touch ${marker})`,
      }));
    }
    if (req.url === '/api/v1/device/token') return res.end(JSON.stringify({ api_key: 'vlf_e2e_key_00001' }));
    if (req.url === '/api/v1/usage') return res.end(JSON.stringify({ email: 'e2e@example.com', plan: 'Free' }));
    res.statusCode = 404;
    return res.end('{}');
  });
  try {
    const env = hermeticEnv({ PATH: `${stubs}${path.delimiter}${process.env.PATH}` });
    const r = await runCli(['login', '--base-url', base], { env });
    assert.equal(r.code, 0, r.stderr);
    for (let i = 0; i < 50 && !fs.existsSync(log); i++) await new Promise((res) => setTimeout(res, 50));
    assert.match(fs.readFileSync(log, 'utf-8'), /user_code=\$\(touch/, 'the opener got the URL as one literal argument');
    assert.equal(fs.existsSync(marker), false, 'no shell ran the command substitution');
  } finally {
    server.close();
  }
});

// ---------------------------------------------------------------------------
// login (G2, G6, F1, npm-7, dev-7, npm-8)
// ---------------------------------------------------------------------------

test('G2: loading the module registers no SIGINT handler', () => {
  const r = childProcess.spawnSync(
    process.execPath,
    ['-e', `require(${JSON.stringify(CLI_PATH)}); process.stdout.write(String(process.listenerCount('SIGINT')))`],
    { encoding: 'utf-8' }
  );
  assert.equal(r.stdout, '0');
});

test('G2: Ctrl-C during the login poll cancels cleanly and the handler is removed', async () => {
  const before = process.listeners('SIGINT');
  t.deps.sleep = (ms, signal) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    const ours = process.listeners('SIGINT').filter((l) => !before.includes(l));
    assert.equal(ours.length, 1, 'one handler while polling');
    setImmediate(ours[0]);
  });
  mockFetch(async (url) => {
    if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 5, expires_in: 600 });
    throw new Error(`unexpected ${url}`);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 130);
  assert.match(r.err, /Login cancelled\. Nothing was stored\./);
  assert.ok(!/may have issued/.test(r.err), 'no poll was in flight');
  assert.deepEqual(process.listeners('SIGINT'), before);
  assert.equal(cli.readStoredKey().source, 'none');
});

test('npm-8: Ctrl-C while a token poll is in flight says a key may have been issued', async () => {
  // The server mints the key on the poll that delivers it.
  const before = process.listeners('SIGINT');
  const sigint = sigintFrom(before);
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 600 });
    if (url.endsWith('/api/v1/device/token')) {
      sigint();
      assert.ok(init.signal.aborted, 'the poll is aborted');
      throw init.signal.reason;
    }
    throw new Error(`unexpected ${url}`);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 130);
  assert.match(r.err, /Login cancelled\. Nothing was stored\. A poll got no answer, so the server may have issued a key that never arrived: if a new "Terminal login" key shows in the dashboard, revoke it\./);
  assert.equal(cli.readStoredKey().source, 'none');
  assert.deepEqual(process.listeners('SIGINT'), before);
});

test('npm-8: Ctrl-C while waiting to retry a poll whose answer was lost also points at the dashboard', async () => {
  const before = process.listeners('SIGINT');
  const sigint = sigintFrom(before);
  let sleeps = 0;
  t.deps.sleep = (ms, signal) => new Promise((resolve, reject) => {
    sleeps += 1;
    if (sleeps === 1) {
      resolve();
      return;
    }
    // The wait before the retry: Ctrl-C.
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    setImmediate(sigint);
  });
  mockFetch(async (url) => {
    if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 600 });
    if (url.endsWith('/api/v1/device/token')) throw new TypeError('fetch failed');
    throw new Error(`unexpected ${url}`);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 130);
  assert.equal(sleeps, 2);
  assert.match(r.err, /Login cancelled\. Nothing was stored\. A poll got no answer, so the server may have issued a key/);
  assert.equal(cli.readStoredKey().source, 'none');
  assert.deepEqual(process.listeners('SIGINT'), before);
});

test('npm-8: a lost poll answer followed by invalid_grant or expiry points at the dashboard', async () => {
  const lost = /may have issued a key that never arrived/;
  const cases = [
    // [first poll, second poll's RFC 8628 error, hint expected]
    ['timeout', () => { throw new TypeError('fetch failed'); }, 'invalid_grant', true],
    ['500 after minting', () => rawResponse(500, 'Server Error'), 'invalid_grant', true],
    ['504 then past expiry', () => rawResponse(504, 'Gateway Timeout'), 'expired_token', true],
    // A denied grant never minted a key.
    ['timeout then denied', () => { throw new TypeError('fetch failed'); }, 'access_denied', false],
    // Throttled or intercepted before reaching the view: nothing minted.
    ['429', () => jsonResponse(429, { error: 'throttled' }), 'invalid_grant', false],
    ['captive portal', () => rawResponse(200, '<html>portal</html>'), 'invalid_grant', false],
    ['no hiccup', () => jsonResponse(400, { error: 'authorization_pending' }), 'invalid_grant', false],
  ];
  for (const [name, first, error, hint] of cases) {
    let polls = 0;
    mockFetch(async (url) => {
      if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 600 });
      polls += 1;
      return polls === 1 ? first() : jsonResponse(400, { error });
    });
    const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
    assert.equal(r.code, 1, name);
    assert.equal(lost.test(r.err), hint, `${name}: ${r.err}`);
    assert.equal(cli.readStoredKey().source, 'none');
  }
});

test('G2: Ctrl-C after the key is stored says so instead of "Nothing was stored"', async () => {
  const before = process.listeners('SIGINT');
  const exits = [];
  const origExit = process.exit;
  process.exit = (c) => { exits.push(c); throw new Error('exit'); };
  const flow = deviceFlow('vlf_stored_then_c');
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/usage')) {
      // Ctrl-C while the key is already on disk.
      process.listeners('SIGINT').filter((l) => !before.includes(l))[0]();
    }
    return flow(url, init);
  });
  let r;
  try {
    r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  } finally {
    process.exit = origExit;
  }
  assert.deepEqual(exits, [130]);
  assert.match(r.err, /Interrupted\. The new key is already stored in /);
  assert.ok(!/Nothing was stored/.test(r.err));
  assert.equal(cli.readStoredKey().key, 'vlf_stored_then_c');
  assert.deepEqual(process.listeners('SIGINT'), before);
});

test('F1: an explicit interval of 0 is honoured (1s floor); a missing or bad one means 5s', async () => {
  for (const [interval, expected] of [[0, 1000], [undefined, 5000], [2, 2000], ['5s', 5000], [-1, 5000]]) {
    const waits = [];
    t.deps.sleep = async (ms) => { waits.push(ms); };
    mockFetch(deviceFlow('vlf_interval_key1', { interval }));
    const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
    assert.equal(r.code, 0, r.err);
    assert.equal(waits[0], expected, `interval ${interval}`);
    cli.deleteStoredKey();
  }
});

test('npm-7/dev-7: 5xx, network errors, non-JSON pages and 429 during polling are retried with backoff', async () => {
  const waits = [];
  t.deps.sleep = async (ms) => { waits.push(ms); };
  let polls = 0;
  mockFetch(async (url) => {
    if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 600 });
    if (url.endsWith('/api/v1/device/token')) {
      polls += 1;
      if (polls === 1) return rawResponse(502, '<html>502 Bad Gateway</html>', { 'content-type': 'text/html' });
      if (polls === 2) throw new TypeError('fetch failed');
      if (polls === 3) return rawResponse(200, '<html>captive portal</html>');
      if (polls === 4) return jsonResponse(429, { error: 'Too many requests.' }, { 'retry-after': '40' });
      if (polls === 5) return jsonResponse(400, { error: 'authorization_pending' });
      return jsonResponse(200, { api_key: 'vlf_after_hiccup' });
    }
    if (url.endsWith('/api/v1/usage')) return jsonResponse(200, { email: 'a@b.c', plan: 'Free' });
    throw new Error(`unexpected ${url}`);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.equal(polls, 6);
  assert.equal(cli.readStoredKey().key, 'vlf_after_hiccup');
  // Doubling backoff, Retry-After honoured, back to the interval once the
  // server answers normally.
  assert.deepEqual(waits, [1000, 2000, 4000, 8000, 40000, 1000]);
  assert.match(r.err, /HTTP 502/);
});

test('G6: terminal poll results return an exit code instead of calling process.exit', async () => {
  const origExit = process.exit;
  process.exit = (c) => { throw new Error(`process.exit(${c}) called`); };
  try {
    const cases = [
      [{ error: 'access_denied' }, /denied in the browser\. Nothing was stored/],
      [{ error: 'access_denied', error_description: "Your plan's API key limit has been reached." }, /key limit has been reached/],
      [{ error: 'expired_token' }, /expired before approval/],
      [{ error: 'invalid_grant' }, /Login failed \(invalid_grant\)/],
    ];
    for (const [body, pattern] of cases) {
      mockFetch(async (url) => {
        if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 600 });
        return jsonResponse(400, body);
      });
      const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
      assert.equal(r.code, 1);
      assert.match(r.err, pattern);
      assert.equal(cli.readStoredKey().source, 'none');
    }
    // A server that keeps failing until the code expires: expiry, with the last error.
    t.deps.sleep = () => new Promise((resolve) => setTimeout(resolve, 20));
    mockFetch(async (url) => {
      if (url.endsWith('/api/v1/device/code')) return jsonResponse(200, { device_code: 'dc', interval: 1, expires_in: 1 });
      return rawResponse(503, 'down');
    });
    const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
    assert.equal(r.code, 1);
    assert.match(r.err, /expired before approval\. Run `veilfile login` again\. \(Last error: HTTP 503/);
    assert.ok(!/Retrying in 60s/.test(r.err), 'never promises a wait past the expiry');
  } finally {
    process.exit = origExit;
  }
});

test('npm-8: re-login revokes the key this CLI stored before, authenticating as that key', async () => {
  cli.writeStoredKey('vlf_oldkey_abcdef');
  const calls = [];
  const flow = deviceFlow('vlf_newkey_123456');
  mockFetch(async (url, init) => {
    calls.push(`${init.method} ${new URL(url).pathname} ${init.headers.Authorization || ''}`.trim());
    if (url.endsWith('/api/v1/keys') && init.method === 'GET') {
      return jsonResponse(200, [
        { id: 41, key_prefix: 'vlf_newkey_1', is_active: true },
        { id: 40, key_prefix: 'vlf_oldkey_a', is_active: true },
      ]);
    }
    if (url.endsWith('/api/v1/keys/40') && init.method === 'DELETE') return jsonResponse(204, {});
    return flow(url, init);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.equal(cli.readStoredKey().key, 'vlf_newkey_123456');
  assert.ok(calls.includes('GET /api/v1/keys Bearer vlf_oldkey_abcdef'), calls.join('\n'));
  assert.ok(calls.includes('DELETE /api/v1/keys/40 Bearer vlf_oldkey_abcdef'), calls.join('\n'));
  assert.ok(!calls.some((c) => c.startsWith('DELETE /api/v1/keys/41')));
  assert.match(r.out, /previously stored key was revoked/);
  assert.ok(!r.out.includes('vlf_newkey_123456'));
});

test('npm-8: an already-invalid previous key is fine; a failed revocation warns but login succeeds', async () => {
  cli.writeStoredKey('vlf_oldkey_abcdef');
  const flow = deviceFlow('vlf_newkey_000001');
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/keys')) return jsonResponse(401, { error: 'Invalid API key.', code: 'unauthorized' });
    return flow(url, init);
  });
  let r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.equal(r.err, '');

  const flow2 = deviceFlow('vlf_newkey_000002');
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/keys')) return jsonResponse(500, { error: 'boom' });
    return flow2(url, init);
  });
  r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0);
  assert.match(r.err, /previously stored key \(vlf_newkey_0…\) could not be revoked: .*HTTP 500/);
  assert.equal(cli.readStoredKey().key, 'vlf_newkey_000002');
});

test('npm-8: logging in to another server never touches this server\'s key', async () => {
  cli.writeStoredKey('vlf_production_k1', 'https://veilfile.com');
  const calls = [];
  const flow = deviceFlow('vlf_devserver_k1');
  mockFetch(async (url, init) => {
    calls.push(`${init.method} ${url}`);
    return flow(url, init);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'http://localhost:8000' }));
  assert.equal(r.code, 0, r.err);
  assert.ok(calls.every((c) => c.includes('http://localhost:8000/')));
  assert.ok(!calls.some((c) => c.includes('/api/v1/keys')));
  assert.equal(cli.readStoredKey('https://veilfile.com').key, 'vlf_production_k1');
  assert.equal(cli.readStoredKey('http://localhost:8000').key, 'vlf_devserver_k1');
});

test('npm-8: when the key cannot be stored, the approved key is revoked instead of left orphaned', async () => {
  // The config dir path is a regular file, so nothing can be written there.
  const blocked = path.join(tmp, 'blocked');
  fs.writeFileSync(blocked, '');
  process.env.XDG_CONFIG_HOME = blocked;
  process.env.APPDATA = blocked;
  const calls = [];
  const flow = deviceFlow('vlf_orphan_key_01');
  mockFetch(async (url, init) => {
    calls.push(`${init.method} ${new URL(url).pathname} ${init.headers.Authorization || ''}`.trim());
    if (url.endsWith('/api/v1/keys')) return jsonResponse(200, [{ id: 9, key_prefix: 'vlf_orphan_k', is_active: true }]);
    if (url.endsWith('/api/v1/keys/9')) return jsonResponse(204, {});
    return flow(url, init);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.match(r.err, /could not be stored/);
  assert.match(r.err, /new key was revoked/);
  // At the key cap the approval page may have replaced the previous key, so
  // the CLI must not promise the old login still works.
  assert.ok(!/unchanged/.test(r.err), r.err);
  assert.match(r.err, /If you chose a key to replace on the approval page, it is already revoked\. .*run `veilfile login` again/);
  assert.ok(calls.includes('DELETE /api/v1/keys/9 Bearer vlf_orphan_key_01'), calls.join('\n'));
});

test('npm-8: a previously stored key that is also VEILFILE_API_KEY is revoked on re-login, with a warning', async () => {
  // Owner decision 3, same as the Python CLI: the CLI's own previous key is
  // revoked; the exported copy is called out because it stops working.
  cli.writeStoredKey('vlf_exported_key1');
  // As exported after `login --print-key`, with stray whitespace.
  process.env.VEILFILE_API_KEY = ' vlf_exported_key1\n';
  const calls = [];
  const flow = deviceFlow('vlf_newkey_777777');
  mockFetch(async (url, init) => {
    calls.push(`${init.method} ${new URL(url).pathname} ${init.headers.Authorization || ''}`.trim());
    if (url.endsWith('/api/v1/keys')) return jsonResponse(200, [{ id: 12, key_prefix: 'vlf_exported', is_active: true }]);
    if (url.endsWith('/api/v1/keys/12')) return jsonResponse(204, {});
    return flow(url, init);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.ok(calls.includes('DELETE /api/v1/keys/12 Bearer vlf_exported_key1'), calls.join('\n'));
  assert.match(r.out, /previously stored key was revoked/);
  assert.match(r.err, /Warning: VEILFILE_API_KEY is set in this shell to the previously stored key, which no longer works\. Unset it/);
  assert.ok(!/takes precedence/.test(r.out), 'no contradictory note');
  assert.equal(t.readFileKey().key, 'vlf_newkey_777777');

  // A revocation that fails leaves the exported key working: no warning.
  process.env.VEILFILE_API_KEY = 'vlf_newkey_777777';
  const flow2 = deviceFlow('vlf_newkey_888888');
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/keys')) return jsonResponse(500, { error: 'boom' });
    return flow2(url, init);
  });
  const r2 = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r2.code, 0, r2.err);
  assert.match(r2.err, /could not be revoked/);
  assert.ok(!/no longer works/.test(r2.err), r2.err);
  assert.match(r2.out, /VEILFILE_API_KEY is set in this shell and takes precedence/);
});

test('npm-8: a 403 while revoking the replaced key is a warning, not "already invalid"', async () => {
  cli.writeStoredKey('vlf_blocked_key01');
  const flow = deviceFlow('vlf_newkey_403403');
  mockFetch(async (url, init) => {
    // A WAF or proxy block page in front of the API.
    if (url.endsWith('/api/v1/keys')) return rawResponse(403, '<html>Access denied</html>', { 'content-type': 'text/html' });
    return flow(url, init);
  });
  const r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.match(r.err, /previously stored key \(vlf_blocked_…\) could not be revoked: .*HTTP 403.* Revoke it in the dashboard/);
  assert.equal(t.readFileKey().key, 'vlf_newkey_403403');
});

/** Calls the SIGINT handler a login registered (not those already present). */
function sigintFrom(before) {
  return () => process.listeners('SIGINT').filter((l) => !before.includes(l))[0]();
}

test('G2/npm-8: Ctrl-C after the key is stored still revokes the replaced key before exiting', async () => {
  cli.writeStoredKey('vlf_oldkey_abcdef');
  const before = process.listeners('SIGINT');
  const sigint = sigintFrom(before);
  const exits = [];
  const origExit = process.exit;
  process.exit = (c) => { exits.push(c); throw new Error('exit'); };
  const calls = [];
  const flow = deviceFlow('vlf_newkey_sigint');
  mockFetch(async (url, init) => {
    calls.push(`${init.method} ${new URL(url).pathname}`);
    if (url.endsWith('/api/v1/usage')) {
      sigint();
      assert.ok(init.signal.aborted, 'the account lookup is dropped');
      throw init.signal.reason;
    }
    if (url.endsWith('/api/v1/keys')) return jsonResponse(200, [{ id: 40, key_prefix: 'vlf_oldkey_a', is_active: true }]);
    if (url.endsWith('/api/v1/keys/40')) return jsonResponse(204, {});
    return flow(url, init);
  });
  let r;
  try {
    r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  } finally {
    process.exit = origExit;
  }
  assert.deepEqual(exits, [], 'did not exit before revoking');
  assert.equal(r.code, 130);
  assert.ok(calls.includes('DELETE /api/v1/keys/40'), calls.join('\n'));
  assert.match(r.err, /Interrupted\. The new key is already stored in .*Revoking the previously stored key first/);
  assert.match(r.err, /previously stored key was revoked/);
  assert.ok(!/Logged in/.test(r.out));
  assert.equal(cli.readStoredKey().key, 'vlf_newkey_sigint');
  assert.deepEqual(process.listeners('SIGINT'), before);
});

test('G2/npm-8: a second Ctrl-C skips the revocation and names the key to revoke by hand', async () => {
  cli.writeStoredKey('vlf_oldkey_abcdef');
  const before = process.listeners('SIGINT');
  const sigint = sigintFrom(before);
  const exits = [];
  const origExit = process.exit;
  process.exit = (c) => { exits.push(c); throw new Error('exit'); };
  const flow = deviceFlow('vlf_newkey_twice1');
  mockFetch(async (url, init) => {
    if (url.endsWith('/api/v1/usage')) {
      sigint();
      throw init.signal.reason;
    }
    if (url.endsWith('/api/v1/keys')) sigint();
    return flow(url, init);
  });
  let r;
  try {
    r = await capture(() => cli.cmdLogin({ baseUrl: 'https://veilfile.com' }));
  } finally {
    process.exit = origExit;
  }
  assert.deepEqual(exits, [130]);
  assert.match(r.err, /previously stored key \(vlf_oldkey_a…\) was not revoked\. Revoke it in the dashboard/);
  assert.equal(cli.readStoredKey().key, 'vlf_newkey_twice1');
  assert.deepEqual(process.listeners('SIGINT'), before);
});

// ---------------------------------------------------------------------------
// Credentials: host binding and file writes (npm-5, F2, npm-11)
// ---------------------------------------------------------------------------

test('npm-5: a stored key is only sent to the server that issued it', async () => {
  cli.writeStoredKey('vlf_PRODUCTIONkey', 'https://veilfile.com');
  const seen = [];
  mockFetch(async (url, init) => {
    seen.push(`${url} ${init.headers && init.headers.Authorization}`);
    return jsonResponse(200, { plan: 'Free', email: 'x@y.z' });
  });
  for (const baseUrl of ['http://localhost:8000', 'https://attacker.example', 'https://veilfile.com.evil.example']) {
    let r = await capture(() => cli.cmdStatus({ baseUrl }));
    assert.equal(r.code, 1);
    assert.match(r.err, /Not logged in/);
    const f = path.join(tmp, 'p.json');
    fs.writeFileSync(f, '{}');
    r = await capture(() => cli.cmdPush({ baseUrl, ttlDays: null }, f));
    assert.equal(r.code, 1);
  }
  assert.ok(!seen.some((s) => s.includes('vlf_PRODUCTIONkey')), seen.join('\n'));

  const r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com/' }));
  assert.equal(r.code, 0, r.err);
  assert.deepEqual(seen, ['https://veilfile.com/api/v1/usage Bearer vlf_PRODUCTIONkey']);
});

test('npm-5: credentials written by older clients still work for the default server only', async () => {
  fs.mkdirSync(path.join(home, 'veilfile'), { recursive: true });
  fs.writeFileSync(path.join(home, 'veilfile', 'credentials'), JSON.stringify({ api_key: 'vlf_legacy_key01' }));
  assert.equal(cli.readStoredKey('https://veilfile.com').key, 'vlf_legacy_key01');
  assert.equal(cli.readStoredKey('http://localhost:8000').key, null);
  // The default file keeps the field older clients read.
  cli.writeStoredKey('vlf_new_default1', 'https://veilfile.com');
  const data = JSON.parse(fs.readFileSync(path.join(home, 'veilfile', 'credentials'), 'utf-8'));
  assert.deepEqual(data, { api_key: 'vlf_new_default1', base_url: 'https://veilfile.com' });
});

test('npm-5: VEILFILE_API_KEY is only sent to the VEILFILE_BASE_URL server', async () => {
  process.env.VEILFILE_API_KEY = 'vlf_env_shared_k';
  const seen = [];
  mockFetch(async (url, init) => {
    seen.push(`${url} ${init.headers.Authorization}`);
    return jsonResponse(200, { plan: 'Free', email: 'x@y.z' });
  });
  let r = await capture(() => cli.cmdStatus({ baseUrl: 'https://attacker.example' }));
  assert.equal(r.code, 1);
  assert.match(r.err, /VEILFILE_API_KEY is only sent to https:\/\/veilfile\.com/);
  assert.deepEqual(seen, []);

  r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0);
  process.env.VEILFILE_BASE_URL = 'http://localhost:8000';
  r = await capture(() => cli.cmdStatus({ baseUrl: 'http://localhost:8000' }));
  assert.equal(r.code, 0);
  assert.deepEqual(seen, [
    'https://veilfile.com/api/v1/usage Bearer vlf_env_shared_k',
    'http://localhost:8000/api/v1/usage Bearer vlf_env_shared_k',
  ]);
});

test('npm-5: keys never go over plain http to a remote host without --allow-http', async () => {
  cli.writeStoredKey('vlf_staging_key01', 'http://staging.example');
  let fetched = 0;
  mockFetch(async () => { fetched += 1; return jsonResponse(200, { plan: 'Free', email: 'x@y.z' }); });
  let r = await capture(() => cli.cmdStatus({ baseUrl: 'http://staging.example' }));
  assert.equal(r.code, 1);
  assert.match(r.err, /Refusing to exchange an API key with http:\/\/staging\.example over plain http/);
  r = await capture(() => cli.cmdLogin({ baseUrl: 'http://staging.example' }));
  assert.equal(r.code, 1);
  assert.equal(fetched, 0);

  r = await capture(() => cli.cmdStatus({ baseUrl: 'http://staging.example', allowHttp: true }));
  assert.equal(r.code, 0);
  assert.match(r.err, /Warning: exchanging an API key with .* over plain http/);
  assert.equal(fetched, 1);

  // Loopback http (a local dev server) needs no flag.
  cli.writeStoredKey('vlf_local_key_001', 'http://127.0.0.1:8000');
  r = await capture(() => cli.cmdStatus({ baseUrl: 'http://127.0.0.1:8000' }));
  assert.equal(r.code, 0);
  assert.equal(r.err, '');
});

test('npm-5: 0.0.0.0, IPv4-mapped 127.x and a trailing dot count as this machine; look-alikes do not', async () => {
  const loopback = (u) => t.isLoopbackHost(new URL(u).hostname);
  for (const u of [
    'http://localhost:8000', 'http://LOCALHOST.:8000', 'http://dev.localhost/', 'http://127.0.0.1/',
    'http://127.1.2.3./', 'http://0x7f.1/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]:8000',
    'http://0.0.0.0:8000', 'http://0/',
  ]) {
    assert.equal(loopback(u), true, u);
  }
  for (const u of [
    'http://10.0.0.1/', 'http://192.168.1.2/', 'http://[::ffff:8.8.8.8]/', 'http://[::ffff:10.0.0.1]/',
    'http://localhost.evil.example/', 'http://127.0.0.1.nip.io/', 'http://evil.example./',
    'http://0.0.0.1/', 'http://[fe80::1]/', 'http://[::]/',
  ]) {
    assert.equal(loopback(u), false, u);
  }
  // `runserver 0.0.0.0:8000` needs no --allow-http.
  cli.writeStoredKey('vlf_zero_key_0001', 'http://0.0.0.0:8000');
  let fetched = 0;
  mockFetch(async () => { fetched += 1; return jsonResponse(200, { plan: 'Free', email: 'x@y.z' }); });
  const r = await capture(() => cli.cmdStatus({ baseUrl: 'http://0.0.0.0:8000' }));
  assert.equal(r.code, 0, r.err);
  assert.equal(r.err, '');
  assert.equal(fetched, 1);
});

test('F2: credentials are written 0600 via a temp file even over a looser existing file', { skip: !POSIX }, () => {
  const dir = path.join(home, 'veilfile');
  fs.mkdirSync(dir, { mode: 0o755 });
  fs.chmodSync(dir, 0o755);
  const p = path.join(dir, 'credentials');
  fs.writeFileSync(p, JSON.stringify({ api_key: 'vlf_old' }), { mode: 0o644 });
  fs.chmodSync(p, 0o644);
  const opens = [];
  const realOpen = fs.openSync;
  fs.openSync = (file, flags, mode) => {
    opens.push({ file: String(file), flags, mode });
    return realOpen(file, flags, mode);
  };
  try {
    assert.equal(cli.writeStoredKey('vlf_new_key_0001'), p);
  } finally {
    fs.openSync = realOpen;
  }
  assert.ok(!opens.some((o) => o.file === p), 'the live file is never opened for writing');
  assert.ok(opens.some((o) => o.flags === 'wx' && o.mode === 0o600));
  assert.equal(fs.statSync(p).mode & 0o777, 0o600);
  assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
  assert.equal(cli.readStoredKey().key, 'vlf_new_key_0001');
  assert.deepEqual(fs.readdirSync(dir), ['credentials']);
});

test('F2: a failed credentials write leaves the previous key intact', () => {
  cli.writeStoredKey('vlf_keep_me_0001');
  const realWrite = fs.writeSync;
  fs.writeSync = () => { throw Object.assign(new Error('ENOSPC: no space left on device'), { code: 'ENOSPC' }); };
  try {
    assert.throws(() => cli.writeStoredKey('vlf_lost_0000001'), /ENOSPC/);
  } finally {
    fs.writeSync = realWrite;
  }
  assert.equal(cli.readStoredKey().key, 'vlf_keep_me_0001');
  assert.deepEqual(fs.readdirSync(path.join(home, 'veilfile')), ['credentials']);
});

test('npm-11: tests keep every platform\'s config dir inside the temp dir', () => {
  const realPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  try {
    for (const platform of ['win32', 'darwin', 'linux']) {
      Object.defineProperty(process, 'platform', { value: platform, configurable: true });
      assert.ok(cli.configDir().startsWith(home), `${platform}: ${cli.configDir()}`);
    }
  } finally {
    Object.defineProperty(process, 'platform', realPlatform);
  }
});

// ---------------------------------------------------------------------------
// logout (npm-4)
// ---------------------------------------------------------------------------

test('npm-4: logout with VEILFILE_API_KEY set revokes only the stored key and never sends the env key', async () => {
  cli.writeStoredKey('vlf_terminal_key0');
  process.env.VEILFILE_API_KEY = 'vlf_shared_mcp_key';
  const seen = [];
  mockFetch(async (url, init) => {
    seen.push(`${init.method} ${new URL(url).pathname} ${init.headers.Authorization}`);
    if (url.endsWith('/api/v1/keys')) {
      return jsonResponse(200, [
        { id: 3, key_prefix: 'vlf_shared_m', is_active: true },
        { id: 4, key_prefix: 'vlf_terminal', is_active: true },
      ]);
    }
    if (url.endsWith('/api/v1/keys/4')) return jsonResponse(204, {});
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  const r = await capture(() => cli.cmdLogout({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.deepEqual(seen, [
    'GET /api/v1/keys Bearer vlf_terminal_key0',
    'DELETE /api/v1/keys/4 Bearer vlf_terminal_key0',
  ]);
  assert.equal(cli.__test.readFileKey().key, null, 'stored copy removed');
  assert.match(r.out, /logout only revokes the key stored by `veilfile login`; VEILFILE_API_KEY is left alone/);
  assert.match(r.out, /Logged out\./);
});

test('npm-4: logout with only VEILFILE_API_KEY set makes no server call', async () => {
  process.env.VEILFILE_API_KEY = 'vlf_shared_mcp_key';
  let fetched = 0;
  mockFetch(async () => { fetched += 1; return jsonResponse(204, {}); });
  const r = await capture(() => cli.cmdLogout({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0);
  assert.equal(fetched, 0);
  assert.match(r.out, /No key stored by `veilfile login`/);
});

test('logout: a network failure while revoking keeps the local copy', async () => {
  cli.writeStoredKey('vlf_somekey_0000');
  mockFetch(async (url, init) => {
    if (init.method === 'GET') return jsonResponse(200, [{ id: 1, key_prefix: 'vlf_somekey_', is_active: true }]);
    throw new TypeError('fetch failed');
  });
  const r = await capture(() => cli.cmdLogout({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.match(r.err, /local copy was left in place/);
  assert.equal(cli.readStoredKey().key, 'vlf_somekey_0000');
});

test('npm-4: logout never revokes a stored key that is also VEILFILE_API_KEY', async () => {
  cli.writeStoredKey('vlf_same_key_0001');
  process.env.VEILFILE_API_KEY = 'vlf_same_key_0001 ';
  let fetched = 0;
  mockFetch(async () => {
    fetched += 1;
    return jsonResponse(200, [{ id: 8, key_prefix: 'vlf_same_key', is_active: true }]);
  });
  const r = await capture(() => cli.cmdLogout({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 0, r.err);
  assert.equal(fetched, 0, 'no revocation request');
  assert.equal(t.readFileKey().key, null, 'stored copy removed');
  assert.match(r.out, /also the value of VEILFILE_API_KEY, so logout did not revoke it/);
  assert.ok(!/is left alone/.test(r.out), 'no contradictory note');
  assert.ok(!/Logged out\./.test(r.out));
});

test('logout: a 403 (block page, session_required) keeps the local copy', async () => {
  cli.writeStoredKey('vlf_blocked_key01');
  for (const response of [
    () => rawResponse(403, '<html>Access denied</html>', { 'content-type': 'text/html' }),
    () => jsonResponse(403, { error: 'Use the dashboard to manage keys.', code: 'session_required' }),
  ]) {
    mockFetch(async () => response());
    const r = await capture(() => cli.cmdLogout({ baseUrl: 'https://veilfile.com' }));
    assert.equal(r.code, 1);
    assert.match(r.err, /HTTP 403/);
    assert.match(r.err, /local copy was left in place/);
    assert.ok(!/already invalid|Logged out/.test(r.out), r.out);
    assert.equal(cli.readStoredKey().key, 'vlf_blocked_key01');
  }
});

// ---------------------------------------------------------------------------
// push (npm-6, ctr-11, npm-12, npm-13)
// ---------------------------------------------------------------------------

test('npm-6/ctr-11: push sends the MIME type for the file extension', async () => {
  cli.writeStoredKey('vlf_pushkey_0000');
  const cases = {
    'shot.png': 'image/png',
    'photo.JPG': 'image/jpeg',
    'anim.gif': 'image/gif',
    'pic.webp': 'image/webp',
    'doc.pdf': 'application/pdf',
    'notes.txt': 'text/plain',
    'build.log': 'text/plain',
    'report.md': 'text/markdown',
    'data.json': 'application/json',
    'trace.har': 'application/json',
    'blob.bin': 'application/octet-stream',
  };
  for (const [name, type] of Object.entries(cases)) {
    const f = path.join(tmp, name);
    fs.writeFileSync(f, 'x');
    let part;
    let wire;
    mockFetch(async (url, init) => {
      part = init.body.get('file');
      wire = await new Response(init.body).text();
      assert.ok(init.signal, 'upload has a timeout signal');
      return jsonResponse(201, { url: 'https://veilfile.com/a/T' });
    });
    const r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
    assert.equal(r.code, 0, r.err);
    assert.equal(part.type, type, name);
    assert.equal(part.name, name);
    assert.ok(wire.includes(`Content-Type: ${type}`), `${name} multipart part`);
  }
});

test('npm-13: push shows the server\'s reason, Retry-After, and TTL adjustments', async () => {
  cli.writeStoredKey('vlf_pushkey_0000');
  const f = path.join(tmp, 'data.csv');
  fs.writeFileSync(f, 'a,b');
  mockFetch(async () => jsonResponse(400, {
    error: 'File type is not allowed.',
    code: 'file_type_not_allowed',
    detail: "extension '.csv' is not allowed; allowed: gif, har, jpeg",
  }));
  let r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
  assert.equal(r.code, 1);
  assert.match(r.err, /File type is not allowed\. \(extension '\.csv' is not allowed; allowed: gif, har, jpeg\)/);

  mockFetch(async () => jsonResponse(429, { error: 'Request was throttled.', code: 'rate_limited' }, { 'retry-after': '30' }));
  r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
  assert.equal(r.code, 1);
  assert.match(r.err, /HTTP 429\): Request was throttled\. Retry after 30s\./);

  mockFetch(async () => jsonResponse(429, { error: 'Monthly upload cap for your plan has been reached.', code: 'monthly_cap_reached' }));
  r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
  assert.match(r.err, /Monthly upload cap/);

  mockFetch(async (url, init) => {
    assert.equal(init.body.get('ttl_days'), '400');
    return jsonResponse(201, { url: 'https://veilfile.com/a/T', expires_at: '2026-10-14T00:00:00Z', ttl_adjusted: true });
  });
  r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: '400' }, f));
  assert.equal(r.code, 0);
  assert.equal(r.out.split('\n')[0], 'https://veilfile.com/a/T');
  assert.match(r.out, /adjusted the expiry/);
});

test('npm-12: errors go to stderr with a non-zero exit; status rejects non-usage 200s', async () => {
  const f = path.join(tmp, 'report.md');
  fs.writeFileSync(f, '# hi');
  let r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
  assert.equal(r.code, 1);
  assert.equal(r.out, '', 'nothing on stdout that could be mistaken for a URL');
  assert.match(r.err, /Not logged in/);

  cli.writeStoredKey('vlf_statuskey_000');
  mockFetch(async () => rawResponse(200, '<html>Hotel Wi-Fi login</html>', { 'content-type': 'text/html' }));
  r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.match(r.err, /not a JSON response/);

  mockFetch(async () => jsonResponse(200, { hello: 'world' }));
  r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);

  mockFetch(async () => jsonResponse(201, { id: 'x' }));
  r = await capture(() => cli.cmdPush({ baseUrl: 'https://veilfile.com', ttlDays: null }, f));
  assert.equal(r.code, 1);
  assert.equal(r.out, '');
});

test('npm-12: status failures go to stderr, not stdout', async () => {
  let r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.equal(r.out, '');
  assert.match(r.err, /Not logged in/);

  cli.writeStoredKey('vlf_revoked_key01');
  mockFetch(async () => jsonResponse(401, { error: 'Invalid API key.', code: 'unauthorized' }));
  r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.equal(r.out, '');
  assert.match(r.err, /stored key is invalid or revoked/);

  process.env.VEILFILE_API_KEY = 'vlf_env_revoked01';
  r = await capture(() => cli.cmdStatus({ baseUrl: 'https://veilfile.com' }));
  assert.equal(r.code, 1);
  assert.equal(r.out, '');
  assert.match(r.err, /VEILFILE_API_KEY key is invalid or revoked/);
});

// ---------------------------------------------------------------------------
// Timeouts (npm-10)
// ---------------------------------------------------------------------------

test('npm-10: a server that accepts but never answers times out', async () => {
  const sockets = [];
  const server = net.createServer((s) => sockets.push(s));
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/api/v1/usage`;
  try {
    const started = Date.now();
    await assert.rejects(
      cli.request('GET', url, { timeoutMs: 300 }),
      (err) => err instanceof t.ApiError && /Timed out/.test(err.message)
    );
    assert.ok(Date.now() - started < 5000);
  } finally {
    for (const s of sockets) s.destroy();
    server.close();
  }
});

test('npm-10: every API request carries an abort signal', async () => {
  const signals = [];
  mockFetch(async (url, init) => { signals.push(init.signal); return jsonResponse(200, {}); });
  await cli.request('GET', 'https://veilfile.com/api/v1/usage');
  assert.ok(signals[0] instanceof AbortSignal);
});

// ---------------------------------------------------------------------------
// Arguments (G4, S2)
// ---------------------------------------------------------------------------

test('G4/S2: global options work in any position', () => {
  let opts = cli.parseArgs(['push', 'f.png', '--base-url', 'http://localhost:8000', '--ttl-days', '3']);
  assert.equal(opts.baseUrl, 'http://localhost:8000');
  assert.deepEqual(opts.rest, ['f.png']);
  assert.equal(opts.ttlDays, '3');
  opts = cli.parseArgs(['login', '--base-url=http://127.0.0.1:9000/', '--print-key']);
  assert.equal(opts.baseUrl, 'http://127.0.0.1:9000');
  assert.equal(opts.printKey, true);
  opts = cli.parseArgs(['pull', 'https://veilfile.com/a/T', '-o', 'x.txt', '--force', '--allow-http']);
  assert.equal(opts.out, 'x.txt');
  assert.equal(opts.force, true);
  assert.equal(opts.allowHttp, true);
});

test('G4/S2: unknown options, missing values and bad --ttl-days are usage errors', () => {
  const bad = [
    ['push', 'f.png', '--ttl-days'],
    ['push', 'f.png', '--ttl-days', '1d'],
    ['push', 'f.png', '--ttl-days', '0'],
    ['push', 'f.png', '--ttl-days', '-5'],
    ['push', 'f.png', '--ttl', '1'],
    ['status', '--print-key'],
    ['login', '--out', 'x'],
    ['push'],
    ['push', 'a', 'b'],
    ['pull', 'u', '--out='],
    ['bogus'],
    ['--base-url', 'ftp://veilfile.com', 'status'],
    ['--base-url', 'https://veilfile.com/?x=1', 'status'],
    ['status', '--base-url'],
  ];
  for (const argv of bad) {
    assert.throws(() => cli.parseArgs(argv), t.UsageError, argv.join(' '));
  }
});

test('G4/S2: the binary exits 2 with usage on stderr for bad arguments', async () => {
  const r = await runCli(['push', 'f.png', '--ttl-days']);
  assert.equal(r.code, 2);
  assert.match(r.stderr, /--ttl-days/);
  assert.match(r.stderr, /Usage: veilfile/);
  assert.equal(r.stdout, '');
  const v = await runCli(['status', '--version']);
  assert.equal(v.code, 0);
  assert.equal(v.stdout.trim(), `veilfile ${cli.VERSION}`);
});

test('npm-15: VERSION matches package.json', () => {
  assert.equal(cli.VERSION, require('../package.json').version);
});
