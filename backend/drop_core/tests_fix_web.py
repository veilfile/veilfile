"""Regression tests for the web cluster fixes: sessionless funnel
tracking and pruning (web-4, ops-10), robots.txt (web-6), resilient
blog/docs loading (web-7), a content guard for raw HTML (web-2), the
dashboard CSRF cookie (web-11), optional artifact-list pagination
(web-10), and heading ids, code blocks and section lists for the
reading pages."""

import io
import os
import re
import shutil
import tempfile
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib import robotparser

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core import funnel
from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    FunnelEvent,
    get_or_create_workspace,
)
from drop_core.services import mint_upload_url

User = get_user_model()

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

BOT_UAS = [
    "",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.1; +https://openai.com/gptbot)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36",
    "curl/8.7.1",
    "python-requests/2.32.3",
    "Go-http-client/1.1",
]


def _browser():
    return Client(HTTP_USER_AGENT=BROWSER_UA)


class FunnelSessionlessTests(TestCase):
    """web-4 / ops-10: funnel tracking must not create database sessions,
    must not count bots or HEAD requests, and must not touch the session
    on credential-authenticated agent traffic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-fixweb-")
        # Restore (not unset) the runner's DROP_LOCAL_DIR afterwards, so later
        # tests never fall back to backend/media/artifacts.
        local_dir = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir})
        local_dir.start()
        self.addCleanup(local_dir.stop)
        self.user = User.objects.create_user(username="funnel", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _landing_count(self):
        return FunnelEvent.objects.filter(stage=funnel.LANDING_VISIT).count()

    def test_anonymous_landing_creates_no_session_and_counts_once(self):
        c = _browser()
        sessions = Session.objects.count()
        response = c.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(Session.objects.count(), sessions)
        self.assertEqual(self._landing_count(), 1)
        # The same browser is deduped by the funnel cookie alone.
        c.get("/")
        c.get("/")
        self.assertEqual(self._landing_count(), 1)
        self.assertEqual(Session.objects.count(), sessions)
        # A different browser is a new visit.
        _browser().get("/")
        self.assertEqual(self._landing_count(), 2)

    def test_funnel_cookie_is_browser_session_scoped_and_httponly(self):
        response = _browser().get("/")
        cookie = response.cookies[funnel.COOKIE_NAME]
        self.assertEqual(cookie["max-age"], "")
        self.assertEqual(cookie["expires"], "")
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        event = FunnelEvent.objects.get(stage=funnel.LANDING_VISIT)
        self.assertRegex(event.anonymous_id, r"^[0-9a-f]{32}$")
        self.assertTrue(cookie.value.startswith(event.anonymous_id + "."))

    def test_bots_and_scripts_are_not_counted(self):
        sessions = Session.objects.count()
        for ua in BOT_UAS:
            with self.subTest(ua=ua):
                response = Client(HTTP_USER_AGENT=ua).get("/")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("sessionid", response.cookies)
                self.assertNotIn(funnel.COOKIE_NAME, response.cookies)
        self.assertEqual(self._landing_count(), 0)
        self.assertEqual(Session.objects.count(), sessions)

    def test_real_browsers_are_counted(self):
        for ua in [
            BROWSER_UA,
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
        ]:
            Client(HTTP_USER_AGENT=ua).get("/")
        self.assertEqual(self._landing_count(), 3)

    def test_head_requests_are_not_counted(self):
        sessions = Session.objects.count()
        response = _browser().head("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)
        self.assertNotIn(funnel.COOKIE_NAME, response.cookies)
        self.assertEqual(self._landing_count(), 0)
        self.assertEqual(Session.objects.count(), sessions)

    def test_tampered_cookie_is_replaced_not_stored(self):
        c = _browser()
        c.cookies[funnel.COOKIE_NAME] = "<script>.ls"
        response = c.get("/")
        self.assertEqual(response.status_code, 200)
        event = FunnelEvent.objects.get(stage=funnel.LANDING_VISIT)
        self.assertRegex(event.anonymous_id, r"^[0-9a-f]{32}$")
        self.assertIn(funnel.COOKIE_NAME, response.cookies)

    def test_signup_page_creates_no_session_and_links_completion(self):
        c = _browser()
        c.get("/")
        sessions = Session.objects.count()
        response = c.get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(Session.objects.count(), sessions)
        c.get("/accounts/signup/")
        started = FunnelEvent.objects.get(stage=funnel.SIGNUP_STARTED)
        landing = FunnelEvent.objects.get(stage=funnel.LANDING_VISIT)
        self.assertEqual(started.anonymous_id, landing.anonymous_id)
        response = c.post(
            "/accounts/signup/",
            {
                "username": "newdev",
                "password1": "pw12345678",
                "password2": "pw12345678",
                "email": "newdev@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        completed = FunnelEvent.objects.get(stage=funnel.SIGNUP_COMPLETED)
        self.assertEqual(completed.anonymous_id, started.anonymous_id)
        self.assertEqual(completed.workspace.owner.username, "newdev")

    def test_signup_page_does_not_count_bots(self):
        response = Client(HTTP_USER_AGENT=BOT_UAS[1]).get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FunnelEvent.objects.filter(stage=funnel.SIGNUP_STARTED).exists())

    def test_api_key_upload_touches_no_session(self):
        client = APIClient()
        sessions = Session.objects.count()
        response = client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")},
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("sessionid", response.cookies)
        self.assertNotIn(funnel.COOKIE_NAME, response.cookies)
        self.assertEqual(Session.objects.count(), sessions)
        event = FunnelEvent.objects.get(stage=funnel.UPLOAD_COMPLETED)
        self.assertEqual(event.workspace, self.workspace)
        self.assertEqual(event.anonymous_id, "")

    def test_one_time_upload_url_touches_no_session(self):
        pending = mint_upload_url(
            workspace=self.workspace, api_key=self.key, filename="note.txt"
        )
        sessions = Session.objects.count()
        response = Client().put(
            f"/api/v1/uploads/{pending.token}",
            data=b"hello",
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(Session.objects.count(), sessions)
        self.assertTrue(
            FunnelEvent.objects.filter(
                stage=funnel.UPLOAD_COMPLETED, workspace=self.workspace
            ).exists()
        )

    def test_dashboard_key_creation_still_recorded(self):
        EmailVerification.objects.create(
            user=self.user,
            email="funnel@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.workspace.plan = "team"
        self.workspace.save()
        client = APIClient()
        client.force_login(self.user)
        response = client.post(
            "/api/v1/keys/", {"name": "ci"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            FunnelEvent.objects.filter(
                stage=funnel.KEY_ISSUED, workspace=self.workspace
            ).exists()
        )

    def test_record_failure_is_logged_not_raised(self):
        with mock.patch.object(
            FunnelEvent.objects, "create", side_effect=RuntimeError("db down")
        ):
            with self.assertLogs("drop_core.funnel", level="ERROR") as logs:
                response = _browser().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("landing_visit", "\n".join(logs.output))

    def test_second_upload_survives_artifact_expiry(self):
        # upload_number comes from the live artifact count: after the
        # first artifact is swept, the second upload is numbered 1 again.
        for _ in range(2):
            FunnelEvent.objects.create(
                stage=funnel.UPLOAD_COMPLETED,
                workspace=self.workspace,
                upload_number=1,
            )
        summary = {row["stage"]: row for row in funnel.funnel_summary()}
        self.assertEqual(summary["first_upload"]["count"], 1)
        self.assertEqual(summary["second_upload"]["count"], 1)


class FunnelPruneTests(TestCase):
    """ops-10: FunnelEvent rows are pruned after a retention window."""

    def _event(self, age_days):
        event = FunnelEvent.objects.create(stage=funnel.LANDING_VISIT, anonymous_id="a" * 32)
        FunnelEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return event

    def test_prune_deletes_only_events_past_default_retention(self):
        old = self._event(400)
        recent = self._event(10)
        out = io.StringIO()
        call_command("prune_funnel_events", stdout=out)
        self.assertFalse(FunnelEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(FunnelEvent.objects.filter(pk=recent.pk).exists())
        self.assertIn("pruned=1 days=365", out.getvalue())

    def test_days_option_and_env_override(self):
        event = self._event(20)
        call_command("prune_funnel_events", "--days", "30", stdout=io.StringIO())
        self.assertTrue(FunnelEvent.objects.filter(pk=event.pk).exists())
        with mock.patch.dict(os.environ, {funnel.RETENTION_ENV: "10"}):
            call_command("prune_funnel_events", stdout=io.StringIO())
        self.assertFalse(FunnelEvent.objects.filter(pk=event.pk).exists())

    def test_bad_env_value_falls_back_to_default(self):
        event = self._event(20)
        with mock.patch.dict(os.environ, {funnel.RETENTION_ENV: "soon"}):
            call_command("prune_funnel_events", stdout=io.StringIO())
        self.assertTrue(FunnelEvent.objects.filter(pk=event.pk).exists())

    def test_nonpositive_days_refused(self):
        # CommandError: manage.py exits non-zero, so a scheduler notices.
        event = self._event(400)
        for days in ("0", "-5"):
            with self.subTest(days=days):
                with self.assertRaisesMessage(CommandError, "--days must be at least 1"):
                    call_command(
                        "prune_funnel_events", "--days", days, stdout=io.StringIO()
                    )
                self.assertTrue(FunnelEvent.objects.filter(pk=event.pk).exists())


class RobotsTxtTests(TestCase):
    """web-6: no crawler group may allow private artifact links or the
    app/API/auth surfaces; public pages stay crawlable. Fetchers acting for
    a person may open a /a/ link that person handed them."""

    PRIVATE = ["/a/", "/app/", "/api/", "/o/", "/mcp", "/device", "/accounts/"]
    USER_FETCHERS = {"chatgpt-user", "claude-user", "perplexity-user"}

    def _robots(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_every_group_disallows_private_paths(self):
        groups = []
        for line in self._robots().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            field, _sep, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if not groups or groups[-1]["rules"]:
                    groups.append({"agents": [], "rules": []})
                groups[-1]["agents"].append(value)
            elif field in ("allow", "disallow"):
                groups[-1]["rules"].append((field, value))
        self.assertTrue(groups)
        for group in groups:
            with self.subTest(agents=group["agents"]):
                disallowed = {v for f, v in group["rules"] if f == "disallow"}
                user_fetchers = {a.lower() for a in group["agents"]} <= self.USER_FETCHERS
                for path in self.PRIVATE:
                    if path == "/a/" and user_fetchers:
                        self.assertNotIn(path, disallowed)
                    else:
                        self.assertIn(path, disallowed)

    def test_user_fetchers_may_open_artifact_links_but_not_private_surfaces(self):
        parser = robotparser.RobotFileParser()
        parser.parse(self._robots().splitlines())
        for agent in ("ChatGPT-User", "Claude-User", "Perplexity-User"):
            with self.subTest(agent=agent):
                self.assertTrue(parser.can_fetch(agent, "/a/" + "x" * 43))
                for path in ("/app/", "/api/v1/artifacts/", "/o/authorize/", "/device"):
                    self.assertFalse(parser.can_fetch(agent, path))

    def test_crawlers_blocked_from_private_paths_and_allowed_public(self):
        parser = robotparser.RobotFileParser()
        parser.parse(self._robots().splitlines())
        agents = [
            "*",
            "GPTBot",
            "ClaudeBot",
            "PerplexityBot",
            "Google-Extended",
            "Googlebot",
            "bingbot",
        ]
        blocked = [
            "/a/" + "x" * 43,
            "/app/",
            "/api/v1/artifacts/",
            "/o/authorize/",
            "/mcp",
            "/device",
            "/device/approve",
            "/accounts/login/",
        ]
        public = [
            "/",
            "/docs/",
            "/docs/quickstart/",
            "/blog/",
            "/blog/introducing-veilfile/",
            "/plans/",
            "/trust/",
            "/about/",
            "/privacy/",
            "/llms.txt",
            "/sitemap.xml",
        ]
        for agent in agents:
            for path in blocked:
                with self.subTest(agent=agent, path=path):
                    self.assertFalse(parser.can_fetch(agent, "https://veilfile.com" + path))
            for path in public:
                with self.subTest(agent=agent, path=path):
                    self.assertTrue(parser.can_fetch(agent, "https://veilfile.com" + path))


class _ContentDirMixin:
    """Temp copies of the real content dirs, patched into the loaders."""

    def _tmpdir(self, src):
        tmp = Path(tempfile.mkdtemp(prefix="veilfile-content-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for path in Path(src).glob("*.md"):
            shutil.copy(path, tmp / path.name)
        return tmp


class BlogLoaderResilienceTests(_ContentDirMixin, TestCase):
    """web-7: one bad post is skipped with a warning; the blog, every
    other post, the feed and the sitemap stay up."""

    PAGES = ["/blog/", "/blog/introducing-veilfile/", "/blog/feed.xml", "/sitemap.xml"]

    def setUp(self):
        from blog import loader

        self.loader = loader
        self.posts_dir = self._tmpdir(loader.POSTS_DIR)
        patcher = mock.patch.object(loader, "POSTS_DIR", self.posts_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name, text):
        (self.posts_dir / name).write_text(text, encoding="utf-8")

    def _assert_skipped(self, name):
        with self.assertLogs("blog.loader", level="WARNING") as logs:
            slugs = [p.slug for p in self.loader.load_posts()]
        self.assertIn(name, "\n".join(logs.output))
        self.assertEqual(slugs, ["introducing-veilfile"])
        for url in self.PAGES:
            with self.subTest(url=url), self.assertLogs("blog.loader", "WARNING"):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_malformed_date_is_skipped(self):
        self._write(
            "bad-date.md",
            "---\ntitle: Bad\ndate: 2026-10-7\nslug: bad-date\ndescription: x\n---\n\nBody.\n",
        )
        self._assert_skipped("bad-date.md")

    def test_missing_title_is_skipped(self):
        self._write("no-title.md", "---\ndate: 2026-01-01\nslug: no-title\n---\n\nBody.\n")
        self._assert_skipped("no-title.md")

    def test_invalid_slug_is_skipped(self):
        self._write(
            "bad-slug.md",
            "---\ntitle: Bad slug\ndate: 2026-01-01\nslug: has space\n---\n\nBody.\n",
        )
        self._assert_skipped("bad-slug.md")

    def test_non_utf8_file_is_skipped(self):
        (self.posts_dir / "binary.md").write_bytes(
            b"---\ntitle: \xff\xfe\ndate: 2026-01-01\nslug: binary\n---\n"
        )
        self._assert_skipped("binary.md")

    def test_duplicate_slug_is_skipped(self):
        self._write(
            "zz-duplicate.md",
            "---\ntitle: Dupe\ndate: 2026-01-01\nslug: introducing-veilfile\n---\n\nDupe.\n",
        )
        self._assert_skipped("zz-duplicate.md")
        with self.assertLogs("blog.loader", "WARNING"):
            response = self.client.get("/blog/introducing-veilfile/")
        self.assertNotContains(response, "Dupe.")


class DocsLoaderResilienceTests(_ContentDirMixin, TestCase):
    """web-7 (docs side): a bad page is skipped with a warning and the
    other docs pages keep rendering."""

    def setUp(self):
        from docs import loader

        self.loader = loader
        self.pages_dir = self._tmpdir(loader.PAGES_DIR)
        patcher = mock.patch.object(loader, "PAGES_DIR", self.pages_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _assert_skipped(self, name):
        with self.assertLogs("docs.loader", level="WARNING") as logs:
            pages = self.loader.load_pages()
        self.assertIn(name, "\n".join(logs.output))
        self.assertEqual(len(pages), 5)
        for url in ["/docs/", "/docs/quickstart/", "/docs/api/"]:
            with self.subTest(url=url), self.assertLogs("docs.loader", "WARNING"):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_invalid_slug_is_skipped(self):
        (self.pages_dir / "bad.md").write_text(
            "---\ntitle: Bad\nslug: not a slug\norder: 9\n---\n\nBody.\n",
            encoding="utf-8",
        )
        self._assert_skipped("bad.md")

    def test_bad_order_is_skipped(self):
        (self.pages_dir / "bad.md").write_text(
            "---\ntitle: Bad\nslug: bad\norder: second\n---\n\nBody.\n",
            encoding="utf-8",
        )
        self._assert_skipped("bad.md")

    def test_non_utf8_file_is_skipped(self):
        (self.pages_dir / "bad.md").write_bytes(b"---\ntitle: \xff\nslug: bad\n---\n")
        self._assert_skipped("bad.md")


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    handle_startendtag = handle_starttag


_BANNED_TAGS = {"script", "iframe", "object", "embed", "base", "form", "meta"}
_URL_ATTRS = ("href", "src", "action", "formaction")
_BANNED_SCHEMES = ("javascript:", "data:", "vbscript:")


def _active_html(html):
    """Active content in ``html``: banned tags, on* event-handler
    attributes and javascript:/data:/vbscript: URLs, in document order."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    found = []
    for tag, attrs in parser.tags:
        if tag in _BANNED_TAGS:
            found.append(f"<{tag}>")
        for attr, value in attrs:
            if attr.startswith("on"):
                found.append(f"{tag}[{attr}]")
            elif attr in _URL_ATTRS and (value or "").strip().lower().startswith(
                _BANNED_SCHEMES
            ):
                found.append(f"{tag}[{attr}]")
    return found


