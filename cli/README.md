# veilfile CLI

Command-line client for [Veilfile](https://veilfile.com). Single file, standard
library only — no dependencies to audit.

## Install

```sh
pip install veilfile
```

Or run the script directly with any Python 3.9+:

```sh
python3 veilfile.py login
```

## Usage

```sh
veilfile login    # browser approval flow; stores the API key locally
veilfile logout   # revoke the key on the server; delete the local copy
veilfile key      # print the stored API key
veilfile status   # confirm the stored key is valid (never prints it)
veilfile push FILE [--ttl-days N] [--visibility only_me|team]  # upload a file; print its share URL
veilfile pull URL [--out PATH] [--force]  # download an artifact
```

`veilfile login` prints a code and opens the verification page in your browser.
Approve it there and the terminal picks up the API key — you never paste a
secret. `veilfile login --print-key` prints the raw key too, for the rare case
you explicitly need it at login time (it is never printed otherwise). To print
the stored key later, run `veilfile key`. It prints the key and nothing else,
so `export VEILFILE_API_KEY=$(veilfile key)` works as written, and it does not
confirm the key is still valid; run `veilfile status` for that. Logging in again
replaces the stored key and revokes the old one on the server that issued it
(the old key is only ever sent there). If you also copied that old key into
`VEILFILE_API_KEY` (a shell profile or MCP config), login warns you, because
that copy stops working too.

`veilfile logout` revokes and deletes only the key `veilfile login` stored.
It never revokes `VEILFILE_API_KEY`, which is usually shared with an MCP
client or CI; revoke that one in the dashboard if you mean to. If the stored
key is the same key as `VEILFILE_API_KEY` (for example after you exported what
`veilfile key` printed), logout only deletes the local copy. If revoking fails
(a network error, or an error status other than 401, which means the key is
already invalid), the local copy is kept and logout exits 1.

The key is stored in a `0600` credentials file (`~/.config/veilfile` on
macOS/Linux; on Windows, `%APPDATA%\veilfile`, protected by that folder's
per-user permissions rather than mode bits). Setting `VEILFILE_API_KEY`
overrides the file, matching the MCP setup docs. `--base-url` (or
`VEILFILE_BASE_URL`) points the CLI at a local dev server.

`veilfile pull` saves under the artifact's own name in the current directory
(or the `--out` directory). The name is reduced to a plain file name, so it
can never land outside that directory, and an existing file is never
overwritten unless you pass `--force`.

## Distribution notes

- **pip (recommended):** `pip install veilfile` pulls a versioned package over
  TLS from PyPI. Zero dependencies, so the supply chain is one package.
- **Inspect first:** `pip download veilfile --no-deps` fetches the package
  without installing it; the whole client is one file, `veilfile.py`, so you
  can read every line before you run it.
- **curl-to-shell: not recommended.** Piping remote code straight into a shell
  skips integrity verification entirely. For a security-conscious buyer that
  is the wrong default, so we do not publish a one-liner.

## What's new in 0.5.0

- **`push --visibility only_me|team`** chooses who on your team can see the
  file on their Files page. It is honored only when your administrator lets
  each person decide. After the upload, `push` says on stderr who can see the
  file, or that the server did not apply the option. Stdout still holds only
  the share URL.

## What's new in 0.4.0

Security and reliability fixes; upgrade with `pip install -U veilfile`.

- **`pull` is safe on untrusted links.** The server's filename is reduced to
  one plain file name, the file is written only inside the target directory,
  an existing file is never overwritten without `--force`, and redirects are
  followed only within your configured server.
- **Credentials** are written atomically with `0600` permissions.
- **`login`** keeps waiting through brief network errors, opens the browser
  only for your server's https page, and revokes your previous key once the
  new one is stored.
- **`logout`** handles network errors cleanly, and a `429` now says whether
  you hit the monthly upload cap or a short rate limit.

## Development

```sh
cd cli
python3 -m unittest discover -s tests -t .
```
