# PR 8 plan: the words agents read, and tool annotations

- **Spec:** [2026-10-28-pr8-agent-text-design.md](../specs/2026-10-28-pr8-agent-text-design.md)
- **Words:** [2026-10-28-pr8-words.json](2026-10-28-pr8-words.json), approved by the owner on 2026-10-29. Each item's `new` is the text a client receives, exactly. Nobody changes a word while building; a word that cannot be applied as written goes back to the owner.
- **Branch:** `agent-text/pr8`, from `main` at `c413686`.

## Rules for every task

1. Apply the approved words byte for byte. When a text is built in code (an f-string, a `%` format or a list joined at run time), the text a client receives must equal the approved words with its placeholders filled.
2. Change no behavior: no status code, error code, response shape, tool name, input schema, URL or header changes. The one addition is each tool's `title` and `annotations` (Task 1).
3. A test that pins a text changes with it, in the same commit. A test that only checks a code or a status stays as it is.
4. Figures come from where they live. The plan maximums in the `ttl_days` descriptions come from `drop_core/plans.py`, and the allowed extensions from `drop_core/services.py`, so the words stay true if those change.
5. Commit as Veilfile, dated in UTC (`TZ=UTC git commit`), with no attribution lines. The identity gate runs as a hook and must pass.
6. Run the tests the task names before committing, and the whole backend suite (`cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`) at the end.

## Tasks

### Task 1: tool titles, descriptions and annotations (T01 to T18)

- **File:** `backend/drop_mcp/views.py` (`TOOLS`), and its tests.
- Replace each tool's description and each parameter description with the approved words.
- Add `"title"` and `"annotations": {"title": ..., "readOnlyHint": ..., "destructiveHint": ..., "idempotentHint": ..., "openWorldHint": ...}` to each tool, with the title words in both places and the values in the words file's `annotations`.
- Build the plan maximums in T06 and T11 from `PLANS`, and the extension list in T03 and T10 from the allowed extensions, so the rendered text equals the approved words today.
- Tests: change the pins on "ask the user" (`drop_mcp/tests.py`, `drop_mcp/tests_fix_mcp.py`) and on "recommended" to the new words. Add a test that every tool has a title equal in both places and all four hints with the approved values, and a test that the rendered T06 and T11 texts name the plan maximums from `PLANS`.
- Check: `manage.py test drop_mcp drop_core.tests_fix_docs`.

### Task 2: MCP errors (M01 to M29)

- **File:** `backend/drop_mcp/views.py`, and its tests.
- Replace every tool-error text and JSON-RPC error message with the approved words. M29 changes only the 405 body's `hint`; the body keeps its shape.
- Tests: change the pins that quote the old words (for example "unknown tool" and "unauthorized" in `drop_mcp/tests.py`).
- Check: `manage.py test drop_mcp drop_core.tests_oauth drop_core.tests_fix_auth`.

### Task 3: REST errors in the core app (C01 to C37)

- **Files:** `backend/drop_core/views.py` (`ERROR_MESSAGES` and the upload and key views), `backend/drop_core/services.py` (details), `backend/drop_core/errors.py`, and their tests.
- Replace each text with the approved words. For `invalid_request`, the `error` text keeps carrying the detail, as it does today.
- In `errors.py`, map the library's default texts to the approved words by exception class, keeping each status and code: C24 to C36. The throttle text (C29) uses its two variants for a one-second wait and for no known wait.
- C10 and C37 are unchanged.
- Tests: change every pin that quotes an old text, including the byte-for-byte pins in `drop_core/tests_json_answers.py`. Add a test for each library text the handler now rewrites.
- Check: `manage.py test drop_core drop_mcp`.

### Task 4: keys, files, connected apps and billing (A01 to A16)

- **Files:** `backend/drop_core/account.py`, `backend/drop_billing/account.py`, and their tests.
- Replace A01 to A06 and A08 to A10 and A12. A07, A11 and A13 to A16 are unchanged.
- The dashboard replaces some of these texts with its own (for example `dashboard/keys.py` for A05); those dashboard sentences stay as they are.
- Tests: `drop_core/tests_json_answers.py`, `drop_billing/tests_json_answers.py`, `drop_billing/tests_account.py` and any other pin.
- Check: `manage.py test drop_core drop_billing dashboard`.

### Task 5: device login and OAuth (D01 to D26)

- **Files:** `backend/drop_core/device.py`, `backend/drop_core/oauth.py`, and their tests.
- Replace each Veilfile-written description with the approved words. Standard error codes stay exactly as they are.
- Tests: keep the pins that still hold (`tests_fix_device.py` needs "limit" and "no longer active"), and change the others.
- Check: `manage.py test drop_core`.

### Task 6: the docs

- **Files:** `docs/API.md`, `backend/docs/pages/api.md`, `docs/llms.txt`.
- Quote the approved words wherever these files quote a tool description or an error text, and fix the rows that have drifted from the code. Describe each error code in the tables in the owner's voice, and keep each file's structure. `llms.txt` keeps its sections and values.
- Check: `manage.py test pages.tests_content drop_core.tests_fix_docs drop_core.tests.DocsTests pages.tests_voice`.

### Task 7: AGENTS.md and the spec

- **Files:** `AGENTS.md`, the spec.
- Add X01 after step 4 of "Releasing the CLIs", and change invariant 4 to X02.
- If AGENTS.md lists a pinned sentence this PR changed, update it.
- Mark the spec's status as words approved on 2026-10-29.

## Review

Each task is reviewed before the next begins: one review checks that the words applied are exactly the approved ones and that nothing else changed, and one checks the code and tests. Findings go back to the task's implementer, for at most two rounds. After Task 7, two reviews read the whole branch: one for behavior (no code, status or shape changed) and one for the words and the voice. Then the whole backend suite, the CLI tests and the reading-page site checks run, and the identity gate runs in strict mode on the tree.
