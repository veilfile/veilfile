# AGENTS.md

Rules and commands for agents (and people) working on this repo. Keep it short
and keep it true: if you change something described here, change this file.

## What this is

Veilfile (https://veilfile.com) is private, ephemeral artifact hosting for AI
agents: upload a file with an API key (REST or MCP), get an unguessable,
expiring URL. Django 6 + DRF + django-oauth-toolkit backend, every page (the
dashboard included) a Django template on one shell with no build step, a
hosted MCP server, and two CLIs. It is live in production with
real customers and live Stripe billing. Product spec: `SPEC.md`. API
reference: `docs/API.md` (agent manual: `docs/llms.txt`).

## Repo map

```
backend/config/        settings, urls, /healthz, deploy checks, log redaction
backend/drop_core/     models, plans.py, upload pipeline (services.py), storage,
                       secret scan, device login, OAuth glue, REST views, and
                       account.py: usage, keys, files and connected apps as plain
                       functions, shared by the REST views and the dashboard
backend/drop_billing/  Stripe checkout / switch / portal / status / webhook
                       (account.py holds the operations as plain functions)
backend/drop_mcp/      MCP server (POST /mcp)
backend/blog, docs, legal/   Markdown-rendered reading pages: the Journal and its
                       feed, Docs, and the legal documents (the legal text is the
                       owner's)
backend/dashboard/     the signed-in dashboard at /app/: five sections as Django views
                       on layouts/app.html (views, URLs, tests)
backend/templates/     Django templates, every page on the new shell: shell.html,
                       layouts/ (chapter, reading, form, app), chapters/ (the threshold and
                       the chapters), components/, the blog/, docs/ and legal/ pages,
                       the form pages (registration/, device/, oauth/), the dashboard
                       (dashboard/) and the error pages (404.html, 403.html,
                       403_csrf.html; 500.html stands alone)
backend/pages/         public-page registry (its reading layout also covers every
                       Journal post and Docs page) and views (threshold, chapters),
                       sitemap, component catalog, content checks, and the error
                       pages' views (errors.py)
backend/static/        the new shell's CSS, JS (js/veil.js: the cloth;
                       js/behaviors/: tabs, Copy, Come in, Local time), fonts and images
                       (no build step)
site-tests/            browser checks for pages on the new shell (CI jobs `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`)
tools/                 one-off generators run by hand: vendor_fonts.py, make_images.py
cli/                   Python `veilfile` CLI (PyPI), single file
npm/                   Node `veilfile` CLI (npm), single file
plugin/, server.json   Claude Code plugin + MCP registry manifest
docs/                  API.md, llms.txt, REGISTRIES.md, superpowers/ (design specs and build plans)
Dockerfile, render.yaml, backend/entrypoint.sh   deploy
```

`cli/` and `npm/` in this repo are the canonical sources of the published
packages. Do not publish from, or edit, any other copy.

## Run the tests

Use a Python 3.12 venv with `pip install -r backend/requirements.txt`.

```bash
# Backend. Needs DJANGO_DEBUG=True and a local DB; the runner forces local
# storage and strips STRIPE_*/AWS_*. The count changes; do not hardcode it.
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key python manage.py test
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key python manage.py makemigrations --check --dry-run

# CLIs
cd npm && node --test
cd cli && python -m unittest discover -s tests -t .
```

The site has no build step and needs no Node: Django serves the templates, stylesheets and scripts as they are
(`config.tests_fix_platform.NoBuildStepTests` keeps a build output from coming back as a template or static
directory, a Dockerfile stage or a CI step). Node is for the `npm/` CLI and for axe-core in the browser checks
(`npm ci --prefix site-tests`, "Site checks" below).

A deploy-config check runs in CI (`.github/workflows/ci.yml`, step "Deploy
checks"): `manage.py check --deploy` with a production-like environment.
Warnings other than the accepted list in that file fail the build.

CI also runs `collectstatic` with production settings, so a broken
reference inside a stylesheet fails the pull request instead of the Docker
build. `collectstatic` does not read templates: a `{% static %}` path in a
template that names no file is caught by the test
`pages.tests_site_rules` (`test_every_static_path_in_a_template_exists`),
because in production that path is a server error on every page using it.

## Site checks

The browser checks run as CI jobs, one per part of the run, each with its own 20 minutes: `site` (`--part pages`),
`site-cloth` (`--part cloth`, the cloth's families, below), `site-ink` (`--part ink`, the ink washes and the phone sheet that carries them, below) and
`site-reading` (`--part reading`, the reading pages, below), `site-phone` (`--part phone`: the phone menu's sheet in Chromium and in WebKit, the changes chosen from it and the faces they wait for, below), `site-forms` (`--part forms`: the forms and the error pages, below), `site-catalog` (`--part catalog`: the component catalog, below) and `site-dashboard` (`--part dashboard`: the signed-in dashboard, below). A new part is a new job in `ci.yml`, a name wherever the docs list the required checks (the spec's section 10 and 11, this file's "wait for" rule, `ci.yml`'s header) and an entry in `pages.tests_site_parts`, which fails until all three are done. `site` opens every page in `PAGES` (the reading pages are `site-reading`'s, the form and error pages `site-forms`', the component catalog `site-catalog`'s) in Chromium at phone
and desktop widths, in Paper and Ink, with Motion on and off. It fails on
sideways scrolling, console errors, fonts that never load or accessibility
violations. It also tries what a visitor can bring: no JavaScript,
`shell.js` failing to load, blocked storage, saved values it does not know,
reduced motion, and narrow and short screens (corners must not overlap and
every menu link must be reachable). On the chapters it also clicks through
the in-place changes (rapid clicks, Back and Forward, failed fetches), Come in,
and Copy with the clipboard denied. An in-place change cross-fades `<main>` with
a view transition where the browser has them (Chromium does), wherever the page is
scrolled; otherwise `<main>` fades out from the click and back in once the new page
is swapped in (`is-leaving`). A link chosen from the open phone sheet is the exception, in
every browser: it has no view transition (`sheet_change_problems` and, in WebKit, the phone part's
WebKit pass read every frame from the tap; `sheet_change_edge_problems` that a newer change wins, a failed fetch is an
ordinary load and Motion off is instant (it waits for the states it reads, bounded, and sleeps for none, so a slow runner gives no false finding: it passes with every frame held 120 and 250 ms; the held-face checks are released on a signal, not a time, below; while an ordinary load is held the page cannot be asked anything, so `<main>` is watched from inside the page by a sampler that reports to the check and ends itself); `sheet_change_reach_problems` that the invisible old page can be neither tapped nor tabbed to while the new one is on its way; "Choosing", below). A change that cross-fades and does not
wait on a font (`font_change_problems`) is "Faces", below. The new chapter shows from the top, or where Back left
it, so the swap scrolls the page inside the transition. Nothing may slide while it does:
the chapter's group has no animation of its own (it would travel from the old
chapter's place to the new one's), the old picture is moved back to where it was seen
by the distance `shell.js` leaves in `--slide` on the root (`::view-transition-old(chapter)`
in `base.css`), and both pictures only fade (`crossfade_scroll_problems` holds the
cross-fade at its start and compares the screen with the page a moment before, in
Chromium: Safari and Firefox are unverified here). `shell.js`
gives `<main>` its `view-transition-name` only while a transition runs (and keeps
it for the newest one when a newer change skips the running one): a name that
stayed would make `<main>` a stacking context and a backdrop root all the time.
Only `<main>` is in the transition: the root is not captured (`:root { view-transition-name: none }`)
and `::view-transition` takes no pointer events, so the cloth and the corners stay live and the
pointer still reaches the page (Chromium hits the transition's pictures instead for as long as the
root is captured, which dropped a click on another chapter and made a hovered control's wash regrow; the moment the page is paused is the exception that "The pointer in a change", below, explains).
A browser that does not honour the name and captures the root all the same still has the old rules
(`::view-transition-old(root)` not shown, `new(root)` without its fade: they match nothing otherwise),
and a check names the root again to see them work; Safari and Firefox are unverified.
Both ways (`VIEW_MODES`, or a context with no `startViewTransition`) are tried for: a failed
fetch or swap leaving `<main>` faded, two chapters clicked at once (the overtaken change leaves no
history entry), Back and Forward from a scrolled page, and the reading font arriving late. The
rest of the change checks run with view transitions only. They time a change from inside the
page (`CHANGE_PROBE`).
Begin's five code blocks (`components/code_block.html`) have `code_block_problems`: at 320, 375 and 1280 the Copy button lies in its row above the code, never over it or out of its block, and the page does not scroll sideways; each Copy puts exactly its block's text (as a reader sees it) on the clipboard, says Copied, speaks in its own block's status and no other's, and moves nothing; with the clipboard refused it selects the code; without JavaScript Copy is not shown.

The phone menu's checks are the `phone` part (`run_phone`, CI's `site-phone`): the sheet's own opening and closing in Chromium (`phone_sheet_problems`,
`sheet_scrolled_close_problems`), the changes chosen from it (`sheet_change_problems`, `sheet_change_edge_problems`, `sheet_change_reach_problems`), the faces
they wait for (`font_change_problems`, which runs `font_timer_problems` too), and `webkit_sheet_problems`, which repeats the sheet, the changes and the faces in
WebKit and is the only check that runs there, so `site-phone` is the only job that installs WebKit. They are a job of their own because `site` took 17.7 of its 20
minutes with them in it (the checks this branch added cost about 5.5 minutes on CI and under one locally, the CI figure measured before CI moved to hosted runners: WebKit's frames are far slower in CI's software
compositing). The sheet's own checks are in this part and not in `pages` to keep both engines' reading of the sheet and its changes in the job that is re-read
when the sheet is touched, and because `pages` stays the longer job without them. `pages` runs the phone menu only as each page has it (`phone_menu_problems`, the
corners, the chapters' in-place changes). A helper that both parts call is not named `..._problems` (`chapter_head_findings`), which `pages.tests_site_parts` counts as a check
and allows in one part only. Each check of the pages and phone parts prints how long it took (`timed`), as the cloth's families do.

The reading pages (the Journal, its newest post, the Docs, the quickstart and Terms) are in `READING_PAGES` in
`site-tests/run_site_checks.py`, not in `PAGES`, and `site-reading` checks them: they are long, and in `PAGES` they would
take the pages part past what it can spare. The newest post is not written down: the part takes it from the Journal's first
entry, so a new post needs no edit. Each page opens at 375 and 1280 in Paper and Ink with Motion on (Motion changes only how
a change fades, which the change checks watch); the corners are looked at in eight scroll positions, not every quarter of a
screen, and the menu as the pages part looks at it. Then come the checks a long column calls for. A long word in a heading,
a long URL, a long code line in a code block and a wide table are added to the column at 1280, 375 and 320: the page must not
scroll sideways, a word and a URL wrap, and code and tables scroll inside their own boxes. The column is no wider than 65
characters. The menu starts closed on every page and never pins open: that is checked at 1280 and 375, and again after each
change in place. A Docs page or legal document with two or more `##` headings has a section list, each of whose links lands
its heading at the top, and at 375 below the top fade. Every fenced code block in the reading column is drawn by
`components/code_block.html` (`render_reading`): `reading_code_problems` checks that each has its Copy button and an id of its
own, and that Copy puts exactly the code as a reader sees it on the clipboard and says Copied in its own block's status (the
quickstart's `DELETE` example must be there). A page with no JavaScript still works. The changes among the pages (the Journal,
a post and the posts either side of it, the Docs, the quickstart and the Docs page after it, Terms) all happen in place, with
the head swapped as a direct load has it (title, canonical address, structured data), the menu closed and focus on the heading;
Back and Forward put the pages back, and Terms to About is an ordinary load. A check that finds nothing to measure says so: as
a problem where the page must have the thing, as a note in `review-by-eye.txt` where it may rightly lack it (a Journal with one
post has no neighbours to link). A check that the pages part also runs on another part's own pages (the reading, forms and catalog parts') is named in `SHARED_PAGE_CHECKS`. The
part loads no cloth, so it follows the cloth parts in `--part all` (the phone, forms and catalog parts, which load none either, come after it, in the order of the jobs in `ci.yml`: catalog is the last); it takes about 85 s on a laptop.

The forms and the error pages (sign in and sign up, password reset, email verification, checkout confirm, the device flow, OAuth consent, the 404, the 403s, the 429s and the 500) are `FORM_STATES` in `site-tests/run_site_checks.py`, one `FormState` for each state a page has, and `site-forms` (`--part forms`, `run_forms`) checks them, in Chromium only: Playwright's WebKit cannot show what differs on an iPhone, so a pass there would repeat Chromium's reading at twice the cost. The words of each page are its Django tests'; this part checks what only a browser sees. A new form page or state is a `FormState`: its address (`{device[consent]}` and the like come from the fixtures), whose session it is (`staff` is the staff login of `--session-file`; the others are the fixtures' `sessions`), the driver in `FORM_DRIVERS` that brings it to the state (it fills the form and submits it as a visitor does, or uses a throttle's budget up), its status and what it must say about itself (`expect`). Each state is opened in Paper at 1280, 375 and 320 px, with axe at 1280 and 375. Three are opened in Ink as well (`theme="dark"`: the sign-in page with an error, the device approval page and the 404, named `...-ink`; `forms_theme_problems` reads that the page is in the appearance its state names), so the colours that differ most from Paper's (`--hair`, `--muted`, the field's line, an error's mark) are looked at on a form page, an approval and an error page; the cloth is stubbed there as in every state, and the cloth's own Ink (the washes, the contrast over the real cloth) is the cloth and ink parts', which open the chapters and not these pages: the other form and error pages are checked in Paper only. `forms_page_problems` reads one `<h1>` inside `<main>`, a visible label and the old autocomplete token on every field, every error tied to its field (`aria-invalid`, `aria-describedby` naming the help before the error), the ink wash on every button (`.interactive`), a blank page saying nothing is wrong and, on a phone, text of 16 px or more in a field; `forms_bare_problems` that the 500 asks for no file. A page with fields gets the phone's on-screen keyboard (`forms_focus_problems`: each field is put partly under the top fade and focused, and must land clear of it and above the keyboard; the input is what is measured, because Chromium's `focus()` does not scroll an input that is already in view, whatever stands above it), Enter (`forms_enter_problems`: it submits by the form's main action) and autofill (`forms_autofill_problems`: a rule on each of `:autofill` and `:-webkit-autofill` keeps the text `--ink` under a transition that never ends, and one paints the field paper with Motion off). `forms_error_focus_problems` that, after a submit that came back with errors (a `FormState` that expects `field` or `alert`), focus is where the server put it, read at 1280, 375 and 320 px before a screenshot, axe or a key has touched the page: on the first field marked `aria-invalid`, and with no field in error (the failed sign-in, in Paper and in Ink, and the refused device approval) on the `role="alert"` summary, which draws the site's focus outline (`:focus-visible` matches it and its outline is not none) and lies in the window, clear of the top fade and with its outline inside the width. `forms_links_problems` that no line of a `.form-links` paragraph ends with its separator (it reads each character's line box with `Range.getClientRects`, at 1280, 375 and 320, and the sign-in, sign-up and reset throttle pages, which offer Log in and Forgot password? with a separator, are `separated` in their `FormState` and fail when they have none). `forms_mark_problems` that the approved device login's check mark, drawn in CSS (`.device-mark`), is one aria-hidden box with its two borders painting ink, in the window at each width. `forms_round_trip_problems` follows `?next=` through sign in to the device page and OAuth consent, and `forms_slow_frames_problems` repeats the keyboard checks with every frame held 120 ms. Nothing waits for a fixed time: a check waits for the state it reads (`wait_for_state`), and `pages.tests_site_parts` fails if `run_forms` reaches a `wait_for_timeout` or a `sleep`. The part takes about 1.5 minutes on a laptop and 2 to 3 on CI's runner, measured before CI moved to hosted runners (the laptop's time measured, CI's from the reading part's ratio of the two; the run prints the real figures).

`site-tests/make_form_fixtures.py` makes what the part needs, as `make_staff_session.py` makes the staff login, and writes `site-tests/form-fixtures.json` (git ignores it): verified users and their sessions (one with the longest values the models take), a user whose verification link went a moment ago, a user to reset, device codes (two of them are used up by the run: the one approved and the one denied), OAuth clients registered through `/o/register/`, the links of the emails, and the 404 and the 500. Those two are drawn there because the dev server runs with DEBUG on, which answers an unknown address and an uncaught error with Django's technical pages and never calls the site's handlers: the script draws them with DEBUG off, by the whole request stack, and the checks serve those bytes (`drive_baked`). Run it again after a template changes (those two are drawn when it runs), and restart the dev server and run it again before each run of the part: the throttled states use up limits that live in the server's memory (sign-in, code lookups, password reset), and the codes and sessions are used up. A state that answers 429 where it should not says so. To be looked at on a real iPhone and a real Android phone when these pages change, since no check here sees them: iOS zooming into a field under 16 px (the check reads the size), the on-screen keyboard, the browser's real autofill (the check reads the rules), the order of the Tab stops, the label above a field that takes focus where it stands (the check measures the input), a screen reader reading the errors (the failed sign-in's summary is read as focus reaches it, and a field in error with its sentence) and that Safari and Firefox put focus on that summary and draw the outline round it (the check reads Chromium's).

The component catalog (`/app/components/`, the staff page that shows every component in each of its states) is in `CATALOG_PAGES` in `site-tests/run_site_checks.py`, not in `PAGES`, and `site-catalog` (`--part catalog`, `run_catalog`) checks it, in Chromium only, as the staff login of `--session-file` (it needs no form fixtures). It is a part of its own for time: once the form components were in the catalog, each with several states, its page in every width, appearance and Motion took 159 s on CI and its edge cases 371 s, measured before CI moved to hosted runners (73 s and 158 s before), and the `site` job took 19.8 of its 20 minutes (run 39694271834); every other page moved by 10 to 20 %, which is a runner's noise. The part does for the catalog what the pages part does for each page of `PAGES`, with the same functions: `page_states` (the page in every width, appearance and Motion, with the checks of each: the page, the bottom row, the menu and the phone menu, the corners, the grain and the switches) and `page_edge_problems` (no JavaScript, `shell.js` failing, throwing or late, the corners wherever the page is scrolled and on either side of 64rem, narrow and short screens, blocked storage, saved values it does not know, reduced motion). The pages part runs `edge_case_problems`, which is `page_edge_problems` and then the threshold's and the chapters' own (Come in, the in-place changes, the fades), which the catalog has no use for. `pages.tests_site_parts.CatalogPartTests` holds the split: `run_catalog` loops over `CATALOG_PAGES` and `run_pages` over `PAGES`; each reaches every check of both helpers; the catalog part reaches none of the threshold's and the chapters' and none of the pages part's whole-site checks (`grain_scale_problems`, `code_block_problems`, `corner_type_problems`); and the checks the two share are in `SHARED_PAGE_CHECKS`. The cloth and ink parts still open the catalog (`cloth_presence_problems` with the cloth running, and `SHEET_COVER_PAGES` for the open phone sheet) through `page_named`, which looks in `PAGES` and `CATALOG_PAGES`. A new component or state costs this job and not the pages job. On a laptop the part takes about 3 minutes (52 s for the page in its states and 135 s for its edge cases) and the pages part about 6.5 (the two together took about 9.7 before the split); the runs print each step's time (`pages:` and `catalog:` lines).

The dashboard (`/app/` and the pages that hang off it) is `DASHBOARD_PAGES` in `site-tests/run_site_checks.py`, its five sections, not `PAGES`, and `site-dashboard` (`--part dashboard`, `run_dashboard`) checks it, in Chromium, with `dashboard_webkit_problems` in WebKit (the times and the Sign out button in the phone's sheet are the engine's: `Date` and `Intl`, and iPhones run WebKit; the job installs both, and the check would otherwise skip itself and pass by not running). It signs in as the users of `site-tests/make_dashboard_fixtures.py`, not as the staff login, which has no workspace to show. The script writes `site-tests/dashboard-fixtures.json` (git ignores it): `site-dash-owner` (a Team workspace with four keys, one with the longest name a key takes and one revoked, 53 files so that the list has a second page, among them the longest file name, an expired file, a held one, one with secret flags and sizes in gigabytes, and three connected apps, one with the longest name; the plan's key cap leaves room for the two keys the key flow makes), `site-dash-over` (a Free workspace over its limits on keys, storage, link lifetime and uploads, with a stored unpaid subscription and a customer id) and `site-dash-sub` (a Team subscriber whose last payment failed, with a switch to Scale waiting for payment in its session), their sessions, and 14 spare sessions of the owner, one for each Sign out. The users have no password. The script resets what a run changed (the key flow adds two keys, the revoke flows take a key, a file and an app, Sign out ends sessions), so run it again before each run of the part. It refuses to run unless DEBUG is on and the database is SQLite or local, with no `STRIPE_` variable set and the local file storage, because the checks press Upgrade, Manage billing and Revoke: nothing in the part contacts Stripe or a bucket. Billing is seen with no Stripe key (each button comes back to Billing with the customer sentence `Billing isn't available right now. Try again later.` and a six-character reference, with none of Stripe's or the operator's words on the page, and `refuse_foreign` refuses and counts every request to another host) and with the states the fixtures store; the Stripe flows are Django tests.

The part runs `page_states` and `page_edge_problems` on the five sections (the pages part's checks of a page), then what only the dashboard has, each timed. `dashboard_section_problems` loads each section directly (200, the noindex header and tag, none of the public head, the title, one `h1`, the menu's mark, what the fixtures put on the page, and the downgraded user's Overview with its meters over their limits). `dashboard_change_problems` (`dashboard_walk_findings`, as `reading_change_problems` is for the reading pages) changes among the sections in place: the head as a direct load has it, the menu shut and marking the section, focus on the `h1` once the cross-fade is over, `?cursor=` on Files, Back and Forward; Revoke on Keys is an ordinary load whose Keep comes back; and with the session cookie gone the next change is an ordinary load that reaches sign in with `?next=`. `dashboard_sign_out_problems`: the menu's Sign out is one POST form with a token, the last item of the second group, set in the links' type; in the open menu at 1280, 375, 320 and 667x375 it can be brought into view, lies clear of the display controls, takes a click and covers no text; from every section it signs out with scripts at 1280, in the phone's sheet and without scripts at 375, lands on sign in with 200 and leaves the dashboard out of reach; Tab meets the five sections, Docs, Journal and Site and then the button, which draws the focus ring; a GET of `/accounts/logout/` signs nobody out. `dashboard_error_page_problems`: the error pages of a signed-in visitor (a key or an app of nobody's, an unknown plan, the link-miss page, a POST without its token) have the ordinary menu and no Sign out and no token, and the pages under `/app/` say noindex; `forms_sign_out_problems` looks for the same on every state of the forms part, the throttled pages and the 403s included. `dashboard_key_problems`: a new key is the answer to the POST (200, no-store), written once in the page and nowhere else the browser keeps it, with the warning focused and showing its ring after a key press, and Copy puts exactly the key on the clipboard; a reload says no key was made and makes no second one; Back from the page after it shows no key and makes none; the keys are counted before and after. `dashboard_revoke_problems`: a key, a file and a connected app: the row's link opens a confirmation page that changes nothing (Keep comes back with the row as it was), whose button revokes and comes back with the one line, read once. `dashboard_rows_problems`: the tables of rows stack at 375, 320 and 1280 (the column is 624px there) and are tables at 1440, with the 255-character key and app names, the 512-character file name, sizes in gigabytes and meters over 100 %: nothing scrolls sideways or is clipped and each value is led by its column's label. `dashboard_time_problems`: times are rewritten into the zone the browser reports, with its short name (`America/Chicago`, `Asia/Kolkata`, `UTC`), again after a change in place, and stay the UTC the server wrote with scripts off. `dashboard_billing_problems`, `dashboard_slow_frames_problems` (the changes and the keyboard Sign out again with every frame held 120 ms) and `dashboard_webkit_problems`. Every wait is for a state, and `pages.tests_site_parts.DashboardPartTests` fails if a `dashboard_` or `dash_` function sleeps. The dev server runs with DEBUG on, which answers an address no route matches, and an `Http404` that a view raises, with Django's technical page and never calls the site's handlers (the reason the forms part bakes its 404): the part sees only the 404s that views return, and the Django tests have the rest. The part takes about 8 minutes on a laptop (7 minutes 38 seconds measured; the Files page is the long one, 70 s in its states and 156 s in its edge cases, and each of the other four sections 30 to 50 s); `run_dashboard` prints each step's time.

The cloth has checks of its own. Chromium runs with SwiftShader (`GL_ARGS`), so
WebGL exists on any machine, and the run stops at once if it does not (it also prints the
pointer and hover media the site's own pages report, which the hover fills answer to, and
stops for a coarse pointer where no touch is emulated). Every
other check gets a stub `veil.js` (`VEIL_STUB`), so it runs as without the
cloth. The cloth checks load the real one, drive its time with a virtual clock
and read the canvas back, and the ink washes it was asked to draw: they check
when the cloth runs and when it must not,
the still page when WebGL is missing or fails, Motion switched on and off, and
off while the cloth is still loading, contrast (small text in `--muted` at 4.5:1
over every pixel of the whole canvas, and over each place it sits, which axe cannot
see; and the other side of it, a floor on how flat the calm may leave the cloth, the spread
of its luma), the ink wash (on phones too, and on a touchscreen a press shows the flat
fill; below a device's own first step (0.8 to 1.2, or from 0.65 on a phone) the wash is softer, because the shader widens its edge and takes
its grain down by how many times coarser the canvas is than that step (`u_soft`), so a phone at 0.28 is less rough at its edge than at its first step,
and a healthy device at its first step asks the shader for `u_soft` 1 and draws the crisp ragged wash, nothing changed), the page fading out along the top of a phone
(over the cloth, and over the still grain), the install box's backdrop blur
(the grain's spread with the blur against without it, so a blur that sees nothing fails), the film grain (read with the cloth told to draw only the paper: drifting about 1.5 cycles a second, not a fresh value each frame, bell-shaped, zero-mean, weaker in Ink; `cloth_grain_problems`), the resolution (it follows the screen: 0.4 of the device pixel ratio, kept between 0.8, or 0.65 on a phone, and 1.2, then
three quarters of the step above down to 0.35, or 0.28 on a phone: the checks' Chromium has a pixel ratio of 1 unless a context sets
`device_scale_factor`, so the presence, canvas-box, adaptive and soft-edge cases set 2 and 3 for the 0.8 and 1.2 starts), a device that cannot keep up
(on the short ladder of a phone at ratio 1 and the long one at ratio 3; frames that a smaller canvas shortens keep the lower scale, and a step that brings them under the limit is kept however little it shortened them; frames held at 30 a second
whatever the size are a display's cap, as iOS Low Power Mode makes them, and the step that did not help is undone and remembered for the tab as
`veilfile-veil-capped`; a step that is kept is remembered too, as `veilfile-veil-step`, so the tab's next page starts there and only goes on down, and a 3x phone
does not walk the ladder from 1.2 on every page, in a walk that finished and in one the next page cut short; the checks choose the frame length from the canvas's width), a hidden tab and a lost context. A cloth that stops drawing
while a check waits for a frame fails that family with "the cloth stopped
drawing" rather than hanging the run. Each family prints how long it took. The families are in two parts, two CI jobs that each stay well under their 20 minutes: `CLOTH_FAMILIES` (`--part cloth`, `site-cloth`: presence, failure, lifecycle, input, appearance, change, contrast, adaptive, top fade, backdrop and grain; about 545 s on CI) and `INK_FAMILIES` (`--part ink`, `site-ink`: `ink_problems` and the phone sheet's `cloth_menu_sheet_problems`, `cloth_sheet_ink_problems` and `cloth_sheet_motion_problems`; about 460 s; both measured before CI moved to hosted runners); a new family goes at the end of the one it belongs to, and `webgl_precondition` runs at the start of each part. Take
full-page screenshots with `full_page_screenshot()`: in Chromium with
SwiftShader a screenshot taller than 8,192 px takes WebGL away from the rest of
the run, so taller pages are cut at 8,000 px. The cloth checks count display
frames: `run_frames()` lets that many frames pass, and anything else that waits
on animation frames shares the frame's time. They draw only the frames they read
back; take a picture of the cloth with `cloth_screenshot()`, which draws for
that moment.

The corners' type is friendly to read: labels 13px on phones and 14px from 686px wide (`--label-size`, the owner's sizes), tracking .14em (chosen by eye between .2em and .12em), the mark 24px (6px cells) and 28px from 64rem (7px cells); `corner_type_problems` reads them as drawn from 375 to 1440 px. `--corner-reach` (16 label widths), the menu column's `--reach`, `--fade-top` and the corner checks all follow the label size.
The open menu's column starts 1em under MENU: the current page's link holds an ink wash whose ragged rim reaches up to 1.4 times its box, and with 14px labels it lightened MENU's line under 4.5:1 in Ink for some of the seeds a wash is drawn with. The contrast family therefore draws it at 1024 wide with five fixed seeds (`WASH_SEEDS`, `Math.random` overridden: the ones that reached furthest, three from a sweep of 12 seeds and two, `Math.random` 0.1625 and 0.2375, from one of 80 seeds 0.0125 apart) as well as a random one.
Words are measured over the band their letters fill, not the line they sit in (`WORD_RECTS`, `WORD_BAND_PAD`): a 14px label's line is 18 px, its capitals fill the 10 in the middle, and the four pixels above them and below are empty. The two seeds above reach the next link's line (3.76:1 and 3.93:1 in Ink, 4.46:1 in Paper over the whole line, which a random seed did in about one run in forty) by a pixel into those empty four above its capitals, never to a letter (every row of that line below its first 1.5 px reads 5.99:1 or better with either seed), and a wash of 3,200 seeds 0.0003 apart, drawn with `veil.js`'s own `wash` at its widest (INK_AGE), reaches the next link's line by 1.1 px at most (3.6% of them reach it at all), 2.8 px short of its letters, and MENU's line 0.2 px, 3.6 px short of its letters. `words_band_problems` proves the band against the page's pixels (the open menu's words photographed without the cloth: the rows that hold a letter lie inside the band, and the band is no more than 2.5 px taller than they are, so a band that slips off the letters or falls back to the whole line fails), and a wash that does reach the letters still fails the contrast family: with the links' gap .8em where it is .95em, seed 0.1625 reads 3.76:1 again. More room between the links, or a wash that reaches less, is the owner's to choose if the rim should also stay clear of the next link's empty line; the check does not need it.
The menu starts closed on every page at every width and only MENU opens it (spec 4.4, after earendil.com, whose menu is never pinned open): `shell.js` has no pin and opens nothing on load. Escape (returning focus), a click elsewhere and an in-place change (`veilfile:swapped`) close it, the phone's sheet also closes at once at `veilfile:leaving` (a change that cross-fades), and a link chosen from it closes it by its driver at the tap (`closeSheet`, "Choosing", below). A check that needs a menu link opens the menu as a visitor does: `set_menu`, `menu_link`, `try_click` (it presses MENU first for a `#shell-menu a`) and, for the held wash of the current page's link, `hold_menu` (it also leaves MENU's own wash time to fade). That wash shows only while the menu is open; when the menu shuts it is let go like any other and fades over 1.8 s where the link was (`WASH_HISTORY_AT` reads it at the link's place, since the link has no box then). Checks: `menu_rest_problems` (closed at rest on every page at 1280 and 375, MENU opens it with the current page marked, a click elsewhere closes it at 1280), `chapter_change_problems` (a link in the open menu leaves it shut) and `pages.tests_shell.MenuStartsClosedTests` (the script has no pin and opens only for MENU).
The harness lets go of what it could mislead on: a Playwright task nobody awaits (the "Task exception was never retrieved" notice, usually a route handler still in flight when its context closed) is a problem of the run, `playwright: ...`, with the frame of `run_site_checks.py` that started it (`UNRETRIEVED`, `note_unretrieved`: only that notice is taken, and not when it is a `TargetClosedError` from a task no frame of `run_site_checks.py` started (Playwright's own route-pattern update racing a close, which nothing of ours can await); every other report of the loop, such as a destroyed pending task, still goes to its default handler on stderr; a Playwright without the private `_loop` skips the hook and prints a line saying so), and every context unroutes its routes before it closes (`shut_after_routes`, Playwright's own `unroute_all(behavior="ignoreErrors")` advice). `menu_reach_problems` allows `REACH_SLACK` (a quarter of a pixel, just above the 0.22 px measured) for a scroll that lands on whole pixels: with none, 14 of the 42 pages and small viewports fail by 0.203 px (Journal's bottom at 375.203 in a viewport 375 high), and over viewports 300 to 516 high at pixel ratios 1 and 2 the most was 0.219 px.
The motion switch bottom right reads FX · on or FX · off, so that the bottom row reads PAPER and FX · ON; its accessible name is "FX: motion" (it holds the visible label, WCAG 2.5.3, and says what it does), with `aria-pressed` carrying on or off and the "· on" hidden from the name (`motion_name_problems`, through the browser's own accessible name). Motion stays the setting's name everywhere else.
At every scroll position nothing in the page may show under a corner label or
through the open menu (`under_corner_problems` runs at 1024x768 with the menu shut and again with it open, where the column, part of the top-right corner's box, is at its tightest wide width). From 64rem up the page runs between the corners
(`--corner-reach` in `tokens.css`); below that the bottom corners wait at the
end of the page and the page fades out along the top (a mask on `<main>`, a band as
tall as the corners' rows and then a short fade, which `shell.js` keeps at the top of
the screen as the page scrolls by setting `--fade-y` to the scroll offset; the footer
cannot reach the band and is not masked; nothing is laid over the page).
Nothing inside `<main>` may use a `z-index` above 2: the corners are at 3.
A `backdrop-filter` inside `<main>` (the install box's slight blur) sees the cloth only
while `<main>` is not a backdrop root: it is one during a change's cross-fade (it is named
for the transition) and, below 64rem, always (the top fade's mask), where such a filter
sees only what `<main>` paints (the second mask layer the cloth-drawn phone sheet adds, further down, is on top of a mask that is already there). Keep anything else on `<main>` that makes a backdrop root
(a filter, an opacity under 1, a mask, a `will-change` for them) to the same moments;
`cloth_backdrop_problems` checks the install box at 1280.
What no check here can see, to be looked at on real devices when these parts change. (1) A view transition
pauses the page's drawing while its update callback runs, so the callback must not wait: `shell.js` loads the faces of the layout (`data-faces` on
`<body>`) when the menu opens and when a change begins, and waits for them (at most `FONT_WAIT_MS`, 400 ms, from the page's arrival) before it swaps the
content or starts the transition, while the page still draws, and the swap's own wait for the fonts then finds them loaded (`font_change_problems` and `font_timer_problems` hold
Cormorant italic 400 in Chromium and WebKit and time the callback and read the face's state (an engine with no view transitions has no callback to time: the probe leaves it as it is and the check says so in `review-by-eye.txt`, not as a problem); `font_wait_problems` holds the reading font, late
and never arriving). A held face is never let go after a fixed time: a change begins its wait when the page has come and the old content has gone (from the open sheet, when its close has ended), which on a slow runner is later than any fixed hold from the tap (a hold of 250 ms ended before the wait began, the face had arrived, and `font_timer_problems` counted no timer on CI), so the check lets the face go when the wait has begun (`release_when_waited_for`: the timer of 400 ms that `shell.js` makes only when a face is loading, which `FONT_TIMER_PROBE` sees, or the change being over), and `font_timer_problems` has a case with the page held 400 ms (`PAGE_LATE_MS`) that fails a fixed hold. If the bound itself runs out before a released face arrives, as it may on a runner so slow that the face does not come in 400 ms, the face is not judged and a line in `review-by-eye.txt` says so; a change that swapped with the face loading and no timer run out waited for less than the bound, and that is a problem. What no headless run shows is how long a face takes on a phone's connection, and a character outside the Latin file (the one file per
face that `data-faces` loads) still loads on demand, inside the bounded wait of the swap. (2) The top fade is driven from the main thread: during a fast
fling the mask can trail the page by a frame or two, so text shows for a moment in the corner rows under the mark
and MENU. Every check runs in Chromium (Playwright's iPhone profile is Chromium; Firefox is not installed here), and the phone menu sheet's also in Playwright's WebKit as an iPhone 13 (`webkit_sheet_problems`, as the iPhone 13's viewport, touch and user agent but at a pixel ratio of 1, since a full-screen masked layer at 3x in software compositing is what made CI's frames 100 to 150 ms apart and nothing under test depends on the density: it fades in and out over frames, also when its first frame is slow and when every frame is (`sheet_slow_first_frame_problems`, `sheet_slow_frames_problems`, below), a tap on its left half and Escape close it; skipped with a line saying so where WebKit is not installed; Playwright's WebKit is not Safari, and the blink an iPhone showed shows in it only when a frame of the fade is slow, which the held frames reproduce; the sheet the cloth draws, with its second mask layer (`mask-composite`, `color-mix()` in a gradient stop, a registered `--sheet`), was looked at in Playwright's WebKit by screenshot, not by a check, and not on an iPhone), so the mask, `@property --fade-y` and masking very tall elements (the long Docs, Journal and legal pages, now
on the shell) are unverified in iOS Safari and Android Chrome. (3) The cross-fade's rules that keep a
scrolled page from sliding (`--slide`, the group without an animation) are standard view-transition CSS, checked
in Chromium only.
The still grain, with Motion off, is drawn at screen-pixel size: its 128 px tile is shown at 128 divided by the pixel ratio in CSS px (`--grain-size` in `base.css`, by resolution, which `html`'s `background-size` uses), with a rule for each ratio screens really have (1.25, 1.5, 1.75, 2, 2.625, 2.75, 3, 4: `GRAIN_RATIOS`; the tile is a whole 128 device pixels at each, which a tile sized for the nearest whole ratio is not at 1.25 or 2.75: 106.67 and 117.33 device px, resampled, not repeating on the pixel grid), and `grain_scale_problems` reads the computed size at those ratios and the screenshot's repeat (`GRAIN_REPEAT`: a row must equal itself 128 device px along, and `GRAIN_ROW_SPREAD`: it must vary at all, since a flat row repeats perfectly; the grain measures 1.08 in Ink at 3x to 1.79, the limit is 0.5; the screenshot's own width and channel count are read, because a clip of 787.5 device px comes out 787 or 788 wide). The page's ground (`html`) and the phone menu's sheet (`.menu__list::before`, in `base.css` because a `url()` in a custom property is resolved from the stylesheet that uses it) are the only things that draw it, at the same size.
The phone menu (below 64rem) opens like earendil.com's: a full-screen sheet of paper, masked `linear-gradient(to right, transparent, solid 50%)` (nothing at the left edge, solid from the middle), so the cloth, and the page's own text, show across the left half and the right-aligned links lie on a solid sheet. While the cloth runs it draws the sheet (`u_sheet` in `veil.js`: the appearance's paper mixed over the cloth by `u_sheet * clamp(2x, 0, 1)`, before the washes and the film grain, so both lie on it; `u_sheet` is the sheet's one value, `--sheet`, below, for the frame it draws, and is 0 once Motion goes off) and the CSS surface (`.js .menu__list::before`, the page's ground: paper with the still grain) is not drawn: `html[data-veil="on"]:not([data-motion="off"])` is the whole of "the cloth is drawing" (veil.js sets `data-veil` after its first frame and deletes it when it stops, and `data-motion` is "off" the moment it is told to), and every rule of that state is keyed on it, at the end of `base.css`. `<main>` is masked out of the right half by the mirror of the paper's share (a second mask layer, intersected with the top fade: alpha 1 at the left edge to `1 - --sheet` from the middle on), and the display control bottom right (`.shell-corner--bottom-right`, which the solid surface used to cover) fades with it (`opacity: calc(1 - var(--sheet))`). MENU and the links are above all of it. Anything new that lies above the canvas on the right half needs the same, or it shows through the paper. Where the cloth is not drawing (Motion off, reduced motion's still page, no WebGL, a slow device's still page, a lost context) the CSS surface is the sheet and nothing is inked; the page behind is inert and does not scroll (`overflow: hidden` on `html` while the sheet is displayed).
The sheet's fade is ONE value, `--sheet` (0 shut, 1 open), that the sheet's `driver` (`shell.js`, made in `setUpMenu`; `<main>`'s fade is a second one, "Choosing", below) advances once for every frame that is drawn, by that frame's share of the fade (the frame's time over 240 ms opening, 180 ms closing) and never by more than a quarter of it. The sheet's opacity (`max(.01, var(--sheet))`) and the move of its groups of links (`translateY(calc((1 - var(--sheet)) * -6px))`, menu.css, the sheet in the corner only), the paper the cloth draws and `<main>`'s mask and the display control's fade (base.css) all read it, so the links, the paper and the page's fade-out under the right half move together, and nothing about the sheet is a CSS transition, animation or keyframe or is timed from a clock. It is a registered `<number>` that does not inherit, written by `shell.js` on each element that reads it (the sheet, its groups of links, and `covered`: `<main>` and the corners): written on the root and inherited, it makes the browser recalculate every element's style on every frame of the fade (8.5 ms a write at 6,000 elements in Chromium on a Mac, 0.15 ms written on the readers; the long pages that move onto the shell have thousands), and `--sheet: inherit` down the chain is cheap in Chromium but WebKit never updates it (a child keeps 0 while its parent moves; checked in Playwright's WebKit). Why: a fade that is a function of time can finish between two drawn frames when one comes late, as CI's WebKit showed (a lab job there opened the sheet in Playwright's WebKit as an iPhone 13: at a pixel ratio of 3 every frame took about 250 ms and every CSS variant tried, `will-change` on the sheet or on it and its groups, a `translateZ(0)` layer, no surface, no mask, no grain, no scroll lock, no group move, blinked from .01 to .99 in one frame, while a fade stepped from script by at most a quarter per frame showed .01, .58, .88, .98; nothing about the sheet is expensive, the clock is the cause), and an iPhone drawing the cloth has late frames too. A step per drawn frame cannot be skipped: a late frame only makes the step bigger, up to a quarter, so the fade shows at least two frames between 0 and 1 however far apart the frames are. The curves are `v = 1 - q ** k`, `q` the share of the fade still to go: k = 5 opening, within .0114 of `cubic-bezier(.22, 1, .36, 1)` at any time of the fade, and k = 1.7 closing, within .010 of `ease-in` (`pages.tests_shell.PhoneSheetDriverTests` measures both from the exponents in the script); `q` is recovered from `v` at each step, so a turn-around (a close that interrupts the opening, an opening that interrupts the close) goes on from the value the sheet has, where one easing for the pair would jump (their values differ at the same share). The first step of a fade has no time behind it, so the first frame displays the sheet at `--sheet` 0, opacity .01 and the links 6px up, and the fade goes on from the next frame, however long that takes to come. Why .01 and not 0: WebKit does not paint a layer at opacity 0, so a sheet held at 0 gets its first full-screen paint (grain, mask) in the first frame in which its opacity is above 0, and that frame is slow (CI's WebKit drew four frames 16 ms apart at opacity 0, then one of 194 ms); measured in Playwright's WebKit, a layer at exactly 0 is not painted and one at 1e-5 is painted with the frame that displays it, so .01 is far from that edge and under three levels of an 8-bit channel, which no eye sees, for the one frame it lasts. `will-change: opacity` was tried and measured to add nothing; do not add it without a frame list that wants it. `is-open` is the sheet's `display` (`.js .menu__list:not(.is-open) { display: none }`): `shell.js` sets it with the first step and takes it away when `--sheet` is back at 0, so the sheet is displayed for the whole of its close, when MENU already says shut and the sheet takes no pointer (`.js .menu__toggle[aria-expanded="false"] + .menu__list`, scoped to `.js`: the markup renders the toggle with `aria-expanded="false"` as the list's sibling, so unscoped the rule would leave every menu link ignoring taps wherever the script does not run, with no JavaScript and when `shell.js` fails to load or throws, which take `.js` away; `menu_link_hit_problems` hit-tests the first link at 375 px in those three); the held wash and the pointer follow `aria-expanded`, not `is-open`. The sheet itself fades and the groups of links inside it make the move: a transform on the sheet would make it the containing block of its `position: fixed` surface, and a sheet scrolled when it closes (a phone turned sideways) would carry the surface away with the links for the whole fade (`sheet_scrolled_close_problems`). The cloth is handed the driver's step (`let sheetStep` in `shell.js`, `start({ ..., sheet: sheetStep })`; `const share = leaving || !SHEETED.matches ? 0 : sheet(time)` in `veil.js`, taken first thing in the frame; from 64rem up the open menu is a column: the driver does not draw it, it jumps to the end there (`v = to`, so the value is 1 while the menu is open), and the cloth's own guard, `SHEETED`, is what keeps the paper off at those widths, so the cloth is given 0 there): the step is made once for a frame's time, whoever asks first, so it does not matter whether the cloth's frame callback or the driver's own comes first in a frame, and asking for the step means the paper is the value of its own frame; reading `--sheet` from CSS there would give the paper the last frame's value, a whole slow frame behind the links, whenever the cloth's callback came first. A menu shown inside the page (the catalog's samples) has no driver and is shown as it is. `shell.js` plus the behaviours stay under 10 KB gzipped (8,066 of 10,240 B once the long comments moved out to "How shell.js works", below, from 10,218; the driver cost 252 B net over the two-step `is-shown` code it replaced, 9,966 B; the change from the sheet and the faces took it to 8,789 B), so its explanation lives here, in menu.css and in the spec, not in the script. Its items do not fade in one by one (from 64rem up the column's items still do).
A change chosen from the sheet closes it like any close (`closeSheet`: the normal .18 s, `is-open` and the scroll lock going when it ends, "Choosing", below), and one that cross-fades takes the sheet away at once, if it is open (`veilfile:leaving`, which `shell.js` fires just before it names `<main>` for the view transition, takes `--sheet` to 0 and `is-open` away in the same task), as the cross-fade draws the old chapter above everything. Motion off and reduced motion (`instant()`) take the value to its end at once when MENU is pressed or shut; a Motion switched off in the middle of a fade lets that fade finish (it lasts at most 240 ms). The fade is judged frame by frame, in Chromium and in WebKit: `sheet_fade_findings` (the phone part's `sheet_fade_problems`; the ink part's `cloth_sheet_motion_problems` reads the same on a cloth page, so the shared helpers are not named `..._problems`, which `pages.tests_site_parts` counts as checks) reads `--sheet`, the sheet's opacity, its links' place, how many CSS transitions or animations run on the sheet and its first group of links (none may: a transition on the sheet would run during a fade, where the values alone cannot tell it from the driver) and (on a cloth page) the shader's share, the control's opacity and `<main>`'s reading at every frame (`FADE_PROBE`) and replays them against the driver written again in Python (`SheetModel`, given the frames' own times): the lengths, the curves, the first step with no time behind it, a reversal starting from the value it has and the end at exactly 1 and 0 must agree within `SHEET_MODEL_TOLERANCE` (.001), and no frame spacing excuses a fade that shows fewer than two frames between 0 and 1 (`fade_blink_problem`; the note for "frames too far apart" is gone for it: the driver makes it unnecessary; `unsettled_problem` alone still turns a sheet that had not come to rest by `FADE_BOUND_MS` into a note when the frames were further apart than `FADE_FRAME_LIMIT_MS`). An interruption's second click is made at the first frame that shows the sheet between a quarter and three quarters of the way through its fade, not after a fixed delay, and with no such frame it is a note; an opening that interrupts the close also turns back without a snap or a jump (`sheet_reversal_findings`: the opacity read just after the click equals the one just before it; no step between two frames falls or rises faster than the closing's or the opening's curve can go in the time between them, `REVERSAL_FALL_PER_MS` and `REVERSAL_RISE_PER_MS`; and the lowest opacity comes within `REVERSAL_TURN_FRAMES`, one frame, of the click, whose own frame has no time behind it). The slow-frame checks are the ones that hold the owner's blink and do not depend on the runner's speed, in Chromium and in WebKit: `sheet_slow_first_frame_problems` holds one frame of the opening 400 ms (the frame that displays the sheet, by a `ResizeObserver` that busy-waits between layout and paint, and the first in which its computed opacity is above 0, by a `requestAnimationFrame` loop that busy-waits in the frame where it first leaves 0) and wants two frames between 0 and 1 still to come, and `sheet_slow_frames_problems` holds EVERY frame of the opening and of the closing 120 ms (`SLOW_EVERY_FRAME_MS`, a model of CI's WebKit at a pixel ratio of 3 and of a phone drawing the cloth) and wants the same. A fade timed by CSS fails that last one in both engines (the two-step `is-shown` transitions drew `[(2, .01), (8, .01), (144, .01), (264, .01), (384, .96), (505, 1)]` opening and `[(1, 1), (11, 1), (136, .46), (258, none)]` closing in WebKit, (ms, opacity): one frame between 0 and 1, and Chromium's were as bad); the driver draws `[(2, .01), (8, .01), (149, .76), (270, .97), (390, 1)]` and `[(1, 1), (8, 1), (129, .91), (251, .69), (371, .39), (491, none)]`, in both.
A tap on the sheet's own surface (`event.target === list` in a click listener on the sheet itself in `shell.js`: the visible left half, the padding and the gaps between the groups of links, never a link) closes it, as a tap on earendil.com's backdrop does; WebKit fires no click for a tap on an element it does not take for clickable (a listener on the document does not count), so the open sheet also has `cursor: pointer` (iOS Safari reads it as clickable) and the listener (Playwright's WebKit wants the listener). The sheet is inked like the rest of the page while the cloth draws it (`inkable` in `veil.js` has no exception for it): the current page's link holds its wash, a hovered, focused or tapped link takes one, and when the sheet closes the washes are let go and linger on the cloth behind it (the first closes with a tap on the left half or Escape; a change chosen from the sheet closes it by its driver and the wash fades where the link was). Checks: `phone_sheet_problems` (no transition and no animation runs on the sheet or its groups, read in the click's own task and at rest and while it closes; the first frame is `--sheet` 0, opacity .01 and the links 6px up; it settles shown, opaque and in place with `--sheet` at 1 and goes with `--sheet` back at 0, displayed and taking no pointer while it closes; instant, with nothing running, with Motion off or reduced motion; no item animations, the column from 64rem up unchanged, no page scroll, a tap at 40px closes the sheet and a tap on a link does not by itself; then `sheet_fade_problems`, `sheet_slow_first_frame_problems` and `sheet_slow_frames_problems` above, in Chromium), `cloth_menu_sheet_problems` (the sheet as the cloth draws it, in both appearances: `u_sheet` 1, `<main>`'s `--sheet` 1 and two mask layers, the display control's opacity 0 and the CSS surface not drawn; the strip between the middle and the links reads `--paper` and the grain's spread on the canvas, and the frame with the cloth magnified twelve times equals the frame with none from just past the middle to the right edge within `SHEET_LEAK` 0.0003 (a sheet at 99% reads 0.007, a ramp that ends at 55% of the width 0.0018); the left half is the cloth blended with the paper by the ramp within `SHEET_BLEND_TOLERANCE`; and the screen, with `<main>` painted black in Paper and white in Ink behind its words so that any share left of it shows, equals the canvas blended with that colour by what the mask leaves of `<main>` at seven places across within `SHEET_LUMA_TOLERANCE` 0.006 (a mask that leaves 1% of `<main>` in the right half measures 0.012 off), the display control's box included; then the fallbacks, Motion off and a lost context with the sheet open, where the CSS surface is the page's ground, `<main>` keeps its top fade alone (one mask layer; `--sheet` is 1 all the same, the sheet's links read it), the control stays, nothing is washed and the flat fill answers; then `sheet_cover_problems` on every page of `PAGES` and on the threshold after Come in, in both appearances, because with the cloth drawing the sheet `<main>`'s mask alone keeps a page's content out of the right half and `/about/` has none of the install box, the code blocks or the plans: `<main>` and everything in it, boxes and pseudo-elements included, painted black or white, the right half of the screen equals the screen with `<main>` hidden, pixel for pixel (`SHEET_COVER_MEAN` 0.0005 on average, `SHEET_COVER_BLOCK` 0.01 over the worst 16 by 16 pixels: 0.0000 now, a box that shows through 0.05 or more), at the top of the page and, where it scrolls, at its end, where the control bottom right waits, which is also where it is checked to have faded), `cloth_sheet_ink_problems` (the held wash on the current link, a hovered link's, a focused link's and a tap's, each a full or just fading wash with no flat fill, in the canvas by `SHEET_INK_SEEN` 0.03 of luma and on the screen; after a tap on the left half the share falls over 0.18 s, the held wash lingers on the cloth at about 0.7 of its strength half a second later, read anywhere from the link's place to 6px above it (the groups' rise as the sheet fades out; a wash follows its link only in the frames that draw it, so where it ends depends on how often frames come) and is gone by 2.5 s; a tap on a link that changes the page in place closes the sheet like any close, with the old content fading out with it at the sheet's own value and the new content fading in, and lets the wash fade, not cut), `cloth_sheet_motion_problems` (on the cloth's virtual clock, 60 frames a second, run while the probe reads: the opening and the closing held to Earendil's own curves, the share within `SHEET_EASE_ERROR` .012 and a frame's slack of `cubic-bezier(.22, 1, .36, 1)` in 240 ms and `ease-in` in 180 ms, and five frames or more between 0 and 1; then `sheet_fade_problems` with `cloth=True`: the four fades replayed against the driver at every frame, and in the same frame the shader's share EQUAL to the links' `--sheet` (the paper in lockstep with the links and the page), `<main>` reading the same, the control at 1 minus it and the links at their place by it), the contrast family (`contrast_problems` also opens the phone sheet in both appearances, once at random and for each of `WASH_SEEDS`: MENU and the links in `--muted` and the current link in `--ink` hold 4.5:1 over paper, grain and wash: 6.27:1 at the least in Paper and 7.04:1 in Ink for the first, 9.06:1 and 9.31:1 for the second, with every seed; the labels are 13px there and the links 45px apart, so no seed's wash reaches a neighbour's letters), `phone_menu_problems` (no strong leak through the solid half, the motion switch included: `content_shows_under` reports only a difference over 40 levels of 255, so a surface 85% opaque passes it; that the half is solid is `cloth_menu_sheet_problems`') and the grain check (the sheet's surface is the grain's tile, at the page's size).
The `site` job keeps screenshots of every state as a build artifact named
`site-screenshots` (`site-reading` keeps its own as `site-reading-screenshots`, `site-forms` its own as `site-forms-screenshots`, `site-catalog` its own as `site-catalog-screenshots`, `site-dashboard` its own as `site-dashboard-screenshots`, `site-phone` the few its checks take as `site-phone-screenshots`, and `site-cloth` and `site-ink` the few their families take as `site-cloth-screenshots` and `site-ink-screenshots`), with `review-by-eye.txt`: what axe could not decide (usually contrast over a fill or the grain), and what a check found nothing to measure, for a person
to look at in the screenshots. Pages are listed in `PAGES` in
`site-tests/run_site_checks.py`; add a page there when it moves onto the
shell, except a reading page (the Journal, the Docs, the legal documents), which goes in `READING_PAGES`, a form or error page, which is a `FormState` in `FORM_STATES`, the component catalog, which is in `CATALOG_PAGES`, and a section of the dashboard, which is in `DASHBOARD_PAGES`. To run it locally (`--part pages`, `--part cloth`, `--part ink`, `--part reading`, `--part phone`, `--part forms`, `--part catalog` or `--part dashboard` runs one job's checks, `--part all`, the
default, every part, one after the other; a test, `pages.tests_site_parts`, holds `all` to the parts so that no check falls between them or runs in two, and a check that the pages part and the reading, forms, catalog or dashboard part run on their own pages must be named in `SHARED_PAGE_CHECKS`; `webgl_precondition` runs at the start of each part):

```bash
python3 -m venv site-tests/.venv && site-tests/.venv/bin/pip install -r site-tests/requirements.txt
site-tests/.venv/bin/python -m playwright install chromium webkit   # webkit is optional: the checks that use it are skipped without it
npm ci --prefix site-tests
# terminal 1, from backend/:
DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput
DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
# terminal 2, from backend/, then the repo root:
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
# the forms part also needs its fixtures (make them again, on a freshly started server, before each run of the part):
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"
# the dashboard part needs its own (make them again before each run of the part: it adds keys, revokes a file and ends sessions):
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_dashboard_fixtures.py').read())"
cd .. && site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
```

`make_staff_session.py` creates a staff login, so it refuses to run unless
`DJANGO_DEBUG=True` and the database is SQLite or on localhost. If it
refuses, unset `DATABASE_URL`; never point it at production. `make_form_fixtures.py` plants users with a known password, and refuses in the same way. `make_dashboard_fixtures.py` plants users that only their sessions reach, and refuses in the same way, and also while a `STRIPE_` variable is set or `DROP_STORAGE` is not `local`.

When running a CLI, or anything that might open a URL, put no-op `open` and
`xdg-open` shims first on `PATH` and set `BROWSER=true`: `veilfile login`
opens a real browser tab. Never point a CLI or test at https://veilfile.com.

## How shell.js works

The long explanations that used to be comments in `backend/static/js/shell.js` and `js/behaviors/*.js` live here. They are
downloaded by every visitor because there is no build step, and `shell.js` plus the behaviours must stay under 10 KB
gzipped (`pages.tests_static.ScriptBudgetTests`), so the code keeps a one-line pointer where each comment was, named
below by its bold heading. The sheet driver's reasons and measurements are in "Site checks" above and in the spec (4.4).
Comment out of date? Change it here, in the same commit as the code.

**Page set-up.** The inline boot script in `shell.html` has already set `data-theme` and `data-motion` on `<html>`. Stored values
stay `light | dark | system` and `on | off`, because visitors' saved choices are already stored in those words. The boot script is the only
reader of `localStorage` `veilfile-theme` and `veilfile-motion` on a page (`500.html` reads the theme alone) and `shell.js` the only writer, on a
click: nothing writes a default, so a visitor who has never touched a switch still follows the system's colours and its reduced-motion setting
(`pages.tests_shell.StoredChoicesTests`). The visible word of the
motion switch is FX and its name "FX: motion" (`controls.html`); `aria-pressed` carries the state, the "· on" is hidden from
the name.

**Sheet driver.** `--sheet`, one value stepped once per drawn frame by at most a quarter of the fade, `v = 1 - q ** k`: see the
phone menu's paragraphs under "Site checks" for why and for the checks.

**Backdrop tap.** A click outside the menu closes it. On a phone the sheet lies inside the nav, so a tap on its own surface (the
visible left half, the padding, the gaps between the groups of links; never a link) is the backdrop tap that closes
earendil.com's menu. That one is heard on the sheet itself: a WebKit tap on an element with no click listener of its own (the
document's does not count) fires no click at all, so the sheet would stay open. A browser takes such a sheet for the tapped element and washes all of it
with its tap highlight (blue in Chromium, grey on an iPhone) for the frame or two after every tap, over a sheet that is closing, so `html` in `base.css` sets
`-webkit-tap-highlight-color: transparent` (inherited: the sheet, its links and the controls; their own press feedback, the fill or the ink, is the
feedback; `pages.tests_shell.TapHighlightTests`).

**Leaving.** A change in place that cross-fades is about to start (`veilfile:leaving`): the phone's sheet closes first, since the
view transition draws the old chapter above everything else, which the sheet would otherwise be covering. The menu starts closed
on every page, as earendil.com's does, and only MENU opens it (spec 4.4). A link in it that changes the page in place leaves
it closed, as the next page of that site arrives with its menu closed (`veilfile:swapped`).

**Choosing.** A link in the open phone sheet (`#shell-menu.is-open` below 64rem) changes the page in place with no view transition: the
sheet starts its normal close at the tap (`closeSheet`, the driver's .18 s), `<main>` fades out with it (`fade`, a second `driver` with the
sheet's closing length and curve, started at the same tap, so its value is the sheet's in every frame: `<main>` never reappears at full
strength), and the page is fetched meanwhile. When the close has ended, which takes `is-open` and so the scroll lock away, and the page has
come, `<main>`'s content is swapped while `<main>` is at opacity 0 (if MENU was pressed again during the close the sheet is open again, and the close's promise, which a turn-round resolves at once, says nothing of it: the swap waits for `whenShut()`, a promise the sheet's driver resolves when `--sheet` is back at 0 and not on its way to 1, with `<main>` held out of reach and the scroll lock on for as long as the visitor has the sheet open, and a newer change ends the wait; `sheet_change_edge_problems` presses MENU three frames into the close), and then `fade` brings it in (.17 s on the sheet's opening curve, .35 s
in all with the close: the `--change-fade` token's length). Both are advanced per drawn frame by at most a quarter, as the sheet is, so a late
frame cannot skip them (the checks hold every frame 120 ms). From the tap until the swap `<main>` is inert (`holdMain`, which sets `away`; the menu's `updateInert` reads it, so opening and shutting MENU in between does not give the old page back): it is invisible for as long as the fetch and
the font wait take (up to `FETCH_TIMEOUT_MS`), and a second tap, common when nothing seems to happen, would otherwise land on a link nobody can see, snap the old page back to full strength and cross-fade to a page the visitor did not choose; Tab would also go into it. The swap, a failed fetch and a change not from the sheet take it back. `sheet_change_reach_problems` holds Docs' page of links.
While `fade` has `<main>` it writes `style.opacity` and
`style.transition = "none"` on it (a CSS transition on its opacity would lag every step) and takes both away at rest. Why no view
transition: the old way dropped the sheet at once, put the old page back at full strength and only then cross-faded (the owner's "blink out
and in"; the dead time after the tap was the fetch, with the sheet frozen); a view transition pauses the page's drawing while its callback
waits; the scroll lock's release, in the task that started the transition, moves iOS toolbars and `dvh`, and a viewport resize cuts a running
transition short in both engines; and it needs a picture of `<main>`, which for the tallest reading page (Terms, about 5,400 px at 375 wide,
16,200 device px at 3x) a phone may not take. A newer change overtakes it as before: one not from the sheet takes `<main>` back to full
strength at once (`fade.go(1, true)`) and skips a cross-fade still running; one from the sheet goes on from where `<main>` is. A failed fetch
or swap fades `<main>` back in while the ordinary load starts. Motion off and reduced motion (`instant()`) change at once as before. Links
in the page, Back and Forward keep the view-transition cross-fade (`veilfile:leaving` closes a sheet that is open at once). The tall pages were
measured at 375 x 3 in Playwright's Chromium (software GL and Metal) and WebKit, Terms to Privacy and a page stretched to 14,000 px: the
transition runs (not skipped), nothing is clipped (the end of the page shows in the old picture at every scroll depth), and Chromium's
picture is exactly as soft as a short page's (its sharpness against the live page is 0.85 to 0.90 at every height, 0.83 for About) while
WebKit's is sharp, so the in-page cross-fade between reading pages stays. A phone's texture limit is not what was measured: look at one when
the reading pages grow.

**Faces.** A change must not wait on a font. `<body data-faces>` (`shell.html`, for a page with a layout) names the faces any page of the layout may set
text in, as font shorthands joined with `|` (Cormorant Garamond italic 300 and 400 and Newsreader; the reading layout adds Newsreader italic, which its emphasis, `cite`
and quotations are set in, a 147 KB file a chapter never shows and never changes in place to; the app layout, the dashboard's, names italic 300 and Newsreader
only, since the dashboard sets no text in italic 400, a 39 KB file it would fetch for nothing; IBM Plex Mono is preloaded on every page), and `loadFaces`
asks the browser for each (`document.fonts.load`, which loads the latin file: the one whose range holds a space). It runs when the menu opens, the way to
every chapter, and at the start of every change, in parallel with the fetch. Before the swap (and before the view transition, whose update callback pauses
the page's drawing for as long as it waits) the change waits for them if they are still loading, bounded by one timer of `FONT_WAIT_MS` that is made only
then; the swap's own wait for `document.fonts.ready` (a face the new content needs that `data-faces` does not name, such as a character outside Latin) is made only when
`document.fonts.status` is "loading" once the new content is laid out, and shares that timer, so a change that finds every face loaded makes no timer (`font_timer_problems`
counts the timers of `FONT_WAIT_MS` in Chromium and WebKit, from the column at 1280 and the sheet at 375: none with every face loaded first, one with Cormorant italic 400 held). The wait was inside the callback before: in Chromium the page, the cloth included, stood still for up to 400 ms on a change from About to How (About
never loads Cormorant italic 400, which How, Begin, Promises and Plans set their headings in), and WebKit did not wait at all, so the new headings showed in a
fallback face for about 270 ms. The pages preload their own first screen too (`preload_heading` in `shell.html`, italic 400): the chapters with headings, the Journal's list
and a reading page with a `##` section (an `h2`) do; About, the threshold, the Docs index and a post, a Docs page or a legal document whose words have no `##`
section (`report-abuse.md` has none) do not, because those three templates keep the layout's link only `{% if "<h2" in ... %}` (`{{ block.super }}`). What
`pages.tests_faces` pins: the chapters' lists are written by hand (`EXPECTED`, from the faces a browser loaded for each page at 375 and 1280, which no test reads
again), the reading pages' are derived from whether the rendered page has an `<h2`, and the three templates' condition is tried on words with and without a section.

**Cloth loader.** `veil.js` is fetched only when Motion is on, WebGL works and the system has not forced its own colours, once the
page has loaded, and again whenever Motion comes back on. If it cannot run, Motion goes off for this visit only: `data-motion`
changes and `data-veil-off` says why; the visitor's saved choice is never touched. A device that is too slow is remembered for
the tab (`sessionStorage`), so the visit's other pages do not start the cloth only to give it up again; any other reason is
tried again on the next page.

**Top fade.** Narrow screens (below 64rem): along the top the page fades out as it scrolls up toward the mark and MENU, so it never
reaches them (`base.css`, spec 4.1). A mask moves with the element it is on, so its gradient is moved back as the page scrolls:
`--fade-y` is where the top of the screen falls on `<main>`, which starts at the top of the document, so it is the scroll offset.
Set once a frame at most, as the page scrolls, and as an in-place change swaps the content in.

**Changing in place.** A link to another page of the same layout swaps in that page's `<main>` and its marked `<head>` elements, so
the shell stays put. Anything unexpected (an error, a redirect, another layout) becomes an ordinary page load. The latest click
wins; an overtaken change leaves the newer one's fade alone, and the newest falls back to an ordinary load without sitting
faded out while that load runs. Motion is off when `data-motion` is "off", or when the system asks for reduced motion and nothing
is set (the visitor's saved "on" wins): the same rule as `base.css` and as `instant()` in `come-in.js`. How long the page waits
for a fade out is the `--change-fade` token in `tokens.css` (".35s"), read by `fadeMs()` so changing it there changes it here;
an unreadable value gives 350, the token's original. A browser with view transitions cross-fades the content (`base.css`),
wherever the page is scrolled. Anything else fades the old content out from the click, while the page is on its way, and the new
in once it has arrived; the old content has gone when its fade has ended (the fade starts at the next frame, which a slow
device is late with), and the timer only keeps a page whose animations do not run (a tab in the background) from waiting for
ever. A view transition calls the swap a frame or more after the click that asked for it, and a newer click in that gap must
find nothing swapped in, pushed to the history or titled by the change it overtook. The old picture stays where it was seen, the
new content comes in where the page is scrolled now: nothing slides between them (`base.css`, `::view-transition-old`); `--slide`
is how far apart they are. A page may not have loaded the font the new chapter reads in (the threshold does not preload it, About
never loads Cormorant italic 400): the chapter stays hidden until it is there, or fallback text flashes as it fades in, which is
"Faces" (the wait is before the swap, bounded by `FONT_WAIT_MS`), and a newer change wins. A link chosen from the open phone
sheet takes the other path, "Choosing". After the swap the menu follows the page first (on a phone this closes the sheet and ends the page's inertness),
then focus moves: an inert heading cannot take it. The page is fetched before the transition, never inside it: a view
transition pauses the page's drawing until the swap is done.

**The pointer in a change.** The cross-fade takes no pointer from the page (the root is not captured and `::view-transition` takes no pointer events,
"Site checks"), but the moment before it does. From the old page being taken to the animation's first frame the page is paused, and there Chromium's
`::view-transition-*` pseudo-elements read `pointer-events: auto` whatever the page says (with `!important` too) and a hit test finds the document element. A
hover update of the browser's own that lands in that moment is a `pointerout` from the control the pointer rests on to `<html>` and, at the first frame, a
`pointerover` back, with the pointer not having moved; `veil.js` let the wash go at the first and began another, growing from nothing, at the second. Chromium
makes such an update about 11 ms after a layout (11 to 16 ms in the logs), so a layout in the task that begins the change, followed by a frame that is late, is
enough. `loadFaces()` (34a7b6a, "Faces") asks for the layout's faces at the start of every change, and on the first change from a page that has not loaded one
(About's, for Cormorant italic 400) the face's arrival lays the text out just before `startViewTransition`. With every frame held 33 ms the pointer was taken from
the footer's switch in 19 of 20 changes from About at that commit and in 21 of 30 at the merge of PR 5, in none of 30, 20 and 20 at the commits before it, and in
none of 20 with the faces loaded first (also at 80 ms); at normal speed in none (0 of 60 runs of `ink_change_problems`, which is why CI failed it once, run
39700492384). Opening the menu loads the faces, so a click in the menu never meets the layout; Back and Forward and a key on a link do, with the pointer resting on
a corner control. Nothing in the page stops the update: no stylesheet beats the pointer-events, waiting for a frame before `startViewTransition` did nothing,
and waiting for the font set to settle lowered the count (16 of 30 at 33 ms, 0 of 30 at 80) and did not remove it, because the update follows any layout less
than 11 ms old (a layout flushed in the same task takes the pointer in 20 of 20 changes at normal speed). So `veil.js` takes it: a `pointerover` whose
`relatedTarget` is the document element resumes the wash still fading under that control (`resume`, as it does for a held link) from the strength it has, instead
of beginning another, and a pointer that has gone is not given a wash back. `ink_change_problems` makes the take certain (`LAYOUT_AT_CHANGE`, a layout flushed in
the task that calls `startViewTransition`; `TAKEN_PROBE` counts the `pointerout` to the document element, and a Chromium that does not take the pointer there
leaves a note in `review-by-eye.txt`) and holds three changes: with nothing else (the wash and the page as before), with the pointer taken and given back (the same
wash, never one begun again), and with the pointer gone while the update callback is held (the wash is let go and gone 3.3 s later, not held for ever). Safari's
and Firefox's pauses are unverified.

**View transition name.** `<main>` carries `view-transition-name: chapter` only while it cross-fades (`base.css`): a name that stayed
would make it a stacking context and a backdrop root at all times, so no backdrop blur inside it would see the cloth. A newer
change skips the running transition, whose end must not take the name from under the newer one.

**Scroll.** Where the page showing is scrolled is kept in its history entry's state. Only for an entry whose scroll is ours
(`scrollRestoration` "manual"); the browser restores the others. Not while the entry is already another page's. It is saved as
the page goes away: `pagehide` is enough to leave it for another page but not for a reload (Chromium drops a state set then,
and keeps one set in `beforeunload`); also a moment after each scroll, for a page that is discarded without either. An entry
whose scroll is ours is not restored by the browser when its page loads again (a reload, or Back across a full load), so
`restoreScroll()` puts the remembered position back; the page is parsed by now, and `scrollTo` lays it out. Leaving a page for
another remembers where it was scrolled, so Back can put it there: the browser must not restore scroll itself (the page still
showing would jump while it fades), so this entry and the ones pushed after it are put right by hand, remembered when the
link is followed and as the page goes away, put back by `popstate` or, after a reload or a full load, by `restoreScroll()`.

**Behaviours.** Come in (spec 4.2): the line and COME IN fade out, then the install line fades in alone, already in its centred
place; nothing moves while it can be seen, and focus moves to Copy. Without the script Come in is an ordinary link to
`/begin/`. Its fade length is the `--fade` token in `tokens.css` (".7s"), read as in `shell.js`; an unreadable value gives 700.
Copy (spec 4.2): copies the command without its prompt, says "Copied" for 1.8 s and tells screen readers (in the status of its
install line or its code block; a Copy button in neither, such as each row of the dashboard's Files list, speaks in the page's
first `data-copy-status`, so that page has exactly one), then shows the tab's line after Copy. When the page may not write to the clipboard (an
insecure origin, a refused permission), it selects the command and says so instead. The status region is emptied first and set
a moment later: set to the text it already holds (a second Copy within 1.8 s), it might not be read out again. Tabs (spec 9: a
proper tablist): a click or the arrow, Home and End keys choose a tab and show its panel. Delegated from the document, so
tabs swapped in by an in-place change work too. Local time (spec 4.8): a `<time datetime data-local>` that the server wrote in UTC
(the `utc_time` filter, `dashboard/templatetags/dashboard.py`) is written again in the visitor's own zone, `Intl.DateTimeFormat` with the
date, the time to the minute and the zone's short name (`Oct 22, 2026, 9:58 AM EDT`), on load and after each change in place (`veilfile:swapped`). With no script the UTC text stays, and a time
that does not parse keeps it; it sets no `title`, which a phone and a keyboard cannot read.

## CI must be green before merging to main

Render deploys `main` automatically once the CI checks on that commit pass
(auto-deploy trigger "after CI checks pass"). There is no staging environment.
Work on
a branch, open a pull request, wait for the `backend`, `npm-cli` and
`python-cli` checks and for every browser-check job (`site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`; the list is in `ci.yml`), and merge only when they all pass. Never push to `main` to
"see if it works".

Merging also waits for the owner's go-ahead. Muse's account merges a pull request, with a merge commit, only after CI is green and the owner has said so in chat. The same goes for pushing a release tag. Commit as Veilfile <support@veilfile.com>, dated in UTC (TZ=UTC git commit), with no attribution lines; the names check refuses any name on the owner's list.

## Where CI runs

CI runs on GitHub-hosted runners: every job in `ci.yml` has `runs-on: ubuntu-24.04` (x64), a fresh machine for each job, and a pull request's jobs run side by side.
`pages.tests_site_parts` (`RunnerTests`) holds the rules below.

- **Which jobs run:** a push to `main` runs `backend`, `npm-cli`, `python-cli` and `names`; a pull request runs those and the `site*` jobs, which a push does not (their
  checks ran on the pull request's merge commit). The `changes` job skips the `site*` jobs when every file the pull request changes is one that cannot
  reach a rendered page: `cli/`, `npm/`, `plugin/`, the repo-root `docs/` (not `backend/docs/`), markdown files at the repo root, `backend/**/tests*.py`,
  `backend/**/tests/**` and `.github/workflows/release.yml`. Any other path, `ci.yml` and `backend/drop_core/oauth.py` included, or any doubt (no list of files, a
  git error) runs them. A skipped job counts as passed for a required check. A new kind of file that only tests or the CLIs read goes on that list in `ci.yml`.
- **Jobs run side by side:** the jobs of a pull request (`backend`, two `npm-cli`, two `python-cli`, `changes`, `names` and eight `site*`) run at the same time, up to GitHub's
  limit of concurrent jobs, so a pull request takes about as long as its slowest job. Each job has its own `timeout-minutes` (20 for the browser jobs).
- **Times:** on the first hosted run (pull request #1, 24 October 2026) the pull request took about 14 minutes from start to finish, with `site-dashboard` at 12 minutes, `site-cloth` at 10, `site-ink` at 9, `backend`, `site` and `site-catalog` at 8 each (`backend` took 14 on the first push to `main`), `site-phone`, `site-reading` and `site-forms` at 3 each, and `changes`, `names`, `npm-cli` and `python-cli` at under 15 seconds each.
- **Required checks:** a ruleset on `main` requires all fifteen by name, so a renamed job must be renamed in the ruleset too (the owner's account changes rulesets).
- **Names:** the `names` job fails when any tracked text file holds a name on the owner's private list, the repository secret `FORBIDDEN_NAMES`, which only the owner can read or set. It prints file paths, never a matched line. If it fails, remove the name from the files it lists. If it says the secret is not set, tell the owner.
- **Ports:** each `site*` job serves on its `SITE_PORT` (job-level `env`), used by the server, the health check and `--base-url`: `site` 8801,
  `site-cloth` 8802, `site-ink` 8803, `site-reading` 8804, `site-phone` 8805, `site-forms` 8806, `site-catalog` 8807 and `site-dashboard` 8808. A new `site*` job takes
  the next port. The job saves the server's PID (`SERVER_PID`, through `GITHUB_ENV`), refuses to start if something already answers on its port, and ends with a
  step that has `if: always()` and kills the server.
- **Setup:** Python comes from `actions/setup-python` with its pip cache. The `site*` jobs run `playwright install --with-deps` and cache the browsers in
  `~/.cache/ms-playwright`, keyed on the Playwright version in `site-tests/requirements.txt` and on the browsers installed (`-webkit` for `site-phone` and
  `site-dashboard` only).
- **What stays between jobs:** nothing. Each job starts on a fresh machine; only the pip, npm and browser caches are restored, and none of them can change a result.
- **The environment:** the `site*` jobs blank `DATABASE_URL` in their `Start the app` step, because `migrate` runs before the fixture scripts check the database.
- **Outside contributors:** the repository's Actions setting makes a workflow run from a pull request by an outside contributor wait for approval, and such a run never gets secrets or a write token. The repository takes no outside pull requests (README, "License and contributions").

## Deploy facts

- Container entrypoint (`backend/entrypoint.sh`): `migrate --noinput` at every
  boot (skipped when `SKIP_MIGRATE=1`, for use with a pre-deploy
  command), `createcachetable`, a background daily housekeeping loop
  (`sweep_expired`, `clearsessions`, `cleartokens`, `prune_funnel_events`,
  `prune_email_ips`, `purge_expired_cache`), then gunicorn (gthread, 1 worker x 4 threads by default).
  A migration therefore runs against production the moment a deploy starts:
  keep migrations additive and safe on a live Postgres table (new columns
  need defaults; no destructive changes without the owner).
- Health check: `GET /healthz` (200 after `SELECT 1`, else 503). The host's
  Health Check Path is `/healthz`; a failing check restarts the instance.
- `STRIPE_SECRET_KEY` is a restricted live key (`rk_live_...`). It needs Write
  on Customers, Checkout Sessions, Customer portal, Subscriptions and
  Invoices. Every webhook re-reads the customer's subscriptions from Stripe,
  so a missing permission turns each webhook into a 500 (Stripe retries for
  3 days) and blocks checkout for returning customers. New Stripe calls in
  code may need new key permissions: tell the owner before deploying them.
- Production refuses to start without `DATABASE_URL` (no SQLite unless
  `DJANGO_ALLOW_SQLITE=True`). Storage is Cloudflare R2 (`DROP_STORAGE=s3`);
  Render's disk is ephemeral. Env vars are documented in the README table; a
  test fails if the code reads a variable the README omits.
- Never contact production (veilfile.com, Stripe, Cloudflare, Render, PyPI/npm
  publish) from automation without the owner's explicit say-so.

## Private operations (not in this repository)

The production recipes, the pricing reasoning and the Stripe launch runbook are not in this
repository. They are in the private ops repository, `veilfile/ops`: how to run a command in
production, the reasoning behind the plans' prices and caps, and the step-by-step Stripe setup.
Muse's account can read it. Read it before you touch production or Stripe, and never copy an
identifier, key or account name from it into this repository.

## Releasing the CLIs

`.github/workflows/release.yml` publishes with trusted publishing (OIDC): no
npm or PyPI token exists anywhere. Only a version the owner asked for is
released (invariant 9).

1. Bump the version: npm = `npm/package.json` + `VERSION` in
   `npm/veilfile.js`; PyPI = `cli/pyproject.toml` + `__version__` in
   `cli/veilfile.py` (tests fail on a half-done bump). Add a "What's new"
   section to that package's README (it is the public package page).
2. Merge to `main` through a PR with CI green. Deploy the server first when
   the release depends on server changes.
3. Tag that commit on `main` and push the tag: `npm-v<version>` and/or
   `pypi-v<version>`. The workflow refuses tags that do not match the package
   version or point off `main`.
4. Confirm the release: `npm view veilfile version` and
   `https://pypi.org/pypi/veilfile/json`.

npm and PyPI each trust one publisher, release.yml in this repository. npm 0.4.1 was the first release from here, and PyPI's next release will be its first. Only the maintainers team can create npm-v* and pypi-v* tags (the release-tags ruleset), so Muse's account pushes them after the owner's go-ahead.

## Invariants: do not break these

1. **Raw API keys are never stored.** Only the SHA-256 hash is (`APIKey`);
   the raw `vlf_` key is shown once. The dashboard shows it in the answer to
   the POST that makes it (`Cache-Control: no-store`, `dashboard/keys.py`),
   never in a redirect, the session, a message, a cookie, a URL, a log or the
   cache. Never log keys, artifact tokens
   (`/a/<token>`, upload and reset tokens) or file contents; the access log
   redacts paths, queries and referers (`config/log_redaction.py`).
2. **Filenames are sanitized on every upload path** (`sanitize_filename` in
   `drop_core/services.py`), and the stored and served `Content-Type` comes
   from the extension, never from client-declared types. CLIs also reduce
   server-supplied names to one safe file name and never overwrite without
   `--force`.
3. **Plan state changes only from Stripe-verified state**: the signature-
   verified webhook or a read-back from the Stripe API. No client request may
   set a plan. `past_due` keeps the paid plan; `unpaid`, `incomplete_expired`,
   `paused` and `canceled` drop to Free. A workspace with a live subscription
   gets `409 already_subscribed` instead of a second Checkout.
4. **Published clients keep working.** Every published CLI (npm `veilfile`
   0.2.0, 0.4.0 and 0.4.1, PyPI `veilfile` 0.3.0 and 0.4.0) and MCP clients in the
   wild call the current endpoints and tools; old versions stay installed for
   a long time.
   Changes to responses, status codes, error codes or tool schemas must be
   backward compatible, or the CLI/MCP release order must be planned (server
   first). Error bodies are always `{"error", "code"}`; clients branch on
   `code`.
5. **Device-flow keys count toward the plan's API-key cap**, and the key is
   created on the CLI's poll, not at approval.
6. **No secrets in the repo.** Env vars come from Render. Example keys in
   docs are placeholders. `.env`, `db.sqlite3` and `media/` are gitignored.
7. **Rate limits and IP logic use `client_ip()`** (trusted-hop aware,
   `VEILFILE_TRUSTED_PROXY_HOPS`), never the leftmost `X-Forwarded-For`.
   `client_ip()` prefers a valid `CF-Connecting-IP` (`VEILFILE_CLIENT_IP_HEADER`)
   only when the proxy-appended peer is a Cloudflare address (the ranges in
   `email_verification.py`; refresh them if Cloudflare changes its list),
   then the hop logic, then `REMOTE_ADDR`; it never returns non-IP text.
   The hop logic counts `VEILFILE_TRUSTED_PROXY_HOPS` entries from the right
   only when the last public entry is a Cloudflare address. A request that did
   not pass Cloudflare (straight to Render's origin) has the client's own typed
   entries left of the one Render's proxy appended, so the last entry is the
   client and the hop count is not applied: a throttle must never trust
   `entries[-hops]` blindly, and nothing but `client_ip()` reads
   `X-Forwarded-For` (`DirectToOriginTests`, `tests_oauth_hardening`).
   Every OAuth endpoint that checks or hashes a client secret (`/o/token/`,
   `/o/revoke_token/`, and `/o/register/`, which hashes the secret it
   generates) is throttled per IP in `drop_core/oauth.py` (`_ip_allowed`, a
   `Throttled...View`); a route added under `/o/` is classified in
   `OAuthRoutesThrottleTests`, which fails until it is. A counter built on
   `cache.incr()` catches `ValueError` (the window ended between the read and
   the increment) and restarts the window, as `device._bump` and
   `throttle._hit` do; a test fails on a bare `incr`. DCR bodies (POST and PUT)
   are at most 16 KiB, checked before they are parsed or the registration token
   is, and may hold no NUL and no lone surrogate.
8. **Do not edit `backend/legal/documents/*`** (terms, privacy policy,
   abuse policy) without the owner. If a code change makes legal text
   inaccurate, say so in your report.
9. **Do not change package versions** (`npm/package.json` + `VERSION` in
   `npm/veilfile.js`, `cli/pyproject.toml` + `__version__`, `server.json`,
   `plugin/.claude-plugin/plugin.json`, `SERVER_VERSION` in
   `drop_mcp/views.py`) except in a release the owner asked for.
10. **The licenses are the owner's.** The root LICENSE (all rights reserved) and the CLIs' MIT licenses change only with the owner. Never add an open-source license to the server code.

## Pricing changes

Plan prices and caps are defined in `backend/drop_core/plans.py` (`PLANS`, and
`ANNUAL_PRICE_USD` for yearly billing, ten months' price) and enforced from there: a
yearly subscriber is on the same plan slug, with the same caps, as a monthly one.
Stripe prices are separate objects in Stripe, wired via
`STRIPE_PRICE_TEAM`, `STRIPE_PRICE_TEAMPLUS` and `STRIPE_PRICE_SCALE`, and yearly via
`STRIPE_PRICE_TEAM_ANNUAL`, `STRIPE_PRICE_TEAMPLUS_ANNUAL` and `STRIPE_PRICE_SCALE_ANNUAL`,
each mapped by name, by plan and interval, in
`backend/drop_billing/account.py` (Team Plus's is `STRIPE_PRICE_TEAMPLUS`, the owner's choice, never the
slug's `STRIPE_PRICE_TEAM_PLUS`); changing a price needs new Stripe
prices (the owner does that) and then every place that advertises it. A test
(`backend/drop_core/tests_fix_docs.py`) scans these and fails on any mismatch
with `plans.py` (a price followed by `/yr`, `/year`, "a year" or "per year" is
checked against `ANNUAL_PRICE_USD`, and a table row "Team (yearly)" against
Team's caps):

`README.md`, `SPEC.md`, `docs/API.md`, `docs/llms.txt`,
`backend/docs/pages/*.md`,
`backend/blog/posts/*.md`, `backend/legal/documents/*.md`,
`backend/templates/**/*.html` (none types a plan figure today: the sign-up
blurb is typed in `backend/drop_core/views.py`, which also passes the checkout
page its price, and the Plans chapter takes every figure from `plans.py`; the
scan catches one typed later), `backend/drop_core/views.py` and
`backend/drop_billing/views.py` (docstrings and copy),
`backend/drop_core/account.py` and `backend/drop_billing/account.py` (the
functions behind both the JSON endpoints and the dashboard),
`backend/dashboard/billing_page.py` (the Billing page's sentences and plan figures),
and `backend/pages/registry.py` (page descriptions). The dashboard's pages are
under the templates glob above and take every figure from `plans.py`.

Checklist: edit `plans.py`; update every file above; ask the owner to update
the Terms of Service wording and the Stripe prices; run the backend suite.

## Voice

Every page, message, Markdown file and llms.txt line is written in the owner's voice. These
seven rules are the whole of it; the recipes below point here instead of repeating them.

1. Every sentence is complete: a subject and a finite verb, and an object when the verb
   needs one. An imperative counts, with "you" understood. A list of noun phrases ending in
   a full stop is a fragment, and so is a clipped question.
   - Before: "Fine for a snippet; wrong for a client’s failing screenshot." After: "A gist
     suits a snippet but not a client’s failing screenshot."
   - Before: "No account?" After: "Do you need an account?"
2. A semicolon joins only two independent clauses. Anywhere else it becomes a full stop, a
   comma or a rewrite. The first "before" in rule 1 joins a fragment to a clause, which is why it changes.
3. Labels are exempt: buttons (Copy, Come in), tabs, menu items, field labels, table column
   headers, table cells that hold a value, short mono labels such as "Check this", and the
   corner labels.
4. A heading has no full stop, even when it is a complete sentence, and ends with a question
   mark only when it is a complete question. Headings are `h1` to `h6`, the terms (`dt`) of
   a definition list, Markdown headings and page titles.
   - Before: "Nothing here." After: "Nothing here".
   - Before: a confirmation heading that is a phrase. After: a complete question, as in
     "Do you want to revoke this API key?".
5. Everything else is a full sentence wherever it can be: status lines, notes, result lines,
   empty states, the questions before a link, and every search description.
   - Before: "Key revoked." After: "The key is revoked."
6. `docs/llms.txt` follows the same rule. Data stays terse as a label and its value with no
   full stop ("Upload rate limit: 100 uploads/hour per credential (shared by REST and MCP uploads)"), while anything that explains, qualifies or warns
   is a complete sentence. Its structure, sections and exact values do not change.
7. The older rules stay: no em dashes, no exclamation marks, none of the banned words
   (`BANNED` in `backend/pages/tests_chapters.py`), American spelling ("canceled", not
   "cancelled"), `’` for apostrophes in templates, errors that say what happened and what to
   do with no apology, and no claim the product does not back.
   - Before: "revoke any of them". After: "revoke only the key in use", because a key
     approved on the device page can revoke only itself and not the workspace's others.

A test holds rule 4 and the Markdown voice (`backend/pages/tests_voice.py`), and review
holds the rest, since no test can judge grammar. Every team writes words, so this section
names no single owner.

## Site recipes

### Write a Journal post

1. Create `backend/blog/posts/<slug>.md`. The file name is the slug:
   lowercase words joined by hyphens, like `where-agents-put-screenshots.md`.
   Do not put a date in the file name.
2. Start the file with this header, then a blank line, then the post:

   ```
   ---
   title: Where should an agent put a screenshot?
   date: 2026-11-03
   slug: where-agents-put-screenshots
   description: Agents make files that one or two people need to see for a while. Here is why a private link fits.
   ---
   ```

   - `title`: 60 characters or fewer. Do not repeat it as a heading in the body.
   - `date`: YYYY-MM-DD. A post dated in the future stays hidden until that day.
   - `description`: 70 to 160 characters, plain sentences. It is the search-result snippet.
   - Optional `image`: a path under `backend/static/`, like `img/journal/checkout.png`.
     It needs `image_alt`, one sentence describing the image. The post page shows
     it under the title. Use a PNG: the page reads the size from the file and
     keeps the space for it, so the text does not jump when the image arrives;
     another format is shown too, without that.
   - The page adds "Journal · date · N min read" above the title itself. The
     reading time is the words in the body at 200 a minute (code blocks count),
     so write it nowhere.
   - Each fenced code block in the body gets a Copy button on the page, which
     copies the fence's text. Write nothing for it.
3. Write the body in the voice of `## Voice`, in Markdown only: `##` headings, paragraphs, lists, links,
   `code`, fenced code blocks (each with a Copy button), and images with alt
   text, like `![A failing checkout page](/static/img/journal/checkout.png)`.
   No HTML and no styling: the template decides how everything looks.
   Nothing else needs registering. The post's address, its place in the index,
   the feed and the sitemap, its link preview, its reading time and the older and
   newer links on the posts beside it all come from this file.
4. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content`.
   Every failure names the file, the line, the problem and the fix.
5. Open a pull request. Plan prices and limits in the post must match
   `plans.py` (the docs tests check them).

### Edit a chapter's words

Who: usually the CMO's team. The chapters are the threshold (`/`), About,
How it works, Begin, Promises and Plans.

1. Open `backend/templates/chapters/<name>.html`: `threshold`, `about`, `how`,
   `begin`, `promises` or `plans`. The install line's words (MCP, npm, pip and
   the line after Copy) are in `backend/pages/install.py`.
2. Change only the text between the line
   `{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}`
   and the line `{# End of words. #}`. Keep every tag (`<h1 ...>`, `<p>`,
   `<dt>`, `{% include "components/code_block.html" ... %}`), every `class="..."` and every `{% ... %}`
   exactly as it is. A code block is that include (`id`, `label` for the Copy button's name, `code`): the component
   renders `<pre tabindex="0"><code>...</code></pre>` (the tabindex lets keyboard users scroll a long command, and
   the browser checks fail without it) with a Copy button. Begin's snippets are the one place a chapter's words are
   not in its template: a template cannot hold a multi-line value in an include, so their text is in
   `backend/pages/snippets.py`. A link inside a sentence is a plain
   `<a href="...">`; a labelled action such as Come in is
   `class="label interactive"` and stays as it is.
3. Write in the voice of `## Voice`. Use `’` for apostrophes.
4. State only what the product does (`docs/API.md` is the reference). Never
   type a price or a limit in a chapter: Plans takes every figure from
   `backend/drop_core/plans.py`.
5. Some sentences are product claims that tests pin word for word. In
   `backend/pages/tests_chapters.py`: the threshold's two halves and Come in;
   About's "A quiet place for what your agents make" (and About stays under
   300 words and names no person); How it works' four steps (Made, Veiled,
   Seen, Let go), its sentence "The 256-bit token in the address is the only
   key" and "deleted from storage soon after"; Promises' "The link is the only
   way in", "File contents are never logged." and "Why shouldn’t I use a
   gist?"; Plans' "Prices include tax. The price you see is the price you pay."
   and, in the cards that `backend/pages/plan_cards.py`
   writes, "Unlimited keys", the words around each figure ("N uploads a
   month", "Links last up to N days", "N GB of storage", "$N a month",
   "$N a year, two months free"), Free's price "$0", Team's button "Choose
   Team" and each paid plan's "Choose yearly"; and Begin's three
   headings ("Connect your agent", "Or use the command line", "Or plain
   HTTP", in that order; the test finds them with `.index()`, so a
   renamed one fails with a bare ValueError that names no
   file), its sentences "Clients that support MCP sign-in ask you to sign in
   the first time", "your account needs a verified email", "The others take
   an API key instead" and "The snippets below use the key", its commands
   `npm install -g veilfile`, `pip install veilfile`, `veilfile login`,
   `veilfile push` and `curl -X POST https://veilfile.com/api/v1/artifacts`,
   and its link to `/llms.txt`. In `backend/pages/tests_install_line.py`: the
   tab labels (MCP, npm, pip), what Copy copies (`https://veilfile.com/mcp`
   and `npm install -g veilfile`), the MCP tab's line after Copy ("Add this
   address to your agent as a remote MCP server, and sign in if the agent
   asks.") and `Then run <code>veilfile login</code>` on the npm and pip tabs. To reword one
   on purpose, change the same text in the test in the same pull request.
   Begin's client snippets (`backend/pages/snippets.py`) must equal `backend/docs/pages/agent.md` exactly:
   change them only by changing agent.md and snippets.py together.
6. A page's title and its summary in search results live in
   `backend/pages/registry.py`, not in the chapter: title at most 60
   characters, description 70 to 160. The threshold's and Plans' descriptions
   there type prices; change those only through "Pricing changes".
7. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages`.
   A failure in the words markers, the voice rules in `## Voice`, a figure typed into Plans
   or Begin's snippets names the file and the fix; any other failure only
   shows the text a test expected, which is the pinned-sentence case in step 5.
8. Open a pull request.

A new chapter is more than words, so it is the CTO's team's work. It needs:

1. A template in `backend/templates/chapters/`, like `about.html`, with the two
   words markers.
2. A route in `backend/config/urls.py`, like the others:
   `path("<name>/", pages_views.chapter, {"name": "<name>"}, name="<name>")`.
3. A `Page` in `backend/pages/registry.py` with `layout="chapter"`. That is all
   in-place changes need: `shell.js` changes in place to every registry page
   of the same layout. The other registry layout is `reading` (the Journal, Docs and
   Terms, Privacy and Report abuse): its paths also include every Journal post
   and Docs page, which `layout_paths()` in the same file lists from their
   loaders, so a new post or Docs page needs no `Page`. A change never crosses
   from one layout to the other; that is an ordinary page load. (The dashboard's
   five sections are a third layout, `app`, listed in `DASHBOARD_MENU` and not in
   `PAGES`: see "Add or change a dashboard section".)
4. A `MenuLink` for it in `MENU[0]` in the same file.
5. A line for it under "Pages for people" in `docs/llms.txt` (a test requires
   one for every link in `MENU[0]`).
6. The tests that list the chapters: the address set in
   `test_a_chapter_names_the_pages_it_changes_to_in_place` in
   `backend/pages/tests_chapters.py`, and the menu labels in
   `backend/pages/tests_registry.py` and `backend/pages/tests_shell.py`.
7. A line in `PAGES` in `site-tests/run_site_checks.py`, with `chapter=True`.

### Edit Docs

Who: usually the CTO's team. The pages describe the API, the MCP server and
setup, so what they say must be true of the code.

1. Open `backend/docs/pages/<name>.md`: `index` (`/docs/`), `quickstart`,
   `agent`, `how-it-works` or `api` (`/docs/<name>/`). The file name is the
   slug.
2. Keep the header at the top of the file, with exactly these five keys:

   ```
   ---
   title: Limits
   slug: limits
   seo_title: Limits | Veilfile docs
   description: How big a file can be, how long a link lasts, and how many uploads each plan allows.
   order: 6
   ---
   ```

   - `title`: 60 characters or fewer. The page shows it as its heading, so do
     not start the body with a heading that repeats it.
   - `seo_title`: 60 characters or fewer, the title search results show.
   - `description`: 70 to 160 characters, plain sentences.
   - `order`: a whole number. Pages are listed from the smallest, and the
     previous and next links at the foot of a page follow it.
3. Write the body in Markdown: `##` headings, paragraphs, lists, tables, links,
   `code` and fenced code blocks (each with a Copy button). A page with two or
   more `##` headings opens with a list of them; `###` headings are on the page
   but not in the list. Every heading gets an address from its words
   (`/docs/api/#errors`), so renaming one breaks links to it: search the repo
   for the old one first. A wide table or a long code line scrolls inside its
   own box; a long word wraps.
4. No raw HTML. The one exception is `quickstart.md` (`RAW_HTML_ALLOWED` in
   `backend/pages/content_checks.py`): its shell tabs and Copy buttons are
   written as the shell's own markup (`data-tabs`, `role="tab"`, `data-copy`)
   because Markdown cannot express them. Change their words; keep every tag,
   attribute and id.
5. Write in the voice of `## Voice`, and state only what the product does. Prices and limits must match `plans.py`, tool
   names and error codes must exist in the code, and documented paths must
   resolve (`backend/drop_core/tests_fix_docs.py` checks all three). The client
   snippets under `### Claude Code`, `### Codex` and `### Cursor` in `agent.md`
   must equal Begin's, which are in `backend/pages/snippets.py`: change them
   only together (step 5 of "Edit a chapter's words"; `pages.tests_chapters`
   checks).
6. A new page is a new file written the same way, with an `order` between its
   neighbours. It joins the Docs index, the sitemap, the previous and next links
   and in-place changes by itself, with no registry entry. Add its slug to
   `DOCS_SLUGS` in `DocsTests` (`backend/drop_core/tests.py`) and raise the 5 in
   `_assert_skipped` in `DocsLoaderResilienceTests`
   (`backend/drop_core/tests_fix_web.py`).
7. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content drop_core.tests_fix_docs drop_core.tests.DocsTests`.
   A failure from `pages.tests_content` names the file, the line, the problem
   and the fix.
8. Open a pull request.

### Legal pages

Who: the owner only. Stop and ask.

The Terms of Service, the Privacy Policy and the abuse policy
(`backend/legal/documents/*.md`) are the owner's words (invariant 8). Do not
edit them, not even for a typo or the "Last updated" line, and do not add a
document. If a code change makes one of them inaccurate, say so in your report
and wait for the owner.

How the pages look is code: `backend/templates/legal/document.html` and the
reading layout's CSS may change while every word stays as it is. A test in
`backend/pages/` renders each document from its Markdown and fails when the
page's words differ. Check it:
`cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests.LegalTests`.

### Change or add a component

Who: usually the CTO's team. A component is one piece of the shell, such as
the menu, a plan or the install line; `/app/components/` (staff only) shows
every one in Paper and Ink, Motion on and off.

1. Its template is `backend/templates/components/<name>.html`. Line 1 is
   `{# Component: <name>. What it is (spec section). #}`; line 2 is
   `{# Inputs: each input and what it holds. #}`.
2. Its one stylesheet is `backend/static/css/components/<name>.css`. Colours
   only as `var(--token)` from `backend/static/css/tokens.css` (a test fails
   on a hex value or an `rgb(`, `rgba(`, `hsl(`, `hsla(`, `oklch(`, `lab(` or
   `lch(` call; it does not see named colours or `color-mix()`, so use a
   token for those too). A new colour token also needs an `@property` rule in
   `tokens.css` (syntax `"<color>"`), or it will not fade with the appearance
   switch (`pages.tests_tokens` checks). The cloth's own colours, five per
   appearance, are in `appearance()` in `backend/static/js/veil.js` (spec 3.2);
   `pages.tests_veil` keeps its ground equal to `--paper`, so change them
   together. The calm that keeps small text legible (`CALM`, `FLOOR` and
   `CEILING` in the same file) is tied to `--muted`: a lighter `--muted`, or
   a change to the cloth's colours, needs them measured again
   (`pages.tests_veil` works the contrast out from them, and the browser
   checks measure it over the real cloth). The film grain's strengths (`GRAIN_PAPER` and `GRAIN_INK`, which the calm
   assumes are at most 0.025) set the still grain's two tiles too: after changing them run `python3 tools/make_images.py`
   and commit the two `grain-*.png` (`pages.tests_images` fails until the tiles match). Use a size or space token from the same file (`--space-1` to
   `--space-6`, `--reading-size` and the rest) where one fits.
3. Link the stylesheet in `backend/templates/shell.html`, next to the others:
   `<link rel="stylesheet" href="{% static 'css/components/<name>.css' %}">`.
4. Add an `Entry` for it to `backend/pages/catalog.py`, with sample inputs. If
   its markup has ids, give it an `id_prefix` input. If it only ever sits in a
   layout's column (the reading column's `sections`, `pager` and `entries`), pass
   `within="reading"`: the catalog then shows it inside `<div class="reading">`,
   as the page does; a component of the dashboard's column passes `within="app"` (the rows of `file_list`
   stack by a container query on `.app`, which the catalog draws only round a sample that says so) or the
   class of its own wrapper (`meters` for `meter`). A component with states worth seeing side by side (a field
   blank, with help, with an error) lists the others in `states`, each a context
   of its own with ids that differ from the rest; the frame draws them one under
   the other.
5. Include it only as `{% include "components/<name>.html" with ... only %}`.
6. Add a rendering test, like `backend/pages/tests_install_line.py`.
7. If it needs behaviour, add `backend/static/js/behaviors/<name>.js` and its
   `<script type="module" src="{% static 'js/behaviors/<name>.js' %}"></script>`
   line in `shell.html` next to the others (a test requires every file there to
   be loaded). Listen on `document` and find the component with
   `event.target.closest(...)`, like `tabs.js`, so content swapped in by an
   in-place change needs no re-attaching. `shell.js` and the behaviours together
   stay within 10 KB gzipped (`pages.tests_static` checks it). `veil.js` has its
   own budget, 15 KB gzipped (`pages.tests_veil`).
8. Small text on the cloth needs nothing special as long as it is set in
   `--muted` or `--ink`: the cloth is calmed everywhere (spec 3.1), so the
   contrast holds over every pixel it draws. There are no clearings, and no
   attribute to ask for one. A new text colour (any other colour for small
   text) must be checked against the whole canvas, in Paper and Ink, by the
   contrast family (`contrast_problems` in `site-tests/run_site_checks.py`).
   To have a component's text measured where it sits, add it to the call for
   its page there: something on every page goes in the `/about/` list (the
   `[".chapter", *labels]` of its `text_contrast_problems` call), and in that
   call's `required=` when it always shows at that width, so a selector that
   matches nothing fails instead of passing. Something on one page only, like
   COME IN and the install line on `/`, needs its own call in that page's
   scenario, with `required=`. A component no scenario opens is not measured.
   Text that sits on an ink wash (a held current link, the selected install
   tab) is measured by its words: give its selector its own call with
   `words=True` and the token its words use, as the open menu's links have,
   and leave that wash out of the box measured around it (`apart=`).
9. Check it: the `pages` tests above, then the browser checks ("Site checks"): the catalog part
   (`--part catalog`) opens the catalog, where the component and its states are.

### Change a color, size or spacing

Who: usually the CTO's team. Every color, size and space on the shell's pages, the
dashboard's included, is a token.

1. Change only `backend/static/css/tokens.css`. A color token is defined twice: under
   `:root` for Paper, and under the dark selectors for Ink (`[data-theme="dark"]` and the
   `prefers-color-scheme: dark` rule for `system`). It also has an `@property` rule with its
   Paper value as the initial value, so that it fades when the appearance switches; a new
   color token needs all three.
2. Keep text tokens at 4.5:1 contrast against `--paper` in both appearances (`--ink` at 7:1).
   The cloth's own colors are in `appearance()` in `backend/static/js/veil.js` and
   `pages.tests_veil` keeps its ground equal to `--paper`, so a changed `--paper` changes
   both.
3. Run `pages.tests_tokens` and the other modules that read `tokens.css`
   (`git grep -l tokens.css -- 'backend/**/tests*.py'`), then the site checks' `site` and
   `site-cloth` parts ("Site checks").

### Add or change a form page

Who: usually the CTO's team. A form page has an input or a one-time state: sign in,
sign up, password reset, password change, email verification, checkout confirmation, the device-code
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
   href="{% static 'css/pages/<name>.css' %}">{% endblock %}` (a page that needs none links
   none), with classes inside `form_page` to hang it on, not a body class (the body's is the
   layout's, `layout-form`): the OAuth consent wraps its page in `<div class="consent">`
   (`consent.css`, `.consent__...`), and the device approval and its result use `.device-...`
   classes (`device.css`). Write no
   `<html>`, `<head>`, `<style>`, skip link, menu, `<header>` or `<footer>`: the shell has
   them, and tests fail when a page brings its own.
3. Fields are `components/field.html`: a mono label above the input, with a help line
   (above the input too) and an error sentence. Include it like this (line 2 of the
   component says what each input holds):

   ```
   {% include "components/field.html" with id=... name=... label=... type=... value=... autocomplete=... required=... autofocus=... focus=... help=... errors=... extra=... mono=... only %}
   ```

   `errors` is a form field's `form.<field>.errors` or a list of sentences, never a bare
   string. `extra` is the exact text of an attribute a test pins, and it must be a string
   literal in the template, never a variable (a test scans every template). `mono` is for a
   code. The input's line is drawn in `--muted`, not `--hair`: `--hair` is 1.45:1 on Paper,
   and a field's boundary needs 3:1 (WCAG 1.4.11). Errors that belong to no field go in
   `components/form_errors.html`, with `errors=...` (a list, such as
   `form.non_field_errors`) or `message=...` (one fixed sentence of the page's own), `focus=...` (step 7), `only`.
4. `{% csrf_token %}` goes in the page's own `<form method="post">`, never inside a
   component: a component included with `only` renders no token. Where a form goes is part
   of its contract. Sign in, sign up, checkout confirmation, both password-reset forms, the password-change form, both
   verification forms and the two OAuth consent forms post to their own address (no `action`
   attribute), so `?next=`, `?plan=` and the OAuth query string ride along. The device code
   entry is a GET with `action="{% url 'device' %}"`, so a typed code reaches
   `/device?user_code=...`, the address the CLI opens. The device approval posts to
   `{% url 'device-approve' %}` with the code in a hidden field, and its Deny button's
   `formaction` is `{% url 'device-deny' %}`. Keep each form's method and address. Never
   rename a field or a hidden field, or change a value.
5. One main action: `<button class="action interactive" type="submit">`, a hairline box
   in a mono label that takes the ink wash (`interactive` is what gives it the wash and
   the press; a test fails a button or an `.action` link without it). Two actions of equal
   weight, such as Approve and Deny, go in `<div class="form-actions">`. Quieter links and
   buttons are `<p class="form-links">` holding `a.label.interactive` or
   `button.label.interactive`. A lead-in and its link ("No account?" and Sign up), or a separator
   and the link after it, are one `<span class="form-links__item">`, so they wrap together and a
   separator never ends a line (`<span class="form-links__item"><span aria-hidden="true">·</span>
   <a ...>`); links that do not fit one line of the column with a separator stack, with none
   (`form-links form-links--stacked`, the sign-in page's). A status line that is not an error is
   `<p class="notice" role="status">` (every notice on these pages is news that is read out:
   a link was sent, an address is verified, verify your email first). A checkbox or radio row is `<label class="check"><input class="check__input"
   type="checkbox" ...><span class="check__text">words</span></label>`, a group of them in
   a `<fieldset>` with a `<legend>`. Errors use no colour, only weight, a solid line and a
   small "Check this" (the field component draws them): do not add a colour. Colours live
   in `tokens.css` and nowhere else.
6. Words: keep the page's current words. A new sentence follows `## Voice`, says what
   happened and what to do, and does not apologize. Every non-ASCII character must exist in all three fonts (the check mark
   U+2713 does not; `pages.tests_content` checks).
7. Accessibility (spec 9): one `<h1>` per page, inside `<main>`; a visible `<label for>`
   on every input; `autocomplete` tokens kept; `autofocus` only on a page that exists to
   take one input (the device code, the reset request, the new password), and on the first field
   in error when a form is sent back with errors: the page asks `{% load shell %}{% focus_field form
   "username" "email" ... default="..." as focus %}` (the fields in the order it draws them; `default`
   is the page's usual field, none on a page of several fields) and passes `focus=focus` to each field,
   and the field named prints `autofocus`, so one field has it and the server does it with no script
   (a plain `autofocus=True` is for a page with no form object, the device code). An error that belongs
   to no field (`form_errors`, `role="alert"`), when no field shows an error, takes focus itself, because an alert
   already on the page as it loads is announced inconsistently while focus stays at the top: the page passes
   `focus=...` to `form_errors`, which prints `tabindex="-1"` and `autofocus` on the summary. Where the page has a form
   object it asks `{% focus_summary form "username" ... as summary %}` (the same fields as `focus_field`, in the
   same order) and passes `focus=summary`: true only when the form has an error of its own and no field has one, and
   `focus_field` then names no field, the page's usual one included, so a field in error wins and a page never has two
   autofocus. Where no field of the page shows an error, the page passes `focus=True`: sign in (its one sentence stands
   for every failure) and the device approval (`errors` is its refusal). Only those two can show a summary alone
   today: the Django forms of sign up, both password-reset forms, password change and verify email put every error on a field, so their
   summary is drawn only for a form given an error of its own, which a test does; the device code entry puts its
   errors on its field, OAuth consent draws no summary, and the 429 pages' own `role="alert"` is no form's summary.
   The focused summary shows the global `:focus-visible` outline (Chromium draws it on an autofocused element, after a
   key or a click, and the forms part reads that); inputs 16px or larger on phones, so iOS does not zoom in, and clear
   of the top fade when focused; nothing scrolls sideways at 320px. The components do these; keep to them.
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

### Add or change a dashboard section

Who: usually the CTO's team. The signed-in dashboard is the Django app `backend/dashboard/`, mounted at
`/app/` in `backend/config/urls.py` after the staff catalog (`/app/components/` stays in `pages`); any other
address under `/app/` is the shell's 404. Its pages are of two kinds. A **section** (Overview, Files, Keys,
Connected apps, Billing) is on `layouts/app.html`, one layout family (`app`), so moving between sections
changes in place, as the chapters do (spec 4.5). A **page that hangs off a section** (a confirmation page, or
the page that shows a new key) is on `layouts/form.html` with no `shell_page` entry, so it loads as an
ordinary page: build it as "Add or change a form page" says, plus the rules below.

The dashboard holds no rule of its own. Usage, keys, files, connected apps and billing are plain functions in
`backend/drop_core/account.py` and `backend/drop_billing/account.py`, each returning an `Answer(status, body)`:
the JSON endpoints send it (`answer_response`) and the dashboard draws a page from it, so a rule changes once
and the endpoints keep answering as published clients expect (invariant 4). A dashboard view calls those
functions, never a DRF view, and reaches Stripe only through `drop_billing.account`.

1. The rule first. A new or changed rule goes in the account function, with its tests in
   `backend/drop_core/tests_account.py` or `backend/drop_billing/tests_account.py` and, where the JSON answer
   must not change, in the app's `tests_json_answers.py` (they pin its bytes). What a page needs that is not
   HTTP (shaping rows, words, figures) goes in a module beside the views: `backend/dashboard/file_rows.py`,
   `backend/dashboard/overview_page.py` and `backend/dashboard/billing_page.py` are the examples, and
   `backend/dashboard/keys.py` holds the views of a section that has several.
2. The view, in `backend/dashboard/views.py` or the section's own module. Decorate it `@dashboard_view` (a
   signed-out request goes to sign in and back, every answer carries `X-Robots-Tag: noindex, nofollow` and the
   redirect to sign in too, and the CSRF cookie is set), and `@require_safe` for a page that only shows or
   `@require_http_methods([...])` for one that also acts. A page that holds a link or a one-time token (Files,
   Keys) is never cached. Its address goes in `backend/dashboard/urls.py` as `app-<name>`: the names and
   addresses are the table in spec 4.8, and `dashboard.tests_recipe` fails until the table lists the new one.
3. The menu and the family, in `backend/pages/registry.py`. A section is a `MenuLink` in `DASHBOARD_MENU`; its
   place there is its place in the menu, and `layout_paths("app")` and the shell's entry for the page (the
   title `<Label> | Veilfile`, the layout) follow from it. Every dashboard URL name, a confirmation page's and a
   POST-only address's included, is a key of `DASHBOARD_SECTIONS` whose value is the section it marks: a
   section marks itself, a page that hangs off one marks it. A name missing there draws the ordinary menu with
   no Sign out button, and `dashboard.tests_menu` fails. A new section also joins the lists the tests walk:
   `SECTIONS` in `backend/dashboard/tests_app.py`, `SECTION_LABELS` and `SECTION_NAMES` in
   `backend/dashboard/tests_menu.py` and `APP_PATHS` in `backend/dashboard/tests_layout.py`.
4. A section's template is `backend/templates/dashboard/<name>.html`: `{% extends "layouts/app.html" %}`,
   filling `app_main`. It has one `<h1>`, which is exactly the menu's word for the section (a test compares
   them); no `title` block (the entry gives it) and no `meta` block (the layout has the `noindex, nofollow`
   tag); each part of the page is a `<section aria-labelledby=...>` with an `<h2>`, a mono label. It prints no
   messages: the layout does, above the `<h1>`. It sets no text in `--font-heading` (italic 400): the layout
   names only italic 300 and Newsreader for the faces, and a section that needs more names it in
   `backend/templates/shell.html` and `LAYOUT_FACES` in `backend/pages/tests_faces.py` together. Its CSS is the
   shell's, never the page's, because an in-place change brings no stylesheet: a rule goes in
   `backend/static/css/base.css` under `.app`, or in a component's sheet (a piece used twice is a component:
   "Change or add a component"; `meter` and `file_list` are two, and the catalog draws each inside the wrapper
   its `within` names: `within="app"` for the rows, which stack by a container query on `.app`). Only a page on
   the form layout may link a sheet of its own from `backend/static/css/pages/`.
5. Tables are rows: `<table class="rows">` with a `<caption>` (add `class="visually-hidden"` when an `<h2>`
   right above says the same) or an `aria-label`, `<th scope="col">`, and `data-label="<its column's name>"`
   on every `<td>`. The name a person gave comes first and wraps anywhere; any other cell that may hold an
   unbroken string from a visitor takes `data-wrap`; an empty cell is `<td data-label="X"></td>` with no space
   in it, and is not drawn. Narrower than 40rem of the column (a container query on `.app`, not the window)
   the rows stack and each value is led by its label. `dashboard.tests_app_css.RowsMarkupTests` reads every
   template under `backend/templates/dashboard/` and names the file and the fix.
6. Times and sizes: `{% load dashboard %}` (`backend/dashboard/templatetags/dashboard.py`). `{{ when|utc_time }}`,
   or `{{ when|utc_time:"Never" }}`, takes a datetime or the ISO string an account function answers and writes
   `<time datetime="2026-10-22T13:58Z" data-local>22 Oct 2026, 13:58 UTC</time>`; `local-time.js` (a behaviour)
   writes it again in the visitor's own zone and names the zone (`Oct 22, 2026, 9:58 AM EDT`), on load and after
   each change in place, and with no script the UTC text stays. A time on the page always names its zone. `{{ size|bytes }}` writes `999 B`, `1.5 KB`, `25.0 GB`. A dashboard template never uses Django's
   `date`, `time`, `timesince`, `timeuntil`, `naturaltime`, `naturalday` or `filesizeformat` (they print the
   site's New York time, and other words for a size): `FilterConventionTests` fails it. Plan names, prices and
   caps are read from `backend/drop_core/plans.py`, never typed ("Pricing changes").
7. Actions. Every state change is a POST from a `<form method="post">` with `{% csrf_token %}` in the page's own
   template. It answers with a redirect and one line from `messages.success`, `info` or `warning`: a fixed
   sentence ("The key is revoked."), never anything secret; every word on a dashboard page follows `## Voice`. The layout prints the line as a `.notice` with
   `role="status"` in whichever dashboard page loads next; a page on the form layout prints none. A GET changes
   nothing, revokes nothing and creates no Stripe session. An action that takes something away (revoke,
   disconnect, a switch between paid plans) goes first to a confirmation page: a GET that only shows a form,
   names what it will change and has one main action and a quiet way back, on `layouts/form.html` with the title
   `Name | Veilfile` and `<meta name="robots" content="noindex, nofollow">` in its `meta` block. A POST without
   its token is the shell's "Form not sent" page, from the middleware.
8. Whose objects. An address names an object (a key's id, a file's uuid, an app's id). Look it up inside the
   signed-in person's own account (`get_or_create_workspace(request.user)` is the workspace the account
   functions read), and answer another person's and a missing one with the shell's 404, the same bytes for both:
   `return not_found(request)` (`pages.errors`) or `raise Http404`, never a 403 and never a change. Ask whose
   it is before anything about its state, so that a stranger never gets the sentence an owner would. A malformed
   id never reaches a view, because the converter in the route (`<uuid:...>`, `<int:...>`) does not match, and
   that is the 404. An object that is the person's own but can no longer be acted on (a key already revoked, a
   file whose link is dead) gets a sentence and a redirect, not an error.
9. A new key is shown once (invariant 1). `backend/dashboard/keys.py` writes the raw key in the answer to the
   POST that makes it (200, `Cache-Control: no-store`) and nowhere else: not in a redirect, the session, a
   message, a cookie, a URL, a log, the cache or the database. A reload resends the POST, so the Keys form
   carries a one-time token (`backend/dashboard/forms.py`; the Keys page keeps the last five unspent in
   `request.session["dashboard_key_tokens"]`), the POST spends it before it makes the key, and a spent token is a
   409 that says no key was made. A page that shows any other credential follows the same rule, with a test
   like `RawKeyIsKeptNowhereTests`.
10. Stripe. Checkout and the portal are POSTs that answer 303 to the address Stripe's API returned, and only if
    it starts with `https://`. A refusal is a message and a redirect to Billing, never a 500 and never a Stripe
    page from a GET. No dashboard request sets a plan (invariant 3). A customer reads only Veilfile's own fixed
    sentence for a refusal, never Stripe's text, an API code or operator wording ("this Veilfile instance",
    "until Stripe is connected"): `backend/dashboard/billing_page.py` (`refusal`) chooses the sentence. A failure
    the page cannot explain (a 502, a 503, a status it does not know, an address that is not `https://`) ends
    with `Reference: 4F9A2C` (`reference()`, six capitals or digits), and the one line
    `billing: dashboard refusal ref=4F9A2C where=... status=... code=... message=...`, written through
    `logging.getLogger("drop_billing")`, holds the status, the code and Stripe's message under that reference
    (ERROR for those, WARNING for a refusal the page explains). The JSON endpoints keep their bodies. A test
    for a new refusal reads the page for the sentence, the page for the absence of Stripe's words, and the log
    record for the reference. Tests patch the Stripe library through `FixBillingBase`; nothing contacts
    Stripe, Render or production.
11. Pages that carry nothing of a person. The menu inside the dashboard ends in a Sign out form that holds the
    page's CSRF token (`shell_sign_out` asks for it only inside the dashboard), so the error pages, the
    throttled (429) pages and the `/a/<token>` miss page never draw it (`plain_menu`; "Error pages"). A
    dashboard page therefore has two CSRF tokens, the menu's and its own: a test that counts them reads
    `<main>` only.
12. No build, no stored defaults. A dashboard page is a template and a stylesheet and, at most, a behaviour
    (`backend/static/js/behaviors/`, within the script budget): nothing is built or bundled, and
    `NoBuildStepTests` fails a build output that comes back as a template or static directory, a Dockerfile
    stage or a CI step. The saved theme and motion have one reader, the boot script in
    `backend/templates/shell.html`, and one writer, `backend/static/js/shell.js`, on a click: a dashboard page
    writes no default of its own, and `StoredChoicesTests` fails one that does.
13. Tests, in `backend/dashboard/tests_<name>.py`: each class sets `longMessage = False`, every failure message
    names the file and the fix, and the module is listed in `READING_TEST_MODULES`
    (`backend/pages/tests_site_rules.py`). For every page: a signed-out request is a 302 to
    `/accounts/login/?next=...`; another person's object is the shell's 404; a GET changes nothing; a POST
    without its token is "Form not sent"; every refusal is a sentence and not a 500; and the page holds nothing
    it must not (the raw key, a typed price). A page on the form layout also goes through
    `FormPagesAreOnTheShellTests.check`. The rules that run on every dashboard template are already in place
    (`backend/dashboard/tests_app_css.py`, `tests_filters.py`, `tests_layout.py`, `tests_menu_pages.py`).
14. Browser checks: the dashboard part (`--part dashboard`, CI job `site-dashboard`) opens the sections, walks
    the changes in place, Sign out, the key flow, a revoke, the stacked rows and the times with script on and
    off. Add a new page to that part's pages in `site-tests/run_site_checks.py`, and whatever the page needs (a
    user, a key, a file, an app) to `site-tests/make_dashboard_fixtures.py`, which like `make_form_fixtures.py`
    refuses to run outside local SQLite. CI has no Stripe, so the part sees Billing unconfigured and with stored
    states; the Stripe flows are Django tests. CI runs it as one job beside the others, on a runner of its own, so a part that grows lengthens that job's own run (it has 20 minutes) and not the other jobs ("Where CI runs").
    "Site checks" says how to run it.
15. Check it:
    `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard pages drop_core drop_billing`,
    then the dashboard part ("Site checks").
16. Open a pull request.

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
  For the same reason they always draw the ordinary menu (`plain_menu` in their context), even
  for a failure at a dashboard address: inside the dashboard the menu ends in a Sign out button
  that carries the page's CSRF token. An error page drawn for an address under `/app/` carries
  `X-Robots-Tag: noindex, nofollow`, as every answer there does.
- A 404 or 403 that cannot be drawn does not become a worse error: `shell_error_page`
  catches the failure and answers with `500.html` under the same status. Keep these pages
  simple all the same: words and the shell, nothing else.
- `/a/<token>`: a missing link and an expired one (and a revoked or held one, or one whose
  file is gone) get the same page from `artifact_miss()` in `pages/errors.py`, with the same
  status, body and headers (`Vary` and `Cache-Control` included). It is drawn with no
  request, so it never reads the session and is the same bytes for everyone, and it says only
  "Nothing here" and "This link does not lead to a file. If someone shared it with you,
  ask them for a new link." Whether a
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

- New behavior needs a test (new tests go in a `tests_<topic>.py` module next
  to the code, or `npm/test/`, `cli/tests/`) and, if externally visible, an
  update to `docs/API.md`, `docs/llms.txt` and `backend/docs/pages/`. The
  docs tests check tool names, error codes, documented paths and env vars
  against the code.
- What the dashboard and the JSON endpoints both do (usage, API keys, files,
  connected apps, billing) is plain functions in `backend/drop_core/account.py`
  and `backend/drop_billing/account.py`, each returning an `Answer`. The REST
  views send it with `drop_core.errors.answer_response`; the dashboard draws a
  page from it. Change a rule there, once. `tests_json_answers.py` in each app
  pins the bytes the JSON endpoints answer, which published clients read
  (invariant 4). The Stripe library is still `drop_billing.views.stripe`,
  where the tests patch it: `account.py` reads it from there at call time.
- Match surrounding style and comment density. Keep changes focused; no
  drive-by refactors.
- A test that compares a whole page (up to 160 KB of HTML) sets
  `longMessage = False` on its class and passes a message that names the file
  and the fix, so a failure prints that message and not the page before it.
  `pages.tests_site_rules` (`ReadableFailuresTests`) holds the reading pages'
  and the form pages' test modules to this: list a new one in
  `READING_TEST_MODULES` there.
- Do not add runtime dependencies to `backend/requirements.txt` (all 29 are
  pinned) without the owner's approval.
