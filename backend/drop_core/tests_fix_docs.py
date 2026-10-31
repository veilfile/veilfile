"""Docs-vs-code consistency checks (ops-13, ctr-12, mcp-14, web-12, upl-14).

Plan prices and caps are written by hand in docs, templates and the legal
text. These tests read those files and fail when a stated price or cap
disagrees with ``drop_core.plans.PLANS``, when the docs name a different set
of MCP tools than ``drop_mcp.views.TOOLS``, when a documented error code does
not exist in the code, when a documented ``/api/v1`` path does not resolve, or
when an environment variable the code reads is missing from the README.

Changing a price or cap: edit ``plans.py`` and Stripe, then make these tests
pass by updating every file they name (see AGENTS.md, "Pricing changes").
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from drop_core.plans import ANNUAL_PRICE_USD, PLANS
from drop_mcp.views import TOOLS

REPO = Path(settings.BASE_DIR).parent
BACKEND = Path(settings.BASE_DIR)

# Files that state plan prices or caps. Globs are relative to the repo root.
PLAN_COPY_GLOBS = [
    "README.md",
    "SPEC.md",
    "docs/API.md",
    "docs/llms.txt",
    "backend/docs/pages/*.md",
    "backend/blog/posts/*.md",
    "backend/legal/documents/*.md",
    "backend/templates/**/*.html",
    "backend/drop_core/views.py",
    "backend/pages/registry.py",
    "backend/drop_billing/views.py",
    "backend/drop_core/account.py",
    "backend/drop_billing/account.py",
    "backend/dashboard/billing_page.py",
]

# Files that list the MCP tools or say how many there are.
TOOL_DOC_FILES = [
    "README.md",
    "docs/API.md",
    "docs/llms.txt",
    "backend/docs/pages/agent.md",
    "plugin/skills/share-artifact/SKILL.md",
]

# Docs that document endpoints and error codes.
API_DOC_FILES = ["docs/API.md", "docs/llms.txt", "backend/docs/pages/api.md"]

# Error codes the docs have wrongly used in the past.
BANNED_CODES = ("artifact_not_found", "invalid_base64")

NAME_TO_PLAN = {"Free": "free", "Team": "team", "Team Plus": "team_plus", "Scale": "scale"}
# A table row for a plan billed yearly ("| Team (yearly) | $40/yr | ...") has the plan's caps, which the row must state as they are.
YEARLY_ROW = re.compile(r"\s*\((?:yearly|annual)\)$")
# What follows a yearly price: "$40/yr", "$40/year", "$40 a year", "$40 per year". Such a price is checked against ANNUAL_PRICE_USD,
# not the monthly price_usd.
YEARLY = re.compile(r"(?:/yr|/year|\s+a\s+year|\s+per\s+year)\b")
# The names as a regex alternation, "Team Plus" before "Team" so that the longer name wins.
PLAN_NAMES = "Free|Team Plus|Team|Scale"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _files(globs):
    seen = []
    for pattern in globs:
        seen.extend(sorted(REPO.glob(pattern)))
    return [p for p in seen if p.is_file()]


def _read(path):
    return path.read_text(encoding="utf-8")


def _num(text):
    return int(text.replace(",", ""))


def _named_prices(text):
    """(plan, dollars, yearly) for each price written after a plan's name: "Team $4", "Team ($4/month)", "| Team | $4/mo |",
    "Team **$4/month**", and yearly, "Team $40/yr", "| Team (yearly) | $40/yr |"."""
    price = r"\$(\d+(?:\.\d+)?)"
    for m in re.finditer(r"\b(" + PLAN_NAMES + r")\b[^\n$0-9/]{0,20}" + price, text):
        if " to " in m.group(0):  # a range such as "Free to $12" (the landing description reads "Plans cost $0 to $12 a month.")
            continue
        yield NAME_TO_PLAN[m.group(1)], float(m.group(2)), bool(YEARLY.match(text, m.end()))


def _annual_price_facts(text):
    """(plan, dollars) pairs for the yearly prices stated in ``text``."""
    return [(plan, dollars) for plan, dollars, yearly in _named_prices(text) if yearly]


def _price_facts(text):
    """(plan, dollars) pairs for the monthly prices stated in ``text`` (a yearly one is _annual_price_facts')."""
    facts = []
    price = r"\$(\d+(?:\.\d+)?)"
    facts.extend((plan, dollars) for plan, dollars, yearly in _named_prices(text) if not yearly)
    # "$4 Team / $12 Scale", "$6/mo Team Plus"
    for m in re.finditer(price + r"(?:/mo)?\s+(Team Plus|Team|Scale)\b", text):
        facts.append((NAME_TO_PLAN[m.group(2)], float(m.group(1))))
    # JSON-LD offers: {"name": "Team", "price": "4"}
    for m in re.finditer(r'"name":\s*"(' + PLAN_NAMES + r')"[^}]*?"price":\s*"(\d+)"', text):
        facts.append((NAME_TO_PLAN[m.group(1)], float(m.group(2))))
    return facts


def _blocks(text):
    """Yield (plan, block) for multi-line JSON-LD offers, which name the plan in one line and
    describe it in the next."""
    for m in re.finditer(r'"name":\s*"(' + PLAN_NAMES + r')"(.*?)\}', text, re.S):
        yield NAME_TO_PLAN[m.group(1)], m.group(2)


def _segments(text):
    """Yield (plan, clause): the text after a plan name up to the next plan
    name or the end of the line."""
    yield from _blocks(text)
    for line in text.splitlines():
        marks = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"\b(" + PLAN_NAMES + r")\b", line)]
        for i, (_, end, name) in enumerate(marks):
            stop = marks[i + 1][0] if i + 1 < len(marks) else len(line)
            yield NAME_TO_PLAN[name], line[end:stop]


