# Veilfile site redesign, PR 7: the voice pass and launch

- **Date:** 2026-10-23
- **Status:** approved by the owner (2026-10-23). The words were approved on 2026-10-25 and are in [the words file](../plans/2026-10-25-site-redesign-pr7-words.json), which the [plan](../plans/2026-10-25-site-redesign-pr7-voice.md) applies.
- **Parent spec:** [2026-10-08-site-redesign-design.md](2026-10-08-site-redesign-design.md). This document expands its step 7 (§11, "Voice pass and launch") and adds to §5 (voice), §7 (recipes and checks) and §10 (branch protection). Where the two disagree, this one wins for PR 7.
- **The sweep:** every finding this PR answers is listed, with its file, line, quote and reason, on a private page: <https://claude.ai/artifact/WSxJz2WXckZBMutBVMzXsD>. The page embeds its data, so it is the durable copy of the list.

## 1. Intent

PRs 1 to 6 moved every page onto the new shell and kept the old words, which tests pinned "until the voice pass". This PR writes the final words with the owner and launches the redesign.

The owner named what bothers them most: sentence fragments, such as Promises' "Fine for a snippet; wrong for a client's failing screenshot." Every sentence the site prints should be grammatical, syntactically sensible and complete, with a subject, a verb and an object where the verb needs one, and a semicolon should only separate independent clauses.

A sweep of the site's words (2026-10-23) found 202 findings in about 60 files. Most are fragments (111) and misused semicolons (17). Fourteen are claims that say more than the product does, two of them flatly false, and 48 are judgment calls the owner decides in review. The voice rules of §5 already forbid implying a feature that does not exist, so the wrong claims are the most important part of the PR.

The site should stay what §1.2 asks: calm, minimal and specific, so that a visitor feels both invited and fully informed. Fixing a fragment must not make a sentence stiff, padded or longer than it needs to be.

## 2. The rules

These are the owner's (2026-10-23) unless marked otherwise.

1. **Complete sentences.** Every sentence is grammatical and complete: a subject and a finite verb, and an object when the verb needs one. Imperatives count as complete, with "you" understood. A list of noun phrases ending in a full stop ("No public repository, no bucket to lock down, no paste that lives forever.") is a fragment. So is a clipped question ("Why not a gist?", "No account?", "Already subscribed?").
2. **Semicolons** join only two independent clauses. Anywhere else, a semicolon becomes a full stop, a comma or a rewrite.
3. **Labels are exempt.** That covers buttons (COPY, COME IN), tabs, menu items, field labels, table column headers, table cells that hold a value, short mono labels ("Check this") and the corner labels.
4. **Headings drop the full stop.** A heading that is a phrase stands as a label with no full stop: the 404's "Nothing here." becomes "Nothing here", and About's title becomes "A quiet place for what your agents make". Headings are `h1` to `h6`, the terms (`dt`) of a definition list, Markdown headings, and page titles. *My call, for consistency and so a test can hold it:* no heading ends with a full stop, even one that is a full sentence ("The link is the only way in" in Promises loses its stop as well). A heading may end with a question mark only when it is a complete question.
5. **Everything else is a full sentence wherever it can be.** This covers status lines ("Key revoked." becomes "The key is revoked." or similar), notes ("Tax included."), result lines ("Approved."), empty states ("No files yet."), the clipped questions before a link ("No account?"), and every search description in `backend/pages/registry.py` and the Markdown front matter.
6. **llms.txt follows the same rule** (*my call*; the owner deferred to me on what agents read best). Agents read complete sentences more reliably than clipped ones. Data stays terse as a label and its value with no full stop ("Max upload size: 25 MiB"). Anything that explains, qualifies or warns is a full sentence. Its structure (sections, endpoint lists, exact values) stays as it is, because that is what agents scan for.
7. **The existing voice rules stay** (§5 and AGENTS.md): no em dashes, no exclamation marks, none of the banned words, American spelling ("canceled", not "cancelled"), `’` for apostrophes in templates, errors that say what happened and what to do with no apology, and no claim the product does not back.

