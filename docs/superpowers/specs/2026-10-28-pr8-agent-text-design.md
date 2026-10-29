# PR 8: the words agents read, and tool annotations

- **Date:** 2026-10-28
- **Status:** scope approved by the owner (2026-10-28). The words were approved on 2026-10-29 and are in [the words file](../plans/2026-10-28-pr8-words.json), which [the plan](../plans/2026-10-28-pr8-agent-text.md) applies.
- **Parent specs:** [the PR 7 voice design](2026-10-23-site-redesign-pr7-voice-design.md), whose §11 named this PR: "the MCP server's tool descriptions and the API's error messages that agents read". Its rules (§2) apply here unchanged.

## 1. Intent

PR 7 put every page in the owner's voice. Agents read different words: the MCP server's tool descriptions, its titles and parameter descriptions, and the error messages the API and the MCP server send back. This PR writes those words in the same voice, and it adds the tool annotations that Anthropic's Connectors Directory requires before Veilfile can be listed.

An agent acts on these words, so they must be exact. A tool description says what the tool does and when to use it. An error says what happened and what to do next. Neither apologizes, and neither claims more than the code does.

## 2. Scope

The owner chose the scope on 2026-10-28.

**In scope:**

1. The four MCP tools' descriptions and parameter descriptions (`backend/drop_mcp/views.py`, `TOOLS`).
2. A title for each tool, in both the top-level `title` and `annotations.title`, with the same words. Clients that follow the 2025-06-18 specification read the first, and older clients read the second.
3. Tool annotations (§3).
4. Every Veilfile-written error text an API or MCP client receives:
   - MCP tool errors (`isError` results) and JSON-RPC error messages;
   - the `error` text (and `detail`, where it is sent) of every `{"error", "code"}` body in `drop_core` and `drop_billing`;
   - the device flow's and OAuth's `error_description` texts and dynamic client registration errors;
   - the library's default texts that `drop_core/errors.py` already wraps in the envelope ("Authentication credentials were not provided." and the like), reworded through a mapping in that handler.
5. The docs that quote these texts or describe these errors: `docs/API.md`, `backend/docs/pages/api.md` and `docs/llms.txt`.
6. In AGENTS.md: the "Releasing the CLIs" note about trusted publishing (left over from the repository move), and npm 0.4.1 in invariant 4's list of published clients.

**Out of scope:**

- Any behavior: status codes, error codes, response shapes, tool names, input schemas, URLs and headers stay as they are. Adding the titles and annotations is the only change to what the server sends besides words.
- Texts the dashboard shares with the API (`email_verification_required` in `drop_core/account.py` and the already-subscribed messages in `drop_billing/account.py`). The owner approved them in PR 7.
- The billing errors documented as bare codes (`billing_not_configured`, `invalid_plan`, `no_billing_account`), and Stripe's own message passed to API callers after "Stripe: ".
- Error codes and texts fixed by a standard (RFC 6749, RFC 7591, RFC 8628, JSON-RPC), and the library's own OAuth bodies that Veilfile does not write.
- HTML pages (PR 7), the Stripe webhook's plain-text bodies, `/healthz`, and the MCP server's name and version.

## 3. Annotations

Anthropic's review criteria require a title and the applicable hints on every tool. The specification's defaults are `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false` and `openWorldHint: true`, so every hint is written out.

| Tool | readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
|---|---|---|---|---|
| `list_artifacts` | true | false | true | false |
| `upload_artifact` | false | false | false | false |
| `create_upload_url` | false | false | false | false |
| `revoke_artifact` | false | true | true | false |

- The two upload tools only add: each call makes a new file or a new upload address and never changes an existing one. Repeating a call makes another, so neither is idempotent.
- `revoke_artifact` deletes a file for good. A second call with the same id finds nothing and changes nothing, so it is idempotent.
- No tool reaches anything outside Veilfile's own service, so `openWorldHint` is false for all four. A link that a tool returns can be opened by anyone who holds it, but the tool itself publishes nothing elsewhere.

## 4. How the words are written

1. The rules of the PR 7 voice design, §2, and the "Voice" section of AGENTS.md.
2. A tool description states what the tool does, what it returns and when to choose it over the others. It does not tell the model how to behave. The owner chose (2026-10-28) to state the secret scan as facts about the tool and its result; the result's `warning`, which an agent sees only when a file is flagged, keeps asking the agent to check with the user before sharing the link.
3. An error says what happened and what to do, in that order, with no apology. When the fix is a different value, the error names the allowed values or the limit.
4. A JSON-RPC error message is one short sentence, as the specification recommends.
5. Placeholders in a text stay, with the same meaning (for example the size limit in a too-large error).
6. One condition gets one wording. Where REST and MCP share a text (`ERROR_MESSAGES` in `drop_core/views.py`), it changes once, for both.
7. The words match the site's terms (a file, its link, an API key, a workspace, a plan) unless the API's field names require otherwise.

## 5. Constraints

- **Published clients keep working** (invariant 4). Clients branch on `code` and status; when they show the server's text, they print it as it is. No published client matches on an error's words, so the words can change while every code stays.
- For `invalid_request`, the published clients up to npm 0.2.0 and PyPI 0.3.0 print only `error`, so its text keeps carrying the detail (`drop_core/tests_fix_upload.py`).
- Tests that pin a text change with it, in the same commit, including the byte-for-byte pins in `tests_json_answers.py`.
- The docs tests check tool names, error codes and documented paths against the code. They do not compare quoted texts, so the docs are updated by hand to quote the approved words exactly.

## 6. Process

1. Draft every text in scope, with an independent check of each draft against the code and the rules.
2. The owner approves or edits each text on a review page that saves the choices.
3. A plan applies the approved words, task by task, with a review of each task and of the whole branch.
4. Muse's account opens the pull request and merges it after CI is green and the owner says so.

## 7. Non-goals, noted for later

- `ttl_days` of zero or less: the REST API raises it to one day, and the MCP tools reject it. The descriptions state each tool's real behavior, and the difference stays as it is.
- The OAuth resource identifier is `https://veilfile.com/mcp`, and the MCP registry manifest names `https://veilfile.com/mcp/`. The URL entered in the directory's portal must equal the resource exactly, so the submission uses `https://veilfile.com/mcp`.
- The directory submission also needs a reviewer test account with a verified email and at least three example prompts. Those are prepared outside the repository.
- Client ID metadata documents (CIMD) are not supported. Dynamic client registration is, and the directory accepts it.
