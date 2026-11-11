"""With the team-seats switch off, no dashboard page changes by a byte.

Each page is rendered for an account on every plan and for a member, with the CSRF token, ids and times masked, and compared with the
file in dashboard/golden/ that was captured from main's templates before any page changed. To regenerate the files after a
deliberate change to a page, run this module once with GOLDEN_REGENERATE=1 and review the diff.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import os
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from drop_core.models import APIKey, Artifact, EmailVerification, Membership, get_or_create_workspace

User = get_user_model()
PASSWORD = "pw12345678"
GOLDEN = Path(settings.BASE_DIR) / "dashboard" / "golden"
MASKS = [
    (re.compile(r'(name="csrfmiddlewaretoken" value=")[^"]*'), r"\1CSRF"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "UUID"),
    (re.compile(r'(name="token" value=")[^"]*'), r"\1TOKEN"),
    (re.compile(r"(/a/)[A-Za-z0-9_-]{20,}"), r"\1ARTIFACT"),
    (re.compile(r"\bvlf_[A-Za-z0-9_-]+"), "KEY"),
    # A key's id depends on the database: SQLite restarts the count in each test, and Postgres keeps counting.
    (re.compile(r"(/app/keys/)\d+(/)"), r"\1ID\2"),
    (re.compile(r"(<time[^>]*>)[^<]*"), r"\1TIME"),
    (re.compile(r"\b20\d\d-\d\d-\d\d[T ][0-9:.+Z-]+"), "TIME"),
    (re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, 20\d\d(?:,? [0-9:]+(?: ?[ap]\.?m\.?)?(?: UTC)?)?"), "TIME"),
]
PLANS = ("free", "team", "team_plus", "scale")
PAGES = ("overview", "files", "keys", "billing", "key-revoke")


def mask(html):
    for pattern, replacement in MASKS:
        html = pattern.sub(replacement, html)
    return html


def fixture_user(name, plan):
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password=PASSWORD)
    EmailVerification.objects.create(user=user, email=user.email, is_verified=True, verified_at=timezone.now())
    workspace = get_or_create_workspace(user)
    workspace.plan = plan
    workspace.save(update_fields=["plan"])
    return user, workspace


def fixture_account(name, plan, member_of=None):
    """A signed-in client for an account with one key and one file; a member when `member_of` names the team's workspace."""
    user, workspace = fixture_user(name, plan)
    if member_of is not None:
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        Membership.objects.create(workspace=member_of, user=user, role=Membership.MEMBER)
        workspace = member_of
    current = Membership.objects.get(workspace=workspace, user=user, removed_at__isnull=True)
    key, _ = APIKey.create_key(workspace, "agent", member=current)
    Artifact.objects.create(workspace=workspace, member=current, filename="report.txt", mime_type="text/plain", size_bytes=5,
                           expires_at=timezone.now() + timezone.timedelta(days=3), content_sha256="0" * 64)
    client = Client()
    client.login(username=name, password=PASSWORD)
    return client, key


def page_path(page, key):
    return {"overview": "/app/", "files": "/app/files/", "keys": "/app/keys/", "billing": "/app/billing/",
            "key-revoke": f"/app/keys/{key.pk}/revoke/"}[page]


@override_settings(TEAM_SEATS=False)
class SwitchOffTests(TestCase):
    longMessage = False

    def check(self, label, client, key):
        for page in PAGES:
            response = client.get(page_path(page, key))
            self.assertEqual(response.status_code, 200, f"backend/dashboard: {label} {page} answered {response.status_code}")
            body = mask(response.content.decode())
            golden = GOLDEN / f"{label}-{page}.html"
            if os.environ.get("GOLDEN_REGENERATE") == "1":
                GOLDEN.mkdir(exist_ok=True)
                golden.write_text(body)
                continue
            self.assertEqual(
                body,
                golden.read_text(),
                f"backend/dashboard/golden/{golden.name}: the {page} page for {label} differs from the page main served. With the switch off a page must "
                "not change. After a deliberate change to a page, regenerate the files with GOLDEN_REGENERATE=1 and review the diff.",
            )

    def test_every_page_is_as_main_served_it_for_each_plan(self):
        for plan in PLANS:
            client, key = fixture_account(f"ada-{plan}".replace("_", "-"), plan)
            self.check(plan, client, key)

    def test_every_page_is_as_main_served_it_for_a_member(self):
        _, team = fixture_user("grace", "team")
        client, key = fixture_account("alan", "free", member_of=team)
        self.check("member", client, key)
