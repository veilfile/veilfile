---
title: Quickstart
slug: quickstart
seo_title: Quickstart | Veilfile docs
description: Sign up, verify your email, install the CLI, log in, and push your first file. Your workspace is provisioned automatically when you sign up.
order: 2
---

Sign up, verify your email, install the CLI, log in, and push your first file. Your workspace is provisioned automatically when you sign up. It takes about five minutes to go from signup to your first URL.

Three steps take you from signup to your first unguessable link.

## 1. Install the CLI

Sign up at [veilfile.com](https://veilfile.com). We email you a verification link. Click it. You cannot create an API key until your email is verified.

Install the `veilfile` command with pip, which needs Python 3.9 or later:

<div class="command-group" data-tabs>
<div class="command">
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-install" tabindex="0">pip install veilfile</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-install" aria-label="Copy install command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>

If you use Node 18.3 or later, you can run `npm install -g veilfile` instead. Install only one of the two, because both install a command named `veilfile`.

## 2. Log in

Run this command. It prints a web address and a code, then tries to open that page in your browser.

<div class="command-group" data-tabs>
<div class="command">
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-login" tabindex="0">veilfile login</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-login" aria-label="Copy login command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>

Check that the code on the page matches the one in your terminal, tick the confirmation box, and press Approve. The terminal receives its key within a few seconds. The code expires after 10 minutes and works once. If it expires, run `veilfile login` again.

If the browser does not open, open the printed address on any device and approve it there. The terminal keeps waiting until the code expires.

A login creates one API key, and keys made this way count toward your plan's key limit, the same as keys you create in the dashboard. Free allows one key, Team allows five keys, and Scale allows unlimited keys. At the limit, the approval page asks you to choose a key to replace, and anything still using that key stops working.

The CLI saves the key in a credentials file that only you can read. By default that file is `~/.config/veilfile/credentials` on macOS and Linux, and `%APPDATA%\veilfile\credentials` on Windows. The CLI does not print the key unless you ask it to.

## 3. Push

Replace `screenshot.png` with another file of an allowed type (see Limits below):

<div class="command-group" data-tabs>
<div class="command">
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-push" tabindex="0">veilfile push screenshot.png</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-push" aria-label="Copy push command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>

The command prints the share URL. Anyone who has the URL can open it in a browser with no login: the token in the URL is the auth.

The link expires when your plan's TTL runs out, which is seven days on Free. Pass `--ttl-days 1` to shorten it.

Run `veilfile logout` when you are done with a machine. It revokes the key and deletes the credentials file, and the links you already made keep working until they expire.

Next: [connect an agent via MCP](/docs/agent/), or read [how it works](/docs/how-it-works/).

## Manual fallback

Use this route on a machine that runs unattended, such as a CI runner, or when an agent needs its key in `VEILFILE_API_KEY`. A key saved by `veilfile login` stays in the credentials file, and the key-based MCP setups in the agent docs read `VEILFILE_API_KEY` instead.

### Create an API key

On Free, which allows one key, first revoke the key from `veilfile login`, either in the dashboard or with `veilfile logout`.

Open the [dashboard](https://veilfile.com/app/keys/) and create a key. It is shown exactly once. Copy it now.

Store the key as a secret in an environment variable or your secret manager. Your agent gets the variable name, `VEILFILE_API_KEY`. It never gets the value in a prompt.

### Save it in your shell

Pick your shell. Replace `PASTE_YOUR_KEY_HERE` with the key, paste the line, press enter. It takes effect in this terminal at once.

<div class="command-group" data-tabs>
<div class="command-group__tabs" role="tablist" aria-label="Shell">
<button type="button" role="tab" id="shelltab-bash" aria-selected="true" aria-controls="shellpanel-bash" tabindex="0" class="command-group__tab label interactive">bash</button>
<button type="button" role="tab" id="shelltab-zsh" aria-selected="false" aria-controls="shellpanel-zsh" tabindex="-1" class="command-group__tab label interactive">zsh</button>
<button type="button" role="tab" id="shelltab-fish" aria-selected="false" aria-controls="shellpanel-fish" tabindex="-1" class="command-group__tab label interactive">fish</button>
<button type="button" role="tab" id="shelltab-powershell" aria-selected="false" aria-controls="shellpanel-powershell" tabindex="-1" class="command-group__tab label interactive">PowerShell</button>
<button type="button" role="tab" id="shelltab-cmd" aria-selected="false" aria-controls="shellpanel-cmd" tabindex="-1" class="command-group__tab label interactive">cmd</button>
</div>
<div class="command" role="tabpanel" id="shellpanel-bash" aria-labelledby="shelltab-bash">
<div class="command__for"><span class="command__shell">bash · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-bash" tabindex="0">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.bashrc &amp;&amp; source ~/.bashrc</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-bash" aria-label="Copy bash setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-zsh" aria-labelledby="shelltab-zsh" hidden>
<div class="command__for"><span class="command__shell">zsh · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-zsh" tabindex="0">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.zshrc &amp;&amp; source ~/.zshrc</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-zsh" aria-label="Copy zsh setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-fish" aria-labelledby="shelltab-fish" hidden>
<div class="command__for"><span class="command__shell">fish · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-fish" tabindex="0">set -Ux VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-fish" aria-label="Copy fish setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-powershell" aria-labelledby="shelltab-powershell" hidden>
<div class="command__for"><span class="command__shell">PowerShell · </span>Windows · macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">PS&gt;</span>
<code id="shellcmd-powershell" tabindex="0">$env:VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"; [Environment]::SetEnvironmentVariable("VEILFILE_API_KEY", "PASTE_YOUR_KEY_HERE", "User")</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-powershell" aria-label="Copy PowerShell setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-cmd" aria-labelledby="shelltab-cmd" hidden>
<div class="command__for"><span class="command__shell">cmd · </span>Windows</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">C:\&gt;</span>
<code id="shellcmd-cmd" tabindex="0">set "VEILFILE_API_KEY=PASTE_YOUR_KEY_HERE" &amp;&amp; setx VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-cmd" aria-label="Copy cmd setup command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>

### Upload with curl

With the variable set, this runs as written. Replace `@screenshot.png` with another file of an allowed type (see Limits below):

<div class="command-group" data-tabs>
<div class="command">
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-upload" tabindex="0">curl -X POST https://veilfile.com/api/v1/artifacts -H "Authorization: Bearer $VEILFILE_API_KEY" -F "file=@screenshot.png" -F "ttl_days=7"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-upload" aria-label="Copy upload command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>

A `201` comes back with `id`, `url`, `expires_at`, `size_bytes`, and `secret_flags`. Open the `url` in a browser. It works with no login. The token in the URL is the auth.

Then revoke it:

```bash
curl -X DELETE https://veilfile.com/api/v1/artifacts/<id> \
  -H "Authorization: Bearer $VEILFILE_API_KEY"
```

Replace `<id>` with the id from the upload. Success is a `204` with an empty body. The link returns `404` from that moment on.

## Limits

- One file is at most 25 MiB.
- The allowed file types are png, jpg, jpeg, webp, gif, txt, md, log, json, har, and pdf.
- Each API key or connected app can make 100 uploads per hour.
- Each plan has a monthly upload quota and a hard storage cap. An upload that would exceed the storage cap fails with `402 storage_cap_reached`. See [how it works](/docs/how-it-works/) for the full table.

Prices include tax. The price you see is the price you pay. Free costs $0, Team costs $4 a month, and Scale costs $12 a month. See [Plans](/plans/) for the full table.
