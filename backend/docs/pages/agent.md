---
title: Agent setup
slug: agent
seo_title: Agent setup | Veilfile docs
description: Give an agent a Veilfile key: env var setup, curl upload examples, MCP connection, key rotation, and instant revocation.
order: 3
---

## MCP

Veilfile hosts a Streamable HTTP MCP server at `POST https://veilfile.com/mcp`. It speaks JSON-RPC 2.0. Auth is your API key on every request:

```
Authorization: Bearer <key>
```

Keys start with `vlf_`. The server exposes four tools: `upload_artifact`, `create_upload_url`, `list_artifacts`, `revoke_artifact`.

`upload_artifact` takes the file as base64, which suits small files. For anything over a few MB (HAR files, log bundles, recordings), call `create_upload_url` and then `curl --upload-file` the raw bytes to the URL it returns. Failures come back as tool results with `isError: true` and a JSON `{error, code}` body.

### Claude Code

```bash
claude mcp add --transport http veilfile https://veilfile.com/mcp \
  --header "Authorization: Bearer $VEILFILE_API_KEY"
```

Your shell expands the variable before Claude Code sees it.

### Codex

```bash
codex mcp add veilfile --url https://veilfile.com/mcp --bearer-token-env-var VEILFILE_API_KEY
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.veilfile]
url = "https://veilfile.com/mcp"
bearer_token_env_var = "VEILFILE_API_KEY"
```

### Antigravity

In `~/.gemini/config/mcp_config.json` (Antigravity uses `serverUrl`):

```json
{
  "mcpServers": {
    "veilfile": {
      "serverUrl": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer $VEILFILE_API_KEY" }
    }
  }
}
```

If the variable is not expanded, paste the key value directly.

### Gemini CLI

In `~/.gemini/settings.json` (Gemini CLI uses `httpUrl` for Streamable HTTP):

```json
{
  "mcpServers": {
    "veilfile": {
      "httpUrl": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer $VEILFILE_API_KEY" }
    }
  }
}
```

### Cursor

In `~/.cursor/mcp.json` (Cursor expands `${env:...}`, so the raw key never sits in the file):

```json
{
  "mcpServers": {
    "veilfile": {
      "url": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer ${env:VEILFILE_API_KEY}" }
    }
  }
}
```

Or install in one click (uses the config above; you paste your own key in Cursor):

[Add to Cursor](cursor://anysphere.cursor-deeplink/mcp/install?name=veilfile&config=eyJ1cmwiOiJodHRwczovL3ZlaWxmaWxlLmNvbS9tY3AiLCJoZWFkZXJzIjp7IkF1dGhvcml6YXRpb24iOiJCZWFyZXIgJHtlbnY6VkVJTEZJTEVfQVBJX0tFWX0ifX0=)

### VS Code

In `.vscode/mcp.json` (remote entries need `"type": "http"`; VS Code prompts for the key once and stores it):

```json
{
  "servers": {
    "veilfile": {
      "type": "http",
      "url": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer ${input:veilfile_key}" }
    }
  },
  "inputs": [
    { "type": "promptString", "id": "veilfile_key", "description": "Veilfile API key", "password": true }
  ]
}
```

### Windsurf

In `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "veilfile": {
      "serverUrl": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer ${env:VEILFILE_API_KEY}" }
    }
  }
}
```

Refresh from the Cascade panel after saving.

### Other clients

Point any MCP client at `https://veilfile.com/mcp` over Streamable HTTP. The client must send `Authorization: Bearer <key>` on every request, with the key read from the `VEILFILE_API_KEY` environment variable or your secret manager. Header configuration differs per client. Check its docs.

## When a secret is flagged

Uploads are scanned for secret-shaped content. By default the scan warns and does not block: a flagged upload is stored and its link works. Pass `on_secret: "reject"` (recommended for agents) and a flagged file is refused and nothing is stored.

If `secret_flags` is non-empty in the upload response, do not share the link. Ask the user whether to keep or revoke the upload. Flagged responses also carry a `warning` field with this instruction.

For never-host semantics, check `secret_flags` in every upload response. Call `revoke_artifact` (or `DELETE /api/v1/artifacts/<id>`) when it is non-empty.

## No MCP? Use REST

Any agent that can make HTTP calls can use the REST API instead. See the [API reference](/docs/api/).
