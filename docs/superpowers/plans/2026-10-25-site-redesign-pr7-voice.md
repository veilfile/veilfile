# Site redesign PR 7, the voice pass and launch: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the owner's approved words on every page the sweep found, hold the new rules with tests, write the Voice section of AGENTS.md, and launch the redesign through one pull request on `veilfile/veilfile`.

**Architecture:** The approved words are data, not prose to retype. `2026-10-25-site-redesign-pr7-words.json` (next to this plan) holds 220 items, each with its file, its exact old text and its exact new text, and `2026-10-25-site-redesign-pr7-apply.py` applies one group of them, refusing any item whose old text does not occur exactly once. Each words task first changes the tests that pin old words to the new words (they fail), then applies its group (they pass). Two later tasks add the tests the design asks for and the AGENTS.md changes; the last task runs everything and opens the pull request.

**Tech Stack:** Django 6.1 templates and views, Markdown pages read by `backend/docs` and `backend/blog`, plain-text `docs/llms.txt`, Python `unittest` through Django's runner, the Playwright browser checks in `site-tests/`.

**Spec:** `docs/superpowers/specs/2026-10-23-site-redesign-pr7-voice-design.md` (the PR 7 design; it expands the parent spec `docs/superpowers/specs/2026-10-08-site-redesign-design.md`, sections 5, 7, 10 and 11).

## Global Constraints

- **The words are final.** Apply them only with the apply script, never by retyping. Change no word the words file does not change. The only edits outside it are: test assertions changed to the new words, the tests and AGENTS.md text this plan names, and nothing else. If a test or a check seems to need a word the owner did not approve, stop and report it.
- **Rules the words already follow (design section 2):** every sentence complete; a semicolon only between two independent clauses; labels exempt; no heading ends with a full stop, and a heading ends with a question mark only when it is a complete question; llms.txt keeps "Label: value" data terse; no em dashes, no exclamation marks, none of the words in `BANNED` (`backend/pages/tests_chapters.py`); American spelling; `’` for apostrophes in templates; errors say what happened and what to do, with no apology; no claim the product does not back.
- **Constraints (design section 12):** search descriptions 70 to 160 characters and titles at most 60 (`backend/pages/tests_registry.py`); price figures exactly as they are (`backend/drop_core/tests_fix_docs.py`); Begin's client snippets equal `backend/docs/pages/agent.md` (`backend/pages/snippets.py`); every character exists in all three fonts (`backend/pages/tests_content.py`); no phrase the CLIs print or match changes; the forms' and approval pages' URLs, form fields, hidden fields, the `GET /device`, the `POST /device/approve/`, the Approve and Deny forms, the OAuth consent's "unverified app" framing and `X-Frame-Options: DENY` do not change.
- **Tests:** a test that pins old words changes to the new words in the same task as the words. Never delete, skip or loosen an assertion; change only its expected text. Never delete or skip a check.
- **Where and how:** work in a clone of `veilfile/veilfile` at `~/dev/veilfile`, branch `voice/pr7`, never in a checkout under `~/Desktop` (iCloud copies files there). Its local git identity is `Veilfile <support@veilfile.com>`; commit with `TZ=UTC git commit`; no attribution lines (no Co-Authored-By, session links or model names); the pre-commit identity gate runs on every commit and must pass, so never use `--no-verify`.
- **Commands:** backend tests run as `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <modules>`; the CLIs as `cd npm && node --test` and `cd cli && ../venv/bin/python -m unittest discover -s tests -t .`; the browser checks as AGENTS.md "Site checks" describes, on port 8777.
- **Outward steps wait for the owner:** the push, the pull request, and the merge each happen only after the owner says so in chat, and only as Muse's account (`gh api user --jq .login` prints `yourimaginationwillbefired`).

## Review Focus

