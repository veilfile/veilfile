# Team seats PR E1: What a Member's Agent Reads, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member of a team, and a member whose team is on Free, read sentences that fit them on the REST API, over MCP and on the OAuth consent page, instead of being told to upgrade a plan they cannot change.

**Architecture:** The JSON codes, the HTTP statuses and the MCP error numbers stay as they are, so published clients keep working; only the sentences change, and only while the switch is on. A plain member at a plan's cap reads a member sentence. A barred member's key or token still gets the 401, with a sentence that names the bar, found by a second look that never touches the credential. The consent page refuses a barred member before it offers Authorize.

**Tech Stack:** Django 6.1, DRF, django-oauth-toolkit, the MCP server in `backend/drop_mcp/`.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md`, with PR D's decision 16 (`docs/superpowers/plans/2026-11-12-team-seats-pr-d-billing.md:89`) and the E items carried by the C2, C3 and D plans.

## Questions for the owner

Each has a recommendation, and the plan proceeds on it.

1. **The 401's code.** Keep `unauthorized` for a barred member, with only the sentence changed (recommended), or add a new code. A new code could change how a published client handles the 401.
2. **The consent page for a barred member.** Refuse it with an error page that says why (recommended), or show the page with a warning and let Authorize mint a token that every call refuses until the team pays.
3. **The words** below.

## Decisions

1. **Who reads a member sentence.** The person the request acts as has an open `Membership` row with the role `MEMBER`, and `settings.TEAM_SEATS` is on. Everyone else, including the administrator of the same team, reads today's sentence. With the switch off, every answer is byte for byte what `main` answers.
2. **One helper chooses the sentence.** `cap_message(code: str, member: Membership | None) -> str` in `backend/drop_core/views.py` returns the member sentence for `monthly_cap_reached`, `storage_cap_reached` and `key_limit_reached` when decision 1 holds, and `ERROR_MESSAGES[code]` otherwise. `_upload_failure_response(exc, member=None)` in `drop_core/views.py:164` and `_service_failure(exc, member=None)` in `drop_mcp/views.py:610` pass the member the upload acted as. `create_key` in `drop_core/account.py` uses it for its 402. The text of `key_limit_reached` moves into `ERROR_MESSAGES`, unchanged.
3. **The barred 401.** After `APIKey.verify` and `resolve_oauth_credential` both refuse, `barred_credential(raw: str) -> bool` in `backend/drop_core/auth.py` says whether the credential would have been accepted but for the bar: a key that passes every check of `APIKey.verify` (`models.py:291`) except the `BARRED` exclusion and matches `BARRED`, or a token that passes every check of `resolve_oauth_credential` (`oauth.py:183`) except `is_barred`. It returns False before any query while the switch is off. It writes nothing: no `last_used_at`, no revoke. The REST 401 (`auth.py:47`) and the MCP 401 (`drop_mcp/views.py:549`) keep their status, code, `WWW-Authenticate` header and JSON-RPC `-32001`, and use the barred sentence when it returns True.
4. **The consent page.** `VeilfileAuthorizationView.dispatch` (`oauth.py:336`), after the email check and only with the switch on, answers a signed-in person whose `current_membership(user, reopen=False)` is barred with `_error_page` and status 403, for GET and POST alike. No grant or token is made.
5. **Left as they are.** The member allowance sentences, `team_on_free`, `member_removed` and `not_your_file` already fit a member. The dashboard's own cap sentences are not REST or MCP. The consent page for a member of a paid team keeps today's words. Visibility on upload is E2, and the CLIs are E3.

## Words

Voice: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, American spelling.

- `monthly_cap_reached`, for a member: "Your team has reached its plan's monthly upload cap. Uploads work again next month, or you can ask your administrator to choose a larger plan under Billing."
- `storage_cap_reached`, for a member: "Your team has reached its plan's storage cap. Revoke files you no longer need, or ask your administrator to choose a larger plan under Billing."
- `key_limit_reached`, for a member: "Your team has reached its plan's API key limit. Revoke a key, or ask your administrator to choose a larger plan under Billing."
- The 401 for a barred member's key or token, on REST and MCP: "Your team is on the Free plan, so its members' API keys and connected apps are paused. Ask your administrator to choose a paid plan under Billing."
- The consent page for a barred member: the title "Your team is on the Free plan", and the message "An app you connect now could not upload or read anything, because your team is on the Free plan. Ask your administrator to choose a paid plan under Billing, then connect the app again."

## Global Constraints

- Switch-off answers do not change: every status, code, header and sentence is what `main` answers.
- No JSON code, HTTP status or MCP error number changes, because published clients key on them (AGENTS.md invariant 4).
- No model, migration, setting or URL is added. Nothing contacts Stripe.
- New test classes set `longMessage = False`. A new test module is registered in `READING_TEST_MODULES`, as `drop_billing.tests_paid_join` is.
- Never edit `backend/legal/documents/`. Never read `.env` files or a secret value.
- Heavy checks run one at a time, after `memory_pressure | tail -1`.
- Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no Co-Authored-By, session link or model name. PRODUCT.md and DESIGN.md stay uncommitted. Nothing is pushed, opened or merged without the owner's go-ahead at that step.

## Review Focus

1. A member and the administrator of the same paid team at the same cap read different sentences with the same code, on REST and MCP.
2. With the switch off, the cap answers and the 401 are byte for byte what `main` answers.
3. A removed person's key, a revoked key and an unknown key still get today's 401 sentence.
4. The barred check leaves the key untouched, and the same key works again once the team pays.
5. A barred member's POST of Authorize makes no grant.

---

### Task 1: The member sentences at a plan's cap

**Files:**
- Modify: `backend/drop_core/views.py` (`ERROR_MESSAGES` at 101, `cap_message`, `_upload_failure_response` at 164 and its four callers)
- Modify: `backend/drop_mcp/views.py` (`_service_failure` at 610 and its callers)
- Modify: `backend/drop_core/account.py` (the `KeyCapReached` answer at 207-213)
- Test: `backend/drop_core/tests_member_words.py` (new), `MemberCapTests`

- [ ] **Step 1: Write the failing tests.** On a paid team with a member, with the switch on:
  - `test_a_member_at_the_monthly_cap_reads_the_member_sentence_on_rest_and_mcp`: the REST upload answers 429 with `code` `monthly_cap_reached` and the member sentence, and the MCP `upload_artifact` tool error has the same code and sentence.
  - `test_a_member_at_the_storage_cap_reads_the_member_sentence_on_rest_and_mcp`: the same for 402 `storage_cap_reached`.
  - `test_a_member_at_the_key_limit_reads_the_member_sentence`: `create_key` answers 402 `key_limit_reached` with the member sentence.
  - `test_the_administrator_reads_todays_sentences`: the administrator of the same team, at each cap, reads `ERROR_MESSAGES[code]` as on `main`.
  - `test_with_the_switch_off_the_sentences_are_mains`: under `override_settings(TEAM_SEATS=False)`, a one-person account at each cap reads `main`'s text, compared to literals copied from `main`.
- [ ] **Step 2: Run them and see the member tests fail.** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_member_words`
- [ ] **Step 3: Add `cap_message`, and pass the member** through `_upload_failure_response`, `_service_failure` and `create_key`, as decision 2 says.
- [ ] **Step 4: Run the module, then `drop_core`, `drop_mcp` and `drop_core.tests_json_answers`, one at a time.** Expected: OK. The tests that pin `main`'s literal text still pass, because the administrator's sentence is unchanged.
- [ ] **Step 5: Commit** "A member at a plan's cap reads that their administrator can choose a larger plan".