class ContentGuardTests(TestCase):
    """CI guard for the shipped content: every file loads cleanly (no
    skipped files, no duplicate slugs) and no rendered page carries
    script, event-handler attributes or javascript: URLs (web-2: the
    markdown renderer passes raw HTML through, so this is the check)."""

    def test_every_post_and_page_loads_without_warnings(self):
        from blog import loader as blog_loader
        from docs import loader as docs_loader

        with self.assertNoLogs("blog.loader", level="WARNING"):
            blog_loader.load_posts()
        with self.assertNoLogs("docs.loader", level="WARNING"):
            pages = docs_loader.load_pages()
        self.assertEqual(len(pages), len(list(docs_loader.PAGES_DIR.glob("*.md"))))

    def _rendered_content(self):
        from blog import loader as blog_loader
        from blog.render import render_reading
        from docs import loader as docs_loader
        from legal.views import DOCUMENTS, DOCUMENTS_DIR

        for path in sorted(blog_loader.POSTS_DIR.glob("*.md")):
            _meta, body = blog_loader._parse_frontmatter(path.read_text(encoding="utf-8"))
            yield f"blog/{path.name}", render_reading(body).html
        for page in docs_loader.load_pages():
            yield f"docs/{page.slug}", render_reading(page.body_md).html
        for filename, _title in DOCUMENTS.values():
            body = (DOCUMENTS_DIR / filename).read_text(encoding="utf-8")
            yield f"legal/{filename}", render_reading(body).html

    def test_rendered_content_has_no_active_html(self):
        for name, html in self._rendered_content():
            with self.subTest(content=name):
                self.assertEqual(_active_html(html), [])

    def test_guard_catches_active_html(self):
        # The guard itself, on raw HTML: says nothing about what
        # render_markdown passes through (a sanitiser there is welcome).
        self.assertEqual(
            _active_html(
                '<p><img src=x onerror="alert(1)">'
                '<a href=" JavaScript:alert(1)">x</a>'
                '<a href="&#106;avascript:alert(1)">y</a></p>'
                "<script>alert(1)</script><iframe src=\"data:text/html,x\"></iframe>"
            ),
            ["img[onerror]", "a[href]", "a[href]", "<script>", "<iframe>", "iframe[src]"],
        )

    def test_guard_allows_the_docs_shell_switcher_markup(self):
        # The raw HTML quickstart.md really ships must not trip the guard.
        self.assertEqual(
            _active_html(
                '<div class="command-group" data-tabs>'
                '<div class="command-group__tabs" role="tablist" aria-label="Shell">'
                '<button type="button" role="tab" id="shelltab-bash" '
                'aria-selected="true" aria-controls="shellpanel-bash" tabindex="0" '
                'class="command-group__tab label interactive">bash</button></div>'
                '<div class="command" role="tabpanel" id="shellpanel-bash" '
                'aria-labelledby="shelltab-bash">'
                '<div class="command__line">'
                '<span class="command__prompt" aria-hidden="true">$</span>'
                '<code id="shellcmd-bash" tabindex="0">veilfile login</code>'
                '<button type="button" class="command__copy label interactive" '
                'data-copy="shellcmd-bash" aria-label="Copy bash setup command">'
                "Copy</button></div></div>"
                '<span class="visually-hidden" role="status" data-copy-status></span></div>'
                '<p><a href="/docs/api/">API</a> <a href="https://veilfile.com/">'
                "home</a></p>"
            ),
            [],
        )


