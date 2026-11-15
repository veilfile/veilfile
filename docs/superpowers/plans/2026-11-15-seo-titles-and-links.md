# SEO, second fixes: two titles and three links

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry out the parts of items 6 and 8 of the SEO review of 2026-11-15 (`~/dev/veilfile-seo-audit/ACTION-PLAN.md`) that the owner chose. Two page titles change, Plans links the Docs' limits, and both blog posts link the quickstart.

**Architecture:** One registry change, one template change and two Markdown changes. The feed already makes relative links absolute (`blog/feeds.py`, `absolute_links`).

**Tech Stack:** Django templates and Python-Markdown.

**Spec:** The SEO review's action plan, items 6 and 8. Branch `seo/titles-and-links`, from `main` at 6d9535e.

## The owner's choices (2026-11-15)

1. **Titles.** The home title becomes "Veilfile | Private, expiring file hosting for AI agents". The /how/ title becomes "The life of a file | Veilfile".
2. **Links.** Plans gets "The full limits for each plan are in the Docs: plans and limits.", and "plans and limits" links to the Docs' "Plans and limits" section. In the first post, "Start at veilfile.com." becomes "Start with the quickstart.". The second post ends with "To set this up, start with the quickstart.". In both posts, "the quickstart" links to `/docs/quickstart/`.
3. **Left as they are:** the home page's words and links, dates (item 5), headings (item 7) and the blog's name (item 13).

## Global Constraints

- The voice rules: complete sentences, semicolons only between independent clauses, no em dashes or exclamation marks, and American spelling.
- New words go between the template's `{# Words: … #}` and `{# End of words. #}` markers. Titles stay at 60 characters or fewer.
- Agents do not edit `backend/legal/documents/`. Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC` and no attribution lines.
- Tests run from `backend/` with `DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <module>`, and the whole suite once at the end, in the background.

## Review Focus

1. **The section link.** The Docs' `toc` extension must give "## Plans and limits" the id `plans-and-limits`, or the link lands at the top of the page.
2. **The titles elsewhere.** `og:title`, the JSON-LD and the menu must still read correctly, and no test or browser check may pin the old titles.
3. **The feed.** Both posts' quickstart links must come out absolute in `/blog/feed.xml`.

---

### Task 1: Two titles

**Files:** `backend/pages/registry.py` (Pages `landing` and `how`) and `backend/pages/tests_head.py`.

- [ ] **Step 1: Write the failing test.** `test_home_and_how_titles` asserts `<title>Veilfile | Private, expiring file hosting for AI agents</title>` on `/` and `<title>The life of a file | Veilfile</title>` on `/how/`.
- [ ] **Step 2: Run** it. Expected: it fails.
- [ ] **Step 3: Implement** the two titles.
- [ ] **Step 4: Run** `pages`. Expected: all pass.
- [ ] **Step 5: Commit** "Name files, not artifacts, in the home title, and give How its own title".

### Task 2: Plans links the Docs' limits

**Files:** `backend/templates/chapters/plans.html`, where a `chapter__more` paragraph follows the file-size paragraph, and `backend/pages/tests_chapters.py` (PlansTests).

- [ ] **Step 1: Write the failing test.** `test_plans_links_the_limits_in_the_docs` asserts the sentence on `/plans/`, the link `<a href="/docs/how-it-works/#plans-and-limits">plans and limits</a>`, and the id `plans-and-limits` on `/docs/how-it-works/`.
- [ ] **Step 2: Run** it. Expected: it fails.
- [ ] **Step 3: Implement** with `{% url 'docs-page' 'how-it-works' %}#plans-and-limits`.
- [ ] **Step 4: Run** `pages`. Expected: all pass.
- [ ] **Step 5: Commit** "Plans links the limits in the Docs".

### Task 3: The posts link the quickstart

**Files:** `backend/blog/posts/introducing-veilfile.md`, `backend/blog/posts/stop-pasting-screenshots-into-public-places.md` and `backend/blog/tests_journal.py`.

- [ ] **Step 1: Write the failing test.** `QuickstartLinkTests` asserts that each post at `/blog/{slug}/` contains `<a href="/docs/quickstart/">the quickstart</a>`, and that `/blog/feed.xml` contains `href="https://veilfile.com/docs/quickstart/"` once for each post.
- [ ] **Step 2: Run** it. Expected: it fails.
- [ ] **Step 3: Implement** the two Markdown changes.
- [ ] **Step 4: Run** `blog`. Expected: all pass.
- [ ] **Step 5: Commit** "The posts link the quickstart".

### Task 4: Checks

- [ ] Run the whole suite in the background after `memory_pressure | tail -1`, then `makemigrations --check --dry-run`.
- [ ] Write a short test card, `~/dev/veilfile-test-cards/seo-titles-and-links.md`.
