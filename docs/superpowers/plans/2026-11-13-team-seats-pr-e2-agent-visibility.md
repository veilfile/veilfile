# Team seats PR E2: Agents Choose Visibility, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent can choose, for each file it uploads, whether the uploader's team sees it, on the REST upload and both MCP upload tools; the choice is honored only when the administrator lets each person decide, and the answer says which visibility the file got.

**Architecture:** One optional parameter, `visibility`, with the values `only_me` and `team`, read only while the team seats switch is on. The upload service applies it under the workspace lock it already takes, in place of today's rule, and only when the team's setting is `members_choose`. An upload URL records the choice when it is minted, in a new field on `PendingUpload`, and its PUT applies it. The answers gain a `visibility` key while the switch is on. Published clients send no `visibility` and ignore the new key, so they keep working.

**Tech Stack:** Django 6.1, DRF, the MCP server in `backend/drop_mcp/`.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md`, line 54: "Agents choose visibility too. The upload API, the MCP upload tools and both CLIs take an optional visibility of "only me" or "team" for each file. It is honored only when the administrator lets members choose; otherwise the team's setting applies, and the response says which visibility the file got." The CLIs are E3.

## Questions for the owner

Each has a recommendation, and the plan proceeds on it.

1. **The values.** `only_me` and `team`, as the files store them and as the Files page shows them (recommended), or also the spelling "only me".
2. **With the switch off.** The parameter is ignored entirely, and every answer stays what `main` answers (recommended). The alternative checks it always, which would change a switch-off answer for a bad value.
3. **A migration.** `create_upload_url` records the choice on the upload URL, which needs one new field and migration 0022 (recommended). You would confirm it in Render's log, as with 0020 and 0021. The alternative puts the choice in the upload URL's query string, which needs no migration, but the URL's holder could then change it before the PUT.
4. **The words** below.

## Decisions

1. **Who may choose.** While the switch is on, a `visibility` of `only_me` or `team` is checked on every upload, and a bad value is refused as `on_secret`'s is. It is honored only when the workspace's `file_visibility` is `members_choose`, read under the upload's workspace lock. Otherwise the team's setting applies, as today: `always_team` gives `team`, and `always_only_me` gives `only_me`. With no value, a `members_choose` workspace gives `only_me`, as today. A one-person account is `members_choose`, so its choice is honored, which matters only once other people join.
2. **The answer.** While the switch is on, the REST 201 (`_artifact_response_body` at `drop_core/views.py:197`, which also serves the upload URL's PUT) and the MCP `upload_artifact` result gain `"visibility": "only_me"` or `"team"`, the value the file got. The `create_upload_url` result does not change, because nothing is stored yet. With the switch off, no answer changes.
3. **The service.** `upload_artifact` in `drop_core/services.py:334` and `mint_upload_url` at `:267` gain a keyword `visibility: str | None = None`. `upload_artifact` writes it at `:513` as decision 1 says. `mint_upload_url` stores it on `PendingUpload.visibility`, a `CharField(max_length=8, blank=True, default="")` where an empty value means no choice, and the PUT passes it on.
4. **Where it is read.** The REST upload reads the form field `visibility` beside `ttl_days` (`drop_core/views.py:433`). Both MCP tools read the argument `visibility`, check it beside `on_secret` (`drop_mcp/views.py:711` and `:788`) and add it to their `inputSchema`. The upload URL's PUT reads no query parameter for it.
5. **Left as they are.** The Files page already shows a file's visibility and lets its uploader change it under `members_choose`. The list answers (REST and MCP) keep their shape. The generic `invalid_request` sentence, shown only when a refusal has no detail, does not change.

## Words

Voice: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, American spelling.

- The `visibility` property's description, for both MCP tools and the REST docs: "Who on your team can see the file on their Files page: "only_me" or "team". It is honored only when your administrator lets each person decide; otherwise the team's setting applies, and the answer says which visibility the file got. The link works for anyone it is sent to either way."
- A bad value on REST (the `detail`, which is also the `error`): "The visibility value is not valid. Send "only_me" or "team"."
- A bad value over MCP (JSON-RPC `-32602`): "Send visibility as "only_me" or "team"."

## Global Constraints

- Switch-off answers do not change: every status, code, body and tool schema is what `main` answers.
- No existing JSON key, code, HTTP status or MCP error number changes. New keys are added only while the switch is on (AGENTS.md invariant 4).
- One migration, 0022, adds `PendingUpload.visibility`. No other model, setting or URL is added.
- `docs/API.md` quotes every MCP property description verbatim, as `drop_core/tests_fix_docs.py:480` requires.
- New test classes set `longMessage = False`. A new test module is registered in `READING_TEST_MODULES` in `pages/tests_site_rules.py`.
- Never edit `backend/legal/documents/`. Never read `.env` files or a secret value. Heavy checks run one at a time, after `memory_pressure | tail -1`.
- Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no Co-Authored-By, session link or model name. PRODUCT.md and DESIGN.md stay uncommitted. Nothing is pushed, opened or merged without the owner's go-ahead at that step.

## Review Focus

1. An administrator changes the setting from `members_choose` to `always_only_me` after an upload URL is minted with `team`: the PUT gives `only_me`, because the setting is read under the PUT's lock.
2. A published client that sends no `visibility` gets today's visibility and, with the switch off, today's bytes.
3. A bad value is refused before anything is stored or counted, on REST and both MCP tools.
4. A member and the administrator of the same `members_choose` team each get the visibility they asked for, and a teammate's Files page lists a `team` file.
5. The migration adds the field with a default, so upload URLs minted before the deploy still work.

---

### Task 1: The service applies a chosen visibility

**Files:**
- Modify: `backend/drop_core/models.py` (`PendingUpload` at 377), `backend/drop_core/services.py` (`mint_upload_url` at 267, `upload_artifact` at 334 and the write at 513)
- Create: `backend/drop_core/migrations/0022_pendingupload_visibility.py`
- Test: `backend/drop_core/tests_agent_visibility.py` (new), `ServiceVisibilityTests`

- [ ] **Step 1: Write the failing tests,** with the switch on, on a team with a member:
  - `test_under_members_choose_the_choice_is_honored`: with `subTest` over `only_me` and `team`, the stored file has that visibility.
  - `test_with_no_choice_members_choose_gives_only_me`.
  - `test_a_team_rule_wins_over_the_choice`: `always_team` with `only_me` gives `team`, and `always_only_me` with `team` gives `only_me`.
  - `test_a_bad_value_is_refused_before_anything_is_stored`: `public` raises `RejectedUpload` with `invalid_request` and the REST sentence as its detail, and no file or usage count is written.
  - `test_an_upload_url_carries_the_choice_to_its_put`: `mint_upload_url(..., visibility="team")` stores `team` on the `PendingUpload`, and an `upload_artifact` that passes it on stores a `team` file.
  - `test_the_setting_read_at_the_put_decides`: the URL is minted with `team`, the setting then becomes `always_only_me`, and the file gets `only_me`.
  - `test_with_the_switch_off_the_choice_is_ignored`: under `override_settings(TEAM_SEATS=False)`, `team` and `public` both give today's `only_me` with no error.
- [ ] **Step 2: Run them and see them fail.** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_agent_visibility`
- [ ] **Step 3: Add the field, `makemigrations drop_core`, and the keyword,** as decisions 1 and 3 say.
- [ ] **Step 4: Run the module, then `drop_core.tests_allowances`, `drop_core.tests_fix_upload` and `drop_core.tests_removal`, one at a time.** Expected: OK.
- [ ] **Step 5: Commit** "An upload can carry a chosen visibility, honored when the team lets each person decide".

