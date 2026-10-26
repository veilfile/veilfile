#!/usr/bin/env node
/**
 * veilfile — command-line client for Veilfile.
 *
 * Zero dependencies. Node 18.3+ only (global fetch, FormData, Blob,
 * util.parseArgs). Talks to the RFC 8628 device flow so `veilfile login`
 * never asks you to paste a secret into the terminal.
 *
 *   veilfile login        start the browser approval flow, store the API key
 *   veilfile logout       revoke the stored key server-side, delete the local copy
 *   veilfile status       confirm the key is valid (never prints it)
 *   veilfile push <file>  upload a file, print its share URL
 *   veilfile pull <url>   download a shared file (--out <path>, --force)
 *
 * Keys are stored per server in 0600 credentials files under the platform
 * config dir (~/.config/veilfile on macOS/Linux, %APPDATA%\veilfile on
 * Windows), and a stored key is only ever sent to the server that issued it.
 * VEILFILE_API_KEY, when set, takes precedence over the file for the
 * VEILFILE_BASE_URL server (default https://veilfile.com) and is not sent
 * to any other.
 */

'use strict';

const childProcess = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const util = require('node:util');

const VERSION = '0.4.1';
const DEFAULT_BASE_URL = 'https://veilfile.com';
const DEVICE_GRANT_TYPE = 'urn:ietf:params:oauth:grant-type:device_code';
const ENV_VAR = 'VEILFILE_API_KEY';
const BASE_URL_ENV_VAR = 'VEILFILE_BASE_URL';
const SLOW_DOWN_INCREMENT_SECS = 5;
const MAX_POLL_WAIT_SECS = 60;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
// pull refuses anything much larger than the biggest artifact the server stores.
const MAX_DOWNLOAD_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024;
const REQUEST_TIMEOUT_MS = 30 * 1000;
// pull gives up when no bytes arrive for this long.
const TRANSFER_IDLE_TIMEOUT_MS = 30 * 1000;
// ...and when the whole download takes longer than this (26 MiB at ~45 KB/s).
const TRANSFER_TOTAL_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_REDIRECTS = 5;
const EXIT_USAGE = 2;
const EXIT_CANCELLED = 130;

class ApiError extends Error {}
class UsageError extends Error {}

/** A login that ended without a key. The message is user-facing. */
class LoginError extends Error {
  constructor(message, exitCode = 1) {
    super(message);
    this.exitCode = exitCode;
  }
}

// ---------------------------------------------------------------------------
// Servers
// ---------------------------------------------------------------------------

/**
 * True for a host that only ever reaches this machine. Takes a WHATWG URL
 * hostname, which already has IPv4 shorthand normalized (0x7f.1 is
 * 127.0.0.1, [::ffff:127.0.0.1] is [::ffff:7f00:1]).
 */
function isLoopbackHost(hostname) {
  const host = String(hostname).toLowerCase().replace(/^\[(.*)\]$/, '$1').replace(/\.$/, '');
  return (
    host === 'localhost' ||
    host.endsWith('.localhost') ||
    host === '::1' ||
    /^127(\.\d{1,3}){3}$/.test(host) ||
    // IPv4-mapped 127.x.x.x.
    /^::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}$/.test(host) ||
    // `runserver 0.0.0.0:8000`: connecting to it stays on this machine.
    host === '0.0.0.0'
  );
}

/** Validate a base URL; returns it without trailing slashes. */
function normalizeBaseUrl(raw) {
  let url;
  try {
    url = new URL(String(raw));
  } catch {
    throw new UsageError(`Not a valid base URL: ${raw}`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new UsageError(`The base URL must start with https:// (or http://): ${raw}`);
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new UsageError(`The base URL must not contain credentials, a query or a fragment: ${raw}`);
  }
  return url.href.replace(/\/+$/, '');
}

function envBaseUrl() {
  return process.env[BASE_URL_ENV_VAR] || DEFAULT_BASE_URL;
}

function originOf(baseUrl) {
  return new URL(baseUrl).origin;
}

function safeOrigin(value) {
  try {
    return originOf(value);
  } catch {
    return null;
  }
}

/**
 * An API key travels in a header (or, for login, in the response): refuse
 * plain http to anything but this machine unless --allow-http was passed.
 * Returns false (after saying why) when the command must stop.
 */
function keyTransportAllowed(opts) {
  const url = new URL(opts.baseUrl);
  if (url.protocol !== 'http:' || isLoopbackHost(url.hostname)) return true;
  if (!opts.allowHttp) {
    process.stderr.write(
      `Refusing to exchange an API key with ${url.origin} over plain http. ` +
      'Use https, or pass --allow-http to accept that risk.\n'
    );
    return false;
  }
  process.stderr.write(`Warning: exchanging an API key with ${url.origin} over plain http (--allow-http).\n`);
  return true;
}

// ---------------------------------------------------------------------------
// Key storage
// ---------------------------------------------------------------------------

function configDir() {
  if (process.platform === 'win32') {
    const base = process.env.APPDATA || os.homedir();
    return path.join(base, 'veilfile');
  }
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(base, 'veilfile');
}

/**
 * One credentials file per server: `credentials` for https://veilfile.com
 * (the file both CLIs have always used), `credentials-<origin>` otherwise,
 * so logging in to a dev server never replaces the production key.
 */
function credentialsPath(baseUrl = envBaseUrl()) {
  const origin = originOf(baseUrl);
  if (origin === originOf(DEFAULT_BASE_URL)) return path.join(configDir(), 'credentials');
  return path.join(configDir(), `credentials-${origin.replace(/[^A-Za-z0-9.-]+/g, '_')}`);
}

/** The key `veilfile login` stored for baseUrl's server: { key, path }. */
function readFileKey(baseUrl = envBaseUrl()) {
  const p = credentialsPath(baseUrl);
  try {
    const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
    const key = String((data && data.api_key) || '').trim();
    // Files written before keys were bound to a server carry no base_url;
    // they can only belong to the default server.
    const stored = data && data.base_url ? safeOrigin(data.base_url) : originOf(DEFAULT_BASE_URL);
    if (key && stored === originOf(baseUrl)) return { key, path: p };
  } catch {
    // missing or unreadable: not logged in
  }
  return { key: null, path: p };
}

/**
 * The key to send to baseUrl. Returns { key, source, path, note } where
 * source is 'environment', 'file', or 'none'. VEILFILE_API_KEY wins for the
 * server it is meant for (VEILFILE_BASE_URL, default https://veilfile.com)
 * and is never sent to another; `note` says when it was skipped.
 */
function readStoredKey(baseUrl = envBaseUrl()) {
  const envKey = (process.env[ENV_VAR] || '').trim();
  let note = '';
  if (envKey) {
    const envOrigin = safeOrigin(envBaseUrl());
    if (envOrigin === originOf(baseUrl)) {
      return { key: envKey, source: 'environment', path: null, note };
    }
    note = envOrigin
      ? `${ENV_VAR} is only sent to ${envOrigin}; it was not used for ${originOf(baseUrl)}.`
      : `${ENV_VAR} was not used: ${BASE_URL_ENV_VAR} is not a valid URL.`;
  }
  const { key, path: p } = readFileKey(baseUrl);
  if (key) return { key, source: 'file', path: p, note };
  return { key: null, source: 'none', path: p, note };
}

/**
 * Store the key for baseUrl's server with 0600 permissions. The JSON goes to
 * a fresh temp file created 0600 and is renamed into place, so the key is
 * never briefly readable by others (even when the old file had looser
 * permissions) and a failed write leaves the previous credentials intact.
 */
function writeStoredKey(key, baseUrl = envBaseUrl()) {
  const dir = configDir();
  fs.mkdirSync(dir, { mode: 0o700, recursive: true });
  // mkdirSync's mode only applies when it creates the directory.
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort on Windows */ }
  const p = credentialsPath(baseUrl);
  const tmp = path.join(dir, `.credentials-${crypto.randomBytes(6).toString('hex')}.tmp`);
  const fd = fs.openSync(tmp, 'wx', 0o600);
  try {
    try {
      fs.writeSync(fd, JSON.stringify({ api_key: key, base_url: originOf(baseUrl) }));
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, p);
  } catch (err) {
    try { fs.unlinkSync(tmp); } catch { /* already gone */ }
    throw err;
  }
  return p;
}