No test can judge grammar, so rules 1, 2, 5 and 6 are held by review. Rule 4 is held by a new test (§8).

## 3. Scope

Every finding on the sweep page is in scope, the "your call" ones included: the owner decides those during review (§10). By area:

| Area | Findings | Notes |
|---|---|---|
| Chapters and search descriptions | 34 | About, How it works, Begin, Promises, Plans; nearly every registry description is a fragment. |
| Journal | 18 | The one post, `introducing-veilfile.md`: its opening run of fragments, three wrong claims (§4), and "Last week", which becomes a date. Muse wrote it; the owner asked for the Journal to be swept. |
| Sign-in forms and email | 12 | Sign in, sign up, reset, verify, checkout, throttled, the three emails. |
| Device and app sign-in | 8 | Rewritten as a whole in the spirit of §5. |
| Dashboard | 30 | Includes the deferred items from PR 6: "1 uploads", the plan name printed in lowercase in the over-limit heading, and British "Cancelled". |
| Error pages | 9 | 403, the CSRF page, 404, the missing-file page, 500. |
| Docs | 41 | Includes descriptions that promise sections the pages do not have (§4). |
| llms.txt | 23 | Under rule 6. |
| Frames, filters and the staff catalog | 27 | The frame around the legal pages, the dashboard's filters, robots.txt comments, and the staff-only component catalog's captions. |

## 4. The wrong claims

Each must say exactly what the product does, checked against its source (`docs/API.md`, `docs/llms.txt`, `backend/drop_core/plans.py`, `sweep.py`, `secrets.py`, `safety.py` and the views). A second, independent check confirmed all fourteen.

**False:**
1. `templates/device/includes/grant_summary.html:10`: "Disconnect every app connected to your account". An API key cannot disconnect apps (`drop_core/views.py:611-615`, 403 `session_required`).
2. `docs/pages/api.md:5`, the page's description: "Every Veilfile endpoint: upload, list, revoke, keys, usage, billing, and the MCP server…". The page covers upload, list, revoke and download only, and its last line sends readers to API.md for the rest.

**Overstates:**
3. `grant_summary.html:9`: "List the workspace's API keys and revoke any of them". A key can list keys but revoke only itself (`drop_core/account.py:213-226`).
4. `chapters/how.html:11`: "Uploads are checked for anything shaped like a secret". The scanner reads the first 1 MiB and matches a fixed list of named patterns (`secrets.py`).
5. `chapters/promises.html:11`: "scanned for anything shaped like a key or a token". The same limits apply.
6. `chapters/promises.html:10`: "We look only for the shapes of secrets." Uploads are also screened against a blocked-hash table (`safety.py`) and checked by magic bytes and UTF-8.
7. `pages/registry.py:70`, the Journal's description: "Updated twice a month." One post exists and no cadence is promised anywhere.
8. `blog/posts/introducing-veilfile.md:14`: "Keys are issued per agent and can be revoked in one call." Keys belong to the account, and a key can revoke only itself.
9. `introducing-veilfile.md:16`: "Uploads are verified by file contents, not by extension." The extension decides the type and must be allowed, and the contents are checked as well.
10. `introducing-veilfile.md:16`: "verified email is required on every account". Only key issuance, uploads by session and app sign-in need it.
11. `docs/pages/agent.md:5`, the description, promises curl examples, key rotation and revocation that the page does not have.
12. `docs/pages/index.md:5` (and its copy in `registry.py:65`) promises "the full REST and MCP API reference", which the Docs do not hold.
13. `docs/pages/how-it-works.md:41`: "Nothing caches the bytes. Artifact URLs are never indexed." The headers ask caches and crawlers not to, which is all a server can do.
14. `docs/llms.txt:73`: CSAM reports "go to NCMEC". The policy (`legal/documents/report-abuse.md`) says confirmed exploitation is reported to NCMEC after review.

A fix may correct the claim or remove it. It must not swap one overstatement for another.

## 5. The approval pages

