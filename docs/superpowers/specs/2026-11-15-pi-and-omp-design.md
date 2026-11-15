# Veilfile for pi and omp users: design

Short design, 2026-11-15. It covers item 4 of the SEO review of 2026-11-15 (`~/dev/veilfile-seo-audit/ACTION-PLAN.md`), as the owner reshaped it.

## Intent

The pages should reach developers who want what Veilfile offers, and they should buy a paid plan. Claude now offers its own answer to the problem, so the best prospects use other coding agents, such as pi and omp. A pi or omp user who searches for a private way to share what their agent makes should find a page that names their agent, set Veilfile up in a minute, and see the plans.

## Goals

**PR 1 (this design):**
1. A version of the share skill that uses the `veilfile` command-line tool instead of MCP. pi (0.79.6, checked on 2026-11-15) has no MCP of its own, and it loads Agent Skills from `~/.agents/skills/`.
2. pi and omp sections in the Docs' Agent setup page (`backend/docs/pages/agent.md`), beside Codex and Cursor.
3. Each new section ends by pointing to Plans.

**PR 2 (its own short design later):** two use-case pages in the Docs, sharing a HAR file or log bundle, and how Veilfile compares with gists. Both end by pointing to Plans.

## Non-goals

- A landing page for each agent, or Journal posts about pi and omp.
- Publishing the skill to omp's registry (skills.omp.sh) or as a pi package. Both are outward steps for later, each on the owner's go-ahead.
- A `veilfile skill install` command. The next CLI release waits for the team-seats switch (F4).
- Pages about Claude.

## Anti-goals

- Words that say what pi or omp do without the claim having been checked against the tool or its own docs.
- A skill that puts the API key into the agent's prompt or context. The key stays in the CLI's stored login or in `VEILFILE_API_KEY`.
- Any sentence about team features before the switch is on.

## Constraints

- The skill uses only commands and flags in the CLI versions already published (npm 0.4.1 is the latest npm release tag; the PyPI version is checked in the plan), such as `login`, `push` and `status`. The unreleased 0.5.0 flag `--visibility` is not used.
- The owner approves every word: the skill text and the Docs sections. The voice rules apply, and facts must hold.
- The owner's identity never appears.

## Approaches

1. **Docs first (chosen by the owner):** the command-line skill, served from veilfile.com; pi and omp sections in the Docs; then two use-case pages. Everything is Markdown on pages that are already indexed.
2. **A landing page for each agent** (/for/pi/, /for/omp/): rejected, because they would mostly repeat the Docs and each needs page design work.
3. **Journal posts:** rejected, because posts are dated news, and setup instructions go stale there.

The owner also chose to split the work: PR 1 is the skill and the two setup sections, and PR 2 is the use-case pages.

## My calls (the owner can correct any of them)

- **Where the skill lives and how it is served.** The file is `backend/pages/skills/veilfile/SKILL.md`, inside the Docker image. A small view serves it at `https://veilfile.com/skills/veilfile/SKILL.md` as `text/markdown`, the way `llms_txt` serves `docs/llms.txt`. It stays out of the sitemap.
- **Its name is `veilfile`.** The Agent Skills format requires the directory and the `name` to match.
- **Install line for pi:** `mkdir -p ~/.agents/skills/veilfile && curl -fsSL https://veilfile.com/skills/veilfile/SKILL.md -o ~/.agents/skills/veilfile/SKILL.md`, after `npm install -g veilfile` (or `pip install veilfile`) and `veilfile login`.
- **What the skill tells the agent:** when to share a file, to run `veilfile push <path>` and give the user the link it prints, what to do when the CLI is missing or not logged in (tell the user the one command to run, never ask for the key), and the existing skill's "Never" rules. The existing MCP skill in `plugin/` stays as it is.
- **The pi section** covers only the command-line route. It does not mention community MCP add-ons, because no one has tested Veilfile's sign-in through them.
- **The omp section** says that omp picks up MCP servers already set up for Claude, Cursor or Codex, and gives the same command-line route. Where omp loads skills from is checked during the build, against omp itself, before any sentence says so.
- **Tests:** the served file matches the repo file byte for byte, with the right type; every `veilfile <command>` the skill names exists in the published CLI; the Docs sections render, appear in the page's contents list, and link Plans.

## Left to the builder

The exact words, drafted in the plan for the owner's approval; test details; the test card, in which the owner installs the skill in their own pi and omp and asks each to share a file.
