# Team seats PR E3: The CLIs Choose Visibility, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `veilfile push` in both published CLIs, the PyPI package in `cli/` and the npm package in `npm/`, takes `--visibility only_me|team`, sends it as the upload's `visibility` form field, and says which visibility the file got.

**Architecture:** The server side is E2: `POST /api/v1/artifacts/` reads the form field `visibility` while the team seats switch is on, honors it only when the administrator lets each person decide, and answers with `"visibility"`. With the switch off, or on an older server, the field is ignored and the answer has no such key. Each CLI adds one option to `push`, adds the field only when the option is given, and prints the answer's `visibility` when it is there. An upload without the option is byte for byte what each CLI sends today.

**Tech Stack:** Python 3.9 and later with argparse (`cli/veilfile.py`), Node 20 and 22 (`npm/veilfile.js`).

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md`, line 54: "The upload API, the MCP upload tools and both CLIs take an optional visibility of "only me" or "team" for each file." E2's plan: `docs/superpowers/plans/2026-11-13-team-seats-pr-e2-agent-visibility.md`.

## Questions for the owner

Each has a recommendation, and the plan proceeds on it.

1. **The flag.** `--visibility only_me|team`, the server's own name and values (recommended), or a shorter `--team`.
2. **What push prints.** When the answer has `visibility`, a line that says who can see the file. When `--visibility` was given and the answer has none, a line that says the server did not apply it (recommended). Python keeps stdout for the URL alone and prints these on stderr, as it does its warning. Node prints them on stdout beside `Expires:`, as it does its other lines.
3. **The versions.** Both packages become 0.5.0: npm from 0.4.1 and PyPI from 0.4.0 (recommended).
4. **When to publish.** Merge E3 now, and request the two releases when F turns the switch on, so that the flag works from the first day anyone can install it (recommended). Publishing now is harmless, but every published version must keep working for good, and until F the flag would only print that the server did not apply it.
5. **The words** below.

## Decisions

1. **The option.** Python: `push` gains `--visibility` with `choices=["only_me", "team"]` in the parser (`cli/veilfile.py:1255`), so argparse refuses any other value before a request is made. Node: `--visibility` joins `CLI_OPTIONS` (`npm/veilfile.js:1371`) and `push`'s allow-list in `COMMANDS` (`:1384`), and a value other than `only_me` or `team` exits with status 2 and the Node sentence below, before a request is made.
2. **The request.** `cmd_push` (`cli/veilfile.py:908`) and `cmdPush` (`npm/veilfile.js:879`) add the multipart field `visibility` only when the option is given. With no option, the request is unchanged. `cmdPush` takes `visibility` from its options object and treats a missing one as none, because the Node tests call `cmdPush({baseUrl, ttlDays})` directly.
3. **The output.** When the 201 has `visibility` of `team` or `only_me`, push prints the matching sentence from Words. When the option was given and the 201 has no `visibility`, push prints the "did not apply" sentence. Neither changes the exit status. Which stream each line goes to is as question 2 says.
4. **Versions and notes.** Each package's two version copies move together (`npm/package.json` and `VERSION` at `npm/veilfile.js:32`; `cli/pyproject.toml:7` and `__version__` at `cli/veilfile.py:39`), and each README's "What's new" gains a 0.5.0 entry. Publishing is the owner's, through the tags `npm-v0.5.0` and `pypi-v0.5.0` that `.github/workflows/release.yml` publishes with trusted publishing.
5. **Left as they are.** `pull`, `login` and `status` do not change. `plugin/` and `server.json` wrap the MCP server, which E2 covered.

## Words

Voice: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, American spelling.

- The option's help, in both CLIs: "Who on your team can see the file on their Files page: only_me or team. It is honored only when your administrator lets each person decide."
- After an upload whose answer says `team`: "Visible to: your team."
- After an upload whose answer says `only_me`: "Visible to: only you."
- When `--visibility` was given and the answer has no `visibility`: "The server did not apply --visibility."
- A bad value in the Node CLI: "Send --visibility as only_me or team." The Python CLI uses argparse's own message for a bad choice.

## Global Constraints

- A push without `--visibility` sends exactly what each CLI sends today, and its output does not change against a server that answers as `main` does with the switch off.
- No server code changes in E3.
- Both packages keep their supported versions: Python 3.9 and 3.12, Node 20 and 22, as CI runs them.
- Tests: `cd cli && python -m unittest discover -s tests -t .` and `cd npm && node --test`. Each version's two copies agree, as the existing tests check.
- Nothing is published, and no release tag is pushed, without the owner's request (AGENTS.md invariant 9). Nothing is pushed, opened or merged without the owner's go-ahead at that step.
- Never read `.env` files or a secret value. Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no Co-Authored-By, session link or model name. PRODUCT.md and DESIGN.md stay uncommitted.

## Review Focus

1. A push with no option, against a server with the switch off, sends and prints exactly what it does today.
2. `--visibility team` against an older server, or one with the switch off, uploads the file and prints the "did not apply" sentence, with exit status 0.
3. A bad value is refused before any network request, in both CLIs.
4. Python's stdout holds only the URL, so `url=$(veilfile push f)` still works.
5. Node's `cmdPush` called without a `visibility` field, as the existing tests call it, behaves as today.

---

### Task 1: The Python CLI

**Files:**
- Modify: `cli/veilfile.py` (`cmd_push` at 908, the parser at 1255, `__version__` at 39), `cli/pyproject.toml:7`, `cli/README.md` ("What's new" and the `push` usage)
- Test: `cli/tests/` (a new `test_visibility.py`, beside the existing push tests)

- [ ] **Step 1: Write the failing tests,** with the HTTP call stubbed as the existing push tests stub it:
  - `test_the_option_sends_the_field`: `push f --visibility team` sends a multipart field `visibility` of `team`.
  - `test_without_the_option_the_request_is_todays`: the multipart body has no `visibility` part.
  - `test_a_bad_value_is_refused_before_any_request`: `--visibility public` exits with status 2, and no request is made.
  - `test_the_answer_says_who_can_see_it`: with a 201 that has `"visibility": "team"`, stdout is the URL alone and stderr has "Visible to: your team."; with `only_me`, stderr has "Visible to: only you."
  - `test_an_answer_without_visibility_says_it_was_not_applied`: with `--visibility team` and a 201 that has no `visibility`, stderr has "The server did not apply --visibility." and the exit status is 0. Without the option, nothing new is printed.
- [ ] **Step 2: Run them and see them fail.** `cd cli && python -m unittest discover -s tests -t .`
- [ ] **Step 3: Add the option, the field and the lines,** as decisions 1 to 3 say, and set the version to 0.5.0 in both places with a "What's new" entry.
- [ ] **Step 4: Run the whole Python test suite.** Expected: OK, including the test that the two version copies agree.
- [ ] **Step 5: Commit** "The Python CLI's push takes --visibility and says who can see the file".

### Task 2: The Node CLI

**Files:**
- Modify: `npm/veilfile.js` (`cmdPush` at 879, `helpText` at 1338, `CLI_OPTIONS` at 1371, `COMMANDS` at 1384, `VERSION` at 32), `npm/package.json`, `npm/README.md`
- Test: `npm/test/visibility.test.js` (new)

- [ ] **Step 1: Write the failing tests,** with `fetch` stubbed as `npm/test/cli.test.js:327` stubs it:
  - `parseArgs accepts --visibility on push`, and refuses it on another command, as the allow-list does for `--ttl-days`.
  - `push sends the visibility field only when it is given`.
  - `a bad visibility exits 2 before any request`, with the Node sentence from Words.
  - `push prints who can see the file`: "Visible to: your team." or "Visible to: only you." after the `Expires:` line.
  - `an answer without visibility says it was not applied`, only when the option was given, with exit status 0.
  - `cmdPush without a visibility field behaves as today`.
- [ ] **Step 2: Run them and see them fail.** `cd npm && node --test`
- [ ] **Step 3: Add the option, the field and the lines,** as decisions 1 to 3 say, and set the version to 0.5.0 in both places with a "What's new" entry.
- [ ] **Step 4: Run the whole Node test suite.** Expected: OK, including the version check.
- [ ] **Step 5: Commit** "The Node CLI's push takes --visibility and says who can see the file".

### Task 3: The docs

**Files:**
- Modify: `backend/docs/pages/quickstart.md:72`, `docs/API.md:366` and `docs/llms.txt:36`, each naming `--visibility` beside `--ttl-days` in one sentence

- [ ] **Step 1: Write the sentences** in the voice rules.
- [ ] **Step 2: Run `drop_core.tests_fix_docs` and `pages`, one at a time,** from `backend/`. Expected: OK.
- [ ] **Step 3: Commit** "The docs say that both CLIs take --visibility".

### Task 4: The test card

- [ ] Write `~/dev/veilfile-test-cards/e3-local.md`, outside the repository. It reuses the E2 card's local team and Grace's key. It runs each CLI from the checkout (`python cli/veilfile.py` and `node npm/veilfile.js`) with `VEILFILE_BASE_URL=http://127.0.0.1:8000` and `VEILFILE_API_KEY` set to Grace's key, and checks `push` with and without `--visibility`, with a bad value, and against the server run with the switch off. Each step ends with a **Pass** line.

## Changed during the build

- The Python test class borrows the shared test class's setup and helpers instead of subclassing it, because a subclass would run the shared class's own tests a second time.
- The Node suite ran here on Node 26 only. CI runs it on Node 20 and 22.
- The test card was run end to end on a throwaway local server, with the switch on and then off.

## Carried to F

- **F:** the owner requests the releases `npm-v0.5.0` and `pypi-v0.5.0` when the switch turns on, and the items carried by the C2, C3, D, D2, E1 and E2 plans, including the one rehearsal on Postgres for D2 and F.
- **Later, from E3's review:** if a future server answers a `visibility` value other than `team` or `only_me`, both CLIs say the server did not apply the option, which would then be untrue. The Node help puts a capital letter after a colon in "--visibility: Who on your team". `docs/llms.txt` does not say which stream the new line goes to in each CLI.