/** Delete baseUrl's credentials file. Returns true if one existed. */
function deleteStoredKey(baseUrl = envBaseUrl()) {
  try {
    fs.unlinkSync(credentialsPath(baseUrl));
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

/** Abort after timeoutMs, or earlier when `signal` (Ctrl-C) aborts. */
function requestSignal(timeoutMs, signal) {
  const timeout = AbortSignal.timeout(timeoutMs);
  if (!signal) return timeout;
  if (typeof AbortSignal.any === 'function') return AbortSignal.any([timeout, signal]);
  const controller = new AbortController();
  for (const s of [timeout, signal]) {
    if (s.aborted) controller.abort(s.reason);
    else s.addEventListener('abort', () => controller.abort(s.reason), { once: true });
  }
  return controller.signal;
}

function transportError(err, url, timeoutMs) {
  if (err && err.name === 'TimeoutError') {
    return new ApiError(`Timed out after ${Math.round(timeoutMs / 1000)}s waiting for ${url}.`);
  }
  const cause = err && err.cause;
  const reason = (cause && (cause.code || cause.message)) || (err && err.message) || String(err);
  return new ApiError(`Could not reach ${url}: ${reason}`);
}

/**
 * One JSON request. Returns { status, body, isJson, headers }; body is the
 * parsed object or array, or {} when the response was not JSON. Throws
 * ApiError on network failure or timeout. HTTP error statuses are returned,
 * not thrown.
 */
async function request(method, url, { key = null, body = undefined, timeoutMs = REQUEST_TIMEOUT_MS, signal = null } = {}) {
  const headers = { Accept: 'application/json' };
  if (key) headers['Authorization'] = `Bearer ${key}`;
  // The API never redirects; following one could carry the key elsewhere.
  const init = { method, headers, redirect: 'manual', signal: requestSignal(timeoutMs, signal) };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let response;
  let raw;
  try {
    response = await fetch(url, init);
    raw = await response.text();
  } catch (err) {
    if (signal && signal.aborted) throw err;
    throw transportError(err, url, timeoutMs);
  }
  let parsed = null;
  if (raw.trim()) {
    try { parsed = JSON.parse(raw); } catch { parsed = null; }
  }
  const isJson = parsed !== null && typeof parsed === 'object';
  return {
    status: response.status,
    body: isJson ? parsed : {},
    isJson,
    headers: response.headers || new Headers(),
  };
}

function api(baseUrl, method, pathStr, opts = {}) {
  return request(method, baseUrl.replace(/\/+$/, '') + pathStr, opts);
}

// Control, zero-width, bidi-override and line-separator characters: never
// echoed to the terminal or used in a filename.
const UNSAFE_CHARS = /[\u0000-\u001f\u007f-\u009f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]/g;

/** Server-supplied text with control and bidi characters removed. */
function printable(text, max = 300) {
  const clean = String(text)
    .replace(UNSAFE_CHARS, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

/** ": <error> (<detail>)" from an API error body, or ''. */
function serverDetail(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return '';
  const error = typeof body.error === 'string' ? printable(body.error) : '';
  const detail = typeof body.detail === 'string' ? printable(body.detail) : '';
  if (error && detail) return `: ${error} (${detail})`;
  return error || detail ? `: ${error || detail}` : '';
}

/** Seconds from a Retry-After header (delta-seconds or HTTP-date), or null. */
function retryAfterSecs(headers) {
  const value = headers && typeof headers.get === 'function' ? headers.get('retry-after') : null;
  if (!value) return null;
  if (/^\s*\d+\s*$/.test(value)) return Number(value);
  const when = Date.parse(value);
  return Number.isNaN(when) ? null : Math.max(0, Math.ceil((when - Date.now()) / 1000));
}

function formatBytes(num) {
  let value = Number(num);
  if (!Number.isFinite(value)) return '–';
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (value < 1024 || unit === 'TB') return `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(1)} TB`;
}

const sleep = (ms, signal = null) => new Promise((resolve, reject) => {
  if (signal && signal.aborted) {
    reject(signal.reason);
    return;
  }
  const onAbort = () => {
    clearTimeout(timer);
    reject(signal.reason);
  };
  const timer = setTimeout(() => {
    if (signal) signal.removeEventListener('abort', onAbort);
    resolve();
  }, ms);
  if (signal) signal.addEventListener('abort', onAbort, { once: true });
});

/**
 * The verification page to open, or null when it should only be printed:
 * it must be https (http only on this machine) and on the base URL's origin,
 * so a hostile server cannot make the CLI launch anything else.
 */
function browserUrl(raw, baseUrl) {
  let url;
  try {
    url = new URL(String(raw));
  } catch {
    return null;
  }
  const secure = url.protocol === 'https:' || (url.protocol === 'http:' && isLoopbackHost(url.hostname));
  if (!secure || url.username || url.password || url.origin !== originOf(baseUrl)) return null;
  return url.href;
}

/**
 * Hand url to the platform opener without a shell, so nothing in it is ever
 * parsed as a command line. Resolves true once the opener has started;
 * never waits for the browser.
 */
function openBrowser(url) {
  return new Promise((resolve) => {
    let command;
    let args;
    if (process.platform === 'darwin') {
      command = 'open';
      args = [url];
    } else if (process.platform === 'win32') {
      // Not `cmd /c start`: cmd.exe would treat & and ^ in the URL as syntax.
      command = 'rundll32';
      args = ['url.dll,FileProtocolHandler', url];
    } else {
      command = 'xdg-open';
      args = [url];
    }
    let child;
    try {
      child = childProcess.spawn(command, args, { stdio: 'ignore', detached: true, windowsHide: true });
    } catch {
      resolve(false);
      return;
    }
    child.once('error', () => resolve(false));
    child.once('spawn', () => {
      child.unref();
      resolve(true);
    });
  });
}

// Seams for tests: the browser launcher, the poll-loop sleep and pull's
// overall deadline.
const deps = { openBrowser, sleep, pullDeadlineMs: TRANSFER_TOTAL_TIMEOUT_MS };

/**
 * Revoke `key` on the server, authenticating as the key itself, so it works
 * whichever account the key belongs to. Resolves { ok: true, state } with
 * state 'revoked', 'invalid' (the server rejects the key with 401) or
 * 'missing' (no active key with that prefix), or { ok: false, message }.
 */
async function revokeOwnKey(baseUrl, key) {
  let listed;
  try {
    listed = await api(baseUrl, 'GET', '/api/v1/keys', { key });
  } catch (err) {
    return { ok: false, message: err.message };
  }
  // The server answers a bad or revoked key with 401. A 403 (a proxy or WAF
  // block page, session_required) says nothing about the key: report it.
  if (listed.status === 401) return { ok: true, state: 'invalid' };
  if (listed.status !== 200 || !Array.isArray(listed.body)) {
    return {
      ok: false,
      message: `Could not list keys on the server (HTTP ${listed.status})${serverDetail(listed.body)}.`,
    };
  }
  const match = listed.body.find(
    (entry) =>
      entry &&
      typeof entry === 'object' &&
      entry.key_prefix === key.slice(0, 12) &&
      entry.is_active
  );
  if (!match) return { ok: true, state: 'missing' };
  let deleted;
  try {
    deleted = await api(baseUrl, 'DELETE', `/api/v1/keys/${encodeURIComponent(match.id)}`, { key });
  } catch (err) {
    return { ok: false, message: err.message };
  }
  if ([200, 204, 401, 404].includes(deleted.status)) return { ok: true, state: 'revoked' };
  // Server rejected the request: say so plainly, not "could not reach".
  return {
    ok: false,
    message: `Could not revoke the key on the server (HTTP ${deleted.status})${serverDetail(deleted.body)}.`,
  };
}

// ---------------------------------------------------------------------------
// login
// ---------------------------------------------------------------------------

const EXPIRED_MESSAGE = 'The code expired before approval. Run `veilfile login` again.';
const CANCELLED_MESSAGE = 'Login cancelled. Nothing was stored.';
// The server mints the key on the poll that delivers it. When that answer is
// lost (timeout, reset, 5xx, Ctrl-C mid-request) the key may exist anyway.
const LOST_KEY_HINT =
  'A poll got no answer, so the server may have issued a key that never arrived: ' +
  'if a new "Terminal login" key shows in the dashboard, revoke it.';

/**
 * A seconds value from the device-code response: `fallback` when absent or
 * not a number >= min (RFC 8628: interval defaults to 5, and an explicit 0
 * is honoured), else capped at max.
 */
function secondsField(value, fallback, min, max) {
  if (value === undefined || value === null || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n) || n < min) return fallback;
  return Math.min(n, max);
}

/** Next wait after a failed poll: doubled, at least Retry-After, at most a minute. */
function backoff(currentSecs, intervalSecs, retryAfter) {
  const doubled = Math.max(intervalSecs, currentSecs * 2);
  return Math.min(MAX_POLL_WAIT_SECS, Math.max(doubled, retryAfter || 0));
}

/**
 * Poll the token endpoint until the grant resolves. Returns the API key;
 * throws LoginError on a terminal state. Server hiccups (network errors,
 * timeouts, 5xx, 429, answers that are not JSON) are retried with backoff
 * until the code expires. `signal` aborts the wait (Ctrl-C); aborting while
 * a poll is in flight throws a LoginError with EXIT_CANCELLED.
 */
async function pollForKey(baseUrl, deviceCode, intervalSecs, expiresInSecs, signal = null) {
  const deadline = Date.now() + expiresInSecs * 1000;
  let interval = Math.max(1, intervalSecs);
  let waitSecs = interval;
  let lastProblem = '';
  // A poll whose answer never arrived may have minted the key (the next
  // poll then gets invalid_grant, or expired_token once past expiry).
  let answerLost = false;
  const failure = (message) => new LoginError(answerLost ? `${message} ${LOST_KEY_HINT}` : message);
  // Say how long the next sleep really is (never past the code's expiry);
  // say nothing once the code has expired, the loop reports that.
  const retryNote = (problem) => {
    const secs = Math.min(waitSecs, Math.ceil((deadline - Date.now()) / 1000));
    if (secs > 0) process.stderr.write(`${problem} Retrying in ${secs}s…\n`);
  };
  for (;;) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
      throw failure(lastProblem ? `${EXPIRED_MESSAGE} (Last error: ${lastProblem})` : EXPIRED_MESSAGE);
    }
    try {
      await deps.sleep(Math.min(waitSecs * 1000, remainingMs), signal);
    } catch (err) {
      if (!(signal && signal.aborted)) throw err;
      // Ctrl-C between polls: an earlier lost answer may still have minted a key.
      throw new LoginError(answerLost ? `${CANCELLED_MESSAGE} ${LOST_KEY_HINT}` : CANCELLED_MESSAGE, EXIT_CANCELLED);
    }
    let result;
    try {
      result = await api(baseUrl, 'POST', '/api/v1/device/token', {
        body: { grant_type: DEVICE_GRANT_TYPE, device_code: deviceCode },
        timeoutMs: Math.max(5000, Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now())),
        signal,
      });
    } catch (err) {
      if (signal && signal.aborted) {
        throw new LoginError(`${CANCELLED_MESSAGE} ${LOST_KEY_HINT}`, EXIT_CANCELLED);
      }
      // Transient network failure mid-poll: back off and retry until the
      // grant expires rather than killing the whole login flow.
      answerLost = true;
      lastProblem = err.message;
      waitSecs = backoff(waitSecs, interval, null);
      retryNote(err.message);
      continue;
    }
    const { status, body, isJson, headers } = result;
    if (status === 200 && isJson && typeof body.api_key === 'string' && body.api_key) {
      return body.api_key;
    }
    if (status >= 500 || status === 429 || !isJson) {
      // A deploy, a proxy error page, throttling: the grant is still alive.
      if (status >= 500) answerLost = true;
      lastProblem = `HTTP ${status}${isJson ? '' : ', not a JSON response'}`;
      waitSecs = backoff(waitSecs, interval, retryAfterSecs(headers));
      retryNote(`The server answered ${lastProblem}.`);
      continue;
    }
    waitSecs = interval;
    const error = typeof body.error === 'string' ? body.error : '';
    const description = typeof body.error_description === 'string' ? printable(body.error_description) : '';
    if (error === 'authorization_pending') continue;
    if (error === 'slow_down') {
      interval = Math.min(interval + SLOW_DOWN_INCREMENT_SECS, MAX_POLL_WAIT_SECS);
      waitSecs = interval;
      continue;
    }
    if (error === 'expired_token') throw failure(EXPIRED_MESSAGE);
    if (error === 'access_denied') {
      // A denied grant never minted a key (a redeemed one stays consumed).
      throw new LoginError(
        `Login was denied${description ? `: ${description}` : ' in the browser.'} Nothing was stored.`
      );
    }
    throw failure(
      `Login failed (${error ? printable(error, 64) : `unexpected response, HTTP ${status}`})` +
      `${description ? `: ${description}` : '.'} Run \`veilfile login\` again.`
    );
  }
}