### Task 2: The REST upload and the upload URL's PUT

**Files:**
- Modify: `backend/drop_core/views.py` (the upload at 407-466, `_artifact_response_body` at 197, the PUT at 493-570)
- Test: `backend/drop_core/tests_agent_visibility.py`, `RestVisibilityTests`

- [ ] **Step 1: Write the failing tests,** with the switch on:
  - `test_a_member_chooses_team_and_a_teammate_sees_the_file`: a member's key uploads with the form field `visibility=team`; the 201 body has `"visibility": "team"`; the administrator's `GET /api/v1/artifacts/` lists the file.
  - `test_the_answer_says_what_the_team_rule_gave`: under `always_only_me`, `visibility=team` answers 201 with `"visibility": "only_me"`.
  - `test_a_bad_value_answers_400`: `visibility=public` answers 400 with `code` `invalid_request` and the REST sentence as `error` and `detail`.
  - `test_the_put_answers_the_visibility_it_gave`: a PUT to an upload URL minted with `team` answers 201 with `"visibility": "team"`.
  - `test_with_the_switch_off_the_answer_is_mains`: under `override_settings(TEAM_SEATS=False)`, an upload with `visibility=public` answers 201, and the body's keys are exactly those `main` answers.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Read the field and add the key,** as decisions 2 and 4 say.
