"""The chapters (spec 4.3): real pages on the shell, their words in templates/chapters/, in the site's voice."""
import json
import re
from html import unescape
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from drop_core.plans import PLANS, plan_name
from pages import registry

CHAPTERS = Path(settings.BASE_DIR) / "templates" / "chapters"
WORDS_START = "{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}"
WORDS_END = "{# End of words. #}"
BANNED = ("genuinely", "utilize", "innovative", "innovation", "passionate about", "excited to share",
          "powerful force", "leverage", "great ")


def chapter_pages():
    """The registry pages in the chapter layout: the threshold and every chapter."""
    return [page for page in registry.PAGES if page.layout == "chapter"]


def visible_text(html):
    """The words a visitor reads in <main>, tags removed and spaces collapsed."""
    main = html[html.index("<main"):html.index("</main>")]
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main))).strip()


class ChapterPagesTests(TestCase):
    def test_every_chapter_renders_on_the_shell_in_the_chapter_layout(self):
        self.assertTrue(chapter_pages(), "no registry page has layout='chapter'")
        for page in chapter_pages():
            with self.subTest(page=page.url_name):
                response = self.client.get(reverse(page.url_name))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('data-layout="chapter"', html)
                self.assertIn('<div class="chapter">', html)
                self.assertRegex(html[html.index("<main"):], r"<h1[ >]")

    def test_every_chapter_template_is_rendered_by_a_chapter_page_and_the_other_way_round(self):
        # The words and voice checks read every file in templates/chapters/; this ties those files to the
        # registry, so a template nothing routes to (never rendered, never seen) is a failure, not silence.
        used = set()
        for page in chapter_pages():
            response = self.client.get(reverse(page.url_name))
            used |= {template.name for template in response.templates
                     if template.name and template.name.startswith("chapters/")}
        on_disk = {f"chapters/{path.name}" for path in CHAPTERS.glob("*.html")}
        self.assertEqual(
            used - on_disk, set(),
            "a chapter page renders a template that is not in backend/templates/chapters/")
        self.assertEqual(
            on_disk - used, set(),
            "backend/templates/chapters/ holds a template no chapter page renders: add its route in "
            "config/urls.py and its Page in pages/registry.py (AGENTS.md, 'Edit a chapter's words'), or delete it")

    def test_chapter_templates_mark_their_words_and_keep_the_voice(self):
        templates = sorted(CHAPTERS.glob("*.html"))
        self.assertTrue(templates, "no templates in templates/chapters/")
        for path in templates:
            text = path.read_text(encoding="utf-8")
            where = f"backend/templates/chapters/{path.name}"
            with self.subTest(template=path.name):
                self.assertIn(WORDS_START, text, f"{where}: put the page's words after {WORDS_START}")
                self.assertIn(WORDS_END, text, f"{where}: end the page's words with {WORDS_END}")
                self.assertEqual(text.count(WORDS_START), 1, f"{where}: {WORDS_START} must appear exactly once")
                self.assertEqual(text.count(WORDS_END), 1, f"{where}: {WORDS_END} must appear exactly once")
                self.assertLess(text.index(WORDS_START), text.index(WORDS_END),
                                f"{where}: the words start marker must come before the end marker")
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_a_chapter_names_the_pages_it_changes_to_in_place(self):
        """shell.js changes in place only to these addresses; a link anywhere else is an ordinary load."""
        found = re.search(r'data-layout-paths="([^"]*)"', self.client.get("/about/").content.decode())
        self.assertIsNotNone(found, "a chapter's <body> should carry data-layout-paths")
        self.assertEqual(set(found.group(1).split()), {"/", "/about/", "/how/", "/begin/", "/promises/", "/plans/"})


class AboutTests(TestCase):
    def test_about_is_short_and_says_what_veilfile_is(self):
        """About is short and says what Veilfile is. That it names no person is held by CI's names job (AGENTS.md, Where CI runs), which keeps the names out of this repository."""
        text = visible_text(self.client.get(reverse("about")).content.decode())
        self.assertIn("A quiet place for what your agents make", text)
        self.assertLess(len(text.split()), 300)


class HowAndPromisesTests(TestCase):
    def test_how_it_works_tells_the_life_of_a_file(self):
        text = visible_text(self.client.get(reverse("how")).content.decode())
        for step in ("Made", "Veiled", "Seen", "Let go"):
            self.assertIn(step, text)
        self.assertIn("The 256-bit token in the address is the only key", text)
        self.assertIn("deleted from storage soon after", text)

    def test_promises_say_what_we_keep_and_answer_why_not_a_gist(self):
        text = visible_text(self.client.get(reverse("promises")).content.decode())
        self.assertIn("The link is the only way in", text)
        self.assertIn("File contents are never logged.", text)
        self.assertIn("Why shouldn’t I use a gist?", text)

    def test_the_old_trust_address_moves_permanently_to_promises(self):
        response = self.client.get("/trust/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/promises/")