def _cap_facts(text):
    """(plan, what, value) tuples for caps stated next to a plan name."""
    facts = []
    for plan, clause in _segments(text):
        for m in re.finditer(r"(\d[\d,]*)\s*GB\b", clause):
            facts.append((plan, "storage_gb", _num(m.group(1))))
        for m in re.finditer(r"(\d[\d,]*)\s+uploads\b", clause):
            facts.append((plan, "uploads", _num(m.group(1))))
        for m in re.finditer(r"(\d+)[- ]days?\b", clause):
            facts.append((plan, "ttl_days", _num(m.group(1))))
        for m in re.finditer(r"\b(\d+)\s+(?:API\s+)?keys\b", clause):
            facts.append((plan, "keys", _num(m.group(1))))
        if re.search(r"\bunlimited\b[^.|\n]{0,12}\bkeys\b", clause):
            facts.append((plan, "keys", None))
    return facts


def _table_facts(text):
    """Cap facts from Markdown pricing tables (header row names the columns)."""
    facts = []
    columns = None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            columns = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if lowered and lowered[0] == "plan":
            columns = {}
            for i, c in enumerate(lowered):
                if c.startswith("uploads"):
                    columns["uploads"] = i
                elif "ttl" in c:
                    columns["ttl_days"] = i
                elif c.startswith("storage"):
                    columns["storage_gb"] = i
                elif "keys" in c:
                    columns["keys"] = i
            continue
        name = YEARLY_ROW.sub("", cells[0]) if cells else ""
        if columns and name in NAME_TO_PLAN:
            plan = NAME_TO_PLAN[name]
            for what, i in columns.items():
                if i >= len(cells):
                    continue
                m = re.match(r"(\d[\d,]*)", cells[i])
                if m:
                    facts.append((plan, what, _num(m.group(1))))
                elif what == "keys" and cells[i].lower() == "unlimited":
                    facts.append((plan, "keys", None))
    return facts


def _expected(plan, what):
    caps = PLANS[plan]
    return caps[what]