_HEADING_ID_RE = re.compile(r'(<h[1-6]) id="[^"]*">')
# render_markdown's code block (after its scroll region is marked) and the code_block
# component's: both give the escaped text of the block, as group 1.
_MARKDOWN_CODE_RE = re.compile(
    r'<pre tabindex="0" role="region" aria-label="Scrollable code block">'
    r'<code(?: class="[^"]*")?>(.*?)</code></pre>',
    re.DOTALL,
)
_COMPONENT_CODE_RE = re.compile(
    r'<div class="code-block" data-code-block>.*?<code>(.*?)</code>.*?</div>',
    re.DOTALL,
)


def _headings(html):
    """The (tag, id) of every heading, h1 to h6, in ``html``, in document order."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [
        (tag, dict(attrs).get("id"))
        for tag, attrs in parser.tags
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6")
    ]


def _all_ids(html):
    """Every id attribute in ``html``, in document order, repeats included."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [value for _tag, attrs in parser.tags for name, value in attrs if name == "id"]


def _copy_buttons(html):
    """The (data-copy, aria-label) of every Copy button in ``html``, in document order."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [
        (dict(attrs).get("data-copy"), dict(attrs).get("aria-label"))
        for tag, attrs in parser.tags
        if tag == "button" and dict(attrs).get("data-copy")
    ]


class _PreText(HTMLParser):
    """The text of every <pre> in the HTML fed to it, tags removed and character
    references decoded: what a reader sees, and what Copy takes."""

    def __init__(self):
        super().__init__()
        self.texts = []
        self._inside = False

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.texts.append("")
            self._inside = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.texts[-1] += data


def _pre_texts(html):
    parser = _PreText()
    parser.feed(html)
    parser.close()
    return parser.texts


def _same_blocks(pattern, html):
    """``html`` with each code block (found by ``pattern``) replaced by its text, so two pages
    can be compared however each draws a block. The text is decoded and loses the one newline
    the Markdown parser ends a block with."""

    def text_of(match):
        text = unescape(match[1]).removesuffix("\n")
        return f"<<code {text!r}>>"

    return pattern.sub(text_of, html)


def _reading_sources():
    """(file, Markdown text) for every Journal post, Docs page and legal document: the text
    the views hand to the renderer (front matter already taken off)."""
    from blog import loader as blog_loader
    from docs import loader as docs_loader

    backend = Path(__file__).resolve().parent.parent
    sources = []
    for path in sorted((backend / "blog" / "posts").glob("*.md")):
        _meta, body = blog_loader._parse_frontmatter(path.read_text(encoding="utf-8"))
        sources.append((f"backend/blog/posts/{path.name}", body))
    for path in sorted((backend / "docs" / "pages").glob("*.md")):
        _meta, body = docs_loader._parse_frontmatter(path.read_text(encoding="utf-8"))
        sources.append((f"backend/docs/pages/{path.name}", body))
    for path in sorted((backend / "legal" / "documents").glob("*.md")):
        sources.append((f"backend/legal/documents/{path.name}", path.read_text(encoding="utf-8")))
    return sources


class RenderReadingTests(SimpleTestCase):
    """render_reading() in backend/blog/render.py: render_markdown's HTML with an id on every
    heading, each code block drawn by components/code_block.html, and the list of a page's `##`
    sections, for the reading pages (Docs, Journal, legal)."""

    # A failure shows the message (it names the file and the fix), not the page-sized HTML.
    longMessage = False

    def _render(self, text):
        from blog.render import render_reading

        return render_reading(text)

    def test_h2_and_h3_headings_get_ids(self):
        reading = self._render("## First part\n\ntext\n\n### A detail\n\nmore\n")
        message = "backend/blog/render.py: render_reading must give headings ids (the toc extension)"
        self.assertIn('<h2 id="first-part">First part</h2>', reading.html, message)
        self.assertIn('<h3 id="a-detail">A detail</h3>', reading.html, message)

    def test_every_heading_level_gets_an_id(self):
        # The toc extension gives every heading an id whatever toc_depth says (that setting
        # only limits the section list), so the legal documents' own `#` heading has one too.
        reading = self._render("# One\n\n## Two\n\n### Three\n\n#### Four\n\n##### Five\n\n###### Six\n")
        self.assertEqual(
            _headings(reading.html),
            [("h1", "one"), ("h2", "two"), ("h3", "three"), ("h4", "four"), ("h5", "five"), ("h6", "six")],
        )

    def test_sections_are_the_h2_headings_in_order_with_plain_text_titles(self):
        from blog.render import Section

        reading = self._render(
            "# Page title\n\n"
            "## Using `veilfile` with *care*\n\ntext\n\n"
            "### A detail under it\n\ntext\n\n"
            "## Fish & chips, **bold** and [a link](/docs/)\n\ntext\n\n"
            "## What's next?\n"
        )
        self.assertEqual(
            reading.sections,
            (
                Section(id="using-veilfile-with-care", title="Using veilfile with care"),
                Section(id="fish-chips-bold-and-a-link", title="Fish & chips, bold and a link"),
                Section(id="whats-next", title="What's next?"),
            ),
            "backend/blog/render.py: sections must be the ## headings only, in order, flat "
            '(toc_depth="2-2"), with their text and no markup',
        )

    def test_section_titles_are_text_not_html(self):
        # Templates escape what they print, so a title must hold the characters themselves:
        # a title of "Fish &amp; chips" would be shown as "Fish &amp;amp; chips".
        reading = self._render("## Fish &amp; chips\n\n## 1 < 2 > 0\n\n## `a<b>`\n")
        self.assertEqual(
            [section.title for section in reading.sections],
            ["Fish & chips", "1 < 2 > 0", "a<b>"],
            "backend/blog/render.py: render_reading must html.unescape each token's name",
        )

    def test_a_numbered_heading_keeps_its_number_in_the_title_and_the_id(self):
        # The legal documents and the quickstart number their sections ("## 3. Upload").
        reading = self._render("## 3. Upload\n")
        self.assertEqual([(s.id, s.title) for s in reading.sections], [("3-upload", "3. Upload")])

    def test_results_are_frozen_values(self):
        from dataclasses import FrozenInstanceError

        reading = self._render("## One\n\n## Two\n")
        self.assertIsInstance(reading.sections, tuple)
        with self.assertRaises(FrozenInstanceError):
            reading.sections[0].title = "Changed"
        with self.assertRaises(FrozenInstanceError):
            reading.html = ""

    def test_duplicate_headings_get_distinct_ids_and_the_sections_match_the_html(self):
        reading = self._render(
            "## Notes\n\none\n\n## Notes\n\ntwo\n\n### Notes\n\nthree\n\n## Notes\n\nfour\n"
        )
        headings = _headings(reading.html)
        ids = [heading_id for _tag, heading_id in headings]
        self.assertEqual(ids, ["notes", "notes_1", "notes_2", "notes_3"])
        self.assertEqual(
            [section.id for section in reading.sections],
            [heading_id for tag, heading_id in headings if tag == "h2"],
            "backend/blog/render.py: a section's id must be the id of the h2 it names in the HTML",
        )
        self.assertEqual([section.title for section in reading.sections], ["Notes", "Notes", "Notes"])

    def test_a_text_with_no_h2_has_no_sections(self):
        for name, text in (
            ("empty", ""),
            ("a paragraph", "Just words.\n"),
            ("h1 and h3 only", "# Title\n\n### A detail\n\ntext\n"),
        ):
            with self.subTest(text=name):
                self.assertEqual(self._render(text).sections, ())

    def test_headings_in_code_are_not_sections(self):
        reading = self._render("```\n## not a heading\n```\n\n    ## nor this\n\n## Real\n")
        self.assertEqual(_headings(reading.html), [("h2", "real")])
        self.assertEqual([section.id for section in reading.sections], ["real"])

    def test_a_toc_marker_stays_text(self):
        # By default the toc extension swaps a paragraph that is only "[TOC]" for a generated
        # contents list. The Markdown here is the owner's: it must come out as typed.
        reading = self._render("Intro.\n\n[TOC]\n\nIn a sentence [TOC] too.\n\n## One\n\n## Two\n")
        self.assertNotIn(
            'class="toc"',
            reading.html,
            "backend/blog/render.py: render_reading must configure the toc extension with "
            'marker="" so a "[TOC]" in a page stays text',
        )
        self.assertIn("<p>[TOC]</p>", reading.html)
        self.assertIn("<p>In a sentence [TOC] too.</p>", reading.html)
        self.assertEqual([section.id for section in reading.sections], ["one", "two"])

    def test_tables_get_the_scroll_region_render_markdown_gives(self):
        html = self._render("| a | b |\n|---|---|\n| 1 | 2 |\n").html
        self.assertIn(
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable table"><table>',
            html,
        )
        self.assertIn("</table></div>", html)

    def test_a_code_block_is_the_code_block_component_with_its_own_copy_button(self):
        # A fence with a language, a fence without, and an indented block: three blocks,
        # three components. The scroll box is the component's focusable <pre> (the one
        # Begin's snippets have), and its Copy button reads that <pre> by its id.
        reading = self._render(
            "```bash\nlong\n```\n\n```\nplain\n```\n\nText.\n\n    indented\n"
        )
        message = (
            "backend/blog/render.py: render_reading must draw each code block with "
            "components/code_block.html (id, code, label), once per block"
        )
        self.assertEqual(reading.html.count('<div class="code-block" data-code-block>'), 3, message)
        self.assertEqual(_all_ids(reading.html), ["code-block-1", "code-block-2", "code-block-3"], message)
        self.assertEqual(
            [button_id for button_id, _label in _copy_buttons(reading.html)],
            ["code-block-1", "code-block-2", "code-block-3"],
            "each Copy button must name the id of the <pre> in its own block",
        )
        self.assertIn('<pre tabindex="0" id="code-block-2"><code>plain</code></pre>', reading.html, message)
        self.assertNotIn("<pre>", reading.html, message)
        self.assertNotIn('aria-label="Scrollable code block"', reading.html, message)

    def test_a_blocks_text_is_the_text_of_the_block_exactly(self):
        # Copy copies the <pre>'s text, so that text must be what the author typed in the
        # fence: quotes, apostrophes, angle brackets, ampersands, backslashes, indentation,
        # braces that look like template tags, and a non-ASCII letter.
        first = (
            "curl -X POST \"https://example.test/a?b=1&c=2\" \\\n"
            "  -H 'Authorization: Bearer <key>' \\\n"
            "  -d '{\"name\": \"{{ name }}\", \"if\": \"{% if x %}\", \"home\": \"$HOME\"}' # café"
        )
        second = "    {\n      \"two\": 2\n    }\n\nafter a blank line"
        reading = self._render(f"## Upload\n\n```bash\n{first}\n```\n\n```\n{second}\n```\n\n    indented\n    block\n")
        self.assertEqual(
            _pre_texts(reading.html),
            [first, second, "indented\nblock"],
            "backend/blog/render.py: render_reading must pass each block's text to the component "
            "decoded (html.unescape) and without the newline the Markdown parser ends it with",
        )

    def test_block_ids_are_unique_and_skip_the_ids_a_heading_or_raw_html_has(self):
        # "Code block 1" has the id code-block-1; the raw HTML has code-block-3. Blocks must
        # not repeat either, however the page grows.
        reading = self._render(
            "## Code block 1\n\n```\na\n```\n\n"
            "## Code block 2\n\n```\nb\n```\n\n"
            '<div id="code-block-3">raw HTML</div>\n\n'
            "```\nc\n```\n"
        )
        ids = _all_ids(reading.html)
        self.assertEqual(
            ids,
            ["code-block-1", "code-block-4", "code-block-2", "code-block-5", "code-block-3", "code-block-6"],
            "backend/blog/render.py: a code block's id must skip every id the page already has "
            "(headings from the toc extension, ids in raw HTML)",
        )
        self.assertEqual(len(set(ids)), len(ids))

    def test_each_button_is_named_by_the_language_and_the_heading_above_its_block(self):
        reading = self._render(
            "```\nbefore any heading\n```\n\n"
            "## Upload\n\n```bash\none\n```\n\n```json\n{}\n```\n\n"
            "### Using `veilfile` with *care*\n\n```\nthree\n```\n\n"
            "## Fish & chips\n\n```bash\na\n```\n\n```bash\nb\n```\n\n```bash\nc\n```\n"
        )
        self.assertEqual(
            [label for _id, label in _copy_buttons(reading.html)],
            [
                "Copy the code",
                "Copy the bash code under Upload",
                "Copy the json code under Upload",
                "Copy the code under Using veilfile with care",
                "Copy the bash code under Fish & chips",
                "Copy the bash code under Fish & chips (2)",
                "Copy the bash code under Fish & chips (3)",
            ],
            "backend/blog/render.py: a button's label is 'Copy the', the fence's language, 'code', "
            "'under' and the heading above (plain text); a repeated label gets (2), (3)",
        )

    def test_render_markdown_has_no_copy_buttons(self):
        # The feed and the legal text comparison use render_markdown: a Copy button has no place
        # in RSS, and the legal documents' words are compared as they were.
        from blog.render import render_markdown

        html = render_markdown("```bash\nls\n```\n\n    indented\n")
        self.assertNotIn("data-code-block", html, "backend/blog/render.py: render_markdown must stay as it was")
        self.assertNotIn("<button", html)
        self.assertEqual(html.count('<pre tabindex="0" role="region" aria-label="Scrollable code block">'), 2)

    def test_reading_html_is_render_markdown_plus_heading_ids_and_code_blocks_for_every_content_file(self):
        # render_markdown's output cannot be compared before and after this change in one test, so
        # it is pinned from the other side: render_reading may differ from it by heading ids and by
        # the way each code block is drawn, and by nothing else. Heading ids are taken off both
        # (only heading ids: the quickstart's raw HTML carries ids of its own), and each code block
        # on each side is swapped for its text, so the blocks are compared by what they say.
        from blog.render import render_markdown

        sources = _reading_sources()
        self.assertEqual(
            {name.split("/")[1] for name, _text in sources},
            {"blog", "docs", "legal"},
            "a content folder is empty or moved: this test must read the Journal, Docs and legal files",
        )
        for name, text in sources:
            with self.subTest(file=name):
                reading = _same_blocks(_COMPONENT_CODE_RE, _HEADING_ID_RE.sub(r"\1>", self._render(text).html))
                markdown = _same_blocks(_MARKDOWN_CODE_RE, render_markdown(text))
                self.assertEqual(
                    reading,
                    markdown,
                    f"{name}: render_reading(text).html differs from render_markdown(text) by more "
                    "than heading ids and the drawing of its code blocks. Fix backend/blog/render.py: "
                    "render_reading may add the toc extension and _draw_code_blocks to what "
                    "render_markdown does, and nothing else.",
                )

    def test_every_code_block_in_a_content_file_has_one_copy_button(self):
        from blog.render import render_markdown

        for name, text in _reading_sources():
            with self.subTest(file=name):
                html = self._render(text).html
                blocks = render_markdown(text).count("<pre")
                # Only the buttons inside code blocks: the quickstart's raw-HTML switcher (Task 5) has Copy buttons of its own.
                in_blocks = [button for block in re.findall(r'<div class="code-block" data-code-block>.*?</div>', html, re.DOTALL)
                             for button in _copy_buttons(block)]
                self.assertEqual(
                    (html.count("<pre"), html.count("data-code-block"), len(in_blocks)),
                    (blocks, blocks, blocks),
                    f"{name} has {blocks} code blocks and each needs a Copy button. A block the page "
                    "shows without one is a fence with { } attributes, or a raw HTML <pre>: write it "
                    "as a plain fence (``` and an optional language). The legal documents are the "
                    "owner's: stop and ask.",
                )
                copy_buttons = _copy_buttons(html)
                for index, kind in ((0, "an id"), (1, "a name")):
                    values = [button[index] for button in copy_buttons]
                    self.assertEqual(
                        len(set(values)),
                        len(values),
                        f"{name}: two Copy buttons share {kind}. Fix backend/blog/render.py "
                        "(_draw_code_blocks numbers the ids and the repeated labels).",
                    )

    def test_every_id_in_a_rendered_content_file_is_unique(self):
        for name, text in _reading_sources():
            with self.subTest(file=name):
                ids = _all_ids(self._render(text).html)
                repeated = sorted({value for value in ids if ids.count(value) > 1})
                self.assertEqual(
                    repeated,
                    [],
                    f"{name} renders the id {repeated} more than once. Heading ids come from the "
                    "heading text: reword one of the headings, or change the id of the raw HTML "
                    "that shares it (code blocks skip taken ids, render.py). The legal documents "
                    "are the owner's: stop and ask.",
                )

    def test_ids_in_a_rendered_content_file_never_repeat_an_id_the_shell_puts_on_every_page(self):
        # A heading called "Main" would get id="main", the skip link's target. Only shell.html is
        # read here; an id a layout or component writes into its own markup belongs in this test too.
        from django.contrib.auth.models import AnonymousUser
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        shell_ids = set(_all_ids(render_to_string("shell.html", {}, request=request)))
        self.assertIn("main", shell_ids, 'backend/templates/shell.html lost <main id="main">: update this test')
        for name, text in _reading_sources():
            with self.subTest(file=name):
                page_ids = set(_all_ids(self._render(text).html))
                self.assertEqual(
                    sorted(shell_ids & page_ids),
                    [],
                    f"{name} has an id that is also an id in backend/templates/shell.html "
                    "(the skip link's target is main). Reword the heading. The legal documents are "
                    "the owner's: stop and ask.",
                )


class AppShellCsrfCookieTests(TestCase):
    """web-11: every dashboard page sets the csrftoken cookie that the forms
    on it post with (the dashboard's views are Django's now, the cookie is
    set by dashboard.views.dashboard_view), so a reload recovers from a
    CSRF 403."""

    SECTIONS = ("/app/", "/app/files/", "/app/keys/", "/app/apps/", "/app/billing/")

    def test_app_shell_sets_csrf_cookie(self):
        user = User.objects.create_user(username="dash", password="pw12345678")
        c = Client()
        c.force_login(user)
        for address in self.SECTIONS:
            with self.subTest(address=address):
                response = c.get(address)
                self.assertEqual(response.status_code, 200)
                self.assertIn("csrftoken", response.cookies)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_signed_out_app_still_redirects_to_login(self):
        for address in self.SECTIONS:
            with self.subTest(address=address):
                response = Client().get(address)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])


class ArtifactListPaginationTests(TestCase):
    """web-10: optional ?limit=/&cursor= pagination. Without them the
    response is the same full bare list as before."""

    def setUp(self):
        self.user = User.objects.create_user(username="lister", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, raw = APIKey.create_key(self.workspace, "agent")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        base = timezone.now() - timedelta(hours=1)
        expires = timezone.now() + timedelta(days=7)
        self.artifacts = []
        for i in range(7):
            a = Artifact.objects.create(
                workspace=self.workspace,
                filename=f"f{i}.txt",
                mime_type="text/plain",
                size_bytes=i,
                expires_at=expires,
            )
            # Two pairs share a timestamp to exercise the id tiebreaker.
            created = base + timedelta(minutes=min(i, 5) if i != 3 else 2)
            Artifact.objects.filter(pk=a.pk).update(created_at=created)
            self.artifacts.append(a)
        self.expected_ids = [
            str(a.id)
            for a in Artifact.objects.filter(workspace=self.workspace).order_by(
                "-created_at", "-id"
            )
        ]

    def test_default_response_is_the_full_bare_list(self):
        response = self.client.get("/api/v1/artifacts/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 7)
        self.assertEqual(sorted(i["id"] for i in body), sorted(self.expected_ids))
        self.assertEqual(
            set(body[0]),
            {"id", "filename", "mime_type", "size_bytes", "url", "expires_at",
             "secret_flags", "created_at"},
        )
        created = [i["created_at"] for i in body]
        self.assertEqual(created, sorted(created, reverse=True))
        self.assertNotIn("X-Next-Cursor", response)
        self.assertNotIn("Link", response)

    def test_cursor_walk_returns_every_artifact_once_in_order(self):
        seen = []
        url = "/api/v1/artifacts/?limit=2"
        pages = 0
        while True:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIsInstance(body, list)
            self.assertLessEqual(len(body), 2)
            seen.extend(i["id"] for i in body)
            pages += 1
            cursor = response.get("X-Next-Cursor")
            if not cursor:
                self.assertNotIn("Link", response)
                break
            self.assertIn('rel="next"', response["Link"])
            url = f"/api/v1/artifacts/?limit=2&cursor={cursor}"
        self.assertEqual(seen, self.expected_ids)
        self.assertEqual(pages, 4)

    def test_link_header_points_at_next_page(self):
        response = self.client.get("/api/v1/artifacts/?limit=3")
        link = response["Link"]
        target = link[link.index("<") + 1 : link.index(">")]
        nxt = self.client.get(target)
        self.assertEqual(
            [i["id"] for i in nxt.json()], self.expected_ids[3:6]
        )

    def test_limit_is_clamped(self):
        self.assertEqual(len(self.client.get("/api/v1/artifacts/?limit=0").json()), 1)
        response = self.client.get("/api/v1/artifacts/?limit=100000")
        self.assertEqual(len(response.json()), 7)
        self.assertNotIn("X-Next-Cursor", response)

    def test_invalid_parameters_are_400(self):
        for query in [
            "limit=abc",
            "cursor=garbage",
            "cursor=123-nothex",
            "cursor=99999999999999999999999-" + "0" * 32,
            "limit=2&cursor=-1",
        ]:
            with self.subTest(query=query):
                response = self.client.get(f"/api/v1/artifacts/?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")

    def test_session_dashboard_can_page(self):
        client = APIClient()
        client.force_login(self.user)
        response = client.get("/api/v1/artifacts/?limit=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([i["id"] for i in response.json()], self.expected_ids[:5])
        self.assertTrue(response["X-Next-Cursor"])
