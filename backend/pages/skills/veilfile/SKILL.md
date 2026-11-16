---
name: veilfile
description: Upload a file you generated (screenshot, log, report, HAR file, PDF) with the veilfile command-line tool, and share its private, expiring link with the user instead of pasting large contents into chat.
---

# Share a file through Veilfile

Veilfile hosts private, expiring file links for AI agents. When you make a file the user needs to see or download, upload it with the `veilfile` command and share the link it prints. The link is unguessable, and it stops working when the file expires.

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

## Before the first upload

Run `veilfile status`. If the shell cannot find the command, tell the user to install it with `npm install -g veilfile` or `pip install veilfile`, then stop. If it fails for any other reason, tell the user to run `veilfile login` in their own terminal, then stop. Never ask the user to paste a key into chat, and never run `veilfile login --print-key`, which prints the key.

## How

1. Run `veilfile push <path>`. To choose how many days the link lasts, add `--ttl-days N`; the server shortens it if the plan does not allow that many.
2. The first line of output is the link. If a line of output, on stdout or stderr, begins with `Warning:`, the secret scan flagged the file: do NOT share the link. Tell the user the upload was flagged, and ask whether to keep it or revoke it in the Files section of their Veilfile dashboard.
3. Otherwise, share the link in one line, with the expiry if the output gives one: `Here is the <kind>: <url> (expires <date>)`.
4. If the command fails, it exits with a nonzero status and prints the reason. Tell the user the reason. A refused file type, a file over 25 MiB and a month with no uploads left are not fixed by retrying.

## Never

- Never upload a file the user did not generate or ask about in the course of the work.
- Never share a link whose upload printed a `Warning:` line.
- Never put the API key in a prompt, a message, or a file. It lives in the CLI's credentials file or the environment, not in text.
