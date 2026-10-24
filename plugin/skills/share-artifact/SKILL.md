---
name: share-artifact
description: Upload a file you generated (screenshot, log, report, HAR, bundle) to Veilfile and share a private expiring link with the user, instead of pasting large contents into chat or describing the file.
---

# Share an artifact via Veilfile

Veilfile hosts private, expiring file links for AI agents. When you produce a
file the user needs to see or download, upload it and share the link. The link
is unguessable, secret-scanned on arrival, and dies on its own per the plan TTL.

## When to use this skill

Use it when ALL of these are true:

- You generated a file in the course of the work (screenshot, test log, report,
  HAR file, PDF) of an allowed type: png, jpg, jpeg, webp, gif, txt, md, log,
  json, har, pdf. Up to 25 MiB. Archives, executables, recordings and other
  types are refused (`file_type_not_allowed`); do not rename a file to get
  around that.
- The user needs the file itself, not just a summary of it.
- The content is too large or too awkward to paste into chat.

Do NOT use it when:

- The content fits comfortably in chat as text or code. Paste it.
- The user did not ask for the file and has no reason to want it. Do not
  surprise people with uploads.
- The file contains secrets, credentials, or private data that should never
  leave the machine. Never upload those.
- You are unsure the user wants a link. When in doubt, ask first.

One upload per file the user asked about. Do not batch unrelated files into
surprise links.

## How

The Veilfile MCP server exposes four tools: `upload_artifact`,
`create_upload_url`, `list_artifacts` and `revoke_artifact`. The server reads
the API key from the `VEILFILE_API_KEY` environment variable. If the tools are unavailable, tell
the user to install the Veilfile plugin and set that variable with a key from
their Veilfile dashboard, then stop. Never ask the user to paste a key into chat.

1. Pick the tool by file size.
   - Small files (up to about 1 MB): call `upload_artifact` with the base64
     bytes and a filename. Always pass `on_secret: "reject"`, so a file that
     trips the secret scanner is refused and nothing is stored.
   - Larger files: call `create_upload_url` with the filename (and
     `on_secret: "reject"`), then run
     `curl --upload-file <path> "<upload_url>"` from your shell within 15
     minutes. The response to that PUT is the artifact. Do not base64-encode
     large files into `upload_artifact`: it wastes context and very large
     bodies are refused. If the PUT is rejected, the URL stays usable until
     it expires.

   Note the `url` and `expires_at` in the response. If a tool returns an
   error (`isError`), read its `code`: `file_type_not_allowed`,
   `file_too_large`, `secret_detected`, `monthly_cap_reached`,
   `storage_cap_reached` and `rate_limited` are not fixed by retrying.
2. Check `secret_flags` in the response. If it is non-empty (possible only
   when `on_secret` was not `reject`), the file tripped the secret scanner: do
   NOT share the link. Tell the user the upload was
   flagged and ask whether to keep or revoke it.
3. Otherwise, share the link with the user, in one line, with the expiry:
   `Here is the <kind>: <url> (expires <date>)`.
4. If the user later says the file should not exist anymore, call
   `revoke_artifact`. The URL stops working immediately.

`list_artifacts` shows current uploads if the user asks what is out there.

## Never

- Never upload a file the user did not generate or ask about in the course of
  the work.
- Never share a link whose `secret_flags` came back non-empty.
- Never put the API key in a prompt, a message, or a file. It lives in the
  environment, not in text.