async function cmdLogin(opts) {
  if (!keyTransportAllowed(opts)) return 1;
  let result;
  try {
    result = await api(opts.baseUrl, 'POST', '/api/v1/device/code', { body: {} });
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    return 1;
  }
  const { status, body, isJson, headers } = result;
  if (status === 429) {
    const wait = retryAfterSecs(headers);
    process.stderr.write(
      'Too many login attempts from this address. ' +
      `${wait != null ? `Retry in ${wait}s.` : 'Wait a while and retry.'}\n`
    );
    return 1;
  }
  if (status !== 200 || !isJson || typeof body.device_code !== 'string' || !body.device_code) {
    process.stderr.write(`Could not start the login flow (HTTP ${status})${serverDetail(body)}. Try again.\n`);
    return 1;
  }

  const deviceCode = body.device_code;
  const userCode = body.user_code ? printable(body.user_code, 64) : '';
  const completeUri = String(body.verification_uri_complete || body.verification_uri || '');
  const interval = secondsField(body.interval, 5, 0, MAX_POLL_WAIT_SECS);
  const expiresIn = secondsField(body.expires_in, 600, 1, 3600);

  console.log('To log in, open this page and enter the code:\n');
  if (completeUri) console.log(`  ${printable(completeUri, 2048)}\n`);
  if (userCode) console.log(`  Code: ${userCode}\n`);
  if (completeUri) {
    const target = browserUrl(completeUri, opts.baseUrl);
    if (!target) {
      console.log(`(Not opened automatically: it is not an https page on ${originOf(opts.baseUrl)}.)\n`);
    } else if (await deps.openBrowser(target)) {
      console.log('(Opened in your browser.)\n');
    }
  }
  console.log('Waiting for approval in the browser… (Ctrl-C to cancel)');

  // Ctrl-C is handled only while this login runs, never for other commands.
  const controller = new AbortController();
  // The account lookup after storing, dropped on Ctrl-C.
  const lookup = new AbortController();
  let storedPath = null;
  // Prefix of the replaced key while it still has to be revoked.
  let pendingRevoke = null;
  let interrupted = false;
  const onSigint = () => {
    if (!storedPath) {
      controller.abort();
      return;
    }
    if (pendingRevoke && !interrupted) {
      // Exiting now would leave the replaced key active and stored nowhere:
      // skip the lookup but finish the revocation (bounded by its timeout).
      interrupted = true;
      lookup.abort();
      process.stderr.write(
        `\nInterrupted. The new key is already stored in ${storedPath}. ` +
        'Revoking the previously stored key first (Ctrl-C again to skip)…\n'
      );
      return;
    }
    if (interrupted) {
      process.stderr.write(`\nThe previously stored key (${pendingRevoke}) was not revoked. Revoke it in the dashboard.\n`);
    } else {
      process.stderr.write(`\nInterrupted. The new key is already stored in ${storedPath}.\n`);
    }
    process.exit(EXIT_CANCELLED);
  };
  process.on('SIGINT', onSigint);
  try {
    let apiKey;
    try {
      apiKey = await pollForKey(opts.baseUrl, deviceCode, interval, expiresIn, controller.signal);
    } catch (err) {
      if (err instanceof LoginError) {
        process.stderr.write(`${err.exitCode === EXIT_CANCELLED ? '\n' : ''}${err.message}\n`);
        return err.exitCode;
      }
      if (controller.signal.aborted) {
        process.stderr.write(`\n${CANCELLED_MESSAGE}\n`);
        return EXIT_CANCELLED;
      }
      throw err;
    }

    const previousKey = readFileKey(opts.baseUrl).key;
    try {
      storedPath = writeStoredKey(apiKey, opts.baseUrl);
    } catch (err) {
      process.stderr.write(`Login approved, but the key could not be stored: ${err.message}\n`);
      if (opts.printKey) {
        console.log(`\nAPI key: ${apiKey}`);
        process.stderr.write('The key above is active but was not saved. Keep it safe, or revoke it in the dashboard.\n');
        return 1;
      }
      // Nobody holds the new key, so it should not stay active.
      const revoked = await revokeOwnKey(opts.baseUrl, apiKey);
      process.stderr.write(
        revoked.ok
          ? 'The new key was revoked again.\n'
          : `The new key could not be revoked (${revoked.message}) Revoke the newest "Terminal login" key in the dashboard.\n`
      );
      // At the key cap the approval page has the user pick a key to replace,
      // often this CLI's own: the server revoked it when it issued the new
      // one, so the previous login may no longer work either.
      process.stderr.write(
        'If you chose a key to replace on the approval page, it is already revoked. ' +
        'Fix the problem above, then run `veilfile login` again.\n'
      );
      return 1;
    }

    // A re-login replaces this CLI's own key: revoke the one it held before
    // so it does not stay active unseen (and count toward the key cap), even
    // when it was also exported as VEILFILE_API_KEY (warned about below).
    const previousPrefix = previousKey ? `${previousKey.slice(0, 12)}…` : '';
    const envKey = (process.env[ENV_VAR] || '').trim();
    if (previousKey && previousKey !== apiKey) pendingRevoke = previousPrefix;

    // Confirm the key works and learn who it belongs to (never print the key).
    let account = '';
    try {
      const usage = await api(opts.baseUrl, 'GET', '/api/v1/usage', { key: apiKey, signal: lookup.signal });
      if (usage.status === 200 && usage.isJson && usage.body.email) {
        account = printable(usage.body.email, 254);
      }
    } catch {
      // non-fatal: login already succeeded
    }

    let replaced = '';
    // The exported copy stops working with the key it copies.
    let envStale = false;
    if (pendingRevoke) {
      const revoked = await revokeOwnKey(opts.baseUrl, previousKey);
      if (!revoked.ok) {
        process.stderr.write(
          `Warning: the previously stored key (${previousPrefix}) could not be revoked: ` +
          `${revoked.message} Revoke it in the dashboard.\n`
        );
      } else {
        if (revoked.state === 'revoked') replaced = 'The previously stored key was revoked.';
        // 'missing' means the key still authenticated, so it still works.
        envStale = previousKey === envKey && revoked.state !== 'missing';
      }
      pendingRevoke = null;
    }
    const envWarning = envStale
      ? `Warning: ${ENV_VAR} is set in this shell to the previously stored key, which no longer works. ` +
        'Unset it to use the new key, and update it wherever else you set it (shell profile, MCP config).\n'
      : '';
    if (interrupted) {
      if (replaced) process.stderr.write(`${replaced}\n`);
      if (envWarning) process.stderr.write(envWarning);
      return EXIT_CANCELLED;
    }

    if (opts.printKey) console.log(`\nAPI key: ${apiKey}`);
    console.log(`\nLogged in${account ? ` as ${account}` : ''}.`);
    console.log(`Key stored in ${storedPath} (not shown).`);
    if (replaced) console.log(replaced);
    if (envWarning) {
      process.stderr.write(envWarning);
    } else if (readStoredKey(opts.baseUrl).source === 'environment') {
      console.log(
        `Note: ${ENV_VAR} is set in this shell and takes precedence over the stored key.`
      );
    }
    return 0;
  } finally {
    process.removeListener('SIGINT', onSigint);
  }
}