class PlanCopyMatchesPlansTests(SimpleTestCase):
    """ops-13: stated prices and caps must equal PLANS."""

    def test_agents_md_lists_every_file_the_price_test_scans(self):
        # The pricing recipe is the checklist an agent follows; it must name every file this test reads.
        section = _read(REPO / "AGENTS.md").split("## Pricing changes", 1)[1].split("\n## ", 1)[0]
        for glob in PLAN_COPY_GLOBS:
            with self.subTest(glob=glob):
                self.assertIn(f"`{glob}`", section,
                              f"AGENTS.md, Pricing changes: add `{glob}` to the list of files to update")

    def test_the_business_documents_live_in_the_ops_repository(self):
        for path in ("docs/stripe-live-launch-runbook.md", "docs/superpowers/plans/2026-10-05-repricing-storage-caps.md"):
            self.assertFalse((REPO / path).exists(), f"{path}: business documents live in the private ops repository, veilfile/ops, not in this public one")
        self.assertIn("`veilfile/ops`", (REPO / "AGENTS.md").read_text(), "AGENTS.md: say that the production recipes are in the private ops repository, veilfile/ops")

    def test_prices_match_plans(self):
        found = 0
        for path in _files(PLAN_COPY_GLOBS):
            for plan, dollars in _price_facts(_read(path)):
                found += 1
                self.assertEqual(
                    dollars,
                    float(PLANS[plan]["price_usd"]),
                    f"{path.relative_to(REPO)} states {plan} = ${dollars:g}, "
                    f"but plans.py says ${PLANS[plan]['price_usd']}",
                )
        # Guard against the regexes silently matching nothing.
        self.assertGreaterEqual(found, 25)  # 29 once the Stripe runbook moved to the private ops repository (it held 6)

    def test_yearly_prices_match_plans(self):
        found = 0
        for path in _files(PLAN_COPY_GLOBS):
            for plan, dollars in _annual_price_facts(_read(path)):
                found += 1
                self.assertIn(plan, ANNUAL_PRICE_USD, f"{path.relative_to(REPO)} states a yearly price for {plan}, which has none in plans.py")
                self.assertEqual(
                    dollars,
                    float(ANNUAL_PRICE_USD[plan]),
                    f"{path.relative_to(REPO)} states {plan} = ${dollars:g} a year, "
                    f"but plans.py (ANNUAL_PRICE_USD) says ${ANNUAL_PRICE_USD[plan]}",
                )
        # Guard against the regexes silently matching nothing: the README, SPEC, API.md, llms.txt and How it works each state all three.
        self.assertGreaterEqual(found, 15)

    def test_every_plan_table_lists_every_yearly_price(self):
        for rel in ("README.md", "docs/API.md", "backend/docs/pages/how-it-works.md"):
            text = _read(REPO / rel)
            facts = _annual_price_facts(text)
            for plan, dollars in ANNUAL_PRICE_USD.items():
                self.assertIn((plan, float(dollars)), facts, f"{rel}: the plan table is missing the yearly row for {plan}")

    def test_caps_match_plans(self):
        found = 0
        for path in _files(PLAN_COPY_GLOBS):
            text = _read(path)
            for plan, what, value in _cap_facts(text) + _table_facts(text):
                found += 1
                expected = PLANS[plan][what]
                self.assertEqual(
                    value,
                    expected,
                    f"{path.relative_to(REPO)} states {plan} {what} = {value}, "
                    f"but plans.py says {expected}",
                )
        self.assertGreaterEqual(found, 60)

    def test_every_core_copy_file_states_something(self):
        # A file that should carry plan copy but yields no facts means its
        # wording changed shape and this test no longer covers it.
        for rel in [
            "README.md",
            "SPEC.md",
            "docs/API.md",
            "docs/llms.txt",
            "backend/docs/pages/how-it-works.md",
            "backend/legal/documents/terms-of-service.md",
            "backend/drop_core/views.py",
            "backend/pages/registry.py",
        ]:
            text = _read(REPO / rel)
            facts = _price_facts(text) + _cap_facts(text) + _table_facts(text)
            self.assertTrue(facts, f"{rel}: no plan prices or caps recognised")

    def test_every_plan_table_lists_every_plan(self):
        for rel in ("README.md", "docs/API.md", "backend/docs/pages/how-it-works.md"):
            facts = _table_facts(_read(REPO / rel))
            for plan in PLANS:
                for what in ("uploads", "ttl_days", "storage_gb", "keys"):
                    self.assertIn(
                        (plan, what, PLANS[plan][what]),
                        facts,
                        f"{rel}: the plan table is missing {plan}/{what}",
                    )

    def test_a_yearly_price_is_read_as_yearly_and_its_row_as_the_plans_caps(self):
        text = ("| Plan | Price | Storage | API keys |\n| Team (yearly) | $40/yr | 25 GB | 5 |\n"
                "Team Plus $60/yr, Scale ($120 a year), Team $4/mo, Scale $120 per year, Team **$40/year**")
        self.assertEqual(_annual_price_facts(text), [("team", 40.0), ("team_plus", 60.0), ("scale", 120.0), ("scale", 120.0), ("team", 40.0)])
        self.assertEqual(_price_facts(text), [("team", 4.0)])
        self.assertEqual(_table_facts(text), [("team", "storage_gb", 25), ("team", "keys", 5)])

    def test_team_plus_is_read_as_its_own_plan(self):
        # "Team" alone must not swallow "Team Plus", or every Team Plus figure would be read as Team's.
        text = "| Plan | Price | Storage |\n| Team Plus | $6/mo | 40 GB |\nTeam Plus ($6/month)\n$6/mo Team Plus\nTeam $4"
        self.assertEqual(_price_facts(text), [("team_plus", 6.0), ("team_plus", 6.0), ("team", 4.0), ("team_plus", 6.0)])
        self.assertEqual(_table_facts(text), [("team_plus", "storage_gb", 40)])


