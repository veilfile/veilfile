---
title: Stop pasting screenshots into public places
date: 2026-11-01
slug: stop-pasting-screenshots-into-public-places
description: Learn how to share agent screenshots and logs through private, expiring links instead of public repos, gists, and chat scrollback.
---

On September 29, 2026, Glow Labs disclosed the PixelLeak incident. AI coding agents had left more than 13,000 internal images in public GitHub repositories. The images were private screenshots, meant for one person or one team. They sat in the open because the tooling made public the path of least resistance. The agent needed a place to put a file, and the easy place was public.

That pattern repeats in smaller ways every day. An agent drops a screenshot into a public gist so a human can see it. The gist has no expiry, and search engines can index it. An agent commits a log bundle to a repo where the file does not belong. The commit stays in history even after the file is deleted from the working tree. An agent uploads a HAR file to a chat channel. The file lives in scrollback, and every person added to the channel later can scroll back to it. Each choice feels quick in the moment. Each one hands a private artifact to a place built to keep things and show them to more people over time.

The fix is a private default with an expiry. Veilfile gives an agent a place to put a file that starts private and stays private. You upload through the REST API or the MCP server, and you get back a URL with a 256-bit token in it. Only someone who holds the link can open the file. The link stops working when its lifetime runs out or when you revoke it, and an expired or revoked link returns a 404 and never comes back.

The workflow takes a few minutes to set up and one command per file after that. Sign up at veilfile.com and verify your email, since you cannot create an API key until your email is verified. Install the CLI with pip or npm, run the login command, and approve the code it shows you. Then push the file.

```bash
pip install veilfile
veilfile login
veilfile push screenshot.png
```

The command prints the share link. Paste the link where the file needs to be seen, and send nothing else. If the file only needs to be seen today, shorten its lifetime with the `--ttl-days 1` flag. The API response carries the expiry time in `expires_at`. Do not assume the value you asked for is the value you got: an ask above your plan's maximum is clamped down, never rejected. An agent that runs through the MCP server follows the same rule. It uploads with its own key, and it shares the link it gets back, not the file itself.

Keys deserve the same discipline as links. Give each agent its own key, so one compromised agent does not mean rotating the whole fleet. If a key leaks, revoke that key from the dashboard or run `veilfile logout` on the machine that holds it. Revoking a key stops that key from uploading or listing files, but links the key already created keep working until they expire or you revoke those artifacts. Kill the link too when the file must stop being reachable.

Teams can turn this into a rule that is easy to check. No artifact leaves a machine except through a private link. No screenshot, log, or HAR file goes into a repo, a public gist, or a chat upload. A reviewer who finds a file in one of those places treats it as a mistake in the workflow, not as a one-off slip. The rule works because the private path is no harder than the public one. An agent that can paste a file into a gist can run one push command instead, and the human watching it gets the same link to open in a browser, with no login.

PixelLeak was not a story about careless people. It was a story about defaults. Public tooling made publishing the easy move, and agents took the easy move thousands of times. Change the default, and the same agents take the private move instead. Upload the file, share the link, and let the link die on its own.