### Task 2: The 401 for a barred member

**Files:**
- Modify: `backend/drop_core/auth.py` (`barred_credential`, the 401 at 47-51), `backend/drop_mcp/views.py` (`_authenticate` at 259, `_unauthenticated` at 549)
- Test: `backend/drop_core/tests_member_words.py`, `BarredCredentialTests`

- [ ] **Step 1: Write the failing tests,** on a team on Free with a member who has a key and an OAuth token, with the switch on:
  - `test_a_barred_members_key_gets_the_barred_sentence_on_rest`: 401, `code` `unauthorized`, `WWW-Authenticate: Bearer`, and the barred sentence.
  - `test_a_barred_members_token_gets_the_barred_sentence_on_mcp`: 401 with JSON-RPC `-32001` and the barred sentence.
  - `test_other_refused_credentials_keep_todays_sentence`: with `subTest`, a removed member's key, a revoked key and an unknown key get today's REST sentence.
  - `test_the_check_leaves_the_key_untouched_and_it_works_once_the_team_pays`: after the 401, the key's `last_used_at` and revoked state are unchanged. With the team's plan set to Team, the same key is accepted.
  - `test_with_the_switch_off_the_check_asks_nothing`: under `override_settings(TEAM_SEATS=False)` and `assertNumQueries(0)`, `barred_credential` returns False.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Add `barred_credential`, and use its answer** in the REST and MCP 401s, as decision 3 says.
