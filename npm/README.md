# veilfile CLI (npm)

Command-line client for [Veilfile](https://veilfile.com). Single file, zero
dependencies — Node 18.3+ only (global `fetch`, `FormData`, `Blob`,
`util.parseArgs`).

## Install

```sh
npm install -g veilfile
```

Or run the script directly with any Node 18.3+:

```sh
node veilfile.js login
```

## Usage

```sh
veilfile login             # browser approval flow; stores the API key locally
veilfile logout            # revoke the stored key on the server; delete the local copy
veilfile status            # confirm the key in use is valid (never prints it)
veilfile push <file>       # upload a file; print its share URL
veilfile pull <url>        # download a shared file into the current directory
```

Options:

- `--ttl-days N` on push: days until the link expires, a whole number of
  1 or more. The server caps it at your plan's maximum and says so.
- `--out PATH` (`-o`) on pull: target file, or an existing directory to
  save into.
- `--force` (`-f`) on pull: replace an existing file.
- `--print-key` on login: also print the raw key. Use it only when you
  explicitly need the key; it is never printed otherwise.
- `--base-url URL` (or `VEILFILE_BASE_URL`): the server to talk to, for
  example a local dev server. Default `https://veilfile.com`.
- `--allow-http`: allow an API key to travel over plain `http` to a host
  other than this machine (see below).

Options can go anywhere on the command line (`veilfile push f.png
--base-url http://localhost:8000` works). Unknown options, missing values,
extra arguments and a `--ttl-days` that is not a whole number are errors.

### Exit codes and output

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | The command failed (not logged in, server refused, network error, …) |
| 2    | Bad command line; usage is printed on stderr |
| 130  | Cancelled with Ctrl-C |

`push` prints the share URL as the first line of stdout and nothing on
stdout when it fails. Errors go to stderr. `status` exits 1 unless the
server answered with a real usage document (a captive portal or proxy page
is an error, not a status).

## Logging in

`veilfile login` prints a code and the verification page, and opens that
page in your browser. Approve it there and the terminal picks up the API
key — you never paste a secret.

- The page is opened only if it is an `https` page on the server you are
  logging in to (plain `http` only for this machine). Anything else is
  printed, not opened. The browser is launched without a shell.
- While waiting, server errors, timeouts and rate limits are retried with
  backoff until the code expires. The server creates the key on the poll
  that delivers it, so if a poll's answer is lost (a timeout, a server
  error, Ctrl-C mid-request) and the login then fails, the CLI says a key
  may have been issued: check the dashboard for a new "Terminal login" key.
- If a key from an earlier `veilfile login` to the same server is stored,
  it is revoked after the new key is saved, so re-running `login` does not
  leave old keys active. Ctrl-C at that point still finishes the
  revocation first; a second Ctrl-C skips it and names the key to revoke
  in the dashboard. This includes a stored key you also exported as
  `VEILFILE_API_KEY` (say after `login --print-key`): the CLI warns that
  the variable no longer works, so update it wherever you set it.
- Keys made by `veilfile login` count toward your plan's API-key limit. If
  you are at the limit, the approval page lets you choose an existing key
  to replace.

## Where the key is kept, and where it is sent

Keys are stored in `0600` files in `~/.config/veilfile` (macOS/Linux,
honouring `XDG_CONFIG_HOME`) or `%APPDATA%\veilfile` (Windows), one file per
server: `credentials` for `https://veilfile.com`, `credentials-<server>` for
any other. Each file records the server that issued the key, and a stored
key is only ever sent to that server. Logging in to a dev server never
replaces your production key. Files are written to a temp file and renamed
into place, so a failed write keeps the previous key.

`VEILFILE_API_KEY`, when set, takes precedence over the stored key, but
only for the `VEILFILE_BASE_URL` server (default `https://veilfile.com`).
It is not sent to a server named only with `--base-url`.

A key is never exchanged over plain `http` with a host other than this
machine (`localhost`, `127.x.x.x`, `::1`, `0.0.0.0`) unless you pass
`--allow-http`. The CLI does not follow redirects on API calls, so a
redirect cannot carry the key elsewhere.

`veilfile logout` revokes and deletes only the key `veilfile login` stored
for that server. It never revokes `VEILFILE_API_KEY`, which is usually
shared with an MCP client or CI; revoke that one in the dashboard if you
mean to. If the stored key is the same key as `VEILFILE_API_KEY` (for
example after `login --print-key`), logout only deletes the local copy.
If revoking fails (a network error, or an error status other than 401,
which means the key is already invalid), the local copy is kept and
logout exits 1.