1. **A whole-template rewrite breaks the approval flow's contract.** Seven approval templates are replaced whole. A reasonable person expects every template tag, variable, form and field to survive. Task 4 checks that each rewritten template keeps exactly its `{% %}` tags, `{{ }}` variables and `name="..."` fields.
2. **A fixed wrong claim comes back in a later edit.** Design section 4 lists fourteen claims that said more than the product does. Task 6 pins each fix with a test that fails if the old claim returns.
3. **The registry's copy of the Docs index description drifts from the page's front matter.** The two must match word for word (F013 and F140), and no test holds them. Task 2 adds one.
4. **PR 6's deferred dashboard fixes regress.** "1 uploads", a plan name in lowercase in the over-limit heading, and British "Cancelled" are fixed by F100, F097 and F074. Task 5 pins each.
5. **A heading filled in at run time ends with a full stop on a public page.** A template scan cannot see a heading whose words come from a view. Task 6 renders every public page in the registry and checks its headings and its `<title>`.

---

### Task 1: Chapters and search descriptions

**Files:**
- Modify: `backend/pages/install.py`, `backend/pages/registry.py`, `backend/templates/blog/index.html`, `backend/templates/chapters/{about,begin,how,plans,promises,threshold}.html` (all through the apply script)
- Test: `backend/pages/tests_install_line.py`, `backend/pages/tests_registry.py`, `backend/pages/tests_chapters.py`, `backend/blog/tests_journal.py`, `backend/drop_core/tests_fix_docs.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: the words file and the apply script (committed with this plan).
- Produces: the chapters group applied, including `X-chapters-rule4-threshold` (the threshold's `<h1>` loses its full stop under design rule 4, with no new words).

- [ ] **Step 1: List the pins.** Run `python3 -I docs/superpowers/plans/2026-10-25-site-redesign-pr7-apply.py chapters --pins`. The drafters also found these: F001 and F002/F003 in `tests_install_line.py:35-36`; F006 (the price range "Free to $12/mo") in `tests_fix_docs.py:86`; F008 in `tests_registry.py:54`; F010 in `tests_fix_docs.py:184`; F020 in `tests_journal.py:124` and its message; F021 (About's heading) in `tests_chapters.py:91` and `run_site_checks.py:3344`, `:4370`, `:6217`; F028 in `tests_chapters.py:136`; F033 and the two Promises `dt` items in `tests_chapters.py:106-109`; the threshold heading in `tests_chapters.py:258`.
- [ ] **Step 2: Change each pinned assertion to the item's new words** (look each item up by id in the words file). For F006, the special case in `tests_fix_docs.py:86` follows the new landing description ("Plans cost $0 to $12 a month."), so the scan still finds no plan figure there.
- [ ] **Step 3: Run the tests and see them fail on the new words.** `... manage.py test pages.tests_install_line pages.tests_registry pages.tests_chapters blog.tests_journal drop_core.tests_fix_docs`. Expected: failures only where Step 2 changed an assertion.
- [ ] **Step 4: Apply the group.** `python3 -I docs/superpowers/plans/2026-10-25-site-redesign-pr7-apply.py chapters`. Expected: `chapters: 36 of 36 applied; 0 covered by a whole rewrite; 0 unchanged`.
- [ ] **Step 5: Run the same tests and the whole `pages` app.** Expected: OK.
- [ ] **Step 6: Commit.** `git add -A backend site-tests && TZ=UTC git commit -m "Chapters and search descriptions in complete sentences, with exact claims"`.

### Task 2: Docs

**Files:**
- Modify: `backend/docs/pages/{agent,api,how-it-works,index,quickstart}.md`, `backend/pages/registry.py` (F013, the registry's copy of the Docs index description)
- Test: `backend/drop_core/tests.py`, `backend/pages/tests_docs_reading.py`, `backend/drop_core/tests_site_pages.py`, new test in `backend/pages/tests_registry.py`

**Interfaces:**
- Consumes: Task 1's tree.
- Produces: the docs group applied, including the new `## Claude apps` section in `agent.md` (inserted before `## When a secret is flagged`) and F013 with F140 word for word.