class PlansTests(TestCase):
    def setUp(self):
        self.html = self.client.get(reverse("plans")).content.decode()
        self.text = visible_text(self.html)

    def test_every_figure_comes_from_plans_py(self):
        for slug, caps in PLANS.items():
            with self.subTest(plan=slug):
                self.assertIn(f"{caps['uploads']:,} uploads a month", self.text)
                self.assertIn(f"Links last up to {caps['ttl_days']} days", self.text)
                self.assertIn(f"{caps['storage_gb']} GB of storage", self.text)
                self.assertIn("$0" if caps["price_usd"] == 0 else f"${caps['price_usd']} a month", self.text)
        self.assertIn("Unlimited keys", self.text)

    def test_each_plan_links_into_sign_up_with_it_chosen(self):
        for slug in PLANS:
            with self.subTest(plan=slug):
                self.assertIn(f'href="/accounts/signup/?plan={slug}"', self.html)

    def test_says_the_price_includes_tax(self):
        self.assertIn("Prices include tax. The price you see is the price you pay.", self.text)

    def product(self):
        """The Product block of Plans' JSON-LD."""
        blocks = [json.loads(block) for block in re.findall(
            r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', self.html, re.DOTALL)]
        return next(block for block in blocks if block["@type"] == "Product")

    def test_structured_data_offers_match_plans_py(self):
        product = self.product()
        self.assertEqual({offer["name"]: offer["price"] for offer in product["offers"]},
                         {plan_name(slug): str(caps["price_usd"]) for slug, caps in PLANS.items()})
        self.assertEqual(product["url"], registry.canonical_url("plans"))
        # Each Offer's description is its card's limits, as the visitor reads them, joined with ", ".
        limits = {name: re.findall(r"<li>([^<]*)</li>", items) for name, items in re.findall(
            r'<h2 class="plan__name">([^<]+)</h2>.*?<ul class="plan__limits">(.*?)</ul>', self.html, re.DOTALL)}
        self.assertEqual(set(limits), {plan_name(slug) for slug in PLANS})
        slugs = {plan_name(slug): slug for slug in PLANS}
        for offer in product["offers"]:
            slug = slugs[offer["name"]]
            with self.subTest(offer=offer["name"]):
                self.assertEqual(offer["url"], f"{registry.SITE_ORIGIN}{reverse('signup')}?plan={slug}")
                self.assertEqual(offer["description"], ", ".join(limits[offer["name"]]))

    def test_every_offer_is_priced_in_dollars(self):
        offers = self.product()["offers"]
        self.assertEqual(len(offers), len(PLANS))
        for offer in offers:
            with self.subTest(offer=offer["name"]):
                self.assertEqual(offer["priceCurrency"], "USD")

    def test_every_plan_says_how_many_keys(self):
        items = re.findall(r"<li>([^<]*)</li>", self.html)  # the plan cards' limits; the menu's items hold links
        for slug, caps in PLANS.items():
            keys = caps["keys"]
            phrase = "Unlimited keys" if keys is None else f"{keys} key" + ("" if keys == 1 else "s")
            with self.subTest(plan=slug, phrase=phrase):
                self.assertIn(phrase, items)  # the whole item, not a substring: "1 keys" contains "1 key"

    def test_the_template_states_no_figures(self):
        # Every chapter template, not only Plans: no price, upload count, link life, storage size or key count as
        # literal text. A figure is a number followed by its unit, with a space or a hyphen between ("90 days",
        # "90-day", "5-key"), singular or plural. Begin's example request (-F "ttl_days=7"), where the number
        # follows the name, is not one and needs no exemption.
        figure = r"\$\d|\b\d[\d,]*[\s-]*(GB|uploads?|days?|keys?)\b"
        for path in sorted(CHAPTERS.glob("*.html")):
            with self.subTest(template=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), figure,
                                    f"backend/templates/chapters/{path.name}: figures come from drop_core/plans.py, through the view")

    def test_the_plan_component_renders_one_card(self):
        from pages.plan_cards import plan_cards

        team = next(card for card in plan_cards() if card["slug"] == "team")
        html = render_to_string("components/plan.html", {"plan": team})
        self.assertIn('<h2 class="plan__name">Team</h2>', html)
        self.assertIn("$4 a month", html)
        self.assertIn('href="/accounts/signup/?plan=team"', html)
        self.assertIn(">Choose Team</a>", html)


AGENT_SETUP = Path(settings.BASE_DIR) / "docs" / "pages" / "agent.md"


def fenced_after(markdown, heading):
    """The first fenced code block after the line '### <heading>' in a Markdown file."""
    start = markdown.index(f"### {heading}\n")
    return re.search(r"```[a-z]*\n(.*?)\n```", markdown[start:], re.DOTALL).group(1)


class BeginTests(TestCase):
    def setUp(self):
        self.html = self.client.get(reverse("begin")).content.decode()
        self.text = visible_text(self.html)
        self.blocks = [unescape(block) for block in re.findall(r"<pre[^>]*><code>(.*?)</code></pre>", self.html, re.DOTALL)]

    def test_mcp_comes_first_with_the_docs_snippet_for_each_client(self):
        markdown = AGENT_SETUP.read_text(encoding="utf-8")
        for client in ("Claude Code", "Codex", "Cursor"):
            with self.subTest(client=client):
                self.assertIn(fenced_after(markdown, client), self.blocks,
                              f"backend/pages/snippets.py: copy the {client} snippet from "
                              f"backend/docs/pages/agent.md exactly")
        self.assertLess(self.text.index("Connect your agent"), self.text.index("Or use the command line"))
        self.assertLess(self.text.index("Or use the command line"), self.text.index("Or plain HTTP"))

    def test_every_snippet_is_a_code_block_with_a_copy_button_named_for_it(self):
        # components/code_block.html, five times: the three clients, the command line and plain HTTP.
        buttons = re.findall(r'<button class="code-block__copy label interactive" type="button" data-copy="([^"]+)" aria-label="([^"]+)">Copy</button>', self.html)
        self.assertEqual(len(buttons), 5, "backend/templates/chapters/begin.html: each of the five snippets is a components/code_block.html")
        self.assertEqual(len({identifier for identifier, _ in buttons}), 5, "backend/templates/chapters/begin.html: the five code blocks need five different ids")
        for identifier, label in buttons:
            with self.subTest(code_block=identifier):
                self.assertTrue(label.startswith("Copy "), f"{label!r}: the name of a Copy button starts with the word it shows (WCAG 2.5.3)")
                self.assertEqual(self.html.count(f'<pre tabindex="0" id="{identifier}">'), 1)
        self.assertEqual(len(self.blocks), 5)
        self.assertEqual(self.html.count("data-copy-status"), 5, "each code block speaks in a status region of its own")

    def test_says_who_signs_in_and_who_takes_a_key(self):
        self.assertIn("Clients that support MCP sign-in ask you to sign in the first time", self.text)
        self.assertIn("your account needs a verified email", self.text)
        self.assertIn("The others take an API key instead", self.text)
        self.assertIn("The snippets below use the key", self.text)

    def test_the_commands_are_in_the_page_without_javascript(self):
        for command in ("npm install -g veilfile", "pip install veilfile", "veilfile login", "veilfile push",
                        "curl -X POST https://veilfile.com/api/v1/artifacts"):
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_points_agents_at_llms_txt(self):
        self.assertIn('href="/llms.txt"', self.html)


class ThresholdTests(TestCase):
    def setUp(self):
        self.response = self.client.get("/")
        self.html = self.response.content.decode()

    def test_the_threshold_is_the_line_and_come_in(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertIn('data-layout="chapter"', self.html)
        self.assertIn("Some files are meant to be seen for a moment,", self.html)
        self.assertIn("by the right eyes, and then let go", self.html)
        self.assertIn('<a class="threshold__enter label interactive" href="/begin/" data-come-in>Come in</a>', self.html)

    def test_the_install_line_waits_behind_come_in(self):
        self.assertIn('<div class="threshold__install" data-install hidden>', self.html)
        self.assertIn('id="threshold-tab-mcp"', self.html)

    def test_the_funnel_rule_sets_no_csrf_or_session_cookie(self):
        # The funnel on / (spec 6.6) is cookie-deduped and never touches the session: no csrftoken (no
        # {% csrf_token %} in the page) and no sessionid. Only the funnel's own cookie may be set.
        self.assertNotIn("csrftoken", self.response.cookies)
        self.assertNotIn("sessionid", self.response.cookies)
        self.assertNotIn("csrfmiddlewaretoken", self.html)

    def test_its_head_comes_from_the_registry(self):
        self.assertIn('<link rel="canonical" href="https://veilfile.com/" data-page-meta>', self.html)
        self.assertNotIn("fonts/newsreader/normal-400-500-latin.woff2", self.html)  # no reading text here

    def test_the_dashboard_goes_home_with_a_full_page_load(self):
        # The mark in the dashboard's corner leads to the threshold. The threshold is in the chapter family and the dashboard in the app
        # family, and shell.js changes in place only to an address in its own page's family (data-layout-paths), so going home is an ordinary load.
        self.client.force_login(get_user_model().objects.create_user("goes-home", "goes-home@example.com"))
        html = self.client.get("/app/").content.decode()
        self.assertEqual(html.count('<a class="lockup label interactive" href="/">'), 1,
                         "backend/templates/shell.html: the mark in the top left leads to the threshold on every page, the dashboard's included")
        paths = re.search(r'data-layout-paths="([^"]*)"', html).group(1).split()
        self.assertNotIn("/", paths, "backend/pages/registry.py: layout_paths('app') must not hold the threshold, or going home from the dashboard "
                         "would be an in-place change that fetches a page of another family")