class McpToolDocsTests(SimpleTestCase):
    """mcp-14 / ctr-18: docs name exactly the tools the server exposes."""

    def test_docs_mention_every_tool(self):
        names = [t["name"] for t in TOOLS]
        for path in _files(TOOL_DOC_FILES):
            text = _read(path)
            for name in names:
                self.assertIn(name, text, f"{path.relative_to(REPO)} never mentions {name}")

    def test_tool_counts_in_docs(self):
        for path in _files(TOOL_DOC_FILES + ["SPEC.md", "docs/REGISTRIES.md", "backend/docs/pages/*.md"]):
            for m in re.finditer(
                r"\b(one|two|three|four|five|six)\s+(?:MCP\s+)?tools\b", _read(path), re.I
            ):
                self.assertEqual(
                    NUMBER_WORDS[m.group(1).lower()],
                    len(TOOLS),
                    f"{path.relative_to(REPO)} says '{m.group(0)}' but the server has {len(TOOLS)} tools",
                )

    def test_docs_do_not_name_unknown_tools(self):
        names = {t["name"] for t in TOOLS}
        pattern = re.compile(r"`((?:[a-z]+_)+(?:artifact|artifacts|upload_url))`")
        for path in _files(TOOL_DOC_FILES):
            for m in pattern.finditer(_read(path)):
                self.assertIn(m.group(1), names, f"{path.relative_to(REPO)}: unknown tool {m.group(1)}")


