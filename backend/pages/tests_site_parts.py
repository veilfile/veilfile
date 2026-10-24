"""The browser checks run as CI jobs, `site` (the pages), `site-cloth` (the cloth), `site-ink` (the ink washes), `site-reading` (the Journal, Docs and
legal pages), `site-phone` (the phone menu's sheet, in Chromium and WebKit), `site-forms` (the forms and the error pages), `site-catalog` (the component
catalog's page checks) and `site-dashboard` (the signed-in dashboard), each with its own 20 minutes, and locally as `--part all`, which must be every one of them: a check that fell between the parts would pass
CI without ever running, and one in two would run twice (the few that the pages, reading, forms, catalog and dashboard parts run, each on its own pages, are named in
SHARED_PAGE_CHECKS in the harness). Every `site*` job in ci.yml is a part (a new one is one more part and one more name in the docs below).
site-tests/run_site_checks.py needs Playwright, which the backend does not install, so it is read as source here."""
import ast
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR).parent
HARNESS = ROOT / "site-tests" / "run_site_checks.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-10-08-site-redesign-design.md"
AGENTS = ROOT / "AGENTS.md"


def workflow_jobs(text):
    """{job name: the job's lines} of ci.yml (the keys under `jobs:`, not `on:`'s `push:`)."""
    return {match.group(1): match.group(2) for match in re.finditer(r"^  ([a-z-]+):\n((?:    .*\n|\n)+)", text[text.index("\njobs:\n"):], re.M)}


class Harness:
    """The harness's module-level functions and assignments, and what each one reaches by name."""

    def __init__(self):
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        self.functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.values = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}

    def reach(self, start):
        """Every module-level function or value that `start` names, directly or through what it names."""
        seen, todo = set(), [start]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            node = self.functions.get(name) or self.values.get(name)
            if node is not None:
                todo += [use.id for use in ast.walk(node) if isinstance(use, ast.Name) and (use.id in self.functions or use.id in self.values)]
        return seen

    def checks(self, start):
        """The check functions (named ..._problems) that `start` reaches."""
        return {name for name in self.reach(start) if name in self.functions and name.endswith("_problems")}

    def names(self, value):
        """The names a module-level value (a tuple or list of functions, say) lists."""
        return {use.id for use in ast.walk(self.values[value]) if isinstance(use, ast.Name)}


# CI job, the part it runs, the artifact it keeps. A new part goes here, in ci.yml and in the docs (the last test).
JOBS = (("site", "pages", "site-screenshots"), ("site-cloth", "cloth", "site-cloth-screenshots"), ("site-ink", "ink", "site-ink-screenshots"),
        ("site-reading", "reading", "site-reading-screenshots"), ("site-phone", "phone", "site-phone-screenshots"), ("site-forms", "forms", "site-forms-screenshots"),
        ("site-catalog", "catalog", "site-catalog-screenshots"), ("site-dashboard", "dashboard", "site-dashboard-screenshots"))
# Every job of ci.yml runs on a GitHub-hosted Ubuntu 24.04 runner.
RUNS_ON = "ubuntu-24.04"
# `playwright install --with-deps` installs the browsers' Ubuntu packages (a hosted runner has sudo).
WITH_DEPS = "--with-deps"
SITE_JOBS = [job for job, _, _ in JOBS]
# The jobs that run a check in WebKit, and so install it (and say so in their cache key): the phone menu's sheet, and the dashboard's times and Sign out button.
WEBKIT_JOBS = ("site-phone", "site-dashboard")
# The part's entry point in the harness, and the list of its families if it has one.
PART_ENTRIES = {"pages": ("run_pages", None), "cloth": ("cloth_problems", "CLOTH_FAMILIES"), "ink": ("ink_part_problems", "INK_FAMILIES"),
                "reading": ("run_reading", None), "phone": ("run_phone", None), "forms": ("run_forms", None), "catalog": ("run_catalog", None),
                "dashboard": ("run_dashboard", None)}
# The parts that open pages, and so may run the checks named in SHARED_PAGE_CHECKS, each on its own pages.
PAGE_PARTS = {"pages", "reading", "forms", "catalog", "dashboard"}