// ---------------------------------------------------------------------------
// logout
// ---------------------------------------------------------------------------

async function cmdLogout(opts) {
  // logout only ever revokes the key `veilfile login` stored. An environment
  // key is usually shared (MCP config, CI), so it is left alone.
  const envKey = (process.env[ENV_VAR] || '').trim();
  const envSet = Boolean(envKey);
  const { key } = readFileKey(opts.baseUrl);
  if (key && key === envKey) {
    // The stored key was exported (say after `login --print-key`): revoking
    // it would also cut off whatever reads the variable.
    deleteStoredKey(opts.baseUrl);
    console.log(
      `The stored key is also the value of ${ENV_VAR}, so logout did not revoke it on the server. ` +
      'Deleted the local copy only. Unset the variable and revoke the key in the dashboard if you mean to.'
    );
    return 0;
  }
  if (envSet) {
    console.log(
      `Note: logout only revokes the key stored by \`veilfile login\`; ${ENV_VAR} is left alone. ` +
      'Unset it to stop using it here, or revoke it in the dashboard.'
    );
  }
  if (!key) {
    console.log(envSet ? `No key stored by \`veilfile login\` for ${originOf(opts.baseUrl)}.` : 'Not logged in.');
    return 0;
  }
  if (!keyTransportAllowed(opts)) return 1;

  const revoked = await revokeOwnKey(opts.baseUrl, key);
  if (!revoked.ok) {
    process.stderr.write(`${revoked.message}\n(The local copy was left in place.)\n`);
    return 1;
  }
  if (revoked.state === 'invalid') console.log('The stored key is already invalid on the server.');
  if (revoked.state === 'missing') console.log('No matching active key found on the server.');
  deleteStoredKey(opts.baseUrl);
  console.log('Logged out.');
  return 0;
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

async function cmdStatus(opts) {
  const { key, source, path: keyPath, note } = readStoredKey(opts.baseUrl);
  if (!key) {
    process.stderr.write('Not logged in. Run `veilfile login`.\n');
    if (note) process.stderr.write(`Note: ${note}\n`);
    return 1;
  }
  if (!keyTransportAllowed(opts)) return 1;
  let result;
  try {
    result = await api(opts.baseUrl, 'GET', '/api/v1/usage', { key });
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    return 1;
  }
  const { status, body, isJson } = result;
  if (status === 401 || status === 403) {
    process.stderr.write(
      source === 'environment'
        ? `The ${ENV_VAR} key is invalid or revoked.\n`
        : 'The stored key is invalid or revoked. Run `veilfile login` again.\n'
    );
    return 1;
  }
  // Anything but the usage document (a captive portal, a proxy page) is a
  // failure, not a status.
  if (status !== 200 || !isJson || Array.isArray(body) || typeof body.plan !== 'string') {
    process.stderr.write(
      `Could not check status (HTTP ${status}${isJson ? '' : ', not a JSON response'})` +
      `${serverDetail(body)}. Try again.\n`
    );
    return 1;
  }

  const email = body.email ? printable(body.email, 254) : '–';
  const accountLine = email + (body.email_verified ? '' : ' (email not verified)');
  const uploadsCap = body.uploads_cap != null ? printable(body.uploads_cap) : '–';
  const storageCap = body.storage_cap_bytes ? formatBytes(body.storage_cap_bytes) : '–';
  console.log(`Account:  ${accountLine}`);
  console.log(`Plan:     ${printable(body.plan) || '–'}`);
  console.log(`Uploads:  ${body.uploads_used != null ? printable(body.uploads_used) : '–'} / ${uploadsCap} this month`);
  console.log(`Storage:  ${formatBytes(body.storage_used_bytes)} / ${storageCap}`);
  console.log(`Key from: ${source === 'environment' ? 'environment variable' : keyPath}`);
  return 0;
}

// ---------------------------------------------------------------------------
// push
// ---------------------------------------------------------------------------

// The server's upload allowlist. Anything else goes up as
// application/octet-stream and the server explains the refusal.
const MIME_TYPES = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  webp: 'image/webp',
  gif: 'image/gif',
  pdf: 'application/pdf',
  txt: 'text/plain',
  log: 'text/plain',
  md: 'text/markdown',
  json: 'application/json',
  har: 'application/json',
};