**The pages:** the device flow's five (`device_enter_code`, `device_consent` with `includes/grant_summary`, `device_success`, `device_denied`, `device_error`) and the OAuth consent page (`oauth/authorize` with `includes/grant_summary_oauth`), where an app such as Claude asks to connect. They are one moment: someone is about to give a terminal or an app power over their account. The device page is also where a phisher would send someone with a code of their own.

**The owner's intent:** the person should be careful, and sure of what they are doing and why. The pages should not scare them away, but they should know this is important.

**What the words do:**
- They say plainly what approving does: it gives that terminal a key to the account, or lets that app act on the workspace.
- They list exactly what the key or the app can do, accurately (§4, items 1 and 3).
- They say to approve only a code from a `veilfile login` the person started themselves, just now. The current sentence, "Only enter a code from a veilfile login you ran yourself.", is the seed.
- They make Deny feel safe: denying issues nothing.
- The result pages say what to do next. Approved sends the person back to the terminal. Denied says no key was issued and what to do if they did not start it.

**How it should feel:** calm gravity, not alarm. There are no warning colors, capitals or "WARNING". The site already uses no color for errors (PR 5), and the weight comes from the words. The sentences are short.

**What must not change** (spec §4.7): the URLs, form fields, hidden fields, the GET to `/device`, the post to `/device/approve/`, the Approve and Deny forms, the OAuth consent's "unverified app" framing, and `X-Frame-Options: DENY`. `device_error.html` is also the OAuth consent's 403, so it stays neutral and says nothing of a terminal or a code.

## 6. Begin's sentence for the Claude apps

Begin says "Add a custom connector with the address above, and sign in when it asks." The spec held it until the owner confirmed the steps. A web check of Anthropic's help center and docs (2026-10-23) found:
- The menu path is Customize, then Connectors, then Add custom connector. Older articles say Settings.
- Every plan can add one, but a Free account can add only one custom connector.
- On Team and Enterprise, an Owner adds the connector for the organization first, and members then connect.
- The apps sign in by OAuth with dynamic client registration, which Veilfile's server offers. The hosted apps' callback is `https://claude.ai/api/mcp/auth_callback`.
- The add dialog asks when to sign in (now, when needed, or never) and how to register the client. It recommends "Claude's published identity", a client ID metadata document (CIMD).
- Phones use a connector added on the web or in Desktop. Adding one on a phone is in beta.

**Verified while planning (2026-10-25):** Veilfile's authorization-server metadata advertises `client_id_metadata_document_supported: false` beside a `registration_endpoint`, so Claude falls back to dynamic client registration. Anthropic's documentation (claude.com/docs/connectors/custom/add-unlisted) names the dialog's setting OAuth client and the option to choose Register automatically; an older one-screen layout shows Advanced settings instead, where the OAuth client ID and secret stay blank. Team and Enterprise Owners add a connector under Organization settings, then Connectors, and members then connect to it. The owner confirmed the Customize path in the app. When Veilfile is listed in Anthropic's Connectors Directory, Begin and the Docs point to the listing instead; that is a later change.

*My call:* Begin keeps one or two sentences, and `backend/docs/pages/agent.md` gains a short "Claude apps" section with the full steps, so the Docs carry what Begin summarizes. Both print the current menu path (Customize, then Connectors), and the owner confirms it in the app during review (§10), since a wrong path would be a new wrong claim. (Done on 2026-10-25.)

## 7. AGENTS.md