- [ ] **Step 4: Run the module, then `drop_core.tests_bar`, `drop_core.tests` and `drop_mcp`, one at a time.** Expected: OK.
- [ ] **Step 5: Commit** "A barred member's key or app gets a 401 that says the team is on Free".

### Task 3: The consent page refuses a barred member

**Files:**
- Modify: `backend/drop_core/oauth.py` (`VeilfileAuthorizationView.dispatch` at 336)
- Modify: `site-tests/make_form_fixtures.py` (a member of a team on Free, and that member's session), `site-tests/run_site_checks.py` (a form state `oauth-barred` beside `oauth-unverified`), `backend/pages/tests_form_fixtures.py` (the new fixture key)
- Test: `backend/drop_core/tests_oauth_consent.py`, `BarredConsentTests`

- [ ] **Step 1: Write the failing tests,** with the switch on:
  - `test_a_barred_member_is_told_why_instead_of_offered_authorize`: GET answers 403 with the title and message from Words, and no Authorize button.
  - `test_a_barred_members_post_of_allow_makes_no_grant`: POST with `allow` answers 403, and no grant or access token exists afterwards.
  - `test_a_member_of_a_paid_team_sees_todays_page`: 200 with today's page.
  - `test_with_the_switch_off_the_page_is_mains`: under `override_settings(TEAM_SEATS=False)`, the person sees today's page.
- [ ] **Step 2: Run them and see them fail.** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_oauth_consent`
- [ ] **Step 3: Add the check to `dispatch`,** as decision 4 says, and add the form state and its fixture.
- [ ] **Step 4: Run the module and `pages.tests_form_fixtures`.** Expected: OK. Then rebuild the form fixtures and run the forms part of the site checks for the new state, as AGENTS.md describes for `site-forms`.
- [ ] **Step 5: Commit** "The consent page tells a member whose team is on Free why it cannot connect an app".

### Task 4: The docs, and the whole suite

**Files:**
- Modify: `docs/API.md` (the `storage_cap_reached` row at 724, the 401 row at 723, the `monthly_cap_reached` row at 737) and `backend/docs/pages/api.md` (its matching rows at 88 and 94), with one sentence each saying what a member reads
- Modify: `docs/llms.txt` (lines 63 and 139-141): an agent of a team member tells the person to ask their administrator, not to upgrade
- Modify: `AGENTS.md`, "People inside an account": one sentence on the member sentences, the barred 401 and the consent page

- [ ] **Step 1: Write the doc sentences** in the voice rules.
- [ ] **Step 2: Run `drop_core.tests_fix_docs` and `pages`, one at a time.** Expected: OK.
- [ ] **Step 3: Run the whole suite once, then `makemigrations --check --dry-run`.** Expected: OK, and "No changes detected".
- [ ] **Step 4: Commit** "The docs say what a team member's agent reads at a cap and when the team is on Free".

### Task 5: The test card

- [ ] Write `~/dev/veilfile-test-cards/e1-local.md`, outside the repository. It runs the server locally with the switch on and no Stripe key, makes a team with a member by hand, and has the owner read each sentence with `curl` and in the browser: a member at the key limit, a barred member's key, and the consent page for a barred member. Each step ends with a **Pass** line.

## Changed during the build

- `cap_message` lives in a new module, `backend/drop_core/cap_words.py`, and takes the sentence everyone else reads as a third argument, `cap_message(code, member, message)`. `views.py` imports `account.py`, so `account.py` cannot import `views.py`, and the text of `key_limit_reached` stays in `account.py`.
- The key and token checks were split, so that `APIKey.is_barred_key` and `oauth.token_is_barred` reuse every check of `APIKey.verify` and `resolve_oauth_credential` except the bar, instead of copying them.
- The browser form checks for the new `oauth-barred` state run in CI's site-forms check, which runs the site with the switch on.
- The review corrected AGENTS.md, which said that no JSON answer changes with the switch on or off. Its minor findings are carried below.

## Carried to E2, E3 and F

- **E2:** agents choose visibility on the REST upload and both MCP upload tools, honored only under `members_choose`, with `docs/API.md`, `docs/llms.txt` and `backend/docs/pages/`.
- **E3:** both CLIs gain the visibility option, after the server, and the owner publishes their releases.
- **F:** the items carried by the C2, C3, D and D2 plans, including the one rehearsal on Postgres for D2 and F.
- **Later, from E1's review:** tests for the member sentence on an upload URL's PUT and on MCP `create_upload_url` at a cap, and switch-off tests for the MCP 401, a member's storage cap and a barred token on REST. The barred check could skip the token lookup for a `vlf_` string. The device poll's key-limit sentence (`backend/drop_core/device.py:508`) still says "workspace" to a member.