- [ ] **Step 4: Run the module, then `drop_core.tests_json_answers` and `drop_core.tests`, one at a time.** Expected: OK, with the exact-body tests unchanged.
- [ ] **Step 5: Commit** "The REST upload takes a visibility and says which one the file got".

### Task 3: The MCP upload tools

**Files:**
- Modify: `backend/drop_mcp/views.py` (the two `inputSchema`s at 137-193, `_tool_upload_artifact` at 695, `_tool_create_upload_url` at 777)
- Test: `backend/drop_core/tests_agent_visibility.py`, `McpVisibilityTests`

- [ ] **Step 1: Write the failing tests,** with the switch on:
  - `test_both_tools_list_the_visibility_property`: `tools/list` shows `visibility` with the description from Words on `upload_artifact` and `create_upload_url`, and neither lists it as required.
  - `test_upload_artifact_honors_the_choice_and_says_so`: `visibility: "team"` stores a `team` file, and the result has `"visibility": "team"`.
  - `test_create_upload_url_records_the_choice`: the minted `PendingUpload` has `visibility` `team`.
  - `test_a_bad_value_is_a_32602_on_both_tools`: `public` answers JSON-RPC `-32602` with the MCP sentence, and nothing is stored or minted.
  - `test_with_the_switch_off_the_tools_are_mains`: under `override_settings(TEAM_SEATS=False)`, `tools/list` has no `visibility` property, and an upload with `visibility: "public"` succeeds with `main`'s result keys.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Add the property while the switch is on, and read and check the argument,** as decisions 2 and 4 say.
- [ ] **Step 4: Run the module, then `drop_mcp`, one at a time.** Expected: OK.
- [ ] **Step 5: Commit** "Both MCP upload tools take a visibility, and the upload says which one the file got".

### Task 4: The docs, and the whole suite

**Files:**
- Modify: `docs/API.md` (the REST upload table at 35-40, the MCP tools at 604-635), `backend/docs/pages/api.md` (the parameters at 32-33) and `backend/docs/pages/agent.md` (19-21), `docs/llms.txt` (line 44), each with the `visibility` parameter, the description from Words where a property is quoted, and the new `visibility` key in the answers
- Modify: `AGENTS.md`, "People inside an account": one sentence on an agent's choice and when it is honored

- [ ] **Step 1: Write the doc sentences** in the voice rules.
- [ ] **Step 2: Run `drop_core.tests_fix_docs` and `pages`, one at a time.** Expected: OK.
- [ ] **Step 3: Run the whole suite once, then `makemigrations --check --dry-run`.** Expected: OK, and "No changes detected".
- [ ] **Step 4: Commit** "The docs say how an agent chooses a file's visibility".

### Task 5: The test card

- [ ] Write `~/dev/veilfile-test-cards/e2-local.md`, outside the repository, reusing `e1-env.sh` and `e1-tool.py`. It makes a team with a member, has the member upload with `curl` and `visibility=team`, shows the file on the administrator's Files page, and then, under each of the three settings on the Members page, shows the `visibility` the answer reports. Each step ends with a **Pass** line.

## Changed during the build

## Carried to E3 and F

- **E3:** both CLIs gain a visibility option, after the server, and the owner publishes their releases.
- **F:** the items carried by the C2, C3, D, D2 and E1 plans, including the one rehearsal on Postgres for D2 and F.