- [ ] **Step 1: Write the drift test.** In `backend/pages/tests_registry.py`, add `test_the_docs_index_description_matches_its_front_matter`: read the front matter `description:` line of `backend/docs/pages/index.md` and the registry `Page` whose `url_name` is the Docs index's, and `assertEqual` the two, with the message `"backend/pages/registry.py and backend/docs/pages/index.md: the Docs index description must be the same words in both"`.
- [ ] **Step 2: Change the pins to the new words.** `--pins` for the group, plus F143 in `backend/drop_core/tests.py:1026` and `backend/pages/tests_docs_reading.py:198` (they assert the owner's "If you're new here, start with the"), and F151 in `backend/drop_core/tests_site_pages.py:70`.
- [ ] **Step 3: Run and see the failures.** `... manage.py test drop_core.tests drop_core.tests_site_pages pages.tests_docs_reading pages.tests_registry`. Expected: the pinned assertions fail; the drift test passes (both copies still hold the old words).
- [ ] **Step 4: Apply the group.** Expected: `docs: 42 of 42 applied; 0 covered by a whole rewrite; 1 unchanged`.
- [ ] **Step 5: Run the same modules, `pages.tests_chapters` (it holds the check that Begin's snippets equal agent.md, lines 196-217) and `pages.tests_content`.** Expected: OK. The snippets still match, because the new section adds no code block.
- [ ] **Step 6: Commit.** `TZ=UTC git commit -m "Docs in complete sentences, descriptions that match their pages, and the Claude apps steps"`.

### Task 3: The Journal and llms.txt

**Files:**
- Modify: `backend/blog/feeds.py`, `backend/blog/posts/introducing-veilfile.md` (its body is replaced whole; the front matter is not touched), `docs/llms.txt`
- Test: `backend/blog/tests_feed.py`, `backend/drop_core/tests.py`, `backend/drop_core/tests_fix_docs.py`, `backend/pages/tests_site_rules.py`

**Interfaces:**
- Consumes: Task 2's tree.
- Produces: the journal-llms group applied. The 17 item edits inside the post are `covered` by `X-journal-llms-journal-full`, which already contains each of them.

- [ ] **Step 1: Change the pins.** F035, the feed description, in `backend/blog/tests_feed.py:68`. `backend/drop_core/tests.py:972-974` pins only "Agents stash files constantly.", which stays.
- [ ] **Step 2: Run and see `tests_feed` fail.**
- [ ] **Step 3: Apply the group.** Expected: `journal-llms: 27 of 27 applied; 17 covered by a whole rewrite; 0 unchanged`.
- [ ] **Step 4: Run** `blog drop_core.tests drop_core.tests_fix_docs pages.tests_site_rules pages.tests_content`. Expected: OK. `tests_fix_docs` now also reads "Free ... 100 uploads" from llms.txt, and the figure equals `PLANS`.
- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "The Journal post and llms.txt in complete sentences, with exact claims and a date for the incident"`.

### Task 4: Sign-in forms, the approval pages and the error pages

**Files:**
- Modify: `backend/drop_core/{email_verification,oauth,throttle,views}.py`, `backend/pages/errors.py`, `backend/pages/catalog.py` (F195 only), `backend/templates/{403,403_csrf,404}.html`, `backend/templates/device/*.html`, `backend/templates/device/includes/grant_summary.html`, `backend/templates/oauth/authorize.html`, `backend/templates/oauth/includes/grant_summary_oauth.html`, `backend/templates/registration/{checkout_confirm,login,signup,verify_email,verify_email_invalid}.html`, `backend/templates/registration/email_change_notice_email.txt`
- Test: the modules in Step 1

**Interfaces:**
- Consumes: Task 3's tree.
- Produces: the forms-device-errors group applied. Six items are `covered` by the whole-template rewrites of `grant_summary.html` (F069, F070, F071), `device_success.html` (F068), `device_denied.html` (F067) and `grant_summary_oauth.html` (F072).

- [ ] **Step 1: Change the pins.** Run `--pins`. The drafters also found pins for these items:
  - F054: `tests_fix_auth.py:638` and `tests_sign_in_pages.py:845`.
  - F055: `tests_throttled_pages.py:109`.
  - F059 and F195: `tests.py:2056`, `tests_sign_in_pages.py:42` and `tests_forms.py:274`, `:454-455`, `:727`.
  - F063 and F064: `tests_reset_verify_pages.py:53`, `:116`, `:441`.
  - F065: `tests_oauth_consent.py:325`, `:339`, `:356`.
  - F067 to F072: `tests_device_pages.py:462-467`, `:582`, `:594`, `tests_fix_device.py:386`, `:744` and `tests_oauth_consent.py:188`.
  - F103 to F111: `pages/tests_errors.py`, `pages/tests_artifact_miss.py`, `dashboard/tests_keys.py:651`, `:814`, `:840`, `:1043` and `dashboard/tests_app.py:108`.

  F061 is "unchanged", so its pins stay.
- [ ] **Step 2: Record the approval templates' contract.** For each of `device_enter_code.html`, `device_consent.html`, `device_success.html`, `device_denied.html`, `device_error.html`, `includes/grant_summary.html`, `oauth/authorize.html` and `oauth/includes/grant_summary_oauth.html`, save `grep -o -E '\{%[^%]*%\}|\{\{[^}]*\}\}|name="[^"]*"' <file> | sort` to a scratch file (not in the repository).
- [ ] **Step 3: Run the step 1 modules and see them fail on the new words.** The modules are `drop_core.tests drop_core.tests_fix_auth drop_core.tests_sign_in_pages drop_core.tests_throttled_pages drop_core.tests_reset_verify_pages drop_core.tests_oauth_consent drop_core.tests_device_pages drop_core.tests_fix_device pages.tests_forms pages.tests_errors pages.tests_artifact_miss dashboard.tests_keys dashboard.tests_app`.
- [ ] **Step 4: Apply the group.** Expected: `forms-device-errors: 31 of 31 applied; 6 covered by a whole rewrite; 2 unchanged`.
- [ ] **Step 5: Check the contract.** Rerun Step 2's command for each template, and `diff` each result with its saved one. Expected: no output for every file, except changes that only reorder the same tags. If a tag, variable or field is missing or new, stop and report it.
- [ ] **Step 6: Run the step 3 modules and `pages.tests_content`.** Expected: OK.
- [ ] **Step 7: Commit.** `TZ=UTC git commit -m "Sign-in forms, approval pages and error pages in complete sentences; the device grant says exactly what a key can do"`.

### Task 5: The dashboard, the frames and the catalog

**Files:**
- Modify: `backend/dashboard/{billing_page,keys,views}.py`, `backend/pages/catalog.py`, `backend/templates/components/file_list.html`, `backend/templates/dashboard/{app_disconnect,apps,billing,billing_switch,file_revoke,files,key_revoke,keys,over_limits,overview}.html`, `backend/templates/pages/catalog.html`, `backend/templates/robots.txt`
- Test: `backend/dashboard/tests_billing.py`, `backend/dashboard/tests_overview.py`, the modules in Step 1

**Interfaces:**
- Consumes: Task 4's tree.
- Produces: the dashboard-frames group applied. The four confirmation headings are complete questions: F084 ("Do you want to disconnect {{ app.name }}?"), F089, F092 and F094. F083's words are in `dashboard/overview.html`'s include call, which the words file names.

- [ ] **Step 1: Write the three regression tests.** In `backend/dashboard/tests_billing.py`: `test_a_canceled_subscription_reads_canceled` asserts the status label for `"canceled"` is `"Canceled"` and that `"Cancelled"` appears nowhere on the billing page. Beside the existing over-limit tests (`backend/dashboard/tests_billing.py` and `backend/dashboard/tests_overview.py` both render `over_limits.html`; use the one whose fixtures set a plan over its limits), add two tests. `test_the_over_limit_heading_names_the_plan_with_a_capital` asserts the heading prints `Team plan's limits`, not `team plan's limits`. `test_one_upload_is_singular` asserts `1 upload this month`, never `1 uploads`, and `2 uploads this month` for two.
- [ ] **Step 2: Change the pins.** Run `--pins`. The drafters also found pins for these items:
  - F073 to F077: `tests_billing.py:69`, `:73`, `:76`, `:188-189`, `:291-292`, `:361`, `:836`, `:847` and `:865`, and `run_site_checks.py:9864-9865`.
  - F078 and F079: `tests_keys.py:799`, `:807`, `:976`, `:993`, `:1002`, and `run_site_checks.py:9638-9652`.
  - F080 and F081: `tests_app.py:33` and `tests_files.py:470`, `:684-729`, and `run_site_checks.py:9639`.
  - F083 and F199: `tests_overview.py:140`.

  `tests_meter.py:115-116` passes its own note to the component, so it stays.
- [ ] **Step 3: Run and see the failures.** The modules are `dashboard pages.tests_meter pages.tests_forms drop_core.tests_sign_in_pages drop_core.tests_reset_verify_pages`. Expected: the Step 1 tests and the changed pins fail.
- [ ] **Step 4: Apply the group.** Expected: `dashboard-frames: 54 of 54 applied; 0 covered by a whole rewrite; 4 unchanged`.
- [ ] **Step 5: Run the same modules.** Expected: OK.
- [ ] **Step 6: Commit.** `TZ=UTC git commit -m "Dashboard, frames and catalog in complete sentences; confirmation headings ask a complete question"`.

### Task 6: The voice tests

**Files:**
- Create: `backend/pages/tests_voice.py`

**Interfaces:**
- Consumes: Tasks 1 to 5 (every heading now ends without a full stop; the Markdown pages break no voice rule; the fourteen claims are fixed).
- Produces, in `backend/pages/tests_voice.py`:
  - `template_text(source: str) -> str`: the source with every `{% %}`, `{{ }}` and `{# #}` removed and line breaks kept.
  - `html_headings(html: str) -> list[tuple[int, str]]`: (line, visible text with whitespace collapsed) for every `h1` to `h6`, `dt` and `title` element, using `html.parser`.
  - `markdown_headings(text: str) -> list[tuple[int, str]]`: (line, text) for every line matching `^#{1,6} ` outside fenced code.
  - `ends_with_full_stop(text: str) -> bool`: true when the stripped text ends with `.` but not `...`.

- [ ] **Step 1: Write the helper tests (they fail: the module has no helpers yet).**

```python
class HelperTests(SimpleTestCase):
    def test_the_full_stop_rule(self):
        self.assertTrue(ends_with_full_stop("Nothing here."))
        self.assertFalse(ends_with_full_stop("Nothing here"))
        self.assertFalse(ends_with_full_stop("Why shouldn’t I use a gist?"))
        self.assertFalse(ends_with_full_stop("Wait..."))

    def test_headings_are_found_with_template_tags_removed(self):
        html = template_text('<h1 class="x">Switch to {{ plan }}.</h1>\n<dt>Why?</dt>')
        self.assertEqual(html_headings(html), [(1, "Switch to ."), (2, "Why?")])

    def test_markdown_headings_skip_fenced_code(self):
        self.assertEqual(markdown_headings("# A\n```\n# not\n```\n## B.\n"), [(1, "A"), (5, "B.")])
```

- [ ] **Step 2: Run `pages.tests_voice` and see it fail.** Expected: an ImportError or NameError for the helpers.
- [ ] **Step 3: Implement the four helpers** with the signatures above.
- [ ] **Step 4: Add the repository tests.** Every failure message names the file, the line, the text and the fix.
  - `HeadingStopTests.test_templates` runs over every `backend/templates/**/*.html`, through `template_text`, with the message `f"{path}:{line}: the heading {text!r} ends with a full stop; drop it, since a heading is a label"`.
  - `test_markdown_pages` runs over `backend/docs/pages/*.md` and `backend/blog/posts/*.md`.
  - `test_registry_titles` checks every `PAGES` title.
  - `test_rendered_public_pages` fetches `reverse(page.url_name)` for each `PAGES` entry with the test client, expects 200, and checks every heading and the `<title>` of the response.
  - `MarkdownVoiceTests.test_no_em_dash_exclamation_mark_or_banned_word` runs over `backend/docs/pages/*.md`, `backend/blog/posts/*.md` and `docs/llms.txt`. It skips fenced and inline code and the `![` of an image, and uses `BANNED` from `pages.tests_chapters`.
  - `WrongClaimTests.test_the_fourteen_claims_stay_fixed` asserts each old phrase in the table below is absent from its file. It also asserts that `docs/llms.txt` contains `apparent child sexual exploitation`.

| File | Old phrase that must not return |
|---|---|
| `backend/templates/device/includes/grant_summary.html` | `Disconnect every app` |
| `backend/templates/device/includes/grant_summary.html` | `revoke any of them` |
| `backend/docs/pages/api.md` | `Every Veilfile endpoint` |
| `backend/templates/chapters/how.html` | `anything shaped like a secret` |
| `backend/templates/chapters/promises.html` | `anything shaped like a key or a token` |
| `backend/templates/chapters/promises.html` | `We look only for the shapes of secrets` |
| `backend/pages/registry.py` | `Updated twice a month` |
| `backend/blog/posts/introducing-veilfile.md` | `issued per agent` |
| `backend/blog/posts/introducing-veilfile.md` | `not by extension` |
| `backend/blog/posts/introducing-veilfile.md` | `required on every account` |
| `backend/docs/pages/agent.md` | `key rotation, and instant revocation` |
| `backend/docs/pages/index.md` and `backend/pages/registry.py` | `full REST and MCP API reference` |
| `backend/docs/pages/how-it-works.md` | `Nothing caches the bytes` |
| `docs/llms.txt` | `go to NCMEC` |

- [ ] **Step 5: Run `pages.tests_voice`.** Expected: OK. A failure here means a word the words file did not cover. If it is a full stop on a heading, drop the full stop under design rule 4, add the change to the commit, and report it. Anything else, stop and report.
- [ ] **Step 6: Commit.** `TZ=UTC git commit -m "Tests that hold the voice rules: no heading ends with a full stop, the Markdown pages keep the voice, and the fixed claims stay fixed"`.

### Task 7: AGENTS.md

**Files:**
- Modify: `AGENTS.md`
- Test: the modules that read AGENTS.md, listed in Step 4

**Interfaces:**
- Consumes: the final words (Tasks 1 to 5) and the tests (Task 6).
- Produces: a `## Voice` section, recipes that point to it, current pinned-sentence lists, and a new recipe `### Change a color, size or spacing`.

- [ ] **Step 1: Write the `## Voice` section, just before `## Site recipes`.** State design section 2's seven rules in one numbered list, in full sentences. Under each rule, give one before-and-after pair taken from this PR:
  - Rule 1, a fragment: "Fine for a snippet; wrong for a client’s failing screenshot." becomes "A gist suits a snippet but not a client’s failing screenshot."
  - Rule 1, a clipped question: "No account?" becomes "Do you need an account?"
  - Rule 4, a heading: "Nothing here." becomes "Nothing here"; a confirmation heading becomes a complete question, as in "Do you want to revoke this API key?"
  - Rule 5, a status line: "Key revoked." becomes "The key is revoked."
  - Rule 6, llms.txt: "Max upload size: 25 MiB" stays terse, while a sentence that explains or warns becomes complete.
  - Rule 7, a claim: "revoke any of them" becomes "revoke only the key in use".

  Then add two sentences. The first: a test holds rule 4 and the Markdown voice (`backend/pages/tests_voice.py`), and review holds the rest. The second: every team writes words, so the section names no single owner.
- [ ] **Step 2: Point the recipes to it.**
  - "Edit a chapter's words" step 3 becomes one sentence that sends readers to `## Voice` and keeps "Use `’` for apostrophes."
  - Every other recipe that repeats the voice rules, or cites "step 3 of \"Edit a chapter's words\"", points to `## Voice` instead. These are "Edit Docs" step 5, "Add or change a form page" step 6, "Write a Journal post" and "Add or change a dashboard section". Find them with `grep -n "step 3 of\|em dash\|banned" AGENTS.md`.
- [ ] **Step 3: Bring the pinned-sentence lists up to date.** In "Edit a chapter's words" step 5, and in any other recipe that lists sentences tests pin, replace each old sentence with the words the tests pin now. Examples are About's "A quiet place for what your agents make", Promises' "The link is the only way in" and "Why shouldn’t I use a gist?", Plans' note, and the install line's words. A listed sentence that no test pins any more comes off the list.
- [ ] **Step 4: Add `### Change a color, size or spacing`** under `## Site recipes`, after "Change or add a component". Give it three steps:
  1. Change only `backend/static/css/tokens.css`. A colour token is defined twice, under `:root` for Paper and under the dark selectors for Ink, and has an `@property` rule with its Paper value.
  2. Keep text tokens at 4.5:1 contrast against `--paper` in both appearances.
  3. Run `pages.tests_tokens` and the other modules that read tokens.css (`git grep -l tokens.css -- 'backend/**/tests*.py'`), then the site checks' `site` and `site-cloth` parts.
- [ ] **Step 5: Run the modules that read AGENTS.md.** These are `dashboard.tests_billing dashboard.tests_files dashboard.tests_keys dashboard.tests_overview dashboard.tests_recipe drop_billing.tests_account drop_core.tests_account drop_core.tests_fix_docs drop_core.tests_oauth_consent drop_core.tests_password_change_pages drop_core.tests_sign_in_pages pages.tests_chapters pages.tests_form_fixtures pages.tests_forms pages.tests_legal pages.tests_meter pages.tests_reading pages.tests_shell pages.tests_site_parts pages.tests_veil`. Expected: OK.
- [ ] **Step 6: Commit.** `TZ=UTC git commit -m "AGENTS.md: one Voice section, recipes that point to it, current pinned sentences, and a recipe for tokens"`.

### Task 8: Whole-branch checks and launch

**Files:** none new.

**Interfaces:**
- Consumes: Tasks 1 to 7.
- Produces: pull request on `veilfile/veilfile`, merged on the owner's go-ahead.

- [ ] **Step 1: Run every suite.**
  - The whole backend suite: expected OK, with every module of Tasks 1 to 7 green.
  - `cd npm && node --test`: expected `pass 75`.
  - The Python CLI: expected OK.
  - `python3 -I docs/superpowers/plans/2026-10-25-site-redesign-pr7-apply.py all --verify`: expected `190 applied items verified; 0 missing`. This shows every approved word is in place.
- [ ] **Step 2: Run the eight browser-check parts locally,** as AGENTS.md "Site checks" describes: `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`. Expected: 0 problems in each. A failing check that pins old words in `site-tests/run_site_checks.py` changes to the new words; any other failure is a real problem to fix.
- [ ] **Step 3: Run the identity gate on the tree.** Run `move/gate/gate.py` from the private `veilfile/ops` repository (a local copy sits in the move folder beside `dev/`) as `<python> <gate.py> . --rev HEAD --tree-only --strict`, with a Python that has fontTools and brotli (the gate's own `.venv`), so the fonts can be read. Expected: exit 0.
- [ ] **Step 4: Run the whole-branch review** (superpowers:requesting-code-review). Fix what it finds, within the words file and this plan.
- [ ] **Step 5 (owner's go-ahead): push and open the pull request as Muse.**
  - First check that `gh api user --jq .login` prints `yourimaginationwillbefired`.
  - Then run `git push -u origin voice/pr7` and `gh pr create --title "The voice pass: complete sentences, exact claims and the launch of the redesign"`.
  - The body is one paragraph in full sentences. It says what changed, that the owner approved every word, and that the fifteen checks must pass. It has no attribution lines.
- [ ] **Step 6: Wait for the fifteen checks.** Expected: all green, and `gh pr view --json mergeStateStatus --jq .mergeStateStatus` prints `CLEAN`.
- [ ] **Step 7 (owner's go-ahead): merge as Muse.** `gh pr merge --merge`. Then:
  - check that Render deploys the merge by itself, because Auto-Deploy waits for CI;
  - on the owner's go-ahead, check that `https://veilfile.com/promises/` shows "Why shouldn’t I use a gist?";
  - run the public-trace check (no owner account on the pull request, its commits or its merge).