class SiteChecksSplitTests(SimpleTestCase):
    def test_all_is_every_part_in_order(self):
        stages = ast.literal_eval(Harness().values["PART_STAGES"])
        self.assertEqual(stages["all"], tuple(stage for _, part, _ in JOBS for stage in stages[part]),
                         "run_site_checks.py: `--part all` must run the stages of every part, in the order of the jobs, and nothing else")
        self.assertEqual(set(stages), {part for _, part, _ in JOBS} | {"all"}, "run_site_checks.py: PART_STAGES has a part for each CI job, and `all`")

    def test_every_part_that_another_follows_in_all_has_a_label_for_the_webgl_look(self):
        # `run` looks at WebGL again before a part that follows another in `all` and names the part before it from PREVIOUS_STAGE: a part without a
        # label would be a KeyError in the middle of the run, after the earlier parts had spent their time (a part after `reading` makes `reading` one).
        harness = Harness()
        stages = ast.literal_eval(harness.values["PART_STAGES"])["all"]
        labels = ast.literal_eval(harness.values["PREVIOUS_STAGE"])
        for stage in stages[:-1]:
            self.assertTrue(labels.get(stage, "").strip(), f"run_site_checks.py: PREVIOUS_STAGE has no label for `{stage}`, which another part follows in `--part all`")

    def test_no_check_falls_between_the_parts_or_runs_in_two(self):
        harness = Harness()
        reached = {part: harness.checks(entry) for part, (entry, _) in PART_ENTRIES.items()}
        everything = harness.checks("run")
        shared = set().union(*(harness.checks(name) for name in harness.names("SHARED_PAGE_CHECKS")))
        for part, found in reached.items():
            self.assertTrue(found, f"{part}: the part reaches no check")
        self.assertEqual(everything - set().union(*reached.values()), set(),
                         "these checks run in `--part all` but in no part, so CI would never run them: put them under a part's entry point")
        for first in reached:
            for second in reached:
                if first < second:
                    pair = {first, second} <= PAGE_PARTS  # the parts that open pages run some checks each on its own pages
                    advice = ", or, if the part means to run the same check on its own pages, name it in SHARED_PAGE_CHECKS" if pair else ""
                    self.assertEqual((reached[first] & reached[second]) - (shared if pair else set()), set(),
                                     f"these checks run in both `{first}` and `{second}`, so CI runs them twice: call each from one part{advice}")
        defined = {name for name in harness.functions if name.endswith("_problems")}
        self.assertEqual(defined - everything, set(), "these checks are defined but no part runs them")

    def test_every_family_is_in_one_part_and_runs_there(self):
        harness = Harness()
        listed = {}
        for part, (entry, families) in PART_ENTRIES.items():
            if families is None:
                continue
            listed[part] = {use.id for use in ast.walk(harness.values[families]) if isinstance(use, ast.Name)}
            self.assertTrue(listed[part], f"{families} is empty")
            self.assertLessEqual(listed[part], harness.checks(entry), f"{families}: a family not run by {entry}")
            for other, (other_entry, _) in PART_ENTRIES.items():
                if other != part:
                    self.assertEqual(listed[part] & harness.checks(other_entry), set(), f"{families}: a family that {other_entry} runs too")
        self.assertEqual(listed["cloth"] & listed["ink"], set(), "a family is in both CLOTH_FAMILIES and INK_FAMILIES")

    def test_the_shared_page_checks_are_run_by_the_pages_part_and_by_another_part_that_opens_pages(self):
        harness = Harness()
        listed = harness.names("SHARED_PAGE_CHECKS")
        self.assertTrue(listed, "run_site_checks.py: SHARED_PAGE_CHECKS names no check")
        others = set().union(*(harness.checks(PART_ENTRIES[part][0]) for part in PAGE_PARTS - {"pages"}))
        self.assertEqual(listed - (harness.checks("run_pages") & others), set(),
                         "run_site_checks.py: SHARED_PAGE_CHECKS names checks that the pages part and another part that opens pages (reading, forms, catalog) do not both run: take them out of it")

    def test_the_reading_checks_run_in_the_reading_part_only(self):
        harness = Harness()
        named = {name for name in harness.functions if name.startswith("reading_") and name.endswith("_problems")}
        self.assertTrue(named, "run_site_checks.py: no check is named reading_..._problems, but the reading part's checks are")
        self.assertEqual(named - harness.checks("run_reading"), set(),
                         "run_site_checks.py: these checks are named for the reading part but run_reading never reaches them: call them from run_reading")
        self.assertEqual(named & harness.checks("run_pages"), set(),
                         "run_site_checks.py: the pages part runs these reading checks, which are for the pages in READING_PAGES: call them from run_reading only")
        self.assertEqual({"install_line_problems", "install_line_height_problems"} & harness.reach("run_reading"), set(),
                         "run_site_checks.py: run_reading reaches the install-line checks, which look for the threshold's install line in the page's first [data-tabs] and would fail on the quickstart's shell tabs: keep them out of run_reading (reading_tabs_problems checks the quickstart's tabs)")

    def test_a_page_is_in_one_list_of_pages_only(self):
        harness = Harness()
        lists = ("PAGES", "READING_PAGES", "CATALOG_PAGES", "DASHBOARD_PAGES")
        paths = {name: {call.args[0].value for call in harness.values[name].elts} for name in lists}
        self.assertTrue(all(paths.values()))
        for first in lists:
            for second in lists:
                if first < second:
                    self.assertEqual(paths[first] & paths[second], set(),
                                     f"run_site_checks.py: these pages are in both {first} and {second}, so two parts would check them: keep each in one "
                                     f"(a Journal, Docs or legal page in READING_PAGES, the component catalog in CATALOG_PAGES)")

    def test_the_workflow_runs_each_part_in_a_job_of_its_own(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = {}
        for match in re.finditer(r"^  ([a-z-]+):\n((?:    .*\n|\n)+)", text, re.M):
            jobs[match.group(1)] = match.group(2)
        for job, part, artifact in JOBS:
            body = jobs.get(job, "")
            self.assertIn(f"python -u site-tests/run_site_checks.py --part {part} ", body, f"ci.yml: the `{job}` job should run `--part {part}` with unbuffered output")
            self.assertIn("timeout-minutes: 20", body, f"ci.yml: the `{job}` job has its own 20 minutes")
            self.assertIn(f"name: {artifact}\n", body, f"ci.yml: the `{job}` job keeps its screenshots as `{artifact}`")
            for other_job, other_part, _ in JOBS:
                if other_job != job:
                    self.assertNotIn(f"--part {other_part} ", body, f"ci.yml: the `{job}` job runs only its own part")
        self.assertEqual(sorted(re.findall(r"^  (site[a-z-]*):\n", text, re.M)), sorted(job for job, _, _ in JOBS),
                         "ci.yml: every `site*` job is a part of the browser checks: add it to JOBS here, give it a part in run_site_checks.py and name it in the docs")

    def test_the_phone_job_installs_webkit_for_the_checks_that_run_in_it_and_the_site_job_does_not(self):
        # webkit_sheet_problems skips itself, with a line, where WebKit is not installed: in CI that would be a check that passes by not running. The pages part
        # runs no check in WebKit, and its job does not spend the minute on the install.
        harness = Harness()
        self.assertIn("webkit_sheet_problems", harness.checks("run_phone"))
        self.assertNotIn("webkit_sheet_problems", harness.checks("run_pages"))
        text = WORKFLOW.read_text(encoding="utf-8")

        def job(name, following):
            end = text.index(f"\n  {following}:\n") if following else len(text)
            return text[text.index(f"\n  {name}:\n"):end]

        self.assertRegex(job("site-phone", "site-forms"), rf"playwright install {re.escape(WITH_DEPS)} chromium webkit\n",
                         "ci.yml: the `site-phone` job runs checks in WebKit, so it installs it")
        self.assertRegex(job("site", "site-cloth"), rf"playwright install {re.escape(WITH_DEPS)} chromium\n",
                         "ci.yml: the `site` job installs Chromium")
        self.assertNotIn("webkit", job("site", "site-cloth").split("Browser checks")[0],
                         "ci.yml: the `site` job runs no check in WebKit (webkit_sheet_problems is the phone part's), so it does not install it")

    def test_the_docs_name_every_site_job_where_they_say_which_checks_are_required(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = re.findall(r"^  (site[a-z-]*):\n", text, re.M)
        self.assertTrue({job for job, _, _ in JOBS} <= set(jobs), "ci.yml: the browser checks are the `site*` jobs")

        def paragraph(path, marker):
            """The text from the line that has the marker to the next blank line."""
            whole = path.read_text(encoding="utf-8")
            # assertTrue and assertIsNotNone, not assertIn and assertRegex: those print the whole document (AGENTS.md is 60 KB) before the message
            self.assertTrue(marker in whole, f"{path.name} no longer has {marker!r}; update this test to where the docs now say it")
            start = whole.rindex("\n", 0, whole.index(marker)) + 1
            return whole[start:whole.find("\n\n", start)]

        for where, block in (
            ("the spec's description of the browser-check jobs (section 10)", paragraph(SPEC, "- **Browser-check jobs")),
            ("the spec's branch protection bullet (section 10)", paragraph(SPEC, "- **Branch protection:**")),
            ("the spec's launch step (section 11)", paragraph(SPEC, "**Voice pass and launch:**")),
            ("AGENTS.md's rule on how the browser checks run", paragraph(AGENTS, "The browser checks run as")),
            ("AGENTS.md's rule on which checks to wait for", paragraph(AGENTS, "wait for the `backend`")),
            ("ci.yml's header on the required checks", text[:text.index("name: CI")]),
        ):
            for job in jobs:
                self.assertIsNotNone(re.search(rf"(?<![\w-])`?{re.escape(job)}`?(?![\w-])", block), f"{where} should name the `{job}` job")


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
        self.assertRegex(job, rf"playwright install {re.escape(WITH_DEPS)} chromium\n", "ci.yml: the `site-forms` job installs Chromium and nothing else")
        start = job.find("Start the app")
        order = [job.find(step, start) for step in ("manage.py migrate", "make_staff_session.py", "make_form_fixtures.py", "manage.py runserver")]
        self.assertNotIn(-1, order, "ci.yml: the `site-forms` job runs migrate, make_staff_session.py, make_form_fixtures.py and runserver")
        self.assertEqual(order, sorted(order), "ci.yml: the `site-forms` job migrates, then makes the staff session and the fixtures, then starts the server")
        self.assertEqual([name for name, body in jobs.items() if "make_form_fixtures.py" in body], ["site-forms"], "ci.yml: only the `site-forms` job makes the fixtures")
        self.assertIn("site-tests/form-fixtures.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
                      ".gitignore: add site-tests/form-fixtures.json beside site-tests/session.txt: it holds session keys and the fixture users' password")


class CatalogPartTests(SimpleTestCase):
    """The catalog part (`run_catalog`, CI's `site-catalog`) is the pages part's checks run on the component catalog alone: the catalog's states
    (every form component with its several states) made the pages part take 19.8 of its 20 minutes. Every message names the file and the fix."""

    longMessage = False

    def test_the_catalog_is_in_a_list_of_its_own_and_the_cloth_parts_still_find_it(self):
        harness = Harness()
        paths = {call.args[0].value for call in harness.values["CATALOG_PAGES"].elts}
        self.assertEqual(paths, {"/app/components/"}, "site-tests/run_site_checks.py: CATALOG_PAGES is the component catalog, /app/components/, and nothing else")
        # The cloth and ink parts look the catalog up by its path (page_named) and open it with the open menu sheet (SHEET_COVER_PAGES): taking it out of
        # PAGES must not take it out of them.
        self.assertIn("CATALOG_PAGES", harness.reach("page_named"), "site-tests/run_site_checks.py: page_named must find the catalog, which cloth_presence_problems opens with the cloth running")
        self.assertTrue({"PAGES", "CATALOG_PAGES"} <= harness.names("SHEET_COVER_PAGES"),
                        "site-tests/run_site_checks.py: SHEET_COVER_PAGES must keep the catalog (CATALOG_PAGES) beside PAGES: cloth_menu_sheet_problems looks at the open sheet on each")

    def test_each_part_loops_over_its_own_pages_and_the_catalog_part_gets_every_check_a_page_gets_in_the_pages_part(self):
        harness = Harness()

        def named(function):
            return {use.id for use in ast.walk(harness.functions[function]) if isinstance(use, ast.Name)}

        self.assertTrue("PAGES" in named("run_pages") and "CATALOG_PAGES" not in named("run_pages"),
                        "site-tests/run_site_checks.py: run_pages loops over PAGES only: the catalog is the catalog part's")
        self.assertTrue("CATALOG_PAGES" in named("run_catalog") and "PAGES" not in named("run_catalog"),
                        "site-tests/run_site_checks.py: run_catalog loops over CATALOG_PAGES only")
        # Whatever the pages part checks on a page in every width, appearance and Motion (page_states) and in its edge cases (page_edge_problems, the part of
        # edge_case_problems that is not the threshold's or the chapters') the catalog gets too: moving it must not leave it with fewer.
        per_page = harness.checks("page_states") | harness.checks("page_edge_problems")
        self.assertTrue(per_page, "site-tests/run_site_checks.py: page_states and page_edge_problems reach no check")
        for part in ("run_pages", "run_catalog"):
            self.assertEqual(per_page - harness.checks(part), set(), f"site-tests/run_site_checks.py: {part} must run page_states and page_edge_problems on its pages")
        self.assertIn("page_edge_problems", harness.checks("edge_case_problems"),
                      "site-tests/run_site_checks.py: edge_case_problems (the pages part's) must run page_edge_problems, which the catalog part runs alone")

    def test_the_catalog_runs_none_of_the_checks_that_are_for_the_pages_in_PAGES(self):
        harness = Harness()
        only_pages = harness.checks("edge_case_problems") - harness.checks("page_edge_problems") - harness.checks("page_states")
        self.assertTrue(only_pages, "site-tests/run_site_checks.py: edge_case_problems adds the threshold's and the chapters' checks to page_edge_problems")
        self.assertEqual(only_pages & harness.checks("run_catalog"), set(),
                         "site-tests/run_site_checks.py: the catalog part reaches checks for the threshold and the chapters (they are the pages part's): call page_edge_problems, not edge_case_problems")
        for check in ("grain_scale_problems", "code_block_problems", "corner_type_problems"):
            self.assertNotIn(check, harness.checks("run_catalog"), f"site-tests/run_site_checks.py: {check} looks at the chapters and the shell, not the catalog: it stays in run_pages")

    def test_the_catalog_job_installs_chromium_only_and_signs_in_as_staff_without_the_form_fixtures(self):
        job = workflow_jobs(WORKFLOW.read_text(encoding="utf-8")).get("site-catalog", "")
        self.assertRegex(job, rf"playwright install {re.escape(WITH_DEPS)} chromium\n", "ci.yml: the `site-catalog` job installs Chromium and nothing else")
        self.assertNotIn("webkit", job.split("Browser checks")[0], "ci.yml: the `site-catalog` job runs no check in WebKit, so it does not install it")
        self.assertIn("make_staff_session.py", job, "ci.yml: the catalog is a signed-in page: the `site-catalog` job makes the staff session")
        self.assertNotIn("make_form_fixtures.py", job, "ci.yml: the catalog needs no form fixtures: only the `site-forms` job makes them")


class DashboardPartTests(SimpleTestCase):
    """The dashboard part (`run_dashboard`, CI's `site-dashboard`) is the pages part's checks of a page on the five sections of the signed-in dashboard, and what only the dashboard has: its
    changes in place, Sign out, a new key, a revoke, rows that stack and times in the visitor's zone. It signs in as the users of site-tests/make_dashboard_fixtures.py. Every message names
    the file and the fix."""

    longMessage = False

    def test_the_five_sections_are_checked_with_the_pages_parts_checks_signed_in_as_the_fixture_owner_and_not_as_staff(self):
        harness = Harness()
        calls = harness.values["DASHBOARD_PAGES"].elts
        paths = {call.args[0].value for call in calls}
        self.assertEqual(paths, {"/app/", "/app/files/", "/app/keys/", "/app/apps/", "/app/billing/"},
                         "site-tests/run_site_checks.py: DASHBOARD_PAGES are the dashboard's five sections, and nothing else (a confirmation page is not a page of the family)")
        for call in calls:
            self.assertTrue(any(keyword.arg == "signed_in" and getattr(keyword.value, "value", None) is True for keyword in call.keywords),
                            f"site-tests/run_site_checks.py: {call.args[0].value} in DASHBOARD_PAGES must be SitePage(..., signed_in=True): the sections need a session")
        reached = harness.reach("run_dashboard")
        self.assertTrue({"page_states", "page_edge_problems", "DASHBOARD_PAGES"} <= reached,
                        "site-tests/run_site_checks.py: run_dashboard must run page_states and page_edge_problems on each page of DASHBOARD_PAGES, as run_catalog does on the catalog")
        self.assertNotIn("edge_case_problems", reached, "site-tests/run_site_checks.py: run_dashboard calls page_edge_problems, not edge_case_problems, which adds the threshold's and the chapters' checks")
        owner = [node for node in ast.walk(harness.functions["run_dashboard"]) if isinstance(node, ast.Subscript) and getattr(node.slice, "value", None) == "owner"]
        self.assertGreaterEqual(len(owner), 2, "site-tests/run_site_checks.py: run_dashboard opens the sections with fixtures[\"sessions\"][\"owner\"] (page_states and page_edge_problems), "
                                "not the staff session it is given: the staff login has no workspace to show")

    def test_the_dashboard_part_runs_every_check_it_has_and_the_forms_part_looks_for_a_sign_out_button_on_every_state(self):
        harness = Harness()
        own = {name for name in harness.functions if name.startswith("dashboard_") and name.endswith("_problems")}
        self.assertTrue(len(own) >= 10, "site-tests/run_site_checks.py: the dashboard part has its checks named dashboard_..._problems (sections, change, sign out, key, revoke, rows, time, billing, error pages, webkit)")
        self.assertEqual(own - harness.checks("run_dashboard"), set(), "site-tests/run_site_checks.py: run_dashboard must run every dashboard_..._problems check")
        self.assertEqual(own & harness.checks("run_forms"), set(), "site-tests/run_site_checks.py: the forms part runs a dashboard check")
        self.assertIn("forms_sign_out_problems", harness.checks("run_forms"),
                      "site-tests/run_site_checks.py: forms_state_problems must run forms_sign_out_problems: the throttled pages and the 403s of a signed-in visitor must not carry the dashboard's Sign out button")
        self.assertNotIn("forms_sign_out_problems", harness.checks("run_dashboard"), "site-tests/run_site_checks.py: forms_sign_out_problems is the forms part's")

    def test_no_dashboard_check_waits_a_fixed_time_and_the_changes_run_again_with_every_frame_held(self):
        harness = Harness()
        for name in sorted(name for name in harness.functions if name.startswith(("dashboard_", "dash_"))):
            for call in (each for each in ast.walk(harness.functions[name]) if isinstance(each, ast.Call)):
                called = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                self.assertNotIn(called, ("wait_for_timeout", "sleep"), f"site-tests/run_site_checks.py: {name} calls {called}, a wait for a fixed time: wait for the state the check reads "
                                 f"(wait_for_state, page.wait_for_function), which stays right with frames 120 ms apart")
        self.assertIn("dashboard_walk_findings", harness.reach("dashboard_slow_frames_problems"), "site-tests/run_site_checks.py: dashboard_slow_frames_problems must repeat the changes in place")
        self.assertIn("FRAME_STALL", harness.reach("dashboard_slow_frames_problems"), "site-tests/run_site_checks.py: dashboard_slow_frames_problems must install FRAME_STALL, which holds each frame SLOW_EVERY_FRAME_MS")
        self.assertIn("SLOW_EVERY_FRAME_MS", harness.names("FRAME_STALL"), "site-tests/run_site_checks.py: FRAME_STALL must hold each frame SLOW_EVERY_FRAME_MS")

    def test_the_two_checks_that_depend_on_the_engine_run_in_webkit_and_the_rest_in_chromium(self):
        harness = Harness()
        self.assertIn("dashboard_webkit_problems", harness.checks("run_dashboard"),
                      "site-tests/run_site_checks.py: run_dashboard must run dashboard_webkit_problems: the times (Date and Intl) and the Sign out button in the phone sheet are the engine's, and iPhones are WebKit")
        self.assertIn("playwright", {argument.arg for argument in harness.functions["run_dashboard"].args.args}, "site-tests/run_site_checks.py: run_dashboard takes playwright, which starts WebKit")

    def test_the_dashboard_job_installs_chromium_and_webkit_and_makes_its_fixtures_after_migrate_and_git_ignores_them(self):
        jobs = workflow_jobs(WORKFLOW.read_text(encoding="utf-8"))
        job = jobs.get("site-dashboard", "")
        self.assertRegex(job, rf"playwright install {re.escape(WITH_DEPS)} chromium webkit\n",
                         "ci.yml: the `site-dashboard` job installs Chromium and WebKit: dashboard_webkit_problems skips itself where WebKit is missing, which would pass CI by not running")
        start = job.find("Start the app")
        order = [job.find(step, start) for step in ("manage.py migrate", "make_staff_session.py", "make_dashboard_fixtures.py", "manage.py runserver")]
        self.assertNotIn(-1, order, "ci.yml: the `site-dashboard` job runs migrate, make_staff_session.py, make_dashboard_fixtures.py and runserver")
        self.assertEqual(order, sorted(order), "ci.yml: the `site-dashboard` job migrates, then makes the staff session and the fixtures, then starts the server")
        self.assertEqual([name for name, body in jobs.items() if "make_dashboard_fixtures.py" in body], ["site-dashboard"], "ci.yml: only the `site-dashboard` job makes the dashboard fixtures")
        self.assertNotIn("make_form_fixtures.py", job, "ci.yml: the dashboard part needs no form fixtures: only the `site-forms` job makes them")
        self.assertIn("site-tests/dashboard-fixtures.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
                      ".gitignore: add site-tests/dashboard-fixtures.json beside site-tests/form-fixtures.json: it holds the keys of signed-in sessions")

    def test_the_docs_say_how_to_run_the_part_and_make_its_fixtures(self):
        agents, workflow = AGENTS.read_text(encoding="utf-8"), WORKFLOW.read_text(encoding="utf-8")
        for words in ("--part dashboard", "make_dashboard_fixtures.py", "dashboard-fixtures.json", "site-dashboard-screenshots"):
            self.assertTrue(words in agents, f"AGENTS.md: \"Site checks\" should mention {words}, where it describes the dashboard part")
        self.assertTrue("--part dashboard " in workflow and "make_dashboard_fixtures.py" in workflow, "ci.yml: the `site-dashboard` job runs --part dashboard after make_dashboard_fixtures.py")


def job_field(body, key):
    """The value of a job-level `key:` (four spaces in) in a job's lines, or None."""
    match = re.search(rf"^    {re.escape(key)}: (.*)$", body, re.M)
    return match.group(1).strip() if match else None


def workflow_steps(body):
    """The steps of a job's lines, each as its text from the `- ` that starts it."""
    steps = re.search(r"^    steps:\n", body, re.M)
    return ["- " + step for step in re.split(r"^      - ", body[steps.end():], flags=re.M)[1:]]


def step_script(step):
    """The script of a step (`run: |` and its lines, or `run: command`), as the shell gets it."""
    match = re.search(r"^        run: \|\n((?:          .*\n|\n)+)", step, re.M)
    if match:
        return textwrap.dedent(match.group(1))
    single = re.search(r"^        run: (?!\|)(.*)$", step, re.M)
    return single.group(1) if single else ""


class RunnerTests(SimpleTestCase):
    """ci.yml runs on GitHub-hosted ubuntu-24.04 runners, one fresh machine per job, side by side. Every message names the file and the fix."""

    longMessage = False

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.jobs = workflow_jobs(self.text)

    def test_every_job_runs_on_ubuntu_24_04(self):
        self.assertLessEqual({"backend", "npm-cli", "python-cli", *SITE_JOBS}, set(self.jobs), ".github/workflows/ci.yml: the backend, npm-cli, python-cli and site* jobs are missing")
        for name, body in self.jobs.items():  # every job, a new one too
            self.assertEqual(job_field(body, "runs-on"), RUNS_ON,
                             f".github/workflows/ci.yml: the `{name}` job's runs-on must be exactly {RUNS_ON}: CI runs on GitHub-hosted runners only")
        self.assertNotRegex(self.text, r"self-hosted|CI_RUNS_ON|veilfile-vm", ".github/workflows/ci.yml: no self-hosted path: every job runs on ubuntu-24.04")

    def test_no_job_uses_port_8000_and_every_site_job_has_a_port_of_its_own(self):
        code = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))  # the comments may name the port
        self.assertIsNone(re.search(r"(?<!\d)8000(?!\d)", code),
                          ".github/workflows/ci.yml: nothing may use port 8000, the Django default: give the job a SITE_PORT of its own (8801 and up)")
        ports = {}
        for job in SITE_JOBS:
            body = self.jobs[job]
            match = re.search(r'^    env:\n(?:      .*\n)*?      SITE_PORT: "(\d+)"$', body, re.M)
            self.assertIsNotNone(match, f'.github/workflows/ci.yml: the `{job}` job needs a job-level `env:` with SITE_PORT: "88xx", the port of its server')
            ports[job] = int(match.group(1))
            self.assertTrue(1024 < ports[job] < 65536 and ports[job] != 8000, f".github/workflows/ci.yml: the `{job}` job's SITE_PORT {ports[job]} must be above 1024 and not 8000")
            # The server, its health check and the harness all take the job's port from SITE_PORT; a port written out would be one the others do not follow.
            self.assertEqual([port for port in re.findall(r"127\.0\.0\.1:(\S*)", body) if not port.startswith("$SITE_PORT")], [],
                             f"ci.yml: the `{job}` job writes a port out: use 127.0.0.1:$SITE_PORT for the server, the health check and --base-url")
            for use in ('runserver "127.0.0.1:$SITE_PORT"', "http://127.0.0.1:$SITE_PORT/healthz", "--base-url http://127.0.0.1:$SITE_PORT "):
                self.assertIn(use, body, f".github/workflows/ci.yml: the `{job}` job should have {use!r}, so that its server, its health check and the harness meet on SITE_PORT")
        self.assertEqual(len(set(ports.values())), len(ports),
                         f".github/workflows/ci.yml: two site jobs share a port ({ports}): each job gets its own, so that a job never depends on the port of another")

    def test_every_site_job_stops_its_server_with_if_always(self):
        for job in SITE_JOBS:
            steps = workflow_steps(self.jobs[job])
            start = next((step for step in steps if "Start the app" in step), "")
            self.assertIn('echo "SERVER_PID=$!" >> "$GITHUB_ENV"', step_script(start),
                          f"ci.yml: the `{job}` job's `Start the app` step must save the server's PID: echo \"SERVER_PID=$!\" >> \"$GITHUB_ENV\" right after it starts runserver")
            last = steps[-1]
            self.assertRegex(last, r"(?m)^        if: always\(\)$", f"ci.yml: the last step of the `{job}` job stops the server and runs `if: always()`, or a failed run leaves it holding the port")
            self.assertIn('kill "$SERVER_PID"', step_script(last), f'ci.yml: the last step of the `{job}` job must run kill "$SERVER_PID"')

    def test_every_job_that_uses_python_gets_it_from_setup_python(self):
        uses_python = re.compile(r"(?m)(?:^|[\s;|&(])(?:python|pip)\s")
        found = {name for name, body in self.jobs.items() if any(uses_python.search(step_script(step)) for step in workflow_steps(body))}
        self.assertEqual(found, {"backend", "python-cli", *SITE_JOBS},
                         f".github/workflows/ci.yml: the jobs that run python or pip are {sorted(found)}: a job that runs either gets setup-python, and goes in this test")
        for name in sorted(found):
            steps = workflow_steps(self.jobs[name])
            first = next(index for index, step in enumerate(steps) if uses_python.search(step_script(step)))
            hosted = [index for index, step in enumerate(steps) if "uses: actions/setup-python@" in step]
            self.assertEqual(len(hosted), 1, f"ci.yml: the `{name}` job needs one setup-python step")
            self.assertLess(hosted[0], first, f"ci.yml: the `{name}` job runs setup-python before the first step that runs python or pip")
            self.assertNotIn("runner.environment", steps[hosted[0]], f"ci.yml: the `{name}` job's setup-python step runs on every run: no runner.environment condition")
            self.assertNotIn("uv venv", self.jobs[name], f"ci.yml: the `{name}` job makes no virtualenv: setup-python is its Python")
            self.assertNotRegex("".join(step_script(step) for step in steps), r"--user|--break-system-packages|\bsudo\b",
                                f"ci.yml: the `{name}` job installs with pip as it is: no --user, --break-system-packages or sudo")
        self.assertEqual(sorted(set(self.jobs) - found), ["changes", "names", "npm-cli"], "ci.yml: the jobs that run no python are `changes`, `names` and `npm-cli`")

    def test_every_site_job_starts_its_server_on_a_throwaway_sqlite_file(self):
        # migrate runs before make_staff_session.py checks the database, so the Start step blanks DATABASE_URL: settings.py then falls back to SQLite, whatever the environment holds.
        for job in SITE_JOBS:
            start = next((step for step in workflow_steps(self.jobs[job]) if "Start the app" in step), "")
            self.assertRegex(start, r'(?m)^          DATABASE_URL: ""', f'.github/workflows/ci.yml: the `{job}` job\'s `Start the app` step needs DATABASE_URL: "" in its env, so that a DATABASE_URL never reaches migrate')

    def test_site_jobs_run_on_pull_requests_whose_changes_can_reach_a_page_and_the_others_run_on_every_push_too(self):
        for job in SITE_JOBS:
            body = self.jobs[job]
            self.assertEqual(job_field(body, "needs"), "changes", f"ci.yml: the `{job}` job needs the `changes` job, which says whether the pull request can change a page")
            condition = job_field(body, "if") or ""
            self.assertIn("github.event_name == 'pull_request'", condition, f"ci.yml: the `{job}` job runs for pull requests only (a push to main was checked on its pull request): add github.event_name == 'pull_request' to its `if:`")
            self.assertIn("needs.changes.outputs.site", condition, f"ci.yml: the `{job}` job's `if:` must read needs.changes.outputs.site, so that a pull request that cannot reach a page skips it")
        for job in ("backend", "npm-cli", "python-cli"):
            self.assertEqual((job_field(self.jobs[job], "if"), job_field(self.jobs[job], "needs")), (None, None),
                             f"ci.yml: the `{job}` job runs on every push to main and every pull request: it takes no `if:` and needs no other job")
        changes = self.jobs["changes"]
        self.assertEqual(job_field(changes, "if"), "github.event_name == 'pull_request'", "ci.yml: the `changes` job runs for pull requests only")
        self.assertIn("fetch-depth: 0", changes, "ci.yml: the `changes` job checks out with fetch-depth: 0, or git diff has no merge base to compare with")
        self.assertIn("site: ${{ steps.paths.outputs.site }}", changes, "ci.yml: the `changes` job outputs site, from the step with id: paths")

    def test_every_playwright_install_passes_with_deps(self):
        installs = [line for line in self.text.splitlines() if "playwright install" in line and not line.lstrip().startswith("#")]
        self.assertEqual(len(installs), len(SITE_JOBS), "ci.yml: each site job has one `playwright install` line")
        for line in installs:
            self.assertIn(WITH_DEPS, line, f"ci.yml: each playwright install line passes {WITH_DEPS}, so that the browsers' system libraries are installed")

    def test_every_site_job_caches_playwrights_browsers_by_its_version(self):
        pinned = re.search(r"^playwright==([\d.]+)$", (ROOT / "site-tests" / "requirements.txt").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(pinned, "site-tests/requirements.txt: pin playwright with `playwright==X.Y.Z` on a line of its own: ci.yml keys the browser cache on it")
        for job in SITE_JOBS:
            steps = workflow_steps(self.jobs[job])
            find = next((step for step in steps if "id: playwright" in step), "")
            self.assertNotIn("runner.environment", find, f"ci.yml: the `{job}` job finds the Playwright version (id: playwright) on every run")
            self.assertIn("sed -n 's/^playwright==//p' site-tests/requirements.txt", find, f"ci.yml: the `{job}` job reads the Playwright version from site-tests/requirements.txt")
            cache = next((step for step in steps if "actions/cache@" in step), "")
            self.assertRegex(cache, r"uses: actions/cache@v\d+\.\d+\.\d+\n", f"ci.yml: the `{job}` job caches Playwright's browsers with actions/cache, pinned to a version like the other actions")
            self.assertNotIn("runner.environment", cache, f"ci.yml: the `{job}` job caches the browsers on every run")
            self.assertIn("path: ~/.cache/ms-playwright", cache, f"ci.yml: the `{job}` job caches ~/.cache/ms-playwright, where Playwright keeps its browsers on Linux")
            self.assertIn("${{ steps.playwright.outputs.version }}", cache, f"ci.yml: the `{job}` job's cache key must include the Playwright version")
            # A cache of Chromium alone must not stand in for Chromium and WebKit: the browsers installed are in the key.
            self.assertEqual("webkit" in cache.split("key:")[1].split("\n")[0], job in WEBKIT_JOBS, f"ci.yml: only the jobs in WEBKIT_JOBS install WebKit, and their cache key says so; no other job's does")
            self.assertLess(steps.index(find), steps.index(cache), f"ci.yml: the `{job}` job finds the version before it restores the cache")

    def test_the_names_job_checks_every_file_against_the_owners_list(self):
        body = self.jobs["names"]
        self.assertIn("secrets.FORBIDDEN_NAMES", body, ".github/workflows/ci.yml: the names job reads the owner's list from the FORBIDDEN_NAMES secret")
        self.assertIn("git grep -l -i -I -F -f", body, ".github/workflows/ci.yml: the names job prints file names only (git grep -l), never a matched line")
        self.assertNotIn("\n    if:", body, ".github/workflows/ci.yml: the names job runs on every push and pull request")
        self.assertIn('-s "$RUNNER_TEMP/names"', body, ".github/workflows/ci.yml: the names job fails when the list holds no names, so an empty pattern file cannot pass")
        self.assertIn("|| status=$?", body, ".github/workflows/ci.yml: the names job captures git grep's exit status, so a git error is not read as no match")

    def test_no_document_still_describes_the_vm(self):
        stale = re.compile(r"self-hosted runner|veilfile-vm|veilfile-ci|CI_RUNS_ON|limactl|one at a time", re.I)
        found = [f"{path.name}:{number}" for path in (AGENTS, ROOT / "README.md", SPEC, WORKFLOW)
                 for number, line in enumerate(path.read_text().splitlines(), 1) if stale.search(line)]
        self.assertFalse(found, f"these lines still describe the old self-hosted CI; CI runs on GitHub-hosted runners (AGENTS.md, Where CI runs): {found}")


class ChangesJobTests(SimpleTestCase):
    """The `changes` job decides, in plain shell, whether a pull request can change a rendered page. The script in ci.yml runs here as the runner runs it
    (bash -e), with a stand-in for `git diff` that lists the paths. Every message names the file and the fix."""

    longMessage = False

    CASES = (
        ("an OAuth-only change reaches the OAuth pages (backend/drop_core/oauth.py is not on the skip list)", ["backend/drop_core/oauth.py"], "true"),
        ("a change to cli/ alone", ["cli/veilfile/client.py", "cli/pyproject.toml"], "false"),
        ("a change to npm/ and plugin/", ["npm/veilfile.js", "plugin/.claude-plugin/plugin.json"], "false"),
        ("a change to the repo-root docs/ and a root markdown file", ["docs/API.md", "docs/superpowers/plans/x.md", "README.md", "AGENTS.md"], "false"),
        ("a change to the backend's own docs, which are pages", ["backend/docs/pages/quickstart.md"], "true"),
        ("a markdown file that is not at the root", ["backend/pages/NOTES.md"], "true"),
        ("a change to ci.yml itself runs everything", [".github/workflows/ci.yml"], "true"),
        ("a change to ci.yml among skippable ones", ["cli/veilfile/client.py", ".github/workflows/ci.yml"], "true"),
        ("a change to release.yml alone", [".github/workflows/release.yml"], "false"),
        ("tests only", ["backend/pages/tests_site_parts.py", "backend/drop_core/tests.py", "backend/drop_core/tests/test_x.py", "backend/tests_root.py"], "false"),
        ("a directory that only starts with tests is not a tests directory", ["backend/pages/tests_data/page.html"], "true"),
        ("the test runner, which is not a test module", ["backend/config/test_runner.py"], "true"),
        ("a template", ["backend/pages/templates/pages/home.html"], "true"),
        ("a static file", ["backend/static/js/shell.js"], "true"),
        ("the dashboard", ["backend/dashboard/views.py"], "true"),
        ("the browser checks themselves", ["site-tests/run_site_checks.py"], "true"),
        ("one path that matters among skippable ones", ["cli/veilfile/client.py", "backend/drop_core/oauth.py", "README.md"], "true"),
        ("a path git quotes (a name with a newline or non-ASCII letters starts with a quote)", ['"cli/caf\\303\\251.py"'], "true"),
        ("no changed paths at all", [], "true"),
    )

    def script(self):
        jobs = workflow_jobs(WORKFLOW.read_text(encoding="utf-8"))
        step = next((step for step in workflow_steps(jobs.get("changes", "    steps:\n      - x\n")) if "id: paths" in step), "")
        self.assertTrue(step_script(step), ".github/workflows/ci.yml: the `changes` job needs a step with `id: paths` and a `run: |` script that writes site=true or site=false to $GITHUB_OUTPUT")
        return step_script(step)

    def run_script(self, script, paths, git_fails=False):
        bash = shutil.which("bash")
        self.assertIsNotNone(bash, "bash is needed to run the `changes` job's script, as the runner does")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            git = tmp / "bin" / "git"
            git.write_text('#!/bin/sh\nprintf \'%s \' "$@" > "$FAKE_GIT_ARGS"\n[ "$1" = diff ] || exit 2\n[ -z "${FAKE_GIT_FAILS:-}" ] || exit 128\n[ -z "$FAKE_CHANGED" ] || printf \'%s\\n\' "$FAKE_CHANGED"\n',
                           encoding="utf-8")
            git.chmod(0o755)
            (tmp / "step.sh").write_text(script, encoding="utf-8")
            env = {"PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", "GITHUB_OUTPUT": str(tmp / "output"), "FAKE_GIT_ARGS": str(tmp / "args"),
                   "BASE_SHA": "a" * 40, "HEAD_SHA": "b" * 40, "FAKE_CHANGED": "\n".join(paths), **({"FAKE_GIT_FAILS": "1"} if git_fails else {})}
            done = subprocess.run([bash, "-e", str(tmp / "step.sh")], env=env, capture_output=True, text=True, timeout=60)
            output = (tmp / "output").read_text(encoding="utf-8") if (tmp / "output").exists() else ""
            args = (tmp / "args").read_text(encoding="utf-8").strip() if (tmp / "args").exists() else ""
            return done, output, args

    def test_each_kind_of_change_gets_the_decision_the_skip_list_gives_it(self):
        script = self.script()
        for what, paths, expected in self.CASES:
            done, output, _ = self.run_script(script, paths)
            self.assertEqual((done.returncode, output), (0, f"site={expected}\n"),
                             f".github/workflows/ci.yml: the `changes` job's script should write site={expected} for {what} ({paths}); it exited {done.returncode} and wrote {output!r}. "
                             f"stderr: {done.stderr.strip()[:300]}")

    def test_a_failed_git_diff_runs_the_site_checks(self):
        done, output, _ = self.run_script(self.script(), ["cli/veilfile/client.py"], git_fails=True)
        self.assertEqual((done.returncode, output), (0, "site=true\n"),
                         ".github/workflows/ci.yml: when git diff fails the `changes` job should write site=true and exit 0 (checks that could not be ruled out run), not fail or skip them")

    def test_the_diff_is_the_pull_requests_own_with_renames_as_two_paths(self):
        _, _, args = self.run_script(self.script(), ["cli/veilfile/client.py"])
        # base...head is what the pull request changes since it branched; --no-renames lists the old path of a moved file too, so that a file moved out of
        # a page's directory into a skipped one still counts as a change to the directory it left.
        self.assertEqual(args, f"diff --name-only --no-renames {'a' * 40}...{'b' * 40}",
                         ".github/workflows/ci.yml: the `changes` job runs git diff --name-only --no-renames \"$BASE_SHA...$HEAD_SHA\"")