function mimeTypeFor(filename) {
  const ext = path.extname(filename).slice(1).toLowerCase();
  return Object.prototype.hasOwnProperty.call(MIME_TYPES, ext) ? MIME_TYPES[ext] : 'application/octet-stream';
}

async function cmdPush(opts, fileArg) {
  const { key, note } = readStoredKey(opts.baseUrl);
  if (!key) {
    process.stderr.write('Not logged in. Run `veilfile login`.\n');
    if (note) process.stderr.write(`Note: ${note}\n`);
    return 1;
  }
  if (!keyTransportAllowed(opts)) return 1;
  const filePath = path.resolve(fileArg);
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    process.stderr.write(`No such file: ${fileArg}\n`);
    return 1;
  }
  if (!stat.isFile()) {
    process.stderr.write(`Not a file: ${fileArg}\n`);
    return 1;
  }
  if (stat.size > MAX_UPLOAD_BYTES) {
    process.stderr.write(
      `File is ${formatBytes(stat.size)}; the upload limit is ${formatBytes(MAX_UPLOAD_BYTES)}.\n`
    );
    return 1;
  }
  let bytes;
  try {
    bytes = fs.readFileSync(filePath);
  } catch (err) {
    process.stderr.write(`Could not read ${fileArg}: ${err.message}\n`);
    return 1;
  }

  const filename = path.basename(filePath);
  const form = new FormData();
  form.append('file', new Blob([bytes], { type: mimeTypeFor(filename) }), filename);
  if (opts.ttlDays != null) form.append('ttl_days', String(opts.ttlDays));

  const url = opts.baseUrl.replace(/\/+$/, '') + '/api/v1/artifacts';
  // Room for slow uplinks: a minute plus a second per 64 KiB.
  const timeoutMs = 60 * 1000 + Math.ceil(stat.size / 65536) * 1000;
  let response;
  let raw;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, Accept: 'application/json' },
      body: form,
      redirect: 'manual',
      signal: AbortSignal.timeout(timeoutMs),
    });
    raw = await response.text();
  } catch (err) {
    process.stderr.write(`${transportError(err, url, timeoutMs).message}\n`);
    return 1;
  }
  let body = {};
  let isJson = false;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      body = parsed;
      isJson = true;
    }
  } catch {
    // fall through to the status checks
  }
  const status = response.status;

  if (status === 201 && isJson && typeof body.url === 'string' && body.url) {
    console.log(printable(body.url, 2048));
    if (body.expires_at) console.log(`Expires: ${printable(body.expires_at)}`);
    if (body.ttl_adjusted) {
      console.log('Note: the server adjusted the expiry to fit your plan (see Expires).');
    }
    if (Array.isArray(body.secret_flags) && body.secret_flags.length > 0) {
      console.log(
        `Warning: secret scan flagged ${body.secret_flags.map((f) => printable(f, 64)).join(', ')}. ` +
        'Ask before sharing this link, or revoke the upload.'
      );
    }
    return 0;
  }
  if (status === 401 || status === 403) {
    process.stderr.write('The stored key is invalid or revoked. Run `veilfile login` again.\n');
    return 1;
  }
  if (status === 402) {
    process.stderr.write('Storage cap reached for your plan. Free space by revoking old uploads, or upgrade.\n');
    return 1;
  }
  if (status === 400 && body.code === 'secret_detected') {
    process.stderr.write(
      'Upload blocked: the file looks like it contains a secret. ' +
      'Remove it and try again, or share the file another way.\n'
    );
    return 1;
  }
  if (status === 400 || status === 413) {
    process.stderr.write(`Upload rejected (HTTP ${status})${serverDetail(body)}\n`);
    return 1;
  }
  if (status === 429) {
    const wait = retryAfterSecs(response.headers);
    process.stderr.write(
      `Upload refused (HTTP 429)${serverDetail(body) || ': too many requests'}` +
      `${wait != null ? ` Retry after ${wait}s.` : ''}\n`
    );
    return 1;
  }
  process.stderr.write(
    `Upload failed (HTTP ${status})${serverDetail(body) || (isJson ? '' : ': unexpected response from the server')}\n`
  );
  return 1;
}