- **A "Voice" section,** in one place, holding §2's rules with before-and-after examples taken from this sweep. The recipes that repeat the voice rules today ("Edit a chapter's words" step 3, "Edit Docs", "Write a Journal post", "Add or change a form page", "Add or change a dashboard section") point to it instead.
- **The recipe the spec's §7.1 lists but AGENTS.md lacks:** "Change a color, size or spacing" (`static/css/tokens.css` only).
- **The pinned-sentence lists** in the recipes (for example "Edit a chapter's words" step 5) are updated to the new words.
- **Owners:** the Voice section names no single owner, since every team writes words (see the Deadrop roles in AGENTS.md's recipes).

## 8. Checks

- **New: no heading ends with a full stop.** It covers HTML headings (`h1` to `h6`) and definition terms (`dt`) in the shell's templates and in rendered pages, Markdown headings in the Docs and the Journal, and the registry's titles. The failure message names the file, the line, the heading and the fix ("drop the full stop; a heading is a label").
- **Pinned words:** every Django test and every check in `site-tests/` that pins an old sentence is updated in the same PR, to the new sentence.
- **Grammar** has no test. Review holds it, including the build's final review.
- *Builder's choice:* where the existing voice checks (no em dash, no exclamation mark, banned words) do not yet cover the Docs, the Journal or llms.txt, extend them if it is cheap.

## 9. Launch

- **Required checks are on the public repository.** The move to `veilfile/veilfile` (2026-10-24) put a ruleset on `main` that requires all fifteen checks: `backend`, the two `npm-cli` and two `python-cli` jobs, `changes`, `names` and the eight browser jobs. The browser jobs first ran green on GitHub's hosted runners in pull request #1.
- **CI:** all fifteen checks green on the PR, then the owner's go-ahead to merge, as for PRs 1 to 6. The merge is also the first change Render deploys from the new repository by itself, which proves that switch.
- **Still open from earlier PRs, not blocking:** the owner's phone retests (picking pages from the menu, Low Power Mode, the scrolled cross-fade in Safari).

## 10. How the words get approved

1. **Draft.** Agents write a rewrite for every finding, area by area. A second agent checks each rewrite against §2, the facts (§4), and the constraints (§12).
2. **Review.** The owner reads the drafts on a page that saves their choices for each one (approve, edit, or leave as it is), so Claude reads the decisions back without the owner retyping anything.
3. **Plan.** `superpowers:writing-plans` turns the approved words into a plan with exact edits, test changes and checks.
4. **Build.** The plan is built as PRs 5 and 6 were, then reviewed, then CI, then the owner's go-ahead.

## 11. Non-goals

- **PR 8:** the MCP server's tool descriptions and the API's error messages that agents read (`backend/drop_mcp/views.py`, `backend/drop_core/errors.py`). Agreed with the owner, 2026-10-23.
- The text of the legal documents (owner-only, spec §12). Their frame is in scope.
- Any behavior: API, MCP, OAuth, CLI, URLs, form fields, status codes, redirects. Supporting CIMD (§6) is behavior.
- Layout and visual design.
- `verify_email_done` losing `next` after a sign-up from the device or OAuth flow (left for later in PR 5).
- README.md, server.json and the plugin folder, which GitHub and the MCP registries show.

## 12. Constraints

- A test that pins words changes with them (§8). AGENTS.md's recipes list the pinned sentences.
- Search descriptions stay 70 to 160 characters, and titles stay at most 60 (`pages/tests_registry.py`).
- The threshold's and Plans' descriptions type prices. Their figures stay exactly as they are, and `drop_core/tests_fix_docs.py` scans them. Chapters never type a figure: Plans reads every one from `plans.py`.
- Begin's client snippets stay equal to `backend/docs/pages/agent.md` (`pages/snippets.py`).
- Every character exists in all three fonts (`pages/tests_content`).
- Phrases a CLI prints or matches cannot change freely. The sweep found none among its findings; a rewrite that touches a CLI-facing phrase checks `cli/veilfile.py` and `npm/` first.
- The forms' and approval pages' contracts in §5.
- Muse's on-hold "Quickstart rewrite" pull request lived in the old private repository, which the move retires. It rewrites `docs/pages/quickstart.md`, which this PR also edits, so if its changes are still wanted they are redone on the new repository after this PR merges.

## 13. Open questions for the owner

None. Branch protection (§9) was settled on 2026-10-23 (a fresh public repository, not GitHub Pro), and the move is done.

## 14. Left to the builder

- The exact words, drafted for the owner's approval (§10).
- The review page's design.
- How the heading test finds headings, including headings filled in at runtime and `<title>` values, without false failures.
- The order and size of the plan's tasks.
