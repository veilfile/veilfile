---
title: Quickstart
slug: quickstart
seo_title: Quickstart | Veilfile docs
description: Sign up, verify your email, create an API key, and upload your first artifact in about five minutes.
order: 2
---

Three steps take you from signup to your first unguessable link.

## 1. Create an API key

Sign up at [veilfile.com](https://veilfile.com). We email you a verification link. Click it. You cannot create an API key until your email is verified.

Open the [dashboard](https://veilfile.com/app/keys/) and create a key. It is shown exactly once. Copy it now.

Store the key as a secret in an environment variable or your secret manager. Your agent gets the variable name, `VEILFILE_API_KEY`. It never gets the value in a prompt.

## 2. Save it in your shell

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

## 3. Upload

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

Next: [connect an agent via MCP](/docs/agent/), or read [how it works](/docs/how-it-works/).

## Limits

- One file is at most 25 MiB.
- The allowed file types are png, jpg, jpeg, webp, gif, txt, md, log, json, har, and pdf.
- Each API key or connected app can make 100 uploads per hour.
- Each plan has a monthly upload quota and a hard storage cap. An upload that would exceed the storage cap fails with `402 storage_cap_reached`. See [how it works](/docs/how-it-works/) for the full table.
