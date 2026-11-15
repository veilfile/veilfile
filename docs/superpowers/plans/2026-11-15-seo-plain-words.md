# SEO, first fixes: who runs Veilfile, and where PixelLeak comes from

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry out items 2 and 3 of the SEO review of 2026-11-15 (`~/dev/veilfile-seo-audit/ACTION-PLAN.md`). About says who runs Veilfile and how to reach them, and both blog posts link Glow Labs' PixelLeak report.

**Architecture:** One template change and two Markdown changes. Python-Markdown (`blog/render.py`) renders the links.

**Tech Stack:** Django templates and Python-Markdown.

**Spec:** The SEO review's action plan, items 2 and 3. Branch `seo/plain-words`, from `main` at 7b9fdc5.

## Questions for the owner (answered 2026-11-15)

1. **The threshold sentence (item 1).** **Answer:** leave the home page as it is. Its title and description already say what Veilfile is.
2. **About.** **Answer:** "Veilfile is made by a small, independent team. To reach us, write to support@veilfile.com." The address is a mailto link.
3. **The blog posts.** **Answer:** in both posts, "disclosed the PixelLeak incident" links to Glow Labs' report, https://www.glow.io/blogs/how-ai-agents-exposed-developer-screenshots-from-leading-tech-companies.
   - The report was checked on 2026-11-15: it is titled "PixelLeak: How AI Coding Agents Leak Screenshots to Public GitHub", dated September 29, 2026, and says "over 13,000 internal images".
   - No other words change.

## Global Constraints

- The voice rules: complete sentences, semicolons only between independent clauses, no em dashes or exclamation marks, and American spelling. The facts must hold.
- New words go between the template's `{# Words: … #}` and `{# End of words. #}` markers.
- The owner's identity never appears. Agents do not edit `backend/legal/documents/`.
- Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no attribution lines. PRODUCT.md and DESIGN.md stay uncommitted.
- Tests run from `backend/` with `DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <module>`, and the whole suite once at the end, in the background.

## Review Focus

1. **An outside link in a post.** Python-Markdown writes a plain `<a href>`. Check that no blog test, site rule or feed check forbids it, and that the RSS feed (`/blog/feed.xml`) still validates.
2. **The About page after an in-place chapter change.** shell.js swaps chapters in place, so the mailto link must work after such a change as well as on a full load.
3. **Pinned copies of the words.** A test, `llms.txt` or a golden file that copies About's paragraphs or the posts' text must be updated with them.

---

### Task 1: About says who runs Veilfile and how to reach them

**Files:** `backend/templates/chapters/about.html`, where a third `<p>` goes inside the markers, and `backend/pages/tests_chapters.py`.

- [ ] **Step 1: Write the failing test.** `test_about_says_who_and_how_to_reach_them` asserts the approved sentence and `<a href="mailto:support@veilfile.com">support@veilfile.com</a>` on `/about/`.
- [ ] **Step 2: Run** it. Expected: it fails.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pages`. Expected: all pass.
- [ ] **Step 5: Commit** "About says who runs Veilfile and how to reach them".

### Task 2: The posts link Glow Labs' PixelLeak report

**Files:** `backend/blog/posts/introducing-veilfile.md`, `backend/blog/posts/stop-pasting-screenshots-into-public-places.md`, and the blog's test module.

- [ ] **Step 1: Write the failing test.** `test_each_post_links_the_pixelleak_report` asserts that each rendered post contains `<a href="https://www.glow.io/blogs/how-ai-agents-exposed-developer-screenshots-from-leading-tech-companies">disclosed the PixelLeak incident</a>`.
- [ ] **Step 2: Run** it. Expected: it fails.
- [ ] **Step 3: Implement** the two Markdown links.
- [ ] **Step 4: Run** the blog tests and `drop_core.tests`, which includes the blog checks. Expected: all pass.
- [ ] **Step 5: Commit** "The posts link Glow Labs' PixelLeak report".

### Task 3: Checks

- [ ] Run the whole suite in the background after `memory_pressure | tail -1`, then `makemigrations --check --dry-run`.
- [ ] Write a short test card, `~/dev/veilfile-test-cards/seo-plain-words.md`, covering About's mail link (on a full load and after a menu change) and both posts' links.
