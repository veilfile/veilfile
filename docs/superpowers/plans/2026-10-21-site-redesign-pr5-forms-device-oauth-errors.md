# Site redesign PR 5: forms, device, OAuth and error pages — Implementation Plan

**Goal:** Move every remaining old-style page onto the new shell. That means sign in, sign up, password reset, email verification, checkout confirm, the device-code flow, OAuth consent and the error pages. The forms share one narrow form layout, and nothing the CLIs, OAuth clients or security tests rely on changes. Then the old includes go.

**Architecture:**
- **The form layout:** a new `layouts/form.html` (body class `layout-form`) extends `shell.html` and draws one narrow column. Form pieces are components:
  - `field` (a mono label above a hairline input, with an optional help line and an error sentence);
  - `form_errors` (non-field errors);
  - the main action, a hairline box (`.action`), with quieter secondary links.
- **No in-place changes:** form pages are outside the registry's layout families. They load ordinarily, with no `data-layout`.
- **Moved pages keep their contracts:** every page keeps its view, URL, field names, status codes and the phrases its tests pin. The templates move onto the shell.
- **Error pages:**
  - 404, 403 (including a CSRF failure view) and every 429 render on the shell.
  - `/a/<token>` misses render one page for a missing or expired link, revealing nothing.
  - 500 is a self-contained template with inline CSS.
- **The old includes:** once no template uses `includes/theme_boot.html`, `ambient.html` and `menu.html`, they and their tests are removed.

**Tech Stack:** Django 6.1 templates and views, Django auth views, django-oauth-toolkit, plain CSS tokens, Playwright for Python 1.49.1 site checks (Chromium, plus WebKit in the phone part), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (as amended through PR #17), sections 4.1, 4.4, 4.7 (forms), 4.9 (error pages), 4.10 (phones), 5 (voice), 6.2 (templates and component rules), 6.5, 9 (accessibility), 10 and 11 step 5.

## Global Constraints

- **Branch:** `design/site-redesign-pr5`, cut from `origin/main` at or after 5893ffc (PR #17). Before starting, `git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github` must print nothing.
- **Commits:** `git -c user.name=Veilfile -c user.email=support@veilfile.com commit` with the step's message and nothing added: no `Co-Authored-By`, no session links, no mention of any AI or model. Stage files by name.
- **Tests:** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <labels>`. The whole suite takes about 3.5 minutes, and every task ends with it green.
- **Browser checks:**
  - **Server:** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload`. Check the port is free first, and restart after template or Python changes.
  - **Checks:** `caffeinate -ims site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part pages|cloth|ink|reading|phone|forms|all`.
  - **A fresh worktree:** run `migrate`, then make the staff session first (AGENTS.md, "Site checks").
  - **CI:** each part runs in its own job with a 20-minute limit. CI's Linux Chromium reports `pointer: none` and `hover: none`, so gate hover CSS on `not all and (pointer: coarse)`.
  - **Timing:** checks that time things must wait for states, never fixed sleeps, and must stay correct with frames 120 ms apart (PR #15 and #17 lessons).
- **Budgets:** `shell.js` plus `behaviors/*.js` may be at most 10,240 B gzipped; 9,112 B are used. This PR adds no JavaScript to them; forms work without script. `veil.js` stays within 15 KB.
- **No new dependencies.**
- **Addresses and contracts do not change.** That covers:
  - every URL under `/accounts/`, `/device/` (all three spellings of each action: `/device`, `/device/`, `/device/approve`, `/device/approve/`, `/device/deny`, `/device/deny/`), `/o/authorize/` and `/a/<token>`;
  - every form's field names, hidden fields and values;
  - status codes and redirects;
  - the query strings that ride along (`?next=`, `?plan=`, `?user_code=`): login and signup post to their own URL with no `action` attribute;
  - the phrases the CLIs print or match.
- **Security invariants:**
  - `{% csrf_token %}` in every POST form, never inside an `only`-included component unless it is passed in.
  - `/a/<token>`: a missing link and an expired one get byte-identical responses (status, body and headers, including `Vary` and `Cache-Control`), and no error page echoes `exception` or `request_path`. A found link still serves the file's bytes untouched.
  - A throttled (429) page is byte-identical for a real and a missing account: no CSRF token, nothing per-user.
  - OAuth consent keeps `X-Frame-Options: DENY`, its "unverified app" framing and the field names django-oauth-toolkit expects.
  - `device.py` never mentions `REMOTE_ADDR`.
- **Legal text is the owner's.** Never edit `backend/legal/documents/*`.
- **Copy:**
  - Keep every page's current words, except where this plan says otherwise. The device page is rewritten in the voice pass (PR 7), not here.
  - New copy follows the voice rules (no em dashes, no exclamation marks, none of `pages/tests_chapters.py`'s banned words).
  - Every non-ASCII character in a shell template must exist in all three fonts. The device page's check mark (U+2713) does not.
- **Accessibility (spec 9):**
  - one `h1` per page, inside `<main>`; never `<header>` or `<footer>` inside `<main>`;
  - every input has a visible `<label for>`;
  - an error sets `aria-invalid="true"` and is tied to its field with `aria-describedby`; a non-field error summary is `role="alert"`;
  - `autocomplete` tokens are kept;
  - focus is visible;
  - `autofocus` only where the page exists to take one input;
  - inputs are at least 16 px on phones, so iOS does not zoom;
  - nothing scrolls sideways at 320.
- **Components (spec 6.2):** each starts with a comment listing its inputs, is included only `with … only`, and has one stylesheet `static/css/components/<name>.css` (linked in `shell.html`), a catalog entry and a rendering test.
- **Colours** live only in `tokens.css`. This PR adds no colour (the owner's choice: errors use weight and a mark, not colour).
- **Muse keeps this site** (spec 1.4, 7): every new convention has a test whose failure message names the file and the fix (`longMessage = False` on new test classes). AGENTS.md stays true after every task.

## Review Focus

1. **Leaks.**
   - A missing and an expired `/a/<token>` give byte-identical responses, including `Vary`, which the shell's session read adds.
   - A throttled page is identical for a real and a fake account.
   - No error page shows `exception`, `request_path` or anything per-user.
   - A found file is never wrapped.
   - Tests: Task 6, and Task 2 for 429.
2. **Round trips.**
   - Sign in and sign up return to `?next=`, including the device page (`/device?user_code=…`) and OAuth consent (`/o/authorize/?…`); `?plan=` survives sign up.
   - A signed-in visit to sign in or sign up goes to `next` or `/app/`.
   - Approve and Deny post to the same URLs with the same fields.
   - Tests: Tasks 2, 4 and 5.
3. **Every state of every form.**
   - Field errors, non-field errors, throttled, invalid, expired or already-used codes, an unverified email, empty and very long values (an email, a user agent, an app name) on a 320 px phone.
   - Browser autofill over the cloth.
   - The keyboard: tab order from the skip link, Enter submits.
   - Tests: Tasks 1 to 5 (Django), Task 7 (browser).
4. **Error pages under failure.**
   - 500 renders with no request, no context processors and no static manifest.
   - A 404 or 403 template that raised would become a 500 and must not.
   - The 404 for an unknown Docs slug still marks Docs current in the menu.
   - Tests: Task 6.
5. **Phones.**
   - Inputs near the top under the phone's top fade: a focused input must not sit under the fade, so check `scroll-padding`.
   - The on-screen keyboard.
   - Input text at 16 px or more, with contrast over the calmed cloth.
   - The phone sheet and its change path are unaffected on form pages, which have no layout family.
   - Tests: Task 7.

## Rulings made while planning

- **The owner's choices (2026-10-18):**
  - **The main action** is a hairline box: a mono label inside a 1 px `--hair` box on `--box`, taking the ink wash on hover and press like every control. Secondary actions are quieter mono links.
  - **Errors use no colour.** The sentence is in full `--ink` (not `--muted`) under the field, the field's line is drawn solid `--ink`, and a small mono label reads "Check this" (uppercased by CSS).
  - **A signed-in visit** to sign in or sign up redirects to the safe `next` or to `/app/`.
- **The controller's rulings while drafting:**
  - Every form, device, OAuth and error page title ends " | Veilfile", as every other page on the new site does; the voice rules ban em dashes.
  - An input's single line is drawn in `--muted`, not `--hair`, so the field's boundary meets WCAG 1.4.11's 3:1: `--hair` is 1.45:1 on Paper. The main action's box stays `--hair`, because a button is identified by its words.
- **The signed-in redirect is for GET and HEAD only.**
  - Django's `redirect_authenticated_user` would raise on a `next` pointing at the login page.
  - It would also redirect POSTs, which lets a signed-in visitor clear another account's sign-in lock.
  - Sign up honours a safe `next` on its free path, so the device and OAuth round trips survive a new account.
- **The `/a/<token>` miss is drawn without the request.** It never reads the session, so a missing and an expired link get identical bytes and headers for anyone.
- **Form pages are outside the registry's layout families.** They load ordinarily, with no in-place changes and no `data-layout`. Each sets its own `<title>` (current titles kept) and, where it holds a token or a one-time state, `<meta name="robots" content="noindex">`.
- **The device copy stays** (spec 4.7: the voice pass rewrites it). Only the check mark, which no font of the site has, becomes a word or a CSS-drawn mark.
- **OAuth consent:**
  - It gains an `h1`.
  - It keeps two forms (Deny and Authorize), its fields and its "unverified" framing.
  - A request django-oauth-toolkit reports as an error (no valid client) shows the error page, not a consent form. This is a bug the map found.
- **One error page for `/a/<token>`:** "Nothing here. If this link was shared with you, its moment may have passed." (spec 4.9).
- **Other error pages:**
  - 404, 403 and the CSRF failure page (`CSRF_FAILURE_VIEW`) use the shell, as do all 429s. The bare-string 429s from sign up and email verification become the shell's throttled page, with `Retry-After` as the throttle sets it.
  - 500 is `templates/500.html`, self-contained: inline CSS and no context, so no `{% static %}`, `{% url %}` or context processors.
- **The old includes go in this PR,** once the last template stops using them, with their tests (`tests_menu.py`, `tests_ambient.py`). The spec said PR 6. They are already unused by `/app/`, which is React's own `index.html`.
- **Browser checks:** a new `forms` part and CI job `site-forms`, with a dev-only fixture script beside `make_staff_session.py`. It makes what the pages need: a verified user's session, a pending device code and an OAuth client. Error pages are checked with the error templates rendered as they are in production. How, given that the dev server runs with DEBUG on, is Task 7's to settle and justify.

## File structure

```
backend/templates/layouts/form.html                 the form layout (Task 1)
backend/templates/components/{field,form_errors}.html + static/css/components/*.css   form components (Task 1)
backend/static/css/base.css                         .layout-form, inputs, .action, errors (Task 1)
backend/templates/registration/*.html                sign in, sign up, throttled, reset, verify, checkout (Tasks 2-3)
backend/drop_core/views.py (+ auth URL config)       signed-in redirect; bare 429s on the shell (Tasks 2, 6)
backend/templates/device/*.html, device/includes/*   the device flow (Task 4)
backend/templates/oauth/authorize.html, drop_core/oauth.py   OAuth consent (Task 5)
backend/templates/{404,403,403_csrf,500}.html, artifact 404, config/urls.py, settings   error pages (Task 6)
site-tests/run_site_checks.py, site-tests/make_form_fixtures.py, .github/workflows/ci.yml, pages/tests_site_parts.py   forms part (Task 7)
backend/templates/includes/* (removed), drop_core/tests_menu.py, tests_ambient.py (retired), AGENTS.md, spec   cleanup (Task 8)
```

## Tasks

1. The form layout and its components
2. Sign in, sign up, the signed-in visit, the throttled page and checkout confirm
3. Password reset and email verification
4. The device flow on the form layout
5. OAuth consent on the form layout
6. Error pages
7. Browser checks for the forms, and the `site-forms` CI job
8. The old includes go, plus recipes, the spec and the last checks

The tasks run in order. Each one quotes the files as the tasks before it leave them, and each ends with the whole Django suite green and its own commit.

---

### Task 1: The form layout and its components

**Files:**
- Create: `backend/templates/layouts/form.html`
- Create: `backend/templates/components/field.html`
- Create: `backend/templates/components/form_errors.html`
- Create: `backend/static/css/components/field.css`
- Create: `backend/static/css/components/form_errors.css`
- Modify: `backend/static/css/tokens.css` (the `--form-measure` token)
- Modify: `backend/static/css/base.css` (a "Forms" section after the reading column)
- Modify: `backend/templates/shell.html` (two stylesheet links)
- Modify: `backend/pages/catalog.py` and `backend/pages/views.py` (an `Entry` can show several states; two new entries)
- Modify: `backend/pages/tests_site_rules.py` (the new test module joins `READING_TEST_MODULES`, so its classes are held to `longMessage = False`)
- Modify: `AGENTS.md` (the repo map names the layout; the component recipe names `states`; the note on readable failures names the form pages)
- Test (create): `backend/pages/tests_forms.py`

**Interfaces:**
- Consumes:
  - `shell.html` and its blocks `preload_display`, `body_class` and `main`; the tokens in `tokens.css`; the `.label`, `.interactive` and `button.label` rules in `base.css`.
  - The test helpers `pages.tests_reading.declarations` and `rules` (a stylesheet's rules as data), `pages.tests_head.request_for`, `pages.tests_shell.render_shell` and `pages.tests_chapters.BANNED`.
  - `READING_TEST_MODULES` in `pages/tests_site_rules.py`, as `main` has it: `("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading", "blog.tests_journal", "blog.tests_feed")`.
- Produces:
  - **`layouts/form.html`**: extends `shell.html`, body class `layout-form`, `{% block main %}<div class="form-page">{% block form_page %}{% endblock %}</div>{% endblock %}`. It preloads the display face (italic 300) for the title. A page that extends it has no `data-layout`, so nothing on it changes in place. A page sets its own title, `{% block title %}Name | Veilfile{% endblock %}` (a vertical bar, as every page of the new site has; the voice rules ban the em dash).
  - **`--form-measure`** in `tokens.css`: `26rem`, about 50 characters of Newsreader.
  - **`components/field.html`**, inputs:
    - `id` (the input's id and the label's `for`; the help's id is `<id>_help` and the error box's `<id>_error`, and the input's `aria-describedby` lists the help's id, then the error's);
    - `name`, `label`;
    - `type` (`text` by default, `email`, `password`);
    - `value` (kept after an error, **never printed for a password**);
    - `autocomplete`, `required`, `autofocus`;
    - `help` (shown above the input; plain text, escaped, or trusted HTML such as a form field's `help_text`);
    - `errors` (`form.field.errors` or **a list** of sentences, **never a bare string**: the component walks it one sentence at a time, so a string would print a letter to a paragraph);
    - `extra` (exact attribute text, **a string literal in the page's template, never a variable**);
    - `mono` (true for a code: mono, tracked, capitals).
  - **`components/form_errors.html`**, inputs: `errors` (`form.non_field_errors` or **a list**, never a bare string) and `message` (one fixed sentence of the page's own, shown first). It is `role="alert"` and draws nothing when both are empty.
  - **The markup other tasks write by hand** (all styled in `base.css`; bare elements inside `.form-page` need no class):
    - the title is a plain `<h1>` inside `.form-page`; `p`, `ul`, `ol`, `li`, `strong`, `code`, `dl`, `dt`, `dd`, `fieldset`, `legend`, and `h2`/`h3` (a mono label) are styled where they stand;
    - the main action is `<button class="action interactive" type="submit">` (or an `<a>`), always with `interactive` and no other class (`.action` alone has no hover, press or focus fill); two of equal weight go in `<div class="form-actions">`, each an `.action` or a `<form>` holding one;
    - secondary actions are `<p class="form-links">` holding `<a class="label interactive">` or `<button class="label interactive">`, and plain words in a `<span>`;
    - a quiet status line is `<p class="notice" role="status">` (`role="note"` where it is not announced), with `<strong>` in `--ink`;
    - a checkbox or radio row is `<label class="check"><input class="check__input" type="checkbox"|"radio" ...><span class="check__text">words<span class="check__detail">smaller muted words</span></span></label>`; a group sits in a `<fieldset>` with a `<legend>`.
  - **The catalog**: `Entry.states`, a tuple of more contexts that the frame draws after `context`'s, one under the other (their ids must differ). `Entry("field", ...)` shows blank, with help, with an error and a code; `Entry("form_errors", ...)` shows a message and a list. Both use `within="form-page"`.
  - **Tests** in `backend/pages/tests_forms.py`, a module held to `longMessage = False` by `READING_TEST_MODULES` (Step 6). Two classes hold every template of the site to the conventions the form pages share, so a later task that breaks one fails at once, with a message naming its file and the fix:
    - `FieldExtraConventionTests` fails a template that passes `extra` a variable;
    - `FormTemplateConventionTests` fails a template that passes the field or `form_errors` an `errors` that is a string literal, a template on the form layout whose title is not `Name | Veilfile`, and a template that gives an element the class `action` without `interactive` (only `.interactive` gives the main action its ink wash and focus fill).

**What the design decides** (the owner's choices are the Rulings; these are the rest):
- **An input's single line is 1 px of `--muted`, round a `--box` field** (the hairline input), not `--hair`. A field's boundary is what identifies it, so it must meet WCAG 1.4.11 (3:1): `--hair` is 1.45:1 against Paper and 1.72:1 against Ink, `--muted` is 6.6:1 and 7.4:1, and spec 3.2's measure of the cloth holds `--muted` at 4.68:1 or better in Paper and 4.84:1 in Ink at the cloth's worst. The line is still 1 px. The main action's box stays `--hair` (below): a button with a text label is identified by its text.
- **Typed text is `--ink`**, which the calmed cloth gives 8:1 or better. The `--box` veil is not needed for contrast, but it can only help: it moves the cloth behind the text away from the text's colour in both appearances (lighter in Paper, darker in Ink). From `FLOOR` and `CEILING` in `veil.js`, ink text over the worst cloth is about 8.7:1 in Paper and 9.3:1 in Ink without the veil, and about 9.9:1 and 10.6:1 with it. The veil also makes a field read as a field.
- **An error uses no colour**, as ruled. The field's line goes from `--muted` to solid `--ink` (`aria-invalid="true"`, and `:user-invalid` after the visitor has been in the field), the sentence is full `--ink` under the field, and a mono label "Check this" sits above it. The label is `aria-hidden`: a screen reader hears the sentences and "invalid data". A form's own errors (`form_errors`) have a 2 px solid `--ink` line down the left, as a notice has a 2 px `--hair` one.
- **`:user-invalid`, never `:invalid`.** `:invalid` matches a required empty field on first paint, so every form would load looking wrong. It has a rule of its own: a browser that does not know the selector drops the whole rule it is in.
- **Autofill** must not paint the browser's yellow or blue. Chromium and Safari set the background and text colour with `!important`, which no author rule beats, so the change is held off: `transition: background-color 600000s 0s, color 600000s 0s` keeps the field at the background it had, and `-webkit-text-fill-color` keeps the text `--ink`. Firefox tints with a filter (`filter: none`). The rules are written once for `:-webkit-autofill` and once for `:autofill`, never in one list. With Motion off every transition is off (`base.css`), so the hold goes too; there is no cloth then, only paper, and the field is painted `--paper` with an inset `box-shadow`.
- **16 px on phones**: `font-size: max(16px, var(--reading-size))`, so 17.5 px on phones and 18.5 px beyond. The mono (code) variant is `max(16px, 0.9 * --reading-size)`.
- **The help sits above the input**, between the label and the field. On a phone the on-screen keyboard covers what is under a focused field, and a password's rules are wanted while it is typed.
- **Focus.** The global `:focus-visible` outline in `base.css` (1 px `--muted`, 3 px off the box) is the field's focus ring. The field's line is `--muted` already, so focus changes no colour and the field has no focus rule of its own; a test pins the global ring, so nobody removes it from under the forms. `scroll-margin-top: var(--space-5)` on the input means a focused field stops with its label showing, below the phone's top fade (`scroll-padding-top` is already set on `.js.shell-ready` below 64rem).
- **A short form is centred between the corners from 64rem**, as a chapter is, and starts at the top below that, so the on-screen keyboard does not move it. The column wraps anywhere (`overflow-wrap: anywhere`, `min-width: 0`, and `min-width: 0` on a `fieldset`), so a very long email or user agent never scrolls the page at 320.
- **Checkbox and radio** boxes are drawn in `--muted` (6.6:1 in Paper, 7.4:1 in Ink: a box has no label inside it to identify it), as a field's line is. The mark is a square, or for a radio a dot, in `--ink`: a shape, not a colour, and not the check mark that no font of the site has. In forced colours the system's own control is used. The device and OAuth pages take their choice and confirmation rows from `.check` and their equal actions from `.form-actions`, and need no CSS of their own for either.
- **CSRF.** The token is the page's own `<form>`'s business: a component included with `only` never sees it (verified), and a test holds both components free of `csrf_token`.
- **Why `extra` must be a literal.** A literal in a template is safe text, so its quotes survive. A variable is escaped, which stops it closing the tag, but a space in it still starts a new attribute (`onfocus=...`), so escaping is not enough. The convention test scans every template.

**A page, end to end** (reference, not a file to create; it was rendered with a failed `AuthenticationForm` to check it):

```django
{% extends "layouts/form.html" %}
{% block title %}Log in | Veilfile{% endblock %}
{% block form_page %}
<h1>Log in</h1>
{% if form.errors %}{% include "components/form_errors.html" with message="Invalid username, email, or password. Try again." only %}{% endif %}
<form method="post">
{% csrf_token %}
{% include "components/field.html" with id="id_username" name="username" label="Username or email" autocomplete="username" required=True autofocus=True value=form.username.value extra='autocapitalize="none" spellcheck="false"' only %}
{% include "components/field.html" with id="id_password" name="password" label="Password" type="password" autocomplete="current-password" required=True only %}
<button class="action interactive" type="submit">Log in</button>
</form>
<p class="form-links"><span>No account?</span> <a class="label interactive" href="{% url 'signup' %}">Sign up</a> <a class="label interactive" href="{% url 'password_reset' %}">Forgot password?</a></p>
{% endblock %}
```

The `<form>` has no `action`, so `?next=` and `?plan=` ride along. A field takes its errors straight from the form: `errors=form.email.errors`, and `value=form.email.value` keeps what was typed.

**Existing tests.** None of them breaks, and the whole suite (1,350 tests) was run green on a copy of the tree with these exact files applied. What was checked:
- The tests that list the shell's stylesheets use `assertIn` and no count (`pages/tests_shell.py`, `ShellHeadTests`); `tests_reading.OwnTemplatesTests` orders only the reading components. Two more links break neither.
- Every test that reads `base.css` by regex (`tests_shell`: top fade, tap highlight, the press fill; `tests_reading`: the prose-link rules, the em dash, the small cascade). The new section sits after the reading column, adds no selector to a pinned rule list, and has no em dash.
- The catalog tests: `Entry` gains a field with a default, and the view draws `context` plus `states`, so a one-sample entry renders as before. `test_ids_are_unique` passes because every state has an id of its own.
- AGENTS.md readers: `tests_site_parts` reads two paragraphs this task does not touch, and `StaleWordsTests` looks for a phrase these sentences do not contain.
- `tests_site_rules.ReadableFailuresTests` imports each module of `READING_TEST_MODULES` and fails a class whose `longMessage` is not `False`. Every class of `pages.tests_forms` sets it, so the module passes once it is listed (Step 6). Nothing else of `tests_site_rules` changes.
- Two tests fail on purpose between Steps 2 and 4 (`tests_catalog.CatalogCoverageTests.test_every_component_has_an_entry` and `tests_site_rules.TemplateRulesTests.test_every_component_has_a_stylesheet_and_a_catalog_entry`): a component with no stylesheet and no catalog entry is exactly what those rules catch.
- The old pages' own tests (`AccessibilityTests`, `tests_menu`, `tests_ambient`) test the old pages and are untouched here. `FieldWithADjangoFormTests` shows that the component keeps the three attributes `test_signup_errors_linked_to_fields` pins (`aria-invalid="true"`, `aria-describedby="id_password2_error"`, `id="id_password2_error"`).

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_forms.py`:

```python
"""The form layout and its pieces (spec 4.7, 6.2): layouts/form.html, the field and form_errors components, the CSS of the form column,
the main action, the secondary links, notices and the checkbox and radio rows, and the conventions every form page shares."""
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import engines
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils.safestring import mark_safe

from drop_core.forms import SignupForm
from pages import catalog
from pages.tests_chapters import BANNED
from pages.tests_head import request_for
from pages.tests_reading import declarations, rules
from pages.tests_shell import render_shell

BACKEND = Path(settings.BASE_DIR)
STATIC = BACKEND / "static"
TEMPLATES = BACKEND / "templates"
BASE_CSS = STATIC / "css" / "base.css"
FIELD_CSS = STATIC / "css" / "components" / "field.css"
ERRORS_CSS = STATIC / "css" / "components" / "form_errors.css"
OWN_TEMPLATES = ("layouts/form.html", "components/field.html", "components/form_errors.html")


class _Tags(HTMLParser):
    """Every start tag of the HTML fed to it, in order, as (name, {attribute: value}); an attribute written bare, such as required, has the value None."""

    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, dict(attrs)))


def tags(html, name):
    """The attributes of every <name> in html, in order."""
    parser = _Tags()
    parser.feed(html)
    parser.close()
    return [attrs for tag, attrs in parser.found if tag == name]


def field(**inputs):
    """components/field.html as a page includes it: with these inputs and nothing else."""
    return render_to_string("components/field.html", {"id": "id_name", "name": "name", "label": "Name", **inputs})


def form_errors(**inputs):
    return render_to_string("components/form_errors.html", inputs)


def page(source, context=None):
    """A template written as a string, rendered for a request outside the registry (a form page has no registry entry)."""
    return engines["django"].from_string(source).render(context or {}, request_for("/app/components/"))


def without_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


INCLUDE_FIELD = re.compile(r"{%\s*include\s+[\"']components/field\.html[\"'](.*?)%}")
NOT_A_LITERAL = re.compile(r"\bextra=(?![\"'])")


def variable_extras(text):
    """The includes of the field component in text whose extra is not a string literal."""
    return [include for include in INCLUDE_FIELD.findall(text) if NOT_A_LITERAL.search(include)]


INCLUDE_PIECE = re.compile(r"{%\s*include\s+[\"']components/(?:field|form_errors)\.html[\"'](.*?)%}")
STRING_ERRORS = re.compile(r"\berrors=[\"']")
EXTENDS_FORM = re.compile(r"{%\s*extends\s+[\"']layouts/form\.html[\"']\s*%}")
TITLE_BLOCK = re.compile(r"{%\s*block title\s*%}(.*?){%\s*endblock\s*%}", re.S)
CLASS_ATTRIBUTE = re.compile(r'\bclass="([^"]*)"')


def without_template_comments(text):
    return re.sub(r"{#.*?#}", "", text, flags=re.S)


def string_errors(text):
    """The includes of the field or form_errors component in text whose errors is a string literal, which the component would walk a letter at a time."""
    return [include for include in INCLUDE_PIECE.findall(without_template_comments(text)) if STRING_ERRORS.search(include)]


def title_problem(text):
    """What is wrong with the title of a template on the form layout, or None: it must be written, as the page's name and then ' | Veilfile'."""
    text = without_template_comments(text)
    if not EXTENDS_FORM.search(text):
        return None
    block = TITLE_BLOCK.search(text)
    if block is None:
        return "has no {% block title %}, so its title is the bare word Veilfile"
    title = block.group(1).strip()
    if not re.fullmatch(r".*\S \| Veilfile", title, re.S) or "—" in title:
        return f"has the title {title!r}; write the page's name, then ' | Veilfile' (a vertical bar: the voice rules ban the em dash)"
    return None


def actions_without_interactive(text):
    """The class attributes of text that name an element's class action without interactive."""
    found = []
    for value in CLASS_ATTRIBUTE.findall(without_template_comments(text)):
        classes = re.sub(r"{%.*?%}|{{.*?}}", " ", value).split()
        if "action" in classes and "interactive" not in classes:
            found.append(value)
    return found


class FieldTests(SimpleTestCase):
    longMessage = False  # a failure prints the message, which names the file and the fix
    FILE = "backend/templates/components/field.html"

    def test_a_blank_field_is_a_label_above_the_input_it_names(self):
        html = field(autocomplete="username")
        label, control = tags(html, "label"), tags(html, "input")
        self.assertEqual((len(label), len(control)), (1, 1), f"{self.FILE}: one <label> and one <input>")
        self.assertEqual(label[0]["for"], control[0]["id"], f"{self.FILE}: the label's for is the input's id, so a click on the label and a screen reader find the input")
        self.assertEqual(label[0]["class"], "field__label label", f'{self.FILE}: the label is class="field__label label" (mono, uppercase, muted: base.css .label)')
        self.assertLess(html.index("<label"), html.index("<input"), f"{self.FILE}: the label comes above the input")
        self.assertEqual((control[0]["name"], control[0]["type"], control[0]["autocomplete"]), ("name", "text", "username"), f"{self.FILE}: print name, type and autocomplete")
        for absent in ("value", "required", "autofocus", "aria-invalid", "aria-describedby"):
            self.assertNotIn(absent, control[0], f"{self.FILE}: print {absent} only when the page asks for it")
        self.assertNotIn("field__error", html, f"{self.FILE}: no error markup without errors")

    def test_the_type_is_text_unless_the_page_says_so(self):
        for given, expected in ((None, "text"), ("email", "email"), ("password", "password")):
            with self.subTest(type=given):
                html = field() if given is None else field(type=given)
                self.assertEqual(tags(html, "input")[0]["type"], expected, f"{self.FILE}: type is the page's type, text by default")

    def test_required_autofocus_and_autocomplete_are_printed_when_asked_for(self):
        control = tags(field(required=True, autofocus=True, autocomplete="current-password"), "input")[0]
        self.assertIn("required", control, f"{self.FILE}: print required when the page passes required=True")
        self.assertIn("autofocus", control, f"{self.FILE}: print autofocus when the page passes autofocus=True")
        self.assertEqual(control["autocomplete"], "current-password", f"{self.FILE}: keep the autocomplete token the page passes")
        plain = tags(field(required=False, autofocus=False), "input")[0]
        self.assertNotIn("required", plain, f"{self.FILE}: no required without required=True")
        self.assertNotIn("autofocus", plain, f"{self.FILE}: no autofocus without autofocus=True (a page that does not exist to take one input must not steal focus)")

    def test_a_value_is_kept_but_never_for_a_password(self):
        self.assertEqual(tags(field(type="email", value="ana@example.com"), "input")[0]["value"], "ana@example.com", f"{self.FILE}: keep the value after an error")
        secret = field(type="password", value="hunter2-secret")
        self.assertNotIn("hunter2-secret", secret, f"{self.FILE}: never print a password back, whatever the page passes")
        self.assertNotIn("value", tags(secret, "input")[0], f"{self.FILE}: no value attribute on a password")
        self.assertNotIn("value", tags(field(value=None), "input")[0], f"{self.FILE}: a value of None prints nothing, not the word None")
        awkward = 'a"b<c&d'
        html = field(value=awkward)
        self.assertEqual(tags(html, "input")[0]["value"], awkward, f"{self.FILE}: the value round-trips through the attribute")
        self.assertNotIn(awkward, html, f"{self.FILE}: print the value escaped")

    def test_the_help_sits_between_the_label_and_the_input_and_describes_it(self):
        html = field(help="We verify this address before issuing API keys.")
        box = [attrs for attrs in tags(html, "div") if attrs.get("class") == "field__help"]
        self.assertEqual([attrs["id"] for attrs in box], ["id_name_help"], f'{self.FILE}: <div class="field__help" id="ID_help">')
        self.assertEqual(tags(html, "input")[0]["aria-describedby"], "id_name_help", f"{self.FILE}: aria-describedby names the help")
        self.assertLess(html.index("<label"), html.index('class="field__help"'), f"{self.FILE}: the help comes after the label")
        self.assertLess(html.index('class="field__help"'), html.index("<input"),
                        f"{self.FILE}: the help comes above the input, where the on-screen keyboard does not cover it")

    def test_help_is_trusted_html_only_when_the_page_marks_it_so(self):
        listed = field(help=mark_safe("<ul><li>One.</li><li>Two.</li></ul>"))
        self.assertIn('<div class="field__help" id="id_name_help"><ul><li>One.</li><li>Two.</li></ul></div>', listed,
                      f"{self.FILE}: a safe help (a form field's help_text) keeps its list")
        plain = field(help="Fish & <b>chips</b>")
        self.assertIn("Fish &amp; &lt;b&gt;chips&lt;/b&gt;", plain, f"{self.FILE}: print help without |safe, so plain text is escaped")
        self.assertNotIn("<b>", plain)

    def test_errors_mark_the_input_invalid_and_tie_their_sentences_to_it(self):
        html = field(errors=["Enter a valid email address.", "It must have an at sign."])
        control = tags(html, "input")[0]
        self.assertEqual(control["aria-invalid"], "true", f'{self.FILE}: aria-invalid="true" on an input with errors')
        self.assertEqual(control["aria-describedby"], "id_name_error", f"{self.FILE}: aria-describedby names the error box")
        box = [attrs for attrs in tags(html, "div") if attrs.get("class") == "field__error"]
        self.assertEqual([attrs["id"] for attrs in box], ["id_name_error"], f'{self.FILE}: <div class="field__error" id="ID_error">')
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["Enter a valid email address.", "It must have an at sign."],
                         f"{self.FILE}: one <p> for each error, in order")
        self.assertIn('<span class="field__flag label" aria-hidden="true">Check this</span>', html,
                      f"{self.FILE}: the mono label Check this sits above the sentences; it is for the eye, so a screen reader hears the sentences and invalid data only")
        self.assertLess(html.index("<input"), html.index("field__error"), f"{self.FILE}: the errors come under the input")
        self.assertIn('class="field field--error"', html, f"{self.FILE}: the wrapper says it holds an error")

    def test_help_and_errors_both_describe_the_input_help_first(self):
        control = tags(field(help="Some help.", errors=["Some error."]), "input")[0]
        self.assertEqual(control["aria-describedby"], "id_name_help id_name_error", f"{self.FILE}: aria-describedby is the help's id, a space, then the error's")

    def test_no_errors_draws_nothing_of_an_error(self):
        for errors in (None, [], ()):
            with self.subTest(errors=errors):
                html = field(errors=errors)
                for absent in ("field__error", "field--error", "aria-invalid", "Check this"):
                    self.assertNotIn(absent, html, f"{self.FILE}: with no errors print no {absent}")
        self.assertNotIn("Check this", field(), f"{self.FILE}: errors left out print no error markup")

    def test_extra_is_the_pages_exact_attribute_text_when_it_is_a_literal(self):
        source = ('{% include "components/field.html" with id="id_user_code" name="user_code" label="Device code" '
                  '''extra='placeholder="XXXX-XXXX" spellcheck="false"' only %}''')
        control = tags(page(source), "input")[0]
        self.assertEqual((control["placeholder"], control["spellcheck"]), ("XXXX-XXXX", "false"),
                         f"{self.FILE}: print extra as it is; a literal in the page's template is safe text, so its quotes survive")

    def test_extra_from_a_variable_cannot_close_the_tag(self):
        # Its quotes and brackets are escaped. It could still add an attribute (a space starts one), which is why a variable is not allowed at
        # all: FieldExtraConventionTests.
        source = '{% include "components/field.html" with id="id_x" name="x" label="X" extra=bad only %}'
        html = page(source, {"bad": '"><script>alert(1)</script>'})
        self.assertNotIn("<script>", html, f"{self.FILE}: never mark extra safe; text that came from anywhere but the template stays escaped")
        self.assertEqual(html.count("<input"), 1)

    def test_a_code_is_marked_mono(self):
        self.assertIn("field__input--mono", tags(field(mono=True), "input")[0]["class"], f"{self.FILE}: mono=True adds field__input--mono")
        self.assertEqual(tags(field(), "input")[0]["class"], "field__input", f"{self.FILE}: without mono the input has only field__input")

    def test_words_and_ids_are_escaped(self):
        html = field(label="<script>alert(1)</script>", id='a" onclick="x')
        self.assertNotIn("<script>", html, f"{self.FILE}: print the label escaped")
        self.assertNotIn("onclick", tags(html, "input")[0], f"{self.FILE}: print the id escaped")

    def test_the_component_holds_no_form_and_no_csrf_token(self):
        source = '{% include "components/field.html" with id="id_x" name="x" label="X" only %}'
        html = engines["django"].from_string(source).render({"csrf_token": "leaked-token"}, request_for("/app/components/"))
        for absent in ("csrfmiddlewaretoken", "leaked-token", "<form"):
            self.assertNotIn(absent, html, f"{self.FILE}: the page's own <form> holds the CSRF token; a component included with only never sees it")
        for name in ("field", "form_errors"):
            text = (TEMPLATES / "components" / f"{name}.html").read_text(encoding="utf-8")
            self.assertNotIn("csrf_token", text, f"backend/templates/components/{name}.html: leave the CSRF token to the page's own <form>")


class FieldExtraConventionTests(SimpleTestCase):
    """extra is attribute text printed as it is. A literal in a template is the author's own; a variable could carry a visitor's words, and
    a space in it starts a new attribute (onfocus=...), which escaping does not stop. So every page passes it as a string literal."""
    longMessage = False

    def test_the_check_tells_a_literal_from_a_variable(self):
        literal = '''{% include "components/field.html" with id="x" extra='spellcheck="false"' only %}'''
        double = '{% include "components/field.html" with id="x" extra="inputmode=numeric" only %}'
        variable = '{% include "components/field.html" with id="x" extra=bad only %}'
        self.assertEqual(variable_extras(literal + double), [], "backend/pages/tests_forms.py: variable_extras must accept a string literal in either quote")
        self.assertEqual(len(variable_extras(variable)), 1, "backend/pages/tests_forms.py: variable_extras must find extra=name")

    def test_no_template_passes_extra_a_variable(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = variable_extras(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"backend/templates/{path.relative_to(TEMPLATES)}: pass the field's extra as a string literal "
                                        "(extra='placeholder=\"XXXX-XXXX\"'), never a variable: a space in a variable starts a new attribute, "
                                        "so a visitor's words could add an onfocus handler")


class FormTemplateConventionTests(SimpleTestCase):
    """What every form page shares, held for every template of the site, so a page written later that breaks one fails here and its
    message names its file: errors are a list (never a string), a form page is titled `Name | Veilfile`, and the main action is interactive."""
    longMessage = False

    def test_the_checks_tell_the_right_from_the_wrong(self):
        where = "backend/pages/tests_forms.py"
        for source in ('{% include "components/field.html" with id="x" errors="Bad." only %}',
                       "{% include 'components/form_errors.html' with errors='Bad.' only %}"):
            self.assertEqual(len(string_errors(source)), 1, f"{where}: string_errors must find errors given as a string literal in either quote")
        for source in ('{% include "components/field.html" with id="x" errors=form.email.errors only %}',
                       '{% include "components/form_errors.html" with message="Invalid username, email, or password. Try again." only %}',
                       '{# {% include "components/field.html" with errors="Bad." only %} #}'):
            self.assertEqual(string_errors(source), [], f"{where}: string_errors must pass a variable, a message and a comment")

        def form(title):
            return '{% extends "layouts/form.html" %}{% block title %}' + title + "{% endblock %}"

        for good in ("Log in | Veilfile", "{{ title }} | Veilfile", "{% if x %}A{% else %}B{% endif %} | Veilfile"):
            self.assertIsNone(title_problem(form(good)), f"{where}: title_problem must accept {good!r}")
        for bad in ("Log in — Veilfile", "Log in", "| Veilfile", "Veilfile", ""):
            self.assertIsNotNone(title_problem(form(bad)), f"{where}: title_problem must refuse {bad!r}")
        self.assertIsNotNone(title_problem('{% extends "layouts/form.html" %}'), f"{where}: title_problem must refuse a form page with no title block")
        self.assertIsNone(title_problem('{% extends "layouts/chapter.html" %}{% block title %}Not found | Veilfile{% endblock %}'),
                          f"{where}: title_problem looks at the form layout's pages only")
        self.assertIsNone(title_problem("<p>no layout</p>"), f"{where}: title_problem looks at the form layout's pages only")
        for good in ('<button class="action interactive" type="submit">', '<a class="action label interactive" href="/">', '<p class="device-actions">',
                     '{# <button class="action"> #}', '<div class="form-actions">'):
            self.assertEqual(actions_without_interactive(good), [], f"{where}: actions_without_interactive must accept {good!r}")
        for bad in ('<button class="action" type="submit">', '<a class="action{% if x %} wide{% endif %}" href="/">'):
            self.assertEqual(len(actions_without_interactive(bad)), 1, f"{where}: actions_without_interactive must find {bad!r}")

    def test_no_template_passes_errors_a_string(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = string_errors(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"backend/templates/{path.relative_to(TEMPLATES)}: pass errors as a list of sentences (form.field.errors, "
                                        "form.non_field_errors, or a list the view builds), never a string literal: the component walks it a letter "
                                        "at a time. For one fixed sentence of the page's own, form_errors takes message=")

    def test_every_form_page_is_titled_with_its_name_and_veilfile(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            problem = title_problem(path.read_text(encoding="utf-8"))
            self.assertIsNone(problem, f"backend/templates/{path.relative_to(TEMPLATES)}: {problem}")

    def test_every_main_action_is_interactive(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = actions_without_interactive(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f'backend/templates/{path.relative_to(TEMPLATES)}: write the main action as class="action interactive" '
                                        "(a button or a link). Only .interactive gives it the ink wash and the focus fill the owner ruled for it")


class FieldWithADjangoFormTests(TestCase):
    """What every form page does: hand a field's errors and value to the component straight from the Django form."""
    longMessage = False
    SOURCE = (
        '{% include "components/form_errors.html" with errors=form.non_field_errors only %}'
        '{% include "components/field.html" with id="id_email" name="email" label="Email" type="email" value=form.email.value '
        'autocomplete="email" required=True errors=form.email.errors only %}'
        '{% include "components/field.html" with id="id_password2" name="password2" label="Confirm password" type="password" '
        'autocomplete="new-password" required=True errors=form.password2.errors only %}'
    )

    def setUp(self):
        get_user_model().objects.create_user("taken", "ana@example.com", "a-test-only-password")
        self.form = SignupForm(data={"username": "ana", "email": "Ana@Example.com",
                                     "password1": "a-test-only-password-1", "password2": "a-different-password"})
        self.assertFalse(self.form.is_valid())
        self.html = page(self.SOURCE, {"form": self.form})
        self.inputs = {attrs["id"]: attrs for attrs in tags(self.html, "input")}

    def test_a_field_error_is_tied_to_its_input_the_way_the_old_pages_pinned(self):
        control = self.inputs["id_password2"]
        self.assertEqual(control["aria-invalid"], "true", 'backend/templates/components/field.html: aria-invalid="true" when form.field.errors is not empty')
        self.assertEqual(control["aria-describedby"], "id_password2_error",
                         "backend/templates/components/field.html: aria-describedby is the id of the error box, id_<field>_error (drop_core.tests.AccessibilityTests pinned this)")
        self.assertIn('id="id_password2_error"', self.html)
        self.assertIn(escape(self.form.errors["password2"][0]), self.html, "backend/templates/components/field.html: print each of the field's errors")

    def test_the_value_is_kept_and_a_password_is_not(self):
        self.assertEqual(self.inputs["id_email"]["value"], "Ana@Example.com", "backend/templates/components/field.html: keep value=form.email.value after an error")
        self.assertIn("An account with this email address already exists.", self.html)
        for typed in ("a-different-password", "a-test-only-password-1"):
            self.assertNotIn(typed, self.html, "backend/templates/components/field.html: a password is never printed back")

    def test_non_field_errors_become_an_alert(self):
        self.form.add_error(None, "Sign-up is paused for this network.")
        html = page(self.SOURCE, {"form": self.form})
        self.assertIn('<div class="form-errors" role="alert">', html, 'backend/templates/components/form_errors.html: role="alert" on the summary')
        self.assertIn("<p>Sign-up is paused for this network.</p>", html)


class FormErrorsTests(SimpleTestCase):
    longMessage = False
    FILE = "backend/templates/components/form_errors.html"

    def test_nothing_is_drawn_when_there_is_nothing_to_say(self):
        for inputs in ({}, {"errors": None}, {"errors": []}, {"errors": ()}, {"message": ""}):
            with self.subTest(inputs=inputs):
                self.assertEqual(form_errors(**inputs).strip(), "", f"{self.FILE}: draw nothing when errors and message are empty or absent")

    def test_the_sentences_are_an_alert_under_the_mark(self):
        html = form_errors(errors=["Your current password is incorrect.", "Try again."])
        box = tags(html, "div")[0]
        self.assertEqual((box["class"], box["role"]), ("form-errors", "alert"), f'{self.FILE}: <div class="form-errors" role="alert">')
        self.assertIn('<span class="form-errors__flag label" aria-hidden="true">Check this</span>', html)
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["Your current password is incorrect.", "Try again."], f"{self.FILE}: one <p> for each error, in order")

    def test_a_message_is_one_sentence_before_the_errors(self):
        html = form_errors(message="Invalid username, email, or password. Try again.", errors=["Second."])
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["Invalid username, email, or password. Try again.", "Second."],
                         f"{self.FILE}: message first, then each error")

    def test_sentences_are_escaped(self):
        html = form_errors(errors=["<b>No</b> & no"])
        self.assertNotIn("<b>", html, f"{self.FILE}: print each error escaped")
        self.assertIn("&lt;b&gt;No&lt;/b&gt; &amp; no", html)


class FormLayoutTests(SimpleTestCase):
    longMessage = False
    FILE = "backend/templates/layouts/form.html"

    def setUp(self):
        self.html = page('{% extends "layouts/form.html" %}{% block form_page %}<h1>Form</h1><p>Words.</p>{% endblock %}')

    def test_the_body_wears_the_layouts_class(self):
        self.assertIn('<body class="layout-form" data-veil-src="/static/js/veil.js">', self.html,
                      f"{self.FILE}: the body_class block is layout-form, and the body has no data-layout: a form page is in no layout family")

    def test_the_words_sit_in_the_form_column_in_the_one_main(self):
        self.assertEqual((self.html.count("<main"), self.html.count("</main>")), (1, 1), f"{self.FILE}: one <main>, the shell's")
        self.assertIn('<main id="main" class="shell-main"><div class="form-page"><h1>Form</h1><p>Words.</p></div></main>', self.html,
                      f'{self.FILE}: the main block is <div class="form-page"> around a form_page block')

    def test_a_form_page_changes_nothing_in_place(self):
        self.assertNotIn("data-layout", self.html, "backend/templates/shell.html: data-layout comes only from a registry page's layout; a form page has none")
        self.assertNotIn("data-faces", self.html)

    def test_it_preloads_the_display_face_of_its_title_and_not_the_heading_face(self):
        self.assertIn('<link rel="preload" href="/static/fonts/cormorant-garamond/italic-300-latin.woff2" as="font" type="font/woff2" crossorigin>', self.html,
                      f"{self.FILE}: preload the display face in its preload_display block")
        self.assertNotIn("italic-400-latin", self.html, f"{self.FILE}: no form page sets a heading in italic 400")

    def test_the_csrf_token_stays_in_the_pages_own_form(self):
        html = page('{% extends "layouts/form.html" %}{% block form_page %}<form method="post">{% csrf_token %}'
                    '{% include "components/field.html" with id="id_x" name="x" label="X" only %}</form>{% endblock %}')
        self.assertEqual(html.count("csrfmiddlewaretoken"), 1, "a page with one form has one CSRF token: in the page's own <form>, not in the layout or a component")
        self.assertTrue(html.index("<form") < html.index("csrfmiddlewaretoken") < html.index("</form>"))

    def test_the_templates_keep_the_voice_and_put_no_header_or_footer_in_main(self):
        for name in OWN_TEMPLATES:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            words = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)  # a template tag may say != ; the words may not shout
            where = f"backend/templates/{name}"
            with self.subTest(template=name):
                self.assertNotRegex(text, r"<(header|footer)\b", f"{where}: base.css hides a bare <footer> without JavaScript; use a <div> or a <nav>")
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", words, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_component_stylesheets_come_after_base_css(self):
        # The form column's element rules and a component's class rules weigh the same; the later stylesheet wins.
        html = render_shell()
        base = html.index('href="/static/css/base.css"')
        for name in ("field", "form_errors"):
            link = f'href="/static/css/components/{name}.css"'
            with self.subTest(component=name):
                self.assertIn(link, html, f"backend/templates/shell.html: link css/components/{name}.css")
                self.assertGreater(html.index(link), base, f"backend/templates/shell.html: link css/components/{name}.css after css/base.css, so the component's classes override the form column's element rules")


class FormCssTests(SimpleTestCase):
    longMessage = False

    def setUp(self):
        self.base = BASE_CSS.read_text(encoding="utf-8")
        self.field = FIELD_CSS.read_text(encoding="utf-8")
        self.errors = ERRORS_CSS.read_text(encoding="utf-8")
        self.tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")

    def declared(self, css, selector, where):
        found = declarations(css, selector)
        self.assertTrue(found, f"{where}: add the rule `{selector} {{ ... }}` (spec 4.7)")
        return found

    def test_the_column_is_form_measure_wide_and_wraps_what_will_not_fit(self):
        column = self.declared(self.base, ".form-page", "static/css/base.css")
        for name, value in (("width", "100%"), ("min-width", "0"), ("max-width", "var(--form-measure)"), ("overflow-wrap", "anywhere")):
            self.assertEqual(column.get(name), value,
                             f"static/css/base.css: `.form-page` needs {name}: {value}; a long email, user agent or app name must wrap, not scroll the page sideways at 320")
        self.assertRegex(self.tokens, r"--form-measure:\s*[\d.]+rem;", "static/css/tokens.css: --form-measure is the form column's width")

    def test_a_short_form_is_centred_from_64rem_and_starts_at_the_top_below(self):
        self.assertRegex(self.base, r"\.layout-form \.shell-main \{[^}]*align-content: start;",
                         "static/css/base.css: `.layout-form .shell-main` starts at the top, so the on-screen keyboard does not move a form about")
        self.assertRegex(self.base, r"@media \(min-width: 64rem\) \{\s*\.layout-form \.shell-main \{\s*align-content: center;",
                         "static/css/base.css: from 64rem a short form is centred between the corners, as a chapter is")

    def test_inputs_are_at_least_16px_so_ios_does_not_zoom(self):
        for selector in (".field__input", ".field__input--mono"):
            size = self.declared(self.field, selector, "static/css/components/field.css").get("font-size", "")
            self.assertRegex(size, r"^max\(16px,", f"static/css/components/field.css: `{selector}` needs font-size: max(16px, ...); under 16px iOS zooms the page when the field takes focus")

    def test_a_field_is_a_muted_line_round_a_box_with_ink_text(self):
        box = self.declared(self.field, ".field__input", "static/css/components/field.css")
        self.assertEqual(box.get("border"), "1px solid var(--muted)",
                         "static/css/components/field.css: `.field__input` needs border: 1px solid var(--muted). The line is --muted, not --hair (1.45:1 on Paper), so the "
                         "field's boundary meets WCAG 1.4.11 (3:1)")
        self.assertNotIn("--hair", without_comments(self.field),
                         "static/css/components/field.css: draw nothing of a field in --hair; a control's boundary needs 3:1 (WCAG 1.4.11) and --hair is 1.45:1 on Paper, --muted 6.6:1")
        for name, value in (("background", "var(--box)"), ("color", "var(--ink)"), ("border-radius", "0"),
                            ("appearance", "none"), ("-webkit-appearance", "none"), ("width", "100%"), ("min-width", "0")):
            self.assertEqual(box.get(name), value, f"static/css/components/field.css: `.field__input` needs {name}: {value}")
        placeholder = self.declared(self.field, ".field__input::placeholder", "static/css/components/field.css")
        self.assertEqual((placeholder.get("color"), placeholder.get("opacity")), ("var(--muted)", "1"),
                         "static/css/components/field.css: a placeholder is --muted at full opacity (Firefox dims it otherwise)")

    def test_focus_is_visible_and_a_focused_field_clears_the_top_fade(self):
        ring = self.declared(self.base, ":focus-visible", "static/css/base.css")
        self.assertEqual(ring.get("outline"), "var(--outline-width) solid var(--muted)",
                         "static/css/base.css: the global `:focus-visible` outline is the focus ring of a field, whose line is --muted already; restore `outline: var(--outline-width) solid var(--muted)` (spec 3.4, 9)")
        self.assertTrue(self.declared(self.field, ".field__input", "static/css/components/field.css").get("scroll-margin-top"),
                        "static/css/components/field.css: `.field__input` needs scroll-margin-top, so a focused field stops with its label below the phone's top fade")
        for where, css in (("static/css/components/field.css", self.field), ("static/css/components/form_errors.css", self.errors)):
            self.assertNotRegex(without_comments(css), r"outline:\s*(none|0)\b", f"{where}: never remove the focus outline (spec 3.4, 9)")

    def test_an_error_is_a_solid_ink_line_and_a_full_ink_sentence_with_no_colour(self):
        where = "static/css/components/field.css"
        self.assertEqual(self.declared(self.field, '.field__input[aria-invalid="true"]', where).get("border-color"), "var(--ink)", f"{where}: an invalid field's line is solid --ink")
        self.assertEqual(self.declared(self.field, ".field__error p", where).get("color"), "var(--ink)", f"{where}: the error sentence is full --ink, not --muted")
        self.assertEqual(self.declared(self.field, ".field__flag", where).get("color"), "var(--ink)", f"{where}: the Check this label is --ink")
        self.assertEqual(self.declared(self.errors, ".form-errors", "static/css/components/form_errors.css").get("border-left"), "2px solid var(--ink)",
                         "static/css/components/form_errors.css: the summary has a solid --ink line down its left")

    def test_the_browser_finds_fault_only_after_interaction_and_in_its_own_rule(self):
        self.assertEqual(self.declared(self.field, ".field__input:user-invalid", "static/css/components/field.css").get("border-color"), "var(--ink)")
        self.assertNotRegex(without_comments(self.field), r"(?<!user-):invalid",
                            "static/css/components/field.css: use :user-invalid, never :invalid, which matches a required empty field before anyone has touched it and makes every form load looking wrong")
        for selectors, _ in rules(self.field):
            joined = " ".join(selectors)
            self.assertFalse(":user-invalid" in joined and "aria-invalid" in joined,
                             "static/css/components/field.css: give :user-invalid a rule of its own; a browser that does not know it drops the whole rule it is in, aria-invalid's line with it")

    def test_autofill_is_held_off_in_both_spellings_and_when_motion_is_off(self):
        where = "static/css/components/field.css"
        for selector in (".field__input:-webkit-autofill", ".field__input:autofill"):
            held = self.declared(self.field, selector, where)
            self.assertIn("background-color 600000s", held.get("transition", ""),
                          f"{where}: `{selector}` holds the browser's tint off with a transition delay that never ends (its !important background cannot be beaten by a rule)")
            self.assertEqual(held.get("-webkit-text-fill-color"), "var(--ink)", f"{where}: `{selector}` keeps the text --ink")
            self.assertEqual(held.get("filter"), "none", f"{where}: `{selector}` turns off Firefox's tint")
            quiet = self.declared(self.field, f'[data-motion="off"] {selector}', where)
            self.assertIn("var(--paper)", quiet.get("box-shadow", ""),
                          f"{where}: with Motion off every transition is off (base.css), so `{selector}` is painted --paper over the tint with an inset box-shadow")
        for selectors, _ in rules(self.field):
            joined = " ".join(selectors)
            self.assertFalse(":autofill" in joined and ":-webkit-autofill" in joined,
                             f"{where}: one rule for each spelling; a browser that does not know one drops the whole selector list")

    def test_the_main_action_is_a_hairline_box_on_the_box(self):
        action = self.declared(self.base, ".action", "static/css/base.css")
        for name, value in (("border", "1px solid var(--hair)"), ("background", "var(--box)"), ("color", "var(--ink)"), ("border-radius", "0"),
                            ("font-family", "var(--font-mono)"), ("text-transform", "uppercase")):
            self.assertEqual(action.get(name), value, f"static/css/base.css: `.action` needs {name}: {value} (the owner's main action: a mono label in a 1px --hair box on --box)")
        self.assertEqual(self.declared(self.base, ".action::before", "static/css/base.css").get("inset"), "0",
                         "static/css/base.css: the action's fill (.interactive::before) is inset to the box's own edge")
        self.assertTrue(declarations(self.base, ".action.interactive"), "static/css/base.css: `.action.interactive` shares .action's rule, so button.label and .label.interactive cannot undo the box")

    def test_actions_of_equal_weight_share_a_row_in_one_width(self):
        where = "static/css/base.css"
        self.assertEqual(self.declared(self.base, ".form-actions > *", where).get("flex"), "1 1 9rem", f"{where}: each item of `.form-actions` is flex: 1 1 9rem, so two share a row and wrap below 19rem")
        direct = self.declared(self.base, ".form-actions > .action", where)
        self.assertEqual(direct.get("margin"), "0", f"{where}: an .action directly in `.form-actions` has no margin of its own (`.action.interactive` weighs more than `.form-actions > *`)")
        self.assertNotIn("flex", direct, f"{where}: an .action directly in `.form-actions` keeps its item's flex, or Approve and Deny are two widths")
        self.assertEqual(self.declared(self.base, ".form-actions > * > .action", where).get("flex"), "1 1 auto", f"{where}: an .action in a <form> item fills the form")

    def test_secondary_links_and_notices_are_quiet(self):
        links = self.declared(self.base, ".form-links", "static/css/base.css")
        for name, value in (("color", "var(--muted)"), ("font-family", "var(--font-mono)"), ("text-transform", "uppercase")):
            self.assertEqual(links.get(name), value, f"static/css/base.css: `.form-links` needs {name}: {value} (quieter than the main action)")
        notice = self.declared(self.base, ".notice", "static/css/base.css")
        self.assertEqual((notice.get("color"), notice.get("border-left")), ("var(--muted)", "2px solid var(--hair)"),
                         "static/css/base.css: `.notice` is a quiet --muted line with the reading column's 2px hairline down its left")

    def test_checkbox_and_radio_rows_draw_their_own_boxes_and_marks(self):
        where = "static/css/base.css"
        self.assertRegex(self.base, r"\.check__input \{[^}]*\n  appearance: none;", f"{where}: `.check__input` draws its own box (appearance: none)")
        self.assertEqual(self.declared(self.base, ".check__input", where).get("border"), "1px solid var(--muted)", f"{where}: `.check__input` draws its box in --muted")
        self.assertIn("var(--ink)", self.declared(self.base, ".check__input:checked", where).get("background-image", ""), f"{where}: a checked box holds a square in --ink")
        self.assertIn("var(--ink)", self.declared(self.base, '.check__input[type="radio"]:checked', where).get("background-image", ""), f"{where}: a checked radio holds a dot in --ink")
        self.assertEqual(self.declared(self.base, '.check__input[type="radio"]', where).get("border-radius"), "50%")
        self.assertRegex(self.base, r"@media \(forced-colors: active\) \{\s*\.check__input \{[^}]*appearance: auto;",
                         f"{where}: in forced colours the cloth is off and the system's own box and mark are right")

    def test_nothing_climbs_above_the_corners_or_makes_main_a_backdrop_root(self):
        # AGENTS.md, Site checks: nothing inside <main> uses a z-index above 2 (the corners are at 3), and <main> has no filter, opacity,
        # mask or will-change outside a change's cross-fade, the narrow top fade and the phone sheet's mask.
        ours = (".form-page", ".layout-form", ".action", ".form-actions", ".form-links", ".notice", ".check")
        found = [(selectors, properties) for selectors, properties in rules(self.base) if any(selector.startswith(ours) for selector in selectors)]
        found += rules(self.field) + rules(self.errors)
        for selectors, properties in found:
            z_index = properties.get("z-index", "0")
            if re.fullmatch(r"-?\d+", z_index):
                self.assertLessEqual(int(z_index), 2, f"static/css: `{selectors[0]}` has z-index {z_index}; nothing inside <main> may use more than 2")
        main = declarations(self.base, ".layout-form .shell-main")
        for name in ("filter", "backdrop-filter", "opacity", "mask-image", "-webkit-mask-image", "will-change"):
            self.assertNotIn(name, main, f"static/css/base.css: `.layout-form .shell-main` must not set {name}: it would make <main> a backdrop root (AGENTS.md, Site checks)")

    def test_a_hover_waits_for_a_pointer_that_can_hover(self):
        # CI's Chromium reports hover: none, so a hover rule is gated on `not all and (pointer: coarse)`, never on (hover: hover).
        self.assertRegex(self.base, r"@media not all and \(pointer: coarse\) \{\s*\.form-page :is\(p, li, dd, \.notice\) a:not\(\.interactive, \.label\):hover \{",
                         "static/css/base.css: gate the form column's link hover on `not all and (pointer: coarse)`")


class FormCatalogTests(TestCase):
    longMessage = False

    def setUp(self):
        staff = get_user_model().objects.create_user("staff", "staff@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.html = self.client.get("/app/components/").content.decode()

    def test_the_field_is_shown_in_each_of_its_states_in_every_frame(self):
        for state in ("blank", "help", "error", "code"):
            for frame in range(4):
                with self.subTest(state=state, frame=frame):
                    self.assertIn(f'id="catalog-field-{state}-{frame}"', self.html,
                                  f"backend/pages/catalog.py: the field's Entry shows a {state} state; give each state an id of its own")

    def test_the_error_state_is_the_only_invalid_field_and_shows_its_sentence(self):
        self.assertEqual(self.html.count('aria-invalid="true"'), 4, "backend/pages/catalog.py: exactly one state of the field has errors, in four frames")
        self.assertEqual(self.html.count("An account with this email address already exists."), 4)
        self.assertEqual(self.html.count('aria-describedby="catalog-field-error-0_error"'), 1)

    def test_extra_arrives_as_written(self):
        self.assertEqual(self.html.count('placeholder="XXXX-XXXX" spellcheck="false"'), 4,
                         "backend/pages/catalog.py: pass the code state's extra through mark_safe, as a literal in a page's template is, or its quotes print escaped")

    def test_both_components_are_shown_inside_the_form_column(self):
        self.assertEqual(self.html.count('<div class="form-page">'), 4 * 2,
                         'backend/pages/catalog.py: pass within="form-page" to the field and form_errors Entries, so the catalog draws them in the column the page does')

    def test_the_form_errors_are_alerts_in_both_states(self):
        self.assertEqual(self.html.count('<div class="form-errors" role="alert">'), 4 * 2)
        self.assertIn("Invalid username, email, or password. Try again.", self.html)


class CatalogStatesTests(SimpleTestCase):
    longMessage = False

    def test_each_state_of_a_component_has_an_id_of_its_own(self):
        for entry in catalog.entries():
            samples = (entry.context, *entry.states)
            ids = [sample["id"] for sample in samples if "id" in sample]
            with self.subTest(component=entry.name):
                self.assertEqual(len(ids), len(set(ids)), "backend/pages/catalog.py: each state of a component needs an id of its own, or the catalog repeats an id")

    def test_the_field_shows_blank_help_error_and_code(self):
        entry = next(entry for entry in catalog.entries() if entry.name == "field")
        samples = (entry.context, *entry.states)
        self.assertEqual(len(samples), 4, "backend/pages/catalog.py: the field's Entry shows four states: blank, with help, with an error and a code")
        self.assertFalse(any(key in samples[0] for key in ("help", "errors", "mono")))
        self.assertTrue(samples[1]["help"] and samples[2]["errors"] and samples[3]["mono"])
```

- [ ] **Step 2: Run the tests and watch them fail**

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms
```

Expected: `Ran 55 tests` and `FAILED (failures=20, errors=53)`. Only the six tests that scan the templates for a convention pass, the two of `FieldExtraConventionTests` and the four of `FormTemplateConventionTests` (no template breaks one yet). The errors are:
- `django.template.exceptions.TemplateDoesNotExist: components/field.html` (and `layouts/form.html`, `components/form_errors.html`) in `FieldTests`, `FieldWithADjangoFormTests`, `FormErrorsTests` and `FormLayoutTests`;
- `FileNotFoundError: ... backend/static/css/components/field.css` in every `FormCssTests` test;
- `AssertionError: backend/pages/catalog.py: the field's Entry shows a blank state; give each state an id of its own` (and the help, error and code states) in `FormCatalogTests`;
- an error in `CatalogStatesTests` (the `Entry` has no `states` yet, and there is no `field` entry).

- [ ] **Step 3: The layout and the two components**

Create `backend/templates/layouts/form.html`:

```django
{% extends "shell.html" %}
{% load static %}
{# Layout: form. Sign in, sign up, password reset, email verification, the device flow and OAuth consent: one narrow column (spec 4.7, 6.2). A page fills the shell's title block with its name, then | Veilfile. #}
{% block preload_display %}<link rel="preload" href="{% static 'fonts/cormorant-garamond/italic-300-latin.woff2' %}" as="font" type="font/woff2" crossorigin>{% endblock %}
{% block body_class %}layout-form{% endblock %}
{% block main %}<div class="form-page">{% block form_page %}{% endblock %}</div>{% endblock %}
```

Create `backend/templates/components/field.html`:

```django
{# Component: field. One form field (spec 4.7): a mono label above a hairline input, a help line between them, the field's errors under it. #}
{# Inputs: id, the input's id and the label's for (the help's id is the id followed by _help, the error's the id followed by _error); name; label, the label's words; type, text (the default), email or password; value, kept after an error and never printed for a password; autocomplete, a token; required and autofocus, true or absent; help, a line or a short list above the input, plain text or trusted HTML such as a form field's help_text; errors, the field's errors (form.field.errors, or a list of sentences and never a bare string, which would be walked a letter at a time), none when empty or absent; extra, exact attribute text for the input, such as placeholder="XXXX-XXXX" spellcheck="false": a string literal in the page's template and never a variable, because a space in a variable starts a new attribute (a test holds every template to this); mono, true for a code. A page's CSRF token stays in the page's own form, never here. #}
<div class="field{% if errors %} field--error{% endif %}">
<label class="field__label label" for="{{ id }}">{{ label }}</label>
{% if help %}<div class="field__help" id="{{ id }}_help">{{ help }}</div>
{% endif %}<input class="field__input{% if mono %} field__input--mono{% endif %}" id="{{ id }}" name="{{ name }}" type="{{ type|default:'text' }}"{% if value and type != 'password' %} value="{{ value }}"{% endif %}{% if autocomplete %} autocomplete="{{ autocomplete }}"{% endif %}{% if required %} required{% endif %}{% if autofocus %} autofocus{% endif %}{% if errors %} aria-invalid="true"{% endif %}{% if help or errors %} aria-describedby="{% if help %}{{ id }}_help{% endif %}{% if help and errors %} {% endif %}{% if errors %}{{ id }}_error{% endif %}"{% endif %}{% if extra %} {{ extra }}{% endif %}>
{% if errors %}<div class="field__error" id="{{ id }}_error">
<span class="field__flag label" aria-hidden="true">Check this</span>
{% for error in errors %}<p>{{ error }}</p>
{% endfor %}</div>
{% endif %}</div>
```

Create `backend/templates/components/form_errors.html`:

```django
{# Component: form_errors. The sentences that belong to a whole form, not to one field (spec 4.7). #}
{# Inputs: errors, the form's non-field errors (form.non_field_errors) or a list of sentences, never a bare string, which would be walked a letter at a time; message, one fixed sentence of the page's own shown before them; nothing is drawn when both are empty or absent. #}
{% if errors or message %}<div class="form-errors" role="alert">
<span class="form-errors__flag label" aria-hidden="true">Check this</span>
{% if message %}<p>{{ message }}</p>
{% endif %}{% for error in errors %}<p>{{ error }}</p>
{% endfor %}</div>
{% endif %}
```

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms.FieldTests pages.tests_forms.FieldExtraConventionTests pages.tests_forms.FieldWithADjangoFormTests pages.tests_forms.FormErrorsTests
```

Expected: `Ran 23 tests` and `OK`. (`pages.tests_catalog`, `pages.tests_site_rules` and the stylesheet-order test of `FormLayoutTests` still fail: no stylesheet, no link and no catalog entry yet. The next two steps fix that.)

- [ ] **Step 4: The token, the two stylesheets, the Forms section and the links**

In `backend/static/css/tokens.css`, replace this exact text:

```css
  --chapter-measure: 40rem; /* a chapter's column */
```

with:

```css
  --chapter-measure: 40rem; /* a chapter's column */
  --form-measure: 26rem; /* a form page's column (layout-form, base.css): about fifty characters of Newsreader */
```

Create `backend/static/css/components/field.css`:

```css
/* A form field (spec 4.7): a mono label above a hairline input, the help line between them and the errors under it. The input's single
   line is 1px of --muted round a --box field. It is --muted and not --hair because a field's boundary is what identifies it and must
   meet WCAG 1.4.11 (3:1): --hair is 1.45:1 on Paper, --muted 6.6:1 (4.68:1 at the cloth's worst). Typed text is --ink, which the
   calmed cloth gives 8:1 or better; the veil of --box only moves the cloth behind it away from the text's colour (lighter in Paper,
   darker in Ink), so that contrast can rise and cannot fall. Text is 16px or more, or iOS zooms the page when the field takes focus.
   An error uses no colour: the line goes solid --ink, the sentence under the field is in full --ink and a small mono label above it
   reads Check this. Focus is the global outline of base.css; the line has no focus colour of its own. Nothing here sets a z-index or
   an opacity. */
.field {
  margin: 0 0 var(--space-4);
}

.field__label {
  display: block;
  margin: 0 0 var(--space-2);
}

/* The help sits above the input, not under it: the on-screen keyboard covers what is under a focused field on a phone, and a rule
   for a password is wanted while it is typed. It is plain text, or the short list a form field's help_text makes. */
.field__help {
  margin: 0 0 var(--space-2);
  font-size: .9em;
  color: var(--muted);
}

.field__help p,
.field__help ul,
.field__help ol {
  margin: 0;
}

.field__help ul,
.field__help ol {
  padding: 0 0 0 1.2em;
}

.field__help li {
  margin: 0;
}

.field__input {
  display: block;
  width: 100%;
  min-width: 0;
  margin: 0;
  padding: var(--space-2) var(--space-3);
  -webkit-appearance: none;
  appearance: none;
  border: 1px solid var(--muted);
  border-radius: 0;
  background: var(--box);
  color: var(--ink);
  caret-color: var(--ink);
  font-family: var(--font-reading);
  font-size: max(16px, var(--reading-size));
  font-optical-sizing: auto;
  font-variant-numeric: lining-nums;
  line-height: 1.6;
  scroll-margin-top: var(--space-5); /* a focused field stops with its label showing, below the phone's top fade (base.css) */
  transition: border-color var(--fill-fade) ease;
}

.field__input::placeholder {
  color: var(--muted);
  opacity: 1; /* Firefox dims a placeholder */
}

/* A code (the device page's): mono, tracked and capitals, as the labels are. */
.field__input--mono {
  font-family: var(--font-mono);
  font-size: max(16px, calc(var(--reading-size) * .9));
  letter-spacing: var(--label-tracking);
  text-transform: uppercase;
}

/* A server error: the line goes from --muted to solid --ink, and stays ink while the field has focus (the global outline of base.css is
   the focus ring). */
.field__input[aria-invalid="true"] {
  border-color: var(--ink);
}

/* What the browser finds wrong after the visitor has been in the field (a required field left empty, an address that is not one).
   :user-invalid, never :invalid, which matches a required empty field before anyone has touched it and would make every form load
   looking wrong. A rule of its own: a browser that does not know the selector drops the whole rule it is in. */
.field__input:user-invalid {
  border-color: var(--ink);
}

/* Browser autofill must not paint its yellow or blue over the cloth. Chromium and Safari set the field's background and text colour
   with !important, which no author rule beats, so the change is held off instead: a transition with a delay that never ends keeps the
   field at the background it had, and -webkit-text-fill-color keeps the text --ink. Firefox tints with a filter. One rule for each
   spelling, because a selector list is dropped whole when one of its selectors is unknown to a browser. */
.field__input:-webkit-autofill,
.field__input:-webkit-autofill:hover,
.field__input:-webkit-autofill:focus {
  -webkit-text-fill-color: var(--ink);
  caret-color: var(--ink);
  filter: none;
  transition: background-color 600000s 0s, color 600000s 0s;
}

.field__input:autofill,
.field__input:autofill:hover,
.field__input:autofill:focus {
  -webkit-text-fill-color: var(--ink);
  caret-color: var(--ink);
  filter: none;
  transition: background-color 600000s 0s, color 600000s 0s;
}

/* With Motion off every transition is off (base.css), and the hold above with them. There is no cloth then, only paper, so the field
   is painted paper over the browser's tint. */
[data-motion="off"] .field__input:-webkit-autofill {
  box-shadow: inset 0 0 0 100vmax var(--paper);
}

[data-motion="off"] .field__input:autofill {
  box-shadow: inset 0 0 0 100vmax var(--paper);
}

/* The errors under the field: a small mono label, then each sentence in full --ink (not --muted). */
.field__error {
  margin: var(--space-2) 0 0;
  color: var(--ink);
}

.field__flag {
  display: block;
  margin: 0 0 var(--space-1);
  color: var(--ink);
}

.field__error p {
  margin: 0 0 var(--space-1);
  color: var(--ink);
}

.field__error p:last-child {
  margin-bottom: 0;
}
```

Create `backend/static/css/components/form_errors.css`:

```css
/* The sentences that belong to a whole form (spec 4.7): a wrong password, a throttled sign in. A 2px line down the left, solid --ink
   (a notice has the same line in --hair), a small mono label reading Check this, then the sentences in full --ink. No colour and
   no box. The markup is role="alert". */
.form-errors {
  margin: 0 0 var(--space-4);
  padding: 0 0 0 var(--space-3);
  border-left: 2px solid var(--ink);
  color: var(--ink);
}

.form-errors__flag {
  display: block;
  margin: 0 0 var(--space-1);
  color: var(--ink);
}

.form-errors p {
  margin: 0 0 var(--space-1);
  color: var(--ink);
}

.form-errors p:last-child {
  margin-bottom: 0;
}
```

In `backend/static/css/base.css`, replace this exact text (the first line of the Docs comment; it occurs once):

```css
/* Docs (spec 4.6): the quickstart's shell switcher and its command boxes. They are raw HTML in
```

with the Forms section followed by that same line:

```css
/* Forms (spec 4.7): sign in, sign up, password reset, email verification, the device flow and OAuth consent, on layouts/form.html.
   One narrow column (--form-measure). These pages are in no layout family and carry no data-layout, so nothing on them changes in
   place and every link out of them is an ordinary load. From 64rem a short form is centred between the corners, as a chapter is;
   below that it starts at the top, so the on-screen keyboard does not move it about. The field and the form's errors are
   components (components/field.css, components/form_errors.css); the main action, the secondary links, a notice and the rows of
   checkboxes and radios a consent form needs are here. As in the reading column, the bare elements are styled where they stand,
   `.form-page :where(...)` weighing one class and no element, so a class of a component or of a piece below overrides them by
   source order. No colour is added: an error is drawn with weight and a mark, in --ink. */
.layout-form .shell-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
  align-content: start;
}

@media (min-width: 64rem) {
  .layout-form .shell-main {
    align-content: center;
  }
}

/* A long email, user agent or app name wraps, anywhere, so the page never scrolls sideways at 320; min-width: 0 lets the column be
   narrower than its widest content. Newsreader has no face heavier than 500 (strong, below), so nothing is drawn in a synthetic bold. */
.form-page {
  width: 100%;
  min-width: 0;
  max-width: var(--form-measure);
  font-variant-numeric: lining-nums;
  font-synthesis-weight: none;
  overflow-wrap: anywhere;
}

/* The title is a chapter's title (.chapter__title): the display face, italic 300. */
.form-page :where(h1) {
  margin: 0 0 var(--space-4);
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 300;
  font-size: var(--title-size);
  line-height: 1.15;
  text-wrap: balance;
}

/* A smaller heading, a term in a list of facts and a fieldset's legend are labels: mono, uppercase, muted. */
.form-page :where(h2, h3, dt, legend) {
  font-family: var(--font-mono);
  font-size: var(--label-size);
  font-weight: 400;
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-transform: uppercase;
  color: var(--muted);
}

.form-page :where(h2, h3) {
  margin: var(--space-4) 0 var(--space-2);
}

.form-page :where(dt) {
  margin: var(--space-2) 0 0;
}

.form-page :where(dd) {
  margin: 0;
}

/* Not the visually hidden ones: their margin is what clips them. */
.form-page :where(p:not(.visually-hidden), ul, ol, dl) {
  margin: 0 0 var(--space-3);
}

.form-page :where(ul, ol) {
  padding: 0 0 0 1.8em; /* room for the markers, which are the browser's */
}

.form-page :where(li) {
  margin: 0 0 var(--space-1);
}

.form-page :where(strong, b) {
  font-weight: 500; /* the heaviest face Newsreader has */
}

.form-page :where(code) {
  font-family: var(--font-mono);
  font-size: .8em;
}

/* min-width: 0, or a fieldset (whose default is its widest content) holds the page wider than a phone. */
.form-page :where(fieldset) {
  min-width: 0;
  margin: 0 0 var(--space-4);
  padding: 0;
  border: 0;
}

.form-page :where(legend) {
  padding: 0 0 var(--space-2);
}

/* Inline links in a form page's words (a notice, a line under the title): the dotted underline that gives way to the flat fill, as in a
   chapter. veil.js washes the chapters' and the reading column's prose links and not these, so the fill answers on its own. A
   labelled link (.label) is not one: it is a secondary action, below. */
.form-page :is(p, li, dd, .notice) a:not(.interactive, .label) {
  text-decoration: underline dotted var(--muted);
  text-decoration-thickness: 1px;
  text-underline-offset: .22em;
  transition: background-color var(--fill-fade) ease, text-decoration-color var(--fill-fade) ease;
}

.form-page :is(p, li, dd, .notice) a:not(.interactive, .label):focus-visible {
  background-color: var(--fill);
  text-decoration-color: transparent;
}

@media not all and (pointer: coarse) {
  .form-page :is(p, li, dd, .notice) a:not(.interactive, .label):hover {
    background-color: var(--fill);
    text-decoration-color: transparent;
  }
}

@media (pointer: coarse) {
  .form-page :is(p, li, dd, .notice) a:not(.interactive, .label):active {
    background-color: var(--fill);
    text-decoration-color: transparent;
  }
}

/* The main action (the owner's choice): a mono label in a 1px hairline box on --box, one to a form. Its box stays --hair: a button is
   identified by its text label, where a field (whose line is --muted) has none. It is .interactive, so it answers the cursor as every
   control does: the cloth's ink wash where the cloth runs, the flat fill where it does not and on a press.
   .interactive's fill is inset a little for a bare label; here it fills the box. A button, or a link. The second selector keeps the
   box if a page also gives the element .label, whose padding and negative margin (.label.interactive) and button.label's bare
   background would otherwise win. */
.action,
.action.interactive {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  min-height: 3rem;
  margin: var(--space-4) 0 0;
  padding: .8em 1.2em;
  border: 1px solid var(--hair);
  border-radius: 0;
  -webkit-appearance: none;
  appearance: none;
  background: var(--box);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--label-size);
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-align: center;
  text-decoration: none;
  text-transform: uppercase;
  cursor: pointer;
}

.action::before {
  inset: 0;
}

/* Two actions of equal weight (Approve and Deny, Authorize and Deny): side by side where there is room, one under the other where there
   is not, all of one width. An item is an .action, or a <form> that holds one, which then fills it. */
.form-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin: var(--space-4) 0 0;
}

.form-actions > * {
  display: flex;
  flex: 1 1 9rem;
  min-width: 0;
  margin: 0;
}

.form-actions > .action,
.form-actions > * > .action {
  margin: 0;
}

.form-actions > * > .action {
  flex: 1 1 auto;
}

/* Secondary actions: quieter than the main one, mono labels in --muted with no box. A link (a.label.interactive) or a button of a form
   of its own (button.label.interactive); words between them ("No account?") are the same label in the same colour. */
.form-links {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-3) var(--space-4);
  margin: var(--space-5) 0 0;
  font-family: var(--font-mono);
  font-size: var(--label-size);
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-transform: uppercase;
  color: var(--muted);
}

/* A quiet line of status or news: a --muted sentence with the reading column's 2px hairline down its left. role="status" on the ones that
   are announced (a link was sent, an address is verified). */
.notice {
  margin: 0 0 var(--space-4);
  padding: 0 0 0 var(--space-3);
  border-left: 2px solid var(--hair);
  color: var(--muted);
}

.notice strong {
  color: var(--ink);
}

.notice > :last-child {
  margin-bottom: 0;
}

/* A checkbox or a radio with its words, one row (a consent form's confirmation, the choice of a key to replace). The whole row is the
   label, so the words are part of the target. The box is drawn here in --muted, as a field's line is: a boundary that must be seen.
   The mark inside is a square, or for a radio a dot, in --ink: a shape and not a colour, and not a check mark, which no font of the
   site has. */
.check {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0 0 var(--space-3);
  cursor: pointer;
}

.check__input {
  flex: none;
  width: 1.25rem;
  height: 1.25rem;
  margin: .2em 0 0;
  -webkit-appearance: none;
  appearance: none;
  border: 1px solid var(--muted);
  border-radius: 0;
  background-color: var(--box);
  background-repeat: no-repeat;
  background-position: center;
  cursor: inherit;
}

.check__input[type="radio"] {
  border-radius: 50%;
}

.check__input:checked {
  background-image: linear-gradient(var(--ink), var(--ink));
  background-size: 55% 55%;
}

.check__input[type="radio"]:checked {
  background-image: radial-gradient(circle closest-side, var(--ink) 0, var(--ink) 50%, transparent 56%);
  background-size: 100% 100%;
}

/* Left unticked after the visitor has tried: the same solid line as a field's. A rule of its own, as the field's is. */
.check__input:user-invalid {
  border-color: var(--ink);
}

@media (forced-colors: active) {
  .check__input {
    -webkit-appearance: auto;
    appearance: auto;
  }
}

.check__text {
  min-width: 0;
}

.check__detail {
  display: block;
  font-size: .9em;
  color: var(--muted);
}

/* Docs (spec 4.6): the quickstart's shell switcher and its command boxes. They are raw HTML in
```

In `backend/templates/shell.html`, replace this exact text:

```django
<link rel="stylesheet" href="{% static 'css/components/entries.css' %}">
```

with:

```django
<link rel="stylesheet" href="{% static 'css/components/entries.css' %}">
<link rel="stylesheet" href="{% static 'css/components/field.css' %}">
<link rel="stylesheet" href="{% static 'css/components/form_errors.css' %}">
```

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms.FormCssTests pages.tests_forms.FormLayoutTests pages.tests_tokens pages.tests_reading pages.tests_shell
```

Expected: `OK`. (`FormCssTests` and `FormLayoutTests` pass; the tokens, reading-column and shell tests that pin `base.css` and `tokens.css` still pass. `pages.tests_catalog` and `pages.tests_site_rules` still fail on the missing catalog entries until the next step.)

- [ ] **Step 5: The catalog shows a component's states**

In `backend/pages/catalog.py`, make three edits.

Replace this exact text:

```python
from types import SimpleNamespace

from blog.render import Section
```

with:

```python
from types import SimpleNamespace

from django.utils.safestring import mark_safe

from blog.render import Section
```

Replace this exact text:

```python
    within: str = ""  # the class of the wrapper the page puts it in, for a component that only sits in one ("reading")
```

with:

```python
    within: str = ""  # the class of the wrapper the page puts it in, for a component that only sits in one ("reading", "form-page")
    states: tuple = ()  # more samples of the same component, each a context of its own (a field blank, with help, with an error): the frame draws them after `context`'s, one under the other, and their ids must differ
```

Replace this exact text (the end of `entries()`):

```python
                                  description="Give an agent a Veilfile key: env var setup, curl upload examples, MCP connection, key rotation."),
              )},
              within="reading"),
    )
```

with:

```python
                                  description="Give an agent a Veilfile key: env var setup, curl upload examples, MCP connection, key rotation."),
              )},
              within="reading"),
        Entry("field", "One form field on every form page: a mono label above a hairline input, the help line above the input and the errors under it. "
                       "Shown blank, with a help line, with an error (the line goes solid ink, a small mono label reads Check this and the sentence is in full ink) and as a code.",
              {"id": "catalog-field-blank", "name": "username", "label": "Username or email", "type": "text",
               "autocomplete": "username", "required": True},
              within="form-page",
              states=(
                  {"id": "catalog-field-help", "name": "email", "label": "Email", "type": "email", "autocomplete": "email", "required": True,
                   "help": "We verify this address before issuing API keys. It is how we reach you about your account."},
                  {"id": "catalog-field-error", "name": "email", "label": "Email", "type": "email", "value": "ana@example.com", "autocomplete": "email",
                   "required": True, "errors": ["An account with this email address already exists."]},
                  {"id": "catalog-field-code", "name": "user_code", "label": "Device code", "autocomplete": "off", "required": True, "mono": True,
                   "extra": mark_safe('placeholder="XXXX-XXXX" spellcheck="false"')},
              )),
        Entry("form_errors", "The top of a form that has something to say as a whole: a summary with a solid ink line down its left, the mark Check this and each sentence in full ink. "
                             "Shown with one sentence of the page's own, and with the form's errors.",
              {"message": "Invalid username, email, or password. Try again."},
              within="form-page",
              states=({"errors": ["Your current password is incorrect."]},)),
    )
```

In `backend/pages/views.py`, make three edits.

Replace this exact text:

```python
from django.utils.html import format_html
```

with:

```python
from django.utils.html import format_html
from django.utils.safestring import mark_safe
```

Replace this exact text:

```python


def component_catalog(request):
```

with:

```python


def _numbered(context, index):
    """A sample's context for frame ``index``: the keys that hold an id get the frame's number, so ids stay unique across the four frames."""
    context = dict(context)
    for key in ("list_id", "id_prefix", "id"):
        if key in context:
            context[key] = f"{context[key]}-{index}"
    return context


def component_catalog(request):
```

Replace this exact text:

```python
            context = dict(entry.context)
            for key in ("list_id", "id_prefix", "id"):  # ids must stay unique across the four frames
                if key in context:
                    context[key] = f"{context[key]}-{index}"
            html = render_to_string(f"components/{entry.name}.html", context)
```

with:

```python
            # The entry's context, then each of its states, one under the other (their ids differ), each numbered for the frame.
            html = mark_safe("".join(render_to_string(f"components/{entry.name}.html", _numbered(sample, index))
                                     for sample in (entry.context, *entry.states)))
```

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms pages.tests_catalog pages.tests_site_rules
```

Expected: `Ran 80 tests` and `OK`.

- [ ] **Step 6: Hold the new module to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a test class whose `longMessage` is not `False`, and its message names the file and the fix. A failure that compares a page would otherwise print the page, up to 160 KB, before the message. `pages.tests_forms` renders whole pages, so list it.

In `backend/pages/tests_site_rules.py`, replace this exact text:

```python
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed")
```

with:

```python
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed", "pages.tests_forms")
```

(Each later task that adds a test module of whole pages appends it to this tuple, quoting it as this step leaves it.)

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules
```

Expected: `OK`.

- [ ] **Step 7: Keep AGENTS.md true**

In `AGENTS.md`, replace this exact text (the repo map):

```
backend/templates/     Django templates: shell.html, layouts/ (chapter, reading),
```

with:

```
backend/templates/     Django templates: shell.html, layouts/ (chapter, reading, form),
```

and replace this exact text (step 4 of "Change or add a component"):

```
   `within="reading"`: the catalog then shows it inside `<div class="reading">`,
   as the page does.
```

with:

```
   `within="reading"`: the catalog then shows it inside `<div class="reading">`,
   as the page does. A component with states worth seeing side by side (a field
   blank, with help, with an error) lists the others in `states`, each a context
   of its own with ids that differ from the rest; the frame draws them one under
   the other.
```

and replace this exact text (the note on readable failures, under "Docs and tests hygiene"):

```
  `pages.tests_site_rules` (`ReadableFailuresTests`) holds the reading pages'
  test modules to this.
```

with:

```
  `pages.tests_site_rules` (`ReadableFailuresTests`) holds the reading pages'
  and the form pages' test modules to this: list a new one in
  `READING_TEST_MODULES` there.
```

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts pages.tests_content pages.tests_shell
```

Expected: `OK`.

- [ ] **Step 8: Look at it (nothing in this task is checked in a browser; Task 7 adds the checks)**

From the repo root, with the worktree prepared as in Global Constraints (migrated database, staff session made), start the server and open the staff-only catalog:

```bash
lsof -ti tcp:8777
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload
```

The first command must print nothing. Then open `http://127.0.0.1:8777/app/components/` signed in as the staff user (the `sessionid` from `site-tests/session.txt`) and scroll to `field` and `form_errors`.

Expected, in Paper and in Ink, Motion on and off:
- **Field:**
  - four fields in a narrow column: blank; with a help line above the input; with a solid ink line, the label CHECK THIS and the sentence in full ink under it; and the code with its placeholder in mono capitals;
  - each field has a clear 1 px line in `--muted` (the main action's box, further down a page, is the faint `--hair` one), the typed email reads clearly, and a click in a field adds the global 1 px outline, 3 px outside the line, which itself does not change.
- **Form errors:** both states have a solid 2 px ink line down the left, with CHECK THIS above the sentence.
- Nothing scrolls sideways at a 320 px window.

Stop the server afterwards.

- [ ] **Step 9: Run the whole suite**

Run:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test
```

Expected: `OK` (about 3.5 to 5 minutes). Then:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

- [ ] **Step 10: Commit**

```bash
git add backend/templates/layouts/form.html backend/templates/components/field.html backend/templates/components/form_errors.html backend/static/css/components/field.css backend/static/css/components/form_errors.css backend/static/css/tokens.css backend/static/css/base.css backend/templates/shell.html backend/pages/catalog.py backend/pages/views.py backend/pages/tests_forms.py backend/pages/tests_site_rules.py AGENTS.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Add the form layout and its pieces: the field and form_errors components, the main action, the secondary links, notices and checkbox rows, the conventions the form pages share, and the component catalog's states"
```

Expected: a commit listing 13 files changed (6 new, 7 modified).

---

### Task 2: Sign in, sign up, the signed-in visit, the throttled page and checkout confirm

Four pages leave the old includes and move onto the shell and `layouts/form.html` (Task 1): `registration/login.html`, `signup.html`, `throttled.html` and `checkout_confirm.html`. Every page keeps its view, address, field names, hidden fields, status codes and the phrases the tests pin. Three behaviours are added, each with a test:

- A signed-in visit to sign in or sign up goes on to the safe `?next=`, else the dashboard.
- Sign up, like sign in, returns to a safe `?next=`. The device page and OAuth consent round trips are tested through both.
- The throttled page is proved to say nothing of the account asked about, for signed-out and signed-in visitors alike.

The unused `logged_out.html` is removed. The old-markup tests that pinned the two moved form pages are pointed at the one old page that remains (`/accounts/password_reset/`) for as long as it is old: Task 3 moves that page and retires them.

**Files:**
- Create: `backend/drop_core/tests_sign_in_pages.py`
- Modify: `backend/drop_core/views.py`, `backend/config/urls.py`
- Modify (templates): `backend/templates/registration/login.html`, `signup.html`, `throttled.html`, `checkout_confirm.html`
- Delete: `backend/templates/registration/logged_out.html`
- Modify (tests): `backend/drop_core/tests.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/pages/tests_site_rules.py`
- Modify (doc): `AGENTS.md`
- Never touch: `backend/legal/documents/*`

**Interfaces:**
- Consumes (Task 1):
  - `layouts/form.html`: extends `shell.html`; body class `layout-form`; a `{% block form_page %}` inside `<div class="form-page">`. The shell's `{% block title %}` and `{% block meta %}` are open to the page.
  - `components/field.html` with inputs `id`, `name`, `label`, `type`, `value`, `autocomplete`, `required`, `autofocus`, `help` (drawn above the input), `errors` (a form field's `ErrorList` or a list, never a bare string), `extra` (a string literal only) and `mono`. This task passes `extra` once per username field, as the literal `'autocapitalize="none" spellcheck="false"'`, and never `autofocus` or `mono`.
  - `components/form_errors.html` with inputs `errors` (a list) and `message` (one fixed sentence), a `role="alert"` summary that draws nothing when both are empty.
  - The main action, `<button class="action interactive" type="submit">` (only `.interactive` gives it the ink wash on hover and on a press), and the secondary links, `<p class="form-links">` holding `<a class="label interactive">`.
- Markup of Task 1 that the tests here rely on:
  - A `<label for>` above each input.
  - `aria-invalid="true"` on an input with `errors`.
  - `aria-describedby` on an input listing the id of the help line and then the id of the error sentence, which must exist on the page.
  - The error sentence in an element whose id is `<input id>_error`, the help line in `<input id>_help`. The older test `test_signup_errors_linked_to_fields` pins `id_password2_error`.
  - `errors` is any iterable of sentences: Django's `ErrorList` and a list both work, and an empty one is falsy.
  - The help line sits in a block element, because the password help is the validators' `<ul>`.
- Consumes (existing):
  - `drop_core.throttle.throttled_response(request, title, message, retry_after_seconds)` renders `registration/throttled.html` with `title`, `message`, `retry_minutes`.
  - `drop_core.throttle.throttle_login` wraps the sign-in view and the admin login.
  - The `shell_menu` tag shows Dashboard to a signed-in visitor and marks Sign in current on `/accounts/login/`.
- Produces:
  - `drop_core.views.safe_next_url(request)` and `signed_in_destination(request)`.
  - `drop_core.views.SignInView`, a `LoginView` mounted by `config/urls.py` under `throttle_login`. It adds the signed-in redirect to `get` and nothing else. Its context's `next` is Django's own: the safe next, or `""`.
  - The sign-up context gains `next`.
  - The titles are `Log in | Veilfile`, `Sign up | Veilfile`, `Checkout | Veilfile` and `{{ title }} | Veilfile`; each page has one `h1`.
  - `registration/throttled.html` reads `title`, `message` and `retry_minutes` and nothing else. Task 6 feeds it the sign-up and verification 429s through `throttle.throttled_response`, and may add context values that are constant for each kind of throttle (how the wait is worded, whether the sign-in links show), never anything of the visitor: the source test in `ThrottledPageTests` bars only the words `user`, `request` and `csrf`.
  - `drop_core/tests_sign_in_pages.py`, whose `Page` parser (inputs, forms, buttons, links, `h1`s, alerts, labels, the text of every element with an id) later tasks may import.
  - Until Task 3 moves it and retires them, `/accounts/password_reset/` is the stand-in for "an old page" in `tests_menu.py`, `tests_ambient.py` and `AccessibilityTests`.

Background for an engineer new to the repo, and the decisions this task makes:

- **The signed-in visit answers a GET (and HEAD) only, and does not use `LoginView.redirect_authenticated_user`.**
  - With that option Django raises `ValueError` ("Redirection loop for authenticated user detected") for a signed-in visit to `/accounts/login/?next=/accounts/login/`, a 500 from a link anyone can send. It also redirects a POST. `throttle_login` reads a redirect as a successful sign-in and clears the failure count of the account named in the POST, so a signed-in visitor could clear any account's count by naming it.
  - Instead `SignInView.get` and `signup` (after the paid-plan branch, so a picked paid plan still reaches its checkout step) redirect to `signed_in_destination(request)`: the safe `?next=`, else `/app/`. A next that names the page itself counts as none.
  - A signed-in POST keeps working as it always did. It signs in as the account named, or makes the account, and counts toward the throttles. The sign-up throttle tests post twelve times from one client that the first sign-up signed in, so they need no change.
- **`?next=` makes the whole trip.**
  - The sign-in page's Sign up link and the sign-up page's Log in link carry the safe next, through `|urlencode`. Both forms post to their own address, so the query rides along as before.
  - A free-plan sign-up lands on the safe next instead of `/app/`. A paid plan still goes to its checkout. Only an address on this site is followed (`url_has_allowed_host_and_scheme`, the test `LoginView` applies).
- **The throttled page.**
  - It is shown to signed-in visitors on three routes: the email-change form, the password-change form, and a sign-in posted while signed in. The template reads only `title`, `message` and `retry_minutes`. A test proves that the only thing that differs between a signed-in and a signed-out visitor is the shell's menu (Dashboard for Sign in), and that nothing of the account asked about, the visitor, or a CSRF token is on the page.
  - `LoginThrottleTests` already holds two responses byte for byte. This task adds the same for a signed-in visitor, the headers, and the `/admin/login/` route.
- **`logged_out.html`.**
  - Nothing links to it and no test renders it. Django's `LogoutView` draws it only when the address to go to is the logout address itself, which `LOGOUT_REDIRECT_URL = "/accounts/login/"` rules out for every form on the site. The one request that ever rendered it is a hand-made POST to `/accounts/logout/` whose body names `/accounts/logout/` as its `next`.
  - The template goes. That request is answered by Django's own admin "Logged out" page (a test pins that it is a page, not an error). Both logout routes redirect, and a test pins that.
- **Smaller decisions.**
  - The pages gain an `h1` ("Log in", "Sign up"); they had none.
  - Titles end ` | Veilfile`, as every other page of the new site does and as the voice rules (no em dashes) ask. They had an em dash before `Veilfile`. No existing test pins a title, and the new tests pin the new ones. (The plan's "current titles kept" is read as the words of each title.)
  - The two username fields say `autocapitalize="none" spellcheck="false"` (`extra`, a string literal, as Task 1 asks): a phone would otherwise capitalise the first letter of a name that is case-sensitive.
  - `autofocus` is dropped from the sign-in and sign-up username fields. These pages have several fields and a heading to read first, and the plan's rule is that only a page that exists to take one input autofocuses it.
  - The sign-in failure sentence still collapses every failure to one line (`form_errors` with `message`, shown when `form.errors` is not empty), so the page never says which of the name or the password was wrong.
- **Prices.** No price is typed in these templates (AGENTS.md "Pricing changes"). The sign-up sentence stays in `views.py` (`_plan_blurb`) and the checkout page's price comes from `plans.py` through the view. A test holds all four templates to it.
- **Test speed.** The new tests use a fast password hasher and put the failure counts on record directly (`throttle.record_login_failure`) instead of making twenty slow requests. The module runs in about half a second.

- [ ] **Step 1: Write the failing tests**

Create `backend/drop_core/tests_sign_in_pages.py`:

```python
"""Sign in, sign up, throttled and checkout confirm on the form layout (spec 4.7, 4.9).

The four pages keep their addresses, field names, hidden fields, status codes and the phrases the CLIs and
older tests match; they move onto the shell and layouts/form.html. A signed-in visit to sign in or sign up
goes on to the safe ?next= or the dashboard, and ?next= makes the whole trip through sign in and sign up.

Every class sets longMessage = False, so that a failure prints its own message, which names the file and
the fix, and not the page it searched.
"""
import json
import logging
import os
import re
from base64 import urlsafe_b64encode
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from drop_core import throttle
from drop_core.models import EmailVerification, get_or_create_workspace
from drop_core.oauth import OAUTH_SCOPE_MCP_USE
from drop_core.plans import PLANS

User = get_user_model()
PASSWORD = "pw12345678"
LOGIN = "/accounts/login/"
SIGNUP = "/accounts/signup/"
DEVICE_LINK = "/device?user_code=ABCD-EFGH"
TEMPLATES = Path(settings.BASE_DIR) / "templates"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
FAILED = "Invalid username, email, or password. Try again."


class Page(HTMLParser):
    """What these tests read in a page: its inputs, forms, buttons, links, headings, alerts, the label of each
    input and the text of every element that has an id."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.inputs, self.forms, self.buttons, self.links = [], [], [], []
        self.headings, self.alerts, self.labels, self.texts = [], [], {}, {}
        self._open = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs.append(attrs)
        if tag == "form":
            self.forms.append(attrs)
        if tag not in VOID:
            self._open.append([tag, attrs, []])

    def handle_data(self, data):
        for element in self._open:
            element[2].append(data)

    def handle_endtag(self, tag):
        names = [element[0] for element in self._open]
        if tag not in names:
            return
        depth = len(names) - 1 - names[::-1].index(tag)
        while len(self._open) > depth:
            name, attrs, parts = self._open.pop()
            text = " ".join("".join(parts).split())
            if "id" in attrs:
                self.texts[attrs["id"]] = text
            if attrs.get("role") == "alert":
                self.alerts.append(text)
            if name == "label" and "for" in attrs:
                self.labels[attrs["for"]] = text
            if name == "button":
                self.buttons.append((attrs, text))
            if name == "a":
                self.links.append((attrs, text))
            if name == "h1":
                self.headings.append(text)

    def input(self, id):
        return next((attrs for attrs in self.inputs if attrs.get("id") == id), None)

    def href(self, text):
        return next((attrs.get("href") for attrs, found in self.links if found == text), None)

    def described(self, attrs):
        """The text of the elements an input's aria-describedby names, and the ids of them that the page lacks."""
        tokens = attrs.get("aria-describedby", "").split()
        return " ".join(self.texts.get(token, "") for token in tokens), [t for t in tokens if t not in self.texts]


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def next_of(href):
    """The address a link carries in its ?next=, decoded, or None."""
    return (parse_qs(urlsplit(href or "").query).get("next") or [None])[0]


def pkce_challenge():
    return urlsafe_b64encode(sha256(b"a verifier for the tests").digest()).rstrip(b"=").decode("ascii")


# These tests make a few dozen accounts and sign-ins; the fast hasher keeps them to seconds. What they check is the
# pages, not the hash.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PageCase(TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        throttle_log = logging.getLogger("drop_core.throttle")
        self.addCleanup(throttle_log.setLevel, throttle_log.level)
        throttle_log.setLevel(logging.ERROR)

    def parse(self, response):
        return Page(response.content.decode())

    def assertSentTo(self, response, expected, message):
        """A 302 to exactly `expected`. A page that answered instead says what it answered, not a KeyError."""
        self.assertEqual((response.status_code, response.get("Location")), (302, expected), message)

    def field(self, page, id):
        found = page.input(id)
        if found is None:
            self.fail(f"the page has no input with id {id}: backend/templates/registration keeps every field id "
                      "(the labels, error ids and older tests find them)")
        return found

    def lock_out(self, *names):
        """Ten failures on record for each name, from the test client's address: the sign-in lock engages for each."""
        for name in names:
            for _ in range(10):
                throttle.record_login_failure("127.0.0.1", name)


class FormLayoutTests(PageCase):
    """The four pages are drawn by layouts/form.html on the shell, with the old includes gone."""
    longMessage = False

    def pages(self):
        User.objects.create_user("signedin", email="signedin@example.com", password=PASSWORD)
        member = Client()
        member.force_login(User.objects.get(username="signedin"))
        User.objects.create_user("victim", password=PASSWORD)
        self.lock_out("victim")
        return {
            "registration/login.html": self.client.get(LOGIN),
            "registration/signup.html": self.client.get(SIGNUP),
            "registration/checkout_confirm.html": member.get(SIGNUP + "?plan=team"),
            "registration/throttled.html": Client().post(LOGIN, {"username": "victim", "password": "nope"}),
        }

    def test_each_page_extends_the_form_layout_and_includes_nothing_old(self):
        for name, response in self.pages().items():
            with self.subTest(template=name):
                used = [template.name for template in response.templates]
                self.assertIn(name, used, f"{name} was not the template drawn for this page")
                self.assertIn("layouts/form.html", used, f"backend/templates/{name} must extend layouts/form.html")
                self.assertEqual([found for found in used if found.startswith("includes/")], [],
                                 f"backend/templates/{name}: the old includes (theme_boot, ambient, menu) are for pages "
                                 "not yet on the shell; this page draws the shell's own corners, menu and switches")
                html = response.content.decode()
                self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                                 "<body> needs the class layout-form (backend/templates/layouts/form.html)")
                self.assertNotIn("data-layout", html,
                                 f"backend/templates/{name}: a form page changes nothing in place, so its <body> has no "
                                 "data-layout (a form page is not in the registry)")

    def test_each_page_has_the_shells_landmarks_and_one_h1_inside_main(self):
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = response.content.decode()
                self.assertIn('<a class="skip-link label" href="#main">', html, f"{name} lost the skip link")
                self.assertIn('<main id="main" class="shell-main">', html, f"{name} lost <main id=\"main\">")
                page = Page(html)
                self.assertEqual(len(page.headings), 1, f"backend/templates/{name} needs exactly one <h1>")
                self.assertEqual(len(re.findall(r"<h1[ >]", main_of(html))), 1, f"{name}: the <h1> belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.assertNotIn(element, main_of(html), f"{name}: no {element}> inside <main>; use a div")

    def test_each_page_names_itself_in_its_title(self):
        titles = {
            "registration/login.html": "Log in | Veilfile",
            "registration/signup.html": "Sign up | Veilfile",
            "registration/checkout_confirm.html": "Checkout | Veilfile",
            "registration/throttled.html": "Too many sign-in attempts | Veilfile",
        }
        for name, response in self.pages().items():
            with self.subTest(template=name):
                self.assertIn(f"<title>{titles[name]}</title>", response.content.decode(),
                              f"backend/templates/{name}: {{% block title %}} names the page, then | Veilfile")

    def test_every_input_has_a_visible_label(self):
        for name, response in self.pages().items():
            page = self.parse(response)
            for attrs in page.inputs:
                if attrs.get("type") in ("hidden", "submit"):
                    continue
                with self.subTest(template=name, input=attrs.get("id")):
                    self.assertTrue(page.labels.get(attrs.get("id")),
                                    f"backend/templates/{name}: input {attrs.get('name')} needs a visible <label for=\"{attrs.get('id')}\"> "
                                    "(components/field.html draws one)")

    def test_no_page_leaves_a_template_comment_in_its_text(self):
        # Django 6.1 strips a one-line {# #} comment and prints a multi-line one as text.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = response.content.decode()
                self.assertNotIn("{#", html, f"backend/templates/{name}: keep each {{# #}} comment on one line")
                self.assertNotIn("#}", html, f"backend/templates/{name}: keep each {{# #}} comment on one line")

    def test_no_page_takes_focus_for_the_visitor(self):
        # A page of several fields reads its heading first; only a page that exists to take one input autofocuses it.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                for attrs in self.parse(response).inputs:
                    self.assertNotIn("autofocus", attrs,
                                     f"backend/templates/{name}: drop autofocus from {attrs.get('name')}; this page has "
                                     "several fields and a heading to read first")

    def test_every_button_and_link_in_the_page_answers_the_cursor(self):
        # Only .interactive gives a control the ink wash on hover and on a press (base.css), so the main action is
        # <button class="action interactive"> and a secondary link is <a class="label interactive"> in a p.form-links.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = main_of(response.content.decode())
                page = Page(html)
                self.assertLessEqual(len(page.buttons), 1, f"backend/templates/{name}: one main action to a page")
                for attrs, text in page.buttons:
                    self.assertTrue({"action", "interactive"} <= set(attrs.get("class", "").split()) and attrs.get("type") == "submit",
                                    f"backend/templates/{name}: the main action {text!r} is <button class=\"action interactive\" "
                                    f"type=\"submit\">; this one has class {attrs.get('class')!r}")
                for attrs, text in page.links:
                    self.assertTrue({"label", "interactive"} <= set(attrs.get("class", "").split()),
                                    f"backend/templates/{name}: the link {text!r} is <a class=\"label interactive\">; this one has "
                                    f"class {attrs.get('class')!r}")
                self.assertEqual(html.count('<p class="form-links">'), 1,
                                 f"backend/templates/{name}: the secondary links sit in one <p class=\"form-links\">")

    def test_no_price_is_typed_in_these_templates(self):
        for name in ("login", "signup", "throttled", "checkout_confirm"):
            with self.subTest(template=name):
                text = (TEMPLATES / "registration" / f"{name}.html").read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"\$\s?\d", text),
                                  f"backend/templates/registration/{name}.html types a price: a plan's price is written in "
                                  "backend/drop_core/plans.py and reaches the page through the view (AGENTS.md, Pricing changes)")


class SignInPageTests(PageCase):
    longMessage = False

    def test_the_form_posts_to_its_own_address_with_a_token(self):
        page = self.parse(self.client.get(LOGIN))
        self.assertEqual(len(page.forms), 1, "backend/templates/registration/login.html holds one form")
        self.assertEqual(page.forms[0].get("method", "").lower(), "post")
        self.assertNotIn("action", page.forms[0],
                         "backend/templates/registration/login.html: a form with an action would drop ?next= on the way "
                         "to the server; post to the page's own address")
        self.assertTrue(any(attrs.get("name") == "csrfmiddlewaretoken" for attrs in page.inputs),
                        "backend/templates/registration/login.html needs {% csrf_token %} inside the form")

    def test_the_fields_keep_their_names_labels_and_tokens(self):
        page = self.parse(self.client.get(LOGIN))
        username, password = self.field(page, "id_username"), self.field(page, "id_password")
        self.assertEqual((username.get("name"), username.get("type"), username.get("autocomplete")),
                         ("username", "text", "username"))
        self.assertEqual((password.get("name"), password.get("type"), password.get("autocomplete")),
                         ("password", "password", "current-password"))
        self.assertIn("required", username)
        self.assertIn("required", password)
        self.assertEqual(page.labels["id_username"], "Username or email")
        self.assertEqual(page.labels["id_password"], "Password")
        self.assertEqual((username.get("autocapitalize"), username.get("spellcheck")), ("none", "false"),
                         "backend/templates/registration/login.html: the name field passes extra='autocapitalize=\"none\" "
                         "spellcheck=\"false\"', or a phone capitalises the first letter of a name that is case-sensitive")

    def test_a_first_visit_announces_nothing(self):
        self.assertEqual(self.parse(self.client.get(LOGIN)).alerts, [],
                         "backend/templates/registration/login.html: the failure sentence is drawn only {% if form.errors %}")

    def test_a_failed_sign_in_is_one_alert_with_one_sentence_whatever_was_wrong(self):
        User.objects.create_user("real", password=PASSWORD)
        inactive = User.objects.create_user("gone", password=PASSWORD)
        inactive.is_active = False
        inactive.save()
        for name, password in (("real", "wrong"), ("nobody", "wrong"), ("gone", PASSWORD), ("", "")):
            with self.subTest(username=name):
                response = self.client.post(LOGIN, {"username": name, "password": password})
                self.assertEqual(response.status_code, 200)
                alerts = self.parse(response).alerts
                self.assertEqual(len(alerts), 1,
                                 "backend/templates/registration/login.html: a failed sign-in is one role=alert summary "
                                 "(components/form_errors.html)")
                self.assertIn(FAILED, alerts[0], "backend/templates/registration/login.html: the one sentence is the message of "
                              "form_errors, whatever failed")

    def test_a_failed_sign_in_keeps_the_name_and_never_the_password(self):
        page = self.parse(self.client.post(LOGIN, {"username": "someone", "password": "secret-guess"}))
        self.assertEqual(self.field(page, "id_username").get("value"), "someone")
        self.assertNotIn("value", self.field(page, "id_password"))
        self.assertNotIn("secret-guess", page.html, "a password is never put back in the page")

    def test_the_sign_up_link_carries_a_safe_next_and_only_a_safe_one(self):
        for query, expected in (
            ("", None),
            ("?next=" + DEVICE_LINK.replace("?", "%3F").replace("=", "%3D"), DEVICE_LINK),
            ("?next=https://evil.example/", None),
            ("?next=//evil.example/", None),
            ("?next=javascript:alert(1)", None),
        ):
            with self.subTest(query=query):
                page = self.parse(self.client.get(LOGIN + query))
                href = page.href("Sign up")
                self.assertEqual(next_of(href), expected,
                                 "backend/templates/registration/login.html: the Sign up link carries the safe next "
                                 "(LoginView puts the safe next in the context) and nothing else")
                self.assertEqual(urlsplit(href).path, SIGNUP, "backend/templates/registration/login.html: the Sign up link goes to the sign-up page")

    def test_a_failed_attempt_keeps_next_on_its_links(self):
        response = self.client.post(LOGIN + "?next=/app/files/", {"username": "someone", "password": "wrong"})
        self.assertEqual(next_of(self.parse(response).href("Sign up")), "/app/files/",
                         "backend/templates/registration/login.html: a page drawn again after a failed attempt keeps the safe next on its Sign up link")

    def test_what_the_page_puts_back_is_escaped(self):
        hostile = '"><script>alert(1)</script>'
        response = self.client.post(LOGIN + "?" + urlencode({"next": "/x" + hostile}),
                                    {"username": hostile, "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        self.assertNotIn("<script>alert(1)</script>", page.html,
                         "a typed name or a ?next= must be escaped where the page puts it back "
                         "(components/field.html for the name, |urlencode in login.html for next)")
        self.assertEqual(self.field(page, "id_username").get("value"), hostile)
        self.assertEqual(next_of(page.href("Sign up")), "/x" + hostile,
                         "backend/templates/registration/login.html: the Sign up link carries next through |urlencode, so it reads back as it went in")

    def test_the_other_link_goes_to_password_reset(self):
        self.assertEqual(self.parse(self.client.get(LOGIN)).href("Forgot password?"), "/accounts/password_reset/")

    def test_the_menu_marks_this_page(self):
        html = self.client.get(LOGIN).content.decode()
        self.assertRegex(html, r'<a [^>]*href="/accounts/login/"[^>]*aria-current="page"[^>]*>Sign in</a>',
                         "the shell's menu marks Sign in as the current page on /accounts/login/")


class SignUpPageTests(PageCase):
    longMessage = False

    FIELDS = {
        "id_username": ("username", "text", "username", "Username"),
        "id_email": ("email", "email", "email", "Email"),
        "id_password1": ("password1", "password", "new-password", "Password"),
        "id_password2": ("password2", "password", "new-password", "Confirm password"),
    }

    def test_the_fields_keep_their_names_labels_and_tokens(self):
        page = self.parse(self.client.get(SIGNUP))
        self.assertEqual(len(page.forms), 1)
        self.assertNotIn("action", page.forms[0],
                         "backend/templates/registration/signup.html: post to the page's own address so ?plan= and ?next= ride along")
        for id, (name, kind, token, label) in self.FIELDS.items():
            with self.subTest(field=id):
                attrs = self.field(page, id)
                self.assertEqual((attrs.get("name"), attrs.get("type"), attrs.get("autocomplete")), (name, kind, token))
                self.assertIn("required", attrs)
                self.assertEqual(page.labels[id], label)
                self.assertNotIn("aria-invalid", attrs, "a blank form has no mistakes to mark")

    def test_the_username_is_not_capitalised_or_spell_checked(self):
        username = self.field(self.parse(self.client.get(SIGNUP)), "id_username")
        self.assertEqual((username.get("autocapitalize"), username.get("spellcheck")), ("none", "false"),
                         "backend/templates/registration/signup.html: the name field passes extra='autocapitalize=\"none\" "
                         "spellcheck=\"false\"', or a phone capitalises the first letter of a name that is case-sensitive")

    def test_the_help_lines_are_tied_to_their_fields(self):
        page = self.parse(self.client.get(SIGNUP))
        said, missing = page.described(self.field(page, "id_email"))
        self.assertEqual(missing, [], "the email field's aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("We verify this address before issuing API keys. It is how we reach you about your account.", said,
                      "backend/templates/registration/signup.html: the email field keeps its help line")
        said, missing = page.described(self.field(page, "id_password1"))
        self.assertEqual(missing, [], "the password field's aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("at least 8 characters", said, "the password's help is the validators' own list (form.password1.help_text)")
        self.assertNotIn("aria-describedby", self.field(page, "id_password2"), "the confirmation has no help line to describe it")

    def test_a_mistake_is_tied_to_its_field_and_the_typed_values_are_kept(self):
        response = self.client.post(SIGNUP, {"username": "a11yuser", "password1": "s3cur3pass!!",
                                             "password2": "different!!", "email": "a11y@example.com"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        confirm = self.field(page, "id_password2")
        self.assertEqual(confirm.get("aria-invalid"), "true")
        said, missing = page.described(confirm)
        self.assertEqual(missing, [], "aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("match", said, "the sentence under the field says what Django found: the passwords do not match")
        for id in ("id_username", "id_email", "id_password1"):
            self.assertNotIn("aria-invalid", self.field(page, id), f"{id} had no mistake")
        self.assertEqual(self.field(page, "id_username").get("value"), "a11yuser")
        self.assertEqual(self.field(page, "id_email").get("value"), "a11y@example.com")
        for id in ("id_password1", "id_password2"):
            self.assertNotIn("value", self.field(page, id), "a password is never put back in the page")

    def test_a_taken_email_is_said_under_the_email_field(self):
        User.objects.create_user("first", email="taken@example.com", password=PASSWORD)
        response = self.client.post(SIGNUP, {"username": "second", "password1": "s3cur3pass!!",
                                             "password2": "s3cur3pass!!", "email": "TAKEN@example.com"})
        page = self.parse(response)
        email = self.field(page, "id_email")
        self.assertEqual(email.get("aria-invalid"), "true")
        self.assertIn("An account with this email address already exists.", page.described(email)[0],
                      "a taken address is said under the email field, tied to it by aria-describedby")

    def test_the_plain_sentence_when_no_plan_was_picked(self):
        for query in ("", "?plan=free", "?plan=bogus"):
            with self.subTest(query=query):
                html = self.client.get(SIGNUP + query).content.decode()
                self.assertIn("Create your account. A workspace is provisioned automatically.", html,
                              "backend/templates/registration/signup.html: the plain sentence shows when no plan sentence does")
                self.assertNotIn("Team plan", html, "a plan nobody picked is not named")
                self.assertNotIn("Scale plan", html, "a plan nobody picked is not named")

    def test_the_plan_sentence_survives_a_mistake(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.post(f"{SIGNUP}?plan={plan}", {"username": "x", "password1": "a", "password2": "b"})
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"{plan.title()} plan", main_of(response.content.decode()),
                              "backend/templates/registration/signup.html shows plan_blurb after a mistake too")

    def test_what_the_page_puts_back_is_escaped(self):
        hostile = '"><script>alert(1)</script>'
        response = self.client.post(SIGNUP + "?" + urlencode({"next": "/x" + hostile}),
                                    {"username": hostile, "email": hostile, "password1": "a", "password2": "b"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        self.assertNotIn("<script>alert(1)</script>", page.html,
                         "a typed value or a ?next= must be escaped where the page puts it back "
                         "(components/field.html for the values, |urlencode in signup.html for next)")
        self.assertEqual(self.field(page, "id_username").get("value"), hostile)
        self.assertEqual(next_of(page.href("Log in")), "/x" + hostile,
                         "backend/templates/registration/signup.html: the Log in link carries next through |urlencode, so it reads back as it went in")

    def test_the_log_in_link_carries_a_safe_next(self):
        for query, expected in (("", None), ("?next=/app/files/", "/app/files/"), ("?next=https://evil.example/", None)):
            with self.subTest(query=query):
                href = self.parse(self.client.get(SIGNUP + query)).href("Log in")
                self.assertEqual(next_of(href), expected,
                                 "backend/templates/registration/signup.html: the Log in link carries the safe next (signup() puts it in the context) and nothing else")
                self.assertEqual(urlsplit(href).path, LOGIN, "backend/templates/registration/signup.html: the Log in link goes to the sign-in page")


class SignedInVisitTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("member", email="member@example.com", password=PASSWORD)
        self.client.force_login(self.user)

    def test_a_signed_in_visit_goes_to_the_dashboard(self):
        for path in (LOGIN, SIGNUP, SIGNUP + "?plan=free", SIGNUP + "?plan=bogus"):
            with self.subTest(path=path):
                self.assertSentTo(self.client.get(path), "/app/",
                                  f"{path}: backend/drop_core/views.py (SignInView and signup) sends a signed-in visitor "
                                  "to signed_in_destination()")

    def test_a_signed_in_visit_goes_to_a_safe_next(self):
        for path in (LOGIN, SIGNUP):
            for target in (DEVICE_LINK, "/o/authorize/?client_id=abc&scope=mcp%3Ause", "/app/files/"):
                with self.subTest(path=path, target=target):
                    self.assertSentTo(self.client.get(path, {"next": target}), target,
                                      f"{path}: a signed-in visitor goes on to a safe ?next= (signed_in_destination() in "
                                      "backend/drop_core/views.py)")

    def test_a_next_that_leaves_the_site_is_not_followed(self):
        for path in (LOGIN, SIGNUP):
            for target in ("https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)", "",
                           "http:evil.example"):
                with self.subTest(path=path, target=target):
                    self.assertSentTo(self.client.get(path, {"next": target}), "/app/",
                                      "backend/drop_core/views.py: safe_next_url() keeps only an address on this site")

    def test_a_next_that_is_the_page_itself_is_not_followed(self):
        for path in (LOGIN, SIGNUP):
            with self.subTest(path=path):
                message = (f"{path}: signed_in_destination() in backend/drop_core/views.py must not send a visitor "
                           "back to the page they asked for")
                self.assertSentTo(self.client.get(path, {"next": path}), "/app/", message)
                self.assertSentTo(self.client.get(path, {"next": path + "?next=/app/files/"}), "/app/", message)

    def test_a_head_request_is_sent_on_too(self):
        self.assertSentTo(self.client.head(LOGIN), "/app/", "SignInView.get answers HEAD as well (django's View.head is get)")

    def test_a_picked_paid_plan_still_shows_the_checkout_step(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.get(f"{SIGNUP}?plan={plan}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("registration/checkout_confirm.html", [template.name for template in response.templates])

    def test_a_signed_in_post_to_sign_in_signs_in_as_the_account_named(self):
        other = User.objects.create_user("other", password=PASSWORD)
        response = self.client.post(LOGIN, {"username": "other", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), other.pk)

    def test_a_signed_in_post_counts_toward_the_lock_like_any_other(self):
        # throttle_login reads a redirect as a successful sign-in and clears that account's count. If a signed-in POST were
        # answered with a redirect, whoever holds an account could clear the count of any other by naming it.
        User.objects.create_user("victim", password=PASSWORD)
        for i in range(9):
            Client().post(LOGIN, {"username": "victim", "password": "wrong"}, REMOTE_ADDR=f"198.51.100.{i}")
        response = self.client.post(LOGIN, {"username": "victim", "password": "wrong"})
        self.assertEqual(response.status_code, 200,
                         "backend/drop_core/views.py: SignInView redirects a signed-in GET only; a POST is a sign-in attempt")
        locked = Client().post(LOGIN, {"username": "victim", "password": PASSWORD})
        self.assertEqual(locked.status_code, 429, "the tenth failure locks the account, whoever made it")

    def test_a_signed_in_post_to_sign_up_still_makes_the_account(self):
        response = self.client.post(SIGNUP, {"username": "second", "password1": "s3cur3pass!!",
                                             "password2": "s3cur3pass!!", "email": "second@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="second").exists())


class SignUpReturnsToNextTests(PageCase):
    longMessage = False

    def post(self, query, **extra):
        data = {"username": "newcomer", "password1": "s3cur3pass!!", "password2": "s3cur3pass!!", "email": "newcomer@example.com"}
        return Client().post(SIGNUP + query, data, **extra)

    def test_a_safe_next_is_where_a_new_account_goes(self):
        for target in (DEVICE_LINK, "/app/files/"):
            with self.subTest(target=target):
                User.objects.filter(username="newcomer").delete()
                response = self.post("?" + urlencode({"next": target}))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], target,
                                 "backend/drop_core/views.py: a free sign-up goes to the safe next, so that a device or OAuth trip is not lost")

    def test_an_unsafe_next_is_not_followed(self):
        for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            with self.subTest(target=target):
                User.objects.filter(username="newcomer").delete()
                self.assertEqual(self.post("?" + urlencode({"next": target}))["Location"], "/app/",
                                 "backend/drop_core/views.py: safe_next_url() keeps only an address on this site")

    def test_no_next_still_goes_to_the_dashboard(self):
        self.assertEqual(self.post("")["Location"], "/app/", "backend/drop_core/views.py: a free sign-up with no next goes to the dashboard")

    def test_a_paid_plan_goes_to_checkout_not_next(self):
        from drop_billing.tests import TEST_ENV, make_stripe_mock

        with mock.patch.dict(os.environ, TEST_ENV, clear=False), mock.patch("drop_billing.views.stripe", make_stripe_mock()):
            response = self.post("?" + urlencode({"plan": "team", "next": DEVICE_LINK}))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://checkout.stripe.com/"),
                        "backend/drop_core/views.py: a paid plan's sign-up goes to its checkout; next is for the free path")


class ReturnToTheNextPageTests(PageCase):
    """The trip a CLI or an MCP client starts: its page needs a sign-in, the visitor signs in or signs up, and the page is
    where they land (spec 4.7)."""
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("returning", email="returning@example.com", password=PASSWORD)
        get_or_create_workspace(self.user)
        EmailVerification.objects.create(user=self.user, email="returning@example.com", is_verified=True,
                                         verified_at=timezone.now())

    def device_link(self):
        grant = Client().post("/api/v1/device/code", {}, content_type="application/json").json()
        return f"/device?user_code={grant['user_code']}"

    def authorize_link(self):
        registration = Client().post("/o/register/", data=json.dumps({
            "client_name": "Round trip client",
            "redirect_uris": ["http://127.0.0.1:9876/callback"],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        }), content_type="application/json")
        self.assertEqual(registration.status_code, 201)
        return "/o/authorize/?" + urlencode({
            "response_type": "code", "client_id": registration.json()["client_id"],
            "redirect_uri": "http://127.0.0.1:9876/callback", "scope": OAUTH_SCOPE_MCP_USE,
            "state": "round-trip", "code_challenge": pkce_challenge(), "code_challenge_method": "S256",
        })

    def sign_in_trip(self, target):
        browser = Client()
        bounce = browser.get(target)
        self.assertEqual(bounce.status_code, 302)
        self.assertEqual(urlsplit(bounce["Location"]).path, LOGIN)
        self.assertEqual(next_of(bounce["Location"]), target)
        page = browser.get(bounce["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertEqual(next_of(self.parse(page).href("Sign up")), target,
                         "backend/templates/registration/login.html: the Sign up link carries next")
        done = browser.post(bounce["Location"], {"username": "returning", "password": PASSWORD})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done["Location"], target,
                         "signing in must land on the page that asked for it, query string and all")
        self.assertEqual(browser.get(target).status_code, 200)

    def sign_up_trip(self, target):
        browser = Client()
        login_url = browser.get(target)["Location"]
        signup_url = self.parse(browser.get(login_url)).href("Sign up")
        signup_page = browser.get(signup_url)
        self.assertEqual(signup_page.status_code, 200)
        self.assertEqual(next_of(self.parse(signup_page).href("Log in")), target,
                         "backend/templates/registration/signup.html: the Log in link carries next, so the way back keeps it")
        done = browser.post(signup_url, {"username": "newcomer", "password1": "s3cur3pass!!",
                                         "password2": "s3cur3pass!!", "email": "newcomer@example.com"})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done["Location"], target,
                         "signing up must land on the page that asked for it (backend/drop_core/views.py, signup)")

    def test_the_device_page_survives_signing_in(self):
        self.sign_in_trip(self.device_link())

    def test_the_device_page_survives_signing_up(self):
        self.sign_up_trip(self.device_link())

    def test_oauth_consent_survives_signing_in(self):
        self.sign_in_trip(self.authorize_link())

    def test_oauth_consent_survives_signing_up(self):
        self.sign_up_trip(self.authorize_link())


class ThrottledPageTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        User.objects.create_user("victim", email="victim@example.com", password=PASSWORD)

    def locked(self, visitor=None, name="victim"):
        self.lock_out("victim", "no-such-user")
        return (visitor or Client()).post(LOGIN, {"username": name, "password": "nope"})

    def test_it_is_a_429_on_the_form_layout_with_the_retry_header(self):
        response = self.locked()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/registration/throttled.html must extend layouts/form.html")
        page = self.parse(response)
        self.assertEqual(page.headings, ["Too many sign-in attempts"])
        self.assertIn("<title>Too many sign-in attempts | Veilfile</title>", page.html,
                      "backend/templates/registration/throttled.html: {% block title %} is the title, then | Veilfile")
        said = main_of(page.html)
        self.assertIn("Sign-in is paused for this account or network after repeated failed attempts.", said,
                      "the message is the throttle's (backend/drop_core/throttle.py)")
        self.assertIn("Wait about 15 minutes, then try again.", said,
                      "backend/templates/registration/throttled.html says how long to wait, from retry_minutes")
        self.assertEqual(page.href("Log in"), LOGIN)
        self.assertEqual(page.href("Forgot password?"), "/accounts/password_reset/")
        self.assertEqual(len(page.alerts), 1, "the message is announced as an alert, once")

    def test_a_real_and_a_missing_account_get_the_same_page(self):
        visitors = {"signed out": Client(), "signed in": Client()}
        visitors["signed in"].force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        for label, visitor in visitors.items():
            with self.subTest(visitor=label):
                cache.clear()
                self.lock_out("victim", "no-such-user")
                real = visitor.post(LOGIN, {"username": "victim", "password": "nope"})
                fake = visitor.post(LOGIN, {"username": "no-such-user", "password": "nope"})
                self.assertEqual((real.status_code, fake.status_code), (429, 429))
                self.assertEqual(real.content, fake.content,
                                 "backend/templates/registration/throttled.html: two addresses must get the same bytes, so "
                                 "the page can say nothing of the account asked about")
                self.assertEqual(sorted(real.items()), sorted(fake.items()), "headers must match too")

    def test_it_carries_no_token_and_nothing_of_the_visitor(self):
        visitor = Client()
        visitor.force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        response = self.locked(visitor)
        html = response.content.decode()
        self.assertEqual(response.status_code, 429)
        for private in ("csrfmiddlewaretoken", "bystander", "bystander@example.com", "victim", "no-such-user"):
            self.assertNotIn(private, html, f"backend/templates/registration/throttled.html shows {private!r}")
        self.assertNotIn("csrftoken", response.cookies, "a throttled page sets no CSRF cookie: it has no form")
        self.assertEqual(self.parse(response).forms, [], "a throttled page has no form")

    def test_a_signed_in_asker_sees_the_same_page_but_for_the_shells_menu(self):
        # The page is shown to a signed-in visitor too (a wrong password on the email form or the password form, or a
        # sign-in posted while signed in). Only the shell's menu may tell them from a signed-out one: Dashboard for Sign in.
        member = Client()
        member.force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        self.lock_out("victim")
        signed_out = Client().post(LOGIN, {"username": "victim", "password": "nope"}).content.decode()
        signed_in = member.post(LOGIN, {"username": "victim", "password": "nope"}).content.decode()
        without_menu = lambda html: re.sub(r'<nav class="menu".*?</nav>', "", html, flags=re.S)
        self.assertNotEqual(signed_out, signed_in, "the menu says Dashboard to a signed-in visitor and Sign in to the rest")
        self.assertEqual(without_menu(signed_out), without_menu(signed_in),
                         "backend/templates/registration/throttled.html: nothing but the shell's menu may differ between a "
                         "signed-in and a signed-out visitor")
        self.assertIn(">Dashboard</a>", signed_in, "the shell's menu shows a signed-in visitor Dashboard")
        self.assertNotIn(">Sign in</a>", signed_in, "the shell's menu does not offer a signed-in visitor Sign in")

    def test_the_template_reads_nothing_of_the_visitor_or_the_request(self):
        source = (TEMPLATES / "registration" / "throttled.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(user|request|csrf\w*)\b", source),
                          "backend/templates/registration/throttled.html reads the user, the request or the CSRF token: the "
                          "same page is served for any address asked about, so it may use only what the throttle fixes for a kind of "
                          "refusal (title, message, the wait), never the user, the request or a token")

    def test_the_admin_sign_in_gets_the_same_page(self):
        User.objects.create_superuser("boss", "boss@example.com", PASSWORD)
        for _ in range(10):
            self.client.post("/admin/login/", {"username": "boss", "password": "nope", "next": "/admin/"})
        response = self.client.post("/admin/login/", {"username": "boss", "password": PASSWORD, "next": "/admin/"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/registration/throttled.html must extend layouts/form.html")
        self.assertEqual(self.parse(response).headings, ["Too many sign-in attempts"])

    def test_a_password_reset_limit_uses_the_same_page(self):
        for _ in range(3):
            self.client.post("/accounts/password_reset/", {"email": "victim@example.com"})
        response = self.client.post("/accounts/password_reset/", {"email": "victim@example.com"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "3600")
        page = self.parse(response)
        self.assertEqual(page.headings, ["Too many password reset requests"])
        self.assertIn("Wait about 60 minutes, then try again.", main_of(page.html),
                      "backend/templates/registration/throttled.html says how long to wait, from retry_minutes")


class CheckoutConfirmPageTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("payer", email="payer@example.com", password=PASSWORD)
        self.client.force_login(self.user)

    def test_it_names_the_plan_and_its_price_from_plans_py(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.get(f"{SIGNUP}?plan={plan}")
                self.assertEqual(response.status_code, 200)
                page = self.parse(response)
                self.assertEqual(page.headings,
                                 [f"Subscribe to the {plan.title()} plan (${PLANS[plan]['price_usd']}/mo, tax included)."])
                self.assertIn("<title>Checkout | Veilfile</title>", page.html,
                              "backend/templates/registration/checkout_confirm.html: {% block title %} is Checkout, then | Veilfile")

    def test_it_asks_before_anything_is_charged(self):
        page = self.parse(self.client.get(f"{SIGNUP}?plan=team"))
        self.assertIn("You're signed in as payer. Checkout opens on Stripe, and nothing is charged until you confirm there.",
                      main_of(page.html).replace("&#x27;", "'"),
                      "backend/templates/registration/checkout_confirm.html says who is signed in and that nothing is charged yet")
        self.assertEqual(len(page.forms), 1)
        self.assertEqual(page.forms[0].get("method", "").lower(), "post")
        self.assertNotIn("action", page.forms[0])
        self.assertTrue(any(attrs.get("name") == "csrfmiddlewaretoken" for attrs in page.inputs),
                        "backend/templates/registration/checkout_confirm.html needs {% csrf_token %} inside the form")
        self.assertEqual([text for _attrs, text in page.buttons if text == "Continue to checkout"], ["Continue to checkout"])
        self.assertEqual(page.href("Go to your dashboard"), "/app/")

    def test_it_is_kept_out_of_search_results(self):
        html = self.client.get(f"{SIGNUP}?plan=team").content.decode()
        self.assertIn('<meta name="robots" content="noindex">', html,
                      "backend/templates/registration/checkout_confirm.html: {% block meta %} carries the noindex tag")

    def test_the_username_is_escaped(self):
        hostile = User.objects.create_user("<b>x</b>", email="hostile@example.com", password=PASSWORD)
        self.client.force_login(hostile)
        html = self.client.get(f"{SIGNUP}?plan=team").content.decode()
        self.assertNotIn("<b>x</b>", html, "backend/templates/registration/checkout_confirm.html must escape the username")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html, "the escaped username is on the page")


class SignOutTests(PageCase):
    longMessage = False

    def test_signing_out_goes_to_the_sign_in_page_and_draws_no_page(self):
        self.client.force_login(User.objects.create_user("leaver", password=PASSWORD))
        response = self.client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], LOGIN,
                         "backend/config/settings.py: LOGOUT_REDIRECT_URL sends every sign-out to the sign-in page")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_hand_made_post_that_names_the_logout_page_as_its_next_still_gets_a_page(self):
        # LogoutView draws a "Logged out" page, not a redirect, only when the address to go to is the logout address itself.
        # No form on the site sends that; with the project's page gone, Django's own admin page answers it.
        self.client.force_login(User.objects.create_user("leaver", password=PASSWORD))
        response = self.client.post("/accounts/logout/", {"next": "/accounts/logout/"})
        self.assertEqual(response.status_code, 200,
                         "a POST to /accounts/logout/ that names itself as next must be answered with a page, not an error")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_old_logged_out_page_is_gone(self):
        self.assertFalse((TEMPLATES / "registration" / "logged_out.html").exists(),
                         "backend/templates/registration/logged_out.html was removed on purpose: LOGOUT_REDIRECT_URL "
                         "redirects every sign-out, so nothing draws it, and it carried the old includes")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_sign_in_pages > /tmp/sign_in_pages.out 2>&1; grep -E '^(FAIL|ERROR): |^Ran |^FAILED|^OK' /tmp/sign_in_pages.out; cd ..`

Expected: `Ran 58 tests` and `FAILED (failures=64)`, with no `ERROR` line. 29 of the 58 test methods fail (64 failure lines, because the loops use subtests). Each failure prints its own message and no page. The failing methods, with the first words of the message:
- `FormLayoutTests`: `test_each_page_extends_the_form_layout_and_includes_nothing_old` ("backend/templates/registration/login.html must extend layouts/form.html", for each of the four pages), `test_each_page_has_the_shells_landmarks_and_one_h1_inside_main` ("registration/login.html lost the skip link"), `test_each_page_names_itself_in_its_title` ("{% block title %} names the page, then | Veilfile"), `test_every_button_and_link_in_the_page_answers_the_cursor` ("the main action 'Log in' is <button class=\"action interactive\" ...", for each of the four pages) and `test_no_page_takes_focus_for_the_visitor` ("drop autofocus from username", login and sign-up only).
- `SignInPageTests`: `test_the_sign_up_link_carries_a_safe_next_and_only_a_safe_one` (the device-link case), `test_a_failed_attempt_keeps_next_on_its_links`, `test_what_the_page_puts_back_is_escaped`, `test_the_menu_marks_this_page`, `test_the_fields_keep_their_names_labels_and_tokens` (only for the name field's `autocapitalize`).
- `SignUpPageTests`: `test_the_help_lines_are_tied_to_their_fields`, `test_the_log_in_link_carries_a_safe_next`, `test_the_username_is_not_capitalised_or_spell_checked`, `test_what_the_page_puts_back_is_escaped`.
- `SignedInVisitTests`: `test_a_signed_in_visit_goes_to_the_dashboard`, `test_a_signed_in_visit_goes_to_a_safe_next`, `test_a_next_that_leaves_the_site_is_not_followed`, `test_a_next_that_is_the_page_itself_is_not_followed`, `test_a_head_request_is_sent_on_too`. The old pages answer 200 with a form, not a redirect.
- `SignUpReturnsToNextTests.test_a_safe_next_is_where_a_new_account_goes` (it goes to `/app/`).
- `ReturnToTheNextPageTests`: all four tests.
- `ThrottledPageTests`: `test_it_is_a_429_on_the_form_layout_with_the_retry_header`, `test_a_signed_in_asker_sees_the_same_page_but_for_the_shells_menu`, `test_the_admin_sign_in_gets_the_same_page`.
- `CheckoutConfirmPageTests.test_it_names_the_plan_and_its_price_from_plans_py` (the title).
- `SignOutTests.test_the_old_logged_out_page_is_gone`.

The other 29 pass already, and must keep passing. They pin what the move must not lose:
- the field names, labels, tokens and kept values;
- the one failure sentence, and no alert on a first visit;
- the byte-identical throttled page for a real and a missing account, and its absence of a token, a form and the visitor;
- the checkout page's price, `noindex`, token and escaped username;
- a signed-in POST still signing in or making the account, and counting toward the lock;
- the logout redirect and the hand-made logout POST.

- [ ] **Step 2: Add the signed-in visit, the safe next and the sign-in view**

In `backend/drop_core/views.py` make these five edits (each quoted text occurs once in the file).

Replace

```python
from urllib.parse import urlencode
```

with

```python
from urllib.parse import urlencode, urlsplit
```

Replace

```python
from django.utils.http import content_disposition_header
```

with

```python
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
```

Replace

```python
@funnel.sets_funnel_cookie
def signup(request):
```

with

```python
def safe_next_url(request):
    """The ?next= of a sign in or sign up page when it is an address on this site that a redirect may
    use, else "".

    It is the test LoginView applies after a sign-in: this host, http or https, https when the request
    is. A page that sends a visitor on, or links to the page that will, reads the address here and
    never from the query itself.
    """
    url = request.GET.get("next", "")
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if url and allowed else ""


def signed_in_destination(request):
    """Where a signed-in visitor to sign in or sign up is sent: the safe ?next=, else the dashboard.
    A next that names the page itself would send the visitor round in a circle, so it counts as none."""
    url = safe_next_url(request)
    if url and urlsplit(url).path != request.path:
        return url
    return settings.LOGIN_REDIRECT_URL


class SignInView(auth_views.LoginView):
    """Sign in (registration/login.html).

    A signed-in visitor who opens the page goes on to ?next= or the dashboard. One who posts it is
    signed in as the account they name, as before: answering that POST with a redirect would make
    throttle_login, which reads a redirect as a successful sign-in, clear the failure count of
    whichever account the POST named.
    """

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(signed_in_destination(request))
        return super().get(request, *args, **kwargs)


@funnel.sets_funnel_cookie
def signup(request):
```

Replace

```python
    if request.method == "POST":
        # The signup endpoint sends email: throttle it per IP.
```

with

```python
    # A signed-in visitor has no use for the form: send them on (a POST is not a visit and is handled
    # as ever). The paid branch above comes first, so a picked plan still reaches its checkout step.
    if request.user.is_authenticated and request.method in ("GET", "HEAD"):
        return redirect(signed_in_destination(request))

    if request.method == "POST":
        # The signup endpoint sends email: throttle it per IP.
```

Replace

```python
            if paid:
                return redirect(_paid_signup_checkout(user, plan))
            return redirect("/app/")
    else:
        form = SignupForm()
        funnel.record(request, funnel.SIGNUP_STARTED, once_per_session=True)
    return render(
        request,
        "registration/signup.html",
        {"form": form, "plan": plan, "plan_blurb": _plan_blurb(plan)},
    )
```

with

```python
            if paid:
                return redirect(_paid_signup_checkout(user, plan))
            return redirect(safe_next_url(request) or "/app/")
    else:
        form = SignupForm()
        funnel.record(request, funnel.SIGNUP_STARTED, once_per_session=True)
    return render(
        request,
        "registration/signup.html",
        {
            "form": form,
            "plan": plan,
            "plan_blurb": _plan_blurb(plan),
            "next": safe_next_url(request),
        },
    )
```

In `backend/config/urls.py` make two edits. Replace

```python
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
```

with

```python
from django.contrib import admin
from django.contrib.auth.decorators import login_required
```

(`auth_views` was used only by the sign-in route.) Replace

```python
        throttle_login(auth_views.LoginView.as_view()),
```

with

```python
        throttle_login(core_views.SignInView.as_view()),
```

- [ ] **Step 3: Put the four pages on the form layout**

Replace the whole of `backend/templates/registration/login.html` (60 lines of standalone page with an inline `<style>`) with:

```django
{% extends "layouts/form.html" %}
{# Page: sign in (spec 4.7). It posts to its own address, so a ?next= rides along. A failed attempt always reads the same, whatever was wrong. #}
{% block title %}Log in | Veilfile{% endblock %}
{% block form_page %}
<h1>Log in</h1>
<p>Private, ephemeral artifact hosting for AI agents.</p>
{% if form.errors %}{% include "components/form_errors.html" with message="Invalid username, email, or password. Try again." only %}{% endif %}
<form method="post">
{% csrf_token %}
{% include "components/field.html" with id="id_username" name="username" label="Username or email" type="text" value=form.username.value autocomplete="username" required=True extra='autocapitalize="none" spellcheck="false"' only %}
{% include "components/field.html" with id="id_password" name="password" label="Password" type="password" autocomplete="current-password" required=True only %}
<button class="action interactive" type="submit">Log in</button>
</form>
<p class="form-links">No account? <a class="label interactive" href="{% url 'signup' %}{% if next %}?next={{ next|urlencode }}{% endif %}">Sign up</a> <span aria-hidden="true">·</span> <a class="label interactive" href="{% url 'password_reset' %}">Forgot password?</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/signup.html` with:

```django
{% extends "layouts/form.html" %}
{# Page: sign up (spec 4.7). It posts to its own address, so ?plan= and ?next= ride along. The plan sentence comes from the view, which is where a plan's price is written (AGENTS.md, Pricing changes). #}
{% block title %}Sign up | Veilfile{% endblock %}
{% block form_page %}
<h1>Sign up</h1>
<p>{% if plan_blurb %}{{ plan_blurb }}{% else %}Create your account. A workspace is provisioned automatically.{% endif %}</p>
{% include "components/form_errors.html" with errors=form.non_field_errors only %}
<form method="post">
{% csrf_token %}
{% include "components/field.html" with id="id_username" name="username" label="Username" type="text" value=form.username.value autocomplete="username" required=True extra='autocapitalize="none" spellcheck="false"' errors=form.username.errors only %}
{% include "components/field.html" with id="id_email" name="email" label="Email" type="email" value=form.email.value autocomplete="email" required=True help="We verify this address before issuing API keys. It is how we reach you about your account." errors=form.email.errors only %}
{% include "components/field.html" with id="id_password1" name="password1" label="Password" type="password" autocomplete="new-password" required=True help=form.password1.help_text errors=form.password1.errors only %}
{% include "components/field.html" with id="id_password2" name="password2" label="Confirm password" type="password" autocomplete="new-password" required=True errors=form.password2.errors only %}
<button class="action interactive" type="submit">Create account</button>
</form>
<p class="form-links">Already have an account? <a class="label interactive" href="{% url 'login' %}{% if next %}?next={{ next|urlencode }}{% endif %}">Log in</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/throttled.html` with:

```django
{% extends "layouts/form.html" %}
{# Page: too many attempts (429, spec 4.9). One page for any address asked about, so it holds no form, no token and nothing of the visitor: LoginThrottleTests holds two responses byte for byte. #}
{% block title %}{{ title }} | Veilfile{% endblock %}
{% block form_page %}
<h1>{{ title }}</h1>
<p role="alert">{{ message }} Wait about {{ retry_minutes }} minutes, then try again.</p>
<p class="form-links"><a class="label interactive" href="{% url 'login' %}">Log in</a> <span aria-hidden="true">·</span> <a class="label interactive" href="{% url 'password_reset' %}">Forgot password?</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/checkout_confirm.html` with:

```django
{% extends "layouts/form.html" %}
{# Page: confirm a paid plan before Stripe (spec 4.7). The price comes from the view, which reads it from plans.py (AGENTS.md, Pricing changes). #}
{% block title %}Checkout | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Subscribe to the {{ plan_name }} plan (${{ price_usd }}/mo, tax included).</h1>
<p>You're signed in as {{ user.get_username }}. Checkout opens on Stripe, and nothing is charged until you confirm there.</p>
<form method="post">
{% csrf_token %}
<button class="action interactive" type="submit">Continue to checkout</button>
</form>
<p class="form-links">Not now? <a class="label interactive" href="/app/">Go to your dashboard</a></p>
{% endblock %}
```

(Each `{# #}` comment is on one line: Django 6.1 shows a multi-line comment as visible text. Every `components/` include ends with `only`, and `{% csrf_token %}` sits in the page, never in a component. `extra` is a string literal, as Task 1 requires (the single quotes let the double quotes inside it stand). The throttled template says no `user`, `request` or `csrf` anywhere, in its comment too, because a test reads its source for those words. The "·" is U+00B7, in all three fonts. Both forms keep posting to their own address with no `action`, so `?next=` and `?plan=` ride along.)

- [ ] **Step 4: Remove the page nothing renders**

Prove that nothing names it.

Run: `grep -rIn "logged_out" backend site-tests .github AGENTS.md README.md docs | grep -v "backend/drop_core/tests_sign_in_pages.py"`
Expected: no output.

Then remove it.

Run: `git rm backend/templates/registration/logged_out.html`
Expected: `rm 'backend/templates/registration/logged_out.html'`.

(Django's `LogoutView` would draw it only if the address to go to were the logout address itself. `LOGOUT_REDIRECT_URL` sends every form on the site to `/accounts/login/`. A hand-made POST to `/accounts/logout/` whose body says `next=/accounts/logout/` is the one request that rendered it; Django's own admin page answers that now, and `test_a_hand_made_post_that_names_the_logout_page_as_its_next_still_gets_a_page` pins that it is a page.)

- [ ] **Step 5: Run the new tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_sign_in_pages; cd ..`
Expected: `Ran 58 tests` and `OK` (about half a second).

- [ ] **Step 6: Hold the new modules to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a test class whose `longMessage` is not `False`, and its message names the file and the fix. A failure that compares a page would otherwise print the page before the message. This is the first edit of the list in the plan: it adds this task's module and Task 1's `pages.tests_forms` (whose classes already set `longMessage = False`, and which Task 1 does not list itself). In `backend/pages/tests_site_rules.py` replace

```python
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed", "pages.tests_forms")
```

with

```python
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed", "pages.tests_forms", "drop_core.tests_sign_in_pages")
```

(Each task after this one that adds a test module appends it to this tuple, quoting it as this step leaves it.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules; cd ..`
Expected: `OK`. The four templates are under the shell rules by what they extend: no inline `style`, components included with `only`, no `<header>` or `<footer>`, every character in the three fonts.

- [ ] **Step 7: Run the old-markup tests and watch them break**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.AccessibilityTests drop_core.tests_menu drop_core.tests_ambient > /tmp/old_markup.out 2>&1; grep -E '^(FAIL|ERROR): |^Ran |^FAILED|^OK' /tmp/old_markup.out | cut -c1-150; cd ..`

Expected: `Ran 39 tests` and `FAILED (failures=27, errors=3)`. They are exactly these 22 test methods and no others (several run once for `/accounts/login/` and once for `/accounts/signup/`, which is why there are 30 lines):
- `AccessibilityTests`:
  - `test_skip_link_and_main_id_on_django_pages` (sign-up and sign-in: the shell's skip link is `class="skip-link label"` and its main is `<main id="main" class="shell-main">`).
  - `test_decorative_wordmark_cursor_hidden` (the shell has a lockup, not the old `.brand` wordmark).
  - `test_focus_indicator_rule_present_and_outline_not_removed` (the focus rule is in `base.css` now, not inline in the page).
  - `test_login_errors_announced` (`class="errors" role="alert"` is the old markup).
- `SharedMenuTests`: `test_menu_present_on_every_page`, `test_menu_link_order`, `test_menu_sits_after_skip_link` (an error: no old skip link to find), `test_menu_keyboard_behavior`, `test_menu_uses_menuitem_roles`. They read the old menu include.
- `tests_ambient` (the old ambient layer, boot script, tokens and controls):
  - `AmbientBootScriptTests.test_boot_script_runs_before_any_style_or_meta`
  - `AmbientFieldTests.test_ambient_layer_present_on_every_page`, `test_django_pages_all_use_field_intensity`, `test_ambient_is_first_in_body` (an error)
  - `AmbientStylesTests.test_light_theme_tokens_defined`, `test_dark_tokens_unchanged`, `test_blob_alpha_ceiling_capped`, `test_transform_only_keyframes`, `test_print_hides_ambient`, `test_no_literal_hex_for_themed_surfaces`
  - `AmbientControlsTests.test_control_cluster_on_every_page`, `test_theme_segmented_group`, `test_motion_toggle_reports_state`

These keep passing without a change, and keep their reasons:
- `test_signup_errors_linked_to_fields` pins `aria-invalid="true"`, `aria-describedby="id_password2_error"` and `id="id_password2_error"`: the error stays tied to its field.
- `AccountFormAccessibilityTests` pins `value="taken"`, `value="kept@example.com"`, `value="someone"` and the `autocomplete` tokens with `new-password` counted twice.
- `test_login_form_invites_email` pins "Username or email".
- `SignupPlanTests` pins "Team plan" and "Scale plan", and that `?plan=bogus` names neither anywhere on the page.
- `PaidSignupTests` pins `method="post"` and `Team`.
- `FunnelSessionlessTests` pins that a GET of sign-up sets no session. The shell reads the session for its menu and does not create one.
- `LoginThrottleTests` pins the two byte-identical 429 bodies.
- `tests_ambient` `test_boot_script_reads_theme_and_motion_keys`, `test_controls_script_writes_same_keys`, `test_veil_present_only_on_text_heavy_pages`, `test_webgl_swap_path_documented` and `NoTemplateCommentLeakTests`, and `tests_menu` `test_no_visible_trust_label`, are about the old includes or the lack of a string, so they still pass.
- The sign-up throttle tests (`test_signup_rate_limited_per_ip`, `test_signup_limit_holds_with_spoofed_prefix`, `test_signup_limit_covers_the_64`) post twelve times from one client that the first sign-up signed in. They pass unchanged because a signed-in POST is processed as ever.

- [ ] **Step 8: Move the old-markup tests to the page that remains old**

The pages still on the old includes are the password reset pages, email verification, the device flow and OAuth consent. `/accounts/password_reset/` is public and carries everything these tests assert: the old skip link and main, the `.brand` wordmark and its hidden `_`, the inline `:focus-visible` rule, the boot script, the ambient layer and its tokens, the control cluster, and the old menu with its ten `role="menuitem"` links. Task 3 moves it and retires these tests (it deletes `tests_menu.py`, `tests_ambient.py` and the three `AccessibilityTests` methods that pin the old markup), so they are pointed here only to keep this commit green.

In `backend/drop_core/tests.py`, in `AccessibilityTests`:

1. Replace

```python
    def test_skip_link_and_main_id_on_django_pages(self):
        urls = [
            "/accounts/signup/",
            "/accounts/login/",
            "/accounts/password_reset/",
        ]
```

with

```python
    def test_skip_link_and_main_id_on_django_pages(self):
        # Sign-in and sign-up are on the shell: FormLayoutTests in drop_core/tests_sign_in_pages.py holds their landmarks.
        urls = [
            "/accounts/password_reset/",
        ]
```

2. Replace

```python
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, '<span aria-hidden="true">_</span>')
```

with

```python
        response = self.client.get("/accounts/password_reset/")
        self.assertContains(response, '<span aria-hidden="true">_</span>')
```

3. Replace

```python
    def test_focus_indicator_rule_present_and_outline_not_removed(self):
        response = self.client.get("/accounts/signup/")
```

with

```python
    def test_focus_indicator_rule_present_and_outline_not_removed(self):
        response = self.client.get("/accounts/password_reset/")
```

4. Replace

```python
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="errors" role="alert"')
```

with

```python
        self.assertEqual(response.status_code, 200)
        # A failed sign-in is announced: one role=alert summary that holds the sentence.
        from drop_core.tests_sign_in_pages import Page

        alerts = Page(response.content.decode()).alerts
        self.assertEqual(len(alerts), 1)
        self.assertIn("Invalid username, email, or password. Try again.", alerts[0])
```

(The test still pins what it pinned: a failed sign-in is announced to a screen reader, as an alert, with the sentence in it. Only the old class names are gone.)

In `backend/drop_core/tests_menu.py`:

1. Replace

```python
"""Hamburger menu on the Django pages that are not on the shell yet (sign in, sign up).
```

with

```python
"""Hamburger menu on the Django pages that are not on the shell yet (the password reset page stands for them).
```

2. Replace

```python
MENU_PAGES = [
    "/accounts/login/",
    "/accounts/signup/",
]
```

with

```python
MENU_PAGES = [
    "/accounts/password_reset/",
]
```

3. Replace

```python
        # opening moves focus into the menu.
        html = _html(self.client.get("/accounts/login/"))
```

with

```python
        # opening moves focus into the menu.
        html = _html(self.client.get("/accounts/password_reset/"))
```

4. Replace

```python
    def test_menu_uses_menuitem_roles(self):
        html = _html(self.client.get("/accounts/login/"))
```

with

```python
    def test_menu_uses_menuitem_roles(self):
        html = _html(self.client.get("/accounts/password_reset/"))
```

In `backend/drop_core/tests_ambient.py`, replace

```python
FIELD_PAGES = [
    ("/accounts/login/", False),
    ("/accounts/signup/", False),
]
```

with

```python
FIELD_PAGES = [
    ("/accounts/password_reset/", False),
]
```

and replace every `self.client.get("/accounts/login/")` in the file (11 places) with `self.client.get("/accounts/password_reset/")`:

```bash
python3 - <<'EOF'
from pathlib import Path

path = Path("backend/drop_core/tests_ambient.py")
text = path.read_text()
old = 'self.client.get("/accounts/login/")'
assert text.count(old) == 11, text.count(old)
path.write_text(text.replace(old, 'self.client.get("/accounts/password_reset/")'))
EOF
```

Expected: no output.

Run: `grep -n "/accounts/login/\|/accounts/signup/" backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py`
Expected: no output.

(`NoTemplateCommentLeakTests` read the login and sign-up pages through `FIELD_PAGES`; `FormLayoutTests.test_no_page_leaves_a_template_comment_in_its_text` now holds the four moved pages.)

- [ ] **Step 9: Run those tests, and every test that exercises these pages**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.AccessibilityTests drop_core.tests_menu drop_core.tests_ambient drop_core.tests_sign_in_pages pages.tests_site_rules; cd ..`
Expected: `Ran 112 tests` and `OK`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_sign_in_pages drop_core.tests.AccessibilityTests drop_core.tests_menu drop_core.tests_ambient pages.tests_site_rules drop_core.tests_fix_auth drop_core.tests_auth_carryover drop_core.tests_fix_web.FunnelSessionlessTests drop_billing.tests_fix_billing.PaidSignupTests drop_core.tests.SignupPlanTests drop_core.tests.EmailVerificationTests drop_core.tests.PasswordResetTests drop_core.tests.FunnelTests config.tests_fix_platform; cd ..`
Expected: `Ran 348 tests` and `OK` (about two minutes; the password hashing in the older tests is slow).

- [ ] **Step 10: Keep AGENTS.md true**

In `AGENTS.md`, in the repo map, replace

```
                       the blog/, docs/ and legal/ pages, all on the new shell; older
                       pages (sign-in, device flow, OAuth consent) until they move
                       onto it
```

with

```
                       the blog/, docs/ and legal/ pages, and sign-in, sign-up and
                       checkout confirm (registration/), all on the new shell; a page
                       whose template still includes includes/ambient.html is an older
                       page that has not moved onto it yet
```

(The new sentence is true by itself, whichever pages have moved: Tasks 3 to 5 move the rest and edit nothing here. Task 8 writes the form-page recipe and the final repo map, starting from this text and Task 1's `layouts/ (chapter, reading, form),`.)

- [ ] **Step 11: Run the whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test; cd ..`
Expected: `Ran 1404 tests` and `OK` (about 4.5 minutes). That is 1,346 tests before this task with Task 1 as drafted, and 58 new here; the total follows Task 1's own count, so if Task 1 changes it, the difference to this run is still the 58.

Run: `git diff --quiet HEAD -- backend/legal/documents && echo untouched`
Expected: `untouched`.

- [ ] **Step 12: Commit**

```bash
git add backend/drop_core/tests_sign_in_pages.py backend/drop_core/views.py backend/config/urls.py \
  backend/templates/registration/login.html backend/templates/registration/signup.html \
  backend/templates/registration/throttled.html backend/templates/registration/checkout_confirm.html \
  backend/drop_core/tests.py backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py \
  backend/pages/tests_site_rules.py AGENTS.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Sign in, sign up, checkout confirm and the throttled page on the form layout: a signed-in visit goes on to the safe next or the dashboard, sign up returns to next, and the unused logged-out page is removed"
```

(`git rm` in Step 4 already staged the removal of `logged_out.html`, so the commit holds it.)

---

---

### Task 3: Password reset and email verification

**Files:**
- Create: `backend/drop_core/tests_reset_verify_pages.py`
- Modify (each rewritten whole): `backend/templates/registration/password_reset_form.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, `verify_email.html`, `verify_email_done.html`, `verify_email_invalid.html`
- Modify (tests): `backend/pages/tests_site_rules.py` (the new module joins `READING_TEST_MODULES`, Step 3) and `backend/drop_core/tests.py` (three tests of the old markup go, two are pointed at the shell's markup, Step 7)
- Delete: `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py` (the old-markup tests, retired in Step 7)
- Not touched, on purpose: the views, the forms, `config/urls.py`, and the mail templates (`password_reset_email.html`, `password_reset_subject.txt`, `verify_email_email.txt`, `email_change_notice_email.txt`). The mail templates are plain text, not pages, and are not part of the shell. `AGENTS.md` is not edited either: Task 2 worded its repo-map sentence so that it stays true as pages move (Step 8 checks it).

**Interfaces:**
- Consumes:
  - Task 1's `templates/layouts/form.html`. It extends `shell.html`, sets the body class `layout-form`, wraps its main block in `<div class="form-page">` and offers `{% block form_page %}`. The shell's own `title` and `meta` blocks are used as they are.
  - Task 1's `components/field.html`, with the inputs `id`, `name`, `label`, `type`, `value`, `autocomplete`, `required`, `autofocus`, `errors` (and `help`, `extra`, `mono`, which this task does not use). `errors` is given a form's `form.<field>.errors`, an `ErrorList` that iterates its sentences and is falsy when empty, never a bare string. The component ties an error to its input by `aria-invalid="true"` and an `aria-describedby` that names `<input id>_error` (and `<input id>_help`, first, when there is a help line).
  - Task 1's `components/form_errors.html`, with the input `errors` (given `form.non_field_errors`) or `message` (a fixed sentence, not used here).
  - Task 1's CSS: `.form-page` (its bare `h1` and `p`), `.action`, `.form-links`, `.notice`.
  - How these pages write the controls, which is Task 1's contract:
    - the main action is `<button class="action interactive" type="submit">`, or `<a class="action interactive" href="...">` on a page with no form;
    - a secondary action is `<a class="label interactive">` or `<button class="label interactive">` inside `<p class="form-links">`;
    - a status line is `<p class="notice" role="status">`.
  - The views, forms and URL names, which do not change.
- Produces:
  - Seven pages on the form layout. Each has one `h1` (the name in its title), the title `<name> | Veilfile`, its current words, links and field names, and a `noindex` robots meta where it holds a token, an address or a one-time state (every page but the reset form).
  - `backend/drop_core/tests_reset_verify_pages.py`. Its tables `PAGES`, `INPUTS`, `ERRORS`, `MAIN_ACTION`, `LINKS` and `WORDS` name every state of these pages and how each is reached. Task 7 can take its fixtures from `EveryStateTests.setUpTestData`.
  - The old-markup tests are retired (Step 7): `tests_menu.py`, `tests_ambient.py` and three `AccessibilityTests` methods. Task 2 had pointed them at the reset page as the one old page left; they are not pointed at another, so Tasks 4 and 5 and Task 8 have nothing of them to re-point or delete. What they guarded that is not about the old markup lives on in the new module (`NoTemplateCommentLeakTests`, `FocusRingTests`), and the rest is held by the shell's own tests.

**What a visitor sees change.** The old standalone look goes (the brand block, the card, the ambient background, the old menu, each page's own style). The words do not change, with these exceptions.
- The tab title reads `<name> | Veilfile` where it read `<name> — Veilfile`, as every page of the new site does (the voice rules ban the em dash).
- Each page gains an `h1` that repeats the name in its title.
- Choose a new password: the old line "Choose a new password." becomes the `h1`, the same words.
- The reset form: a blank submit, possible only with the browser's `required` check bypassed, now says Django's "This field is required." under the field. The old page said "Enter a valid email address." for every error. A bad address still says that, because it is Django's own sentence.
- The choose-a-password form: each error now sits under the field it belongs to. Django puts the password validators' and the mismatch sentences on the confirm field. The old page listed all of them in one box.
- Verify email: the address in "We sent a verification link to ..." is plain text. The old page set it in a monospace span, and Task 1 has no class for that.

**The one rule for actions.** A page with a form has the form's button as its one main action (`.action`). A page without a form has its next step as the main action: "Go to your dashboard", "Log in", "Request a new one", "Back to dashboard" once verified, "Go to dashboard", "Request a new link". The exception is the reset-requested page. Its only link, "Back to log in", is a quiet label, since the visitor is waiting for mail. On the verify page with a form, "Resend verification link" and "Back to dashboard" are quiet labels. Every main action is exactly `class="action interactive"` (`interactive` is what gives it the ink wash and the press fill), and a test holds it to that.

Five facts the steps rely on (all checked in the code):
1. **Django puts the new-password errors on `new_password2`.** In Django 6.1, `SetPasswordMixin.validate_passwords` (the mismatch) and `validate_password_for_user` (the validators) both call `add_error` on the confirm field. So `errors=form.new_password2.errors` carries them, and `new_password1` only ever carries "This field is required."
2. **`{% csrf_token %}` in a component included with `only` renders nothing.** Each page therefore writes its own `{% csrf_token %}` inside its `<form>` and passes nothing about it to a component.
3. **The reset link's form posts to a different address than the link.** `PasswordResetConfirmView` redirects the first GET to `/accounts/reset/<uid>/set-password/` and keeps the token in the session. The form posts to the address it was served from, so no form here has an `action` attribute (as before; `?sent=1` and `?next=` ride along the same way).
4. **A page outside the registry has no `shell_page`.** The pages carry no `data-layout`, make no in-place changes, and take their `<title>` and robots meta from the blocks.
5. **The shell renders with no request.** `shell_menu` reads `request` with `context.get`, so `render_to_string("registration/verify_email.html", {"verified": True, "sent": False})` still works. A test pins it.

- [ ] **Step 1: Check what this task builds on**

Run: `cd backend && ls templates/layouts/form.html templates/components/field.html templates/components/form_errors.html static/css/components/field.css static/css/components/form_errors.css && grep -nE "^\.form-page \{|^\.form-page :where\(h1\)|^\.action\.interactive|^\.form-links \{|^\.notice \{" static/css/base.css && sed -n '/^\.form-page {/,/^}/p' static/css/base.css | grep -c "overflow-wrap: anywhere"`
Expected: the five paths with no error, then five lines of CSS naming `.form-page`, `.form-page :where(h1)`, `.action.interactive`, `.form-links` and `.notice`, then `1`. If a path or a rule is missing, stop: Task 1 has not landed. The `1` says the column lets long unbroken text wrap, which a 254-character address in a sentence on a 320 px phone needs. These templates write the title and the paragraphs bare (`<h1>`, `<p>`) and give them no classes; if the `.form-page` rules do not style them, fix Task 1's block in `base.css`, not these pages.

- [ ] **Step 2: Write the failing tests**

Create `backend/drop_core/tests_reset_verify_pages.py`. Every class sets `longMessage = False` (Step 3 lists the module where `pages.tests_site_rules` holds it to that): a failed `assertIn` on a whole page would otherwise print the page before the message that names the file and the fix.

```python
"""The password reset and email verification pages on the form layout (spec 4.7).

Seven templates in backend/templates/registration/ (password_reset_form, password_reset_done, password_reset_confirm,
password_reset_complete, verify_email, verify_email_done, verify_email_invalid), seventeen states of them. Every state
is rendered by its real view and held to the same rules: a shell page on the form layout with one h1 and a title that
names it, a labelled input for every field, errors tied to their field, a CSRF token in every form, and the words, links and
field names the other tests and the visitors rely on. The mail templates (password_reset_email.html, verify_email_email.txt)
are not pages and are not under these rules. Two small classes at the end keep guards that the retired old-markup tests
held for the whole site (template comments in the blog and docs pages, the keyboard focus ring).

Every class sets longMessage = False (pages.tests_site_rules holds this module to it). A failed assertIn on a whole page
would otherwise print the page before the message that names the file and the fix; with it off only the message prints,
so each message here also says what was found.
"""
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from blog.loader import load_posts
from drop_core.email_verification import make_verification_token
from drop_core.forms import EmailUpdateForm
from drop_core.models import EmailVerification

User = get_user_model()
PASSWORD = "pw12345678"
NEW_PASSWORD = "a-new-pw-98765432"
WRONG_PASSWORD = "wrong-pw-24681357"
FILES = "backend/templates/registration/"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

RESET_FORM = ("reset form", "reset form, bad email", "reset form, empty email")
CONFIRM_FORM = ("reset confirm", "reset confirm, mismatch")
VERIFY_PENDING = ("verify, pending", "verify, link sent", "verify, wrong password", "verify, address taken")

# state -> (template, the page's name). The title is "<name> | Veilfile", as every page of the new site has, and the name is the h1.
PAGES = {
    **{state: ("password_reset_form.html", "Reset password") for state in RESET_FORM},
    "reset done": ("password_reset_done.html", "Check your inbox"),
    **{state: ("password_reset_confirm.html", "Choose a new password") for state in (*CONFIRM_FORM, "reset confirm, dead link")},
    **{state: ("password_reset_complete.html", "Password updated") for state in ("reset complete, signed in", "reset complete, signed out")},
    **{state: ("verify_email.html", "Verify email") for state in ("verify, no address yet", *VERIFY_PENDING, "verify, verified")},
    "verify link works": ("verify_email_done.html", "Email verified"),
    "verify link dead": ("verify_email_invalid.html", "Link expired"),
}

# The inputs of each state that has any, in document order, without the CSRF token: (name, type, value of a hidden one).
RESET_INPUTS = [("email", "email", None)]
CONFIRM_INPUTS = [("new_password1", "password", None), ("new_password2", "password", None)]
VERIFY_FORM = [("action", "hidden", "update_email"), ("email", "email", None), ("password", "password", None)]
INPUTS = {
    **{state: RESET_INPUTS for state in RESET_FORM},
    **{state: CONFIRM_INPUTS for state in CONFIRM_FORM},
    "verify, no address yet": VERIFY_FORM,
    **{state: VERIFY_FORM + [("action", "hidden", "resend")] for state in VERIFY_PENDING},
}
TOKENS = {"id_email": "email", "id_new_password1": "new-password", "id_new_password2": "new-password", "id_password": "current-password"}
LABELS = {"id_email": "Email", "id_new_password1": "New password", "id_new_password2": "Confirm new password",
          "id_password": "Current password"}
AUTOFOCUS = {**{state: "id_email" for state in RESET_FORM}, **{state: "id_new_password1" for state in CONFIRM_FORM}}
ERRORS = {  # state -> {input id: the sentence tied to it}
    "reset form, bad email": {"id_email": "Enter a valid email address."},
    "reset form, empty email": {"id_email": "This field is required."},
    "reset confirm, mismatch": {"id_new_password2": "The two password fields didn’t match."},
    "verify, wrong password": {"id_password": "Your current password is incorrect."},
    "verify, address taken": {"id_email": "This email address can't be used. Try another."},
}
# The one hairline-box action of each state that has one. Every other control on the page is a quiet label.
MAIN_ACTION = {
    **{state: "Send reset link" for state in RESET_FORM},
    **{state: "Set new password" for state in CONFIRM_FORM},
    "reset confirm, dead link": "Request a new one",
    "reset complete, signed in": "Go to your dashboard",
    "reset complete, signed out": "Log in",
    "verify, no address yet": "Send verification link",
    **{state: "Update and resend link" for state in VERIFY_PENDING},
    "verify, verified": "Back to dashboard",
    "verify link works": "Go to dashboard",
    "verify link dead": "Request a new link",
}
BUTTONS = {  # the buttons inside <main>
    **{state: ["Send reset link"] for state in RESET_FORM},
    **{state: ["Set new password"] for state in CONFIRM_FORM},
    "verify, no address yet": ["Send verification link"],
    **{state: ["Update and resend link", "Resend verification link"] for state in VERIFY_PENDING},
}
LINKS = {  # the links inside <main>: (text, href)
    **{state: [("Back to log in", "/accounts/login/")] for state in (*RESET_FORM, "reset done")},
    "reset confirm, dead link": [("Request a new one", "/accounts/password_reset/")],
    "reset complete, signed in": [("Go to your dashboard", "/app/")],
    "reset complete, signed out": [("Log in", "/accounts/login/")],
    **{state: [("Back to dashboard", "/app/")] for state in ("verify, no address yet", *VERIFY_PENDING, "verify, verified")},
    "verify link works": [("Go to dashboard", "/app/")],
    "verify link dead": [("Request a new link", "/accounts/verify-email/")],
}
WORDS = {  # the sentences each state says, word for word
    "reset form": ["Enter the email on your account. We will send a reset link."],
    "reset done": ["If an account exists for that email, a reset link is on its way. It expires in 3 days."],
    "reset confirm, dead link": ["This reset link is invalid or has expired."],
    "reset complete, signed in": [
        "Your password is set, and you are signed in. Other sessions were signed out and connected apps were disconnected."],
    "reset complete, signed out": ["Your password is set. Log in with the new one."],
    "verify, no address yet": [
        "Add an email address to your account. We will send a verification link; API key creation stays locked until you verify."],
    "verify, pending": ["We sent a verification link to pending-verify@example.com. Click it to verify. "
                        "API key creation stays locked until you do."],
    "verify, link sent": ["Verification link sent. Check your inbox."],
    "verify, verified": ["Your email address is verified."],
    "verify link works": ["Your email address is verified. You can now create API keys."],
    "verify link dead": ["This verification link is invalid or has expired."],
}


class Tree(HTMLParser):
    """Every element of a page in document order, with its attributes, its ancestors and all the text inside it."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.elements = [], []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""}
        self.elements.append(element)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""})

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element["text"] += data

    def all(self, tag):
        return [element for element in self.elements if element["tag"] == tag]

    def ids(self):
        return {element["attrs"]["id"]: element for element in self.elements if "id" in element["attrs"]}

    def announced(self, role):
        """The words of each element of <main> with this role."""
        return [words(e) for e in self.elements if in_main(e) and e["attrs"].get("role") == role]


def words(element):
    """The text inside an element with its white space made single, as a reader sees it."""
    return " ".join(element["text"].split())


def inside(element, ancestor):
    return any(parent is ancestor for parent in element["parents"])


def in_main(element):
    return any(parent["tag"] == "main" for parent in element["parents"])


def visible_inputs(tree):
    return [e for e in tree.all("input") if e["attrs"].get("type") != "hidden"]


def sign_in(user):
    client = Client()
    client.force_login(user)
    return client


class PageTestCase(TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        cache.clear()  # the throttle's counters live in the cache
        self.addCleanup(cache.clear)
        throttle_logger = logging.getLogger("drop_core.throttle")
        self.addCleanup(throttle_logger.setLevel, throttle_logger.level)
        throttle_logger.setLevel(logging.ERROR)


class EveryStateTests(PageTestCase):
    """Each of the seventeen states, rendered by its real view."""

    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.html, cls.status = {}, {}

        def keep(state, response):
            cls.html[state] = response.content.decode()
            cls.status[state] = response.status_code

        # Password reset. The link is made as the view mails it; following it stores the token in the session.
        resetter = User.objects.create_user(username="resetpages", email="resetpages@example.com")
        anonymous = Client()
        keep("reset form", anonymous.get("/accounts/password_reset/"))
        keep("reset form, bad email", anonymous.post("/accounts/password_reset/", {"email": "not-an-email"}))
        keep("reset form, empty email", anonymous.post("/accounts/password_reset/", {"email": ""}))
        keep("reset done", anonymous.get("/accounts/password_reset/done/"))
        link = "/accounts/reset/{}/{}/".format(
            urlsafe_base64_encode(force_bytes(resetter.pk)), default_token_generator.make_token(resetter))
        first = anonymous.get(link, follow=True)
        keep("reset confirm", first)
        set_password = first.redirect_chain[-1][0]
        keep("reset confirm, mismatch", anonymous.post(
            set_password, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD + "-other"}))
        keep("reset confirm, dead link", Client().get("/accounts/reset/NA/bogus-token/", follow=True))
        keep("reset complete, signed in", anonymous.post(
            set_password, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}, follow=True))
        keep("reset complete, signed out", Client().get("/accounts/reset/done/"))

        # Email verification.
        legacy = User.objects.create_user(username="legacy-verify")  # no address and no record
        keep("verify, no address yet", sign_in(legacy).get("/accounts/verify-email/"))
        pending = User.objects.create_user(username="pending-verify", email="pending-verify@example.com", password=PASSWORD)
        EmailVerification.objects.create(user=pending, email=pending.email)
        owner = sign_in(pending)
        keep("verify, pending", owner.get("/accounts/verify-email/"))
        keep("verify, link sent", owner.get("/accounts/verify-email/?sent=1"))
        keep("verify, wrong password", owner.post("/accounts/verify-email/", {
            "action": "update_email", "email": "newer@example.com", "password": WRONG_PASSWORD}))
        User.objects.create_user(username="holds-address", email="held@example.com")
        keep("verify, address taken", owner.post("/accounts/verify-email/", {
            "action": "update_email", "email": "held@example.com", "password": PASSWORD}))
        done = User.objects.create_user(username="done-verify", email="done-verify@example.com")
        EmailVerification.objects.create(user=done, email=done.email, is_verified=True, verified_at=timezone.now())
        keep("verify, verified", sign_in(done).get("/accounts/verify-email/"))
        linked = User.objects.create_user(username="link-verify", email="link-verify@example.com")
        EmailVerification.objects.create(user=linked, email=linked.email)
        keep("verify link works", Client().get(f"/accounts/verify-email/{make_verification_token(linked, linked.email)}/"))
        keep("verify link dead", Client().get("/accounts/verify-email/bogus-token/"))
        cache.clear()

    def states(self):
        """(state, the template's path for messages, the page's name, its HTML, its parsed tree) for each state."""
        return [(state, FILES + template, name, self.html[state], Tree(self.html[state]))
                for state, (template, name) in PAGES.items()]

    def test_every_state_answers_200(self):
        for state, status in self.status.items():
            with self.subTest(state=state):
                self.assertEqual(status, 200, f"{FILES}{PAGES[state][0]}: the '{state}' state answers {status}, not 200; "
                                              "a page keeps its view's status code")

    def test_every_state_is_a_page_of_the_shell_on_the_form_layout(self):
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                self.assertIn('<a class="skip-link label" href="#main">', page,
                              f"{where}: extend layouts/form.html, which extends shell.html; the shell brings the skip link")
                self.assertRegex(page, r'<main id="main" class="shell-main">\s*<div class="form-page">',
                                 f"{where}: the page's words go in {{% block form_page %}} of layouts/form.html")
                self.assertRegex(page, r'<body class="[^"]*\blayout-form\b', f"{where}: the form layout gives the body the class layout-form")
                self.assertNotIn("data-layout", page, f"{where}: a form page has no registry layout, so it makes no in-place changes")

    def test_no_old_chrome_is_left_on_any_state(self):
        old = ('<style', 'id="veilfile-theme-boot"', 'site-menu-wrap', 'class="ambient"', 'class="brand"', 'class="card"',
               'class="errors"', 'class="ok"', 'class="foot"', 'class="sub"', 'class="skip-link"', '{#', '#}')
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                for text in old:
                    self.assertNotIn(text, page, f"{where}: found {text!r}, which belongs to the old standalone pages. "
                                                 "Fix: extend layouts/form.html and use components/field.html and form_errors.html")

    def test_one_h1_inside_main_named_as_the_title_and_nothing_main_may_not_hold(self):
        for state, where, name, _page, tree in self.states():
            with self.subTest(state=state):
                h1s = tree.all("h1")
                self.assertEqual(len(h1s), 1, f"{where}: a page has exactly one h1, found {len(h1s)}")
                self.assertTrue(in_main(h1s[0]), f"{where}: the h1 belongs inside <main>")
                self.assertEqual(words(h1s[0]), name, f"{where}: the h1 is the page's name, {name!r}")
                for element in tree.elements:
                    if element["tag"] in ("header", "footer", "script"):
                        self.assertFalse(in_main(element), f"{where}: no <{element['tag']}> inside <main>")

    def test_every_state_is_titled_with_its_name(self):
        for state, where, name, page, _tree in self.states():
            with self.subTest(state=state):
                found = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
                self.assertEqual(found, f"{name} | Veilfile", f"{where}: the title is {name!r} and ' | Veilfile' (no em dash: the voice rules), found {found!r}")

    def test_every_page_but_the_reset_form_is_kept_out_of_search(self):
        noindex = '<meta name="robots" content="noindex">'
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                if state.startswith("reset form"):
                    self.assertNotIn(noindex, page, f"{where}: the reset form is a plain public form and takes no noindex")
                else:
                    self.assertIn(noindex, page, f"{where}: the page holds a token, an address or a one-time state; "
                                                 "add {% block meta %}" + noindex + "{% endblock %}")

    def test_every_form_posts_to_its_own_address_with_its_own_csrf_token(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                forms = tree.all("form")
                wanted = 2 if state in VERIFY_PENDING else (1 if state in INPUTS else 0)
                self.assertEqual(len(forms), wanted, f"{where}: this state has {wanted} forms, found {len(forms)}")
                for form in forms:
                    self.assertEqual(form["attrs"].get("method"), "post", f"{where}: a form that changes state posts")
                    self.assertNotIn("action", form["attrs"],
                                     f"{where}: no action attribute; the form posts to the address it came from, "
                                     "which for the reset link is /accounts/reset/<uid>/set-password/")
                    tokens = [e for e in tree.all("input") if inside(e, form) and e["attrs"].get("name") == "csrfmiddlewaretoken"]
                    self.assertEqual(len(tokens), 1, f"{where}: every POST form holds {{% csrf_token %}} itself, "
                                                     f"never through a component; found {len(tokens)}")

    def test_ids_are_unique_on_every_state(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                seen = [e["attrs"]["id"] for e in tree.elements if "id" in e["attrs"]]
                repeated = sorted({name for name in seen if seen.count(name) > 1})
                self.assertEqual(repeated, [], f"{where}: these ids are used twice: {repeated}. A label's `for`, aria-describedby and the "
                                               "shell's own ids need one element each")

    def test_the_fields_keep_their_names_types_and_hidden_values(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                found = [(e["attrs"].get("name"), e["attrs"].get("type"),
                          e["attrs"].get("value") if e["attrs"].get("type") == "hidden" else None)
                         for e in tree.all("input") if e["attrs"].get("name") != "csrfmiddlewaretoken"]
                self.assertEqual(found, INPUTS.get(state, []),
                                 f"{where}: the views read these field names and these hidden values; found {found}")

    def test_every_input_has_one_visible_label_with_its_words(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for field in visible_inputs(tree):
                    name = field["attrs"].get("id")
                    labels = [label for label in tree.all("label") if label["attrs"].get("for") == name]
                    self.assertEqual(len(labels), 1, f"{where}: the input {name!r} needs exactly one <label for>, found {len(labels)}")
                    self.assertTrue(words(labels[0]).startswith(LABELS[name]),
                                    f"{where}: the label of {name} reads {LABELS[name]!r}, found {words(labels[0])!r}")

    def test_autocomplete_tokens_are_kept(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for field in visible_inputs(tree):
                    name = field["attrs"].get("id")
                    self.assertEqual(field["attrs"].get("autocomplete"), TOKENS[name],
                                     f"{where}: {name} keeps autocomplete={TOKENS[name]!r} (password managers, and the tests count them)")

    def test_autofocus_only_where_the_page_exists_to_take_one_input(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                focused = [e["attrs"].get("id") for e in tree.elements if "autofocus" in e["attrs"]]
                self.assertEqual(focused, [AUTOFOCUS[state]] if state in AUTOFOCUS else [],
                                 f"{where}: autofocus belongs on the first input of the reset form and of the new-password form only; "
                                 f"found {focused}")

    def test_a_field_error_is_tied_to_its_field_and_a_clean_field_is_not_marked(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                ids = tree.ids()
                for element in tree.elements:
                    for token in element["attrs"].get("aria-describedby", "").split():
                        self.assertIn(token, ids, f"{where}: aria-describedby names {token!r}, which is no id on the page")
                invalid = {e["attrs"].get("id"): e for e in visible_inputs(tree) if e["attrs"].get("aria-invalid") == "true"}
                wanted = ERRORS.get(state, {})
                self.assertEqual(set(invalid), set(wanted),
                                 f"{where}: aria-invalid=\"true\" belongs on the fields with an error ({sorted(wanted)}), found {sorted(invalid)}")
                for name, sentence in wanted.items():
                    described = " ".join(words(ids[token]) for token in invalid[name]["attrs"].get("aria-describedby", "").split())
                    self.assertIn(sentence, described,
                                  f"{where}: the error under {name} is {sentence!r} and aria-describedby must point at it; it points at {described!r}")

    def test_a_typed_password_is_never_sent_back(self):
        for state, typed in (("reset confirm, mismatch", (NEW_PASSWORD, NEW_PASSWORD + "-other")), ("verify, wrong password", (WRONG_PASSWORD,))):
            for secret in typed:
                with self.subTest(state=state):
                    self.assertNotIn(secret, self.html[state], f"{FILES}{PAGES[state][0]}: a password field never gets a value attribute")

    def test_the_pages_say_what_they_said_before(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                text = words(tree.all("main")[0])
                for sentence in WORDS.get(state, []):
                    self.assertIn(sentence, text, f"{where}: keep the sentence {sentence!r}")

    def test_the_buttons_and_links_keep_their_words_and_addresses(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                buttons = [words(e) for e in tree.all("button") if in_main(e)]
                self.assertEqual(buttons, BUTTONS.get(state, []), f"{where}: the buttons in <main> read {buttons}")
                links = [(words(e), e["attrs"].get("href")) for e in tree.all("a") if in_main(e)]
                self.assertEqual(links, LINKS.get(state, []), f"{where}: the links in <main> are {links}")

    def test_each_page_has_at_most_one_main_action_and_every_other_control_is_a_quiet_label(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                actions = [e for e in tree.elements if in_main(e) and "action" in e["attrs"].get("class", "").split()]
                found = [words(e) for e in actions]
                wanted = [MAIN_ACTION[state]] if state in MAIN_ACTION else []
                self.assertEqual(found, wanted,
                                 f"{where}: the page's one main action is the hairline box (class=\"action interactive\"); "
                                 f"every other link and button, such as the Resend button, is a plain `label interactive`. Found {found}, wanted {wanted}")
                for action in actions:
                    classes = action["attrs"].get("class", "").split()
                    self.assertEqual(classes, ["action", "interactive"],
                                     f"{where}: the main action {words(action)!r} is exactly class=\"action interactive\" (`interactive` brings the ink wash "
                                     f"and the press fill; a `label` class would add a bare label's padding and negative margin); found {classes}")

    def test_every_link_and_button_takes_the_ink_wash(self):
        # Spec 3.4: every interactive element gets the effect, form buttons included. The cloth inks `.interactive`.
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for control in (e for e in tree.elements if in_main(e) and e["tag"] in ("a", "button")):
                    self.assertIn("interactive", control["attrs"].get("class", "").split(),
                                  f"{where}: the {words(control)!r} control needs class `interactive`, which the fill and the ink wash answer to")

    def test_a_dead_link_shows_nothing_of_the_link(self):
        for state in ("reset confirm, dead link", "verify link dead"):
            with self.subTest(state=state):
                self.assertNotIn("bogus-token", self.html[state], f"{FILES}{PAGES[state][0]}: never print the token from the address")

    def test_the_status_lines_are_announced(self):
        cases = {"verify, link sent": ["Verification link sent. Check your inbox."], "verify, verified": ["Your email address is verified."],
                 "verify, pending": [], "verify, no address yet": [], "verify, wrong password": [], "verify, address taken": []}
        for state, wanted in cases.items():
            with self.subTest(state=state):
                found = Tree(self.html[state]).announced("status")
                self.assertEqual(found, wanted, f"{FILES}verify_email.html: a notice is a role=\"status\" line, and only when the link was just "
                                                f"sent or the address is verified; found {found}")

    def test_the_verified_page_has_no_form_and_the_others_no_verified_notice(self):
        self.assertEqual(Tree(self.html["verify, verified"]).all("form"), [], f"{FILES}verify_email.html: a verified address has nothing to submit")
        for state in ("verify, no address yet", "verify, pending", "verify, wrong password", "verify, address taken"):
            with self.subTest(state=state):
                self.assertNotIn("Your email address is verified.", self.html[state],
                                 f"{FILES}verify_email.html: the verified notice is only for a verified address")


class FormErrorsTests(PageTestCase):
    """A form's own errors (not a field's) show in an alert on every page that has a form, so none is dropped."""

    def test_a_non_field_error_is_shown_in_an_alert_on_each_form(self):
        sentence = "This is an error of the form itself."
        owner = User.objects.create_user(username="formerrors", email="formerrors@example.com", password=PASSWORD)
        cases = (
            ("password_reset_form.html", PasswordResetForm({"email": "formerrors@example.com"}), {}),
            ("password_reset_confirm.html", SetPasswordForm(owner, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}),
             {"validlink": True}),
            ("verify_email.html", EmailUpdateForm(owner, {"email": "formerrors2@example.com", "password": PASSWORD}),
             {"verified": False, "sent": False, "record": None}),
        )
        for template, form, extra in cases:
            with self.subTest(template=template):
                self.assertTrue(form.is_valid())
                form.add_error(None, sentence)
                request = RequestFactory().get("/")
                request.user = AnonymousUser()
                page = render_to_string("registration/" + template, {"form": form, **extra}, request=request)
                alerts = Tree(page).announced("alert")
                self.assertEqual(len([a for a in alerts if sentence in a]), 1,
                                 f"{FILES}{template}: show {{% include \"components/form_errors.html\" with errors=form.non_field_errors only %}} "
                                 f"above the form; the alerts found are {alerts}")


class ResetRequestTests(PageTestCase):
    def test_the_done_page_is_the_same_for_a_known_and_an_unknown_address(self):
        # The reset form answers a request for an address with no account exactly as it answers one with an account.
        User.objects.create_user(username="known", email="known@example.com")
        known = Client().post("/accounts/password_reset/", {"email": "known@example.com"}, follow=True)
        unknown = Client().post("/accounts/password_reset/", {"email": "unknown@example.com"}, follow=True)
        self.assertEqual((known.status_code, unknown.status_code), (200, 200))
        self.assertEqual(known.content, unknown.content,
                         f"{FILES}password_reset_done.html: show nothing that depends on the address or on whether it has an account "
                         "(no address, no token, no per-visitor value)")


class ResetConfirmTests(PageTestCase):
    def test_a_validator_error_lands_under_the_confirm_field(self):
        user = User.objects.create_user(username="shortpw", email="shortpw@example.com")
        link = "/accounts/reset/{}/{}/".format(urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user))
        set_password = self.client.get(link, follow=True).redirect_chain[-1][0]
        page = self.client.post(set_password, {"new_password1": "short1", "new_password2": "short1"}).content.decode()
        ids = Tree(page).ids()
        confirm = ids["id_new_password2"]
        self.assertEqual(confirm["attrs"].get("aria-invalid"), "true",
                         f"{FILES}password_reset_confirm.html: Django attaches the password validators' errors to new_password2; "
                         "pass errors=form.new_password2.errors to its field")
        described = " ".join(words(ids[token]) for token in confirm["attrs"].get("aria-describedby", "").split())
        self.assertIn("too short", described, f"{FILES}password_reset_confirm.html: the validators' sentence is shown under the field")
        self.assertNotIn("short1", page, f"{FILES}password_reset_confirm.html: a password is never sent back")


class VerifyEmailTests(PageTestCase):
    def test_it_renders_with_no_request(self):
        # The one render with no request: the shell and the form layout must not need one.
        page = render_to_string("registration/verify_email.html", {"verified": True, "sent": False})
        found = [words(e) for e in Tree(page).elements if e["attrs"].get("role") == "status"]
        self.assertEqual(found, ["Your email address is verified."],
                         f"{FILES}verify_email.html: the notice is a role=\"status\" line even with no request; found {found}")

    def test_the_address_is_escaped_in_the_sentence_and_in_the_field(self):
        user = User.objects.create_user(username="escaper", email="escaper@example.com")
        EmailVerification.objects.create(user=user, email='x&y"<b>@example.com')
        page = sign_in(user).get("/accounts/verify-email/").content.decode()
        self.assertNotIn("<b>@", page, f"{FILES}verify_email.html: the address is printed escaped, never as markup")
        self.assertGreaterEqual(page.count("x&amp;y&quot;&lt;b&gt;@example.com"), 2,
                                f"{FILES}verify_email.html: the escaped address is in the sentence and in the email field's value")

    def test_a_very_long_address_is_shown_whole(self):
        address = "l" * 64 + "@" + ".".join(["d" * 63, "d" * 63, "d" * 57]) + ".com"
        self.assertEqual(len(address), 254)
        user = User.objects.create_user(username="longaddress", email="longaddress@example.com")
        EmailVerification.objects.create(user=user, email=address)
        tree = Tree(sign_in(user).get("/accounts/verify-email/").content.decode())
        self.assertEqual(tree.ids()["id_email"]["attrs"].get("value"), address, f"{FILES}verify_email.html: the field holds the whole address")
        self.assertIn(f"We sent a verification link to {address}.", words(tree.all("main")[0]),
                      f"{FILES}verify_email.html: the sentence names the whole address")

    def test_nothing_is_offered_to_resend_before_an_address_is_on_record(self):
        legacy = User.objects.create_user(username="norecord")
        page = sign_in(legacy).get("/accounts/verify-email/").content.decode()
        self.assertNotIn('value="resend"', page, f"{FILES}verify_email.html: the resend form is only for an address on record")


class LinkPageTests(PageTestCase):
    def test_a_dead_verification_link_says_the_same_whatever_the_reason(self):
        user = User.objects.create_user(username="deadlinks", email="deadlinks@example.com")
        EmailVerification.objects.create(user=user, email=user.email)
        token = make_verification_token(user, user.email)
        tampered = f"{token[:-2]}xx"
        bodies = {"unknown": self.client.get("/accounts/verify-email/bogus-token/").content,
                  "tampered": self.client.get(f"/accounts/verify-email/{tampered}/").content}
        with mock.patch("drop_core.email_verification.TOKEN_MAX_AGE", -1):
            bodies["expired"] = self.client.get(f"/accounts/verify-email/{token}/").content
        self.assertEqual(len(set(bodies.values())), 1,
                         f"{FILES}verify_email_invalid.html: the view passes `reason`; the page must not use it, so a visitor "
                         f"cannot tell why a link failed. These reasons gave different pages: {sorted(bodies)}")
        self.assertNotIn(tampered.encode(), bodies["tampered"], f"{FILES}verify_email_invalid.html: never print the token")


class NoTemplateCommentLeakTests(PageTestCase):
    def test_no_django_comment_remnants_in_rendered_pages(self):
        # Moved here from the old ambient-layer tests, which were retired with the old pages' markup. Django 6.1 strips
        # single-line {# #} comments but shows multi-line ones as visible text. The seventeen states above are held to it by
        # test_no_old_chrome_is_left_on_any_state; this holds the blog and the docs, which no other test reads for it.
        urls = ["/blog/", f"/blog/{load_posts()[0].slug}/", "/docs/quickstart/"]
        for url in urls:
            with self.subTest(url=url):
                page = self.client.get(url).content.decode()
                for marker in ("{#", "#}"):
                    self.assertNotIn(marker, page, f"{url}: a template comment shows in the page; Django 6.1 shows a multi-line "
                                                   "{# #} comment as text, so write each comment on one line in the template that draws it")


class FocusRingTests(PageTestCase):
    def test_the_keyboard_focus_ring_is_drawn_and_no_stylesheet_takes_it_away(self):
        # Moved here from drop_core/tests.py (AccessibilityTests), where it read an inline rule of an old page. The pages link
        # their stylesheets now: one global rule draws the ring, and no rule of the site may remove an outline (WCAG 2.4.7).
        css = Path(__file__).resolve().parent.parent / "static" / "css"
        self.assertRegex((css / "base.css").read_text(encoding="utf-8"), r"(?m)^:focus-visible \{\s*outline: ",
                         "backend/static/css/base.css: keep the one global :focus-visible rule that draws the keyboard focus ring")
        for sheet in sorted(css.rglob("*.css")):
            with self.subTest(sheet=sheet.name):
                self.assertNotIn("outline: none", sheet.read_text(encoding="utf-8"),
                                 f"backend/static/css/{sheet.relative_to(css).as_posix()}: do not remove an outline; "
                                 "a focused control keeps its ring")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_reset_verify_pages`
Expected: `Ran 31 tests` and `FAILED (failures=120)` (the runner counts subtests: once for each of the seventeen states in the tests that loop over them). Ten of the 31 tests fail; the other 21 pass already and must keep passing, because they pin what these pages must not lose: the status codes, field names, labels, autocomplete tokens, words, links and buttons, the escape of the address, the same page for a dead link whatever the reason, the same done page for a known and an unknown address, and the two guards moved from the retired tests (no template comment in the blog and docs pages, and the focus ring in `base.css`).
The ten, each with a message that names `backend/templates/registration/<file>` and the fix (for example `backend/templates/registration/password_reset_form.html: extend layouts/form.html, which extends shell.html; the shell brings the skip link`):
- 17 each: `test_every_state_is_a_page_of_the_shell_on_the_form_layout` (no shell skip link), `test_no_old_chrome_is_left_on_any_state` (finds `<style`), `test_one_h1_inside_main_named_as_the_title_and_nothing_main_may_not_hold` (found 0 h1), `test_every_state_is_titled_with_its_name` (the title still has the em dash) and `test_every_link_and_button_takes_the_ink_wash` (no `interactive` class on a control).
- 16: `test_each_page_has_at_most_one_main_action_and_every_other_control_is_a_quiet_label` (every state but the reset-requested page).
- 14: `test_every_page_but_the_reset_form_is_kept_out_of_search` (no `noindex`).
- 2: `test_a_field_error_is_tied_to_its_field_and_a_clean_field_is_not_marked` (the empty reset form and the mismatch carry no `aria-invalid`).
- 2: `test_a_non_field_error_is_shown_in_an_alert_on_each_form` (the reset form and the verify page do not show it as it is).
- 1: `test_a_validator_error_lands_under_the_confirm_field`.

- [ ] **Step 3: Hold the new module to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a test class whose `longMessage` is not `False`, and its message names the file and the fix. Earlier tasks have each added their own module to `READING_TEST_MODULES`; this one adds its too, after Task 2's. In `backend/pages/tests_site_rules.py`, replace this exact text (it occurs once, in the tuple):

```python
"drop_core.tests_sign_in_pages"
```

with:

```python
"drop_core.tests_sign_in_pages", "drop_core.tests_reset_verify_pages"
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules`
Expected: `OK`.

- [ ] **Step 4: The password reset pages**

Replace the whole of `backend/templates/registration/password_reset_form.html` (the old standalone document, 50 lines, with its inline style and the three old includes) with:

```django
{% extends "layouts/form.html" %}
{# Reset password: ask for the account's email and mail it a reset link (spec 4.7). #}
{% block title %}Reset password | Veilfile{% endblock %}
{% block form_page %}
<h1>Reset password</h1>
<p>Enter the email on your account. We will send a reset link.</p>
{% include "components/form_errors.html" with errors=form.non_field_errors only %}
<form method="post">
{% csrf_token %}
{% include "components/field.html" with id="id_email" name="email" label="Email" type="email" autocomplete="email" required=True autofocus=True errors=form.email.errors only %}
<button class="action interactive" type="submit">Send reset link</button>
</form>
<p class="form-links"><a class="label interactive" href="{% url 'login' %}">Back to log in</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/password_reset_done.html` (35 lines) with:

```django
{% extends "layouts/form.html" %}
{# Reset requested: the same page whether or not the address has an account (spec 4.7). #}
{% block title %}Check your inbox | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Check your inbox</h1>
<p>If an account exists for that email, a reset link is on its way. It expires in 3 days.</p>
<p class="form-links"><a class="label interactive" href="{% url 'login' %}">Back to log in</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/password_reset_confirm.html` (62 lines) with:

```django
{% extends "layouts/form.html" %}
{# Choose a new password from a reset link, or say the link does not work (spec 4.7). #}
{% block title %}Choose a new password | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Choose a new password</h1>
{% if validlink %}
{% include "components/form_errors.html" with errors=form.non_field_errors only %}
<form method="post">
{% csrf_token %}
{% include "components/field.html" with id="id_new_password1" name="new_password1" label="New password" type="password" autocomplete="new-password" required=True autofocus=True errors=form.new_password1.errors only %}
{% include "components/field.html" with id="id_new_password2" name="new_password2" label="Confirm new password" type="password" autocomplete="new-password" required=True errors=form.new_password2.errors only %}
<button class="action interactive" type="submit">Set new password</button>
</form>
{% else %}
<p>This reset link is invalid or has expired.</p>
<p><a class="action interactive" href="{% url 'password_reset' %}">Request a new one</a></p>
{% endif %}
{% endblock %}
```

(The two fields keep `autocomplete="new-password"`: `drop_core.tests_fix_auth` counts exactly two on this page, so no other element may carry that token. `autofocus` stays on the first field only. The dead-link state shows nothing of the address it was reached by. The old page's title was the same in both states, and so is this one.)

Replace the whole of `backend/templates/registration/password_reset_complete.html` (40 lines) with:

```django
{% extends "layouts/form.html" %}
{# The password is set: signed in when the reset signed the owner in, else a way to log in (spec 4.7). #}
{% block title %}Password updated | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Password updated</h1>
{% if user.is_authenticated %}
<p>Your password is set, and you are signed in. Other sessions were signed out and connected apps were disconnected.</p>
<p><a class="action interactive" href="/app/">Go to your dashboard</a></p>
{% else %}
<p>Your password is set. Log in with the new one.</p>
<p><a class="action interactive" href="{% url 'login' %}">Log in</a></p>
{% endif %}
{% endblock %}
```

(Each sentence stays on one line: `tests_fix_auth` matches "you are signed in" and "Log in with the new one" in the raw HTML.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_reset_verify_pages drop_core.tests.PasswordResetTests drop_core.tests_fix_auth.ResetSignsInPastLockTests drop_core.tests_fix_auth.AccountFormAccessibilityTests drop_core.tests_fix_auth.PasswordResetThrottleTests`
Expected: `Ran 46 tests` and `FAILED (failures=57)`, and every failure is for a `verify` state, whose pages are still the old ones: 8 states in each of seven tests (the shell page, no old chrome, the h1, the title, the ink wash, the main action, the noindex; 56) and the verify page in `test_a_non_field_error_is_shown_in_an_alert_on_each_form` (1). Nothing else fails, so the existing reset tests already pass on the new pages: they match "Reset password" and "Choose a new password" through the `h1`, "invalid or has expired", "you are signed in" and "Log in with the new one" in the words, and the autocomplete tokens in the fields.

- [ ] **Step 5: The email verification pages**

Replace the whole of `backend/templates/registration/verify_email.html` (77 lines, the old standalone document) with:

```django
{% extends "layouts/form.html" %}
{# Verify email: the address on the account, in its two states, verified or not (spec 4.7). #}
{% block title %}Verify email | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Verify email</h1>
{% if verified %}
<p class="notice" role="status">Your email address is verified.</p>
<p><a class="action interactive" href="/app/">Back to dashboard</a></p>
{% else %}
{% if sent %}<p class="notice" role="status">Verification link sent. Check your inbox.</p>
{% endif %}
{% if record %}
<p>We sent a verification link to {{ record.email }}. Click it to verify. API key creation stays locked until you do.</p>
{% else %}
<p>Add an email address to your account. We will send a verification link; API key creation stays locked until you verify.</p>
{% endif %}
{% include "components/form_errors.html" with errors=form.non_field_errors only %}
<form method="post">
{% csrf_token %}
<input type="hidden" name="action" value="update_email">
{% include "components/field.html" with id="id_email" name="email" label="Email" type="email" value=form.email.value autocomplete="email" required=True errors=form.email.errors only %}
{% include "components/field.html" with id="id_password" name="password" label="Current password" type="password" autocomplete="current-password" required=True errors=form.password.errors only %}
<button class="action interactive" type="submit">{% if record %}Update and resend link{% else %}Send verification link{% endif %}</button>
</form>
{% if record %}
<form method="post">
{% csrf_token %}
<input type="hidden" name="action" value="resend">
<p class="form-links"><button class="label interactive" type="submit">Resend verification link</button></p>
</form>
{% endif %}
<p class="form-links"><a class="label interactive" href="/app/">Back to dashboard</a></p>
{% endif %}
{% endblock %}
```

(The two forms keep their hidden `action` values and one `{% csrf_token %}` each: this page is where `update_email` and `resend` are posted from. There is no `autofocus` here, as before: the page has two fields and does not exist to take one. The password field never gets a `value`, and the email field gets the typed or stored address, escaped by the component. The address in the sentence is escaped by autoescape.)

Replace the whole of `backend/templates/registration/verify_email_done.html` (33 lines) with:

```django
{% extends "layouts/form.html" %}
{# The verification link worked (spec 4.7). #}
{% block title %}Email verified | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Email verified</h1>
<p>Your email address is verified. You can now create API keys.</p>
<p><a class="action interactive" href="/app/">Go to dashboard</a></p>
{% endblock %}
```

Replace the whole of `backend/templates/registration/verify_email_invalid.html` (33 lines) with:

```django
{% extends "layouts/form.html" %}
{# The verification link did not work. It says so the same way whatever the reason, and shows nothing of the link (spec 4.7). #}
{% block title %}Link expired | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Link expired</h1>
<p>This verification link is invalid or has expired.</p>
<p><a class="action interactive" href="{% url 'email-verification' %}">Request a new link</a></p>
{% endblock %}
```

(The view still passes `reason`. The template ignores it, as the old one did, so a visitor cannot tell a tampered link from an expired or unknown one; `LinkPageTests` pins it.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_reset_verify_pages`
Expected: `Ran 31 tests ... OK`. If `test_every_state_is_a_page_of_the_shell_on_the_form_layout` is the only failure, Task 1's layout does not put `<div class="form-page">` straight after `<main id="main" class="shell-main">`: fix that in `layouts/form.html`, not here. If `test_a_field_error_is_tied_to_its_field_and_a_clean_field_is_not_marked` is the only failure, the field component does not give the error sentence an id that the input's `aria-describedby` names, or does not set `aria-invalid="true"`: fix it in `components/field.html`.

Then check the new templates against the shell's template rules, which apply to a template because it extends a layout (`pages/tests_site_rules.py`: no `style=`, components included with `only`, no `<header>` or `<footer>`, every include and extends names a file) and to its characters (`pages/tests_content.py`: every character in all three fonts; these seven templates are plain ASCII).

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content pages.tests_catalog`
Expected: `OK`.

- [ ] **Step 6: See which existing tests the new pages break**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests drop_core.tests_fix_auth drop_core.tests_auth_carryover drop_core.tests_fix_web drop_core.tests_reset_verify_pages drop_core.tests_menu drop_core.tests_ambient > /tmp/old_markup.out 2>&1; grep -E '^(FAIL|ERROR): |^Ran |^FAILED|^OK' /tmp/old_markup.out | cut -c1-150; cd ..`
Expected: `Ran 420 tests` and `FAILED (failures=21, errors=2)` (about two and a half minutes). They are exactly these 23 test methods, and each pins the old standalone markup: the old skip link and `<main id="main">`, the `.brand` wordmark, the inline `:focus-visible` rule, the old menu, the boot script, the ambient layer and its tokens, the control cluster, `class="ok" role="status"`. Task 2 pointed them at `/accounts/password_reset/` as the one old page left, and it has now moved.
- `drop_core.tests.AccessibilityTests` (5):
  - `test_skip_link_and_main_id_on_django_pages` for the subtest `url='/accounts/password_reset/'` (it wants `<a class="skip-link" href="#main">` and `<main id="main">`; the shell writes `class="skip-link label"` and `<main id="main" class="shell-main">`);
  - `test_skip_link_on_verify_email_page` (the same two strings, on `/accounts/verify-email/`);
  - `test_decorative_wordmark_cursor_hidden` (the shell has a lockup, not the old `.brand` wordmark);
  - `test_focus_indicator_rule_present_and_outline_not_removed` (the focus rule is in `base.css` now, not inline in the page);
  - `test_verify_email_status_announced` (it wants `class="ok" role="status"`).
- `drop_core.tests_menu.SharedMenuTests` (5): `test_menu_present_on_every_page`, `test_menu_link_order`, `test_menu_keyboard_behavior`, `test_menu_sits_after_skip_link` (an error: no old skip link to find) and `test_menu_uses_menuitem_roles`.
- `drop_core.tests_ambient` (13): `AmbientBootScriptTests.test_boot_script_runs_before_any_style_or_meta`; `AmbientFieldTests.test_ambient_layer_present_on_every_page`, `test_django_pages_all_use_field_intensity` and `test_ambient_is_first_in_body` (an error); `AmbientStylesTests.test_light_theme_tokens_defined`, `test_dark_tokens_unchanged`, `test_blob_alpha_ceiling_capped`, `test_transform_only_keyframes`, `test_print_hides_ambient` and `test_no_literal_hex_for_themed_surfaces`; `AmbientControlsTests.test_control_cluster_on_every_page`, `test_theme_segmented_group` and `test_motion_toggle_reports_state`.

The other tests that touch these pages pass unchanged, and keep their reasons. `PasswordResetTests` and `tests_fix_auth` match `Reset password` and `Choose a new password` (the `h1`), `invalid or has expired`, `you are signed in` and `Log in with the new one` (the sentences, on one line each), the `autocomplete` tokens (`email`, `new-password` exactly twice, `current-password`) and `name="password"` (the field names). `EmailVerificationTests`, `EmailChangeTests` and `VerificationTokenTests` match `verified` and `invalid or has expired` and post to the same address with the same fields. The few tests of `tests_menu` and `tests_ambient` that still pass (`test_no_visible_trust_label`, `test_boot_script_reads_theme_and_motion_keys`, `test_controls_script_writes_same_keys`, `test_veil_present_only_on_text_heavy_pages`, `test_webgl_swap_path_documented`, `NoTemplateCommentLeakTests`) are about the old includes or about what is absent, and go with their modules in the next step.

- [ ] **Step 7: Retire the old-markup tests**

These tests guard the old includes (`includes/menu.html`, `ambient.html`, `theme_boot.html`), and no page this task leaves behind is a stand-in for them for long: the two pages that still include them, the device flow and OAuth consent, move in Tasks 4 and 5, and Task 8 removes the includes. So they are retired now, once, and not pointed at another page. What they guarded that is still true is held elsewhere: the shell's menu, boot script and switches by `pages/tests_shell.py` (`ShellHeadTests`, `ShellBodyTests`, `MenuStartsClosedTests`), the landmarks of every state of these seven pages by `EveryStateTests`, and two guards that were not about the old markup by the last two classes of the new module (Step 2): `NoTemplateCommentLeakTests`, the blog and docs pages' freedom from template comments, which only `tests_ambient` read; and `FocusRingTests`, the keyboard focus ring in `base.css`, which only `test_focus_indicator_rule_present_and_outline_not_removed` read, from an inline rule of an old page.

First prove that nothing else names the two modules.

Run: `grep -rIn "tests_menu\|tests_ambient" backend site-tests/run_site_checks.py .github AGENTS.md`
Expected: no output.

Then remove them.

Run: `git rm backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py`
Expected: `rm 'backend/drop_core/tests_menu.py'` and `rm 'backend/drop_core/tests_ambient.py'`.

In `backend/drop_core/tests.py`, `AccessibilityTests` (Task 2 had pointed three of these methods at the reset page): delete `test_skip_link_and_main_id_on_django_pages` (its list is only the reset page now, and the shell's landmarks are held for it by `EveryStateTests`), delete `test_decorative_wordmark_cursor_hidden` (the shell's lockup is the mark alone with its name hidden for screen readers, which `ShellBodyTests` pins) and `test_focus_indicator_rule_present_and_outline_not_removed` (`FocusRingTests` holds the rule now), and point `test_skip_link_on_verify_email_page` at the shell's markup: the intent is that the signed-in verify page has the skip link and the `main` it targets, and the shell writes them differently. These are one replacement. Replace this exact text:

```python
    def test_skip_link_and_main_id_on_django_pages(self):
        # Sign-in and sign-up are on the shell: FormLayoutTests in drop_core/tests_sign_in_pages.py holds their landmarks.
        urls = [
            "/accounts/password_reset/",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response, '<a class="skip-link" href="#main">'
                )
                self.assertContains(response, '<main id="main">')

    def test_skip_link_on_verify_email_page(self):
        # /accounts/verify-email/ requires a session; check it logged in.
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="a11y", password="pw12345678"
        )
        self.client.force_login(user)
        response = self.client.get("/accounts/verify-email/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a class="skip-link" href="#main">')
        self.assertContains(response, '<main id="main">')

    def test_decorative_wordmark_cursor_hidden(self):
        # The trailing "_" in the wordmark must not be announced. Auth pages use the .brand mark:
        # static underscore, still hidden.
        response = self.client.get("/accounts/password_reset/")
        self.assertContains(response, '<span aria-hidden="true">_</span>')

    # -- visible focus indicator (B6) ----------------------------------------------

    def test_focus_indicator_rule_present_and_outline_not_removed(self):
        response = self.client.get("/accounts/password_reset/")
        html = response.content.decode()
        self.assertIn(":focus-visible", html)
        self.assertNotIn("outline: none", html)
```

with:

```python
    def test_skip_link_on_verify_email_page(self):
        # The skip link and the main it targets, in the shell's markup. /accounts/verify-email/ requires a session; check it
        # logged in. The shell's landmarks on every state of this page are also held by drop_core/tests_reset_verify_pages.py.
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="a11y", password="pw12345678"
        )
        self.client.force_login(user)
        response = self.client.get("/accounts/verify-email/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a class="skip-link label" href="#main">')
        self.assertContains(response, '<main id="main" class="shell-main">')

    # -- visible focus indicator (B6) ----------------------------------------------
```

In the same class, `test_verify_email_status_announced`: the intent is that the status message is announced politely (`role="status"`) and that the page renders with no request. The class name `ok` was the old page's styling, not the reason. Replace this exact text:

```python
        self.assertIn('class="ok" role="status"', html)
```

with:

```python
        self.assertRegex(
            html,
            r'<[a-z]+ [^>]*role="status"[^>]*>\s*Your email address is verified\.',
        )
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.AccessibilityTests drop_core.tests_reset_verify_pages pages.tests_site_rules`
Expected: `OK`.

- [ ] **Step 8: Keep AGENTS.md true**

Nothing in `AGENTS.md` is edited here: Task 2 worded the repo map so that it names no page as old by name, and these seven pages are on the shell now.

Run: `grep -n "password reset\|email verification" AGENTS.md`
Expected: no output. A line printed here would still call these pages older ones: take them out of that sentence.

- [ ] **Step 9: The whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 4 minutes). The suite gains this module's 31 tests and loses 27: the 24 of the two retired modules and the 3 methods of `AccessibilityTests`. If a test outside this task's files fails, read its message first: an older page's test that matched one of these pages' markup would name the string it wanted. Nothing else in the repo does (the search covered every `tests*.py`, `site-tests/run_site_checks.py` and `AGENTS.md` for the phrases, ids, classes and attributes changed here).

Run: `git diff --quiet HEAD -- backend/legal/documents && echo untouched`
Expected: `untouched`.

- [ ] **Step 10: Commit**

`git rm` in Step 7 already staged the two deletions, so the commit holds them.

```bash
git add backend/templates/registration/password_reset_form.html backend/templates/registration/password_reset_done.html backend/templates/registration/password_reset_confirm.html backend/templates/registration/password_reset_complete.html backend/templates/registration/verify_email.html backend/templates/registration/verify_email_done.html backend/templates/registration/verify_email_invalid.html backend/drop_core/tests_reset_verify_pages.py backend/drop_core/tests.py backend/pages/tests_site_rules.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Password reset and email verification move onto the form layout: seven pages on the shell, each with one h1, labelled fields with their errors tied to them, one main action and quiet links, and a test for every state; the tests of the old menu and ambient markup are retired"
```
Expected: one commit, 12 files changed: 9 modified, 1 new and the 2 deleted modules.

---

### Task 4: The device flow on the form layout

The browser half of `veilfile login` (the code entry, the approval, its two results and the refusal) leaves five standalone documents, each with its own `<style>`, wordmark card and the three old includes, and extends `layouts/form.html`, built from Task 1's pieces. The words, the form fields, the three spellings of each address, the confirmation checkbox, the key radios and the escaping of what a stranger's terminal sent do not change; the voice pass (PR 7) rewrites the words. `device/device_error.html` is also the OAuth consent's 403 (`VeilfileAuthorizationView.dispatch` in `drop_core/oauth.py`), so it stays neutral: it knows `error_title`, `error_message` and `show_resend`, and says nothing of a terminal, a code or an app.

**The CLIs' contract.** Neither CLI fetches or parses these pages. Each calls `POST /api/v1/device/code` and `/token` and opens the `verification_uri_complete` the server gives it, `/device?user_code=XXXX-XXXX`. So the contract is that address and what answers at it, and the new tests pin each part: the link, followed signed out, goes to sign in and back to the approval page with its code; a code typed into the entry form reaches the same address in every spelling the server normalizes; the approval page offers a key to replace at the plan's cap (the CLIs' closing sentence, "If you chose a key to replace on the approval page...", depends on it); Approve and Deny post the same fields to the same two addresses, and every spelling of every address answers. The API endpoints and `device.py`'s logic are untouched, and `device.py` still never mentions `REMOTE_ADDR` (`drop_core.tests_client_ip_header`).

**Decisions** (the skeleton leaves them to this task):

- **Focus.** The empty entry page keeps `autofocus` on the code field; after a wrong code the field has none, and no other device page has an input to focus.
  - *Why the empty page keeps it.* The page exists to take one code, and its one input is that field, which is where the skip link (to `<main>`) would take the visitor anyway, so autofocus costs the keyboard nothing. What focus then skips is the paragraph telling them to enter only a code from a `veilfile login` they ran themselves, so the field is described by that paragraph (`aria-describedby="device-intro"`) and a screen reader reads it with the field.
  - *Why a wrong code does not.* The visitor has to read what went wrong: focus would read the field before the sentence, and on a phone the keyboard it opens would cover the sentence. The page loads at the top, the sentence is tied to the field (`aria-invalid`, `aria-describedby`) and the skip link keeps its job.
  - The skeleton's wording was "only when there is no `user_code`". On this page `user_code` and `error` come together (a wrong code is the only way the page has a code), so the template branches on `error`.
- **Task 1's pieces, none of this page's own.**
  - *The code field* is the component's mono variant (`mono=True`: the code's own letters, tracked, in capitals, at the component's size, 16 px or more on phones). The page has no rule for it, and a test fails one.
  - *The key radios and the confirmation* are Task 1's `<label class="check">` rows, so the whole row is the label (and the tap target), each input has its visible words, and the fieldset's legend is the layout's own label style.
  - *The unverified notice* is `<p class="notice" role="status">`.
  - *The resend link* on the refusal is a secondary action: `<p class="form-links">` holding `<a class="label interactive">`.
  - *Every button* is `<button class="action interactive" type="submit">`, which is what gives it the ink wash on hover and press; a test checks each one the pages draw.
- **Approve and Deny are equal.** They are Task 1's `.form-actions`: two `.action.interactive` buttons in one form, Approve first. Deny has to post (`formaction`, `formnovalidate`), so it cannot be a link. It is not a quiet afterthought: the page tells a visitor who did not start the login to click it.
- **The code's name.** The old `div` carried `aria-label="Device code ..."` and no role, which axe reports (`aria-prohibited-attr`: a `div` with no role cannot be named). The code is now plain text under a visible label, `Device code` (the aria-label's own words), so everyone reads what a screen reader was told.
- **The mark.** The success page's check mark (U+2713, entity `&#10003;`) is in none of the site's three fonts. A CSS-drawn mark (`.device-mark`: two borders turned 45 degrees, `aria-hidden`) replaces it. The heading and the first sentence already say "approved", so a screen reader loses nothing (the old `role="img"` mark was a third saying of it).
- **Errors as lists.** `components/field.html` and `components/form_errors.html` take `errors` as a list of sentences (a Django form's `ErrorList` is one; a bare string would be read letter by letter). The two device renders that carry one (a wrong code, a refused approval) hold one sentence, so each passes it twice, as `error` (the template's switch) and as `errors` (the component's input): two context lines in `device.py`.
- **The approval's alert comes first.** When the approval is refused (no confirmation, a key no longer active, the plan's limit) the sentence is drawn right under the heading, through `form_errors` (`role="alert"`). At the foot of this long page, where it used to sit, it was below the fold.
- **A page stylesheet, not a component, and only where it is used.** The pieces only the approval and its success have (the large code, who asked, what the key can do, the mark) are one stylesheet, `static/css/pages/device.css`, linked from the `styles` block of those two pages after the shell's (as `pages/catalog.html` links its own) and unscoped, its classes all `device-*`. The entry, the denial and the refusal are the layout's and the components' alone and load nothing more, so `device_error.html`, which the OAuth consent shares, draws nothing of the device flow's. None of it is a reusable component, so none needs a catalog entry. The layout's `form_page` block is not wrapped in anything of ours.
- **Titles use the shell's form.** The five `<title>`s are the old words with ` | Veilfile` after them, as on every page of the shell (the voice rules ban the em dash the old ones had). The headings are the same words as the titles, and a test pins both, so the voice pass changes them on purpose.
- **Search.** Each page holds a one-time code or state, so each sets `<meta name="robots" content="noindex">` (`/device` is already `Disallow` in `robots.txt`).

**Files:**
- Create: `backend/drop_core/tests_device_pages.py`
- Create: `backend/static/css/pages/device.css`
- Modify: `backend/drop_core/device.py` (two context lines)
- Modify (whole files): `backend/templates/device/device_enter_code.html`, `device_consent.html`, `device_success.html`, `device_denied.html`, `device_error.html`, `backend/templates/device/includes/grant_summary.html`
- Modify: `backend/pages/tests_site_rules.py` (one name appended to `READING_TEST_MODULES`)
- Read, not changed: `backend/drop_core/oauth.py` (renders `device/device_error.html`), `backend/config/urls.py` (the three spellings), `backend/templates/shell.html`, Task 1's `backend/templates/layouts/form.html`, `components/field.html` and `form_errors.html`, and its "Forms" section of `backend/static/css/base.css`
- No existing test changes: Step 7 shows why.

**Interfaces:**
- Consumes:
  - Task 1's `layouts/form.html`: extends `shell.html`, `{% block body_class %}layout-form{% endblock %}`, and `{% block main %}<div class="form-page">{% block form_page %}{% endblock %}</div>{% endblock %}`. These pages use its `form_page` block, and the shell's `title`, `meta` and `styles` blocks.
  - Task 1's `components/field.html` with `id`, `name`, `label`, `type`, `value`, `autocomplete`, `required`, `autofocus`, `errors`, `extra` and `mono`. `errors` is a list of sentences, `extra` is a string literal of raw attribute text, `autofocus` and `mono` are booleans, and `aria-describedby` is written only when there are `help` or `errors` (this page gives the empty entry's field its description through `extra`, since it has neither).
  - Task 1's `components/form_errors.html` with `errors`, a list (and `message`, which this page does not use); it draws a `role="alert"` element and nothing for an empty list.
  - Task 1's markup and CSS: `<button class="action interactive" type="submit">`, `<div class="form-actions">`, `<label class="check"><input class="check__input" ...><span class="check__text">...<span class="check__detail">...`, `<p class="notice" role="status">`, `<p class="form-links">` with `a.label.interactive`, and the layout's styling of a bare `h1`, `p`, `ul`, `dl`/`dt`/`dd`, `fieldset`/`legend` and `code` in `.form-page`.
  - `.label` and `.interactive` (`base.css`), the `--font-mono`, `--space-*`, `--ink`, `--muted` and `--hair` tokens (`tokens.css`).
- Produces:
  - The five pages and the grant summary, on the form layout, keeping every context key `device.py` and `oauth.py` pass (`email_verified`, `error`, `user_code`, `device_code`, `account`, `key_cap`, `replacement_required`, `replaceable_keys`, `selected_key`, `replace_key`, `error_title`, `error_message`, `show_resend`) and adding one: `errors`, a list, from `device_page` (a wrong code) and `_render_consent`.
  - `device_error.html`'s contract for Task 5 and anything else that renders it: `error_title`, `error_message`, `show_resend`. Its title is `{{ error_title }} | Veilfile`.
  - `static/css/pages/device.css`, linked by `device_consent.html` and `device_success.html` (classes `device-warning`, `device-code`, `device-code__value`, `device-request`, `device-grant`, `device-grant__foot` and `device-mark`). No body class.
  - `backend/drop_core/tests_device_pages.py`: 41 tests in seven classes, each setting `longMessage = False` (the shared `DeviceCase` base sets it for five of them), and the module listed in `READING_TEST_MODULES`.

- [ ] **Step 1: Write the failing tests**

Create `backend/drop_core/tests_device_pages.py`:

```python
"""The device flow's pages on the shell (spec 4.7): the code entry, the approval, its two results and the refusal.

The five templates in backend/templates/device/ extend layouts/form.html. Their words are the old pages' (the voice pass,
PR 7, rewrites them), so what these tests hold is what must not change with the move: the form's fields and addresses, the
escaping of what a stranger's terminal sent, the confirmation the approval needs, the keys offered at the plan's cap, and the
two other flows that meet these pages (the CLI's link, and the OAuth consent's refusal, which renders device/device_error.html).

Every class sets longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the
page it searched (tens of kilobytes of HTML). pages.tests_site_rules.READING_TEST_MODULES holds this module to it.
"""
import re
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core import tests_oauth
from drop_core.models import APIKey, DeviceCode, EmailVerification, get_or_create_workspace

User = get_user_model()

DEVICE_TEMPLATES = Path(settings.BASE_DIR) / "templates" / "device"
DEVICE_CSS = Path(settings.BASE_DIR) / "static" / "css" / "pages" / "device.css"
PAGE_TEMPLATES = {"device_enter_code.html", "device_consent.html", "device_success.html", "device_denied.html",
                  "device_error.html"}
DASH = "\N{EM DASH}"  # the voice rules ban it, in a stylesheet's comments too
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
# The <title> of each state a visitor can meet: the old pages' words, in the shell's form (Name | Veilfile).
TITLES = {
    "enter": "Enter device code | Veilfile",
    "enter, wrong code": "Enter device code | Veilfile",
    "consent": "Approve terminal login | Veilfile",
    "consent, refused": "Approve terminal login | Veilfile",
    "invalid": "Invalid code | Veilfile",
    "expired": "This code has expired | Veilfile",
    "too many": "Too many attempts | Veilfile",
    "unverified": "Verify your email first | Veilfile",
    "success": "Terminal approved | Veilfile",
    "denied": "Terminal denied | Veilfile",
}
STATUSES = {"enter": 200, "enter, wrong code": 200, "consent": 200, "consent, refused": 400, "invalid": 400,
            "expired": 410, "too many": 429, "unverified": 403, "success": 200, "denied": 200}
CHECK_MARKS = ("\N{CHECK MARK}", "&#10003;", "&#x2713;", "&check;", "&checkmark;")


class Tags(HTMLParser):
    """The start tags of some HTML, in order, each as (name, attributes). A boolean attribute has the value None."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def find(self, tag, where=None):
        where = where or {}
        return [attrs for name, attrs in self.tags if name == tag and all(attrs.get(k) == v for k, v in where.items())]


def main_of(html):
    return html[html.index('<main id="main"'):html.index("</main>")]


def text_of(html):
    """What a reader reads in a piece of HTML: tags removed, entities decoded, white space made single spaces."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def element_with_id(html, element_id):
    """The inner HTML of the element that carries this id, or None."""
    found = re.search(rf'<(\w+)\b[^>]*\bid="{re.escape(element_id)}"[^>]*>(.*?)</\1>', html, re.DOTALL)
    return found.group(2) if found else None


def buttons_of(html):
    """[(attributes, text)] for each button, in order."""
    return [(Tags(tag).find("button")[0], text_of(text))
            for tag, text in re.findall(r"(<button\b[^>]*>)(.*?)</button>", html, re.DOTALL)]


def check_rows_of(html):
    """The inner HTML of each <label class="check"> row, in order: Task 1's row of a checkbox or a radio and its words."""
    return re.findall(r'<label class="check">(.*?)</label>', html, re.DOTALL)


def raw_input_tag(html, name):
    """The input tag of this name as written, to count attributes that a parser would merge."""
    return next((tag for tag in re.findall(r"<input\b[^>]*>", html) if f'name="{name}"' in tag), "")


class DeviceCase(TestCase):
    longMessage = False

    def says(self, haystack, needle, where):
        self.assertIn(needle, haystack, f"{where} must still hold {needle!r}")

    def omits(self, haystack, needle, where):
        self.assertNotIn(needle, haystack, f"{where} must not hold {needle!r}")

    def setUp(self):
        cache.clear()  # the lookup limits are counted in the cache
        self.user = User.objects.create_user(username="dev", email="dev@example.com", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        EmailVerification.objects.create(user=self.user, email=self.user.email, is_verified=True,
                                         verified_at=timezone.now())
        self.terminal = APIClient()
        self.browser = APIClient()
        self.browser.force_login(self.user)

    def start(self, **extra):
        response = self.terminal.post("/api/v1/device/code", {}, format="json", **extra)
        self.assertEqual(response.status_code, 200, "POST /api/v1/device/code must answer 200 (backend/drop_core/device.py)")
        return response.json()

    def unverified_browser(self):
        other = User.objects.create_user(username="newbie", email="newbie@example.com", password="pw12345678")
        get_or_create_workspace(other)
        client = APIClient()
        client.force_login(other)
        return client

    def at_the_key_cap(self, name="prod-agent"):
        """The free plan allows one key: with it made, an approval must name a key to replace."""
        key, _raw = APIKey.create_key(self.workspace, name)
        return key

    def consent(self, **extra):
        grant = self.start(**extra)
        response = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 200, "GET /device?user_code=... for a live code answers 200")
        return grant, main_of(response.content.decode())

    def every_page(self):
        """label -> response, for each state of the flow a visitor can meet (see TITLES and STATUSES)."""
        grant = self.start()
        pages = {
            "enter": self.browser.get("/device"),
            "enter, wrong code": self.browser.get("/device", {"user_code": "NOPE-NOPE"}),
            "consent": self.browser.get("/device", {"user_code": grant["user_code"]}),
            "consent, refused": self.browser.post("/device/approve", {"user_code": grant["user_code"]}),
            "invalid": self.browser.post("/device/approve", {"user_code": "NOPE-NOPE"}),
        }
        expired = self.start()
        DeviceCode.objects.filter(user_code=expired["user_code"].replace("-", "")).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        pages["expired"] = self.browser.get("/device", {"user_code": expired["user_code"]})
        pages["unverified"] = self.unverified_browser().post(
            "/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        pages["success"] = self.browser.post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        pages["denied"] = self.browser.post("/device/deny", {"user_code": self.start()["user_code"]})
        for _ in range(12):  # ten misses a window are allowed; the next lookup is refused
            response = self.browser.get("/device", {"user_code": "NOPE-NOPE"})
            if response.status_code == 429:
                break
        pages["too many"] = response
        return pages


class DevicePageFrameTests(DeviceCase):
    """What every page of the flow shares: the form layout, one h1, no index, its title and its buttons."""

    def test_every_state_answers_as_it_did(self):
        pages = self.every_page()
        self.assertEqual({label: response.status_code for label, response in pages.items()}, STATUSES,
                         "the device pages keep their status codes (backend/drop_core/device.py); only the templates moved")
        self.assertEqual(sorted(TITLES), sorted(STATUSES),
                         "backend/drop_core/tests_device_pages.py: TITLES and STATUSES must name the same states")

    def test_every_page_is_drawn_by_the_form_layout(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                self.assertIn("layouts/form.html", [template.name for template in response.templates],
                              f"{label}: its template in backend/templates/device/ must extend layouts/form.html")
                self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                                 f"{label}: <body> needs the class layout-form (backend/templates/layouts/form.html)")
                self.omits(html, "data-layout", f"{label}: a form page has no layout family (it is not in "
                           "backend/pages/registry.py), so no in-place change; its page")
                self.says(html, '<a class="skip-link label" href="#main">', f"{label}: the page")
                main = main_of(html)
                self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1, f"{label}: exactly one h1")
                self.assertEqual(len(re.findall(r"<h1[ >]", main)), 1, f"{label}: the h1 belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.omits(main, element, f"{label}: <main> (use a div: backend/static/css/base.css styles the bare element)")

    def test_every_page_has_its_title(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.says(response.content.decode(), f"<title>{TITLES[label]}</title>",
                          f"{label}: the title block of its template in backend/templates/device/ (the old page's words, "
                          "then ' | Veilfile' as on every page of the shell); the page")

    def test_every_page_is_kept_out_of_search(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.says(response.content.decode(), '<meta name="robots" content="noindex">',
                          f"{label}: it holds a one-time code or state, so the meta block of its template in "
                          "backend/templates/device/ needs it; the page")

    def test_a_page_links_the_device_stylesheet_exactly_when_it_uses_a_class_of_it(self):
        # Only the approval and its success draw pieces the form layout lacks (the large code, who asked, what the key can
        # do, the mark). The entry, the denial and the refusal (which the OAuth consent shares) are the layout's and the
        # components' alone, so they load nothing more.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                uses = re.search(r'class="[^"]*\bdevice-[a-z_]+', html) is not None
                self.assertEqual(html.count("css/pages/device.css"), 1 if uses else 0,
                                 f"{label}: a page that uses a device-* class links css/pages/device.css once in its styles "
                                 "block ({{ block.super }} first), and a page that does not leaves it out: "
                                 "backend/templates/device/*.html")

    def test_only_the_empty_code_entry_takes_focus(self):
        # autofocus is for a page that exists to take one input: the entry with no code typed. After a wrong code the
        # visitor reads the sentence first (and a phone's keyboard would cover it); no other page has an input to focus.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                count = response.content.decode().count("autofocus")
                self.assertEqual(count, 1 if label == "enter" else 0,
                                 f"{label}: autofocus appears {count} times; only 'enter' (no code typed) sets it, in "
                                 "backend/templates/device/device_enter_code.html")

    def test_every_button_is_the_forms_main_action_with_its_ink_wash(self):
        # .interactive is what gives a control the ink wash on hover and press (backend/static/css/base.css).
        seen = 0
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                for attributes, text in buttons_of(main_of(response.content.decode())):
                    seen += 1
                    self.assertTrue({"action", "interactive"} <= set(attributes.get("class", "").split())
                                    and attributes.get("type") == "submit",
                                    f'{label}: the {text!r} button must be <button class="action interactive" type="submit"> '
                                    "(components of the form layout, backend/templates/device/*.html)")
        self.assertEqual(seen, 6, "backend/drop_core/tests_device_pages.py: the two entry states have the Continue button "
                         "and the two approval states have Approve and Deny; no other page has a button")

    def test_no_page_shows_a_template_comment(self):
        # Django 6.1 prints a {# comment #} that spans lines as text: each in the device templates stays on one line.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                for mark in ("{#", "#}"):
                    self.omits(html, mark, f"{label}: a template comment shows, so keep each {{# #}} on one line; the page")

    def test_no_page_has_a_check_mark_character(self):
        # U+2713 is in none of the site's three fonts, so it would be drawn in whatever font the system falls back to.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                for mark in CHECK_MARKS:
                    self.omits(html, mark, f"{label}: draw the mark in CSS (.device-mark in "
                               "backend/static/css/pages/device.css); the page")

    def test_nothing_in_main_carries_an_aria_label(self):
        # axe (aria-prohibited-attr): a div or p with no role cannot carry aria-label, which the code's div had.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.omits(main_of(response.content.decode()), "aria-label", f"{label}: <main> (a visible label, class "
                           "label, names things in backend/templates/device/*.html; a role-less element cannot be named)")


class DeviceTemplateSourceTests(SimpleTestCase):
    longMessage = False

    def sources(self):
        return {path.relative_to(DEVICE_TEMPLATES).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(DEVICE_TEMPLATES.rglob("*.html"))}

    def stylesheet(self):
        self.assertTrue(DEVICE_CSS.is_file(), "backend/static/css/pages/device.css is the device pages' stylesheet")
        return DEVICE_CSS.read_text(encoding="utf-8")

    def test_the_five_pages_and_the_summary_are_the_folders_files(self):
        self.assertEqual(set(self.sources()), PAGE_TEMPLATES | {"includes/grant_summary.html"},
                         "backend/templates/device/ holds the five pages and includes/grant_summary.html; "
                         "drop_core/device.py and drop_core/oauth.py render them by name")

    def test_each_page_extends_the_form_layout_and_nothing_old_is_left(self):
        for name, text in self.sources().items():
            with self.subTest(template=name):
                if name in PAGE_TEMPLATES:
                    self.assertTrue(text.startswith('{% extends "layouts/form.html" %}'),
                                    f'backend/templates/device/{name} must start with {{% extends "layouts/form.html" %}}')
                for old in ("includes/theme_boot", "includes/ambient", "includes/menu", "<!DOCTYPE", "<style", "<script"):
                    self.assertNotIn(old, text, f"backend/templates/device/{name} holds {old}, which belongs to the old "
                                     "page; the shell and the form layout give it everything")
                self.assertNotRegex(text, r"\sstyle\s*=", f"backend/templates/device/{name}: no inline style; "
                                    "put it in backend/static/css/pages/device.css")

    def test_no_template_holds_a_check_mark(self):
        for name, text in self.sources().items():
            with self.subTest(template=name):
                for mark in CHECK_MARKS:
                    self.assertNotIn(mark, text, f"backend/templates/device/{name} holds {mark!r}, which none of the "
                                     "site's fonts has; the success page draws its mark in CSS")

    def test_the_stylesheet_is_tokens_only_and_has_no_em_dash(self):
        text = self.stylesheet()
        self.assertNotRegex(text, r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(",
                            "backend/static/css/pages/device.css: colours come only from tokens.css")
        self.assertNotIn(DASH, text, "backend/static/css/pages/device.css: no em dash, not even in a comment")

    def test_the_stylesheet_styles_only_what_the_forms_pieces_do_not(self):
        # The field, the checkbox and radio rows, the actions and the notice are the form layout's (base.css and
        # components/field.css): every form page has the same ones, so a rule for one here would make this page differ.
        rules = re.sub(r"/\*.*?\*/", "", self.stylesheet(), flags=re.DOTALL)
        found = re.findall(r"\.(?:field|check|form-actions|form-links|action|notice)(?![a-z0-9-])\S*|#id_user_code", rules)
        self.assertEqual(found, [], f"backend/static/css/pages/device.css styles {found}: change the piece in "
                         "backend/static/css/base.css or components/field.css instead, or pass the field's mono input")

    def test_what_a_stranger_or_a_user_wrote_wraps_so_a_phone_never_scrolls_sideways(self):
        # The client string and the account's address have no length we choose, and nor has a key's name. The client string
        # and the address are in a grid cell that may shrink (min-width: 0) and break anywhere; the code is nine characters
        # at a tracked size and breaks too. A key's name is in a check row, which the form layout lets wrap. The browser
        # checks look at 320px.
        text = self.stylesheet()
        for selector, shrinks in ((".device-request dd", True), (".device-code__value", False)):
            with self.subTest(selector=selector):
                block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
                self.assertIsNotNone(block, f"backend/static/css/pages/device.css needs a rule for {selector}")
                self.assertIn("overflow-wrap: anywhere", block.group(1),
                              f"backend/static/css/pages/device.css: {selector} needs overflow-wrap: anywhere")
                if shrinks:
                    self.assertIn("min-width: 0", block.group(1), f"backend/static/css/pages/device.css: {selector} is a "
                                  "grid child, so it needs min-width: 0 to be allowed to shrink and wrap")


class DeviceEnterCodeTests(DeviceCase):
    def entry(self, **query):
        return main_of(self.browser.get("/device", query).content.decode())

    def test_the_form_is_the_get_that_the_clis_link_is(self):
        main = self.entry()
        tags = Tags(main)
        forms = tags.find("form")
        self.assertEqual(len(forms), 1, "the entry page has one form (backend/templates/device/device_enter_code.html)")
        self.assertEqual((forms[0].get("method"), forms[0].get("action")), ("get", "/device"),
                         "the entry form is a GET to /device, so a typed code reaches /device?user_code=..., the "
                         "address the CLI opens; changing it breaks that link")
        fields = tags.find("input", {"name": "user_code"})
        self.assertEqual(len(fields), 1, "one input named user_code")
        field = fields[0]
        self.assertEqual({key: field.get(key) for key in ("id", "type", "autocomplete", "spellcheck", "placeholder")}, {
            "id": "id_user_code", "type": "text", "autocomplete": "off", "spellcheck": "false",
            "placeholder": "XXXX-XXXX"}, "the code input keeps its id, type, autocomplete, spellcheck and placeholder")
        self.assertIn("required", field, "the code input is required")
        self.assertIn("field__input--mono", field.get("class", "").split(),
                      "the code input is the field component's mono variant: pass mono=True to components/field.html "
                      "in backend/templates/device/device_enter_code.html (no rule of this page's own)")
        label = re.search(r'<label\b[^>]*\bfor="id_user_code"[^>]*>(.*?)</label>', main, re.DOTALL)
        self.assertIsNotNone(label, 'the code input needs a <label for="id_user_code"> (components/field.html)')
        self.assertEqual(text_of(label.group(1)), "Device code", "the label's visible words are 'Device code'")
        [(button, text)] = buttons_of(main)
        self.assertEqual((button.get("type"), text), ("submit", "Continue"))

    def test_a_typed_code_reaches_the_consent_page_in_any_spelling_the_form_can_send(self):
        grant = self.start()
        raw = grant["user_code"].replace("-", "")
        for typed in (grant["user_code"], raw, raw.lower(), f"{raw[:4].lower()} {raw[4:].lower()}"):
            with self.subTest(typed=typed):
                main = self.entry(user_code=typed)
                self.says(main, "Approve terminal login", "a code typed in the entry form finds its grant "
                          "(_normalize_user_code in backend/drop_core/device.py); the page")

    def test_with_no_code_the_field_is_focused_and_the_intro_is_read_with_it(self):
        main = self.entry()
        tag = raw_input_tag(main, "user_code")
        self.assertIn(" autofocus", tag, "the empty entry page focuses its one input (device_enter_code.html)")
        self.assertEqual(tag.count("aria-describedby="), 1,
                         "one aria-describedby on the code input: components/field.html writes its own only with errors "
                         "or help; if it now writes one always, pass the intro as its help input and drop it from extra")
        self.assertEqual(Tags(tag).find("input")[0].get("aria-describedby"), "device-intro",
                         "focus skips the paragraph above the field, so the field is described by it")
        intro = element_with_id(main, "device-intro")
        self.assertIsNotNone(intro, "the intro paragraph needs id=device-intro: it is the field's description")
        self.assertIn("Enter the code shown in your terminal. Only enter a code from a veilfile login you ran yourself.",
                      text_of(intro), "the intro keeps its words until the voice pass")

    def test_after_a_wrong_code_the_sentence_is_tied_to_the_field_and_nothing_takes_focus(self):
        main = self.entry(user_code="NOPE-NOPE")
        tag = raw_input_tag(main, "user_code")
        attributes = Tags(tag).find("input")[0]
        self.assertEqual(attributes.get("value"), "NOPE-NOPE", "the typed code stays in the field")
        self.assertEqual(attributes.get("aria-invalid"), "true", "a wrong code marks the field invalid")
        self.assertNotIn("autofocus", tag, "no focus after a wrong code: the visitor reads the sentence first")
        described = [element_with_id(main, name) for name in attributes.get("aria-describedby", "").split()]
        self.assertTrue(described and all(described), "aria-describedby must name elements of the page")
        self.assertIn("We couldn't find that code.", text_of(" ".join(described)),
                      "the field's sentence is the view's error (the 'errors' list in backend/drop_core/device.py)")
        self.says(text_of(main), "doesn't match a pending login", "the paragraph above the field")
        self.omits(main, "Enter the code shown in your terminal.", "the page after a wrong code")
        self.omits(main, 'id="device-intro"', "the page after a wrong code (the explanation is the paragraph)")

    def test_a_code_typed_with_markup_comes_back_as_text(self):
        # A wrong code returns in the field's value from the query string, which anyone can write.
        typed = '"><img src=x onerror=alert(1)>'
        main = self.entry(user_code=typed)
        self.omits(main, "<img src=x", "the page after a wrong code: components/field.html must escape the value "
                   "(auto-escaping, never |safe)")
        self.assertEqual(Tags(raw_input_tag(main, "user_code")).find("input")[0].get("value"), typed,
                         "the field shows the typed code as text, so the visitor can correct it")

    def test_the_unverified_notice_is_a_status_line_that_links_the_resend_page(self):
        main = main_of(self.unverified_browser().get("/device").content.decode())
        found = re.search(r'<p class="notice" role="status">(.*?)</p>', main, re.DOTALL)
        self.assertIsNotNone(found, 'an unverified visitor sees a <p class="notice" role="status"> (device_enter_code.html)')
        self.assertIn("Verify your email first.", text_of(found.group(1)))
        self.assertIn(f'href="{reverse("email-verification")}"', found.group(1),
                      "the notice links to the page that re-sends the verification email")
        self.omits(self.entry(), "Verify your email first", "the page of a verified visitor")


class DeviceConsentTests(DeviceCase):
    def test_the_code_is_text_under_a_visible_label(self):
        grant, main = self.consent()
        self.assertRegex(main, r'<p class="label">Device code</p>\s*<p class="device-code__value">'
                         + re.escape(grant["user_code"]) + r"</p>",
                         "the code is a paragraph under a visible 'Device code' label (device_consent.html)")

    def test_who_asked_is_four_terms_and_their_values(self):
        _grant, main = self.consent(REMOTE_ADDR="198.51.100.7", HTTP_USER_AGENT="veilfile-cli/0.3.0 (test)")
        pairs = re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", main, re.DOTALL)
        self.assertEqual([term for term, _value in pairs], ["Requested", "From IP", "Client", "Signed in as"],
                         "the request's details keep their four terms, in order (device_consent.html)")
        values = [text_of(value) for _term, value in pairs]
        self.assertEqual(values[1:], ["198.51.100.7", "veilfile-cli/0.3.0 (test)", "dev@example.com"])
        self.assertRegex(values[0], r"^\d+ minutes? ago$")

    def test_what_the_terminal_sent_is_escaped_and_cut(self):
        # The client string comes from an unauthenticated request. It is shown as text, never as markup, and a long
        # one is cut at 120 characters.
        _grant, main = self.consent(HTTP_USER_AGENT="<img src=x onerror=alert(1)>" + "x" * 250)
        self.omits(main, "<img src=x", "the page: the client string must be escaped (auto-escaping in "
                   "backend/templates/device/device_consent.html, never |safe)")
        self.says(main, "<code>&lt;img src=x onerror=alert(1)&gt;", "the page: the client string, in <code>")
        client = re.search(r"<dt[^>]*>Client</dt>\s*<dd[^>]*>(.*?)</dd>", main, re.DOTALL).group(1)
        self.assertLessEqual(len(text_of(client)), 120, "the client string is cut with truncatechars:120")
        self.assertTrue(text_of(client).endswith(ELLIPSIS), "a cut client string ends in an ellipsis")

    def test_the_warning_and_the_grant_summary_keep_their_words(self):
        _grant, main = self.consent()
        words = text_of(main)
        for needle in (
            "Only approve if you just ran veilfile login yourself and the code below matches the one in your terminal. "
            "If someone sent you this link or asked you to approve it, click Deny: approving would give them access to "
            "your files.",
            "A terminal is asking for an API key to your workspace.",
            "Approving creates an API key for your workspace. Whoever holds it can:",
            "Upload files and create share links",
            "List every artifact in the workspace and download each one through its link",
            "Revoke artifacts",
            "List the workspace's API keys and revoke any of them",
            "Disconnect every app connected to your account (OAuth / MCP clients)",
            "See the account email and plan usage",
            "The key does not expire: it works until you revoke it in the dashboard. It counts toward your plan's limit "
            "of 1 API key. It is sent only to the terminal; this browser never sees it.",
        ):
            self.says(words, needle, "backend/templates/device/device_consent.html or includes/grant_summary.html "
                      "(the words stay until the voice pass, and the summary must stay accurate)")

    def test_the_form_posts_the_same_fields_to_the_same_two_addresses(self):
        grant, main = self.consent()
        tags = Tags(main)
        forms = tags.find("form")
        self.assertEqual(len(forms), 1, "the approval page has one form")
        self.assertEqual((forms[0].get("method"), forms[0].get("action")), ("post", "/device/approve/"))
        self.assertEqual([attrs.get("value") for attrs in tags.find("input", {"type": "hidden", "name": "user_code"})],
                         [grant["user_code"]], "the hidden user_code carries the code as XXXX-XXXX")
        self.assertEqual(len(tags.find("input", {"type": "hidden", "name": "csrfmiddlewaretoken"})), 1,
                         "{% csrf_token %} stays in the page's own form (a component included with only cannot see it)")
        self.says(main, 'class="check__input" type="checkbox" name="confirm" value="yes" required',
                  "the page: the confirmation checkbox (the approval is refused without it)")
        confirm = [row for row in check_rows_of(main) if 'name="confirm"' in row]
        self.assertEqual(len(confirm), 1, 'the confirmation is one <label class="check"> row: the whole row is the label '
                         "(backend/templates/device/device_consent.html)")
        self.assertEqual(text_of(confirm[0]), "I just ran veilfile login myself, and this code matches my terminal.")
        self.assertRegex(confirm[0], r'<span class="check__text">I just ran .*</span>\s*$',
                         "the words of the row are its check__text")
        actions = re.search(r'<div class="form-actions">(.*?)</div>', main, re.DOTALL)
        self.assertIsNotNone(actions, 'Approve and Deny are two actions of equal weight: <div class="form-actions"> '
                             "(backend/templates/device/device_consent.html)")
        buttons = buttons_of(main)
        self.assertEqual(buttons_of(actions.group(1)), buttons, "both buttons sit in the one form-actions div")
        self.assertEqual([text for _attrs, text in buttons], ["Approve", "Deny"],
                         "two submit buttons, Approve first and Deny after it, both in the one form")
        (approve, _), (deny, _) = buttons
        self.assertEqual((approve.get("type"), approve.get("formaction")), ("submit", None),
                         "Approve posts to the form's own action, /device/approve/")
        self.assertEqual((deny.get("type"), deny.get("formaction")), ("submit", "/device/deny/"))
        self.assertIn("formnovalidate", deny, "Deny skips the browser's required-field check")

    def test_below_the_cap_there_is_no_key_to_replace(self):
        _grant, main = self.consent()
        for text in ("replace_key", "Choose a key to replace"):
            self.omits(main, text, "the approval page of a workspace under its plan's key limit")

    def test_at_the_cap_each_key_is_a_radio_row_with_its_id_as_the_value(self):
        first = self.at_the_key_cap("prod-agent")
        APIKey.objects.filter(pk=first.pk).update(last_used_at=None)
        _grant, main = self.consent()
        self.assertRegex(main, r"<fieldset>\s*<legend>Choose a key to replace</legend>",
                         "the keys are one fieldset with the legend 'Choose a key to replace'")
        radios = Tags(main).find("input", {"type": "radio"})
        self.assertEqual([(radio.get("name"), radio.get("value")) for radio in radios], [("replace_key", str(first.pk))],
                         "one radio per active key, named replace_key, its value the key's id as plain digits")
        self.says(main, f'type="radio" name="replace_key" value="{first.pk}"', "the page: the radio, attributes in this order")
        self.assertIn("required", radios[0], "a key must be chosen before the form can be sent")
        self.assertNotIn("checked", radios[0], "no key is chosen on the first view")
        rows = [row for row in check_rows_of(main) if 'name="replace_key"' in row]
        self.assertEqual(len(rows), 1, 'each key is one <label class="check"> row: the whole row is the label '
                         "(backend/templates/device/device_consent.html)")
        self.assertIn('class="check__input"', rows[0], "the radio is the form layout's check__input")
        self.assertRegex(rows[0], r'<span class="check__text">prod-agent<span class="check__detail">.*</span></span>\s*$',
                         "the key's name, then its details in check__detail")
        words = text_of(rows[0])
        for needle in ("prod-agent", first.key_prefix, "last used never"):
            self.assertIn(needle, words, f"the key's row tells {needle!r}")
        self.says(text_of(main), "Your plan allows 1 active API key, and this workspace has 1.", "the page")

    def test_a_key_name_is_shown_as_text(self):
        self.at_the_key_cap("<b>boom</b>")
        _grant, main = self.consent()
        self.omits(main, "<b>boom</b>", "the page: a key's name is the user's own text, so it is escaped")
        self.says(main, "&lt;b&gt;boom&lt;/b&gt;", "the page: the key's name, as text")

    def test_a_refused_approval_says_why_first_and_keeps_the_choice(self):
        key = self.at_the_key_cap("prod-agent")
        grant = self.start()
        response = self.browser.post("/device/approve", {"user_code": grant["user_code"], "replace_key": str(key.pk)})
        self.assertEqual(response.status_code, 400)
        main = main_of(response.content.decode())
        self.assertEqual(main.count('role="alert"'), 1, "a refused approval has one alert (components/form_errors.html)")
        before_the_form = text_of(main[main.index('role="alert"'):main.index("<form")])
        self.says(before_the_form, "Confirm that you ran veilfile login yourself and that the code matches your terminal.",
                  "the alert, which comes before the form where the visitor lands (at the foot of this long page it is "
                  "below the fold; backend/templates/device/device_consent.html)")
        chosen = Tags(main).find("input", {"name": "replace_key"})[0]
        self.assertIn("checked", chosen, 'the key chosen stays chosen (selected_key is compared with key.id|stringformat:"d")')
        self.says(main, f'name="user_code" value="{grant["user_code"]}"', "the page: the hidden code")

    def test_an_unverified_visitor_is_warned_on_the_approval_too(self):
        grant = self.start()
        main = main_of(self.unverified_browser().get("/device", {"user_code": grant["user_code"]}).content.decode())
        self.assertRegex(main, r'<p class="notice" role="status"><strong>Verify your email first\.</strong>',
                         'an unverified visitor sees a <p class="notice" role="status"> on the approval too (device_consent.html)')
        self.says(main, f'href="{reverse("email-verification")}"', "the notice: the link to re-send")
        self.omits(self.consent()[1], "Verify your email first", "the approval page of a verified visitor")


class DeviceResultPageTests(DeviceCase):
    def test_the_success_page_draws_its_mark_and_says_it_with_words(self):
        key = self.at_the_key_cap("old <i>key</i>")
        grant = self.start()
        response = self.browser.post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes",
                                                         "replace_key": str(key.pk)})
        self.assertEqual(response.status_code, 200)
        main = main_of(response.content.decode())
        self.says(main, '<span class="device-mark" aria-hidden="true"></span>',
                  "backend/templates/device/device_success.html: the mark, decoration drawn in CSS; the heading and "
                  "the first sentence say it")
        self.says(main, "<h1>Terminal approved</h1>", "backend/templates/device/device_success.html")
        self.omits(main, 'role="img"', "backend/templates/device/device_success.html (the old mark was an image)")
        words = text_of(main)
        self.says(words, "Approved. Return to your terminal: it receives its key on its next check, within a few seconds. "
                  "You can close this window.", "backend/templates/device/device_success.html")
        self.says(words, "When it does, old <i>key</i> (", "device_success.html: the replaced key's name, as text")
        self.says(words, f"{key.key_prefix}{ELLIPSIS} ) is revoked.", "device_success.html: the replaced key's prefix")
        self.omits(main, "<i>key</i>", "device_success.html: the replaced key's name is escaped; the page")
        self.says(words, "If the terminal already stopped waiting, no key is created. Run veilfile login again.",
                  "backend/templates/device/device_success.html")

    def test_the_denied_page(self):
        response = self.browser.post("/device/deny", {"user_code": self.start()["user_code"]})
        main = main_of(response.content.decode())
        self.says(main, "<h1>Terminal denied</h1>", "backend/templates/device/device_denied.html")
        self.says(text_of(main), "Denied. No key was issued. If you didn't start this request, rotate your keys from "
                  "the dashboard.", "backend/templates/device/device_denied.html")

    def test_the_error_page_is_its_title_and_its_message(self):
        response = self.browser.post("/device/approve", {"user_code": "NOPE-NOPE"})
        self.assertEqual(response.status_code, 400)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Invalid code</h1>", "backend/templates/device/device_error.html")
        self.says(text_of(main), "We couldn't find that device code. Check the terminal and try again.",
                  "backend/templates/device/device_error.html")
        self.omits(main, "Re-send the verification email", "device_error.html without show_resend; the page")

    def test_the_unverified_refusal_offers_the_resend_link(self):
        grant = self.start()
        response = self.unverified_browser().post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        self.assertEqual(response.status_code, 403)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Verify your email first</h1>", "backend/templates/device/device_error.html")
        self.assertRegex(main, rf'<p class="form-links"><a class="label interactive" '
                         rf'href="{re.escape(reverse("email-verification"))}">Re-send the verification email</a></p>',
                         "device_error.html shows the resend link, a secondary action (a.label.interactive in a "
                         "p.form-links), when show_resend is set")
        self.assertEqual(DeviceCode.objects.get(user_code=grant["user_code"].replace("-", "")).status,
                         DeviceCode.STATUS_PENDING, "the refusal grants nothing")

    def test_the_throttled_page_is_the_error_page_and_holds_no_form(self):
        response = self.every_page()["too many"]
        self.assertEqual(response.status_code, 429)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Too many attempts</h1>", "backend/templates/device/device_error.html")
        self.says(main, "Wait 10 minutes", "backend/templates/device/device_error.html")
        self.omits(main, "csrfmiddlewaretoken", "a refusal page")


class OAuthRefusalOnTheDeviceErrorPageTests(tests_oauth.OAuthBaseTestCase):
    """device/device_error.html is also the OAuth consent's 403 (VeilfileAuthorizationView.dispatch in drop_core/oauth.py)."""

    longMessage = False

    def test_an_unverified_visitor_gets_the_shared_page_in_the_oauth_words(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        client_id = self._register_client().json()["client_id"]
        self.client.force_login(self.user)
        _verifier, challenge = tests_oauth._pkce_pair()
        response = self.client.get(tests_oauth._AUTHORIZE_URL, tests_oauth._authorize_params(client_id, challenge))
        self.assertEqual(response.status_code, 403)
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/device/device_error.html (shared with OAuth) must extend layouts/form.html")
        html = response.content.decode()
        main = main_of(html)
        self.assertIn("<h1>Verify your email first</h1>", main, "the OAuth refusal's heading")
        self.assertIn("Authorizing an app requires a verified email address, so we can reach you if the grant is ever "
                      "abused. Check your inbox for the verification link, or send a fresh one.", text_of(main),
                      "the OAuth refusal's sentence (backend/drop_core/oauth.py)")
        self.assertIn(f'href="{reverse("email-verification")}"', main, "the OAuth refusal links the resend page")
        for word in ("terminal", "veilfile login", "device"):
            self.assertNotIn(word, text_of(main).lower(),
                             f"device_error.html is shared with the OAuth consent: nothing on it may say '{word}'")
        self.assertNotIn("device.css", html, "the shared refusal page draws nothing of the device flow's, so it loads "
                         "nothing of it (backend/templates/device/device_error.html)")
        self.assertIn('<meta name="robots" content="noindex">', html, "the OAuth refusal is kept out of search")
        self.assertIn("<title>Verify your email first | Veilfile</title>", html, "the OAuth refusal's title")


class DeviceAddressAndRoundTripTests(DeviceCase):
    def test_every_spelling_of_every_address_answers(self):
        for path in ("/device", "/device/"):
            with self.subTest(path=path):
                grant = self.start()
                self.says(self.browser.get(path).content.decode(), "<h1>Enter device code</h1>", f"GET {path}")
                self.says(self.browser.get(path, {"user_code": grant["user_code"]}).content.decode(),
                          "<h1>Approve terminal login</h1>", f"GET {path}?user_code=...")
        for path in ("/device/approve", "/device/approve/"):
            with self.subTest(path=path):
                response = self.browser.post(path, {"user_code": self.start()["user_code"], "confirm": "yes"})
                self.says(response.content.decode(), "<h1>Terminal approved</h1>", f"POST {path}")
        for path in ("/device/deny", "/device/deny/"):
            with self.subTest(path=path):
                response = self.browser.post(path, {"user_code": self.start()["user_code"]})
                self.says(response.content.decode(), "<h1>Terminal denied</h1>", f"POST {path}")

    def test_the_pages_use_the_names_not_a_spelling(self):
        self.assertEqual((reverse("device"), reverse("device-approve"), reverse("device-deny")),
                         ("/device", "/device/approve/", "/device/deny/"),
                         "backend/config/urls.py: the device templates use {% url %} on these three names")

    def test_a_signed_out_visit_to_the_clis_link_returns_to_it_after_sign_in(self):
        grant = self.start()
        link = f"/device?user_code={grant['user_code']}"
        visitor = APIClient()
        bounce = visitor.get(link)
        self.assertEqual(bounce.status_code, 302)
        self.assertEqual(bounce["Location"], f"/accounts/login/?next={quote(link, safe='/')}",
                         "login_required sends the visitor to sign in, with the device link as next")
        signed_in = visitor.post(bounce["Location"], {"username": "dev", "password": "pw12345678"})
        self.assertEqual(signed_in.status_code, 302, "sign in at the address the bounce gave: the form has no action")
        self.assertEqual(signed_in["Location"], link, "sign in returns to /device?user_code=...: the sign-in form must "
                         "post to its own address and keep ?next= (backend/templates/registration/login.html)")
        landed = visitor.get(signed_in["Location"])
        self.assertEqual(landed.status_code, 200)
        main = main_of(landed.content.decode())
        self.says(main, "<h1>Approve terminal login</h1>", "the page the CLI's link opens after sign in")
        self.says(main, grant["user_code"], "the page the CLI's link opens after sign in")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_device_pages -v 2 > /tmp/tests_device_pages.out 2>&1; grep -E '^(FAIL|ERROR): |^Ran |^FAILED|^OK' /tmp/tests_device_pages.out; cd ..` (every class sets `longMessage = False`, so each failure prints only its own message; the output file is about 80 KB, mostly tracebacks, and the `grep` lists one line per failure.)

Expected: `Ran 41 tests` and `FAILED (failures=72)`, with no errors. That is 27 of the 41 test methods; the counts are per page and per address because the loops use subtests. Task 1's layout and components are in place and the device templates are still the old documents. The failures, by message:
- 30: for each of the ten states (`enter`, `enter, wrong code`, `consent`, `consent, refused`, `invalid`, `expired`, `unverified`, `success`, `denied`, `too many`), `test_every_page_is_drawn_by_the_form_layout` ("enter: its template in backend/templates/device/ must extend layouts/form.html"), `test_every_page_has_its_title` ("... must still hold '<title>Enter device code | Veilfile</title>'") and `test_every_page_is_kept_out_of_search` ("... must still hold '<meta name="robots" content="noindex">'").
- 5: `test_each_page_extends_the_form_layout_and_nothing_old_is_left`, once per page ("backend/templates/device/device_consent.html must start with {% extends "layouts/form.html" %}").
- 6: `test_every_spelling_of_every_address_answers`, one per address ("GET /device must still hold '<h1>Enter device code</h1>'", "POST /device/deny/ must still hold '<h1>Terminal denied</h1>'").
- 5: `test_every_button_is_the_forms_main_action_with_its_ink_wash`, four states and the count ("enter: the 'Continue' button must be <button class=\"action interactive\" type=\"submit\">").
- 4: `test_a_typed_code_reaches_the_consent_page_in_any_spelling_the_form_can_send`, one per spelling (each "must still hold 'Approve terminal login'").
- 3: `test_nothing_in_main_carries_an_aria_label` for `consent`, `consent, refused` and `success` ("<main> (a visible label ...) must not hold 'aria-label'").
- 2: the check mark, `test_no_page_has_a_check_mark_character` for `success` ("... the page must not hold '&#10003;'") and `test_no_template_holds_a_check_mark` for `device_success.html`.
- 1: `test_only_the_empty_code_entry_takes_focus` for `enter, wrong code` ("autofocus appears 1 times; only 'enter' (no code typed) sets it").
- 3, one each: the stylesheet tests, `test_the_stylesheet_is_tokens_only_and_has_no_em_dash`, `test_the_stylesheet_styles_only_what_the_forms_pieces_do_not` and `test_what_a_stranger_or_a_user_wrote_wraps_so_a_phone_never_scrolls_sideways` ("backend/static/css/pages/device.css is the device pages' stylesheet").
- 13, one message each: `test_the_form_is_the_get_that_the_clis_link_is` ("the code input is the field component's mono variant: pass mono=True ..."), `test_with_no_code_the_field_is_focused_and_the_intro_is_read_with_it` (one `aria-describedby`), `test_after_a_wrong_code_the_sentence_is_tied_to_the_field_and_nothing_takes_focus` ("a wrong code marks the field invalid"), `test_the_unverified_notice_is_a_status_line_that_links_the_resend_page` and `test_an_unverified_visitor_is_warned_on_the_approval_too` (`<p class="notice" role="status">`), `test_the_code_is_text_under_a_visible_label`, `test_the_form_posts_the_same_fields_to_the_same_two_addresses` (the `check__input` confirmation), `test_at_the_cap_each_key_is_a_radio_row_with_its_id_as_the_value` (the fieldset and its legend), `test_the_success_page_draws_its_mark_and_says_it_with_words`, `test_the_denied_page`, `test_the_unverified_refusal_offers_the_resend_link`, `test_a_signed_out_visit_to_the_clis_link_returns_to_it_after_sign_in` (it signs in and returns correctly; it fails on the new heading) and `test_an_unverified_visitor_gets_the_shared_page_in_the_oauth_words` ("backend/templates/device/device_error.html (shared with OAuth) must extend layouts/form.html").

The other 14 tests pass already and must keep passing, because they hold what must not change: `test_a_code_typed_with_markup_comes_back_as_text`, `test_a_key_name_is_shown_as_text`, `test_a_page_links_the_device_stylesheet_exactly_when_it_uses_a_class_of_it` (no old page uses a `device-*` class), `test_a_refused_approval_says_why_first_and_keeps_the_choice`, `test_below_the_cap_there_is_no_key_to_replace`, `test_every_state_answers_as_it_did`, `test_no_page_shows_a_template_comment`, `test_the_error_page_is_its_title_and_its_message`, `test_the_five_pages_and_the_summary_are_the_folders_files`, `test_the_pages_use_the_names_not_a_spelling`, `test_the_throttled_page_is_the_error_page_and_holds_no_form`, `test_the_warning_and_the_grant_summary_keep_their_words`, `test_what_the_terminal_sent_is_escaped_and_cut` and `test_who_asked_is_four_terms_and_their_values`.

- [ ] **Step 2: The stylesheet**

Create `backend/static/css/pages/device.css`:

```css
/* The device flow's approval and its success (spec 4.7), in the form layout's column. Only what the layout, the field, the check
   rows, the actions and the notice do not already give them: the code shown large, who asked, what the key can do and the mark of
   an approved login. Colours come only from tokens.css. The code entry, the denial and the refusal (which the OAuth consent
   shares) need none of it and do not link this file. The page's one h1 and its paragraphs, lists and terms are the layout's. */

/* What matters most on the approval: only approve a login you started. An ink rule beside it, no box and no colour. */
.device-warning {
  margin: 0 0 var(--space-4);
  padding-left: var(--space-3);
  border-left: 2px solid var(--ink);
}

/* The code, large, between two hairlines. A visible label above it says what it is: a div with no role cannot carry a name. */
.device-code {
  margin: var(--space-4) 0;
  padding: var(--space-3) 0;
  border-block: 1px solid var(--hair);
}

.device-code .label {
  margin: 0 0 var(--space-2);
}

.device-code__value {
  margin: 0;
  font-family: var(--font-mono);
  font-size: clamp(1.5rem, 8vw, 2.25rem); /* nine characters at .3em take 207px at 320px wide, in a column of 288px */
  line-height: 1.2;
  letter-spacing: .3em;
  color: var(--ink);
  overflow-wrap: anywhere;
}

/* Who asked: a term above its value on a phone, side by side from 40rem. A long client string or address wraps. */
.device-request {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-1) var(--space-4);
  margin: 0 0 var(--space-4);
}

.device-request dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

@media (min-width: 40rem) {
  .device-request {
    grid-template-columns: max-content minmax(0, 1fr);
  }

  .device-request dt {
    margin-top: 0;
  }
}

/* What the key can do (device/includes/grant_summary.html): the list and the closing sentence are quieter than the lead. */
.device-grant {
  margin: 0 0 var(--space-4);
}

.device-grant ul,
.device-grant__foot {
  color: var(--muted);
}

/* An approved login's mark, drawn: two borders turned an eighth of the way round. The check mark character this page used
   to hold is in none of the site's three fonts. It is decoration (aria-hidden): the heading says the same in words. */
.device-mark {
  display: block;
  width: .6rem;
  height: 1.2rem;
  margin: var(--space-2) 0 var(--space-4) .5rem;
  border: solid var(--ink);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
```

(Every colour is a token and no line holds an em dash: `pages.tests_site_rules` and the Step 1 tests check both. Nothing here positions anything fixed, sets a `z-index` or makes `<main>` a backdrop root. Nothing sets a font size on an input, so Task 1's 16 px minimum on phones stands. There is no rule for the field, the check rows, the actions or the notice: a Step 1 test fails one, so every form page keeps the same ones. The wrapping of a long client string, address or key name is the layout's (`.form-page` wraps anywhere) plus the grid cell's own `min-width: 0` here.)

- [ ] **Step 3: Pass each page's error as a list**

In `backend/drop_core/device.py`, make two edits.

In `device_page`, replace this exact text:

```python
    if problem == "invalid":
        return render(
            request,
            "device/device_enter_code.html",
            {
                "error": "We couldn't find that code.",
                "user_code": raw_code,
                "email_verified": is_email_verified(request.user),
            },
        )
```

with:

```python
    if problem == "invalid":
        error = "We couldn't find that code."
        return render(
            request,
            "device/device_enter_code.html",
            {
                "error": error,
                # The same sentence as a list: components/field.html takes its errors as one.
                "errors": [error],
                "user_code": raw_code,
                "email_verified": is_email_verified(request.user),
            },
        )
```

In `_render_consent`, replace this exact text:

```python
            "selected_key": selected_key,
            "error": error,
```

with:

```python
            "selected_key": selected_key,
            "error": error,
            # The same sentence as a list, for components/form_errors.html (it draws nothing for an empty one).
            "errors": [error] if error else [],
```

(`error` stays: the templates switch on it, and `_render_consent`'s callers pass it. Nothing else in the file changes, and the file still never says `REMOTE_ADDR`.)

- [ ] **Step 4: The five pages and the grant summary**

Each page is a whole file. Each `{# #}` comment stays on one line, because Django 6.1 prints one that spans lines as text (a Step 1 test looks for `{#` in every page).

Replace the whole of `backend/templates/device/device_enter_code.html` (57 lines of standalone page) with:

```django
{% extends "layouts/form.html" %}
{# Form page: the device code entry (spec 4.7). The words are the old page's until the voice pass (PR 7). The form is a GET to /device: the link the CLI opens is /device?user_code=XXXX-XXXX, so typing the code here must reach the same address. #}
{% block title %}Enter device code | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Enter device code</h1>
{% if not email_verified %}
<p class="notice" role="status"><strong>Verify your email first.</strong> Approving a terminal key needs a verified email address. <a href="{% url 'email-verification' %}">Re-send the verification email</a>.</p>
{% endif %}
{% if error %}
<p>This code doesn't match a pending login. Codes expire after 10 minutes and work once, so run <code>veilfile login</code> again for a fresh one, or re-type the code your terminal shows.</p>
{% else %}
<p id="device-intro">Enter the code shown in your terminal. Only enter a code from a <code>veilfile login</code> you ran yourself.</p>
{% endif %}
<form method="get" action="{% url 'device' %}">
{# Focus goes to the field only when there is no error: this page exists to take one code, and with the intro (read with the field, by aria-describedby) nothing is skipped. After a wrong code the visitor reads the sentence first, and a phone's keyboard would cover it. #}
{% if error %}
{% include "components/field.html" with id="id_user_code" name="user_code" label="Device code" type="text" value=user_code autocomplete="off" required=True mono=True errors=errors extra='placeholder="XXXX-XXXX" spellcheck="false"' only %}
{% else %}
{% include "components/field.html" with id="id_user_code" name="user_code" label="Device code" type="text" autocomplete="off" required=True autofocus=True mono=True extra='aria-describedby="device-intro" placeholder="XXXX-XXXX" spellcheck="false"' only %}
{% endif %}
<button class="action interactive" type="submit">Continue</button>
</form>
{% endblock %}
```

(The field is written twice because `autofocus` must be true exactly when there is no `error`, which a template cannot negate inside `with`. `mono=True` gives the code its own letters, and `placeholder` and `spellcheck` are pinned attributes, so they ride in `extra`, which is a string literal as Task 1's convention test requires. With no code the field gets `autofocus` and, through `extra`, `aria-describedby="device-intro"`: the component writes its own `aria-describedby` only for help or errors, so the field has exactly one, and a Step 1 test fails if that stops being true. After a wrong code it gets the typed `value`, the `errors` list and no focus, and the component ties the sentence to the field (`aria-invalid`, `aria-describedby`). A wrong code comes back in `value`, which the component escapes; a Step 1 test types markup and looks. Continue is Task 1's main action and needs no wrapper.)

Replace the whole of `backend/templates/device/device_consent.html` (119 lines) with:

```django
{% extends "layouts/form.html" %}
{% load static %}
{# Form page: the approval of a terminal's login (spec 4.7). The words are the old page's until the voice pass (PR 7). Approve and Deny are one form with the same fields; Deny only changes where it posts (formaction). #}
{% block title %}Approve terminal login | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block styles %}{{ block.super }}<link rel="stylesheet" href="{% static 'css/pages/device.css' %}">{% endblock %}
{% block form_page %}
<h1>Approve terminal login</h1>
{% include "components/form_errors.html" with errors=errors only %}
<p>A terminal is asking for an API key to your workspace.</p>
<p class="device-warning"><strong>Only approve if you just ran <code>veilfile login</code> yourself</strong> and the code below matches the one in your terminal. If someone sent you this link or asked you to approve it, click Deny: approving would give them access to your files.</p>
{% if not email_verified %}
<p class="notice" role="status"><strong>Verify your email first.</strong> Approving this terminal key will be refused until your email address is verified. Check your inbox, or <a href="{% url 'email-verification' %}">re-send the verification email</a>.</p>
{% endif %}
{# A div cannot carry an aria-label (axe: aria-prohibited-attr), so the code has a visible label and its text is its name. #}
<div class="device-code">
<p class="label">Device code</p>
<p class="device-code__value">{{ device_code.user_code_display }}</p>
</div>
{# requester_* come from the unauthenticated request: auto-escaped, shown as given. #}
{# requester_ip is email_verification.client_ip, only as trustworthy as its proxy handling. #}
<dl class="device-request">
<dt>Requested</dt>
<dd>{{ device_code.created_at|timesince }} ago</dd>
<dt>From IP</dt>
<dd>{{ device_code.requester_ip|default:"unknown" }}</dd>
<dt>Client</dt>
<dd>{% if device_code.requester_user_agent %}<code>{{ device_code.requester_user_agent|truncatechars:120 }}</code>{% else %}unknown{% endif %}</dd>
<dt>Signed in as</dt>
<dd>{{ account }}</dd>
</dl>
{% include "device/includes/grant_summary.html" with key_cap=key_cap only %}
<form method="post" action="{% url 'device-approve' %}">
{% csrf_token %}
<input type="hidden" name="user_code" value="{{ device_code.user_code_display }}">
{% if replacement_required %}
<fieldset>
<legend>Choose a key to replace</legend>
<p>Your plan allows {{ key_cap }} active API key{{ key_cap|pluralize }}, and this workspace has {{ replaceable_keys|length }}. The key you choose is revoked when the terminal receives its new key; anything still using it stops working.</p>
{% for key in replaceable_keys %}
{# The value is the key's id and the re-checked radio is found by comparing it with the posted string: keep key.id|stringformat:"d". #}
<label class="check"><input class="check__input" type="radio" name="replace_key" value="{{ key.id }}" required{% if selected_key == key.id|stringformat:"d" %} checked{% endif %}><span class="check__text">{{ key.name }}<span class="check__detail"><code>{{ key.key_prefix }}…</code> · created {{ key.created_at|date:"M j, Y" }} · last used {% if key.last_used_at %}{{ key.last_used_at|date:"M j, Y" }}{% else %}never{% endif %}</span></span></label>
{% endfor %}
</fieldset>
{% endif %}
<label class="check"><input class="check__input" type="checkbox" name="confirm" value="yes" required><span class="check__text">I just ran <code>veilfile login</code> myself, and this code matches my terminal.</span></label>
<div class="form-actions">
<button class="action interactive" type="submit">Approve</button>
<button class="action interactive" type="submit" formaction="{% url 'device-deny' %}" formnovalidate>Deny</button>
</div>
</form>
{% endblock %}
```

(`{% csrf_token %}` stays in the page's own form: a component included with `only` would not see it. The keys and the confirmation are Task 1's check rows, each one `<label>` around its input, so each has its visible label and no `id`; the pinned literals `type="radio" name="replace_key" value="N"` and `type="checkbox" name="confirm" value="yes" required` stay whole. `selected_key == key.id|stringformat:"d"` is the old comparison, unchanged. Approve and Deny are in Task 1's `.form-actions`, Deny keeping `formaction` and `formnovalidate`. This page and the success page are the two that draw what the layout lacks, so they link the stylesheet.)

Replace the whole of `backend/templates/device/device_success.html` (39 lines) with:

```django
{% extends "layouts/form.html" %}
{% load static %}
{# Form page: the result of an approval (spec 4.7). The words are the old page's until the voice pass (PR 7). The mark is drawn in CSS: the check mark character this page had is in none of the site's fonts. #}
{% block title %}Terminal approved | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block styles %}{{ block.super }}<link rel="stylesheet" href="{% static 'css/pages/device.css' %}">{% endblock %}
{% block form_page %}
<span class="device-mark" aria-hidden="true"></span>
<h1>Terminal approved</h1>
<p>Approved. Return to your terminal: it receives its key on its next check, within a few seconds. You can close this window.</p>
{% if replace_key %}
<p>When it does, <strong>{{ replace_key.name }}</strong> (<code>{{ replace_key.key_prefix }}…</code>) is revoked.</p>
{% endif %}
<p>If the terminal already stopped waiting, no key is created. Run veilfile login again.</p>
{% endblock %}
```

Replace the whole of `backend/templates/device/device_denied.html` (32 lines) with:

```django
{% extends "layouts/form.html" %}
{# Form page: the result of a denial (spec 4.7). The words are the old page's until the voice pass (PR 7). #}
{% block title %}Terminal denied | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>Terminal denied</h1>
<p>Denied. No key was issued. If you didn't start this request, rotate your keys from the dashboard.</p>
{% endblock %}
```

Replace the whole of `backend/templates/device/device_error.html` (39 lines) with:

```django
{% extends "layouts/form.html" %}
{# Form page: a refusal. Shared: the device flow's own errors and the OAuth consent's unverified-email 403 (drop_core/oauth.py) both render it, with error_title, error_message and show_resend, so nothing on it may say device, terminal or app. #}
{% block title %}{{ error_title }} | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block form_page %}
<h1>{{ error_title }}</h1>
<p>{{ error_message }}</p>
{% if show_resend %}
<p class="form-links"><a class="label interactive" href="{% url 'email-verification' %}">Re-send the verification email</a></p>
{% endif %}
{% endblock %}
```

(This page is also the OAuth consent's 403, which passes the same three names with its own words. It holds no word of its own and nothing of the device flow's, so it links no stylesheet and looks like the rest through the layout alone.)

Replace the whole of `backend/templates/device/includes/grant_summary.html` (18 lines) with:

```django
{# Approval-summary block for the device flow consent page. #}
{# Keep it accurate: it is the user's only description of the key's power. #}
<div class="device-grant">
  <p>Approving creates an API key for your workspace. Whoever holds it can:</p>
  <ul>
    <li>Upload files and create share links</li>
    <li>List every artifact in the workspace and download each one through its link</li>
    <li>Revoke artifacts</li>
    <li>List the workspace's API keys and revoke any of them</li>
    <li>Disconnect every app connected to your account (OAuth / MCP clients)</li>
    <li>See the account email and plan usage</li>
  </ul>
  <p class="device-grant__foot">
    The key does not expire: it works until you revoke it in the dashboard.
    {% if key_cap %}It counts toward your plan's limit of {{ key_cap }} API key{{ key_cap|pluralize }}.{% endif %}
    It is sent only to the terminal; this browser never sees it.
  </p>
</div>
```

(Every sentence is the old one, on the same lines, with new class names. The include in `device_consent.html` now passes `key_cap` and `only`, so the partial reads nothing else from the page.)

- [ ] **Step 5: Run the new tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_device_pages`
Expected: `Ran 41 tests` and `OK`.

- [ ] **Step 6: Hold the new module to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a test class whose `longMessage` is not `False`, and its message names the file and the fix; a failure that compares a page would otherwise print the page before the message. Append this module to its list, which ends where Task 3 left it.

Run: `grep -c '"drop_core.tests_reset_verify_pages")' backend/pages/tests_site_rules.py`
Expected: `1`. (If it prints `0`, an earlier task left the list ending differently: append `"drop_core.tests_device_pages"` before the closing parenthesis of `READING_TEST_MODULES` instead, on a line of its own, and go on.)

In `backend/pages/tests_site_rules.py`, replace this exact text (the end of `READING_TEST_MODULES`):

```python
"drop_core.tests_reset_verify_pages")
```

with:

```python
"drop_core.tests_reset_verify_pages",
                        "drop_core.tests_device_pages")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules`
Expected: `OK`. The five templates are under the shell rules from this step on, with no list to edit: `shell_templates()` picks up every template that extends a layout, so `pages.tests_site_rules` (no inline `style`, components included with `only`, no `<header>` or `<footer>` in `<main>`, every `{% static %}` path exists) reads them.

- [ ] **Step 7: Run the tests that pin the device flow, and look for any that name the old markup**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_device drop_core.tests_auth_carryover drop_core.tests.DeviceFlowTests drop_core.tests_oauth drop_core.tests_client_ip_header pages.tests_forms pages.tests_site_rules pages.tests_content pages.tests_catalog`
Expected: `OK`. `pages.tests_forms` scans every template for an `extra` that is not a literal (the device entry passes two literals), and `pages.tests_content` finds every non-ASCII character of the five templates in all three fonts (here U+2026 and U+00B7 in the key rows and the success sentence; the titles are plain ASCII now).

No existing test is edited. The tests that touch these pages pin phrases, status codes and a few attribute literals, and the new markup keeps each of them:
- `DeviceFlowTests` (`drop_core/tests.py`): `ABCD-EFGH` and `Approve` on the approval page, `Enter the code shown in your terminal.` on the empty entry page, `Verify your email first` in the 403, `Approved`, `Denied` and `This code has expired` in the results. All are text the templates still write.
- `test_consent_at_cap_lists_keys_to_replace` pins `name="replace_key" value="{pk}"`. The radio is `<input class="check__input" type="radio" name="replace_key" value="N" required>`: the pair stays whole and in order. `test_consent_form_requires_the_confirmation_checkbox` pins `type="checkbox" name="confirm" value="yes" required` (the approval is refused without the confirmation) and `formnovalidate` (Deny skips the browser's required-field check): the checkbox is `<input class="check__input" type="checkbox" name="confirm" value="yes" required>` and Deny keeps its `formnovalidate`.
- `test_requester_fields_are_escaped` pins `&lt;script&gt;` and no raw `<script>alert(1)</script>`: the client string is still auto-escaped inside `<code>` after `truncatechars:120`. The new `test_what_the_terminal_sent_is_escaped_and_cut` holds the same reason on the new markup with an `<img onerror>` string and the cut, and `test_a_key_name_is_shown_as_text` and `test_a_code_typed_with_markup_comes_back_as_text` hold the other two places a stranger's or a user's text is drawn.
- `test_unknown_code_from_a_link_says_so_instead_of_asking_to_enter_it` pins `doesn't match a pending login` (an apostrophe in template text, which stays literal: do not move it into a context variable, which would escape it) and the absence of `Enter the code shown in your terminal.`; the new heading `Enter device code` does not contain that sentence.
- `test_consent_page_warns_and_shows_who_asked` pins `Only approve if you just ran`, `matches the one in your terminal` and `click Deny`, each inside one line of the paragraph, which stays one line. `test_grant_summary_*` pin the partial's sentences, kept as they were.
- `DeviceFlowEarlyVerifyNoticeTests` pins `Verify your email first` present for an unverified visitor and absent for a verified one, on the entry and approval pages: the `{% if not email_verified %}` blocks are unchanged and no other text on the pages holds the phrase.

Run: `grep -rn -E "device_enter_code|device_consent|device_success|device_denied|device_error|device/includes|aria-label=.Device code|10003|keyopt|class=.(approve|deny)." backend site-tests --include='*.py' | grep -v tests_device_pages`
Expected: only the render calls, six in `backend/drop_core/device.py` and one in `backend/drop_core/oauth.py` (`"device/device_error.html"`). No test names a template, a class or a string of the old pages, and the browser checks have no device page (`site-tests/run_site_checks.py` has no `/device` entry; Task 7 adds them), so nothing else changes.

Run: `grep -n "REMOTE_ADDR" backend/drop_core/device.py`
Expected: no output.

Run: `grep -n -E "device flow|OAuth consent" AGENTS.md`
Expected: no output. AGENTS.md needs no edit here: the repo-map sentence Task 2 wrote names an older page by its include of `includes/ambient.html` and not by a list, so it stays true as these five pages stop including it. (If a line prints, that sentence still lists pages by hand and is false from this step on: rewrite it so that it names no page, only the include.)

- [ ] **Step 8: Run the whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 4 minutes).

- [ ] **Step 9: Commit**

```bash
git add backend/drop_core/device.py backend/drop_core/tests_device_pages.py backend/static/css/pages/device.css \
  backend/pages/tests_site_rules.py \
  backend/templates/device/device_enter_code.html backend/templates/device/device_consent.html \
  backend/templates/device/device_success.html backend/templates/device/device_denied.html \
  backend/templates/device/device_error.html backend/templates/device/includes/grant_summary.html
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Device flow pages on the form layout: the code entry, the approval, its results and the refusal the OAuth consent shares are shell pages made of the form layout's pieces, the code has a visible label instead of an aria-label on a div, the check mark is drawn in CSS, and tests hold the fields, addresses, escaping and the CLI link"
```

---

### Task 5: OAuth consent on the form layout

**Files:**
- Create: `backend/static/css/pages/consent.css`, `backend/drop_core/tests_oauth_consent.py`
- Modify: `backend/templates/oauth/authorize.html` (rewritten whole), `backend/templates/oauth/includes/grant_summary_oauth.html` (rewritten whole), `backend/drop_core/oauth.py`, `docs/API.md`
- Modify (tests): `backend/pages/tests_site_rules.py` (one name added to `READING_TEST_MODULES`, Step 5). No existing test pins the old markup of the page; fact 7 lists the ones that read it and why they keep passing.

**Interfaces:**
- Consumes:
  - Task 1: `templates/layouts/form.html` (extends `shell.html`; body class `layout-form`; `<div class="form-page">{% block form_page %}{% endblock %}</div>` inside `<main>`). Its markup contract: the main action is `<button class="action interactive" type="submit">`; two actions of equal weight go in `<div class="form-actions">`, each item a `<form>` that holds one `.action` (side by side where there is room, one under the other where there is not, all one width); the bare `h1`, `p`, `ul`, `li`, `strong` and `code` inside `.form-page` are styled where they stand, and `.label` is the mono label. This task takes no component (`field`, `form_errors`), because the page has no field, no visible input and no error line of its own.
  - Task 4: `templates/device/device_error.html` on the form layout, with the context it has always had: `error_title` (its `h1` and its title), `error_message` (a paragraph) and `show_resend` (a link to the `email-verification` page), at the status the caller chooses. The device flow and this view both render it.
  - `shell.html`'s blocks `title`, `meta` and `styles`. All three are empty there; `meta` sits outside the `shell_page` condition, so it renders on a page with no registry entry.
  - `pages.tests_site_rules.READING_TEST_MODULES`, the tuple of test modules held to `longMessage = False` (`ReadableFailuresTests`), which Tasks 1 to 4 appended their modules to. Step 5 appends this task's.
- Produces:
  - `oauth/authorize.html` on the form layout: titled `Authorize app | Veilfile`; one `h1` ("Authorize this app"); the sentences it had; the app's name escaped (or "Unnamed app" when the client registered none); two forms with hidden fields that carry no ids and no `action`, in one `<div class="form-actions">`, each holding one `<button class="action interactive" type="submit">` (Deny first, as the old page had it, then Authorize); `<meta name="robots" content="noindex">`; and none of the old includes (`theme_boot.html`, `ambient.html`, `menu.html`: Task 8 deletes them once every page has moved).
  - `oauth/includes/grant_summary_oauth.html`: the same words; the classes are now `grant` and `grant__head`.
  - `static/css/pages/consent.css`: `.consent`, `.consent__app`, `.consent__label`, `.consent__name`, `.grant__head` and the lists in `.grant`. It is a page stylesheet, linked from the page's `styles` block as `pages/catalog.html` links `css/pages/catalog.css`, not a component: nothing else uses these rules, so it has no catalog entry. The row of two answers is Task 1's `.form-actions`; this file styles none of it.
  - `VeilfileAuthorizationView._error_page(title, message, status, show_resend=False)`, and `VeilfileAuthorizationView.render_to_response`, which shows that error page for a request django-oauth-toolkit refuses (an unknown or missing `client_id`, a `redirect_uri` the client did not register) and for a POST with fields missing. The consent page is built only for a valid client.
  - `drop_core/tests_oauth_consent.py`, listed in `READING_TEST_MODULES`: the tests the map found missing (the escaped name, the unverified sentence, the redirect host, Deny, CSRF, `X-Frame-Options`, the error branch, the OAuth 403 page, the sign-in round trip) and the conventions of the page's own files, among them that Deny and Authorize are equal actions with Task 1's classes.

Facts the steps rely on (all checked in the code):
1. **django-oauth-toolkit renders `template_name` for three different things.** `AuthorizationView.get` renders it with `get_context_data(**kwargs)` for the consent page; `BaseAuthorizationView.error_response` renders it with the bare dict `{"error": <oauthlib error>, "url": ...}` and the error's status (400) when the error is a `FatalClientError` (no valid `client_id`, or a `redirect_uri` the client did not register: it must not redirect there); `FormView.form_invalid` renders it for a POST that fails `AllowForm` (status 200). The last two have no `application` in the context. The old template had no `error` branch (DOT's own `authorize.html` has), so it drew a consent card with an empty app name, an empty host and buttons that carry only a token. `render_to_response` is the one place all three pass, and `application` is what tells the consent page from the other two. An error DOT can report to the app (`invalid_scope`) is a redirect and never reaches it.
2. **The text of a fatal error is fixed.** oauthlib's descriptions for these errors are constants ("Invalid client_id parameter value.", "Missing client_id parameter.", "Mismatching redirect URI.", "Invalid redirect URI.", "Missing redirect URI.", "Unable to parse query string", "Duplicate client_id parameter." and the like), so the error page may show the description and still echoes nothing from the request. One of them has no full stop, hence `rstrip('.')` before the sentence is built.
3. **DOT's `{{ field }}` for the hidden fields gives every input an id** (`id_redirect_uri`, ...), and the page has the fields twice, so every id was used twice. The page writes the inputs itself (`name` and `value` only), from `field.html_name` and `field.value`; the names are the ones `AllowForm` has and the values are what `{{ field }}` printed (`None` becomes an empty string, as before: an input with no value posts an empty string).
4. **Each registered client gets a registration access token** with no user (`DCR_REGISTRATION_SCOPE`), so a test that wants "no token was issued" counts the tokens that have a user.
5. **Two places where the old words meet the new conventions.** The `<title>` becomes `Authorize app | Veilfile`, as every page of the new site names itself (the voice rules ban the dash, so no page of the new site keeps ` — Veilfile`). The one em dash in the page's body, "Unverified app — this name is what the app claims to be...", becomes a colon, the only change to a sentence: every other word is as it was. With both gone the page and its partial hold no em dash, and the voice test reads them whole.
6. **`{% csrf_token %}` is in each form of the page itself.** A component included with `only` would render no token (the context has none), and the consent page has no component. The proof script (`backend/scripts/oauth_mcp_proof.py`) posts the value of the `csrftoken` cookie that the GET sets by rendering the token, so the page must keep rendering it.
7. **Nothing but the view renders the page or its partial.** The tests that read them: `drop_core.tests_fix_auth.ConsentCopyTests` (the words "Connected apps", "account email", "cannot create, list, or revoke API keys", and not "expires automatically": all kept), `CredentialManagesOnlyItselfTests.test_consent_copy_matches` (renders the partial by its path with no context: "disconnect your other apps", kept) and `drop_core.tests_oauth` (posts the fields directly; `VerificationGateTests` asserts the 403 status only). The old-markup tests (`tests_menu`, `tests_ambient` and the old methods of `AccessibilityTests`) never rendered this page, and Task 3 retired them.

Decisions made here:
- **Deny and Authorize are two equal actions**, not a main action and a quieter one. Task 1's markup contract names this pair for `.form-actions`, and a consent page should not nudge the person towards Authorize with a box for one answer and a faint link for the other. Both are `<button class="action interactive" type="submit">`, so both are hairline boxes the ink answers. The order is the old page's, Deny first, so the tab order and the reading order do not change.
- **The page's own stylesheet slims to what is the page's:** the row of two answers is Task 1's `.form-actions`, so `consent.css` has no rule for it.

- [ ] **Step 1: Check what this task builds on, and what reads the old page**

Run:

```bash
cd backend && ../venv/bin/python - <<'EOF'
from pathlib import Path
templates, css = Path("templates"), Path("static/css")
form = (templates / "layouts/form.html").read_text(encoding="utf-8")
error = (templates / "device/device_error.html").read_text(encoding="utf-8")
styles = "".join(path.read_text(encoding="utf-8") for path in css.rglob("*.css"))
for ok, what in (
    ("{% block form_page %}" in form, "layouts/form.html has the form_page block"),
    ("layout-form" in form, "layouts/form.html sets the body class layout-form"),
    ('extends "layouts/form.html"' in error, "device/device_error.html is on the form layout"),
    (".action" in styles, "a stylesheet styles .action"),
    (".form-actions" in styles, "a stylesheet styles .form-actions, the row of equal actions"),
):
    print("ok     " if ok else "MISSING", what)
EOF
```

Expected: five lines, each starting `ok`. If one says `MISSING`, stop: Task 1 (the layout, `.action` and `.form-actions`) or Task 4 (`device_error.html`) has not landed, or names the piece differently from the Interfaces above, and this task's templates and tests must follow the names that landed.

Run (from the repository root): `grep -rn -E "grant-head|app-name|appbox|class=\"(deny|approve)\"|Unverified app —|oauth/authorize|grant_summary_oauth" backend site-tests/run_site_checks.py --include='*.py'`
Expected: exactly two lines, `backend/drop_core/oauth.py` (`template_name = "oauth/authorize.html"`, near line 303) and `backend/drop_core/tests_fix_auth.py` (`render_to_string("oauth/includes/grant_summary_oauth.html")`, near line 1642). No test names a class, an id or a sentence of the old markup, so no existing test needs a new expectation. The tests that do read the page or its partial are listed in fact 7 above, and each keeps passing.

- [ ] **Step 2: Write the failing tests**

Create `backend/drop_core/tests_oauth_consent.py`. Every class sets `longMessage = False` (a failed `assertIn` on a whole page would otherwise print the page before the message), and every message names the file and the fix. Step 5 lists the module in `READING_TEST_MODULES`, which holds it to that. The classes:

- `ConsentPageTests`: the page on the form layout with one `h1` inside `<main>`, its title, noindex, the escaped name (shown once), an app with no name, a 255-character name, the unverified sentences, the redirect-host sentence (host and port only), the plain-words summary of `mcp:use`, the stylesheet, `X-Frame-Options: DENY`.
- `ConsentFormsTests`: two forms with no `action` carrying every field of DOT's `AllowForm` with the request's values, unique ids, a token in each; the two answers are equal actions (each form one `action interactive` button, the two in one `form-actions` row, Deny first); only Authorize posts `allow`; each form posted as the page has it (Authorize gives a code, Deny gives `access_denied` and no grant); a POST without the token is refused (a client with `enforce_csrf_checks`).
- `SignInReturnsToConsentTests`: a signed-out visit goes to sign in, signing in returns to the same request field for field, and the consent page shows.
- `RefusedRequestTests`: an unknown client, a missing client, an unregistered `redirect_uri` and a POST with fields missing show the error page (no form, no token, no consent wording, nothing echoed from the request, no grant); `invalid_scope` still redirects.
- `UnverifiedEmailTests`: the 403 page, whose `h1` is "Verify your email first", with the link to verify, and no grant.
- `ConsentTemplateTests`: the page is under the shell rules, each form renders its own token, the words keep the voice, the partial is ASCII, the stylesheet wraps what an app chooses, and every class the page uses has a rule.

```python
"""OAuth consent on the form layout (spec 4.7): the page names the app that is asking, frames it as unverified, says what
the mcp:use scope lets it do, and has two forms, Authorize and Deny, that post the fields django-oauth-toolkit expects.
A request DOT refuses, and the verified-email gate, show the error page and never a consent form.

Every class sets longMessage = False. A failed assertIn on a whole page would otherwise print the page before the message that
names the file and the fix; with it off only the message prints, so each message here also says what was found."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse
from oauth2_provider.models import get_access_token_model, get_grant_model

from drop_core import tests_oauth
from drop_core.models import EmailVerification
from pages.tests_chapters import BANNED
from pages.tests_site_rules import shell_templates

BACKEND = Path(settings.BASE_DIR)
PAGE = BACKEND / "templates" / "oauth" / "authorize.html"
PARTIAL = BACKEND / "templates" / "oauth" / "includes" / "grant_summary_oauth.html"
STYLES = BACKEND / "static" / "css" / "pages" / "consent.css"
TITLE = "<title>Authorize app | Veilfile</title>"


class _Page(HTMLParser):
    """The parts of a page these tests read: its h1 elements, its forms with their inputs and buttons, every id, its links and metas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1s, self.forms, self.ids, self.hrefs, self.metas = [], [], [], [], []
        self.in_main, self._h1, self._form, self._button = False, None, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "main":
            self.in_main = True
        elif tag == "h1":
            self._h1 = {"text": "", "in_main": self.in_main}
            self.h1s.append(self._h1)
        elif tag == "form":
            self._form = {"attrs": attrs, "inputs": [], "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(attrs)
        elif tag == "button" and self._form is not None:
            self._button = {"attrs": attrs, "text": ""}
            self._form["buttons"].append(self._button)
        elif tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        elif tag == "meta":
            self.metas.append(attrs)

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
        elif tag == "h1":
            self._h1 = None
        elif tag == "form":
            self._form = None
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        if self._h1 is not None:
            self._h1["text"] += data
        if self._button is not None:
            self._button["text"] += data


def read(response):
    found = _Page()
    found.feed(response.content.decode())
    found.close()
    return found


def form_with(found, label):
    """The form whose one button says `label`, and that button."""
    for form in found.forms:
        for button in form["buttons"]:
            if button["text"].strip() == label:
                return form, button
    raise AssertionError(f"backend/templates/oauth/authorize.html: no form has a button that says {label}; "
                         f"the page has {[b['text'].strip() for f in found.forms for b in f['buttons']]}")


def submission(form, button):
    """What a browser posts for this form: every named input, then the pressed button's name and value."""
    data = {field["name"]: field.get("value", "") for field in form["inputs"] if "name" in field}
    if "name" in button["attrs"]:
        data[button["attrs"]["name"]] = button["attrs"].get("value", "")
    return data


class ConsentTestCase(tests_oauth.OAuthBaseTestCase):
    longMessage = False
    NAME = "Some MCP app"

    def setUp(self):
        super().setUp()
        self.client_id = self._register_client(self.NAME).json()["client_id"]
        self.verifier, self.challenge = tests_oauth._pkce_pair()
        self.params = tests_oauth._authorize_params(self.client_id, self.challenge)

    def consent(self, client=None, params=None):
        """GET the consent page as the verified user."""
        client = client or self.client
        client.force_login(self.user)
        return client.get(tests_oauth._AUTHORIZE_URL, params or self.params)

    def no_grants(self, why):
        self.assertEqual(get_grant_model().objects.count(), 0, why)
        # The registration access token DOT makes for each registered client has no user; a token for the user would be a grant's.
        self.assertEqual(get_access_token_model().objects.filter(user__isnull=False).count(), 0, why)


class ConsentPageTests(ConsentTestCase):
    def test_the_page_is_a_form_page_with_one_h1_inside_main(self):
        response = self.consent()
        self.assertEqual(response.status_code, 200, f"the consent page answers 200 for a verified user and a valid request, not {response.status_code}")
        html = response.content.decode()
        self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                         "backend/templates/oauth/authorize.html: extend layouts/form.html, the narrow form column (body class layout-form)")
        found = read(response)
        self.assertEqual([(h1["text"].strip(), h1["in_main"]) for h1 in found.h1s], [("Authorize this app", True)],
                         f"backend/templates/oauth/authorize.html: one h1, \"Authorize this app\", inside <main>; found {found.h1s}")
        self.assertIn(TITLE, html, "backend/templates/oauth/authorize.html: the title is Authorize app | Veilfile, as every page of the shell names itself")

    def test_the_page_is_not_for_search_engines(self):
        found = read(self.consent())
        robots = [meta for meta in found.metas if meta.get("name") == "robots"]
        self.assertEqual([meta.get("content") for meta in robots], ["noindex"],
                         f"backend/templates/oauth/authorize.html: one <meta name=\"robots\" content=\"noindex\"> (it holds a request's state); "
                         f"found {robots}. If layouts/form.html writes one already, drop it from the page")

    def test_the_app_name_is_escaped_and_shown_once(self):
        hostile = "<b>Evil</b> & Co <script>alert(1)</script>"
        client_id = self._register_client(hostile).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        fix = "backend/templates/oauth/authorize.html: print {{ application.name }} auto-escaped; the name is the app's own claim"
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt; &amp; Co &lt;script&gt;alert(1)&lt;/script&gt;", html, fix)
        self.assertNotIn("<b>Evil</b>", html, fix)
        self.assertNotIn("<script>alert(1)", html, fix)
        self.assertEqual(html.count("Evil"), 1, "the name is shown once, framed as unverified, and nowhere else (not in the title or the heading)")

    def test_an_app_with_no_name_is_called_unnamed(self):
        body = {"redirect_uris": [tests_oauth._REDIRECT_URI], "grant_types": ["authorization_code"], "token_endpoint_auth_method": "none"}
        client_id = self.client.post(tests_oauth._DCR_URL, data=json.dumps(body), content_type="application/json").json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        self.assertIn('<p class="consent__name">Unnamed app</p>', html,
                      "backend/templates/oauth/authorize.html: an app registered with no name is shown as Unnamed app, as the dashboard shows it")

    def test_a_name_of_the_longest_length_is_shown_whole(self):
        # DCR accepts a client_name of up to 255 characters; one with no space in it must wrap on a phone (consent.css), not be cut.
        name = "W" * 255
        client_id = self._register_client(name).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        self.assertIn(f'<p class="consent__name">{name}</p>', html, "the whole name is printed; backend/static/css/pages/consent.css makes it wrap")

    def test_the_app_is_framed_as_unverified(self):
        html = self.consent().content.decode()
        for sentence in ("An unverified third-party app is asking for access to your workspace.",
                         "<strong>Unverified app:</strong> this name is what the app claims to be, not something we checked."):
            self.assertIn(sentence, html, f"backend/templates/oauth/authorize.html: keep the sentence {sentence!r} (AGENTS.md invariant on consent phishing; docs/API.md)")
        self.assertLess(html.index(self.NAME), html.index("Unverified app:"),
                        "backend/templates/oauth/authorize.html: the framing comes after the name it frames")

    def test_the_page_says_where_the_user_goes_next_and_only_the_host(self):
        fix = "backend/templates/oauth/authorize.html: keep the redirect-host sentence, with redirect_host (host and port) in <code>"
        self.assertIn("It will send you back to <code>127.0.0.1:9876</code> after you decide.", self.consent().content.decode(), fix)
        other = "https://app.example.com:8443/oauth/callback"
        client_id = self._register_client("Hosted app", redirect_uri=other).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id, "redirect_uri": other}).content.decode()
        self.assertIn("It will send you back to <code>app.example.com:8443</code> after you decide.", html, fix)
        shown = re.sub(r"<input[^>]*>", "", html)  # the hidden redirect_uri field holds the whole address; nothing a reader sees does
        self.assertNotIn("/oauth/callback", shown, "only the host is shown, never the path: the host is the anchor, the path is the app's")

    def test_the_page_says_in_plain_words_what_mcp_use_lets_the_app_do(self):
        response = self.consent()
        html = response.content.decode()
        for words in ("Approving lets the app act on your workspace. It can:",
                      "Upload files and create share links, list your artifacts, and revoke them",
                      "See your plan, usage, and account email address",
                      "It cannot create, list, or revoke API keys, disconnect your other apps, or touch billing or your account settings.",
                      "Access lasts until you disconnect it",
                      "<strong>Connected apps</strong> on your dashboard"):
            self.assertIn(words, html, f"backend/templates/oauth/includes/grant_summary_oauth.html: keep {words!r}; it is how the page says what mcp:use lets an app do")
        self.assertEqual(response.context["scopes"], ["mcp:use"], "the words describe the one scope DOT asks for (backend/config/settings.py, OAUTH2_PROVIDER SCOPES)")
        authorize, _ = form_with(read(response), "Authorize")
        self.assertEqual({f["name"]: f.get("value") for f in authorize["inputs"]}["scope"], "mcp:use", "the form posts the scope the words describe")

    def test_the_stylesheet_is_linked_and_the_page_leaks_no_template_comment(self):
        html = self.consent().content.decode()
        self.assertIn('<link rel="stylesheet" href="/static/css/pages/consent.css">', html,
                      "backend/templates/oauth/authorize.html: link css/pages/consent.css in the styles block")
        self.assertNotIn("{#", html, "a template comment spans lines and shows as text: keep each {# #} on one line")
        self.assertNotIn("#}", html, "a template comment spans lines and shows as text: keep each {# #} on one line")

    def test_the_response_cannot_be_framed(self):
        # The only clickjacking protection a consent page has (XFrameOptionsMiddleware, settings.MIDDLEWARE).
        self.assertEqual(self.consent()["X-Frame-Options"], "DENY",
                         "backend/config/settings.py: keep XFrameOptionsMiddleware and X_FRAME_OPTIONS at DENY; nothing may exempt the consent page")


class ConsentFormsTests(ConsentTestCase):
    def values(self):
        return {"redirect_uri": tests_oauth._REDIRECT_URI, "scope": "mcp:use", "nonce": "", "client_id": self.client_id,
                "state": "test-state-123", "response_type": "code", "code_challenge": self.challenge,
                "code_challenge_method": "S256", "claims": "", "resource": ""}

    def post_path(self, response):
        return response.wsgi_request.get_full_path()

    def test_two_post_forms_with_no_action_carry_every_field_dot_expects(self):
        response = self.consent()
        found = read(response)
        self.assertEqual(len(found.forms), 2, f"backend/templates/oauth/authorize.html: two forms, Authorize and Deny; found {len(found.forms)}")
        for form in found.forms:
            self.assertEqual(form["attrs"].get("method"), "post", "backend/templates/oauth/authorize.html: both forms are method=\"post\"")
            self.assertNotIn("action", form["attrs"],
                             "backend/templates/oauth/authorize.html: no action attribute: the POST returns to this address with its query string")
            posted = {field["name"]: field.get("value", "") for field in form["inputs"]}
            token = posted.pop("csrfmiddlewaretoken", "")
            self.assertTrue(token, "backend/templates/oauth/authorize.html: each form needs {% csrf_token %} (not inside a component)")
            self.assertEqual(posted, self.values(), "backend/templates/oauth/authorize.html: each form carries the hidden fields of DOT's AllowForm, with the request's values")
        repeated = sorted({name for name in found.ids if found.ids.count(name) > 1})
        self.assertEqual(repeated, [], f"backend/templates/oauth/authorize.html: these ids are used twice: {repeated}; render the hidden fields without ids, as the page does")
        self.assertIn("csrftoken", response.cookies, "the page must set the csrftoken cookie: backend/scripts/oauth_mcp_proof.py posts the cookie's value")

    def test_the_two_answers_are_equal_actions_in_one_row(self):
        response = self.consent()
        html = response.content.decode()
        found = read(response)
        fix = ("backend/templates/oauth/authorize.html: Deny and Authorize are two equal actions: each form holds one "
               '<button class="action interactive" type="submit">, and the two forms sit in <div class="form-actions"> (layouts/form.html, base.css)')
        row = re.search(r'<div class="form-actions">\s*(<form method="post">.*?</form>)\s*(<form method="post">.*?</form>)\s*</div>', html, re.S)
        self.assertIsNotNone(row, f"{fix}; found no such row")
        self.assertEqual(["Deny" in row.group(1), "Authorize" in row.group(2)], [True, True], f"{fix}; Deny comes first, as it always has, then Authorize")
        for label in ("Deny", "Authorize"):
            _, button = form_with(found, label)
            self.assertEqual(button["attrs"].get("type"), "submit", f"{fix}; the {label} button is type=\"submit\"")
            self.assertEqual(sorted(button["attrs"].get("class", "").split()), ["action", "interactive"],
                             f"{fix}; the {label} button has the classes action and interactive, which give it the hairline box and the ink wash")

    def test_only_authorize_posts_allow(self):
        found = read(self.consent())
        authorize, button = form_with(found, "Authorize")
        self.assertEqual((button["attrs"].get("type"), button["attrs"].get("name"), button["attrs"].get("value")), ("submit", "allow", "Authorize"),
                         "backend/templates/oauth/authorize.html: Authorize is <button type=\"submit\" name=\"allow\" value=\"Authorize\">: DOT reads allow")
        deny, answer = form_with(found, "Deny")
        names = [field.get("name") for field in deny["inputs"]] + [answer["attrs"].get("name")]
        self.assertNotIn("allow", names, "backend/templates/oauth/authorize.html: Deny must not post allow, or it would approve")
        self.assertEqual((len(authorize["buttons"]), len(deny["buttons"])), (1, 1), "backend/templates/oauth/authorize.html: one button in each form")

    def test_authorize_posts_what_the_page_holds_and_returns_a_code(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Authorize")
        posted = client.post(self.post_path(response), submission(form, button))
        self.assertEqual(posted.status_code, 302, f"the Authorize form, posted as the page has it, must be accepted; it answered {posted.status_code}")
        location = urlparse(posted["Location"])
        query = parse_qs(location.query)
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", tests_oauth._REDIRECT_URI, "Authorize sends the user back to the app's redirect address")
        self.assertEqual(query.get("state"), ["test-state-123"], f"Authorize echoes the app's state; the redirect was {posted['Location']}")
        self.assertEqual(len(query.get("code", [])), 1, f"Authorize returns a code to the app; the redirect was {posted['Location']}")
        self.assertEqual(get_grant_model().objects.count(), 1, "Authorize creates the grant")

    def test_deny_posts_what_the_page_holds_and_returns_access_denied(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Deny")
        posted = client.post(self.post_path(response), submission(form, button))
        self.assertEqual(posted.status_code, 302, f"the Deny form, posted as the page has it, must be accepted; it answered {posted.status_code}")
        query = parse_qs(urlparse(posted["Location"]).query)
        self.assertEqual(query.get("error"), ["access_denied"], f"Deny sends the user back with access_denied; the redirect was {posted['Location']}")
        self.assertEqual(query.get("state"), ["test-state-123"], f"Deny echoes the app's state; the redirect was {posted['Location']}")
        self.assertNotIn("code", query, "Deny returns no code")
        self.no_grants("Deny creates no grant and no token")

    def test_a_post_without_the_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Authorize")
        data = submission(form, button)
        del data["csrfmiddlewaretoken"]
        refused = client.post(self.post_path(response), data)
        self.assertEqual(refused.status_code, 403, "a consent POST with no CSRF token must be refused: an app could post it from another site")
        self.no_grants("a refused POST creates no grant")


class SignInReturnsToConsentTests(ConsentTestCase):
    def test_a_signed_out_visit_signs_in_and_comes_back_to_the_consent_page(self):
        client = Client()
        bounced = client.get(tests_oauth._AUTHORIZE_URL, self.params)
        self.assertEqual(bounced.status_code, 302, f"a signed-out visit is sent to sign in; it answered {bounced.status_code}")
        login = bounced["Location"]
        self.assertTrue(login.startswith("/accounts/login/?next=/o/authorize/"), f"a signed-out visit goes to sign in with ?next=; it went to {login}")
        self.assertEqual(client.get(login).status_code, 200, "the sign-in page opens with ?next= kept")
        # The sign-in form has no action, so it posts to this same address and ?next= rides along.
        signed_in = client.post(login, {"username": "oauthuser", "password": "pw12345678"})
        self.assertEqual(signed_in.status_code, 302, f"signing in redirects; it answered {signed_in.status_code}. Fix: backend/templates/registration/login.html posts to its own address")
        back = urlparse(signed_in["Location"])
        self.assertEqual(back.path, "/o/authorize/", f"sign in returns to ?next=; it went to {signed_in['Location']}")
        self.assertEqual(parse_qs(back.query), {key: [value] for key, value in self.params.items()},
                         "the request survives the round trip through sign in, field for field")
        html = client.get(signed_in["Location"]).content.decode()
        self.assertIn(self.NAME, html, "after sign in the consent page names the app")
        self.assertIn("Unverified app:", html, "after sign in the consent page frames the app as unverified")


class RefusedRequestTests(ConsentTestCase):
    def assert_error_page(self, response, status, description):
        found = read(response)
        html = response.content.decode()
        fix = "backend/drop_core/oauth.py: render_to_response shows device/device_error.html for a request DOT refuses"
        self.assertEqual(response.status_code, status, f"{fix}; expected {status}, got {response.status_code}")
        self.assertEqual([h1["text"].strip() for h1 in found.h1s], ["Invalid request"], f"{fix}; found headings {[h1['text'].strip() for h1 in found.h1s]}")
        self.assertIn(f"{description} Nothing was authorized. Go back to the app and start again.", html, f"{fix}, with the library's description of the error")
        self.assertEqual(found.forms, [], f"{fix}: an error page has no form, there is nothing to authorize")
        self.assertNotIn("csrfmiddlewaretoken", html, f"{fix}: no form, so no token")
        self.assertNotIn("nverified", html, f"{fix}: no consent wording on a page that has no app")
        self.assertEqual(response["X-Frame-Options"], "DENY", "backend/config/settings.py: keep XFrameOptionsMiddleware at DENY")
        self.no_grants("a refused request creates no grant")

    def test_an_unknown_client_gets_the_error_page(self):
        response = self.consent(params={**self.params, "client_id": "no-such-client"})
        self.assert_error_page(response, 400, "Invalid client_id parameter value.")

    def test_a_missing_client_gets_the_error_page(self):
        params = dict(self.params)
        del params["client_id"]
        self.assert_error_page(self.consent(params=params), 400, "Missing client_id parameter.")

    def test_a_redirect_uri_the_client_did_not_register_gets_the_error_page(self):
        response = self.consent(params={**self.params, "redirect_uri": "https://other.example.com/callback"})
        self.assert_error_page(response, 400, "Mismatching redirect URI.")
        self.assertNotIn("other.example.com", response.content.decode(), "backend/drop_core/oauth.py: the page never echoes the request's redirect_uri")

    def test_nothing_from_the_request_is_echoed(self):
        response = self.consent(params={**self.params, "client_id": '"><script>alert(1)</script>', "state": "<img src=x>"})
        self.assertEqual(response.status_code, 400, f"an unknown client answers 400, not {response.status_code}")
        html = response.content.decode()
        for needle in ("alert(1)", "<img", "test-state-123"):
            self.assertNotIn(needle, html, f"backend/drop_core/oauth.py: the error page shows only the library's fixed description, never {needle!r} from the request")

    def test_a_post_with_fields_missing_gets_the_error_page(self):
        self.client.force_login(self.user)
        response = self.client.post(tests_oauth._AUTHORIZE_URL, {"allow": "Authorize"})
        self.assert_error_page(response, 200, "This request is incomplete.")

    def test_an_error_the_app_can_be_told_about_still_goes_back_to_the_app(self):
        # DOT redirects these (not a fatal client error); render_to_response must not turn them into pages.
        response = self.consent(params={**self.params, "scope": "not-a-scope"})
        self.assertEqual(response.status_code, 302, f"backend/drop_core/oauth.py: an unknown scope still redirects to the app; it answered {response.status_code}")
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query.get("error"), ["invalid_scope"], f"the redirect names the error; it was {response['Location']}")
        self.assertEqual(query.get("state"), ["test-state-123"], f"the redirect echoes the app's state; it was {response['Location']}")


class UnverifiedEmailTests(ConsentTestCase):
    def test_an_unverified_user_gets_the_error_page_and_no_grant(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        response = self.consent()
        html = response.content.decode()
        fix = "backend/drop_core/oauth.py: the gate shows device/device_error.html (Task 4's page) with error_title as its h1"
        self.assertEqual(response.status_code, 403, f"{fix}; an unverified user gets 403, not {response.status_code}")
        found = read(response)
        self.assertEqual([h1["text"].strip() for h1 in found.h1s], ["Verify your email first"], f"{fix}; found {[h1['text'].strip() for h1 in found.h1s]}")
        self.assertIn("Authorizing an app requires a verified email address", html, f"{fix}, with error_message")
        self.assertIn(reverse("email-verification"), found.hrefs, f"{fix}, with show_resend: a link to the verification page")
        self.assertEqual(found.forms, [], f"{fix}: nothing on this page can authorize")
        self.assertNotIn("Unverified app", html, f"{fix}: no consent wording on a page that has no app")
        self.assertEqual(response["X-Frame-Options"], "DENY", "backend/config/settings.py: keep XFrameOptionsMiddleware at DENY")
        self.no_grants("an unverified user's visit creates no grant")


class ConsentTemplateTests(SimpleTestCase):
    longMessage = False

    def test_the_page_is_under_the_shell_rules(self):
        # Under shell_templates() the page gets the template rules (no inline style, components included with only, no header or
        # footer, every character in all three fonts) because it extends a layout.
        self.assertIn(PAGE, shell_templates(),
                      "backend/templates/oauth/authorize.html must extend layouts/form.html (or the shell), so that pages.tests_site_rules covers it")

    def test_every_form_has_its_own_csrf_token_and_the_page_has_no_script(self):
        text = PAGE.read_text(encoding="utf-8")
        self.assertEqual((text.count("<form"), text.count("{% csrf_token %}")), (2, 2),
                         "backend/templates/oauth/authorize.html: each of the two forms renders {% csrf_token %} itself")
        self.assertNotIn("<script", text.lower(), "backend/templates/oauth/authorize.html: the consent page needs no script")

    def test_the_words_keep_the_voice(self):
        for path in (PAGE, PARTIAL):
            text = path.read_text(encoding="utf-8")
            where = f"backend/{path.relative_to(BACKEND)}"
            with self.subTest(template=where):
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_partial_is_plain_ascii(self):
        # The partial extends nothing, so the rule that holds every shell template to the fonts' characters does not read it.
        self.assertTrue(PARTIAL.read_text(encoding="utf-8").isascii(),
                        "backend/templates/oauth/includes/grant_summary_oauth.html: write plain ASCII; a character outside it may not be in all three fonts")

    def test_the_stylesheet_wraps_what_an_app_chooses(self):
        css = STYLES.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.consent\s*\{[^}]*overflow-wrap:\s*anywhere",
                         "backend/static/css/pages/consent.css: `.consent` needs overflow-wrap: anywhere, or an app name or a host with no space "
                         "in it scrolls the page sideways on a 320px phone")

    def test_every_class_the_page_uses_has_a_rule(self):
        css = STYLES.read_text(encoding="utf-8")
        used = set()
        for path in (PAGE, PARTIAL):
            for value in re.findall(r'class="([^"]+)"', path.read_text(encoding="utf-8")):
                used.update(name for name in value.split() if name.startswith(("consent", "grant")))
        self.assertTrue(used, "backend/templates/oauth/authorize.html and its partial use no consent or grant class; their styles are in backend/static/css/pages/consent.css")
        for name in sorted(used):
            with self.subTest(css_class=name):
                self.assertIn(f".{name}", css, f"backend/static/css/pages/consent.css: add a rule for .{name}, which the consent page uses")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_oauth_consent`
Expected: `Ran 30 tests` and `FAILED (failures=15, errors=2)`. Seventeen fail:
- ERROR `FileNotFoundError: ... static/css/pages/consent.css`: `ConsentTemplateTests.test_every_class_the_page_uses_has_a_rule` and `test_the_stylesheet_wraps_what_an_app_chooses`.
- FAIL, the old template: `ConsentPageTests` `test_the_page_is_a_form_page_with_one_h1_inside_main` (no `layout-form` body class), `test_the_page_is_not_for_search_engines` (`found []`), `test_the_stylesheet_is_linked_and_the_page_leaks_no_template_comment`, `test_an_app_with_no_name_is_called_unnamed`, `test_a_name_of_the_longest_length_is_shown_whole` and `test_the_app_is_framed_as_unverified` (the old sentence has the dash and no `<strong>`); `ConsentFormsTests` `test_two_post_forms_with_no_action_carry_every_field_dot_expects` (`these ids are used twice: ['id_claims', 'id_client_id', ...]`) and `test_the_two_answers_are_equal_actions_in_one_row` (`found no such row`: the old page has `class="actions"` and buttons of class `deny` and `approve`); `SignInReturnsToConsentTests` (sign in works; the page lacks "Unverified app:"); `ConsentTemplateTests` `test_the_page_is_under_the_shell_rules` and `test_the_words_keep_the_voice` (the old page has an em dash).
- FAIL, the view: the four `RefusedRequestTests` that refuse a request (`test_an_unknown_client_gets_the_error_page`, `test_a_missing_client_gets_the_error_page`, `test_a_redirect_uri_the_client_did_not_register_gets_the_error_page`, `test_a_post_with_fields_missing_gets_the_error_page`), all with `found headings []`: the old template draws a consent card for them.

The other 13 pass already and must keep passing; they pin what this task does not change: the escaped name, the redirect-host sentence, the plain-words summary, `X-Frame-Options`, that only Authorize posts `allow` (and with the right button attributes), the Authorize and Deny round trips, the missing-token refusal, `invalid_scope` redirecting, nothing echoed, the 403 page (Task 4's `device_error.html`), one token per form, and the ASCII partial.

- [ ] **Step 3: The error page for a request DOT refuses**

In `backend/drop_core/oauth.py`, replace this exact text:

```python
    Unverified logged-in users get 403 — no grant, no code, no redirect.
    """

    template_name = "oauth/authorize.html"
```

with:

```python
    Unverified logged-in users get 403 — no grant, no code, no redirect.

    The consent page names the app that is asking, so it is built only for a
    valid client. A request DOT cannot validate (an unknown client_id, a
    redirect_uri the client did not register, a POST with fields missing)
    gets the error page, never a consent form with no app in it.
    """

    template_name = "oauth/authorize.html"
```

Replace this exact text (the 403 for an unverified email, which moves onto the same helper):

```python
        if request.user.is_authenticated and not is_email_verified(request.user):
            return render(
                request,
                "device/device_error.html",
                {
                    "error_title": "Verify your email first",
                    "error_message": (
                        "Authorizing an app requires a verified email address, "
                        "so we can reach you if the grant is ever abused. "
                        "Check your inbox for the verification link, or send a "
                        "fresh one."
                    ),
                    "show_resend": True,
                },
                status=403,
            )
        return super().dispatch(request, *args, **kwargs)
```

with:

```python
        if request.user.is_authenticated and not is_email_verified(request.user):
            return self._error_page(
                "Verify your email first",
                (
                    "Authorizing an app requires a verified email address, "
                    "so we can reach you if the grant is ever abused. "
                    "Check your inbox for the verification link, or send a "
                    "fresh one."
                ),
                403,
                show_resend=True,
            )
        return super().dispatch(request, *args, **kwargs)

    def _error_page(self, title, message, status, show_resend=False):
        """The device flow's error page (templates/device/device_error.html), which takes error_title, error_message
        and show_resend. The OAuth gate and the requests DOT refuses are shown on it."""
        return render(
            self.request,
            "device/device_error.html",
            {"error_title": title, "error_message": message, "show_resend": show_resend},
            status=status,
        )
```

Replace this exact text:

```python
        context["redirect_host"] = urlparse(context.get("redirect_uri") or "").netloc
        return context
```

with:

```python
        context["redirect_host"] = urlparse(context.get("redirect_uri") or "").netloc
        return context

    def render_to_response(self, context, **response_kwargs):
        """DOT renders this view's template for three things: the consent page, a fatal client error (no valid
        client_id, or a redirect_uri the client did not register: it must not redirect there) and a form that failed
        validation. The last two have no application, and a consent page without one is a card with no app in it
        and buttons that carry only a token, so they get the error page. The status stays DOT's (400 for the first,
        200 for the second). The text is the library's own fixed description of the error and never part of the
        request, which the page must not echo."""
        if context.get("application") is None:
            description = getattr(context.get("error"), "description", None) or "This request is incomplete"
            return self._error_page(
                "Invalid request",
                f"{description.rstrip('.')}. Nothing was authorized. Go back to the app and start again.",
                response_kwargs.get("status", 200),
            )
        return super().render_to_response(context, **response_kwargs)
```

(`render_to_response` is called with `status=` by DOT for a fatal error and without it by `form_invalid`, so the error page keeps DOT's status: 400 for the first and 200 for the second, as today. A redirect to the app never comes here: DOT returns `OAuth2ResponseRedirect` for those before it renders anything.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_oauth_consent drop_core.tests_oauth`
Expected: the four `RefusedRequestTests` that failed now pass; `drop_core.tests_oauth` passes whole; `drop_core.tests_oauth_consent` ends `FAILED (failures=11, errors=2)`, and every one that is left is a template or stylesheet test from Step 2 (the page, the partial, `consent.css`).

- [ ] **Step 4: The page, its partial and its stylesheet**

Create `backend/static/css/pages/consent.css`:

```css
/* OAuth consent (spec 4.7): the app that is asking, what it can do, and the two answers. The form layout draws the narrow column, the
   heading and the row of two equal actions (layouts/form.html, base.css: .form-actions, .action); this file is only what this page adds.
   Colours come from tokens.css. */
.consent {
  min-width: 0;
  overflow-wrap: anywhere; /* an app's name can be 255 characters with no space in it, and a host a long one: the page never scrolls sideways */
  font-synthesis-weight: none;
}

.consent :where(p, ul) {
  margin: 0 0 var(--space-3);
}

.consent :where(strong) {
  font-weight: 500; /* the heaviest face Newsreader has */
}

.consent :where(code) {
  font-family: var(--font-mono);
  font-size: .8em;
}

/* The app that is asking, between two hairlines. Its name is its own claim, so it is set in the mono face, as a quotation of
   someone else's words, and the sentence under it says nobody checked it. */
.consent__app {
  margin: var(--space-4) 0;
  padding: var(--space-3) 0;
  border-block: 1px solid var(--hair);
}

.consent__app > :last-child {
  margin-bottom: 0;
}

.consent__label {
  margin: 0 0 var(--space-1);
}

.consent .consent__name {
  font-family: var(--font-mono);
  font-size: 1.1em;
  line-height: 1.4;
}

.grant__head {
  margin-bottom: var(--space-2);
}

.grant ul {
  padding: 0 0 0 1.8em; /* room for the markers, which are the browser's */
}

.grant li {
  margin: 0 0 var(--space-1);
}
```

Replace the whole of `backend/templates/oauth/includes/grant_summary_oauth.html` (the old partial, 15 lines, whose rules lived in the page's inline style) with:

```django
{# Reusable approval-summary block for the OAuth consent page. #}
{# Keep in step with what an mcp:use token can actually do (drop_core.auth, #}
{# drop_core.views key/usage endpoints, drop_mcp tools). #}
<div class="grant">
<p class="grant__head">Approving lets the app act on your workspace. It can:</p>
<ul>
<li>Upload files and create share links, list your artifacts, and revoke them</li>
<li>See your plan, usage, and account email address</li>
</ul>
<p class="grant__head">It cannot create, list, or revoke API keys, disconnect your other apps, or touch billing or your account settings.</p>
<ul>
<li>Access lasts until you disconnect it: the app renews its access while in use, and it lapses after about two weeks unused</li>
<li>Disconnect it any time under <strong>Connected apps</strong> on your dashboard</li>
</ul>
</div>
```

Replace the whole of `backend/templates/oauth/authorize.html` (the old standalone document, 68 lines, with its inline style, its wordmark and its three old includes) with:

```django
{% extends "layouts/form.html" %}
{% load static %}
{# Page: OAuth consent (spec 4.7). VeilfileAuthorizationView shows it only for a valid client: a request DOT refuses gets device/device_error.html instead. #}
{% block title %}Authorize app | Veilfile{% endblock %}
{% block meta %}{{ block.super }}<meta name="robots" content="noindex">{% endblock %}
{% block styles %}{{ block.super }}<link rel="stylesheet" href="{% static 'css/pages/consent.css' %}">{% endblock %}
{% block form_page %}
<div class="consent">
<h1>Authorize this app</h1>
<p>An unverified third-party app is asking for access to your workspace.</p>
<div class="consent__app">
{# client_name is attacker-controlled: auto-escaped, framed as unverified #}
<p class="label consent__label">App</p>
<p class="consent__name">{{ application.name|default:"Unnamed app" }}</p>
<p><strong>Unverified app:</strong> this name is what the app claims to be, not something we checked.</p>
<p>It will send you back to <code>{{ redirect_host }}</code> after you decide.</p>
</div>
{% include "oauth/includes/grant_summary_oauth.html" only %}
{# Two forms, each with every field DOT's AllowForm expects. Authorize posts allow=Authorize; Deny posts no allow. Neither has an action: the POST returns to this address with its query string. Two equal actions, Deny first as it always was. #}
<div class="form-actions">
<form method="post">
{% csrf_token %}
{% for field in form %}{% if field.is_hidden %}<input type="hidden" name="{{ field.html_name }}" value="{{ field.value|default_if_none:'' }}">{% endif %}{% endfor %}
<button class="action interactive" type="submit">Deny</button>
</form>
<form method="post">
{% csrf_token %}
{% for field in form %}{% if field.is_hidden %}<input type="hidden" name="{{ field.html_name }}" value="{{ field.value|default_if_none:'' }}">{% endif %}{% endfor %}
<button class="action interactive" type="submit" name="allow" value="Authorize">Authorize</button>
</form>
</div>
</div>
{% endblock %}
```

(The head, the skip link, the menu, the corners and the appearance and motion switches that the old page wrote by hand come from `shell.html`. Deny and Authorize are the page's two equal actions: each form holds one `<button class="action interactive" type="submit">`, a hairline box that the ink answers, and the two forms sit in Task 1's `<div class="form-actions">`, which puts them side by side where there is room and one under the other where there is not. Deny is first, as it was on the old page. Both forms still carry every field, so the page still posts to the same address with the same names and values. The app's name is the one thing in the page that someone else wrote: it is escaped, shown once, and sits between two rules with the sentence that says nobody checked it.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_oauth_consent drop_core.tests_oauth drop_core.tests_fix_auth.ConsentCopyTests drop_core.tests_fix_auth.CredentialManagesOnlyItselfTests pages.tests_site_rules pages.tests_content`
Expected: `OK`. `drop_core.tests_oauth_consent` alone is `Ran 30 tests ... OK`. The page is now under the shell rules (`pages.tests_site_rules`: no inline style, no header or footer in a page, every `{% static %}` path exists; `pages.tests_content`: every character in all three fonts), and `ConsentCopyTests` and `test_consent_copy_matches` pass on the new partial unchanged.

- [ ] **Step 5: Hold the module to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a test class whose `longMessage` is not `False`, and its message names the file and the fix. A failure that compares a page would otherwise print the page before the message. Tasks 1 to 4 each appended their module to `READING_TEST_MODULES`, and Task 4's is the last name in it. Append this one.

Run (from the repository root): `grep -n '"drop_core.tests_device_pages")' backend/pages/tests_site_rules.py`
Expected: exactly one line, the end of the `READING_TEST_MODULES` tuple. (If it prints nothing, an earlier task left the tuple ending differently: print it with `grep -n -A5 "READING_TEST_MODULES = " backend/pages/tests_site_rules.py` and add `"drop_core.tests_oauth_consent"` as its last name, before the closing parenthesis, which is all the replacement below does.)

In `backend/pages/tests_site_rules.py` replace this exact text:

```python
"drop_core.tests_device_pages")
```

with:

```python
"drop_core.tests_device_pages",
                        "drop_core.tests_oauth_consent")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules`
Expected: `OK`. A class of `drop_core/tests_oauth_consent.py` that loses `longMessage = False` now fails `ReadableFailuresTests`, naming that file and the fix.

- [ ] **Step 6: The API reference**

In `docs/API.md`, replace this exact text:

```text
   **Verified email required**: unverified users get `403` and no grant is
   created.
```

with:

```text
   **Verified email required**: unverified users get `403` and no grant is
   created. A request with an unknown `client_id`, or a `redirect_uri` the
   client did not register, gets `400` and an error page, never a consent
   page, and the browser is not sent to that `redirect_uri`.
```

(The status was already 400; what changes is that the body is an error page, not a consent card with no app in it.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_docs`
Expected: `OK` (the docs tests check paths, error codes and prices against the code; the sentence adds none).

- [ ] **Step 7: The whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes). Nothing else in the suite renders the consent page, so only this task's 30 new tests are new.

- [ ] **Step 8: Commit**

From the repository root:

```bash
git add backend/drop_core/oauth.py backend/drop_core/tests_oauth_consent.py backend/templates/oauth/authorize.html backend/templates/oauth/includes/grant_summary_oauth.html backend/static/css/pages/consent.css backend/pages/tests_site_rules.py docs/API.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "OAuth consent moves onto the form layout with an h1 and Deny and Authorize as two equal actions, and a request django-oauth-toolkit refuses (an unknown client, an unregistered redirect address, an incomplete form) shows the error page instead of an empty consent card; the escaped app name, the unverified framing, the redirect host, Deny, CSRF and X-Frame-Options now have tests"
```
Expected: one commit with those seven files.

---

### Task 6: Error pages

**Files:**
- Create: `backend/pages/errors.py`, `backend/templates/404.html`, `backend/templates/403.html`, `backend/templates/403_csrf.html`, `backend/templates/500.html`
- Create (tests): `backend/pages/tests_errors.py`, `backend/pages/tests_artifact_miss.py`, `backend/drop_core/tests_throttled_pages.py`
- Modify: `backend/config/urls.py` (the three handler names, appended), `backend/config/settings.py` (`CSRF_FAILURE_VIEW`), `backend/drop_core/views.py` (an import, `serve_artifact`'s five misses, and the three bare 429s), `backend/drop_core/throttle.py` (one import, `throttled_response` reworded, three functions added), `backend/templates/registration/throttled.html` (Task 2's page: the wait sentence and the links, in Step 7)
- Modify (tests): `backend/pages/tests_site_rules.py` (three names appended to `READING_TEST_MODULES`, Step 8). No other test changes; Step 10 lists what was searched and why.

**Interfaces:**
- Consumes:
  - `templates/layouts/chapter.html` (PR 2): its blocks `preload_heading` and `chapter`, and the class `chapter__title`, which `base.css` styles for any element. Nothing of Task 1's `layouts/form.html` or CSS is used, so these pages stand whatever Task 1 does.
  - `templates/shell.html`: its blocks `title` and `meta`, and `{% shell_menu %}`, which reads `request.user` and `request.resolver_match` when there is a request and nothing when there is not.
  - `drop_core.throttle.throttled_response(request, title, message, retry_after_seconds)` and Task 2's `registration/throttled.html`, which Task 2 leaves as `layouts/form.html`'s `form_page` with `<h1>{{ title }}</h1>`, `<p role="alert">{{ message }} Wait about {{ retry_minutes }} minutes, then try again.</p>` and a `<p class="form-links">` holding Log in and Forgot password?. Step 7 changes the sentence and the links (it reads no `user`, `request` or `csrf`, a test of Task 2's holds that, and Step 7 keeps it so).
  - `pages.tests_site_rules.READING_TEST_MODULES`, which the tasks before this one append their test modules to. It ends with `"drop_core.tests_oauth_consent")` when Task 5 is done; Step 8 appends this task's three modules after it.
  - `pages.tests_chapters.BANNED`.
- Produces:
  - `pages.errors`: `not_found(request, exception=None)` (handler404), `permission_denied(request, exception=None)` (handler403), `csrf_failure(request, reason="")` (CSRF_FAILURE_VIEW), `server_error(request=None)` (handler500), `artifact_miss()` (the `/a/<token>` miss), and under them `shell_error_page(request, template, status)`, `bare_page(status, words=None)`, `log_failure(exc, message, *args)`, `BARE_WORDS` and `GONE_WORDS`.
  - `config.urls.handler403`, `handler404`, `handler500`, and `settings.CSRF_FAILURE_VIEW`.
  - `templates/404.html` (context: `gone_link`), `403.html`, `403_csrf.html` (no context) and `500.html` (optional `title` and `message`).
  - `drop_core.throttle.wait_in_words(seconds)` (`"1 minute"`, `"15 minutes"`, `"60 minutes"`, `"24 hours"`), `throttled_response(request, title, message, retry_after_seconds, sign_in_links=True)`, `signup_throttled_response(request)`, `verification_throttled_response(request, reason)` and `VERIFICATION_RETRY_AFTER`.
  - The context of `registration/throttled.html` is `title`, `message`, `retry_wait` and `sign_in_links`, all fixed by the throttle that refuses; `retry_minutes` is gone, since nothing reads it.
- Decisions made here:
  - **Layout.** 404, 403 and the failed form check are statements, not forms, so they extend `layouts/chapter.html` (a centred title and a sentence, in the style of About) and carry `<h1 class="chapter__title">`. That is existing, tested CSS. `shell_page` is handed to them as `None`, so they have no registry head, no `data-layout` and no in-place changes, even when the view that failed belongs to a registry page. Each is `noindex`.
  - **`/a/<token>` returns its miss, it does not raise `Http404`.** The view answers every kind of miss (unknown, expired, revoked, held, stored file missing, S3 `NoSuchKey`) with `pages.errors.artifact_miss()`, one function, so no case can differ from another. Two things follow that raising could not give:
    - Under `DEBUG` Django answers a raised `Http404` with its technical page, which prints the token and the message that says which kind of miss it was. A returned response is the same page in the dev server, in the browser checks and in production.
    - The page is drawn with no request (`render_to_string`, no context processors), so it never touches the session: no `Vary: Cookie`, no session or user query (`assertNumQueries(1)`, the token's lookup), and the bytes are the same for a signed-in visitor, a visitor with a stale cookie and a visitor with none. The cost is that the menu on this one page always offers Sign in. The page carries `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`, the headers a found file has, and the status stays 404 (both CLIs and `docs/API.md` rely on it). The 503 for a storage outage and a found file's bytes are untouched.
  - **A page that cannot be drawn is not a worse error.** Django calls `handler500` when a 404 or 403 handler raises, and a template that needs the static manifest, the session or a context processor can raise in production. `shell_error_page` catches that and answers with `500.html` under the same status (`bare_page`), so a 404 stays a 404. `500.html` is both the 500 and that fallback: self-contained (inline CSS, no template tag, only the `default` and `cut` filters, no static file, no context processor; its colours are `tokens.css` values, which a test compares), it says its own words when it is given nothing and `title` and `message` when it is.
  - **Nothing of the failure is printed or logged.** Handlers never read `exception`, `request_path` or `reason`; a test reads the templates for those names. A test found a leak in the first draft: Django runs the 404 handler inside the `except` that caught the `Resolver404`, so an exception raised while drawing the page has the `Resolver404` as its `__context__`, and logging it printed the path, which for `/a/<token>/x` is the token, through a logger that does not redact. `log_failure` cuts `__context__` and `__cause__` before logging.
  - **The bare 429s** of signup and of verification emails use `throttled_response`, so they are the shell's throttled page with `Retry-After`: 3600 for signup (the hour its limit counts), and for a verification email 60, 3600 or 86400 by the reason `resend_allowed()` returned (`too_soon`, `account_limit`, `address_limit`). The page holds nothing of the visitor, the account or the address, and has no form and no CSRF token.
  - **The wait is said in words, and the links fit who is refused.** Task 2's page was written for the sign-in and reset limits (15 and 60 minutes, a visitor who is signed out). Two more reasons reach it here, so it learns two things, both constants of the throttle and never of the visitor, so the bytes stay identical for everyone refused for one reason:
    - `retry_wait` is `wait_in_words(retry_after_seconds)`: whole minutes under two hours (`1 minute`, `15 minutes`, `60 minutes`), else whole hours. The sentence reads `Wait about 1 minute, then try again.` for the resend a moment after the last, and `Wait about 24 hours, then try again.` for the daily limit of an address, not `1 minutes` and not `1440 minutes`.
    - `sign_in_links` is true unless the caller says otherwise. A verification email is asked for by a signed-in visitor (the view is `login_required`), so its page offers `Back to dashboard` (`/app/`) in the place of Log in and Forgot password?, which would send someone who is signed in to the sign-in page.
  - **Voice.** New copy has no apostrophes, em dashes or exclamation marks, and every character is ASCII. 404 and the 500 say what happened and what to do, with no apology.
  - **Tests.** Every new class sets `longMessage = False` and its messages name the file and the fix. Step 8 appends the three modules to `READING_TEST_MODULES`, so `pages.tests_site_rules.ReadableFailuresTests` holds them to it (a module with no test class, or a class without `longMessage = False`, fails there with the file and the fix). Tests that need a view that fails use `ROOT_URLCONF="pages.tests_errors"`, a urlconf made of the site's routes and four that raise; `HandlersAreRegisteredTests` keeps its handlers equal to `config.urls`'s.
  - **Titles** end ` | Veilfile`, as every page of the new site does: `Not found | Veilfile`, `Nothing here | Veilfile`, `Not allowed | Veilfile`, `Form not sent | Veilfile`, `Something went wrong | Veilfile`, and `{{ title }} | Veilfile` for the throttled page. Tests pin each.


- [ ] **Step 1: Write the failing tests for the 404, 403, CSRF and 500 pages**

Create `backend/pages/tests_errors.py`:

```python
"""The error pages (spec 4.9): 404, 403, a failed CSRF check and 500, what they never print, and what they do when they cannot be drawn."""
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import get_resolver, path

from config.urls import urlpatterns as site_urlpatterns
from pages import errors
from pages.tests_chapters import BANNED

BACKEND = Path(settings.BASE_DIR)
TEMPLATES = BACKEND / "templates"
ERROR_TEMPLATES = ("404.html", "403.html", "403_csrf.html", "500.html")
SECRET = "SECRET-DETAIL-4f9c"  # what a failure carries that no error page may print


def boom(request):
    raise RuntimeError(SECRET)


def denied(request):
    raise PermissionDenied(SECRET)


def missing(request):
    raise Http404(SECRET)


def registry_page_that_is_gone(request):
    raise Http404(SECRET)


# The site's own routes, and four that fail on purpose. ROOT_URLCONF names this module in the tests below, so the handlers
# are the site's: a test of config.urls (HandlersAreRegisteredTests) keeps these three lines equal to its own.
urlpatterns = [
    path("boom/", boom),
    path("denied/", denied),
    path("missing/", missing),
    # Named like a registry page, so the context processor would give the 404 that page's head.
    path("about-is-gone/", registry_page_that_is_gone, name="about"),
] + site_urlpatterns
handler403 = "pages.errors.permission_denied"
handler404 = "pages.errors.not_found"
handler500 = "pages.errors.server_error"


def main(html):
    """What a visitor reads: the page from <main> to </main>."""
    return html[html.index("<main"):html.index("</main>")]


def marked_links(html):
    """{label: aria-current} for every menu link that is marked as this page or its section."""
    found = re.findall(r'<a class="label interactive" href="[^"]*" aria-current="([^"]*)">([^<]*)</a>', html)
    return {label: mark for mark, label in found}


def templates_with_processors(processors):
    """settings.TEMPLATES with these context processors in place of the site's."""
    engine = dict(settings.TEMPLATES[0])
    engine["OPTIONS"] = {**engine["OPTIONS"], "context_processors": processors}
    return [engine]


def manifest_with_nothing_in_it():
    """STORAGES as production has them after a bad collectstatic: the manifest storage, and no entry for any file. With
    DEBUG off, {% static %} then raises ValueError on every page that uses it (the tests otherwise run on plain storage)."""
    return {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"}}


def exploding_processor(request):
    raise RuntimeError(SECRET)


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class NotFoundPageTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def test_an_unknown_address_is_a_404_on_the_shell(self):
        response = self.client.get("/no-such-page/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 404, "backend/pages/errors.py: not_found() must answer with status 404")
        self.assertIn('<main id="main"', html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertIn('class="skip-link', html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertIn("data-menu", html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1, "backend/templates/404.html: one <h1>, inside <main>")
        self.assertIn("<h1", main(html), "backend/templates/404.html: the <h1> belongs inside <main>")
        self.assertIn("Not found.", main(html))
        self.assertIn("<title>Not found | Veilfile</title>", html)
        self.assertIn('<meta name="robots" content="noindex">', html, "backend/templates/404.html: an error page is never indexed")

    def test_it_has_no_registry_head_and_no_in_place_changes(self):
        # about-is-gone is routed under the name "about", a registry page: its head (canonical, Open Graph, layout and the
        # family's paths) would claim the error is that page, and shell.js would change in place to and from it.
        response = self.client.get("/about-is-gone/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("data-page-meta", html,
                         "backend/pages/errors.py: shell_error_page() must hand the template shell_page=None, so an error page has no head entry")
        self.assertNotIn("data-layout", html,
                         "backend/pages/errors.py: shell_error_page() must hand the template shell_page=None, so an error page has no layout family")
        self.assertIn("<title>Not found | Veilfile</title>", html)

    def test_it_prints_nothing_of_the_request_or_the_failure(self):
        for address in ("/missing/", f"/{SECRET}-path/", f"/missing/?q={SECRET}-query"):
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(SECRET, response.content.decode(),
                                 "backend/templates/404.html: never print the exception, the path or the query; "
                                 "a 404 must read the same whatever raised it")

    def test_a_missing_docs_page_still_marks_docs_in_the_menu(self):
        # The menu follows the URL's name (docs-page, blog-post), not the head entry, which an unknown slug does not have.
        for address, label in (("/docs/no-such-page/", "Docs"), ("/blog/no-such-post/", "Journal")):
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(marked_links(response.content.decode()), {label: "true"},
                                 f"backend/pages/errors.py: not_found() must draw the page with the request, so the menu marks {label} for {address}")

    def test_an_address_in_no_section_marks_nothing(self):
        self.assertEqual(marked_links(self.client.get("/no-such-page/").content.decode()), {})
        self.assertEqual(marked_links(self.client.get("/a/x/y").content.decode()), {})

    def test_the_menu_follows_who_is_signed_in(self):
        anonymous = self.client.get("/no-such-page/").content.decode()
        self.assertIn(">Sign in<", anonymous)
        self.assertNotIn(">Dashboard<", anonymous)
        self.client.force_login(self.reader())
        signed_in = self.client.get("/no-such-page/").content.decode()
        self.assertIn(">Dashboard<", signed_in, "backend/pages/errors.py: not_found() must draw the page with the request")
        self.assertNotIn(">Sign in<", signed_in)

    def test_a_post_gets_the_same_page(self):
        response = self.client.post("/no-such-page/", {"a": "b"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Not found.", response.content.decode())

    def reader(self):
        return get_user_model().objects.create_user(username="reader", password="pw12345678")


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class PermissionDeniedPageTests(TestCase):
    longMessage = False

    def test_a_denied_view_gets_the_403_page_and_none_of_its_reason(self):
        response = self.client.get("/denied/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 403, "backend/pages/errors.py: permission_denied() must answer with status 403")
        self.assertIn("<title>Not allowed | Veilfile</title>", html)
        self.assertIn("Not allowed.", main(html))
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertNotIn(SECRET, html, "backend/templates/403.html: never print the exception")
        self.assertIn('href="/accounts/login/"', main(html), "backend/templates/403.html: the page says where to sign in")

    def test_it_has_no_registry_head(self):
        html = self.client.get("/denied/").content.decode()
        self.assertNotIn("data-page-meta", html)
        self.assertNotIn("data-layout", html)


@override_settings(DEBUG=False)
class CsrfFailurePageTests(TestCase):
    longMessage = False

    def post_without_a_token(self):
        client = Client(enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            response = client.post("/accounts/verify-email/SECRET_VERIFY_TOKEN/")
        return response, logs

    def test_a_failed_check_gets_the_form_not_sent_page(self):
        response, _logs = self.post_without_a_token()
        html = response.content.decode()
        self.assertEqual(response.status_code, 403, "backend/pages/errors.py: csrf_failure() must answer with status 403")
        self.assertIn("<title>Form not sent | Veilfile</title>", html)
        self.assertIn("Form not sent.", main(html))
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertIn('<main id="main"', html, "backend/templates/403_csrf.html: it must extend a layout, which extends shell.html")

    def test_it_prints_neither_the_reason_nor_the_address(self):
        response, _logs = self.post_without_a_token()
        html = response.content.decode()
        for text in ("SECRET", "CSRF", "cookie not set", "token missing", "Origin"):
            with self.subTest(text=text):
                self.assertNotIn(text.lower(), main(html).lower(),
                                 "backend/templates/403_csrf.html: never print the reason Django gives or the address; "
                                 "it can quote the Origin header")

    def test_the_failure_is_still_logged_once_and_redacted(self):
        response, logs = self.post_without_a_token()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("SECRET", logs.output[0])

    def test_the_site_names_the_view(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "pages.errors.csrf_failure",
                         "backend/config/settings.py: CSRF_FAILURE_VIEW must be pages.errors.csrf_failure")


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class ServerErrorPageTests(TestCase):
    longMessage = False

    def test_a_view_that_raises_gets_the_500_page_and_none_of_the_failure(self):
        client = Client(raise_request_exception=False)
        with self.assertLogs("django.request", level="ERROR"):
            response = client.get("/boom/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 500)
        self.assertIn("<title>Something went wrong | Veilfile</title>", html)
        self.assertIn("<h1>Something went wrong.</h1>", html)
        self.assertIn("support@veilfile.com", html)
        self.assertNotIn(SECRET, html, "backend/templates/500.html: never print the exception")

    def test_it_is_drawn_with_no_request_at_all(self):
        # Django's own server_error() is called with the request and draws with none; so does this one.
        response = errors.server_error(None)
        self.assertEqual(response.status_code, 500)
        self.assertIn("Something went wrong.", response.content.decode())

    def test_it_is_drawn_when_the_static_manifest_is_missing(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()):
            # The same settings break the shell: this is what the test would otherwise not notice.
            with self.assertRaises(ValueError):
                render_to_string("shell.html")
            response = errors.server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Something went wrong.", response.content.decode(),
                      "backend/templates/500.html: no {% static %} tag; it must draw when the static manifest is missing")

    def test_it_is_drawn_when_every_context_processor_raises(self):
        with override_settings(TEMPLATES=templates_with_processors(["pages.tests_errors.exploding_processor"])):
            with self.assertRaises(RuntimeError):
                render_to_string("shell.html", request=RequestFactory().get("/"))
            client = Client(raise_request_exception=False)
            with self.assertLogs("django.request", level="ERROR"):
                response = client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Something went wrong.", response.content.decode(),
                      "backend/pages/errors.py: server_error() must draw templates/500.html with no request, so no context processor runs")

    def test_its_own_words_can_be_replaced_and_are_escaped(self):
        html = render_to_string("500.html", {"title": "A <b>title</b>", "message": "Words & <i>more</i>."})
        self.assertIn("<h1>A &lt;b&gt;title&lt;/b&gt;</h1>", html)
        self.assertIn("Words &amp; &lt;i&gt;more&lt;/i&gt;.", html)

    def test_with_no_words_given_it_says_its_own(self):
        html = render_to_string("500.html")
        self.assertIn("<h1>Something went wrong.</h1>", html)
        self.assertIn("<title>Something went wrong | Veilfile</title>", html)


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class AnErrorPageThatCannotBeDrawnTests(TestCase):
    longMessage = False

    def shell_that_raises(self):
        # The shell's menu is the first thing in <body> that reads the request; the site's real failures (a database that
        # is down under the session, a static file missing from the manifest) raise from the same render.
        return mock.patch("pages.registry.menu_groups", side_effect=RuntimeError(SECRET))

    def assert_bare(self, response, status, title):
        html = response.content.decode()
        self.assertEqual(response.status_code, status,
                         f"backend/pages/errors.py: shell_error_page() must keep the status ({status}) when the shell cannot be drawn, not turn it into a 500")
        self.assertIn(f"<h1>{title}</h1>", html, "backend/pages/errors.py: the fallback is templates/500.html with BARE_WORDS")
        self.assertNotIn('<main id="main"', html)
        self.assertNotIn(SECRET, html)

    def test_a_404_stays_a_404(self):
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR") as logs:
            response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found.")
        self.assertIn("404.html could not be drawn on the shell", logs.output[0])
        self.assertNotIn("no-such-page", logs.output[0], "backend/pages/errors.py: do not log the path; it can be a bearer token")

    def test_a_403_stays_a_403(self):
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/denied/")
        self.assert_bare(response, 403, "Not allowed.")

    def test_a_failed_csrf_check_stays_a_403(self):
        client = Client(enforce_csrf_checks=True)
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR"), self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post("/accounts/verify-email/SECRET_VERIFY_TOKEN/")
        self.assert_bare(response, 403, "Form not sent.")

    def test_a_missing_static_manifest_does_not_turn_a_404_into_a_500(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()), self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found.")

    def test_a_context_processor_that_raises_does_not_turn_a_404_into_a_500(self):
        with override_settings(TEMPLATES=templates_with_processors(["pages.tests_errors.exploding_processor"])):
            with self.assertLogs("pages.errors", level="ERROR"):
                response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found.")

    def test_when_even_the_bare_page_cannot_be_drawn_one_line_of_text_keeps_the_status(self):
        with mock.patch("pages.errors.render", side_effect=RuntimeError(SECRET)), \
                mock.patch("pages.errors.render_to_string", side_effect=RuntimeError(SECRET)), \
                self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.content, b"Something went wrong.")

    def test_the_500_view_cannot_raise_either(self):
        with mock.patch("pages.errors.render_to_string", side_effect=RuntimeError(SECRET)), self.assertLogs("pages.errors", level="ERROR"):
            response = errors.server_error(None)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, b"Something went wrong.")


class HandlersAreRegisteredTests(SimpleTestCase):
    longMessage = False

    def test_the_site_registers_all_three(self):
        resolver = get_resolver("config.urls")
        for status, name in ((403, "permission_denied"), (404, "not_found"), (500, "server_error")):
            with self.subTest(status=status):
                self.assertIs(resolver.resolve_error_handler(status), getattr(errors, name),
                              f"backend/config/urls.py: handler{status} must be \"pages.errors.{name}\"")

    def test_the_test_urlconf_uses_the_sites_handlers(self):
        # The routes above are tried with ROOT_URLCONF = this module: it must name the handlers config.urls does.
        import config.urls

        for status in (403, 404, 500):
            with self.subTest(status=status):
                self.assertEqual(globals()[f"handler{status}"], getattr(config.urls, f"handler{status}"),
                                 f"backend/pages/tests_errors.py: handler{status} must equal config.urls.handler{status}")


class ErrorTemplateRulesTests(SimpleTestCase):
    longMessage = False

    def source(self, name):
        """The template's text with its {# comments #} taken out: the code and the words, not the notes."""
        return re.sub(r"{#.*?#}", "", (TEMPLATES / name).read_text(encoding="utf-8"), flags=re.S)

    def test_every_error_template_exists(self):
        for name in ERROR_TEMPLATES:
            with self.subTest(template=name):
                self.assertTrue((TEMPLATES / name).is_file(), f"backend/templates/{name}: add it (pages/errors.py draws it)")

    def test_no_error_template_prints_the_request_or_the_failure(self):
        # Django hands 404.html `exception` and `request_path`, and 403.html `exception`; a CSRF failure's reason is not
        # passed at all. A page that printed any of them would tell two kinds of failure apart, and /a/<token> must not.
        names = re.compile(r"\b(?:exception|request_path|reason|request|csrf_token)\b|\.META\b|\bGET\b")
        for name in ERROR_TEMPLATES:
            with self.subTest(template=name):
                self.assertIsNone(names.search(self.source(name)),
                                  f"backend/templates/{name}: an error page prints nothing of the request or the failure "
                                  "(exception, request_path, reason, request, csrf_token); remove it")

    def test_the_voice(self):
        for name in ERROR_TEMPLATES:
            text = self.source(name).replace("<!DOCTYPE", "")
            with self.subTest(template=name):
                self.assertNotIn("—", text, f"backend/templates/{name}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"backend/templates/{name}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"backend/templates/{name}: do not use \"{word}\"")

    def test_the_500_is_self_contained(self):
        text = self.source("500.html")
        self.assertTrue(text.isascii(), "backend/templates/500.html: ASCII only; it loads no font and must not depend on a charset header")
        for pattern, why in (
            (r"{%\s*(?:extends|include|load|static|url)\b", "no extends, include, load, static or url tag: they need the template engine's other files, a manifest or the URL conf"),
            (r"<link\b", "no <link>: every style is inline"),
            (r"<script\b[^>]*\bsrc\s*=", "no external script"),
            (r"@import|\burl\(", "no @import and no url(): nothing is fetched"),
            (r"<img\b", "no image"),
        ):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"backend/templates/500.html: {why}")
        self.assertIn("<style>", text, "backend/templates/500.html: the page's styles are inline in a <style> element")

    def test_the_500s_colours_are_the_tokens(self):
        # It cannot link tokens.css, so it repeats three of its values; a new palette must reach it.
        tokens = (BACKEND / "static" / "css" / "tokens.css").read_text(encoding="utf-8").lower()
        colours = set(re.findall(r"#[0-9a-f]{3,8}\b", self.source("500.html").lower()))
        self.assertTrue(colours, "backend/templates/500.html: it draws in Paper and Ink, so it holds their colours")
        stray = sorted(colour for colour in colours if colour not in tokens)
        self.assertEqual(stray, [], f"backend/templates/500.html: {', '.join(stray)} is not in backend/static/css/tokens.css; use the token's value")

    def test_the_fallback_says_what_the_templates_say(self):
        # pages/errors.py repeats the words of 404.html, 403.html for the moment the shell cannot be drawn.
        gone = render_to_string("404.html", {"gone_link": True})
        found = render_to_string("404.html")
        denied = render_to_string("403.html")
        csrf = render_to_string("403_csrf.html")
        for words, html in ((errors.GONE_WORDS, gone), (errors.BARE_WORDS["404.html"], found),
                            (errors.BARE_WORDS["403.html"], denied), (errors.BARE_WORDS["403_csrf.html"], csrf)):
            for sentence in words:
                with self.subTest(sentence=sentence):
                    self.assertIn(sentence, html, f"backend/pages/errors.py: \"{sentence}\" is not what the template says; change them together")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_errors`
Expected: one ERROR, `ImportError: Failed to import test module: tests_errors` ... `ImportError: cannot import name 'errors' from 'pages'`, `Ran 1 test`, `FAILED (errors=1)`.


- [ ] **Step 2: Write the failing tests for the `/a/<token>` miss**

Create `backend/pages/tests_artifact_miss.py`:

```python
"""A link to a file that is not there (spec 4.9): one page, the same bytes and headers for every kind of miss, and the file's bytes untouched when it is found."""
from contextlib import nullcontext
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from drop_core.models import APIKey, Artifact, get_or_create_workspace
from drop_core.services import revoke_artifact, upload_artifact
from drop_core.storage import get_storage
from pages import errors
from pages.tests_errors import SECRET, main, manifest_with_nothing_in_it

User = get_user_model()
UNKNOWN_TOKEN = "u" * 43
WORDS = "If this link was shared with you, its moment may have passed."


class NoSuchKey(Exception):
    """What the S3 and R2 client raises for an object that is gone."""

    response = {"Error": {"Code": "NoSuchKey", "Message": "gone"}}


def everything_a_reader_gets(response):
    """The status, the body, every header and every cookie of a response: what two answers must share to be told apart by no one."""
    return (response.status_code, response.content, sorted((name.lower(), value) for name, value in response.headers.items()),
            sorted(response.cookies.keys()))


@override_settings(DEBUG=False)
class ArtifactMissTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def setUp(self):
        user = User.objects.create_user(username="owner", password="pw12345678")
        self.workspace = get_or_create_workspace(user)
        self.key, _raw = APIKey.create_key(self.workspace, "key")
        self.user = user

    def artifact(self, name="note.txt"):
        return upload_artifact(self.workspace, self.key, name, b"hello", "text/plain")

    def misses(self):
        """{kind: the address of a link that leads nowhere for that reason}. Each is set up and left in place for the request."""
        gone_object = self.artifact("gone-object.txt")
        get_storage().delete(gone_object.token)  # the row is there, the stored file is not
        expired = self.artifact("expired.txt")
        Artifact.objects.filter(id=expired.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        held = self.artifact("held.txt")
        Artifact.objects.filter(id=held.id).update(safety_status="held")
        revoked = self.artifact("revoked.txt")
        revoked_token = revoked.token
        revoke_artifact(self.workspace, revoked.id)
        no_such_key = self.artifact("no-such-key.txt")
        return {
            "unknown": f"/a/{UNKNOWN_TOKEN}",
            "expired": f"/a/{expired.token}",
            "revoked": f"/a/{revoked_token}",
            "held": f"/a/{held.token}",
            "object missing": f"/a/{gone_object.token}",
            "no such key": f"/a/{no_such_key.token}",
        }

    def get_each(self):
        """{kind: response}, every miss fetched the same way."""
        answers = {}
        for kind, address in self.misses().items():
            if kind == "no such key":
                with mock.patch("drop_core.storage.LocalStorage.open", side_effect=NoSuchKey()):
                    answers[kind] = self.client.get(address)
            elif kind == "object missing":
                with self.assertLogs("drop_core.views", level="WARNING"):
                    answers[kind] = self.client.get(address)
            else:
                answers[kind] = self.client.get(address)
        return answers

    def test_every_kind_of_miss_is_the_same_answer_to_the_byte(self):
        answers = self.get_each()
        expected = everything_a_reader_gets(answers["unknown"])
        self.assertEqual(expected[0], 404, "backend/drop_core/views.py: a miss on /a/<token> answers 404 (the CLIs and docs/API.md depend on it)")
        for kind, response in answers.items():
            with self.subTest(kind=kind):
                self.assertEqual(everything_a_reader_gets(response), expected,
                                 f"backend/drop_core/views.py: serve_artifact() must answer a {kind} link with pages.errors.artifact_miss(), "
                                 "and nothing else; the status, body, headers and cookies must equal an unknown token's")

    def test_the_page_says_only_the_spec_words(self):
        response = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        html = response.content.decode()
        self.assertIn("<h1 class=\"chapter__title\">Nothing here.</h1>", html, "backend/templates/404.html: the heading for a file link is Nothing here.")
        self.assertIn(f"<p>{WORDS}</p>", html, "backend/templates/404.html: the sentence for a file link is the spec's (4.9)")
        self.assertIn("<title>Nothing here | Veilfile</title>", html)
        self.assertIn('<main id="main"', html, "backend/templates/404.html: the page is on the shell")
        for text in (UNKNOWN_TOKEN, "expired", "revoked", "held", "found", "Not found", "/a/"):
            with self.subTest(text=text):
                self.assertNotIn(text, main(html), "backend/templates/404.html: the page must not say which kind of miss it is, or echo the address")

    def test_it_is_as_private_as_a_file_that_is_found(self):
        response = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store", "backend/pages/errors.py: artifact_miss() sets the headers a found file has")
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow", "backend/pages/errors.py: artifact_miss() sets the headers a found file has")
        self.assertIn('<meta name="robots" content="noindex">', response.content.decode())

    def test_it_never_reads_the_session(self):
        # The page is drawn with no request, so a signed-in visitor, a visitor with a stale cookie and a visitor with none
        # all get the same bytes, no Vary header, and the one query that looks the token up.
        anonymous = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertNotIn("Vary", anonymous.headers, "backend/pages/errors.py: artifact_miss() must draw the page with no request; the shell's menu reads the session, which adds Vary: Cookie")
        signed_in = Client()
        signed_in.force_login(self.user)
        with self.assertNumQueries(1):
            response = signed_in.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertEqual(everything_a_reader_gets(response), everything_a_reader_gets(anonymous),
                         "backend/pages/errors.py: artifact_miss() must not read the session; a signed-in visitor sees the same page")
        stale = Client()
        stale.cookies["sessionid"] = "not-a-session"
        with self.assertNumQueries(1):
            self.assertEqual(everything_a_reader_gets(stale.get(f"/a/{UNKNOWN_TOKEN}")), everything_a_reader_gets(anonymous))

    @override_settings(DEBUG=True)
    def test_with_debug_on_the_miss_is_still_this_page(self):
        # Django answers a raised Http404 with its technical page under DEBUG, which prints the token and which kind of
        # miss it was. The view returns the page instead of raising, so the dev server and the browser checks see it too.
        for kind, address in self.misses().items():
            if kind == "no such key":
                continue
            with self.subTest(kind=kind):
                with self.assertLogs("drop_core.views", level="WARNING") if kind == "object missing" else nullcontext():
                    response = self.client.get(address)
                html = response.content.decode()
                self.assertEqual(response.status_code, 404)
                self.assertIn("Nothing here.", html, "backend/drop_core/views.py: serve_artifact() returns artifact_miss(); it must not raise Http404, which DEBUG answers with a page that names the token")
                self.assertNotIn(address.rsplit("/", 1)[1], html)

    def test_the_one_log_line_is_the_redacted_one(self):
        token = self.artifact().token
        Artifact.objects.filter(token=token).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertLogs("django.request", level="WARNING") as logs:
            response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(logs.output, ["WARNING:django.request:Not Found: /a/<redacted>"])

    def test_a_link_with_a_slash_or_more_is_the_ordinary_404_for_a_live_token_and_an_unknown_one_alike(self):
        live = self.artifact().token
        first = self.client.get(f"/a/{live}/")
        second = self.client.get(f"/a/{UNKNOWN_TOKEN}/")
        self.assertEqual(first.status_code, 404)
        self.assertIn("Not found.", first.content.decode())
        self.assertEqual(everything_a_reader_gets(first), everything_a_reader_gets(second),
                         "backend/templates/404.html: the 404 for an address that does not resolve must not depend on the token in it")

    def test_a_file_that_is_found_is_the_files_bytes_and_nothing_else(self):
        artifact = self.artifact()
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"hello")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Content-Security-Policy"], "sandbox allow-downloads")

    def test_a_storage_outage_is_still_the_plain_503(self):
        artifact = self.artifact()
        with mock.patch("drop_core.storage.LocalStorage.open", side_effect=RuntimeError("no bucket")):
            with self.assertLogs("drop_core", level="ERROR"):
                response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503, "backend/drop_core/views.py: a storage outage is a 503, not a miss; the link is still valid")
        self.assertEqual(response["Retry-After"], "30")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertNotIn("<html", response.content.decode())


@override_settings(DEBUG=False)
class ArtifactMissThatCannotBeDrawnTests(TestCase):
    longMessage = False

    def setUp(self):
        user = User.objects.create_user(username="owner", password="pw12345678")
        self.workspace = get_or_create_workspace(user)
        self.key, _raw = APIKey.create_key(self.workspace, "key")

    def expired_token(self):
        artifact = upload_artifact(self.workspace, self.key, "late.txt", b"hello", "text/plain")
        Artifact.objects.filter(id=artifact.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        return artifact.token

    def test_the_fallback_is_the_same_for_every_kind_of_miss_and_has_the_spec_words(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()), self.assertLogs("pages.errors", level="ERROR"):
            unknown = self.client.get(f"/a/{UNKNOWN_TOKEN}")
            expired = self.client.get(f"/a/{self.expired_token()}")
        self.assertEqual(unknown.status_code, 404, "backend/pages/errors.py: artifact_miss() keeps the 404 when the page cannot be drawn")
        self.assertIn(WORDS, unknown.content.decode())
        self.assertIn("<h1>Nothing here.</h1>", unknown.content.decode())
        self.assertEqual(everything_a_reader_gets(unknown), everything_a_reader_gets(expired))
        self.assertEqual(unknown["Cache-Control"], "no-store")
        self.assertEqual(unknown["X-Robots-Tag"], "noindex, nofollow")

    def test_the_log_of_that_failure_holds_no_token(self):
        # The token is in the path the view was given, in the Resolver404 quoted for a slash, and in the message of the
        # FileNotFoundError that a missing stored file raises: the log must carry none of them.
        artifact = upload_artifact(self.workspace, self.key, "late.txt", b"hello", "text/plain")
        token = artifact.token
        with mock.patch("drop_core.storage.LocalStorage.open", side_effect=FileNotFoundError(f"/media/artifacts/{token}")), \
                mock.patch("pages.errors.render_to_string", side_effect=RuntimeError("template failed")), \
                self.assertLogs("drop_core.views", level="WARNING"), \
                self.assertLogs("pages.errors", level="ERROR") as missing_object:
            response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 404)
        with mock.patch("pages.registry.menu_groups", side_effect=RuntimeError("menu failed")), \
                self.assertLogs("pages.errors", level="ERROR") as resolver_404:
            self.client.get(f"/a/{token}/more")
        for output in (missing_object.output, resolver_404.output):
            with self.subTest(log=output[0][:60]):
                self.assertNotIn(token, "\n".join(output),
                                 "backend/pages/errors.py: log_failure() must cut the exception's __context__; it quotes the token "
                                 "(the stored file's name, the Resolver404's path) and this logger does not redact")
        self.assertIn("template failed", "\n".join(missing_object.output), "the failure itself is still logged, with its traceback")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_artifact_miss`
Expected: one ERROR, the same `ImportError: cannot import name 'errors' from 'pages'` (it imports `pages.errors` and `pages.tests_errors`), `FAILED (errors=1)`.


- [ ] **Step 3: Write the failing tests for the signup and verification 429s**

Create `backend/drop_core/tests_throttled_pages.py`:

```python
"""The 429s of signup and of verification emails (spec 4.9): the shell's throttled page, with Retry-After, the wait said in words, and the same bytes for every visitor refused for the same reason."""
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from drop_core import throttle
from drop_core.models import EmailSendLog, EmailVerification

User = get_user_model()
IP = "9.9.9.9"


def signup_data(name):
    return {"username": name, "password1": "s3cur3pass!!", "password2": "s3cur3pass!!", "email": f"{name}@example.com"}


def page(response):
    """The visible page: from <main> to </main>. A response with no <main> is a bare string, not the shell's page."""
    html = response.content.decode()
    if "<main" not in html:
        raise AssertionError(f"status {response.status_code} with no <main>, the body being {html[:80]!r}: answer with throttle.signup_throttled_response() "
                             "or throttle.verification_throttled_response() (backend/drop_core/views.py), which draw backend/templates/registration/throttled.html on the shell")
    return html[html.index("<main"):html.index("</main>")]


@override_settings(DEBUG=False)
class SignupThrottleTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def setUp(self):
        for _ in range(10):
            EmailSendLog.objects.create(ip_address=IP, purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT)

    def refused(self, name="first"):
        return self.client.post("/accounts/signup/", signup_data(name), REMOTE_ADDR=IP)

    def test_a_tenth_attempt_in_the_hour_gets_the_shells_throttled_page(self):
        response = self.refused()
        html = response.content.decode()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many signup attempts", page(response), "backend/drop_core/views.py: signup() answers with throttle.signup_throttled_response(), not a bare string")
        self.assertIn('<main id="main"', html, "backend/templates/registration/throttled.html: the page is on the shell")
        self.assertIn("<title>Too many signup attempts | Veilfile</title>", html, "backend/templates/registration/throttled.html: the title is the throttle's, then | Veilfile")
        self.assertEqual(response["Retry-After"], "3600", "backend/drop_core/throttle.py: signup_throttled_response() asks for the hour the limit counts")
        self.assertFalse(User.objects.filter(username="first").exists())

    def test_it_is_the_same_page_whoever_asks_and_whatever_they_posted(self):
        first = self.refused("zq-marker-one")
        second = self.refused("zq-marker-two")
        self.assertEqual(first.content, second.content,
                         "backend/templates/registration/throttled.html: the throttled page holds nothing of the request or the visitor")
        self.assertNotIn("csrfmiddlewaretoken", first.content.decode(), "backend/templates/registration/throttled.html: no form, no CSRF token")
        self.assertNotIn("zq-marker", first.content.decode())
        self.assertNotIn(IP, first.content.decode())

    def test_the_wait_reads_as_a_sentence(self):
        self.assertIn("Wait about 60 minutes, then try again.", page(self.refused()),
                      "backend/templates/registration/throttled.html: the sentence is `Wait about {{ retry_wait }}, then try again.`")

    def test_a_visitor_who_is_not_signed_in_is_offered_the_sign_in_pages(self):
        said = page(self.refused())
        self.assertIn('href="/accounts/login/"', said, "backend/templates/registration/throttled.html: sign_in_links is true by default; it shows Log in")
        self.assertIn(">Forgot password?<", said, "backend/templates/registration/throttled.html: sign_in_links is true by default; it shows Forgot password?")
        self.assertNotIn("Back to dashboard", said)

    def test_a_visitor_under_the_limit_still_gets_the_form(self):
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)


@override_settings(DEBUG=False)
class VerificationThrottleTests(TestCase):
    longMessage = False

    def signed_in(self, name):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345678")
        client = Client()
        client.force_login(user)
        return user, client

    def arrange(self, user, reason):
        """Put the user in the state resend_allowed() refuses for this reason."""
        record, _ = EmailVerification.objects.get_or_create(user=user, defaults={"email": user.email})
        if reason == "too_soon":
            record.last_sent_at = timezone.now()
        else:
            record.last_sent_at = timezone.now() - timedelta(hours=2)
        record.save()
        if reason == "account_limit":
            for _ in range(5):
                EmailSendLog.objects.create(email=user.email, user=user, purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT)
        if reason == "address_limit":
            for _ in range(5):
                EmailSendLog.objects.create(email=user.email, purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT)

    def refused(self, name, reason, action="resend"):
        user, client = self.signed_in(name)
        self.arrange(user, reason)
        data = {"action": action}
        if action == "update_email":
            data.update(email=user.email, password="pw12345678")
        return client.post("/accounts/verify-email/", data)

    def test_each_reason_gets_the_shells_throttled_page_and_its_own_wait(self):
        for reason, title, wait, said in (
            ("too_soon", "Verification email already sent", "60", "Wait about 1 minute, then try again."),
            ("account_limit", "Too many verification emails", "3600", "Wait about 60 minutes, then try again."),
            ("address_limit", "Too many verification emails", "86400", "Wait about 24 hours, then try again."),
        ):
            with self.subTest(reason=reason):
                response = self.refused(f"user-{reason}".replace("_", "-"), reason)
                self.assertEqual(response.status_code, 429)
                self.assertIn(title, page(response), "backend/drop_core/views.py: email_verification() answers with throttle.verification_throttled_response(), not a bare string")
                self.assertIn('<main id="main"', response.content.decode(), "backend/templates/registration/throttled.html: the page is on the shell")
                self.assertIn(f"<title>{title} | Veilfile</title>", response.content.decode(), "backend/templates/registration/throttled.html: the title is the throttle's, then | Veilfile")
                self.assertEqual(response["Retry-After"], wait, f"backend/drop_core/throttle.py: VERIFICATION_RETRY_AFTER[{reason!r}] is {wait}")
                self.assertIn(said, page(response), f"backend/drop_core/throttle.py: wait_in_words({wait}) must give the words in \"{said}\"")

    def test_changing_the_address_is_refused_the_same_way(self):
        response = self.refused("changer", "too_soon", action="update_email")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Verification email already sent", page(response))
        self.assertEqual(response["Retry-After"], "60")

    def test_the_page_is_the_same_for_every_visitor_refused_for_one_reason(self):
        for reason in ("too_soon", "account_limit", "address_limit"):
            with self.subTest(reason=reason):
                first = self.refused(f"first-{reason}".replace("_", "-"), reason)
                second = self.refused(f"second-{reason}".replace("_", "-"), reason)
                self.assertEqual(first.content, second.content,
                                 "backend/templates/registration/throttled.html: the throttled page holds nothing of the visitor or their address")
                self.assertNotIn("csrfmiddlewaretoken", first.content.decode())
                self.assertNotIn("example.com", first.content.decode())

    def test_a_signed_in_visitor_is_offered_the_way_back_not_the_sign_in_pages(self):
        for reason in ("too_soon", "account_limit", "address_limit"):
            with self.subTest(reason=reason):
                said = page(self.refused(f"back-{reason}".replace("_", "-"), reason))
                self.assertIn('<p class="form-links"><a class="label interactive" href="/app/">Back to dashboard</a></p>', said,
                              "backend/drop_core/throttle.py: verification_throttled_response() passes sign_in_links=False, and "
                              "backend/templates/registration/throttled.html then offers the dashboard as a secondary link")
                self.assertNotIn("/accounts/login/", said, "backend/templates/registration/throttled.html: a signed-in visitor is not sent to the sign-in page")
                self.assertNotIn("Forgot password?", said)

    def test_a_day_is_hours_and_one_minute_is_singular(self):
        said = page(self.refused("daily", "address_limit"))
        self.assertNotIn("1440", said, "backend/drop_core/throttle.py: wait_in_words() says hours from two hours up")
        self.assertIsNone(re.search(r"\b1 minutes\b", page(self.refused("quick", "too_soon"))),
                          "backend/drop_core/throttle.py: wait_in_words() says \"1 minute\", singular")


class WaitInWordsTests(SimpleTestCase):
    longMessage = False

    def test_minutes_under_two_hours_then_hours(self):
        for seconds, words in (
            (0, "1 minute"), (30, "1 minute"), (60, "1 minute"), (119, "1 minute"), (120, "2 minutes"), (900, "15 minutes"),
            (3600, "60 minutes"), (7140, "119 minutes"), (7200, "2 hours"), (10800, "3 hours"), (86400, "24 hours"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(throttle.wait_in_words(seconds), words,
                                 f"backend/drop_core/throttle.py: wait_in_words({seconds}) must be \"{words}\" (whole minutes under 120, else whole hours)")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_throttled_pages`
Expected: `Ran 11 tests`, `FAILED (failures=11, errors=11)`. Seven tests fail on the bare strings: three of `SignupThrottleTests` (`test_a_tenth_attempt_in_the_hour_gets_the_shells_throttled_page`, `test_the_wait_reads_as_a_sentence`, `test_a_visitor_who_is_not_signed_in_is_offered_the_sign_in_pages`) and four of `VerificationThrottleTests` (`test_each_reason_gets_the_shells_throttled_page_and_its_own_wait` with three sub-tests, `test_changing_the_address_is_refused_the_same_way`, `test_a_signed_in_visitor_is_offered_the_way_back_not_the_sign_in_pages` with three sub-tests, `test_a_day_is_hours_and_one_minute_is_singular`), each with `AssertionError: status 429 with no <main>, the body being 'Too many signup attempts from this address. Try again later.': answer with throttle.signup_throttled_response() or throttle.verification_throttled_response() ...` (or `'Too many verification emails sent. Try again later.'`). `WaitInWordsTests.test_minutes_under_two_hours_then_hours` errors in each of its eleven sub-tests with `AttributeError: module 'drop_core.throttle' has no attribute 'wait_in_words'`. The other three tests pass: two compare bare strings that are equal and one fetches the signup form.


- [ ] **Step 4: Write the error pages**

Create `backend/pages/errors.py`:

```python
"""The error pages (spec 4.9): 404, 403, a failed CSRF check and 500, and the one page for a file link that leads nowhere.

404, 403 and the CSRF failure are drawn on the shell (templates/404.html, 403.html, 403_csrf.html). 500 is not: it is
templates/500.html, which needs no request, no context processor and no static file, so it still draws when something
deeper is broken.

Rules these views keep (tests: pages/tests_errors.py, pages/tests_artifact_miss.py):

- Nothing about the failure reaches the page: not the exception, not the path, not the CSRF reason. Two requests that
  fail for different reasons get the same words.
- A page that cannot be drawn is never a worse error. A 404 or 403 whose shell template raises (a missing static file,
  a database that is down) is answered with the bare page below and its own status, not with a 500.
- /a/<token> answers a miss from one function, artifact_miss(), for every kind of miss.
"""
import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

# The words of the bare page when the shell cannot be drawn: (title, message), by the template that failed. 500 has none:
# templates/500.html says its own words when it is given nothing.
BARE_WORDS = {
    "404.html": ("Not found.", "There is nothing at this address."),
    "403.html": ("Not allowed.", "You do not have access to this page."),
    "403_csrf.html": ("Form not sent.", "Go back, reload that page and send the form again."),
}
# The same words as templates/404.html says for a file link that leads nowhere (spec 4.9). A test keeps these and the
# templates' words together.
GONE_WORDS = ("Nothing here.", "If this link was shared with you, its moment may have passed.")


def log_failure(exc, message, *args):
    """Log `exc` at ERROR with the traceback of its own frames and nothing it was raised while handling.

    Whatever was being handled is the exception's __context__: a Resolver404 quotes the path, a FileNotFoundError the
    stored file's name, and for /a/<token> both are the token. This logger does not redact paths (django.request does),
    so the chain is cut before it is written.
    """
    exc.__context__ = None
    exc.__cause__ = None
    logger.error(message, *args, exc_info=exc)


def bare_page(status, words=None):
    """templates/500.html with this status. It is the 500 itself, and what a shell page falls back on when it cannot be
    drawn. It is drawn with no request, no context processor and no static file, so whatever was broken does not matter.
    `words` is (title, message), or None for the 500's own. If even this cannot be drawn, one line of plain text."""
    context = {"title": words[0], "message": words[1]} if words else {}
    try:
        return HttpResponse(render_to_string("500.html", context), status=status)
    except Exception as exc:
        log_failure(exc, "500.html could not be drawn; sent plain text")
        return HttpResponse("Something went wrong.", status=status, content_type="text/plain; charset=utf-8")


def shell_error_page(request, template, status):
    """`template` drawn on the shell for this request, with this status.

    shell_page is emptied: an error page has no registry entry, even when the view that failed belongs to one (a head
    with that page's canonical address and layout would claim the error is the page). If the shell cannot be drawn,
    the bare page answers with the same status, and the failure is logged (log_failure) without the path.
    """
    try:
        return render(request, template, {"shell_page": None}, status=status)
    except Exception as exc:
        log_failure(exc, "%s could not be drawn on the shell; sent the bare page instead", template)
        return bare_page(status, BARE_WORDS[template])


def not_found(request, exception=None):
    """handler404. `exception` is never read: it names which kind of 404 this is."""
    return shell_error_page(request, "404.html", 404)


def permission_denied(request, exception=None):
    """handler403. `exception` is never read."""
    return shell_error_page(request, "403.html", 403)


def csrf_failure(request, reason=""):
    """CSRF_FAILURE_VIEW. `reason` is never read: it can quote the Origin header, and Django logs it already."""
    return shell_error_page(request, "403_csrf.html", 403)


def server_error(request=None):
    """handler500. `request` is never read: the page is drawn without it."""
    return bare_page(500)


def artifact_miss():
    """The answer for /a/<token> when the link leads nowhere: the token is unknown, expired, revoked, held, or its stored
    file is gone (spec 4.9). One function, so no case can differ from another.

    The page is drawn with no request, so it never reads the session: it is the same for everyone who asks, it adds
    no Vary header and it costs no query beyond the lookup of the token. It carries the headers of a file that is
    found, because a bearer link's page is as private as its bytes: nothing caches it and nothing indexes it.
    """
    try:
        response = HttpResponse(render_to_string("404.html", {"gone_link": True}), status=404)
    except Exception as exc:
        log_failure(exc, "404.html could not be drawn for a file link; sent the bare page instead")
        response = bare_page(404, GONE_WORDS)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
```

Create `backend/templates/404.html`:

```django
{% extends "layouts/chapter.html" %}
{# Page: not found (spec 4.9). Context: gone_link, true for a /a/<token> that leads nowhere (pages.errors.artifact_miss), else nothing. #}
{# Never print the exception, request_path or anything else of the request: two kinds of miss must read the same, and /a/<token> must not reveal whether a token ever existed (a test reads this file for those names). #}
{% block title %}{% if gone_link %}Nothing here{% else %}Not found{% endif %} | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block preload_heading %}{% endblock %}
{% block chapter %}
{% if gone_link %}
<h1 class="chapter__title">Nothing here.</h1>
<p>If this link was shared with you, its moment may have passed.</p>
{% else %}
<h1 class="chapter__title">Not found.</h1>
<p>There is nothing at this address. Check it for a typo, or start from the <a href="{% url 'landing' %}">home page</a>.</p>
{% endif %}
{% endblock %}
```

Create `backend/templates/403.html`:

```django
{% extends "layouts/chapter.html" %}
{# Page: not allowed (spec 4.9), for a view that raised PermissionDenied. Context: none. #}
{# Never print the exception or anything else of the request (a test reads this file for those names). #}
{% block title %}Not allowed | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block preload_heading %}{% endblock %}
{% block chapter %}
<h1 class="chapter__title">Not allowed.</h1>
<p>You do not have access to this page. If you expected to, <a href="{% url 'login' %}">sign in</a> with the account that does.</p>
{% endblock %}
```

Create `backend/templates/403_csrf.html`:

```django
{% extends "layouts/chapter.html" %}
{# Page: a form that failed its check (spec 4.9), pages.errors.csrf_failure. Context: none. #}
{# Never print the reason Django gives (it can quote the Origin header) or anything else of the request (a test reads this file for those names). #}
{% block title %}Form not sent | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}
{% block preload_heading %}{% endblock %}
{% block chapter %}
<h1 class="chapter__title">Form not sent.</h1>
<p>The page this form was on is out of date, or your browser is blocking the cookie that checks forms. Go back, reload that page and send the form again.</p>
{% endblock %}
```

Create `backend/templates/500.html`:

```django
<!DOCTYPE html>
{# Page: the 500, and the bare page a 404 or 403 falls back on when the shell cannot be drawn (pages/errors.py, spec 4.9). #}
{# Context: none needed. title and message, when given, replace the 500's own words. This page must draw when everything else is broken: no extends, no include, no static file, no url tag, no context processor, and every style inline. Its colours are the values in static/css/tokens.css (a test compares them). #}
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{{ title|default:"Something went wrong."|cut:"." }} | Veilfile</title>
<script>
try { var theme = localStorage.getItem("veilfile-theme"); if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme); } catch (error) {}
</script>
<style>
:root { --paper: #ebe6de; --ink: #2c2925; --muted: #534e47; color-scheme: light; }
:root[data-theme="dark"] { --paper: #1b1917; --ink: #e6e0d6; --muted: #aea79c; color-scheme: dark; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --paper: #1b1917; --ink: #e6e0d6; --muted: #aea79c; color-scheme: dark; }
}
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; padding: calc(5.5vh + 3rem) 5% 5.5vh; display: grid; place-items: center; background: var(--paper); color: var(--ink); font: 18px/1.62 Georgia, "Times New Roman", serif; }
.name { position: absolute; top: 5.5vh; left: 5%; font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); text-decoration: none; }
main { width: 100%; max-width: 40rem; }
h1 { margin: 0 0 1.5rem; font-size: clamp(28px, 2.9vw, 44px); font-style: italic; font-weight: 400; line-height: 1.15; overflow-wrap: anywhere; }
p { margin: 0 0 1rem; overflow-wrap: anywhere; }
a { color: inherit; text-decoration: underline dotted; text-underline-offset: .22em; }
a:focus-visible { outline: 1px solid var(--ink); outline-offset: 3px; }
</style>
</head>
<body>
<a class="name" href="/">Veilfile</a>
<main>
<h1>{{ title|default:"Something went wrong." }}</h1>
<p>{{ message|default:"This one is on our side. Wait a minute, then try again. If it keeps happening, write to support@veilfile.com." }}</p>
<p><a href="/">Go to the home page</a></p>
</main>
</body>
</html>
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_errors`
Expected: `Ran 34 tests`, `FAILED (failures=4, errors=4)`, all about the site not naming the handlers yet: `HandlersAreRegisteredTests` (`AssertionError: backend/config/urls.py: handler403 must be "pages.errors.permission_denied"`, and the same for 404 and 500; `AttributeError: module 'config.urls' has no attribute 'handler403'`, and the same for 404 and 500), `CsrfFailurePageTests.test_the_site_names_the_view` (`AssertionError: backend/config/settings.py: CSRF_FAILURE_VIEW must be pages.errors.csrf_failure`) and `AnErrorPageThatCannotBeDrawnTests.test_a_failed_csrf_check_stays_a_403` (Django's own failure view does not catch the broken shell).


- [ ] **Step 5: Name the handlers and the CSRF failure view**

In `backend/config/urls.py`, replace

```
    path("", pages_views.landing, name="landing"),  # the threshold
]
```

with

```
    path("", pages_views.landing, name="landing"),  # the threshold
]

# Error pages (spec 4.9), drawn by pages/errors.py: 404 and 403 on the shell, 500 self-contained.
# A failed CSRF check has its own view, CSRF_FAILURE_VIEW in settings.py.
handler403 = "pages.errors.permission_denied"
handler404 = "pages.errors.not_found"
handler500 = "pages.errors.server_error"
```

In `backend/config/settings.py`, replace

```
CSRF_TRUSTED_ORIGINS = _explicit_csrf or _derived_csrf
```

with

```
CSRF_TRUSTED_ORIGINS = _explicit_csrf or _derived_csrf
# A POST that fails the CSRF check gets the shell's "Form not sent" page (pages/errors.py, spec 4.9).
CSRF_FAILURE_VIEW = "pages.errors.csrf_failure"
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_errors && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py check`
Expected: `Ran 34 tests` and `OK`; then `System check identified no issues (0 silenced).` (Django's own checks, `urls.E007` for the handlers and `security.E102` for the failure view, bind them to the signatures Django calls them with.)


- [ ] **Step 6: `/a/<token>` answers every miss with one page**

Run first: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_artifact_miss`
Expected: `Ran 11 tests`, `FAILED (failures=10)`. With `Http404` still raised, every miss is the generic 404 page, which is the same for all of them (so `test_every_kind_of_miss_is_the_same_answer_to_the_byte` passes, as it must keep doing), but it is not the spec's page, it reads the session and `DEBUG` shows the technical page: `test_the_page_says_only_the_spec_words` (`AssertionError: backend/templates/404.html: the heading for a file link is Nothing here.`), `test_it_never_reads_the_session` (`AssertionError: backend/pages/errors.py: artifact_miss() must draw the page with no request; the shell's menu reads the session, which adds Vary: Cookie`), five sub-tests of `test_with_debug_on_the_miss_is_still_this_page` (`AssertionError: backend/drop_core/views.py: serve_artifact() returns artifact_miss(); it must not raise Http404, which DEBUG answers with a page that names the token`), the two tests of `ArtifactMissThatCannotBeDrawnTests` (the spec's words in the bare page; no token in the log), and `test_it_is_as_private_as_a_file_that_is_found` (`AssertionError: backend/pages/errors.py: artifact_miss() sets the headers a found file has`).

In `backend/drop_core/views.py`, replace

```
from rest_framework.views import APIView

from .auth import (
```

with

```
from rest_framework.views import APIView

from pages.errors import artifact_miss

from .auth import (
```

In `backend/drop_core/views.py`, replace

```
    try:
        artifact = Artifact.objects.get(token=token)
    except Artifact.DoesNotExist:
        raise Http404("Artifact not found.")
    if artifact.is_expired():
        raise Http404("Artifact expired.")
    if artifact.safety_status == "held":
        # Do not confirm the object exists.
        raise Http404("Artifact not found.")
    try:
        fileobj = get_storage().open(artifact.token)
    except FileNotFoundError:
        logger.warning("serve: stored object missing for artifact id=%s", artifact.id)
        raise Http404("Artifact file missing.")
    except Exception as exc:
        if _is_missing_object(exc):
            raise Http404("Artifact file missing.")
```

with

```
    # Every kind of miss is the same answer, from one function (pages.errors.artifact_miss), so a link
    # that never existed, one that expired and one whose object is gone cannot be told apart.
    try:
        artifact = Artifact.objects.get(token=token)
    except Artifact.DoesNotExist:
        return artifact_miss()
    if artifact.is_expired():
        return artifact_miss()
    if artifact.safety_status == "held":
        # Do not confirm the object exists.
        return artifact_miss()
    try:
        fileobj = get_storage().open(artifact.token)
    except FileNotFoundError:
        logger.warning("serve: stored object missing for artifact id=%s", artifact.id)
        return artifact_miss()
    except Exception as exc:
        if _is_missing_object(exc):
            return artifact_miss()
```

`Http404` stays imported: `llms_txt` still raises it.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_artifact_miss config.tests_fix_platform.LogRedactionTests drop_core.tests_fix_upload.ServeStorageFailureTests drop_core.tests_fix_storage.UploadAndServeFailureTests drop_core.tests_fix_storage.RevokeTests drop_core.tests.VeilfileCoreTests drop_core.tests.SafetyTests`
Expected: `OK`. The existing tests that name a miss (`test_serve_unknown_token_404`, `test_expired_token_404`, `test_held_artifact_returns_404`, the storage-failure and revoke tests) read the status, which is still 404, and `test_django_request_log_lines_are_redacted` still sees exactly `["WARNING:django.request:Not Found: /a/<redacted>"]`, because Django logs a returned 4xx response once, as it logs a raised `Http404`.


- [ ] **Step 7: The signup and verification email 429s on the throttled page**

This step teaches `throttled_response` to say the wait in words and to leave out the sign-in links, then uses it for the three bare 429s. In `backend/drop_core/throttle.py`, replace

```
from .email_verification import SIGNUP_MAX_PER_IP_PER_HOUR, client_ip, throttle_ip
```

with

```
from .email_verification import (
    RESEND_MIN_INTERVAL,
    SIGNUP_MAX_PER_IP_PER_HOUR,
    client_ip,
    throttle_ip,
)
```

In `backend/drop_core/throttle.py`, replace

```
def throttled_response(request, title, message, retry_after_seconds):
    response = render(
        request,
        "registration/throttled.html",
        {
            "title": title,
            "message": message,
            "retry_minutes": max(1, retry_after_seconds // 60),
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after_seconds)
    return response
```

with

```
def wait_in_words(seconds):
    """How long a visitor is asked to wait, as words for the throttled page: "1 minute", "15 minutes", "60 minutes",
    "24 hours". Whole minutes (at least one) under two hours, then whole hours."""
    minutes = max(1, seconds // 60)
    if minutes < 120:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    return f"{minutes // 60} hours"


def throttled_response(request, title, message, retry_after_seconds, sign_in_links=True):
    """The 429 page, with its Retry-After.

    Everything the page shows is fixed by the throttle that refuses (its title, its message, how long it asks for), never
    by the visitor, the account or the address, so two visitors refused for the same reason get the same bytes.
    `sign_in_links` says which secondary links the page offers: Log in and Forgot password? (the default, for a visitor who
    is signed out) or Back to dashboard (for one who is signed in, who has no use for the sign-in page).
    """
    response = render(
        request,
        "registration/throttled.html",
        {
            "title": title,
            "message": message,
            "retry_wait": wait_in_words(retry_after_seconds),
            "sign_in_links": sign_in_links,
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after_seconds)
    return response
```

Append to the end of `backend/drop_core/throttle.py` (after `record_signup_attempt`), leaving two blank lines before it:

```python
def signup_throttled_response(request):
    """The 429 for a signup from an address (or IPv6 network) that has tried too often in the last hour."""
    return throttled_response(
        request,
        "Too many signup attempts",
        "Signup is paused for this address after repeated attempts.",
        SIGNUP_WINDOW_SECONDS,
    )


# How long each limit of email_verification.resend_allowed() asks a visitor to wait, by the reason it returns.
VERIFICATION_RETRY_AFTER = {
    "too_soon": int(RESEND_MIN_INTERVAL.total_seconds()),
    "account_limit": 60 * 60,
    "address_limit": 24 * 60 * 60,
}


def verification_throttled_response(request, reason):
    """The 429 for a verification email that resend_allowed() refused, `reason` being what it returned.

    "too_soon" (the visitor asked a moment ago) has words of its own; the hourly limit of the account and the daily
    limit of the address read alike, each with the wait it asks for. The page holds nothing of the account or the
    address, so it is the same for every visitor who is refused for the same reason. The view is login_required, so the
    visitor is signed in: the page offers the dashboard, not the sign-in page.
    """
    wait = VERIFICATION_RETRY_AFTER.get(reason, VERIFICATION_RETRY_AFTER["account_limit"])
    if reason == "too_soon":
        return throttled_response(
            request,
            "Verification email already sent",
            "One went out a moment ago. Check your inbox and your spam folder.",
            wait,
            sign_in_links=False,
        )
    return throttled_response(
        request,
        "Too many verification emails",
        "Verification emails are paused for a while after repeated requests.",
        wait,
        sign_in_links=False,
    )
```

In `backend/drop_core/views.py`, replace

```
            return HttpResponse(
                "Too many signup attempts from this address. Try again later.",
                status=429,
            )
```

with

```
            return throttle.signup_throttled_response(request)
```

In `backend/drop_core/views.py`, replace

```
                allowed, _reason = resend_allowed(request.user, email)
                if not allowed:
                    return HttpResponse(
                        "Too many verification emails sent. Try again later.",
                        status=429,
                    )
```

with

```
                allowed, reason = resend_allowed(request.user, email)
                if not allowed:
                    return throttle.verification_throttled_response(request, reason)
```

In `backend/drop_core/views.py`, replace

```
            allowed, _reason = resend_allowed(request.user, record.email)
            if not allowed:
                return HttpResponse(
                    "Too many verification emails sent. Try again later.",
                    status=429,
                )
```

with

```
            allowed, reason = resend_allowed(request.user, record.email)
            if not allowed:
                return throttle.verification_throttled_response(request, reason)
```

`HttpResponse` stays imported: the 503 above still builds one.

Last, the page itself. Task 2 wrote `backend/templates/registration/throttled.html` for the sign-in and reset limits. Check that it reads as quoted:

Run: `grep -n "retry_minutes\|form-links\|block title" backend/templates/registration/throttled.html`
Expected: three lines, the `{% block title %}{{ title }} | Veilfile{% endblock %}` line, the `Wait about {{ retry_minutes }} minutes, then try again.` line and the `<p class="form-links">` line.

In `backend/templates/registration/throttled.html`, replace

```
{% block title %}{{ title }} | Veilfile{% endblock %}
```

with

```
{% block title %}{{ title }} | Veilfile{% endblock %}
{# Context: title, message, retry_wait and sign_in_links, all fixed by the throttle that refuses (drop_core/throttle.py), never taken from the visitor, the account or the address. #}
```

In `backend/templates/registration/throttled.html`, replace

```
Wait about {{ retry_minutes }} minutes, then try again.
```

with

```
Wait about {{ retry_wait }}, then try again.
```

In `backend/templates/registration/throttled.html`, replace

```
<p class="form-links"><a class="label interactive" href="{% url 'login' %}">Log in</a> <span aria-hidden="true">·</span> <a class="label interactive" href="{% url 'password_reset' %}">Forgot password?</a></p>
```

with

```
{% if sign_in_links %}
<p class="form-links"><a class="label interactive" href="{% url 'login' %}">Log in</a> <span aria-hidden="true">·</span> <a class="label interactive" href="{% url 'password_reset' %}">Forgot password?</a></p>
{% else %}
<p class="form-links"><a class="label interactive" href="/app/">Back to dashboard</a></p>
{% endif %}
```

(The comment says nothing of a `user`, a `request` or a `csrf` token on purpose: a test of Task 2's reads this file's source for those words, because the same page is served for any address asked about.)

Nothing reads the old `retry_minutes` any more:

Run: `grep -rn "retry_minutes" backend --include='*.html' --include='views.py' --include='throttle.py'`
Expected: no output.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_throttled_pages drop_core.tests_sign_in_pages.ThrottledPageTests drop_core.tests_fix_auth.LoginThrottleTests drop_core.tests_fix_auth.PasswordResetThrottleTests`
Expected: `OK`. The tests are the 11 of `drop_core.tests_throttled_pages`, the 11 of the two throttle classes on main, and Task 2's `ThrottledPageTests`. The sign-in page still says "Wait about 15 minutes" with its Log in and Forgot password? links, the reset page "Wait about 60 minutes", and a real and a missing account still get the same bytes.


- [ ] **Step 8: Hold the three new test modules to `longMessage = False`**

`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has no test class, or a test class whose `longMessage` is not `False`, and its message names the file and the fix. A failure that compares a page would otherwise print the page before the message. In `backend/pages/tests_site_rules.py`, replace

```
"drop_core.tests_oauth_consent")
```

with

```
"drop_core.tests_oauth_consent",
                        "pages.tests_errors", "pages.tests_artifact_miss", "drop_core.tests_throttled_pages")
```

(Task 5 appended `"drop_core.tests_oauth_consent"` last. Run `grep -n -A4 "^READING_TEST_MODULES" backend/pages/tests_site_rules.py` first: if the tuple ends with another name, add the three after that one, on a new line indented to the opening parenthesis.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests`
Expected: `Ran 2 tests` and `OK`. (A mutation check: take `longMessage = False` out of one class of `pages/tests_errors.py` and the first test fails with `['ServerErrorPageTests'] != []` and a message naming `backend/pages/tests_errors.py` and the fix; put it back.)


- [ ] **Step 9: Run the error-page tests together**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_errors pages.tests_artifact_miss drop_core.tests_throttled_pages pages.tests_site_rules pages.tests_content`
Expected: `OK`. The shell rules in `pages.tests_site_rules` and `pages.tests_content` find the three shell templates by what they extend (no inline `style`, no `<header>` or `<footer>` in `<main>`, every character in all three fonts), and `test_every_static_path_in_a_template_exists` and `test_every_include_and_extends_names_a_template_that_exists` read all four. `ReadableFailuresTests` now holds the three new modules to `longMessage = False`.


- [ ] **Step 10: Check what else the change could have broken, then run the whole suite**

No existing test changes, but the one tuple of Step 8. What was searched, and the result:
- Bodies: no existing test reads the body of a miss, of a 403, or of the signup and verification 429s. `grep -rln "Artifact expired\|Artifact file missing\|Too many signup attempts\|Too many verification emails" backend --include='tests*.py'` prints only `backend/drop_core/tests_throttled_pages.py`, this task's own. The one other "Artifact not found." (`drop_core/tests.py` line 797, `drop_core/views.py` line 486) is the REST API's JSON `not_found`, which this task does not touch. Both CLIs and `docs/API.md` read the status of a `/a/<token>` miss, which is still 404.
- The two tests that count log records (`config.tests_fix_platform.LogRedactionTests.test_django_request_log_lines_are_redacted` and `test_csrf_rejection_log_lines_are_redacted`) still see one record each, and that is what they assert; `test_csrf_rejection_log_lines_are_redacted` now also gets the shell's page for its 403.
- The tests that name the 429 pages' words (`drop_core.tests_fix_auth` lines 557 and 638, `drop_core.tests_fix_device` line 500) are for the sign-in, reset and device pages, whose words this task does not change. The sign-in and reset pages keep their sentences too: `wait_in_words(900)` is "15 minutes" and `wait_in_words(3600)` is "60 minutes", which Task 2's `ThrottledPageTests` pins, and they keep their Log in and Forgot password? links, because `sign_in_links` is true unless a caller says otherwise. The context no longer holds `retry_minutes` (Step 7 checks that nothing reads it).
- The signup and verification limit tests (`drop_core.tests.EmailVerificationTests`: `test_resend_second_immediate_attempt_throttled`, `test_resend_per_account_hourly_limit`, `test_resend_per_address_daily_limit`, `test_signup_rate_limited_per_ip`) assert the status 429, which the shell's page keeps.
- Tests that fetch an unknown address (`test_unknown_slug_404` for the Journal and Docs, the catalog's 404 for non-staff, `test_unused_dot_routes_are_not_mounted`, `test_an_unknown_post_is_a_plain_404`) assert the status, and `pages.tests_head` and `pages.tests_reading_family` assert that `shell_page` is falsy for those addresses, which `shell_page(request)` still returns (the handler only hands the template an empty one).

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK`. This task adds 56 tests (34 in `pages.tests_errors`, 11 in `pages.tests_artifact_miss`, 11 in `drop_core.tests_throttled_pages`). On main with only this task applied (and Task 2's throttled page in place), 1351 tests ran and passed: the 1295 that were there and these 56.


- [ ] **Step 11: Commit**

```bash
git add backend/pages/errors.py backend/templates/404.html backend/templates/403.html backend/templates/403_csrf.html backend/templates/500.html backend/pages/tests_errors.py backend/pages/tests_artifact_miss.py backend/drop_core/tests_throttled_pages.py backend/pages/tests_site_rules.py backend/config/urls.py backend/config/settings.py backend/drop_core/views.py backend/drop_core/throttle.py backend/templates/registration/throttled.html
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Error pages on the shell: 404, 403 and a failed form check, a self-contained 500, one page for every miss on /a/<token>, and the signup and verification 429s on the throttled page"
```

---

### Task 7: Browser checks for the forms, and the `site-forms` CI job

The forms, the device flow, OAuth consent and the error pages that Tasks 1 to 6 moved onto the shell are looked at in a browser, in each state they have, as a new part of the browser checks, `forms`, run by a new CI job, `site-forms`, with its own 20 minutes. The Django tests of Tasks 1 to 6 hold the words, field names and statuses; this part checks what only a browser sees: nothing sideways at 320, 375 and 1280, axe, a visible label on every field and every error tied to its field, the ink wash on every button, 16 px text in a field on a phone, a focused field clear of the phone's top fade with the keyboard up, autofill, Enter, and `?next=` through sign in to the device page and OAuth consent. It needs more than the staff login, so a second dev-only script, `make_form_fixtures.py`, behind the same guard, makes a verified user's session, device codes, OAuth clients and the rest.

**Files:**
- Create: `site-tests/make_form_fixtures.py`, `backend/pages/tests_form_fixtures.py`
- Modify: `site-tests/run_site_checks.py`, `backend/pages/tests_site_parts.py`, `backend/pages/tests_site_rules.py` (one name in `READING_TEST_MODULES`), `.github/workflows/ci.yml`, `.gitignore`, `AGENTS.md` (the repo-map line for `site-tests/`, the "Site checks" section, the "CI must be green" paragraph), `README.md` (two lines), `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (sections 10, 11 and 13), `site-tests/requirements.txt` and `site-tests/package.json` (one comment each)

This task makes every "five jobs" to "six jobs" edit, as PR 4's Task 7 did for `site-reading`; Task 8 makes none of them.

**Interfaces:**
- Consumes (Tasks 1 to 6; a message names the file to fix when one is missing): `layouts/form.html`; `components/field.html` (a visible label; the help in `<input id>_help` and the error sentences in `<input id>_error`, both named in `aria-describedby`, the help first; `aria-invalid`); `components/form_errors.html` (`role="alert"`); the buttons of Task 1's contract (the main action `button.action.interactive`, the secondary ones `.label.interactive`: every button of a form page is `.interactive`); the global `:focus-visible` outline; `--fade-top`, `--fade-band` and `scroll-padding-top` on `.js.shell-ready` below 64rem; `field.css` (16 px text, the `:autofill` and `:-webkit-autofill` hold, the paper under `[data-motion="off"]`); the sign-in form and the `?next=` it carries (Task 2); a resend form with a hidden `action=resend` (Task 3); the device approval form (`input[name=confirm]`, Approve as the first `button.action.interactive`) (Task 4); `/o/authorize/` (Task 5); `throttled.html`, the `/a/<token>` 404 and the self-contained 500 (Task 6).
- Produces: `site-tests/make_form_fixtures.py` (run like `make_staff_session.py`, with `SITE_SESSION_FILE`; it writes `form-fixtures.json` beside it, which git ignores); in the harness `--part forms`, `run_forms`, `FormState`, `FORM_STATES` (35 states, the 5 that use up a throttle last), `FORM_DRIVERS`, `load_form_fixtures` and eight checks named `forms_..._problems`; the CI job `site-forms` (Chromium only, artifact `site-forms-screenshots`); in `pages.tests_site_parts` the forms row of `JOBS` and `PART_ENTRIES`, `PAGE_PARTS` and `FormsPartTests`; `pages.tests_form_fixtures` (held to `longMessage = False` by `READING_TEST_MODULES`).

**Decisions:**
- **Chromium only.** The checks read computed values, structure and statuses, which any engine gives alike; what differs on an iPhone (the zoom into a field under 16 px, the keyboard, real autofill) Playwright's WebKit cannot show either, so a WebKit pass would repeat Chromium's reading at twice the cost. `site-phone` stays the only job that installs WebKit.
- **The error pages as production draws them.** The dev server runs with DEBUG on, which answers an unknown address and an uncaught error with Django's technical pages and never calls `handler404` or `handler500`; every other error page (a 403, a failed CSRF check, a 429, the `/a/<token>` miss) is the site's own whatever DEBUG says, so it comes from the running server. For the two there were three ways: a second server with DEBUG off (it needs `collectstatic` and a URL made to fail), a dev-only route that renders the handlers (new code in `config/urls.py`, which Task 6 also edits), or drawing them where settings can change. This task does the third: the fixture script requests an address nothing serves, and a view made to raise, through `Client(raise_request_exception=False)` under `override_settings(DEBUG=False, ...)`, so the whole stack runs, and the checks serve those bytes with `route.fulfill` while the CSS and script come from the dev server. Its cost: the bytes are drawn when the script runs, so it is run again after a template changes (AGENTS.md says so).
- **Waits on states.** Nothing sleeps: a check waits for the state it reads (`wait_for_state`, `expect_navigation`, `SCROLL_SETTLED`). `forms_slow_frames_problems` repeats the keyboard checks on sign in and the code entry with every frame held 120 ms, and `FormsPartTests` fails if anything `run_forms` reaches calls `wait_for_timeout` or `sleep`.
- **Throttled states** use the budget up with the fewest requests until a 429 comes, then ask once in the page, and come last (`spends=True`). The sign-in, code-lookup and reset limits live in the dev server's memory, so each run of the part starts on a freshly started server with fresh fixtures; a state that meets a 429 it should not says so.
- **Autofill.** Playwright cannot make Chromium autofill a field and the browser's colours are `!important`, so the page holds the look itself; the check reads the style sheets for a rule on each spelling that keeps the text `--ink` under a transition that never ends (or an inset shadow), and one that paints the field paper with Motion off (measured in Chromium 131: the two spellings and the transition read back as they are written).
- **Every button is `.interactive`.** `forms_page_problems` reads every `button` and `a.action` in `<main>` and wants the class: `.interactive` is what gives a control the ink wash on hover and press (Task 1's contract, the owner's ruling for the main action). A template that writes `class="action"` alone answers neither.
- **What the focus check measures: the input, not its label.** Measured in Chromium 131 on the real pages: `focus()` scrolls only when the input is not wholly below `scroll-padding-top`, and then leaves the input's `scroll-margin-top` clear above it; an input that is already in view stays where it is, and the label (and a help line) above it may be in the fade. So the check puts each field partly under the fade (`UNDER_THE_FADE`, 20 px above where the fade ends), blurs what has focus (`focus()` on the field that already has it scrolls nothing: a form of one field was reported as under the fade for that reason in the first run), gives a short page room to scroll (`ROOM_TO_SCROLL`: the device code entry is shorter than a window with the keyboard up and could not be measured at all), focuses the field, and wants its top at or below the fade's end and its bottom above the keyboard. Removing `scroll-padding-top` fails it (Step 7); a label left in the fade above an input that was in view does not.
- **Not here:** the identical bytes of a missing and an expired `/a/<token>` and of a real and a fake account's 429 (Django tests, Tasks 2 and 6); the menu on a form page; Ink; the order of the Tab stops; the label above a field that took focus where it stood (see the focus check); sign up through `?next=` (Task 2's Django tests); the text of a form over the moving cloth (the cloth part's contrast family).

**Existing tests this task changes.** All in `backend/pages/tests_site_parts.py`: `JOBS` and `PART_ENTRIES` gain the forms part (the tests already there then hold it to `--part all`, to `ci.yml`, to the docs and to a `phone` label in `PREVIOUS_STAGE`); `test_no_check_falls_between_the_parts_or_runs_in_two` let only the pages and reading pair share the checks named in `SHARED_PAGE_CHECKS`, and the forms part runs some on its own pages, so any two of the three may (`PAGE_PARTS`); the phone job's test read `site-phone` to the end of `ci.yml`, and `site-forms` now follows it. No other test reads these. In `backend/pages/tests_site_rules.py` `READING_TEST_MODULES` gains `pages.tests_form_fixtures`, so that `ReadableFailuresTests` holds that module to `longMessage = False` as it holds the other modules that Tasks 1 to 6 added.

**How long.** Measured, not modelled: the part was run in a scratch copy of the repo with Tasks 1 to 6 applied (nothing of the repo was touched) on an 8-core machine: **80 s** for all 35 states (1 to 3 s each), the round trips 3 s and the held frames 25 s. CI's runner took about 1.4 times the machine's time for the reading part, so about 2 minutes; **about 1.5 minutes on a machine and 2 to 3 on CI (4 to 5 with the job's setup), against the job's 20.** If the machine run takes more than 3 minutes, stop and report DONE_WITH_CONCERNS with the per-state times it prints.

- [ ] **Step 1: Write the failing tests**

Edits to existing files are given as blocks: the text between `<<<<<<<` and `=======` occurs exactly once in the file, and is replaced by the text between `=======` and `>>>>>>>`.

In `backend/pages/tests_site_parts.py` make these seven replacements, then add the class at the end of the file; then make the one replacement in `backend/pages/tests_site_rules.py` that follows them.

```text
<<<<<<<
legal pages) and `site-phone` (the phone menu's sheet, in Chromium and WebKit), each with its own 20 minutes,
=======
legal pages), `site-phone` (the phone menu's sheet, in Chromium and WebKit) and `site-forms` (the forms and the error pages), each with its own 20 minutes,
>>>>>>>
<<<<<<<
(the few that the pages part and the reading part both run, each on its own pages, are named in
=======
(the few that the pages, reading and forms parts run, each on its own pages, are named in
>>>>>>>
<<<<<<<
AGENTS = ROOT / "AGENTS.md"
=======
AGENTS = ROOT / "AGENTS.md"


def workflow_jobs(text):
    """{job name: the job's lines} of ci.yml."""
    return {match.group(1): match.group(2) for match in re.finditer(r"^  ([a-z-]+):\n((?:    .*\n|\n)+)", text, re.M)}
>>>>>>>
<<<<<<<
        ("site-reading", "reading", "site-reading-screenshots"), ("site-phone", "phone", "site-phone-screenshots"))
=======
        ("site-reading", "reading", "site-reading-screenshots"), ("site-phone", "phone", "site-phone-screenshots"), ("site-forms", "forms", "site-forms-screenshots"))
>>>>>>>
<<<<<<<
                "reading": ("run_reading", None), "phone": ("run_phone", None)}
=======
                "reading": ("run_reading", None), "phone": ("run_phone", None), "forms": ("run_forms", None)}
# The parts that open pages, and so may run the checks named in SHARED_PAGE_CHECKS, each on its own pages.
PAGE_PARTS = {"pages", "reading", "forms"}
>>>>>>>
<<<<<<<
                    pair = {first, second} == {"pages", "reading"}  # the one pair that runs some checks on both its own pages
                    advice = ", or, if the reading part means to run the same check on its own pages, name it in SHARED_PAGE_CHECKS" if pair else ""
=======
                    pair = {first, second} <= PAGE_PARTS  # the parts that open pages run some checks each on its own pages
                    advice = ", or, if the part means to run the same check on its own pages, name it in SHARED_PAGE_CHECKS" if pair else ""
>>>>>>>
<<<<<<<
        self.assertRegex(job("site-phone", None), r"playwright install --with-deps chromium webkit\n",
=======
        self.assertRegex(job("site-phone", "site-forms"), r"playwright install --with-deps chromium webkit\n",
>>>>>>>
```

In `backend/pages/tests_site_rules.py` the new module joins the ones that `ReadableFailuresTests` holds to `longMessage = False`. The replaced text is the end of the `READING_TEST_MODULES` tuple and the function after it, which occur once however Tasks 1 to 6 wrapped the tuple, so the name goes last (if an earlier task ended the tuple with a comma, drop that comma):

```text
<<<<<<<
)


def cases_defined_in(module):
=======
,
                        "pages.tests_form_fixtures")


def cases_defined_in(module):
>>>>>>>
```

Append to `backend/pages/tests_site_parts.py`:

```python
class FormsPartTests(SimpleTestCase):
    """The forms part (`run_forms`, CI's `site-forms`) is held to its conventions; every message names the file and the fix."""

    longMessage = False

    def test_the_forms_part_never_waits_a_fixed_time_and_runs_its_keyboard_checks_with_every_frame_held(self):
        harness = Harness()
        self.assertIn("run_forms", harness.functions, "site-tests/run_site_checks.py: the forms part's entry point is run_forms")
        for name in sorted(harness.reach("run_forms")):
            for call in (each for each in ast.walk(harness.functions.get(name, ast.Pass())) if isinstance(each, ast.Call)):
                called = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                self.assertNotIn(called, ("wait_for_timeout", "sleep"), f"site-tests/run_site_checks.py: {name} calls {called}, a wait for a fixed time, and run_forms reaches it: "
                                 f"wait for the state the check reads with wait_for_state(page, state)")
        self.assertIn("forms_slow_frames_problems", harness.checks("run_forms"), "site-tests/run_site_checks.py: run_forms must run forms_slow_frames_problems")
        self.assertIn("SLOW_EVERY_FRAME_MS", harness.names("FRAME_STALL"), "site-tests/run_site_checks.py: FRAME_STALL must hold each frame SLOW_EVERY_FRAME_MS")
        self.assertIn("FRAME_STALL", harness.reach("forms_slow_frames_problems"), "site-tests/run_site_checks.py: forms_slow_frames_problems must install FRAME_STALL")

    def test_the_forms_job_installs_chromium_only_and_makes_the_fixtures_after_migrate_and_git_ignores_them(self):
        jobs = workflow_jobs(WORKFLOW.read_text(encoding="utf-8"))
        job = jobs.get("site-forms", "")
        self.assertRegex(job, r"playwright install --with-deps chromium\n", "ci.yml: the `site-forms` job installs Chromium and nothing else")
        start = job.find("Start the app")
        order = [job.find(step, start) for step in ("manage.py migrate", "make_staff_session.py", "make_form_fixtures.py", "manage.py runserver")]
        self.assertNotIn(-1, order, "ci.yml: the `site-forms` job runs migrate, make_staff_session.py, make_form_fixtures.py and runserver")
        self.assertEqual(order, sorted(order), "ci.yml: the `site-forms` job migrates, then makes the staff session and the fixtures, then starts the server")
        self.assertEqual([name for name, body in jobs.items() if "make_form_fixtures.py" in body], ["site-forms"], "ci.yml: only the `site-forms` job makes the fixtures")
        self.assertIn("site-tests/form-fixtures.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
                      ".gitignore: add site-tests/form-fixtures.json beside site-tests/session.txt: it holds session keys and the fixture users' password")
```

Create `backend/pages/tests_form_fixtures.py`:

```python
"""site-tests/make_form_fixtures.py sets a known password on users, so it must refuse any database but a local or CI one; what it writes must work as the forms part of
site-tests/run_site_checks.py expects; and every state of that part must be reachable from it. Every class sets longMessage = False (READING_TEST_MODULES in pages/tests_site_rules.py holds the module to it),
and every message names the file and the fix."""
import ast
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings

from drop_core.models import APIKey, DeviceCode, EmailVerification

ROOT = Path(settings.BASE_DIR).parent
SCRIPT, HARNESS = ROOT / "site-tests" / "make_form_fixtures.py", ROOT / "site-tests" / "run_site_checks.py"
FAST = ["django.contrib.auth.hashers.MD5PasswordHasher"]   # the script sets five passwords; a test need not wait for PBKDF2 for each
PREFIX = "site-tests/make_form_fixtures.py"


def run_script():
    """Run the script as `manage.py shell -c "exec(...)"` does, in the test's database; the fixtures it wrote."""
    with tempfile.TemporaryDirectory() as directory:
        with override_settings(DEBUG=True, PASSWORD_HASHERS=FAST), mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_form_fixtures"})
        return json.loads((Path(directory) / "form-fixtures.json").read_text(encoding="utf-8"))


def harness_node(name):
    """A module-level assignment of the harness, as the syntax tree has it."""
    for each in ast.parse(HARNESS.read_text(encoding="utf-8")).body:
        if isinstance(each, ast.Assign) and getattr(each.targets[0], "id", "") == name:
            return each.value
    raise AssertionError(f"site-tests/run_site_checks.py: no module-level {name}")


def signed_in(key):
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = key
    return client


class MakeFormFixturesGuardTests(TestCase):
    longMessage = False

    def assert_refused(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}):
            with self.assertRaises(SystemExit) as refusal:
                exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_form_fixtures"})
            self.assertFalse((Path(directory) / "form-fixtures.json").exists(), f"{PREFIX}: it wrote a file before it refused")
        self.assertIn("unset DATABASE_URL", str(refusal.exception))
        self.assertFalse(get_user_model().objects.filter(username="site-verified").exists(), f"{PREFIX}: the guard goes before anything touches the database")

    def test_refuses_when_debug_is_off(self):
        self.assert_refused()   # tests run with DEBUG off, as production does

    @override_settings(DEBUG=True)
    def test_refuses_a_database_on_another_host(self):
        with mock.patch.object(connection, "vendor", "postgresql"), mock.patch.dict(connection.settings_dict, {"HOST": "db.example.com"}):
            self.assert_refused()


@override_settings(PASSWORD_HASHERS=FAST)
class MakeFormFixturesTests(TestCase):
    """What a run writes, and that the app answers each of it as the forms part expects."""

    longMessage = False

    @classmethod
    def setUpTestData(cls):
        cls.f = run_script()

    def test_the_sessions_the_codes_the_clients_and_the_links_work(self):
        f = self.f
        self.assertEqual(sorted(f), ["accounts", "device", "errors", "oauth", "reset", "sessions", "verify"], f"{PREFIX}: the keys run_forms reads")
        self.assertEqual(Client().post("/accounts/login/", f["accounts"]["verified"]).status_code, 302, f"{PREFIX}: the round trips sign in with this name and password")
        for key, address, status, text in (
                ("verified", f"/device?user_code={f['device']['consent']}", 200, 'name="confirm"'),
                ("verified", f"/device?user_code={f['device']['expired']}", 410, ""), ("long", f"/device?user_code={f['device']['long']}", 200, ""),
                ("verified", f["oauth"]["authorize"], 200, f["oauth"]["app"]), ("long", f["oauth"]["long"], 200, "SiteChecksLongName"), ("verified", f["oauth"]["unknown"], 400, ""),
                ("pending", "/device", 200, "Verify your email first"), ("pending", "/accounts/verify-email/?sent=1", 200, ""),
                (None, f["reset"]["link"], 200, "new_password1"), (None, f["verify"]["link"], 200, "")):
            client = signed_in(f["sessions"][key]) if key else Client()
            self.assertContains(client.get(address, follow=True), text, status_code=status, msg_prefix=f"{PREFIX}: {key or 'nobody'} at {address}")
        self.assertEqual(APIKey.objects.get(workspace__owner__username__startswith="site-long-").name, "K" * 255, "the long user is at the free plan's one key, with the longest name")
        self.assertTrue(EmailVerification.objects.get(user__username="site-reset").is_verified)
        pending = signed_in(f["sessions"]["pending"])
        self.assertEqual(pending.post("/accounts/verify-email/", {"action": "resend"}).status_code, 429, f"{PREFIX}: the pending user's last link went in the future: a resend sends nothing")
        self.assertEqual(mail.outbox, [])

    def test_the_error_pages_print_nothing_of_the_request_and_a_second_run_starts_again(self):
        for name, page in self.f["errors"].items():
            self.assertNotIn("leak-check-7c1e", page["body"], f"{name}: an error page prints nothing of the address it was asked for")
            self.assertNotIn("site checks: a view that fails", page["body"], f"{name}: nor of the failure")
        self.assertEqual((self.f["errors"]["not_found"]["status"], self.f["errors"]["server_error"]["status"]), (404, 500))
        self.assertNotEqual(run_script()["sessions"], self.f["sessions"])
        self.assertEqual((get_user_model().objects.filter(username="site-verified").count(), DeviceCode.objects.filter(requester_ip="198.51.100.7").count()), (1, 3),
                         f"{PREFIX}: a run starts by deleting what an earlier one made")


@override_settings(PASSWORD_HASHERS=FAST)
class FormStatesAreReachableTests(TestCase):
    longMessage = False

    def test_every_state_names_a_driver_a_session_and_an_address_the_fixtures_have(self):
        fixtures, names, spends, throttled = run_script(), [], set(), set()
        drivers, sessions = {key.value for key in harness_node("FORM_DRIVERS").keys}, set(fixtures["sessions"]) | {"", "staff"}
        for call in harness_node("FORM_STATES").elts:
            args, options = [ast.literal_eval(arg) for arg in call.args], {k.arg: ast.literal_eval(k.value) for k in call.keywords}
            name, path = args[0], args[1] if len(args) > 1 else ""
            names.append(name)
            (spends if options.get("spends") else set()).add(name)
            (throttled if options.get("status") == 429 else set()).add(name)
            self.assertIn(options.get("how", "open"), drivers, f"the state {name!r} names a driver that FORM_DRIVERS does not have")
            self.assertIn(options.get("who", ""), sessions, f"the state {name!r} names a session that {PREFIX} does not write")
            if options.get("how") == "baked":
                self.assertIn(path, fixtures["errors"], f"the state {name!r} serves a baked page the fixtures do not have")
            else:
                path.format(**fixtures)   # a KeyError here names a field the fixtures lack
        self.assertEqual(len(names), len(set(names)), "site-tests/run_site_checks.py: every state has a name of its own (the screenshots are named by it)")
        self.assertEqual(spends, throttled, "site-tests/run_site_checks.py: a state that answers 429 uses up a budget the others need, so it has spends=True, and only it does")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts pages.tests_form_fixtures`
Expected: `Ran 15 tests`, `FAILED (failures=5, errors=6)`; the other five pass. The errors: `FileNotFoundError: ... make_form_fixtures.py` (the two guard tests, the `setUpClass` of `MakeFormFixturesTests` and `test_every_state_names_a_driver_a_session_and_an_address_the_fixtures_have`), `KeyError: 'forms'` in `test_all_is_every_part_in_order` and `ValueError: substring not found` in the phone job's test. The failures, each with a message that names the file and the fix: `forms: the part reaches no check`, `the `site-forms` job should run `--part forms``, `ci.yml: the browser checks are the `site*` jobs`, and the two `FormsPartTests`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests`
Expected: `OK`: `pages.tests_form_fixtures` is imported and every class in it sets `longMessage = False`.

- [ ] **Step 2: The fixture script, and keeping its output out of git**

Create `site-tests/make_form_fixtures.py`:

```python
"""Make what the forms part of the browser checks needs; CI and local runs only.

Run from backend/ inside Django, with SITE_SESSION_FILE as for make_staff_session.py; the fixtures are written beside it, as form-fixtures.json:
  SITE_SESSION_FILE=../site-tests/session.txt python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"

It makes verified users and their sessions (one with the longest values the models take), a user whose verification link went a moment ago, a user to reset, device codes,
OAuth clients registered through /o/register/, the links of the reset and verification emails, and the 404 and the 500 as production draws them: the dev server runs with
DEBUG on, which never calls the site's error handlers, so those two are drawn here with DEBUG off, by the whole request stack, and the checks serve those bytes.
A run first deletes what an earlier one left. It refuses to run unless DEBUG is on and the database is SQLite or on this machine: it sets a known password on users.
"""
import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import Client, override_settings
from django.urls import path, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from oauth2_provider.models import get_application_model

from drop_core import device
from drop_core.email_verification import make_verification_token
from drop_core.models import APIKey, DeviceCode, EmailSendLog, EmailVerification, get_or_create_workspace

if not settings.DEBUG or (connection.vendor != "sqlite"
                          and connection.settings_dict.get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1")):
    raise SystemExit("make_form_fixtures.py runs only against a local or CI database: set DJANGO_DEBUG=True, "
                     "unset DATABASE_URL (or point it at localhost), and try again.")

User, Application = get_user_model(), get_application_model()
PASSWORD = "site-checks-not-a-secret-1"   # every fixture user's password: a development database only
CODE_IP = "198.51.100.7"   # where the fixture device codes say they came from: how a re-run finds its own
CALLBACK, STATE = "http://127.0.0.1:9876/callback", "site-checks-state"
APP, LONG_APP = "Site checks app", "SiteChecksLongName" + "W" * 237   # 255 characters, no space: the longest name a client registers
LONG_EMAIL = "l" * 64 + "@" + "d" * 63 + "." + "e" * 63 + "." + "f" * 57 + ".com"   # 254 characters, every label at its longest


def make_user(username, email, verified=None, last_sent_at=None):
    """A user with the fixtures' password and, unless verified is None, an email record (verified or pending)."""
    user, _ = User.objects.get_or_create(username=username)
    user.email, user.is_active = email, True
    user.set_password(PASSWORD)
    user.save()
    if verified is None:
        EmailVerification.objects.filter(user=user).delete()
    else:
        EmailVerification.objects.update_or_create(user=user, defaults={"email": email, "is_verified": verified, "last_sent_at": last_sent_at,
                                                                         "verified_at": timezone.now() if verified else None})
    return user


def session_for(user):
    """A signed-in session, as make_staff_session.py makes one; the key is what the browser's cookie holds."""
    session = SessionStore()
    session[SESSION_KEY], session[BACKEND_SESSION_KEY] = str(user.pk), settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


def make_code(user_agent, lasts=timedelta(days=1)):
    """A pending device code, as the terminal asks for one; it lasts a day, so a slow run does not meet an expired one."""
    code, _secret, display = device._issue_device_code(CODE_IP, user_agent)
    code.expires_at = timezone.now() + lasts
    code.save(update_fields=["expires_at"])
    return display


def authorize_url(name):
    """A client registered as one registers itself (POST /o/register/), and the address that asks a user to authorize it (PKCE with the RFC 7636 example challenge)."""
    reply = Client().post("/o/register/", data=json.dumps({"client_name": name, "redirect_uris": [CALLBACK], "grant_types": ["authorization_code"],
                                                           "token_endpoint_auth_method": "none"}), content_type="application/json")
    if reply.status_code != 201:
        raise SystemExit(f"POST /o/register/ answered {reply.status_code}: the OAuth client could not be made")
    return "/o/authorize/?" + urlencode({"response_type": "code", "client_id": reply.json()["client_id"], "redirect_uri": CALLBACK, "scope": "mcp:use", "state": STATE,
                                         "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "code_challenge_method": "S256"})


def bake_error_pages():
    """The 404 and the 500 as production draws them: DEBUG off, plain /static/ URLs (no manifest here, as in the test runner), the whole request stack and the site's own
    handlers. A view that raises, added to the URLs for the moment, stands for the 500."""
    import config.urls as site_urls

    def fails(request):
        raise RuntimeError("site checks: a view that fails")

    boom = path("site-checks-boom/", fails)
    plain = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    baked, was = {}, logging.root.manager.disable
    site_urls.urlpatterns.append(boom)
    logging.disable(logging.CRITICAL)   # the 500 is logged as the error it is
    try:
        with override_settings(DEBUG=False, STORAGES=plain):
            for name, address in (("not_found", "/no-such-page-leak-check-7c1e/"), ("server_error", "/site-checks-boom/")):   # the marker: no error page may print its address
                reply = Client(raise_request_exception=False).get(address)
                baked[name] = {"path": address, "status": reply.status_code, "body": reply.content.decode("utf-8"),
                               "headers": {key: value for key, value in reply.items() if key.lower() != "content-length"}}
    finally:
        logging.disable(was)
        site_urls.urlpatterns.remove(boom)
    if (baked["not_found"]["status"], baked["server_error"]["status"]) != (404, 500):
        raise SystemExit("the error pages were not drawn as a 404 and a 500")
    return baked


# What an earlier run left, so that a run is the same every time.
DeviceCode.objects.filter(requester_ip=CODE_IP).delete()
EmailSendLog.objects.filter(purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT, ip_address__in=["127.0.0.1", "::1"]).delete()
EmailSendLog.objects.filter(user__username__startswith="site-").delete()
Application.objects.filter(name__in=[APP, LONG_APP]).delete()

verified = make_user("site-verified", "site-verified@example.com", verified=True)
long_user = make_user("site-long-" + "u" * 140, LONG_EMAIL, verified=True)   # 150 characters, the longest username
pending = make_user("site-pending", "p" + LONG_EMAIL[1:], verified=False, last_sent_at=timezone.now() + timedelta(hours=1))   # a resend is always too soon: no mail is sent
reset = make_user("site-reset", "site-reset@example.com", verified=False)
APIKey.objects.filter(workspace__owner__in=[verified, long_user]).delete()
APIKey.create_key(get_or_create_workspace(long_user), "K" * 255)   # the free plan allows one key: the long user is at the limit

fixtures = {
    "sessions": {"verified": session_for(verified), "long": session_for(long_user), "pending": session_for(pending)},
    "accounts": {"verified": {"username": verified.username, "password": PASSWORD}},
    "device": {"consent": make_code("veilfile-cli/0.4.0 (site checks)"), "long": make_code("U" * 256),
               "expired": make_code("veilfile-cli/0.4.0 (site checks)", lasts=timedelta(seconds=-30)), "unknown": "ZZZZ-2222"},
    "oauth": {"app": APP, "authorize": authorize_url(APP), "long": authorize_url(LONG_APP),
              "unknown": "/o/authorize/?response_type=code&client_id=site-checks-no-such-client&redirect_uri=" + CALLBACK},
    "reset": {"link": reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(reset.pk)), default_token_generator.make_token(reset)])},
    "verify": {"link": reverse("verify-email", args=[make_verification_token(reset, reset.email)])},
    "errors": bake_error_pages(),
}
Path(os.environ["SITE_SESSION_FILE"]).with_name("form-fixtures.json").write_text(json.dumps(fixtures, indent=1), encoding="utf-8")
print(f"form fixtures written: {len(fixtures['sessions'])} sessions, {DeviceCode.objects.filter(requester_ip=CODE_IP).count()} device codes, "
      f"{len(fixtures['errors'])} error pages drawn with DEBUG off")
```

In `.gitignore`:

```text
<<<<<<<
site-tests/session.txt
=======
site-tests/session.txt
site-tests/form-fixtures.json
>>>>>>>
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_form_fixtures`
Expected: `Ran 5 tests`, `FAILED (failures=1)`: the other four pass; `test_every_state_names_a_driver_a_session_and_an_address_the_fixtures_have` (`no module-level FORM_DRIVERS`) waits for the harness.

Then run it once against the local database (`migrate` first on a new branch):

```bash
cd backend && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"
```
Expected: `form fixtures written: 3 sessions, 3 device codes, 2 error pages drawn with DEBUG off`; run again and it prints the same line, with new codes and sessions.

- [ ] **Step 3: The forms part in the harness**

Seventeen replacements in `site-tests/run_site_checks.py`; each old text occurs exactly once (checked against `main` at 5893ffc; Tasks 1 to 6 do not touch this file); do not rely on line numbers. Replacement 6 is the part's whole code. The file runs under Python 3.9 (`site-tests/.venv`). A helper is not named `..._problems` unless it is a check (`pages.tests_site_parts` counts those), and no function of the block may use a local name that is also a module-level name of the file (`run`, say): that test follows names, and a local `run` once made `run_forms` reach every part.

```text
<<<<<<< 1. docstring: the jobs
`site-ink` and `site-reading`).
=======
`site-ink`, `site-reading`, `site-phone` and `site-forms`).
>>>>>>>
<<<<<<< 2. docstring: the usage
[--part pages|cloth|ink|reading|all]
=======
[--part pages|cloth|ink|reading|phone|forms|all]
>>>>>>>
<<<<<<< 3. docstring: the jobs again
`site-ink` and `site-reading`, each with its own 20 minutes)
=======
`site-ink`, `site-reading`, `site-phone` and `site-forms`, each with its own 20 minutes)
>>>>>>>
<<<<<<< 4. docstring: the end
how a change fades, which its change checks watch).
"""
=======
how a change fades, which its change checks watch). The `forms` part is sign in, sign up, password reset, email verification, the device flow, OAuth
consent and the error pages, each in the states it has; it needs the fixtures that site-tests/make_form_fixtures.py writes beside the session file.
"""
>>>>>>>
<<<<<<< 5. imports
from urllib.parse import urlparse
=======
from urllib.parse import parse_qs, urlparse, urlsplit
>>>>>>>
<<<<<<< 6. the forms part, its whole code
# What Playwright's own tasks fail with when nothing awaits them
=======
# The forms part (CI's job `site-forms`): every form and error page in each state it has. The dev server (DEBUG on) answers the generic 404 and the 500 with Django's
# technical pages, so make_form_fixtures.py draws those two with DEBUG off and drive_baked serves them.
FORM_SCREENS = ((1280, 900), (375, 667), (320, 640))
KEYBOARD_SCREENS = ((375, 380), (320, 300))   # a phone with its on-screen keyboard up
ROOM_TO_SCROLL = "body::after { content: ''; display: block; height: 100vh; }"   # a short page can then be scrolled far enough to put a field under the top fade
UNDER_THE_FADE = 20   # px: a field is put with its top this far above where the top fade ends, so that it is part under it and part clear
FORM_NAV_MS = 15000   # the longest a submission is waited for: a bound, never a delay
FORM_TRIES = 14   # the most requests it takes to reach a throttle
STALE = ("an earlier run of this part may have used up the server's throttles (the sign-in, code and reset limits live in its memory) or the fixtures' device codes: "
         "restart the server and run make_form_fixtures.py again")
TEXT_FIELDS = "main input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), main select, main textarea"
AUTOCOMPLETE = {"username": "username", "email": "email", "password": "current-password", "password1": "new-password", "password2": "new-password",
                "new_password1": "new-password", "new_password2": "new-password", "user_code": "off"}
FRAME_STALL = ("(() => { const hold = () => { const began = performance.now(); while (performance.now() - began < " + str(SLOW_EVERY_FRAME_MS)
               + "); requestAnimationFrame(hold); }; requestAnimationFrame(hold); })();")   # every frame held, as the phone part's probe holds them
NOVALIDATE = "() => document.querySelector('main form[method=post]').setAttribute('novalidate', '')"   # the browser's own check of required fields, switched off
FORM_READY = "() => !document.documentElement.classList.contains('js') || document.documentElement.classList.contains('shell-ready')"   # shell.js has run, or there is none
FORM_PAGE = """() => {
  const words = (e) => e.textContent.replace(/\\s+/g, ' ').trim(), main = document.querySelector('main') || document;
  const fields = [...main.querySelectorAll('input, select, textarea')].filter((f) => !['hidden', 'submit', 'button', 'image', 'reset'].includes(f.type));
  return { alerts: [...main.querySelectorAll('[role=alert]')].map(words),
    buttons: [...main.querySelectorAll('button, a.action, input[type=submit], input[type=button]')].map((b) => ({ words: words(b) || b.value || b.name, interactive: b.classList.contains('interactive') })),
    errorBoxes: [...main.querySelectorAll('[id$=_error]')].map((e) => e.id),
    fields: fields.map((f) => ({ name: f.name || f.id, shown: f.getClientRects().length > 0, size: parseFloat(getComputedStyle(f).fontSize),
      textual: !['checkbox', 'radio'].includes(f.type), invalid: f.getAttribute('aria-invalid') === 'true', autocomplete: f.getAttribute('autocomplete') || '',
      labelled: [...f.labels].some((l) => words(l) && l.getClientRects().length > 0),
      described: (f.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean).map((id) => {
        const m = document.querySelectorAll('[id=' + JSON.stringify(id) + ']'); return m.length === 1 && words(m[0]) ? id : '!' + id; }) })) }; }"""
FORM_BARE = """() => ({ title: document.title.trim(), lang: document.documentElement.lang, viewport: Boolean(document.querySelector('meta[name="viewport"]')),
  h1: document.querySelectorAll('h1').length, files: document.querySelectorAll('script[src], link[rel~="stylesheet"]').length, styles: document.querySelectorAll('style').length })"""
# Field `index` of TEXT_FIELDS put `at` px down the window (null: not moved; otherwise whatever has focus is blurred first, because focus() on the field that already has it scrolls
# nothing), and where it lies in a window `view` high.
FIELD_PLACE = """([selector, index, at]) => { const f = document.querySelectorAll(selector)[index];
  if (at !== null) { document.activeElement.blur(); window.scrollTo(0, Math.max(0, f.getBoundingClientRect().top + window.scrollY - at)); }
  const b = f.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, view: innerHeight }; }"""
# The top fade's height as the tokens set it.
FADE_HEIGHT = """() => { const probe = document.createElement('div'); probe.style.cssText = 'position:absolute;visibility:hidden;height:calc(var(--fade-top) + var(--fade-band))';
  document.body.append(probe); const height = probe.getBoundingClientRect().height; probe.remove(); return height; }"""
WATCH_SUBMIT = """(selector) => { window.__submitted = null; document.querySelector(selector).form.addEventListener('submit', (event) => { event.preventDefault();
  const by = event.submitter; window.__submitted = { by: by ? by.outerHTML.slice(0, 100) : null, ok: Boolean(by) && by.classList.contains('action') && !by.hasAttribute('formnovalidate') }; }, { once: true }); }"""
# The page's rules on the browser's autofill, and whether each holds the field's look: a transition of 1000 s or more, or an inset shadow.
AUTOFILL_RULES = """() => { const found = []; const walk = (rules) => { for (const r of rules) { if (r.cssRules && !r.selectorText) walk(r.cssRules);
    else if (r.selectorText && /:(-webkit-)?autofill/.test(r.selectorText)) found.push({ selector: r.selectorText, fill: r.style.getPropertyValue('-webkit-text-fill-color'),
      held: [...(r.style.getPropertyValue('transition') + ' ' + r.style.getPropertyValue('transition-duration')).matchAll(/(\\d+(?:\\.\\d+)?)s\\b/g)].some((m) => Number(m[1]) >= 1000),
      inset: r.style.getPropertyValue('box-shadow').includes('inset') }); } };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch (error) { /* another origin */ } } return found; }"""


def load_form_fixtures(path):
    """What make_form_fixtures.py wrote, or a stop that says how to make it."""
    if not path.is_file():
        raise SystemExit(f"{path} is missing: the forms part needs the fixtures that site-tests/make_form_fixtures.py writes beside the session file (AGENTS.md, Site checks).")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FormState:
    """One state of one page: name (the screenshots' too), path ({device[consent]} and the like come from the fixtures), who ("", "staff" or a fixtures session), how (a driver of
    FORM_DRIVERS), status, expect ("blank", "field", "alert" or "any"), enter (a field whose form Enter submits: the keyboard checks run on it), bare (the 500)
    and spends (uses a throttle up: comes last)."""
    name: str
    path: str = ""
    who: str = ""
    how: str = "open"
    status: int = 200
    expect: str = "any"
    enter: str = ""
    bare: bool = False
    spends: bool = False


def form_session(who, session_key, fixtures):
    return "" if not who else session_key if who == "staff" else fixtures["sessions"][who]


def form_context(browser, base_url, key, path, name, **options):
    """Paper, Motion on, the session cookie `key` names (none when empty), a bound on every wait."""
    context = new_context(browser, base_url, key, SitePage(path or "/", name, signed_in=bool(key)), theme="light", motion="on", **options)
    context.set_default_timeout(FORM_NAV_MS)
    return context


def fresh_page(context):
    """A page that collects the console's errors and every file it asked for and could not have."""
    page, errors = context.new_page(), []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "Failed to load resource" not in message.text else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: errors.append(f"{response.url} answered {response.status}")
            if response.status >= 400 and response.request.resource_type != "document" and not response.url.endswith("/favicon.ico") else None)
    return page, errors


def set_screen(page, width, height):
    """Resize the window; wait for the page to lay itself out."""
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_function("(width) => innerWidth === width", arg=width)
    page.evaluate(NEXT_FRAMES)
    wait_for_state(page, FORM_READY)


def submitted(page, press=None, click=None):
    """Submit as a visitor does (Enter in a field, or a click); the response of the page that comes."""
    with page.expect_navigation(timeout=FORM_NAV_MS) as navigation:
        if click is not None:
            click.click()
        else:
            press.press("Enter")
    page.wait_for_load_state("networkidle")
    return navigation.value


def fill_fields(form):
    """Every text field of the form gets a value its type takes; a required box is ticked."""
    for field in form.locator(TEXT_FIELDS.replace("main ", "")).all():
        if field.is_visible():
            kind, name = field.get_attribute("type") or "text", field.get_attribute("name")
            field.fill("someone@example.com" if kind == "email" else "pw-site-checks-1" if kind == "password" else "ABCD-EFGH" if name == "user_code" else "site checks")
    for box in form.locator("input[type=checkbox][required]").all():
        box.check()


def drive_open(page, base_url, fixtures, state, problems):
    return page.goto(base_url + state.path.format(**fixtures), wait_until="networkidle")


def drive_baked(page, base_url, fixtures, state, problems):
    """The 404 or the 500 as make_form_fixtures.py drew it, served at its address."""
    baked = fixtures["errors"][state.path]
    page.context.route(base_url + baked["path"], lambda route: route.fulfill(status=baked["status"], headers=baked["headers"], body=baked["body"]))
    return page.goto(base_url + baked["path"], wait_until="networkidle")


def filler(values, press, clear_cookie=False):
    """A driver: open the page, type `values` ({run} comes from the fixtures), press Enter in the field `press`."""
    def drive(page, base_url, fixtures, state, problems):
        drive_open(page, base_url, fixtures, state, problems)
        if clear_cookie:
            page.context.clear_cookies(name="csrftoken")   # a form left open past its cookie
        for name, value in values.items():
            page.fill(f"main input[name={name}]", value.format(**fixtures))
        for box in page.locator("main input[type=checkbox][required]").all():
            box.check()
        return submitted(page, press=page.locator(f"main input[name={press}]"))
    return drive


def approving(tick):
    """A driver for the approval page: tick the confirmation, or not (the browser's own check is then switched off), press Approve."""
    def drive(page, base_url, fixtures, state, problems):
        drive_open(page, base_url, fixtures, state, problems)
        if tick:
            page.locator("main input[name=confirm]").check()
        else:
            page.evaluate(NOVALIDATE)
        return submitted(page, click=page.locator("main button.action").first)   # Approve: the first of the form's actions
    return drive


def throttler(post):
    """A driver that uses a throttle up with requests of the page's own cookies and token until a 429 comes, then asks once in the page."""
    def drive(page, base_url, fixtures, state, problems):
        target = base_url + state.path.format(**fixtures)
        page.goto(target, wait_until="networkidle")
        token = page.locator("main input[name=csrfmiddlewaretoken]").first.input_value() if post else ""
        form = {key: value.format(**fixtures) for key, value in (post or {}).items()}
        for _ in range(FORM_TRIES):
            reply = page.request.post(target, form={"csrfmiddlewaretoken": token, **form}) if post else page.request.get(target)
            if reply.status == 429:
                break
        else:
            problems.append(f"{state.name}: the throttle never answered 429 in {FORM_TRIES} requests")
            return None
        if not post:
            return page.goto(target, wait_until="networkidle")
        for name, value in form.items():
            page.fill(f"main input[name={name}]", value)
        page.evaluate(NOVALIDATE)   # the sign-up page has more required fields than the throttle's request names
        return submitted(page, press=page.locator(f"main input[name={list(form)[-1]}]"))
    return drive


def drive_verify_resend(page, base_url, fixtures, state, problems):
    drive_open(page, base_url, fixtures, state, problems)
    return submitted(page, click=page.locator("main form:has(input[value=resend]) button"))


FORM_DRIVERS = {
    "open": drive_open, "baked": drive_baked, "verify_resend": drive_verify_resend,   # a resend by a user whose last link went just now is too soon: no mail is sent
    "login_failed": filler({"username": "site-nobody-{run}", "password": "not-the-password-1"}, "password"),
    "signup_errors": filler({"username": "site-signup-{run}", "email": "a@b", "password1": "pw1", "password2": "pw2"}, "password2"),   # a@b: a browser takes it, Django does not
    "reset_error": filler({"email": "a@b"}, "email"), "reset_done": filler({"email": "forms-{run}@example.com"}, "email"),   # no account has it: nothing is mailed
    "reset_confirm_errors": filler({"new_password1": "pw1", "new_password2": "pw2"}, "new_password2"),
    "verify_errors": filler({"email": "a@b", "password": "not-the-password-1"}, "password"), "csrf": filler({"username": "x", "password": "y"}, "password", clear_cookie=True),
    "approve": approving(True), "approve_without": approving(False),
    "login_throttled": throttler({"username": "site-throttle-{run}", "password": "not-the-password-1"}), "signup_throttled": throttler({"username": "site-throttle-{run}"}),
    "reset_throttled": throttler({"email": "throttle-{run}@example.com"}), "device_throttled": throttler(None),
}

# Every page in every state it has, in the order they are looked at (the states that spend a throttle's budget come last, whatever their place here).
FORM_STATES = (
    FormState("login", "/accounts/login/", expect="blank", enter="main input[name=password]"),
    FormState("login-failed", "/accounts/login/", how="login_failed", expect="alert"),
    FormState("signup", "/accounts/signup/", expect="blank", enter="main input[name=password2]"),
    FormState("signup-errors", "/accounts/signup/", how="signup_errors", expect="field", enter="main input[name=password2]"),
    FormState("reset", "/accounts/password_reset/", expect="blank", enter="main input[name=email]"),
    FormState("reset-error", "/accounts/password_reset/", how="reset_error", expect="field"),
    FormState("reset-done", "/accounts/password_reset/", how="reset_done"),
    FormState("reset-confirm", "{reset[link]}", expect="blank", enter="main input[name=new_password2]"),
    FormState("reset-confirm-errors", "{reset[link]}", how="reset_confirm_errors", expect="field"),
    FormState("verify-done", "{verify[link]}"),
    FormState("link-missing", "/a/site-checks-missing-9f2c", status=404),
    FormState("not-found", "not_found", how="baked", status=404),
    FormState("server-error", "server_error", how="baked", status=500, bare=True),
    FormState("csrf-failure", "/accounts/login/", how="csrf", status=403),
    FormState("verify-email", "/accounts/verify-email/", who="staff", expect="blank", enter="main input[name=password]"),   # the staff login: no record
    FormState("device-approve-unverified", "/device?user_code={device[consent]}", who="staff", how="approve", status=403),
    FormState("oauth-unverified", "{oauth[authorize]}", who="staff", status=403),
    FormState("forbidden", "/admin/auth/user/", who="staff", status=403),
    FormState("verify-pending", "/accounts/verify-email/?sent=1", who="pending", expect="blank"),   # asked for a link a moment ago; the longest address
    FormState("verify-errors", "/accounts/verify-email/", who="pending", how="verify_errors", expect="field"),
    FormState("checkout", "/accounts/signup/?plan=team", who="verified"),
    FormState("device", "/device", who="verified", expect="blank", enter="main input[name=user_code]"),
    FormState("device-wrong", "/device?user_code={device[unknown]}", who="verified", expect="field"),
    FormState("device-expired", "/device?user_code={device[expired]}", who="verified", status=410),
    FormState("device-consent", "/device?user_code={device[consent]}", who="verified"),
    FormState("device-refused", "/device?user_code={device[consent]}", who="verified", how="approve_without", status=400, expect="alert"),
    FormState("oauth-consent", "{oauth[authorize]}", who="verified"),
    FormState("oauth-unknown-client", "{oauth[unknown]}", who="verified", status=400),
    FormState("device-consent-long", "/device?user_code={device[long]}", who="long"),   # the longest username, address, key name and user agent
    FormState("oauth-consent-long", "{oauth[long]}", who="long"),
    FormState("login-throttled", "/accounts/login/", how="login_throttled", status=429, spends=True),
    FormState("signup-throttled", "/accounts/signup/", how="signup_throttled", status=429, spends=True),
    FormState("reset-throttled", "/accounts/password_reset/", how="reset_throttled", status=429, spends=True),
    FormState("device-throttled", "/device?user_code={device[unknown]}", who="verified", how="device_throttled", status=429, spends=True),
    FormState("verify-throttled", "/accounts/verify-email/", who="pending", how="verify_resend", status=429, spends=True),
)


def forms_page_problems(page, label, state, phone=False):
    """A visible label and the old autocomplete token on every field, every error tied to its field and listed after the help, the ink wash on every button, a blank page saying
    nothing is wrong; (phone) 16 px text."""
    found, problems = page.evaluate(FORM_PAGE), []
    if phone:
        return [f"{label}: the {f['name']} field's text is {f['size']:g}px; under 16px a phone zooms in when it takes focus (static/css/components/field.css)"
                for f in found["fields"] if f["shown"] and f["textual"] and f["size"] < 16]
    problems += [f"{label}: the {b['words']!r} button should be .interactive, or it answers neither the cursor nor a press with the ink wash (class=\"action interactive\" or "
                f"\"label interactive\" in the page's template)" for b in found["buttons"] if not b["interactive"]]
    referred = {name for f in found["fields"] for name in f["described"]}
    for f in (f for f in found["fields"] if f["shown"]):
        wanted = AUTOCOMPLETE.get(f["name"])
        kinds = [d.rsplit("_", 1)[-1] for d in f["described"]]   # the ids end _help or _error: the help is named first
        for bad, why in ((not f["labelled"], "has no visible label"), (wanted and f["autocomplete"] != wanted, f"has autocomplete {f['autocomplete']!r}, not {wanted!r}"),
                         (sorted(kinds, key=lambda kind: kind == "error") != kinds, "lists its error before its help in aria-describedby"),
                         (any(d.startswith("!") for d in f["described"]), "is described by an id that is missing, repeated or empty"),
                         (f["invalid"] and not f["described"], "is aria-invalid and nothing describes it")):
            problems += [f"{label}: the {f['name']} field {why} (components/field.html, the page's template)"] if bad else []
    if [box for box in found["errorBoxes"] if box not in referred]:
        problems.append(f"{label}: an error sentence belongs to no field's aria-describedby, so a screen reader never reads it with the field")
    marked = [f["name"] for f in found["fields"] if f["invalid"]]
    if state.expect == "blank" and (marked or any(found["alerts"]) or found["errorBoxes"]):
        problems.append(f"{label}: a page nobody has filled in says something is wrong: {marked or found['alerts'] or found['errorBoxes']}")
    if state.expect == "field" and not (marked and found["errorBoxes"]):
        problems.append(f"{label}: the page should mark a field aria-invalid and say what is wrong under it; it marks {marked}")
    if state.expect == "alert" and not any(found["alerts"]):
        problems.append(f"{label}: the page should say what is wrong in a role=\"alert\" sentence (components/form_errors.html)")
    return problems


def forms_bare_problems(page, label, response, requests):
    """The 500 asks for no file: it must render when nothing else can."""
    found = page.evaluate(FORM_BARE)
    others = [url for url in requests if url != response.url and not url.endswith("/favicon.ico")]
    bad = others or found["files"] or not found["styles"] or found["h1"] != 1 or not (found["title"] and found["lang"] and found["viewport"])
    return [f"{label}: the 500 asked for {others[:3]} and has {found}; it should ask for no file and have inline CSS, a title, lang, viewport and one h1 (templates/500.html)"] if bad else []


def forms_focus_problems(page, label):
    """With the on-screen keyboard up (a window as high as what is left) a field put partly under the top fade and focused lands clear of it, above the keyboard. The input
    is what is measured: Chromium's focus() scrolls only when the input is not wholly below scroll-padding-top, and then leaves scroll-margin-top clear above it, so a label above
    an input that was already in view may stay in the fade."""
    fields, problems, tried = page.locator(TEXT_FIELDS), [], 0
    page.add_style_tag(content=ROOM_TO_SCROLL)
    for width, height in KEYBOARD_SCREENS if fields.count() else ():
        set_screen(page, width, height)
        here, fade = f"{label} at {width}x{height}px, the keyboard up", page.evaluate(FADE_HEIGHT)
        if fade <= 0:
            problems.append(f"{here}: --fade-top and --fade-band (tokens.css) give no top fade")
            continue
        for index in range(fields.count()):
            if fields.nth(index).is_visible():
                tried += page.evaluate(FIELD_PLACE, [TEXT_FIELDS, index, fade - UNDER_THE_FADE])["top"] < fade
                page.evaluate(NEXT_FRAMES)
                fields.nth(index).focus()
                page.evaluate(SCROLL_SETTLED)
                place = page.evaluate(FIELD_PLACE, [TEXT_FIELDS, index, None])
                if place["top"] < fade - 1 or place["bottom"] > place["view"] + 1:
                    problems.append(f"{here}: field {index + 1} took focus with its top at {place['top']:.0f}px and its bottom at {place['bottom']:.0f}px: under the top fade (ends {fade:.0f}px; "
                                    f"scroll-padding-top in base.css) or below the keyboard ({place['view']}px)")
    if fields.count() and not tried:
        REVIEW_NOTES.append(f"nothing to measure: no field of {label} could be put under the top fade")
    return problems


def forms_enter_problems(page, label, selector):
    """Enter in the field submits the form by its main action (a quiet Deny first would take it). The submit is stopped."""
    fill_fields(page.locator(selector).first.locator("xpath=ancestor::form[1]"))
    page.evaluate(WATCH_SUBMIT, selector)
    page.locator(selector).first.press("Enter")
    if not wait_for_state(page, "() => window.__submitted !== null", timeout=5000):
        return [f"{label}: Enter in {selector} did not submit the form (a required field is empty, or there is no submit button)"]
    seen = page.evaluate("() => window.__submitted")
    return [] if seen["ok"] else [f"{label}: Enter submitted the form by {seen['by']}; the form's first submit button should be its main action (.action), never one that skips the browser's checks"]


def forms_autofill_problems(page, label):
    """The browser's autofill colours are !important, so the page holds the field's look: a rule on each of :autofill and :-webkit-autofill keeps the text --ink under a
    transition that never ends (or an inset shadow), and one paints the field paper with Motion off. Playwright cannot make Chromium autofill a field: the rules are read."""
    rules, problems = page.evaluate(AUTOFILL_RULES), []
    for spelling in (":autofill", ":-webkit-autofill"):
        mine = [r for r in rules if "data-motion" not in r["selector"] and re.search(spelling.replace("-", r"\-") + r"(?![\w-])", r["selector"])]
        if not mine or any(not r["fill"] or not (r["held"] or r["inset"]) for r in mine):
            problems.append(f"{label}: no rule on {spelling} keeps the text --ink and holds the field's look (a long transition or an inset shadow): the browser paints its colour (static/css/components/field.css)")
    if not any(r["inset"] for r in rules if "data-motion" in r["selector"]):
        problems.append(f"{label}: no rule paints an autofilled field paper with Motion off, where every transition is off (static/css/components/field.css)")
    return problems


def forms_state_problems(browser, base_url, session_key, fixtures, state, out):
    """One state at 1280, 375 and 320 px: the page's checks, nothing sideways, axe (not at 320); with fields, the keyboard checks."""
    problems, requests = [], []
    context = form_context(browser, base_url, form_session(state.who, session_key, fixtures), state.path, state.name, width=1280, height=900)
    page, errors = fresh_page(context)
    page.on("request", lambda request: requests.append(request.url))
    try:
        response = FORM_DRIVERS[state.how](page, base_url, fixtures, state, problems)
        if response is None:
            return problems
        if response.status != state.status:
            return problems + [f"{state.name}: the page answered {response.status}, not {state.status}" + (f"; {STALE}" if response.status == 429 else "")]
        wait_for_state(page, FORM_READY)
        for width, height in FORM_SCREENS:
            set_screen(page, width, height)
            here = f"{state.name} at {width}px"
            full_page_screenshot(page, out / f"form-{state.name}-{width}.png")
            if state.bare:
                problems += forms_bare_problems(page, here, response, requests) if width == 1280 else []
            else:
                problems += forms_page_problems(page, here, state, phone=width != 1280)
            if width == 320:
                problems += sideways_problems(page, here)
            else:
                problems += (axe_problems(page, here) + sideways_problems(page, here)) if state.bare else page_problems(page, here)
        if state.enter:
            problems += forms_focus_problems(page, state.name) + forms_autofill_problems(page, state.name) + forms_enter_problems(page, state.name, state.enter)
    except PlaywrightError as error:
        problems.append(f"{state.name}: could not finish looking at the page: {error.message.splitlines()[0]}; if it is not the page the state means, {STALE}")
    finally:
        context.close()
    return problems + [f"{state.name}: console error: {error}" for error in errors]


def forms_round_trip_problems(browser, base_url, session_key, fixtures):
    """?next= makes the trip: the device page and OAuth consent, signed out, go to sign in and come back with their query."""
    problems, account, device = [], fixtures["accounts"]["verified"], fixtures["device"]["consent"]
    for tag, target, words in (("device", f"/device?user_code={device}", device), ("oauth", fixtures["oauth"]["authorize"], fixtures["oauth"]["app"])):
        label = f"{tag}, signed out, through sign in"
        context = form_context(browser, base_url, "", "/", "round-trip", width=1280, height=900)
        page, errors = fresh_page(context)
        try:
            page.goto(base_url + target, wait_until="networkidle")
            if urlsplit(page.url).path != "/accounts/login/" or parse_qs(urlsplit(page.url).query).get("next") != [target]:
                problems.append(f"{label}: went to {page.url}, not to sign in with next={target}")
            else:
                page.fill("main input[name=username]", account["username"])
                page.fill("main input[name=password]", account["password"])
                reply, here = submitted(page, press=page.locator("main input[name=password]")), urlsplit(page.url)
                if reply.status != 200 or here.path != urlsplit(target).path or parse_qs(here.query) != parse_qs(urlsplit(target).query):
                    problems.append(f"{label}: came back to {page.url} with status {reply.status}, not to {target} with 200" + (f"; {STALE}" if reply.status == 429 else ""))
                elif words not in page.locator("main").inner_text():
                    problems.append(f"{label}: came back to the address, but its page does not show {words!r}")
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{label}: console error: {error}" for error in errors]
    return problems


def forms_slow_frames_problems(browser, base_url, session_key, fixtures):
    """The keyboard checks again with every frame held SLOW_EVERY_FRAME_MS: a check that waited a fixed time would find the page unfinished."""
    problems = []
    for path, who, enter in (("/accounts/login/", "", "main input[name=password]"), ("/device", "verified", "main input[name=user_code]")):
        here = f"{path}, every frame held {SLOW_EVERY_FRAME_MS} ms"
        context = form_context(browser, base_url, form_session(who, session_key, fixtures), path, "slow-frames", width=1280, height=900)
        context.add_init_script(FRAME_STALL)
        page, errors = fresh_page(context)
        try:
            page.goto(base_url + path, wait_until="networkidle")
            wait_for_state(page, FORM_READY)
            problems += forms_focus_problems(page, here) + forms_enter_problems(page, here, enter)
        except PlaywrightError as error:
            problems.append(f"{here}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{here}: console error: {error}" for error in errors]
    return problems


def run_forms(browser, base_url, session_key, out, fixtures):
    """The forms part: every state (the throttled ones last), the ?next= round trips, the keyboard with frames held."""
    fixtures["run"] = f"{int(time.time()):x}"   # in the names of what a run makes, so a second run meets none of the first's
    problems = []

    def looked(states):
        nonlocal problems
        for state in states:
            started = time.perf_counter()
            problems += forms_state_problems(browser, base_url, session_key, fixtures, state, out)
            print(f"forms: {state.name} took {time.perf_counter() - started:.0f} s")

    looked([state for state in FORM_STATES if not state.spends])
    problems += timed("forms", forms_round_trip_problems, browser, base_url, session_key, fixtures)
    problems += timed("forms", forms_slow_frames_problems, browser, base_url, session_key, fixtures)
    looked([state for state in FORM_STATES if state.spends])
    return problems


# What Playwright's own tasks fail with when nothing awaits them
>>>>>>>
<<<<<<< 7. the parts
"phone": ("phone",),
               "all": ("pages", "cloth", "ink", "reading", "phone")}
=======
"phone": ("phone",), "forms": ("forms",),
               "all": ("pages", "cloth", "ink", "reading", "phone", "forms")}
>>>>>>>
<<<<<<< 8. the shared checks' comment
and the reading part on its own:
=======
and the reading and forms parts on their own:
>>>>>>>
<<<<<<< 9. the comment on the parts that stub the cloth
; the reading and phone parts stub the cloth out and
=======
; the reading, phone and forms parts stub the cloth out and
>>>>>>>
<<<<<<< 10. the label for the phone part
"reading": "the reading page checks"}
=======
"reading": "the reading page checks", "phone": "the phone menu checks"}
>>>>>>>
<<<<<<< 11. `run`: its signature
def run(base_url, session_key, out, part="all"):
=======
def run(base_url, session_key, out, part="all", fixtures=None):
>>>>>>>
<<<<<<< 12. `run`: its docstring
`reading` and `phone` are what CI runs as five jobs,
=======
`reading`, `phone` and `forms` are what CI runs as six jobs (fixtures: what the forms part needs),
>>>>>>>
<<<<<<< 13. `run`: one more stage
            else:
                problems += run_phone(browser, base_url, session_key, playwright)
=======
            elif stage == "phone":
                problems += run_phone(browser, base_url, session_key, playwright)
            else:
                problems += run_forms(browser, base_url, session_key, out, fixtures)
>>>>>>>
<<<<<<< 14. `run`: what forms says it has looked at
"phone": "in the phone menu's checks",
             "all"
=======
"phone": "in the phone menu's checks",
             "forms": f"across {len(FORM_STATES)} form state(s)",
             "all"
>>>>>>>
<<<<<<< 15. `run`: what all says
f"and in the phone menu's checks"}[part]
=======
f"and in the phone menu's checks, and across {len(FORM_STATES)} form state(s)"}[part]
>>>>>>>
<<<<<<< 16. `--part`: its help
"all (the default): every part, one after the other")
=======
"forms: the forms and the error pages; all (the default): every part, one after the other")
>>>>>>>
<<<<<<< 17. `main`
args.out, args.part))
=======
args.out, args.part,
             load_form_fixtures(args.session_file.with_name("form-fixtures.json")) if "forms" in PART_STAGES[args.part] else None))
>>>>>>>
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts pages.tests_form_fixtures`
Expected: `Ran 17 tests`, `FAILED (failures=3, errors=1)`: the job and the docs are still to come (`test_the_forms_job_installs_chromium_only_and_makes_the_fixtures_after_migrate_and_git_ignores_them`, `test_the_docs_name_every_site_job_where_they_say_which_checks_are_required`, `test_the_workflow_runs_each_part_in_a_job_of_its_own`, and the phone job's test with `ValueError: substring not found`).
Run: `site-tests/.venv/bin/python site-tests/run_site_checks.py --help`
Expected: a usage with `[--part {pages,cloth,ink,reading,phone,forms,all}]`.

- [ ] **Step 4: The CI job**

In `.github/workflows/ci.yml` (the job goes last, after `site-phone`: the phone job's test reads that job as the text up to `site-forms:`):

```yaml
<<<<<<<
# legal pages) and site-phone (the phone menu's sheet: its opening and closing, the
# changes chosen from it and the faces they wait for, in Chromium and WebKit; the
# only job that installs WebKit). Every site* job must pass;
=======
# legal pages), site-phone (the phone menu's sheet: its opening and closing, the
# changes chosen from it and the faces they wait for, in Chromium and WebKit; the
# only job that installs WebKit) and site-forms (the forms and the error pages, each
# in its states; it makes its own fixtures). Every site* job must pass;
>>>>>>>
<<<<<<<
          name: site-phone-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14
=======
          name: site-phone-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14

  site-forms:
    name: site-forms
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4.2.2
      - uses: actions/setup-node@v4.4.0
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: site-tests/package-lock.json
      - uses: actions/setup-python@v5.6.0
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            backend/requirements.txt
            site-tests/requirements.txt
      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt -r site-tests/requirements.txt
          python -m playwright install --with-deps chromium
          npm ci --prefix site-tests
      # The fixture script draws the 404 and the 500 as production does (this server runs with DEBUG on) and makes the users, device codes and OAuth clients.
      - name: Start the app
        working-directory: backend
        env:
          DJANGO_DEBUG: "True"
          DJANGO_SECRET_KEY: ci-site-not-a-secret
          SITE_SESSION_FILE: ../site-tests/session.txt
        run: |
          python manage.py migrate --noinput
          python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
          python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"
          nohup python manage.py runserver 127.0.0.1:8000 --noreload > ../site-tests/server.log 2>&1 &
          for attempt in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null && break; sleep 1; done
          curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { cat ../site-tests/server.log; exit 1; }
      # Chromium only: WebKit would repeat the checks at twice the cost and cannot show the keyboard, the zoom or the autofill of iOS.
      - name: Browser checks, the forms and the error pages
        run: python -u site-tests/run_site_checks.py --part forms --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
      - name: Keep the screenshots and the server log
        if: always()
        uses: actions/upload-artifact@v4.4.3
        with:
          name: site-forms-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14
>>>>>>>
```

Run the tests of Step 3 again. Expected: `Ran 17 tests`, `FAILED (failures=1)`: `test_the_docs_name_every_site_job_where_they_say_which_checks_are_required` (`the spec's description of the browser-check jobs (section 10) should name the `site-forms` job`).

- [ ] **Step 5: The words that name the job, and AGENTS.md**

Each old text occurs exactly once in its file. These are the "Site checks" section of AGENTS.md, its repo-map line for `site-tests/` and its CI paragraph; the paragraphs that Tasks 1 to 6 and 8 edit are others.

`site-tests/requirements.txt`:

```text
<<<<<<<
`site-reading` and `site-phone`)
=======
`site-reading`, `site-phone` and `site-forms`)
>>>>>>>
```

`site-tests/package.json`:

```text
<<<<<<<
`site-reading` and `site-phone`)
=======
`site-reading`, `site-phone` and `site-forms`)
>>>>>>>
```

`README.md`:

```text
<<<<<<<
`site-reading` and `site-phone`; see AGENTS.md
=======
`site-reading`, `site-phone` and `site-forms`; see AGENTS.md
>>>>>>>
<<<<<<<
`site-reading`, `site-phone`)
=======
`site-reading`, `site-phone`, `site-forms`)
>>>>>>>
```

`AGENTS.md` (the fifth adds the forms part's account before the cloth's):

```text
<<<<<<<
browser checks for pages on the new shell (CI jobs `site`, `site-cloth`, `site-ink`, `site-reading` and `site-phone`)
=======
browser checks for pages on the new shell (CI jobs `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone` and `site-forms`)
>>>>>>>
<<<<<<<
and `site-phone` (`--part phone`:
=======
, `site-phone` (`--part phone`:
>>>>>>>
<<<<<<<
faces they wait for, below). A new part
=======
faces they wait for, below) and `site-forms` (`--part forms`: the forms and the error pages, below). A new part
>>>>>>>
<<<<<<<
(the reading pages are `site-reading`'s) in Chromium
=======
(the reading pages are `site-reading`'s, the form and error pages `site-forms`') in Chromium
>>>>>>>
<<<<<<<
The cloth has checks of its own. Chromium runs
=======
The forms and the error pages (sign in and sign up, password reset, email verification, checkout confirm, the device flow, OAuth consent, the 404, the 403s, the 429s and the 500) are `FORM_STATES` in `site-tests/run_site_checks.py`, one `FormState` for each state a page has, and `site-forms` (`--part forms`, `run_forms`) checks them, in Chromium only: Playwright's WebKit cannot show what differs on an iPhone, so a pass there would repeat Chromium's reading at twice the cost. The words of each page are its Django tests'; this part checks what only a browser sees. A new form page or state is a `FormState`: its address (`{device[consent]}` and the like come from the fixtures), whose session it is (`staff` is the staff login of `--session-file`; the others are the fixtures' `sessions`), the driver in `FORM_DRIVERS` that brings it to the state (it fills the form and submits it as a visitor does, or uses a throttle's budget up), its status and what it must say about itself (`expect`). Each state is opened in Paper at 1280, 375 and 320 px, with axe at 1280 and 375 (Ink is the ink part's). `forms_page_problems` reads a visible label and the old autocomplete token on every field, every error tied to its field (`aria-invalid`, `aria-describedby` naming the help before the error), the ink wash on every button (`.interactive`), a blank page saying nothing is wrong and, on a phone, text of 16 px or more in a field; `forms_bare_problems` that the 500 asks for no file. A page with fields gets the phone's on-screen keyboard (`forms_focus_problems`: each field is put partly under the top fade and focused, and must land clear of it and above the keyboard; the input is what is measured, because Chromium's `focus()` does not scroll an input that is already in view, whatever stands above it), Enter (`forms_enter_problems`: it submits by the form's main action) and autofill (`forms_autofill_problems`: a rule on each of `:autofill` and `:-webkit-autofill` keeps the text `--ink` under a transition that never ends, and one paints the field paper with Motion off). `forms_round_trip_problems` follows `?next=` through sign in to the device page and OAuth consent, and `forms_slow_frames_problems` repeats the keyboard checks with every frame held 120 ms. Nothing waits for a fixed time: a check waits for the state it reads (`wait_for_state`), and `pages.tests_site_parts` fails if `run_forms` reaches a `wait_for_timeout` or a `sleep`. The part takes about 1.5 minutes on a laptop and 2 to 3 on CI's runner (the laptop's time measured, CI's from the reading part's ratio of the two; the run prints the real figures).

`site-tests/make_form_fixtures.py` makes what the part needs, as `make_staff_session.py` makes the staff login, and writes `site-tests/form-fixtures.json` (git ignores it): verified users and their sessions (one with the longest values the models take), a user whose verification link went a moment ago, a user to reset, device codes, OAuth clients registered through `/o/register/`, the links of the emails, and the 404 and the 500. Those two are drawn there because the dev server runs with DEBUG on, which answers an unknown address and an uncaught error with Django's technical pages and never calls the site's handlers: the script draws them with DEBUG off, by the whole request stack, and the checks serve those bytes (`drive_baked`). Run it again after a template changes (those two are drawn when it runs), and restart the dev server and run it again before each run of the part: the throttled states use up limits that live in the server's memory (sign-in, code lookups, password reset), and the codes and sessions are used up. A state that answers 429 where it should not says so. To be looked at on a real iPhone and a real Android phone when these pages change, since no check here sees them: iOS zooming into a field under 16 px (the check reads the size), the on-screen keyboard, the browser's real autofill (the check reads the rules), the order of the Tab stops, the label above a field that takes focus where it stands (the check measures the input), and a screen reader reading the errors.

The cloth has checks of its own. Chromium runs
>>>>>>>
<<<<<<<
`site-reading` keeps its own as `site-reading-screenshots`,
=======
`site-reading` keeps its own as `site-reading-screenshots`, `site-forms` its own as `site-forms-screenshots`,
>>>>>>>
<<<<<<<
which goes in `READING_PAGES`. To run it locally (`--part pages`, `--part cloth`, `--part ink`, `--part reading` or `--part phone` runs
=======
which goes in `READING_PAGES`, and a form or error page, which is a `FormState` in `FORM_STATES`. To run it locally (`--part pages`, `--part cloth`, `--part ink`, `--part reading`, `--part phone` or `--part forms` runs
>>>>>>>
<<<<<<<
that the pages part and the reading part both run on their own pages
=======
that the pages, reading and forms parts run on their own pages
>>>>>>>
<<<<<<<
cd .. && site-tests/.venv/bin/python site-tests/run_site_checks.py
=======
# the forms part also needs its fixtures (make them again, on a freshly started server, before each run of the part):
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"
cd .. && site-tests/.venv/bin/python site-tests/run_site_checks.py
>>>>>>>
<<<<<<<
never point it at production.
=======
never point it at production. `make_form_fixtures.py` plants users with a known password, and refuses in the same way.
>>>>>>>
<<<<<<<
`site-reading` and `site-phone`; the list is in `ci.yml`
=======
`site-reading`, `site-phone` and `site-forms`; the list is in `ci.yml`
>>>>>>>
```

`docs/superpowers/specs/2026-10-08-site-redesign-design.md` (1 to 8 in section 10, 9 in section 11 step 7, 10 in section 13):

```text
<<<<<<<
`site-reading` and `site-phone`:** the same browser run
=======
`site-reading`, `site-phone` and `site-forms`:** the same browser run
>>>>>>>
<<<<<<<
which no other job installs). A later part
=======
which no other job installs); `site-forms` runs the forms' and the error pages' checks (`--part forms`: every form, device, OAuth and error page in each state it has, with the phone's top fade, autofill, Enter and `?next=` through sign in, in Chromium, after `site-tests/make_form_fixtures.py` makes its users, device codes, OAuth clients and the 404 and 500 as production draws them). A later part
>>>>>>>
<<<<<<<
`site` and `site-reading` between them visit every public page
=======
`site`, `site-reading` and `site-forms` between them visit every public page, every form and error page in each state it has,
>>>>>>>
<<<<<<<
which its change checks watch).
=======
which its change checks watch); `site-forms` runs each state at 1280px, 375px and 320px in Paper with Motion on.
>>>>>>>
<<<<<<<
  - `site` and `site-reading` fail on:
=======
  - `site`, `site-reading` and `site-forms` fail on:
>>>>>>>
<<<<<<<
    - a broken menu, Come in, Copy or chapter change.
=======
    - a broken menu, Come in, Copy or chapter change;
    - (`site-forms`) a field without a label, an error not tied to its field, a button without the ink wash, text under 16px in a field on a phone, a focused field under the phone's top fade, a form that Enter does not submit, a 500 that asks for a file, and a `?next=` that is lost.
>>>>>>>
<<<<<<<
`site-reading` saves its own (`site-reading-screenshots`), and
=======
`site-reading` saves its own (`site-reading-screenshots`), `site-forms` its own (`site-forms-screenshots`), and
>>>>>>>
<<<<<<<
`site-reading` and `site-phone`) becomes a required check
=======
`site-reading`, `site-phone` and `site-forms`) becomes a required check
>>>>>>>
<<<<<<<
`site-reading` and `site-phone`).
=======
`site-reading`, `site-phone` and `site-forms`).
>>>>>>>
<<<<<<<
(`site-phone`). If it gets slow,
=======
(`site-phone`), and so do the forms' and error pages' checks, some 35 states of about 20 pages (`site-forms`). If it gets slow,
>>>>>>>
```

Run the tests of Step 3 again. Expected: `Ran 17 tests`, `OK`.

- [ ] **Step 6: Run the part in a browser**

This is the first time Tasks 1 to 6 meet this part, so findings are expected. Before each run of the part, start the server fresh and make the fixtures again (the throttled states use up limits that live in the server's memory, and the codes are used up). From `backend/`:

```bash
export DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key
../venv/bin/python manage.py migrate --noinput
SITE_SESSION_FILE=../site-tests/session.txt ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
SITE_SESSION_FILE=../site-tests/session.txt ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"
../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload
```

From the repo root, in a second terminal:

```bash
/usr/bin/time -p caffeinate -ims site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part forms
```

Expected: a `forms: <state> took N s` line for each of the 30 states that spend nothing, `forms: forms_round_trip_problems took N s`, `forms: forms_slow_frames_problems took N s`, then the 5 throttled states; and last `0 problem(s) across 35 form state(s); K note(s) to review by eye; screenshots and review-by-eye.txt in site-tests/artifacts`. The notes are what axe could not decide (a page shorter than the window is given room to scroll, so a `nothing to measure` line is rare: it means no field could be put under the fade). Screenshots: `site-tests/artifacts/form-<state>-<width>.png`.

If problems are listed, each names what to fix. Fix the cause in the file it names (Tasks 1 to 6; add the file to this task's commit and say so), restart the server and make the fixtures again before the next run. Where the messages point: `answered 429`, or a `came back to` that says 429, is not a defect (restart the server, make the fixtures again, as the message says); `could not finish looking at the page` names the selector or wait that failed (compare the page with its driver in `FORM_DRIVERS`); the structure, label, autocomplete and `aria-` messages, the page's template, `layouts/form.html` and `components/field.html`; `under 16px`, `field.css`; `sideways`, the `.form-page` rules in `base.css`; `under the top fade`, `scroll-padding-top` in `base.css`; `should be .interactive`, the button's class list in the page's template (`class="action interactive"` is Task 1's contract); `Enter`, a quiet button before the main action; `autofill` and `no rule`, `field.css`; `prints`, `Retry-After` and `the 500`, `pages/errors.py` and the error templates; `came back to` and `went to`, Task 2 (the sign-in view and its `?next=`).

If the machine run takes more than 3 minutes, stop and report DONE_WITH_CONCERNS. Otherwise record `real`, `user` and `sys`, and if the time is not within a third of the estimate, replace "about 1.5 minutes on a machine and 2 to 3" in AGENTS.md (`grep -n "about 1.5 minutes on a machine" AGENTS.md`) with the measured machine figure and that figure times 1.4 to 1.6.

- [ ] **Step 7: Prove the checks can fail**

Each check is run on a page broken in the browser, so no file is edited. With the server of Step 6 running (it needs no fresh fixtures: nothing here submits), from the repo root:

```bash
site-tests/.venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, "site-tests")
from playwright.sync_api import sync_playwright
import run_site_checks as c

base, login = "http://127.0.0.1:8777", c.FORM_STATES[0]
STYLE = "(css) => document.head.append(Object.assign(document.createElement('style'), { textContent: css }))"
NO_AUTOFILL = "() => { for (const s of document.styleSheets) for (let i = s.cssRules.length - 1; i >= 0; i--) if (/autofill/.test(s.cssRules[i].selectorText || '')) s.deleteRule(i); }"
QUIET = "() => { const b = document.createElement('button'); b.type = 'submit'; b.setAttribute('formnovalidate', ''); b.textContent = 'Deny'; document.querySelector('main form').prepend(b); }"
PLAIN = "() => document.querySelector('main button.action').classList.remove('interactive')"
UNTIED = "() => document.querySelector('main form').insertAdjacentHTML('beforeend', '<div id=\"id_x_error\">Wrong</div>')"
BACKWARDS = ("() => { const f = document.querySelector('main input[name=username]'); f.insertAdjacentHTML('afterend', '<div id=\"u_help\">Help</div><div id=\"u_error\">Wrong</div>');"
             " f.setAttribute('aria-describedby', 'u_error u_help'); }")
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=c.GL_ARGS)
    for what, js, arg, check in (
            ("15px text", STYLE, "main input { font-size: 15px !important; }", lambda p: c.forms_page_problems(p, "login at 375px", login, phone=True)),
            ("no scroll padding", STYLE, "html { scroll-padding-top: 0 !important; }", lambda p: c.forms_focus_problems(p, "login")),
            ("no autofill rules", NO_AUTOFILL, None, lambda p: c.forms_autofill_problems(p, "login")),
            ("a quiet button first", QUIET, None, lambda p: c.forms_enter_problems(p, "login", "main input[name=password]")),
            ("no visible label", STYLE, "main label { display: none !important; }", lambda p: c.forms_page_problems(p, "login at 1280px", login)),
            ("a plain button", PLAIN, None, lambda p: c.forms_page_problems(p, "login at 1280px", login)),
            ("an error tied to nothing", UNTIED, None, lambda p: c.forms_page_problems(p, "login at 1280px", login)),
            ("an error before its help", BACKWARDS, None, lambda p: c.forms_page_problems(p, "login at 1280px", login))):
        context = c.form_context(browser, base, "", "/accounts/login/", "proof", width=375, height=667)
        page, _ = c.fresh_page(context)
        page.goto(base + "/accounts/login/", wait_until="networkidle")
        c.wait_for_state(page, c.FORM_READY)
        page.evaluate(js, arg)
        found = check(page)
        print(what, len(found))
        for problem in found:
            print("   ", problem[:190])
        context.close()
    browser.close()
PYEOF
```
Expected: eight groups, each with the count it prints (measured in a scratch copy with Tasks 1 to 6 applied):
- `15px text 2`: `login at 375px: the username field's text is 15px; under 16px ...` and the same for `password`.
- `no scroll padding 4`: `login at 375x380px, the keyboard up: field 1 took focus with its top at 61px and its bottom at 107px: under the top fade (ends 81px; scroll-padding-top in base.css) ...` for each of the two fields, and again at 320x300px.
- `no autofill rules 3`: no rule on `:autofill`, none on `:-webkit-autofill`, none that paints the field paper with Motion off.
- `a quiet button first 1`: `login: Enter submitted the form by <button type="submit" formnovalidate="">Deny</button>; ...`.
- `no visible label 2`: `login at 1280px: the username field has no visible label (components/field.html, the page's template)` and the same for `password`.
- `a plain button 1`: `login at 1280px: the 'Log in' button should be .interactive, or it answers neither the cursor nor a press with the ink wash ...`.
- `an error tied to nothing 2`: `an error sentence belongs to no field's aria-describedby, so a screen reader never reads it with the field`, and `a page nobody has filled in says something is wrong: ['id_x_error']`.
- `an error before its help 2`: `the username field lists its error before its help in aria-describedby (components/field.html, the page's template)`, and the same `a page nobody has filled in says something is wrong: ['u_error']`.

A check that lists nothing is broken: fix it before going on.

- [ ] **Step 8: The reading part once, the whole suite, and the commit**

The reading part changed only in how `run` reaches it: `site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part reading`. Expected: `0 problem(s) across 5 reading page(s); ...`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes; the new tests are the 5 of `pages.tests_form_fixtures` and the 2 of `FormsPartTests`).

```bash
git add site-tests/make_form_fixtures.py site-tests/run_site_checks.py backend/pages/tests_site_parts.py backend/pages/tests_site_rules.py backend/pages/tests_form_fixtures.py .github/workflows/ci.yml .gitignore \
  AGENTS.md README.md docs/superpowers/specs/2026-10-08-site-redesign-design.md site-tests/requirements.txt site-tests/package.json
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Browser checks for the forms and the error pages, in a part and a CI job of their own, site-forms: every form, device, OAuth and error page in each state it has, a focused field under the phone's top fade, Enter, autofill and ?next= through sign in, and a fixture script for the users, device codes and OAuth clients they need"
```

(Add, by name, any file of Tasks 1 to 6 that Step 6 made you fix, and say which. Report the forms part's `real`, `user` and `sys` and the five slowest states.)

---

### Task 8: The old includes go, plus recipes, the spec and the last checks

> **Controller note (applies to Steps 2 and 5):** Task 3 did not drop `NoTemplateCommentLeakTests` and the old focus-rule test. It moved them into `backend/drop_core/tests_reset_verify_pages.py` as `NoTemplateCommentLeakTests` and `FocusRingTests`. Wherever this task says they "went with tests_ambient.py in Task 3" or "went in Task 3", read "Task 3 moved them into drop_core/tests_reset_verify_pages.py". When this task's own `PageTemplateConventionsTests` and `pages.tests_site_rules.FocusRingTests` are green, delete those two classes from `tests_reset_verify_pages.py` (they are then covered twice), and add that file to the commit.


**Files:**
- Create: `backend/pages/tests_forms_on_the_shell.py`
- Modify: `backend/pages/tests_site_rules.py` (three conventions, one fixture line, and the new module added to `READING_TEST_MODULES`), `AGENTS.md`, `README.md`, `docs/superpowers/specs/2026-10-08-site-redesign-design.md`, and one comment each in `frontend/index.html`, `frontend/src/Ambient.jsx` and `frontend/src/styles.css`
- Delete: `backend/templates/includes/theme_boot.html`, `backend/templates/includes/ambient.html`, `backend/templates/includes/menu.html`
- Not touched, on purpose: `backend/drop_core/tests.py`, `tests_menu.py` and `tests_ambient.py`. Task 3 retires the old-markup tests when it moves the last old page they read (the password reset pages), so this task finds them gone and only checks that they are (Step 1 and Step 5).
- Test: `backend/pages/tests_site_rules.py` (`PageTemplateConventionsTests`, `FocusRingTests`, `ReadableFailuresTests`), `backend/pages/tests_forms_on_the_shell.py` (`FormPagesAreOnTheShellTests`), then the whole suite and every browser part

**Interfaces:**
- Consumes: everything Tasks 1 to 7 made, by the names the plan fixes:
  - `layouts/form.html` (body class `layout-form`), `components/field.html` and `components/form_errors.html`;
  - the 18 old pages (12 under `registration/`, 5 under `device/`, `oauth/authorize.html`) moved onto the form layout by Tasks 2 to 5, so that no template includes `includes/theme_boot.html`, `includes/ambient.html` or `includes/menu.html` any more;
  - the conventions of Tasks 1 to 5 that the form pages share: the title `Name | Veilfile`, the main action `<button class="action interactive" type="submit">`, quieter actions `class="label interactive"`, the field's contract (`errors` a list, `extra` a string literal, `mono`), and a page stylesheet in `static/css/pages/` linked from the `styles` block;
  - `404.html`, `403.html`, `403_csrf.html` and `500.html` and `pages/errors.py` (Task 6), `registration/throttled.html`;
  - the `forms` part (`FORM_STATES`, `FormState`), the `site-forms` CI job and `site-tests/make_form_fixtures.py` (Task 7), and the "Site checks" text Task 7 wrote in AGENTS.md;
  - `READING_TEST_MODULES` in `pages/tests_site_rules.py`, which Tasks 1 to 7 each added their new test modules to (Task 2 Step 6 made the first edit);
  - Task 3's retirement of `drop_core/tests_menu.py`, `tests_ambient.py` and three old-chrome tests of `AccessibilityTests`, and its rewrite of `test_skip_link_on_verify_email_page` to the shell's markup.
- Produces:
  - Three conventions that outlive the old pages, each with a test whose failure names the file and the fix: no page writes its own `<html>` or `<style>` (only `shell.html` and `500.html` are whole documents, only `500.html` has a `<style>`); every `{# #}` comment opens and closes on one line (Django prints one that spans lines on the page); no stylesheet takes the focus outline away.
  - `pages.tests_forms_on_the_shell`: every form page has the shell's skip link and `<main>`, one `<h1>` inside it, a title of the form `Name | Veilfile`, `interactive` on every button and `.action` link, the menu and the mark, and none of the old pages' chrome. It takes the place of what the old-markup tests still tested that is live. It is held to `longMessage = False` by `READING_TEST_MODULES`, and Step 5 checks that every new test module of this PR is.
  - The old includes gone, with the three comments in `frontend/` that named them.
  - AGENTS.md: the "Add or change a form page" recipe, an "Error pages" section, and a repo map and "Page set-up" sentence that say that nothing but the dashboard is outside the shell.
  - The spec amended for every ruling of this PR, and the whole suite and every browser part run once, timed.

- [ ] **Step 1: Check the branch has everything this task cleans up after**

Run from the repo root:

```bash
git status --short
git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github AGENTS.md README.md docs frontend
grep -rlE 'includes/(theme_boot|ambient|menu)\.html' backend/templates | sort
grep -LE '\{% extends "(layouts/[a-z]+|shell)\.html" %\}' backend/templates/registration/*.html backend/templates/device/*.html backend/templates/oauth/*.html
ls backend/templates/layouts/form.html backend/templates/components/field.html backend/templates/components/form_errors.html backend/templates/404.html backend/templates/403.html backend/templates/403_csrf.html backend/templates/500.html
grep -c "site-forms" .github/workflows/ci.yml
site-tests/.venv/bin/python site-tests/run_site_checks.py --help | grep -c forms
ls backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py
grep -c "def test_skip_link_and_main_id_on_django_pages\|def test_decorative_wordmark_cursor_hidden\|def test_focus_indicator_rule_present_and_outline_not_removed" backend/drop_core/tests.py
grep -n -A12 "def test_skip_link_on_verify_email_page" backend/drop_core/tests.py | grep -c 'skip-link label'
grep -c "^READING_TEST_MODULES = (" backend/pages/tests_site_rules.py
```

Expected, in order:
1. Only the untracked `.DS_Store` and `.claude/` (a fresh worktree also shows its linked `venv`, `site-tests/.venv` and `site-tests/node_modules`).
2. Nothing from `git log`.
3. Nothing: every template that used the three includes was rewritten by Tasks 2 to 5, and the includes do not name each other.
4. Exactly one line, `backend/templates/registration/password_reset_email.html` (a plain-text mail body that has an `.html` name; `-L` lists the files that do not match). If it also lists a page (`logged_out.html`, say), a task left a page unmoved: stop and report which. `registration/logged_out.html` in particular is dead (nothing renders it) and Task 2 removes it.
5. The seven paths, with no error.
6. A number of 1 or more (the job's definition and its artifact).
7. A number of 1 or more.
8. Two `ls: ...: No such file or directory` lines, one for `tests_ambient.py` and one for `tests_menu.py`: Task 3 retired both modules in its own commit, because they assert the old chrome of the last old page and could not stay green once it moved.
9. `0` (the three old-chrome tests, `test_skip_link_and_main_id_on_django_pages`, `test_decorative_wordmark_cursor_hidden` and `test_focus_indicator_rule_present_and_outline_not_removed`, are gone from `drop_core/tests.py`), then `1` (`test_skip_link_on_verify_email_page` stays, and asserts the shell's `class="skip-link label"`), then `1` (`READING_TEST_MODULES`, the tuple of test modules held to `longMessage = False`, which Step 3 adds to; Tasks 1 to 7 have each added their modules to it).

If any of 1 to 9 is not so, stop and report which of Tasks 1 to 7 is incomplete: the rest of this task assumes them. A module or a test of the old chrome that is still there is Task 3's to retire, not this task's: do not delete it here.

- [ ] **Step 2: Write the conventions that outlive the old pages (test first)**

The old pages carried their own `<html>`, `<style>` and boot script. Nothing stops the next page from doing the same, so make "a page is a block of the shell" a rule that a test holds, before deleting anything. Three rules, each a test whose failure names the file and the fix: only `shell.html` and `500.html` are whole documents, and only `500.html` has a `<style>` element (a comment that mentions either is not markup, so comments are left out first); every `{# #}` comment opens and closes on one line (Django 6.1 strips a single-line one and prints one that spans lines as text; this replaces `NoTemplateCommentLeakTests`, which went with `tests_ambient.py` in Task 3 and looked at five rendered pages, with a check of every template's source, including the branches no request renders); no stylesheet takes the focus outline away (this replaces the old test that read the focus rule from a page's inline CSS, which went in Task 3 and which no page has now).

In `backend/pages/tests_site_rules.py`, replace this exact line:

```
# The test modules whose assertions compare whole pages. Each of their classes sets longMessage = False (ReadableFailuresTests).
```

with the following (its last line is the line being replaced, which stays):

```python
# The two templates that are whole documents: the shell, and the 500 page, which stands alone so that it still renders when the shell cannot (spec 4.9).
WHOLE_DOCUMENTS = ("shell.html", "500.html")
DOCUMENT_TAG = re.compile(r"<!doctype|<html\b", re.IGNORECASE)
STYLE_ELEMENT = re.compile(r"<style\b", re.IGNORECASE)
UNCLOSED_COMMENT = re.compile(r"{#(?:(?!#}).)*$")
OUTLINE_REMOVED = re.compile(r"\boutline(?:-style|-width)?\s*:\s*(?:none|hidden|0(?![\d.]))")


def without_comments(text):
    """A template's text without its {# #} comments and {% comment %} blocks, so a word in a comment is not taken for markup."""
    text = re.sub(r"{%\s*comment\b.*?{%\s*endcomment\s*%}", "", text, flags=re.DOTALL)
    return re.sub(r"{#.*?#}", "", text)


def templates_matching(pattern, root=TEMPLATES, allowed=()):
    """The templates under root (as paths relative to it) whose text, comments left out, matches pattern, except those named in allowed."""
    names = [path.relative_to(root).as_posix() for path in sorted(root.rglob("*.html"))]
    return [name for name in names
            if name not in allowed and pattern.search(without_comments((root / name).read_text(encoding="utf-8")))]


def comments_that_span_lines(root=TEMPLATES):
    """(file, line) for every {# comment under root that does not close on the line it opens on."""
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".txt"):
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                if UNCLOSED_COMMENT.search(line):
                    found.append((path.relative_to(root).as_posix(), number))
    return found


def outline_removals(css, name):
    """(file, line) for every declaration in css that takes the focus outline away; comments are left out."""
    blank = re.sub(r"/\*.*?\*/", lambda comment: re.sub(r"[^\n]", " ", comment.group()), css, flags=re.DOTALL)
    return [(name, number) for number, line in enumerate(blank.split("\n"), start=1) if OUTLINE_REMOVED.search(line)]


class PageTemplateConventionsTests(SimpleTestCase):
    longMessage = False

    def test_only_the_shell_and_the_500_page_are_whole_documents(self):
        own = templates_matching(DOCUMENT_TAG, allowed=WHOLE_DOCUMENTS)
        self.assertEqual(own, [], "\n" + "\n".join(
            f"backend/templates/{name}: writes its own <html> or doctype. A page extends layouts/form.html (a form), layouts/chapter.html "
            "or layouts/reading.html and fills a block, so it gets the shell's head, boot script, skip link, menu and corners; only "
            "shell.html and 500.html (which must render with nothing else) are whole documents" for name in own))

    def test_only_the_500_page_carries_a_style_element(self):
        own = templates_matching(STYLE_ELEMENT, allowed=("500.html",))
        self.assertEqual(own, [], "\n" + "\n".join(
            f"backend/templates/{name}: has a <style> element. A page's CSS lives in backend/static/css (base.css for a layout, "
            "components/<name>.css for a component, pages/<name>.css for what only one page has, linked from its `styles` block) and uses "
            "the tokens; only 500.html, which must render with nothing else, carries its own <style>" for name in own))

    def test_every_template_comment_opens_and_closes_on_one_line(self):
        found = comments_that_span_lines()
        self.assertEqual(found, [], "\n" + "\n".join(
            f"backend/templates/{name}:{number}: a comment opened with {{# does not close on its line, and Django prints a comment "
            "that spans lines on the page as text. Write one {# #} per line, or wrap the lines in {% comment %} ... {% endcomment %}"
            for name, number in found))

    def test_the_conventions_find_what_they_name(self):
        files = {
            "shell.html": "<!DOCTYPE html><html><body></body></html>",
            "500.html": "<!doctype html><html><style>p { margin: 0 }</style></html>",
            "page.html": '{% extends "layouts/form.html" %}\n{# a note about <html> and <style> #}\n{% comment %}<html><style>{% endcomment %}',
            "own.html": "<!DOCTYPE html>\n<html>\n<style>\np { margin: 0 }\n</style>",
            "mail.txt": "{# fine #}\n{# opens here\n and goes on #}",
            "note.html": "{# one #} text {# two\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
            self.assertEqual(templates_matching(DOCUMENT_TAG, root, WHOLE_DOCUMENTS), ["own.html"],
                             "backend/pages/tests_site_rules.py: templates_matching(DOCUMENT_TAG) must name a template that writes <html> or a doctype, "
                             "leave out the allowed ones, and not read a word in a {# #} comment or a {% comment %} block as markup")
            self.assertEqual(templates_matching(STYLE_ELEMENT, root, ("500.html",)), ["own.html"],
                             "backend/pages/tests_site_rules.py: templates_matching(STYLE_ELEMENT) must name a template that has a <style> element "
                             "and leave out the allowed one")
            self.assertEqual(comments_that_span_lines(root), [("mail.txt", 2), ("note.html", 1)],
                             "backend/pages/tests_site_rules.py: comments_that_span_lines() must name each {# that does not close on its line, "
                             "in .html and .txt templates, and no comment that does")


class FocusRingTests(SimpleTestCase):
    longMessage = False

    def test_base_css_draws_the_focus_outline_for_the_keyboard(self):
        css = (CSS / "base.css").read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r":focus-visible\s*\{[^}]*\boutline\s*:\s*[^;}]*\bsolid\b", css),
                             "backend/static/css/base.css: keep the global `:focus-visible` rule that draws the 1px outline (spec 3.4, 9). "
                             "Every control, a form's inputs included, shows keyboard focus with it")

    def test_no_stylesheet_takes_the_outline_away(self):
        found = []
        for path in sorted(CSS.rglob("*.css")):
            found += outline_removals(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix())
        self.assertEqual(found, [], "\n" + "\n".join(
            f"{name}:{number}: takes the focus outline away. Keyboard focus must stay visible (spec 3.4, 9): leave the outline "
            "that `:focus-visible` in base.css draws, or give the control an outline of its own, never none" for name, number in found))

    def test_the_outline_check_finds_what_it_names(self):
        css = ("a { outline: none; }\n/* outline: 0 in a comment */\nb { outline: 0; }\nc { outline-offset: 0; }\n"
               "d { outline: 1px solid; }\ne { outline-width: 0.5px; }\nf { outline-style: none }\n")
        self.assertEqual(outline_removals(css, "x.css"), [("x.css", 1), ("x.css", 3), ("x.css", 7)],
                         "backend/pages/tests_site_rules.py: outline_removals() must name each declaration that sets the outline, "
                         "its style or its width to none, hidden or 0, and not a comment, an offset or a width of 0.5px")


# The test modules whose assertions compare whole pages. Each of their classes sets longMessage = False (ReadableFailuresTests).
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.PageTemplateConventionsTests pages.tests_site_rules.FocusRingTests -v 1`
Expected: `Ran 7 tests`, then `FAILED (failures=1)`: `test_only_the_500_page_carries_a_style_element` fails with exactly two lines, `backend/templates/includes/ambient.html: has a <style> element. A page's CSS lives in backend/static/css ...` and the same for `backend/templates/includes/menu.html`. The other six pass: no page writes its own document, no comment spans lines, no stylesheet removes the outline, and the two helper checks find what they name. Those two includes are the only `<style>` left, which is what Step 4 deletes. If another template is named, a page still carries its own CSS: stop and report which task left it (a template that writes `<html>` is named by `test_only_the_shell_and_the_500_page_are_whole_documents` in the same way; a dead `registration/logged_out.html` that Task 2 should have removed is one such case: delete it).

Prove the outline rule can fail on a real stylesheet. From the repo root:

```bash
echo 'x { outline: none; }' >> backend/static/css/components/mark.css
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.FocusRingTests.test_no_stylesheet_takes_the_outline_away; cd ..
git checkout -- backend/static/css/components/mark.css
git status --short backend/static
```

Expected: FAIL with one line, `backend/static/css/components/mark.css:` and a line number, then `takes the focus outline away. Keyboard focus must stay visible (spec 3.4, 9): leave the outline that `:focus-visible` in base.css draws, or give the control an outline of its own, never none`; then `git status` prints nothing for `static` (the stylesheet is restored). If the run names another stylesheet instead, a form stylesheet of Task 1, 4 or 5 removes the outline: give that control an outline of its own (`:focus-visible { outline: ... }`) in the same rule, never weaken the test.

- [ ] **Step 3: Write the check that every form page is on the shell (test first)**

The old tests of the sign-in and sign-up pages pinned the old pages' chrome, and Task 3 retired them (Step 5 says where each live part went). What was live in them is that every form page has the shell's skip link and `<main>`, one heading, a title of its own, the menu and the mark, and that a form page is not in a layout family. Two more rules of the form pages belong with them, because nothing else holds every page to them: a title is `Name | Veilfile` (a vertical bar, as every page of the new site; the voice rules ban the em dash), and every `<button>` and every `.action` link in `<main>` carries `interactive`, which is what gives the main action its ink wash on hover and press (spec 4.7). Create `backend/pages/tests_forms_on_the_shell.py`:

```python
"""Every form page is on the shell (spec 4.7, 6.2, 9).

Sign in, sign up, password reset, email verification and the device pages sit on layouts/form.html: the shell's skip link and
<main>, one heading, a title of their own, the menu and the mark, and none of the chrome the old pages wrote by hand (a hamburger,
an ambient layer, a theme boot script, a <style>). Every control in <main> carries `interactive`, so it answers the cursor and a
press (spec 4.7). These checks took the place of the old-markup tests that pinned that chrome on the old pages (a hamburger menu,
an ambient layer, a wordmark with an underscore, a skip link written the old way), which Task 3 of this change retired.
"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

# Form pages a visitor can open with nothing set up. The token addresses are made up: the page for a link that does not work is a page too.
PUBLIC_FORM_PAGES = (
    "/accounts/login/",
    "/accounts/signup/",
    "/accounts/password_reset/",
    "/accounts/password_reset/done/",
    "/accounts/reset/done/",
    "/accounts/reset/MQ/not-a-token/",
    "/accounts/verify-email/not-a-token/",
)
# The ones that need a session: the email page and the device-code entry.
SIGNED_IN_FORM_PAGES = ("/accounts/verify-email/", "/device")
# What the old pages wrote by hand and the shell now gives every page.
OLD_CHROME = ('class="site-menu-wrap"', 'class="ambient"', 'class="ambient-controls"', 'id="veilfile-theme-boot"', "<style")
# A form page's title: its own name, a vertical bar, Veilfile. No em dash (the voice rules), and never the bare word.
TITLE = re.compile(r"[^|—<>]+ \| Veilfile")
CONTROL_TAG = re.compile(r"<(?:button|a)\b[^>]*>")
BODY_CLASS = re.compile(r'<body class="[^"]*\blayout-form\b')


def template_of(response):
    """Where a failure should send the reader: the template the page was drawn from."""
    names = [template.name for template in response.templates if template.name]
    return f"backend/templates/{names[0]}" if names else "the page's template"


def classes_of(tag):
    """The class names in an opening tag."""
    found = re.search(r'\sclass="([^"]*)"', tag)
    return found.group(1).split() if found else []


class FormPagesAreOnTheShellTests(TestCase):
    longMessage = False

    def check(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} answered {response.status_code}, not 200: a form page keeps its status (spec 4.7); "
                         "undo whatever changed it")
        html = response.content.decode()
        page = template_of(response)
        self.assertRegex(html, BODY_CLASS,
                         f"{page}: extend layouts/form.html (backend/templates/layouts/form.html), the narrow column of every form page (spec 6.2); "
                         "its body class is layout-form, and a page may add to it")
        self.assertNotIn("data-layout", html,
                         f"{page}: a form page has no layout family and so no data-layout: the shell would change it in place, and a form must load as an ordinary page")
        skip, main = html.find('<a class="skip-link label" href="#main">'), html.find('<main id="main" class="shell-main">')
        self.assertTrue(0 <= skip < main,
                        f'{page}: the shell\'s skip link must come before <main id="main" class="shell-main">; a form page fills a block of layouts/form.html and writes neither')
        inside = html[main:html.find("</main>", main)]
        self.assertEqual((html.count("<h1"), inside.count("<h1")), (1, 1),
                         f"{page}: one <h1> per page, inside <main> (spec 9). Put the page's heading in {{% block form_page %}} and write no other <h1>")
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        self.assertTrue(title is not None and TITLE.fullmatch(title.group(1).strip()),
                        f"{page}: fill {{% block title %}} with the page's own name, a vertical bar and Veilfile (`Log in | Veilfile`), as every page of the site "
                        "has; no em dash (the voice rules). A form page has no registry entry, so the shell's default is the bare word Veilfile")
        for tag in CONTROL_TAG.findall(inside):
            classes = classes_of(tag)
            if tag.startswith("<button") or "action" in classes:
                self.assertIn("interactive", classes,
                              f"{page}: {tag} is a control without the class `interactive`, so it would not answer the cursor or a press. Write the main action "
                              'as <button class="action interactive" type="submit"> and a quieter one as class="label interactive" (spec 4.7)')
        for chrome in OLD_CHROME:
            self.assertNotIn(chrome, html, f"{page}: has {chrome}, which the old pages wrote by hand. The shell gives the menu, the switches, the head and the "
                             "cloth; delete it from the template")
        self.assertEqual(html.count('<nav class="menu"'), 1, f"{page}: the shell's menu (components/menu.html) must be on the page once")
        self.assertIn('<svg class="mark" viewBox="0 0 560 560" aria-hidden="true"', html,
                      f"{page}: the shell's mark must be on the page and hidden from screen readers (components/mark.html); its name is the lockup's hidden word")
        self.assertNotIn("{#", html, f"{page}: a {{# comment spans lines and Django printed it on the page. Write one {{# #}} per line")
        self.assertNotIn("#}", html, f"{page}: a {{# comment spans lines and Django printed it on the page. Write one {{# #}} per line")

    def test_the_form_pages_a_visitor_can_open(self):
        for url in PUBLIC_FORM_PAGES:
            with self.subTest(url=url):
                self.check(url)

    def test_the_form_pages_that_need_a_session(self):
        self.client.force_login(get_user_model().objects.create_user(username="forms-shell", password="pw12345678"))
        for url in SIGNED_IN_FORM_PAGES:
            with self.subTest(url=url):
                self.check(url)

    def test_the_title_and_control_checks_find_what_they_name(self):
        for title, fits in (("Log in | Veilfile", True), ("Enter device code | Veilfile", True), ("Veilfile", False),
                            ("Log in — Veilfile", False), ("| Veilfile", False), ("Log in | Veilfile | Veilfile", False)):
            self.assertEqual(bool(TITLE.fullmatch(title)), fits,
                             f"backend/pages/tests_forms_on_the_shell.py: TITLE must accept `Name | Veilfile` and refuse the bare word, an em dash and an empty name; {title!r} must be {'accepted' if fits else 'refused'}")
        self.assertEqual(classes_of('<button class="action interactive" type="submit">'), ["action", "interactive"],
                         "backend/pages/tests_forms_on_the_shell.py: classes_of() must list the class names of an opening tag")
        self.assertEqual(classes_of('<a data-class="x" href="/">'), [],
                         "backend/pages/tests_forms_on_the_shell.py: classes_of() must read the class attribute only, not data-class")
        self.assertEqual(CONTROL_TAG.findall('<p>a <a href="/">b</a> <button type="submit">c</button> <abbr>d</abbr> <a class="action">e</a></p>'),
                         ['<a href="/">', '<button type="submit">', '<a class="action">'],
                         "backend/pages/tests_forms_on_the_shell.py: CONTROL_TAG must find each <a> and <button> opening tag, and no other tag that begins with a or b")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms_on_the_shell -v 2`
Expected: `Ran 3 tests`, all `ok`, then `OK`. The first two pass on the first run because Tasks 2 to 5 moved the pages; the third checks the title and control patterns on their own; the proofs that the first two can fail follow. If a failure names a page, its message names the template and the fix (a second `<h1>`, a title left at the bare word Veilfile or written with an em dash, a button without `interactive`, a missing menu). Fix that template as the message says; never weaken a test. If it names a URL that answers other than 200 (for example `/accounts/reset/MQ/not-a-token/`), a task changed a status code that the plan keeps, so fix that task.

Hold the new module to `longMessage = False` as every module of this PR is (`pages.tests_site_rules.ReadableFailuresTests` fails a listed module that has a class whose `longMessage` is not `False`; its message names the file and the fix). `READING_TEST_MODULES` already lists the modules of Tasks 1 to 7, so this script adds one name to the end of whatever the tuple holds, and fails if it cannot find the tuple or the name is already there. From the repo root:

```bash
python3 - <<'EOF'
import re
from pathlib import Path

path = Path("backend/pages/tests_site_rules.py")
text = path.read_text(encoding="utf-8")
match = re.search(r"^READING_TEST_MODULES = \(.*?\)$", text, flags=re.DOTALL | re.MULTILINE)
assert match, "backend/pages/tests_site_rules.py no longer has READING_TEST_MODULES = (...)"
modules = match.group(0)
assert '"pages.tests_forms_on_the_shell"' not in modules, "pages.tests_forms_on_the_shell is already in READING_TEST_MODULES"
held = modules[:-1].rstrip().rstrip(",") + ',\n' + " " * 24 + '"pages.tests_forms_on_the_shell")'
path.write_text(text.replace(modules, held, 1), encoding="utf-8")
EOF
git diff -U1 backend/pages/tests_site_rules.py | tail -8
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests; cd ..
```

Expected: the `git diff` shows `READING_TEST_MODULES` ending with a new line holding `"pages.tests_forms_on_the_shell")` (the module names of Tasks 1 to 7 above it), and the test run ends `OK` after `Ran 2 tests`.

Prove the checks can fail. A second `<h1>` in the shell would be on every page:

```bash
python3 - <<'EOF'
from pathlib import Path
path = Path("backend/templates/shell.html")
text = path.read_text(encoding="utf-8")
new = text.replace('<main id="main" class="shell-main">{% block main %}', '<main id="main" class="shell-main"><h1>Extra</h1>{% block main %}', 1)
assert new != text, "backend/templates/shell.html no longer has the <main> line this proof edits"
path.write_text(new, encoding="utf-8")
EOF
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms_on_the_shell; cd ..
git checkout -- backend/templates/shell.html
git status --short backend/templates/shell.html
```

Expected: `Ran 3 tests` and `FAILED (failures=9)`, one for each of the seven public pages and two signed-in pages, each ending `one <h1> per page, inside <main> (spec 9). Put the page's heading in {% block form_page %} and write no other <h1>` and starting with that page's template (`backend/templates/registration/login.html`, ...); then `git status` prints nothing (the shell is restored).

A page whose title is written with an em dash and a page whose main button lacks `interactive` are named too (the title and button rules of this PR, which Tasks 2 to 5 follow):

```bash
python3 - <<'EOF'
from pathlib import Path
for name, old, new in (
        ("backend/templates/registration/signup.html", "| Veilfile{% endblock %}", "— Veilfile{% endblock %}"),
        ("backend/templates/registration/login.html", '<button class="action interactive"', '<button class="action"')):
    path = Path(name)
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{name} no longer has {old!r}, which this proof edits"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
EOF
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_forms_on_the_shell.FormPagesAreOnTheShellTests.test_the_form_pages_a_visitor_can_open; cd ..
git checkout -- backend/templates/registration/signup.html backend/templates/registration/login.html
git status --short backend/templates/registration
```

Expected: `FAILED (failures=2)`: `backend/templates/registration/login.html: <button class="action" type="submit"> is a control without the class `interactive` ...` for `/accounts/login/` and `backend/templates/registration/signup.html: fill {% block title %} with the page's own name, a vertical bar and Veilfile ...` for `/accounts/signup/`; then `git status` prints nothing for `registration` (both templates are restored). If the assertion in the script fails, the page writes that attribute another way: edit the proof to match the template, never the check.

- [ ] **Step 4: Prove nothing uses the three includes, then delete them**

```bash
grep -rnE 'includes/(theme_boot|ambient|menu)\.html' backend site-tests tools .github AGENTS.md README.md docs/llms.txt docs/API.md frontend/index.html frontend/src
```

Expected: no template, no stylesheet and no script, only these lines (their line numbers are those of `main`; they move if a task edited the file above them):
- `AGENTS.md:NN`, the repo-map sentence Task 2 wrote ("... a page whose template still includes includes/ambient.html is an older page that has not moved onto it yet"), which Step 7 replaces;
- `backend/pages/tests_site_rules.py:NNN` (178 on `main`), a fixture string in `test_a_template_is_under_the_shell_rules_by_what_it_extends`, text in a temporary directory, not a file;
- `frontend/index.html:4`, `frontend/src/Ambient.jsx:2` and `frontend/src/styles.css:459`, three comments in the React dashboard that say it mirrors a deleted include.

If any `backend/templates/...` line prints, that template still includes one: stop and fix it, do not delete. If a test module under `backend/` prints, a task kept a test of the old includes: stop and report which.

Anything else only they used? The old pages' colours and sizes (`--bg`, `--line`, `--text`, `--accent`, `--danger`, `--warn`, `--mono` and the rest) were defined by `ambient.html` alone, so a stylesheet or template that still reads one would lose it with the file:

```bash
grep -rlE 'var\(--(bg|bg-raise|bg-panel|line|text|accent|accent-hover|accent-dim|on-accent|warn|danger|mono|sans)[,)]' backend/templates backend/static | sort
```

Expected: exactly `backend/templates/includes/ambient.html` and `backend/templates/includes/menu.html` (the two files being deleted). If a page or stylesheet is listed, it still draws with the old tokens: stop and report which task left it. Then:

```bash
git rm backend/templates/includes/theme_boot.html backend/templates/includes/ambient.html backend/templates/includes/menu.html
ls backend/templates/includes
```

Expected: `git rm` prints one `rm '...'` line for each of the three includes; `ls` prints `ls: backend/templates/includes: No such file or directory` (the directory goes with its last file). The `includes/` folders under `device/` and `oauth/` hold those pages' own partials and stay.

The fixture string in `tests_site_rules.py` names a file that no longer exists, and it stood for "an old page, not under the shell rules". Make it say that without the name, so the final grep finds nothing:

In `backend/pages/tests_site_rules.py`, replace this exact text:

```
            "forms/login.html": '{% include "includes/menu.html" %}',
```

with:

```
            "forms/login.html": "<!DOCTYPE html><html><body>a page with its own document</body></html>",
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content pages.tests_forms_on_the_shell pages.tests_catalog -v 1`
Expected: OK, with no failure from `test_every_include_and_extends_names_a_template_that_exists` (nothing names a deleted include) and none from `PageTemplateConventionsTests` (the two `<style>` includes are gone).

- [ ] **Step 5: Check that the old-chrome tests stay retired, and that every new test module is held to `longMessage = False`**

Task 3 retired the tests of the old chrome in the commit that moved the last old page, so nothing is left to delete here. What they held, and where each live part is held now:
- `tests_menu.py` (`SharedMenuTests`, six tests of the old hamburger: its markup, ten links in an approved order, its inline keyboard script, its place after the skip link, `role="menuitem"`, no Trust label): retired with the menu it tested. The shell's menu is a different component with its own tests: `pages.tests_shell` (`ShellBodyTests`: the links in order, Sign in or Dashboard; `MenuStartsClosedTests`: Escape, a click elsewhere, closed on load) and `pages.tests_registry` (labels and audiences). `FormPagesAreOnTheShellTests` checks that every form page has that menu, once.
- `tests_ambient.py` (the boot script, the blobs, the old tokens, keyframes, print rule and control cluster of `ambient.html`): retired. The shell's boot script is pinned by `pages.tests_shell.ShellHeadTests` and its switches by `ShellBodyTests`. Its one live class, `NoTemplateCommentLeakTests`, is held now by Step 2 (a check of every template's source) and by `FormPagesAreOnTheShellTests` (a check of each form page as rendered).
- `AccessibilityTests`, three tests of the old pages' chrome (`<a class="skip-link" href="#main">` and `<main id="main">`, the `_` of the old wordmark, `:focus-visible` in the page's inline CSS): `test_skip_link_and_main_id_on_django_pages` is held for every form page, signed out and signed in, by `FormPagesAreOnTheShellTests`; `test_focus_indicator_rule_present_and_outline_not_removed` by `FocusRingTests` (the rule is in `base.css` now, and the test also holds every other stylesheet to it); `test_decorative_wordmark_cursor_hidden` went with the `_` wordmark (the shell's mark is `aria-hidden`, which `FormPagesAreOnTheShellTests` checks on every form page, and its name is the hidden word in the lockup: `pages.tests_shell.ShellBodyTests.test_lockup_is_the_mark_alone_with_its_name_for_screen_readers`). `test_skip_link_on_verify_email_page` stays, pointed at the shell's markup.

Now the second check, which closes what the tuple edits of Tasks 1 to 7 and Step 3 might have missed. Every test module this PR added must be in `READING_TEST_MODULES`. From the repo root:

```bash
for module in $(git diff --name-only --diff-filter=A origin/main HEAD -- 'backend/*tests_*.py' | sed -e 's#^backend/##' -e 's#\.py$##' -e 's#/#.#g') pages.tests_forms_on_the_shell; do
  grep -q "\"$module\"" backend/pages/tests_site_rules.py || echo "not in READING_TEST_MODULES: $module"
done
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.AccessibilityTests pages.tests_site_rules pages.tests_forms_on_the_shell -v 1; cd ..
```

Expected: the loop prints nothing, then the test run ends `OK` (the class keeps its React-source tests, the Docs and the error-announcement tests). If the loop names a module, the task that created it did not add it to the tuple: add its name to the tuple's end with the script of Step 3 (change the name in it), run `pages.tests_site_rules.ReadableFailuresTests`, and say in your report which task left it out. If that test then names a class, the class needs `longMessage = False` in its body: add it.

- [ ] **Step 6: Fix the three comments in the dashboard that name the deleted includes**

The React dashboard stays until the next PR and still shares the storage keys with the shell. Only the words that said it mirrors a deleted file change.

In `frontend/index.html`, replace this exact text:

```
    <!-- Pre-paint theme boot: mirrors backend/templates/includes/theme_boot.html.
         The localStorage keys (veilfile-theme, veilfile-motion) and the
         data-theme / data-motion attributes on <html> are the cross-surface
         contract shared with the Django controls. -->
```

with:

```
    <!-- Pre-paint theme boot: the dashboard's half of the contract with the boot script in backend/templates/shell.html.
         The localStorage keys (veilfile-theme, veilfile-motion) and the
         data-theme / data-motion attributes on <html> are the cross-surface
         contract shared with the shell's controls. -->
```

In `frontend/src/Ambient.jsx`, replace this exact text:

```
// Ambient background + theme controls for the React routes. Mirrors
// backend/templates/includes/ambient.html: the same localStorage keys
// (veilfile-theme, veilfile-motion) and the same data-theme / data-motion
// attributes on <html> are the cross-surface contract, so both surfaces
// honor attributes set by the other. No new dependencies.
```

with:

```
// Ambient background + theme controls for the React routes. The same localStorage
// keys (veilfile-theme, veilfile-motion) and the same data-theme / data-motion
// attributes on <html> as the shell's (backend/templates/shell.html) are the
// cross-surface contract, so both surfaces honor attributes set by the other.
// No new dependencies.
```

In `frontend/src/styles.css`, replace this exact text:

```
/* Mirrors backend/templates/includes/ambient.html: same class names, same
   data-ambient contract, same keyframes. The .ambient wrapper is the future
   WebGL canvas host: to swap, render <canvas class="ambient-canvas"> inside
   it instead of the blob divs and keep everything else as is. */
```

with:

```
/* The dashboard's own ambient field (the Django pages have the shell's cloth). The
   .ambient wrapper is the future WebGL canvas host: to swap, render
   <canvas class="ambient-canvas"> inside it instead of the blob divs and keep
   everything else as is. */
```

- [ ] **Step 7: AGENTS.md**

Make each replacement in `AGENTS.md`. Each old text appears exactly once. The "Site checks" section, the `site-tests/` line of the repo map, the "The browser checks run as" paragraph and the "wait for" paragraph (the job names, the forms part and how to run it) are Task 7's: do not touch them.

(a) Repo map. Tasks 1 and 2 each changed a line of the `backend/templates/` entry (Task 1 added `form` to the layouts, Task 2 wrote the sentence about the pages that moved and the pages that had not), so the old text depends on how they wrapped it. Replace the whole entry, from its first line to the line before `backend/pages/`, with a script that checks what it replaces and prints it. From the repo root:

```bash
python3 - <<'EOF'
import re
from pathlib import Path

path = Path("AGENTS.md")
text = path.read_text(encoding="utf-8")
entry = re.search(r"^backend/templates/ {5}Django templates.*?\n(?=backend/pages/ )", text, flags=re.DOTALL | re.MULTILINE)
assert entry, "AGENTS.md: the repo map no longer has a backend/templates/ entry before the backend/pages/ one"
old = entry.group(0)
assert "shell.html" in old and "layouts/" in old and old.count("\n") <= 8, "AGENTS.md: the backend/templates/ entry is not the few lines this step replaces:\n" + old
print(old, end="")
new = """backend/templates/     Django templates, every page on the new shell: shell.html,
                       layouts/ (chapter, reading, form), chapters/ (the threshold and
                       the chapters), components/, the blog/, docs/ and legal/ pages,
                       the form pages (registration/, device/, oauth/) and the error
                       pages (404.html, 403.html, 403_csrf.html; 500.html stands
                       alone). The dashboard at /app/ is the one page not on the shell
                       (React, in frontend/)
"""
path.write_text(text.replace(old, new, 1), encoding="utf-8")
EOF
```

Expected: the script prints the old entry, which ends with Task 2's clause (`... all on the new shell; a page whose template still includes includes/ambient.html is an older page that has not moved onto it yet`) and has `layouts/ (chapter, reading, form),` in its first line, and no error. The entry now reads as the `new` text above.

In `AGENTS.md`, replace this exact text (the last line of the `backend/pages/` entry; Task 6 added `errors.py` to that folder):

```
                       sitemap, component catalog, content checks
```

with:

```
                       sitemap, component catalog, content checks, and the error
                       pages' views (errors.py)
```

(b) "Page set-up" under "How shell.js works":

In `AGENTS.md`, replace this exact text:

```
because pages not yet on the shell read the same keys.
```

with:

```
because the dashboard, the one page not on the shell yet, reads the same keys.
```

(c) The form-page recipe and the error-pages section, after the component recipe:

In `AGENTS.md`, replace this exact text:

````
9. Check it: the `pages` tests above, then the browser checks ("Site checks").

## Docs and tests hygiene
````

with:

````
9. Check it: the `pages` tests above, then the browser checks ("Site checks").

### Add or change a form page

Who: usually the CTO's team. A form page has an input or a one-time state: sign in,
sign up, password reset, email verification, checkout confirmation, the device-code
pages and OAuth consent. All of them sit on `layouts/form.html`: one narrow column
(`--form-measure` in `tokens.css`) with no registry entry, so each loads as an ordinary
page (no `data-layout`, no change in place). Their URLs, field names, hidden fields,
status codes and pinned phrases are contracts that the CLIs, OAuth clients and tests
rely on (invariant 4): change how a page looks and what it says, not what it posts or
where.

1. Start from the nearest page: `registration/login.html` for fields and one action,
   `registration/password_reset_done.html` for words only, `oauth/authorize.html` for two
   forms on one page. Keep its view, its URL and its status codes.
2. The template starts `{% extends "layouts/form.html" %}` and fills these blocks:
   `title` (`Name | Veilfile`: the page's own name, a vertical bar and Veilfile, as every
   page of the site has; the shell's default is the bare word Veilfile), `meta` (add
   `<meta name="robots" content="noindex">` where the page holds a token or a one-time
   state) and `form_page` (the page's one `<h1>`, then the form or the words). CSS that
   only this page has goes in `backend/static/css/pages/<name>.css`, tokens only, linked
   from the `styles` block as `{% block styles %}{{ block.super }}<link rel="stylesheet"
   href="{% static 'css/pages/<name>.css' %}">{% endblock %}`, with a body class to hang
   it on (`{% block body_class %}{{ block.super }} form-<name>{% endblock %}`). Write no
   `<html>`, `<head>`, `<style>`, skip link, menu, `<header>` or `<footer>`: the shell has
   them, and tests fail when a page brings its own.
3. Fields are `components/field.html`: a mono label above the input, with a help line
   (above the input too) and an error sentence. Include it like this (line 2 of the
   component says what each input holds):

   ```
   {% include "components/field.html" with id=... name=... label=... type=... value=... autocomplete=... required=... autofocus=... help=... errors=... extra=... mono=... only %}
   ```

   `errors` is a form field's `form.<field>.errors` or a list of sentences, never a bare
   string. `extra` is the exact text of an attribute a test pins, and it must be a string
   literal in the template, never a variable (a test scans every template). `mono` is for a
   code. The input's line is drawn in `--muted`, not `--hair`: `--hair` is 1.45:1 on Paper,
   and a field's boundary needs 3:1 (WCAG 1.4.11). Errors that belong to no field go in
   `components/form_errors.html`, with `errors=...` (a list, such as
   `form.non_field_errors`) or `message=...` (one fixed sentence of the page's own), `only`.
4. `{% csrf_token %}` goes in the page's own `<form method="post">`, never inside a
   component: a component included with `only` renders no token. A form posts to its own
   address (no `action` attribute), so `?next=`, `?plan=` and `?user_code=` ride along.
   Never rename a field or a hidden field, or change a value.
5. One main action: `<button class="action interactive" type="submit">`, a hairline box
   in a mono label that takes the ink wash (`interactive` is what gives it the wash and
   the press; a test fails a button or an `.action` link without it). Two actions of equal
   weight, such as Approve and Deny, go in `<div class="form-actions">`. Quieter links and
   buttons are `<p class="form-links">` holding `a.label.interactive` or
   `button.label.interactive`. A status line that is not an error is
   `<p class="notice" role="status">` (`role="note"` where a screen reader should not hear
   it). A checkbox or radio row is `<label class="check"><input class="check__input"
   type="checkbox" ...><span class="check__text">words</span></label>`, a group of them in
   a `<fieldset>` with a `<legend>`. Errors use no colour, only weight, a solid line and a
   small "Check this" (the field component draws them): do not add a colour. Colours live
   in `tokens.css` and nowhere else.
6. Words: keep the page's current words. A new sentence follows the voice rules (step 3
   of "Edit a chapter's words": no em dashes, no exclamation marks, none of the banned
   words, `’` for apostrophes), says what happened and what to do, and does not
   apologise. Every non-ASCII character must exist in all three fonts (the check mark
   U+2713 does not; `pages.tests_content` checks).
7. Accessibility (spec 9): one `<h1>` per page, inside `<main>`; a visible `<label for>`
   on every input; `autocomplete` tokens kept; `autofocus` only on a page that exists to
   take one input (the device code, the reset request, the new password); inputs 16px or
   larger on phones, so iOS does not zoom in, and clear of the top fade when focused;
   nothing scrolls sideways at 320px. The components do these; keep to them.
8. Tests: beside the page's other tests, one render test for each state (blank, field
   errors, non-field errors, throttled), asserting step 4's contracts and the pinned
   words. Add a page a visitor can open to `PUBLIC_FORM_PAGES` (one that needs a session:
   `SIGNED_IN_FORM_PAGES`) in `backend/pages/tests_forms_on_the_shell.py`, which holds every
   form page to the shell's skip link and `<main>`, one heading, a title of the form
   `Name | Veilfile`, `interactive` on every button and `.action` link, and none of the old
   pages' chrome. A new test module sets `longMessage = False` in each class and is added to
   `READING_TEST_MODULES` in `backend/pages/tests_site_rules.py`, which fails a module with a
   class that does not.
9. Browser checks: add a `FormState` for each state the page has to `FORM_STATES` in
   `site-tests/run_site_checks.py` (`--part forms`, CI job `site-forms`), and whatever the
   page needs (a token, a pending code) to `site-tests/make_form_fixtures.py`. "Site checks"
   says how.
10. Check it:
    `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core`,
    then the forms part ("Site checks").
11. Open a pull request.

## Error pages

404, 403 (a failed CSRF check included) and every 429 are pages on the shell; 500 stands
alone (spec 4.9). These are the HTML pages only: the JSON that the API, MCP and OAuth
endpoints answer with is unchanged.

- Where they are: the views are in `backend/pages/errors.py` (`not_found`,
  `permission_denied`, `csrf_failure`, `server_error`), named by `handler404`, `handler403`
  and `handler500` in `config/urls.py` and by `CSRF_FAILURE_VIEW` in `config/settings.py`.
  The templates are `404.html`, `403.html` and `403_csrf.html` (a title and a sentence on
  `layouts/chapter.html`, each `noindex`), `500.html`, and `registration/throttled.html` for
  every throttle: sign in, password reset, sign up and email verification each answer with
  it, and with the `Retry-After` the throttle sets.
- What they may show: their own words and the shell. Never `exception`, `request_path`, a
  CSRF failure's reason or anything that belongs to a person (a name, a username, a CSRF
  token): a page that says a name tells whoever asks which names exist. The throttled page
  is the same bytes for a real account and a missing one, which is why it has no CSRF token.
- A 404 or 403 that cannot be drawn does not become a worse error: `shell_error_page`
  catches the failure and answers with `500.html` under the same status. Keep these pages
  simple all the same: words and the shell, nothing else.
- `/a/<token>`: a missing link and an expired one (and a revoked or held one, or one whose
  file is gone) get the same page from `artifact_miss()` in `pages/errors.py`, with the same
  status, body and headers (`Vary` and `Cache-Control` included). It is drawn with no
  request, so it never reads the session and is the same bytes for everyone, and it says only
  "Nothing here. If this link was shared with you, its moment may have passed." Whether a
  token ever existed must not show. A link that works serves the file's bytes untouched and
  is never wrapped in the shell. `pages.tests_artifact_miss` compares the responses.
- 500 (`500.html`) is self-contained, with inline CSS and no context, so it renders when the
  rest does not: no `{% extends %}`, `{% static %}`, `{% url %}` or context processor. It is the
  one template besides `shell.html` that may write `<html>`, and the only one with a `<style>`
  (`pages.tests_site_rules.PageTemplateConventionsTests`).
- A dev server (`DJANGO_DEBUG=True`) shows Django's own technical 404 and 500 pages, never these
  templates. The Django tests run with DEBUG off and render them as production does, and the
  forms part of the browser checks serves the bytes that `site-tests/make_form_fixtures.py`
  draws with DEBUG off ("Site checks").
- Check a change:
  `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core config`.

## Docs and tests hygiene
````

Check: `grep -n "^### \|^## " AGENTS.md | sed -n 1,40p` lists `### Change or add a component`, `### Add or change a form page`, `## Error pages` and `## Docs and tests hygiene` in that order, and `grep -c "older pages\|until they move\|not moved onto it\|includes/ambient.html" AGENTS.md` prints `0`.

- [ ] **Step 8: README.md**

In `README.md`, replace this exact text:

```
  templates/       shell.html, layouts/, chapters/ and components/, the Journal, Docs and legal pages that use them, plus the older sign-in, device and OAuth pages
```

with:

```
  templates/       shell.html, layouts/, chapters/ and components/, the Journal, Docs and legal pages, the form pages (sign in, device, OAuth) and the error pages
```

(The job names in `README.md` are Task 7's.)

- [ ] **Step 9: Amend the spec for every ruling of this PR**

In `docs/superpowers/specs/2026-10-08-site-redesign-design.md`, make each replacement. Each old text appears exactly once (Task 7 is the only task before this one that edits the spec, in other lines); if one does not match, stop and report which. Do not touch the browser-check jobs bullet, the branch protection bullet or step 7 of section 11, or section 13's CI paragraph (Task 7 amends the job names there), and do not touch section 4.4 or the legal text.

§4.7 Forms. Replace:

````
### 4.7 Forms

This covers sign in, sign up, password reset, email verification, device code entry, approval and results, and OAuth consent.

- **Layout:** one narrow column. Mono labels above hairline inputs. One main action, with quieter secondary links.
- **Errors:** plain sentences under the field that say what happened and what to do next.
- **Device page:** rewritten in the voice pass. The current copy is "Enter the code shown in your terminal. Only enter a code from a veilfile login you ran yourself."
- **OAuth consent:** names the requesting app and says, in plain words, what the `mcp:use` scope lets it do.
````

with:

````
### 4.7 Forms

This covers sign in, sign up, password reset, email verification, checkout confirmation, device code entry, approval and results, and OAuth consent.

- **Layout:**
  - One narrow column (`layouts/form.html`, `--form-measure`). Mono labels above inputs (`components/field.html`); errors that belong to no field are `components/form_errors.html`.
  - Each input's 1px line is drawn in `--muted`, not `--hair`. `--hair` is 1.45:1 on Paper (1.72:1 on Ink), and an input has nothing but its line to mark its boundary, which WCAG 1.4.11 asks to meet 3:1.
  - One main action, with quieter secondary links. The action is a hairline box (its 1px `--hair` border stays: its text label identifies it) with a mono label inside, on `--box`, that takes the ink wash on hover and press like every control (owner's decision, 2026-10-18). The class `interactive` is what gives it that, so the action is `<button class="action interactive" type="submit">`. Two actions of equal weight, such as Approve and Deny, sit together.
  - The quieter pieces: secondary actions are `label interactive` links or buttons in `.form-links`, a status line that is not an error is `.notice` (`role="status"` where it is announced), and a checkbox or radio is a `.check` row with its box drawn in `--muted`.
  - A form page has no layout family (§4.5): it loads as an ordinary page, with no in-place changes and no `data-layout`. It sets its own `<title>` as `Name | Veilfile`, like every page of the site, and `<meta name="robots" content="noindex">` where it holds a token or a one-time state.
- **Errors** (owner's decision, 2026-10-18: no colour):
  - A plain sentence under the field that says what happened and what to do next, in full `--ink` (not `--muted`), with the field's line drawn solid `--ink` and a small mono label that reads "Check this" (uppercased by CSS).
  - The input says so to a screen reader too (`aria-invalid="true"`, tied to the sentence with `aria-describedby`), and a summary of errors that belong to no field is `role="alert"`.
- **Signed in:** a signed-in visit to sign in or sign up goes to the safe `next`, or to `/app/` (owner's decision, 2026-10-18).
- **What does not change:** every URL, form field and hidden field, status code and redirect; the query strings that ride along (`?next=`, `?plan=`, `?user_code=`), because the forms post to their own address with no `action`; and the phrases the CLIs print or match.
- **Device page:** rewritten in the voice pass, so its copy stays as it is for now. The current copy is "Enter the code shown in your terminal. Only enter a code from a veilfile login you ran yourself." Only the check mark on the result page, which none of the three fonts has, is a word or drawn with CSS.
- **OAuth consent:** names the requesting app and says, in plain words, what the `mcp:use` scope lets it do. It has a heading, keeps its two forms (Deny and Authorize), its field names and its "unverified app" framing, and is never framed (`X-Frame-Options: DENY`). A request that django-oauth-toolkit reports as an error (no valid client) shows the error page, not a consent form.
````

§4.9 Error pages. Replace:

````
### 4.9 Error pages

- **Missing or expired file link (`/a/<token>`):**
  - One shell page for both cases, with the same 404 status as today. The response must not reveal whether a token ever existed.
  - Copy: "Nothing here. If this link was shared with you, its moment may have passed."
  - Only the HTML page changes. A link that resolves still serves the file's bytes untouched, never wrapped in the shell.
- **Other errors:** 404, 403 and 429 (throttled) use the shell.
- **500** is a self-contained template with inline CSS and no context, so it still renders when something deeper is broken.
````

with:

````
### 4.9 Error pages

- **Missing or expired file link (`/a/<token>`):**
  - One shell page for both cases, with the same 404 status as today. The response must not reveal whether a token ever existed: a missing link and an expired one (and a held one, or one whose file is gone) get the same status, body and headers, `Vary` and `Cache-Control` included, and the page echoes nothing of the request or the exception.
  - Copy: "Nothing here. If this link was shared with you, its moment may have passed."
  - Only the HTML page changes. A link that resolves still serves the file's bytes untouched, never wrapped in the shell.
- **Other errors:**
  - 404, 403 and 429 (throttled) use the shell, and so does the page for a failed CSRF check (`CSRF_FAILURE_VIEW`), which shows no reason. No error page shows the exception, the request path or anything that belongs to a person. The 404 for an unknown Docs or Journal slug still marks Docs or the Journal as the current section in the menu.
  - Every 429 is the throttled page, with the `Retry-After` the throttle sets: sign in, password reset, sign up and email verification (the last two answered with a bare sentence before). It is the same bytes for a real account and a missing one, so it carries no CSRF token and nothing per person.
- **500** is a self-contained template (`500.html`) with inline CSS and no context (no request, no static manifest, no URL reversing), so it still renders when something deeper is broken.
````

§6.1 old includes. Replace:

````
- The old includes: `ambient.html`, the old `menu.html`, `cursor.html`, `page_transition.html`, and the per-template inline CSS. `cursor.html` and `page_transition.html` go with the reading pages (§11, step 4); the others stay while the forms use them (step 5) and go with the rest in step 6.
- `theme_boot.html` is replaced by the new boot script (§6.4).
````

with:

````
- The old includes: `ambient.html`, the old `menu.html`, `cursor.html`, `page_transition.html`, and the per-template inline CSS. `cursor.html` and `page_transition.html` went with the reading pages (§11, step 4); the others went with the forms (step 5), once no template included them.
- `theme_boot.html` was replaced by the new boot script (§6.4) and removed with them.
````

§6.2 form.html. Replace:

````
    form.html             auth, device, OAuth: narrow form column
````

with:

````
    form.html             auth, device, OAuth: narrow form column (--form-measure); no registry entry, so no in-place changes
````

§6.2 components. Replace:

````
  components/             one file per component, e.g. install_line.html, code_block.html, menu.html, plan.html, key_row.html
````

with:

````
  components/             one file per component, e.g. install_line.html, code_block.html, menu.html, plan.html, field.html, form_errors.html, key_row.html
````

§6.2 component rules. Replace:

````
- It has an entry in the catalog and a rendering test.
````

with:

````
- It has an entry in the catalog and a rendering test.
- A `{% csrf_token %}` inside a component included with `only` renders nothing, so a form's token is written in the page's own template, or passed in as an input.
````

§7.2 template rules. Replace:

````
  - every component has a catalog entry and a stylesheet.
````

with:

````
  - every component has a catalog entry and a stylesheet;
  - no page writes its own `<html>` or `<style>`: only `shell.html` and `500.html` are whole documents, and only `500.html` has a `<style>` element;
  - every `{# #}` comment opens and closes on one line, because Django prints a comment that spans lines on the page as text;
  - no stylesheet takes the focus outline away;
  - every form page has the shell's skip link and `<main>`, one `<h1>` inside it, a title of the form `Name | Veilfile`, `interactive` on every button and `.action` link, and none of the old pages' chrome.
````

§9 forms. Replace:

````
- **Motion:** reduced motion means the still page. Cross-fades only, never slides.
````

with:

````
- **Forms:**
  - one `<h1>` per page, inside `<main>`;
  - every input has a visible `<label for>`, and its line meets 3:1 against the page (WCAG 1.4.11);
  - an error sets `aria-invalid="true"` and is tied to its field with `aria-describedby`; a summary of errors that belong to no field is `role="alert"`;
  - `autocomplete` tokens are kept, and `autofocus` is used only where the page exists to take one input;
  - inputs are 16px or larger on phones, so iOS does not zoom in on them;
  - nothing scrolls sideways at 320px.
- **Motion:** reduced motion means the still page. Cross-fades only, never slides.
````

§4.10 forms. Replace:

````
- **Reading pages:** fill the width with a 20px margin.
````

with:

````
- **Reading pages:** fill the width with a 20px margin.
- **Forms:** inputs are 16px or larger, and a focused input scrolls to below the top fade (`scroll-padding`), clear of the mark and MENU. A form page has no layout family, so nothing about the phone sheet or its changes differs on it.
````

§10 Django tests. Replace:

````
  - missing and expired `/a/<token>` give identical responses;
````

with:

````
  - missing and expired `/a/<token>` give identical responses (status, body and headers), and a found link serves the file's bytes untouched;
  - a throttled (429) page is identical for a real account and a missing one;
  - every form page keeps its fields, hidden fields, status codes and redirects (`?next=` and `?plan=` survive sign in and sign up, the device page and OAuth consent included) and shows each of its states: field errors, non-field errors, throttled, an invalid or expired code, an unverified email;
  - the error pages as production renders them (DEBUG off): 404, 403, the CSRF failure and 500, which renders with no request and no static manifest;
````

§11 step 4. Replace:

````
stay while the forms use them (step 5) and go in step 6.
````

with:

````
stayed while the forms used them and went with the forms (step 5).
````

§11 step 5. Replace:

````
5. **Forms, device, OAuth and error pages.**
````

with:

````
5. **Forms, device, OAuth and error pages,** on one form layout, with the `site-forms` job for their browser checks. The old includes (`ambient.html`, the old `menu.html` and `theme_boot.html`) go with them, once no template includes them.
````

§11 step 6. Replace:

````
6. **The dashboard** as Django views, followed in the same PR by the removal of React, Vite, Node and the old includes.
````

with:

````
6. **The dashboard** as Django views, followed in the same PR by the removal of React, Vite and Node.
````

- [ ] **Step 10: Run the tests that read these documents**

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts pages.tests_shell drop_core.tests_fix_docs -v 1
```

Expected: OK. `pages.tests_site_parts` reads AGENTS.md, the spec and `ci.yml` for the job names (its failure message names the document that lost one: Task 7's paragraphs, which this task did not touch, so stop and report), `pages.tests_shell.StaleWordsTests` reads both documents for sentences that a change fixed, and `drop_core.tests_fix_docs` reads AGENTS.md's "Pricing changes" list and README.md's tables (which this task did not touch).

- [ ] **Step 11: The whole Django suite, and the other steps of CI's backend job**

From the repo root:

```bash
cd backend && /usr/bin/time -p env DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test
```

Expected: `OK` after about 4 minutes, then `real`, `user` and `sys` (a fresh worktree prints `UserWarning: No directory at: .../backend/staticfiles/`; ignore it). Do not hardcode the test count. Record the three timings.

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

```bash
cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && ls staticfiles/css/components/field.*.css staticfiles/css/components/form_errors.*.css staticfiles/css/pages/device.*.css staticfiles/css/pages/consent.*.css && rm -rf staticfiles
```

Expected: a `static files copied` line with no error (a stylesheet that refers to a file that does not exist fails here, as it would in the Docker build), then one hashed file for each of the four names.

```bash
cd backend && DJANGO_DEBUG=False DJANGO_ALLOWED_HOSTS=veilfile.com DROP_BASE_URL=https://veilfile.com DROP_STORAGE=s3 AWS_S3_ENDPOINT_URL=https://example.r2.cloudflarestorage.com AWS_S3_BUCKET=ci AWS_ACCESS_KEY_ID=ci AWS_SECRET_ACCESS_KEY=ci DATABASE_URL=postgres://ci:ci@localhost:5432/ci EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend EMAIL_HOST=smtp.example.com VEILFILE_SAFETY_MODE=log DJANGO_SECRET_KEY="$(../venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))')" ../venv/bin/python manage.py check --deploy --fail-level ERROR 2>&1 | grep -oE '\([a-z_0-9]+\.[WEC][0-9]+\)' | tr -d '()' | sort -u
```

Expected: exactly the accepted list of CI's "Deploy checks" step, `oauth2_provider.W004` to `oauth2_provider.W008`, `security.W005` and `security.W021`, and nothing else. (A new error-handler or `CSRF_FAILURE_VIEW` setting that Django cannot import would be named here, as `urls.E007` or `security.E...`, and would fail CI's step.)

The two script budgets (this PR adds no JavaScript to either; spec 8):

```bash
cd backend && ../venv/bin/python - <<'EOF'
import gzip
from pathlib import Path
js = Path("static/js")
files = [js / "shell.js", *sorted((js / "behaviors").glob("*.js"))]
print("shell.js + behaviors:", sum(len(gzip.compress(p.read_bytes(), 9)) for p in files), "of", 10 * 1024)
print("veil.js:", len(gzip.compress((js / "veil.js").read_bytes(), 9)), "of", 15 * 1024)
EOF
```

Expected: `shell.js + behaviors: 9112 of 10240` and a `veil.js` figure at most 15360 (11812 on `main`). If the first is not 9112, a task of this PR touched one of those four files, which it must not: stop and report.

- [ ] **Step 12: Every browser part, once, timed**

A fresh worktree has none of the checkout's environments: link `venv`, `site-tests/.venv` and `site-tests/node_modules` from the main checkout first. Start the server fresh (templates are cached with `--noreload`, and this PR changed templates), make the staff session (a fresh database needs `migrate` first) and then the form fixtures:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())" && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"; cd ..
lsof -ti tcp:8777
(cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload > ../site-tests/server.log 2>&1 &)
for attempt in $(seq 1 30); do curl -fsS http://127.0.0.1:8777/healthz >/dev/null && break; sleep 1; done; curl -fsS http://127.0.0.1:8777/healthz
```

The fixture command is `make_staff_session.py`'s applied to Task 7's script, as AGENTS.md's "Site checks" gives it. The script also draws the 404 and the 500 as production does (DEBUG off) when it runs, so the forms part needs no second server; it needs a server started after the fixtures were made (the throttled states use up limits that live in the server's memory), which is why the forms part runs last below, once. Expected: `lsof` prints nothing (no old server on the port: the health check cannot tell yours from an old one); the last command prints `{"status": "ok"}`.

Run the six parts one after the other, each with its own screenshots folder (`--part all` runs the same six in one process; the loop times each on its own):

```bash
caffeinate -ims bash -c 'for part in pages reading cloth ink phone forms; do
  echo "=== part $part"
  /usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts/$part --part $part
  echo "exit status $?"
done' > site-tests/parts.log 2>&1
```

Expected for each part: a summary line starting `0 problem(s)` (the "note(s) to review by eye" are not problems; the counts of pages or families depend on the part), `exit status 0`, and three timing lines, `real`, `user` and `sys`, in seconds. The six together take about 40 minutes: start the loop in the background and read the numbers from the log with `grep -E '^=== part|^real|^user|^sys|^exit status|problem\(s\)' site-tests/parts.log`. Write the six triples in your report, with the figures of Step 11. Stop the server afterwards:

```bash
pkill -f "manage.py runserver 127.0.0.1:8777"
```

How to read the timings against CI's 20 minutes a job: the Chromium parts (`pages`, `reading`, `cloth`, `ink`, `forms`) ran about 1.2 times slower on CI's 2-core runner than on the 8-core machine, so report DONE_WITH_CONCERNS, with all the numbers, if any of them exceeds 750 seconds `real` or 600 seconds of `user` plus `sys`: 750 s here is about 15 minutes there, with a minute of setup on top. The forms part is the new one: Task 7 measured it at about 1.5 minutes on a machine and estimates 2 to 3 on CI, so more than 3 minutes `real` here is a concern too, whatever the other parts took. `phone` runs WebKit, whose frames CI draws several times slower than a machine does (AGENTS.md, "Site checks": 5.5 minutes on CI against under one locally for the checks added to it), so its machine time understates CI: compare it with CI's own last `site-phone` run (`gh run list --workflow ci.yml --branch main --limit 1`, then `gh run view <id> --json jobs`) and with the minutes the forms part adds that CI has to find room for. A problem a part reports is not a timing concern: fix it where it is (a template, a stylesheet, a check of Task 7) and run that part again; never weaken a check or change `ci.yml`'s limits. The PR notes ask the owner for one CI run of the branch, to read each job's real duration.

- [ ] **Step 13: A final grep that nothing still names what was removed**

```bash
grep -rnE 'includes/(theme_boot|ambient|menu)\.html|tests_menu|tests_ambient' backend site-tests tools .github AGENTS.md README.md docs/llms.txt docs/API.md SPEC.md frontend/index.html frontend/src
grep -rnE "site-menu|ambient-controls|data-theme-option|veilfile-theme-boot" backend site-tests --exclude='tests_*.py'
grep -rlE 'var\(--(bg|bg-raise|bg-panel|line|text|accent|accent-hover|accent-dim|on-accent|warn|danger|mono|sans)[,)]' backend/templates backend/static
ls backend/templates
git status --short
```

Expected: nothing from the first three `grep`s (the React dashboard keeps its own `ambient`, `ambient-controls` and `veilfile-theme-boot` under `frontend/`, which the second leaves out on purpose; the test modules that name the old chrome to forbid it, `tests_forms_on_the_shell.py` and the form pages' own tests, are why the second skips every `tests_*.py`); `ls` lists `403.html`, `403_csrf.html`, `404.html`, `500.html`, the directories `blog`, `chapters`, `components`, `device`, `docs`, `layouts`, `legal`, `oauth`, `pages`, `registration`, `wellknown` (and whatever else Task 6 added), `robots.txt`, `shell.html` and `sitemap.xml`, and no `includes`; and `git status` lists exactly: the three deleted includes (staged by `git rm`), the files named in Step 14, and the untracked `.DS_Store`, `.claude/` (and, in a fresh worktree, the linked `venv`, `site-tests/.venv`, `site-tests/node_modules`; the screenshots, `parts.log` and `server.log` are ignored by git, and so is `site-tests/session.txt`). Anything else modified is something this task did not mean to change: find out why before committing, and if Task 7's fixture script left a file that git shows, it is Task 7's to ignore, not this task's to commit.

- [ ] **Step 14: Commit**

Check once more that nothing changed on `main` while this ran: `git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github AGENTS.md README.md docs frontend`. Expected: nothing (if it lists commits, stop and report instead of committing).

```bash
git add AGENTS.md README.md docs/superpowers/specs/2026-10-08-site-redesign-design.md \
  frontend/index.html frontend/src/Ambient.jsx frontend/src/styles.css \
  backend/pages/tests_site_rules.py backend/pages/tests_forms_on_the_shell.py
git status --short
```

Also `git add`, by name, any other file `git status` shows as modified because of Steps 2 to 5 (for example a stylesheet or template that a failing convention made you fix, or a test module that Step 5 made hold `longMessage = False`); never `git add -A`. The deleted includes are already staged by `git rm`.

```bash
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "The old includes are gone and nothing but the dashboard is outside the shell: recipes for form pages and error pages, the spec's rulings, and conventions that keep every page a block of the shell" -m "theme_boot.html, ambient.html and menu.html are deleted; the tests of the old chrome went earlier, with the last old page. What those tests still held that is live is kept in a form that fits the shell: no page writes its own html or style (only shell.html and 500.html are whole documents), every comment in a template closes on its line, no stylesheet removes the focus outline, and every form page has the shell's skip link, main, one heading, a title of the form Name | Veilfile, interactive on its buttons and none of the old chrome. The new check is held to longMessage = False with the other new test modules. AGENTS.md gains the form-page recipe and an error-pages section and its repo map and page set-up sentence match; the spec records the form layout, the owner's choices for the main action, errors and the signed-in visit, the device and OAuth rulings, the error pages and the conventions; the dashboard's three comments no longer name the deleted files."
```

Expected: one commit; `git status --short` then shows only the untracked `.DS_Store`, `.claude/` (and a fresh worktree's linked directories). Nothing is pushed.