class ErrorCodeDocsTests(SimpleTestCase):
    """ctr-12 / web-12 / upl-14: documented error codes exist in the code."""

    @staticmethod
    def _source():
        parts = []
        for path in BACKEND.rglob("*.py"):
            name = path.name
            if "migrations" in path.parts or name.startswith("test") or name == "tests.py":
                continue
            parts.append(path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_banned_codes_are_gone(self):
        for path in _files(API_DOC_FILES + TOOL_DOC_FILES + ["README.md"]):
            text = _read(path)
            for code in BANNED_CODES:
                self.assertNotIn(code, text, f"{path.relative_to(REPO)} documents {code}, which the server never sends")

    def test_documented_codes_exist_in_source(self):
        source = self._source()
        checked = 0
        for rel in ("docs/API.md", "backend/docs/pages/api.md"):
            for line in _read(REPO / rel).splitlines():
                m = re.match(r"\|\s*\d{3}\s*\|\s*`([a-z_]+)`\s*\|", line)
                if not m:
                    continue
                code = m.group(1)
                checked += 1
                self.assertTrue(
                    f'"{code}"' in source or f"'{code}'" in source,
                    f"{rel} documents error code {code}, which no source file emits",
                )
        self.assertGreaterEqual(checked, 15)

    def test_every_upload_error_code_is_documented(self):
        from drop_core.views import ERROR_STATUS

        text = _read(REPO / "docs/API.md")
        for code in ERROR_STATUS:
            self.assertIn(f"`{code}`", text, f"docs/API.md never documents {code}")


class DocumentedPathsResolveTests(SimpleTestCase):
    """bill-13: every endpoint a doc names must exist."""

    def test_documented_api_paths_resolve(self):
        pattern = re.compile(r"`?(?:GET|POST|PUT|DELETE|PATCH)\s+(/api/v1/[A-Za-z0-9_/<>\-]*)`?")
        checked = 0
        for path in _files(API_DOC_FILES):
            for m in pattern.finditer(_read(path)):
                url = m.group(1)
                if url.endswith("/*") or "*" in url:
                    continue
                checked += 1
                ok = False
                # <id> is a UUID for artifacts and an integer for keys.
                for ident in ("1", "00000000-0000-0000-0000-000000000000"):
                    candidate = re.sub(r"<[a-z_]+>", lambda g: ident if g.group(0) == "<id>" else "tok", url)
                    for variant in (candidate, candidate.rstrip("/") + "/"):
                        try:
                            resolve(variant)
                            ok = True
                        except Resolver404:
                            continue
                self.assertTrue(ok, f"{path.relative_to(REPO)} documents {m.group(0)}, which does not resolve")
        self.assertGreaterEqual(checked, 15)

    def test_removed_billing_endpoint_not_documented(self):
        for path in _files(API_DOC_FILES):
            self.assertNotIn("/billing/subscription", _read(path), path.name)


class EnvVarDocsTests(SimpleTestCase):
    """deploy-13: every deployment env var the code reads is in the README."""

    PREFIXES = ("DJANGO_", "VEILFILE_", "DROP_", "STRIPE_", "AWS_", "EMAIL_", "GUNICORN_")
    # Set by Django/the harness, not operator settings.
    IGNORE = {"DJANGO_SETTINGS_MODULE"}

    def _names(self):
        names = set()
        pattern = re.compile(r"""environ(?:\.get|\.setdefault|\.pop)?\(\s*["']([A-Z][A-Z0-9_]+)["']|environ\[\s*["']([A-Z][A-Z0-9_]+)["']|getenv\(\s*["']([A-Z][A-Z0-9_]+)["']""")
        for path in BACKEND.rglob("*.py"):
            if "migrations" in path.parts or path.name.startswith("test") or path.name == "tests.py":
                continue
            for m in pattern.finditer(path.read_text(encoding="utf-8")):
                names.add(next(g for g in m.groups() if g))
        text = _read(BACKEND / "entrypoint.sh")
        names.update(re.findall(r"\$\{([A-Z][A-Z0-9_]+)[:}]", text))
        return {
            n for n in names
            if n.startswith(self.PREFIXES) or n in {"DATABASE_URL", "PORT", "WEB_CONCURRENCY", "SKIP_MIGRATE"}
        } - self.IGNORE

    def test_readme_lists_every_env_var(self):
        readme = _read(REPO / "README.md")
        names = self._names()
        self.assertIn("VEILFILE_SAFETY_MODE", names)  # the scan itself works
        missing = sorted(n for n in names if n not in readme)
        self.assertEqual(missing, [], f"README.md env table is missing: {missing}")


class LicenseTests(SimpleTestCase):
    def test_the_root_license_reserves_all_rights_and_the_clis_stay_mit(self):
        notice = (REPO / "LICENSE").read_text()
        self.assertIn("All rights reserved.", notice, "LICENSE: the server code is all rights reserved; only the owner changes that")
        self.assertIn("cli/", notice)
        self.assertIn("npm/", notice)
        self.assertIn("MIT License", notice, "LICENSE: say that the CLIs are under the MIT License")
        for path in ("cli/LICENSE", "npm/LICENSE"):
            self.assertTrue((REPO / path).read_text().startswith("MIT License"), f"{path}: the CLIs stay MIT")
        self.assertIn("All rights are reserved", (REPO / "README.md").read_text(), "README.md: say the code is all rights reserved")


class ApprovedWordsInDocsTests(SimpleTestCase):
    """docs/API.md quotes each tool's title and description as tools/list sends them."""

    def test_api_doc_quotes_every_tool_title_and_description(self):
        text = _read(REPO / "docs/API.md")
        for tool in TOOLS:
            self.assertIn(tool["description"], text, f"docs/API.md does not quote the {tool['name']} description")
            self.assertIn(tool["title"], text, f"docs/API.md does not name the title of {tool['name']}")
            for prop, spec in tool["inputSchema"]["properties"].items():
                escaped = spec["description"].replace('"', '\\"')
                self.assertTrue(
                    spec["description"] in text or escaped in text,
                    f"docs/API.md does not quote {tool['name']}.{prop}",
                )