// ---------------------------------------------------------------------------
// pull
// ---------------------------------------------------------------------------

/** RFC 8187 ext-value (charset'lang'pct-encoded) decoded, or null. */
function decodeExtValue(value) {
  const m = /^([A-Za-z0-9!#$%&+^_`{}~-]+)'[^']*'(.*)$/.exec(value);
  if (!m) return null;
  const charset = m[1].toLowerCase();
  try {
    if (charset === 'utf-8') return decodeURIComponent(m[2]);
    if (charset === 'iso-8859-1') {
      return m[2].replace(/%([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
    }
  } catch {
    // malformed percent-encoding
  }
  return null;
}

/**
 * Content-Disposition parameters (RFC 6266): { filename, filenameStar }.
 * Quoted-pairs are unescaped and filename* is percent-decoded; the plain
 * filename is never percent-decoded.
 */
function parseContentDisposition(header) {
  const s = String(header || '');
  const params = new Map();
  let i = s.indexOf(';');
  if (i < 0) i = s.length;
  while (i < s.length) {
    while (i < s.length && (s[i] === ';' || s[i] === ' ' || s[i] === '\t')) i++;
    let name = '';
    while (i < s.length && s[i] !== '=' && s[i] !== ';') name += s[i++];
    name = name.trim().toLowerCase();
    if (s[i] !== '=') continue;
    i++;
    while (i < s.length && (s[i] === ' ' || s[i] === '\t')) i++;
    let value = '';
    if (s[i] === '"') {
      i++;
      while (i < s.length && s[i] !== '"') {
        if (s[i] === '\\' && i + 1 < s.length) i++;
        value += s[i++];
      }
      while (i < s.length && s[i] !== ';') i++;
    } else {
      while (i < s.length && s[i] !== ';') value += s[i++];
      value = value.trim();
    }
    if (name && !params.has(name)) params.set(name, value);
  }
  return {
    filename: params.has('filename') ? params.get('filename') : null,
    filenameStar: params.has('filename*') ? decodeExtValue(params.get('filename*')) : null,
  };
}

// Superscript digits 1-3 (U+00B9, U+00B2, U+00B3) also name Windows devices.
const WINDOWS_RESERVED_NAME = /^(con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$) *(\.|$)/i;
// Well inside the 255-byte name limit of APFS, ext4 and NTFS.
const MAX_FILENAME_BYTES = 200;

/** s cut to at most maxBytes of UTF-8, never inside a character. */
function truncateUtf8(s, maxBytes) {
  let out = '';
  let bytes = 0;
  for (const ch of s) {
    bytes += Buffer.byteLength(ch);
    if (bytes > maxBytes) break;
    out += ch;
  }
  return out;
}

/**
 * A server-supplied name reduced to one safe path component, or '' when
 * nothing usable is left. Runs after all decoding: keeps only the part after
 * the last '/' or '\', drops control and bidi characters, replaces the
 * characters Windows forbids (':' would name a drive or a data stream),
 * strips leading and trailing dots and spaces (no dotfiles, no '..'),
 * shortens long names to MAX_FILENAME_BYTES of UTF-8 (keeping the
 * extension), and defuses Windows device names.
 */
function sanitizeFilename(name) {
  if (typeof name !== 'string') return '';
  const strip = (s) => s.replace(/^[\s.]+|[\s.]+$/g, '');
  let base = name.split(/[\\/]/).pop();
  base = base.replace(UNSAFE_CHARS, '');
  base = base.replace(/[<>:"|?*]/g, '_');
  base = strip(base);
  if (Buffer.byteLength(base) > MAX_FILENAME_BYTES) {
    const ext = path.extname(base);
    const keptExt = truncateUtf8(ext, 16);
    const stem = truncateUtf8(base.slice(0, base.length - ext.length), MAX_FILENAME_BYTES - Buffer.byteLength(keptExt));
    // The cut can leave a trailing space or dot, which Windows drops.
    base = strip(stem + keptExt);
  }
  if (WINDOWS_RESERVED_NAME.test(base)) base = `_${base}`;
  return base;
}

/** Safe local filename: Content-Disposition (filename* first), then the URL path. */
function filenameFromResponse(url, headers) {
  const { filename, filenameStar } = parseContentDisposition(headers.get('content-disposition'));
  for (const candidate of [filenameStar, filename]) {
    const name = sanitizeFilename(candidate);
    if (name) return name;
  }
  let segment = '';
  try {
    segment = decodeURIComponent(new URL(url).pathname.split('/').filter(Boolean).pop() || '');
  } catch {
    // undecodable path: use the default
  }
  return sanitizeFilename(segment) || 'download';
}

/** The response body as chunks; streamed whenever the runtime provides a stream. */
async function* bodyChunks(response) {
  if (response.body && typeof response.body[Symbol.asyncIterator] === 'function') {
    for await (const chunk of response.body) yield chunk;
    return;
  }
  if (typeof response.arrayBuffer === 'function') yield new Uint8Array(await response.arrayBuffer());
}

function pathExists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

/** Move the finished download into place; without --force never replace a file. */
function placeDownload(tmpPath, target, force) {
  if (force) {
    fs.renameSync(tmpPath, target);
    return true;
  }
  try {
    // link() fails with EEXIST instead of replacing: no window for a race.
    fs.linkSync(tmpPath, target);
  } catch (err) {
    if (err.code === 'EEXIST') return false;
    // Filesystems without hard links: check, then rename.
    if (pathExists(target)) return false;
    fs.renameSync(tmpPath, target);
    return true;
  }
  try { fs.unlinkSync(tmpPath); } catch { /* the saved file is in place either way */ }
  return true;
}

async function cmdPull(opts, urlArg) {
  let url;
  try {
    url = new URL(urlArg);
  } catch {
    url = null;
  }
  if (!url || (url.protocol !== 'https:' && url.protocol !== 'http:')) {
    process.stderr.write(`Not a valid URL: ${urlArg}\n`);
    return 1;
  }
  const baseHost = new URL(opts.baseUrl).hostname.toLowerCase();
  if (url.hostname.toLowerCase() !== baseHost) {
    process.stderr.write(
      `Refusing to download from ${url.hostname}: pull only fetches from ${baseHost} ` +
      '(point --base-url at another deploy to use it).\n'
    );
    return 1;
  }

  // Either an explicit --out file, or a server-named file in --out's
  // directory (default: the current directory).
  let dir = process.cwd();
  let explicitTarget = null;
  if (opts.out) {
    let isDir = false;
    try { isDir = fs.statSync(opts.out).isDirectory(); } catch { /* not an existing directory */ }
    if (isDir) dir = path.resolve(opts.out);
    else explicitTarget = path.resolve(opts.out);
  }
  const refuseOverwrite = (target) => {
    process.stderr.write(
      `Refusing to overwrite ${target}. Pass --force to replace it, or --out to choose another path.\n`
    );
    return 1;
  };
  if (explicitTarget && !opts.force && pathExists(explicitTarget)) return refuseOverwrite(explicitTarget);

  const controller = new AbortController();
  let idleTimer = null;
  const touch = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(
      () => controller.abort(new DOMException('No data received', 'TimeoutError')),
      TRANSFER_IDLE_TIMEOUT_MS
    );
  };
  // A server that keeps sending a byte now and then never trips the idle
  // timeout; this bounds the whole download.
  let deadlinePassed = false;
  const deadlineTimer = setTimeout(() => {
    deadlinePassed = true;
    controller.abort(new DOMException('Download deadline passed', 'TimeoutError'));
  }, deps.pullDeadlineMs);
  const transferFailure = (err, prefix) =>
    deadlinePassed
      ? `Timed out: the download took longer than ${Math.round(deps.pullDeadlineMs / 1000)}s in total.`
      : `${prefix}${transportError(err, current.toString(), TRANSFER_IDLE_TIMEOUT_MS).message}`;
  let tmpPath = null;
  let fd = null;
  let target = explicitTarget;
  // Ctrl-C mid-download: remove the partial file before exiting.
  const onSigint = () => {
    if (fd !== null) try { fs.closeSync(fd); } catch { /* closing anyway */ }
    if (tmpPath) try { fs.unlinkSync(tmpPath); } catch { /* already gone */ }
    process.stderr.write('\nDownload cancelled. Nothing was saved.\n');
    process.exit(EXIT_CANCELLED);
  };
  let current = url;
  process.on('SIGINT', onSigint);
  try {
    touch();
    let response;
    for (let hops = 0; ; hops++) {
      try {
        response = await fetch(current.toString(), {
          headers: { Accept: '*/*' },
          redirect: 'manual',
          signal: controller.signal,
        });
      } catch (err) {
        process.stderr.write(`${transferFailure(err, '')}\n`);
        return 1;
      }
      if (![301, 302, 303, 307, 308].includes(response.status)) break;
      let next = null;
      try { next = new URL(response.headers.get('location'), current); } catch { /* invalid */ }
      const sameServer =
        next &&
        next.hostname.toLowerCase() === baseHost &&
        (next.protocol === 'https:' || (next.protocol === 'http:' && current.protocol === 'http:'));
      if (!sameServer) {
        process.stderr.write(`Refusing to follow a redirect to ${next ? next.origin : 'an invalid location'}.\n`);
        return 1;
      }
      if (hops >= MAX_REDIRECTS) {
        process.stderr.write(`Too many redirects (more than ${MAX_REDIRECTS}).\n`);
        return 1;
      }
      current = next;
    }
    if (response.status === 404) {
      process.stderr.write('Not found: the link is unknown, revoked, or expired.\n');
      return 1;
    }
    if (!response.ok) {
      process.stderr.write(`Download failed (HTTP ${response.status}).\n`);
      return 1;
    }
    const lengthHeader = response.headers.get('content-length');
    const declared = lengthHeader !== null && /^\d+$/.test(lengthHeader.trim()) ? Number(lengthHeader) : null;
    const tooLarge = () => {
      process.stderr.write(
        `Refusing to save more than ${formatBytes(MAX_DOWNLOAD_BYTES)}: larger than any Veilfile artifact.\n`
      );
      return 1;
    };
    if (declared !== null && declared > MAX_DOWNLOAD_BYTES) return tooLarge();

    if (!target) {
      target = path.join(dir, filenameFromResponse(current.toString(), response.headers));
      // The name is a single path component; never write anywhere else.
      if (path.dirname(target) !== dir) {
        process.stderr.write('Refusing to save: the server-supplied filename is not usable.\n');
        return 1;
      }
    }
    if (!opts.force && pathExists(target)) return refuseOverwrite(target);

    try {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      tmpPath = path.join(path.dirname(target), `.veilfile-${crypto.randomBytes(6).toString('hex')}.part`);
      fd = fs.openSync(tmpPath, 'wx');
    } catch (err) {
      process.stderr.write(`Could not write ${target}: ${err.message}\n`);
      return 1;
    }
    let total = 0;
    try {
      for await (const chunk of bodyChunks(response)) {
        touch();
        total += chunk.length;
        if (total > MAX_DOWNLOAD_BYTES) return tooLarge();
        fs.writeSync(fd, chunk);
      }
    } catch (err) {
      const message = err && typeof err.syscall === 'string'
        ? `Could not write ${target}: ${err.message}`
        : transferFailure(err, 'Download interrupted: ');
      process.stderr.write(`${message}\n`);
      return 1;
    }
    fs.closeSync(fd);
    fd = null;
    const encoding = (response.headers.get('content-encoding') || 'identity').toLowerCase();
    // fetch decompresses transparently, so compare only unencoded lengths.
    if (declared !== null && encoding === 'identity' && total !== declared) {
      process.stderr.write('Download incomplete (size mismatch). Try again.\n');
      return 1;
    }
    try {
      if (!placeDownload(tmpPath, target, opts.force)) return refuseOverwrite(target);
    } catch (err) {
      process.stderr.write(`Could not save ${target}: ${err.message}\n`);
      return 1;
    }
    tmpPath = null;
    console.log(`Saved to ${target} (${formatBytes(total)}).`);
    return 0;
  } finally {
    process.removeListener('SIGINT', onSigint);
    clearTimeout(idleTimer);
    clearTimeout(deadlineTimer);
    controller.abort();
    if (fd !== null) try { fs.closeSync(fd); } catch { /* closing anyway */ }
    if (tmpPath) try { fs.unlinkSync(tmpPath); } catch { /* already gone */ }
  }
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

const USAGE = 'Usage: veilfile <login|logout|status|push <file>|pull <url>> [options]\n' +
  'Run `veilfile --help` for details.';

function helpText() {
  return `veilfile ${VERSION} — command-line client for Veilfile.

Usage: veilfile <command> [options]

Commands:
  login [--print-key]     Log in via the browser; store the API key.
  logout                  Revoke the stored key on the server; delete the local copy.
  status | whoami         Show account status for the key in use (never prints it).
  push <file> [--ttl-days N]
                          Upload a file (max 25 MiB); print its share URL.
  pull <url> [--out PATH] [--force]
                          Download a shared file into the current directory
                          (or PATH). Never overwrites a file without --force.

Global options (accepted anywhere on the line):
  --base-url URL          API base URL (or ${BASE_URL_ENV_VAR}). Default: ${DEFAULT_BASE_URL}
  --allow-http            Allow an API key to travel over plain http to a host
                          other than this machine.
  --version               Print version and exit.
  --help                  Show this help.

Keys are stored per server in 0600 files under ${configDir()}
and are only sent to the server that issued them. ${ENV_VAR}, when set,
takes precedence for the ${BASE_URL_ENV_VAR} server (default ${DEFAULT_BASE_URL}).`;
}

function printHelp() {
  console.log(helpText());
}

const CLI_OPTIONS = {
  'base-url': { type: 'string' },
  'allow-http': { type: 'boolean' },
  'print-key': { type: 'boolean' },
  'ttl-days': { type: 'string' },
  out: { type: 'string', short: 'o' },
  force: { type: 'boolean', short: 'f' },
  help: { type: 'boolean', short: 'h' },
  version: { type: 'boolean', short: 'V' },
};

// Positional arguments and command-specific options per command.
const COMMANDS = {
  login: { args: [], options: ['print-key'] },
  logout: { args: [], options: [] },
  status: { args: [], options: [] },
  whoami: { args: [], options: [] },
  push: { args: ['file'], options: ['ttl-days'] },
  pull: { args: ['url'], options: ['out', 'force'] },
};

/**
 * Parse argv. Global options work in any position; unknown options, missing
 * values, stray arguments and a bad --ttl-days throw UsageError.
 */
function parseArgs(argv) {
  let parsed;
  try {
    parsed = util.parseArgs({ args: argv, options: CLI_OPTIONS, strict: true, allowPositionals: true });
  } catch (err) {
    throw new UsageError(err.message);
  }
  const { values, positionals } = parsed;
  const [command = null, ...rest] = positionals;
  const opts = {
    baseUrl: null,
    command: command === 'help' ? null : command,
    rest,
    printKey: Boolean(values['print-key']),
    ttlDays: null,
    out: values.out != null ? values.out : null,
    force: Boolean(values.force),
    allowHttp: Boolean(values['allow-http']),
    help: Boolean(values.help) || command === 'help',
    version: Boolean(values.version),
  };
  if (opts.help || opts.version) return opts;

  opts.baseUrl = normalizeBaseUrl(values['base-url'] != null ? values['base-url'] : envBaseUrl());
  if (opts.command === null) return opts;
  if (!Object.prototype.hasOwnProperty.call(COMMANDS, opts.command)) {
    throw new UsageError(`Unknown command: ${opts.command}`);
  }
  const spec = COMMANDS[opts.command];
  for (const name of ['print-key', 'ttl-days', 'out', 'force']) {
    if (values[name] !== undefined && !spec.options.includes(name)) {
      throw new UsageError(`--${name} does not apply to ${opts.command}.`);
    }
  }
  if (rest.length < spec.args.length) {
    throw new UsageError(`Missing <${spec.args[rest.length]}> for ${opts.command}.`);
  }
  if (rest.length > spec.args.length) {
    throw new UsageError(`Unexpected argument: ${rest[spec.args.length]}`);
  }
  if (values['ttl-days'] !== undefined) {
    if (!/^[1-9][0-9]{0,5}$/.test(values['ttl-days'])) {
      throw new UsageError(`--ttl-days must be a whole number of days (1 or more), not "${values['ttl-days']}".`);
    }
    opts.ttlDays = values['ttl-days'];
  }
  if (opts.out === '') throw new UsageError('--out needs a path.');
  return opts;
}

async function main(argv = process.argv.slice(2)) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    if (!(err instanceof UsageError)) throw err;
    process.stderr.write(`veilfile: ${err.message}\n${USAGE}\n`);
    return EXIT_USAGE;
  }
  if (opts.version) {
    console.log(`veilfile ${VERSION}`);
    return 0;
  }
  if (opts.help) {
    printHelp();
    return 0;
  }
  switch (opts.command) {
    case 'login':
      return await cmdLogin(opts);
    case 'logout':
      return await cmdLogout(opts);
    case 'status':
    case 'whoami':
      return await cmdStatus(opts);
    case 'push':
      return await cmdPush(opts, opts.rest[0]);
    case 'pull':
      return await cmdPull(opts, opts.rest[0]);
    default:
      process.stderr.write(`${helpText()}\n`);
      return EXIT_USAGE;
  }
}

if (require.main === module) {
  main().then(
    (code) => process.exit(code == null ? 0 : code),
    (err) => {
      process.stderr.write(`${err && err.message ? err.message : err}\n`);
      process.exit(1);
    }
  );
}

// Exported for tests.
module.exports = {
  VERSION,
  configDir,
  credentialsPath,
  readStoredKey,
  writeStoredKey,
  deleteStoredKey,
  request,
  formatBytes,
  cmdLogin,
  cmdLogout,
  cmdStatus,
  cmdPush,
  cmdPull,
  parseArgs,
  main,
  __test: {
    ApiError,
    UsageError,
    LoginError,
    deps,
    openBrowser,
    browserUrl,
    isLoopbackHost,
    readFileKey,
    revokeOwnKey,
    pollForKey,
    parseContentDisposition,
    sanitizeFilename,
    filenameFromResponse,
    mimeTypeFor,
    MAX_DOWNLOAD_BYTES,
  },
};