## Pushing

`push` sends the file with its MIME type (from the extension: png, jpg,
jpeg, gif, webp, pdf, txt, log, md, json, har), so images, PDFs and text
open in the browser. Anything else is sent as `application/octet-stream`
and the server explains if it refuses it. Files over 25 MiB are refused
before upload. Rejections show the server's reason; rate limits show when
to retry.

## Pulling

`pull` only downloads from the `--base-url` server's host, and only follows
redirects that stay on that host. It names the file from the server's
`Content-Disposition` header (`filename*` first; only `filename*` is
percent-decoded), then reduces the name to a single safe file name: no
directories, no leading dots, no control characters, no Windows device
names or `:`, at most 200 bytes (the extension is kept). If nothing
usable is left it falls back to the link's last
path segment, then to `download`.

It never replaces an existing file unless you pass `--force`. The body is
streamed to a temporary file in the target directory, capped at 26 MiB,
and renamed into place only when complete, so a failed, oversized or
Ctrl-C'd download leaves nothing behind. A download that stalls for 30
seconds, or takes more than 10 minutes in total, is abandoned; API calls
time out after 30 seconds.

## One binary name, two installers

Both `pip install veilfile` (Python CLI) and `npm install -g veilfile`
(this CLI) install a binary called `veilfile`. They have the same commands
and share the `credentials` file for `https://veilfile.com`, but they are
separate implementations and do not behave identically. Differences as of
this release include:

- This CLI keeps one credentials file per server and sends a stored key
  only to the server that issued it. The Python CLI keeps a single
  `credentials` file for whichever server it last logged in to. This CLI
  uses that file only when it records `https://veilfile.com` as its
  server, or records no server at all (older clients). After a Python
  login to another server, this CLI reports "Not logged in" for
  `https://veilfile.com`, and a login here replaces that file without
  revoking the other server's key.
- `logout` here never revokes `VEILFILE_API_KEY`.
- `--allow-http`, and options in any position, are specific to this CLI.
- Messages differ; scripts should rely on exit codes and the first line of
  `push` output, not on wording.

If you install both, the one whose directory comes first in your `PATH`
wins. To see the order: `which -a veilfile` (macOS/Linux) or
`where veilfile` (Windows). To be explicit, invoke by full path.

Recommendation: install only one. If you work mostly in Node, keep the npm
one; if mostly in Python, keep the pip one. The CLIs do not try to detect
or defer to each other — that would be silent behavior, and silent behavior
around credentials is the wrong call.

## Distribution notes

- **npm (recommended):** `npm install -g veilfile` pulls a versioned package
  over TLS from the npm registry. Zero dependencies, so the supply chain is
  one package.
- **Inspect first:** `npm pack veilfile` downloads the package tarball
  without installing it; the whole client is one file, `veilfile.js`, so you
  can read every line before you run it.
- **curl-to-shell: not recommended.** Piping remote code straight into a
  shell skips integrity verification entirely. For a security-conscious
  buyer that is the wrong default, so we do not publish a one-liner.

## What's new in 0.4.0

Security and reliability fixes; upgrade with `npm install -g veilfile@latest`.

- **`pull` is safe on untrusted links.** The server's filename is reduced to
  one plain file name, the file is written only inside the target directory,
  an existing file is never overwritten without `--force`, and downloads come
  only from your configured server.
- **Keys stay with their server.** Credentials are stored per server in `0600`
  files (written atomically) and sent only to the server that issued them;
  sending a key over plain http to another machine needs `--allow-http`.
- **`login`** opens the browser without a shell, keeps waiting through brief
  network errors, and revokes your previous key once the new one is stored.
- **Options work anywhere on the line**; unknown options and bad values (for
  example `--ttl-days 1d`) are errors (exit 2) instead of being ignored.
- **`push`** sends the file's type, so images, PDFs and text preview in the
  browser, and shows the server's reason when an upload is refused.
- Every request has a timeout. `logout` no longer revokes a shared
  `VEILFILE_API_KEY`.

## Development

```sh
cd npm
npm test           # node --test; fetch, the browser and the clock are stubbed
```

The tests never touch your real credentials (every platform's config
directory points into a temp dir) and never open a browser. A few
end-to-end tests run the CLI against a local HTTP server on 127.0.0.1.
