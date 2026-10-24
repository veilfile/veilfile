# Veilfile — MCP Registry Launch Checklist

Ship these once the production deploy is live (replace `https://veilfile.com`
with the real URL). Everything each registry needs: the repo URL, the hosted
endpoint `POST https://veilfile.com/mcp`, and `docs/llms.txt`.

## Reusable description

> Veilfile is private, ephemeral artifact hosting for AI agents. When a coding
> agent in CI or a cloud session produces a screenshot, log bundle, or HAR
> file for review, it needs a private URL to put it at — not a public repo.
> Agents upload with an API key and get back an unguessable, expiring link;
> uploads are secret-scanned on arrival, files auto-expire per plan TTL, and a
> hosted MCP server (`/mcp`) exposes upload, one-time upload URL, list, and revoke as tools.

## Registries

- [ ] **Official MCP registry** (`mcp-publisher`): install the publisher CLI,
      add a `server.json` to the repo (name `veilfile`, transport `streamable-http`,
      url `https://veilfile.com/mcp`, plus the description above and the repo URL),
      then run `mcp-publisher publish`. Keep `server.json` and `plugin/.claude-plugin/plugin.json` versions in sync with releases (`serverInfo.version` in `backend/drop_mcp/views.py` is `SERVER_VERSION`; update it in the same change).
      The name is `io.github.veilfile/veilfile`, in the `veilfile` organization's namespace. Check the
      registry's current rules for publishing under an organization's namespace before publishing, and
      prefer publishing from GitHub Actions.
- [ ] **Smithery**: connect the GitHub repo; add a `smithery.yaml` declaring the
      hosted endpoint (`https://veilfile.com/mcp`, type `streamable-http`) and the
      required `VEILFILE_API_KEY` (`vlf_...`) config; publish and verify the listing
      renders the four tools (`upload_artifact`, `create_upload_url`, `list_artifacts`, `revoke_artifact`).
- [ ] **mcp.so**: submit the listing via the site's form — name, description
      paragraph, repo URL, endpoint URL, category (Developer Tools), and tags
      (`artifacts`, `file-hosting`, `agents`).
- [ ] **PulseMCP**: submit via the site's listing flow with the same assets;
      confirm the server card links to `docs/llms.txt` for agent onboarding.
- [ ] **Claude Code plugin directory**: add a plugin entry pointing at the
      hosted `/mcp` endpoint with the description paragraph; document the
      `vlf_...` key setup in the plugin README as an env var reference
      (`VEILFILE_API_KEY`): the agent's tooling reads the value, the secret
      itself never enters a prompt.

## Pre-flight

- [ ] `POST https://veilfile.com/mcp` answers `initialize` with a real `vlf_` key.
- [ ] `docs/llms.txt` is served from the repo and referenced by every listing.
- [ ] No secrets in the repo; `vlf_` keys in examples are placeholders only.
