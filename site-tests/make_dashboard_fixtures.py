"""Make what the dashboard part of the browser checks needs; CI and local runs only.

Run from backend/ inside Django, with SITE_SESSION_FILE as for make_staff_session.py; the fixtures are written beside it, as dashboard-fixtures.json:
  SITE_SESSION_FILE=../site-tests/session.txt python manage.py shell -c "exec(open('../site-tests/make_dashboard_fixtures.py').read())"

It makes nine verified users, who have no password and are reached only through the sessions it writes:
  site-dash-owner  a Team workspace: four keys (the longest name a key takes, a revoked one, one to revoke and one that has been used) with room under the plan's cap for
                   the key the checks make, 53 files (so the list has a second page: one with the longest name, one expired, one held, one with secret flags, one to revoke
                   and sizes in gigabytes) and three connected apps (one with the longest name);
  site-dash-over   a Free workspace after a downgrade, over its limits on keys, storage, link lifetime and uploads, with a stored unpaid subscription;
  site-dash-sub    a Team subscriber whose last payment failed, with a switch to Scale waiting for payment (a session hint) for the Billing page;
  site-dash-admin  a Team workspace with people on it, for the Members page (the switch for team seats must be on in the server): its administrator, and
  site-dash-member a member of it, with an allowance of each kind (the keys one above the plan's cap) and some use; the member has a request to become the administrator waiting, and one invitation waits for an address, made directly so that
                   the raw token is known. A run deletes the team's invitations and its people other than the administrator first. The owner's files and keys are not touched.
  site-dash-lapsed-admin  a Free workspace that has other people on it, as a team does after its payment ends (for the bar and the Billing and Members pages that say so): its administrator, and
  site-dash-barred        its member, barred because the team is on Free, with a file and a key of their own;
  site-dash-heir          the administrator of a Team workspace just handed over to them: fake Stripe ids are written to its rows (nothing is sent anywhere) and the handover is marked,
                          so that Billing says to check the card.
It also writes spare sessions of the owner for the Sign out checks, which each use one up. A run first resets what an earlier one left, so a run is the same every time.
It refuses to run unless DEBUG is on and the database is SQLite or on this machine, and refuses while a Stripe variable is set or the storage is not the local one: the
checks press Revoke and the billing buttons, and nothing they do may reach a real account.
"""
import hashlib
import json
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model

from drop_billing.models import Subscription
from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    Invitation,
    Membership,
    MemberUsageCounter,
    UsageCounter,
    administrator_of,
    get_or_create_workspace,
)
from drop_core.oauth import OAUTH_SCOPE_MCP_USE

if not settings.DEBUG or (connection.vendor != "sqlite"
                          and connection.settings_dict.get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1")):
    raise SystemExit("make_dashboard_fixtures.py runs only against a local or CI database: set DJANGO_DEBUG=True, "
                     "unset DATABASE_URL (or point it at localhost), and try again.")
if any(name.startswith("STRIPE_") for name in os.environ):
    raise SystemExit("make_dashboard_fixtures.py runs only with no Stripe variable set: the checks press Upgrade and Manage billing, and a server with a key would ask Stripe. "
                     "Unset every STRIPE_ variable, and try again.")
if os.environ.get("DROP_STORAGE", "local").lower() != "local":
    raise SystemExit("make_dashboard_fixtures.py runs only with the local file storage: the checks revoke a file, which is a delete in the bucket of any other storage. "
                     "Unset DROP_STORAGE, and try again.")

User, Application, AccessToken = get_user_model(), get_application_model(), get_access_token_model()
OWNER, OVER, SUB = "site-dash-owner", "site-dash-over", "site-dash-sub"
ADMIN, MEMBER = "site-dash-admin", "site-dash-member"
LAPSED, BARRED, HEIR = "site-dash-lapsed-admin", "site-dash-barred", "site-dash-heir"
INVITEE = "site-dash-invitee@example.com"   # nobody has this address: the invitation waits and is never answered
LONG_KEY = "K" * 255   # the longest name a key takes
LONG_FILE = "x" * 508 + ".txt"   # 512 characters, with no place to break: the longest name a file takes
LONG_APP = "DashLongName" + "W" * 243   # 255 characters, no space: the longest name a client registers under
APP, GONE = "Dashboard checks app", "Dashboard disconnect-me app"
CALLBACK = "http://127.0.0.1:9876/callback"
FILES = 53   # 50 on the list's first page and 3 on its second
SIGN_OUT_SESSIONS = 14   # the Sign out checks each end a session
NOW = timezone.now()


def session_for(user, **kept):
    """A signed-in session, as make_staff_session.py makes one (and `kept` in it); the key is what the browser's cookie holds."""
    session = SessionStore()
    session[SESSION_KEY], session[BACKEND_SESSION_KEY] = str(user.pk), settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    for name, value in kept.items():
        session[name] = value
    session.create()
    return session.session_key


def make_account(username):
    """A verified user with an unusable password."""
    user, _ = User.objects.get_or_create(username=username)
    user.email, user.is_active, user.is_staff = f"{username}@example.com", True, False
    user.set_unusable_password()
    user.save()
    EmailVerification.objects.update_or_create(user=user, defaults={"email": user.email, "is_verified": True, "verified_at": NOW})
    return user


def make_user(username, plan):
    """A verified user with an unusable password and an empty workspace on `plan`: what an earlier run left of theirs is deleted first."""
    user = make_account(username)
    workspace = get_or_create_workspace(user)
    Artifact.objects.filter(workspace=workspace).delete()
    APIKey.objects.filter(workspace=workspace).delete()
    UsageCounter.objects.filter(workspace=workspace).delete()
    Subscription.objects.filter(workspace=workspace).delete()
    workspace.plan, workspace.stripe_customer_id, workspace.stripe_subscription_id, workspace.billing_handed_over_at = plan, "", "", None
    workspace.save()
    return user, workspace


def add_key(workspace, name, *, used_hours=None, active=True):
    key, _raw = APIKey.create_key(workspace, name)
    APIKey.objects.filter(pk=key.pk).update(is_active=active, last_used_at=None if used_hours is None else NOW - timedelta(hours=used_hours))


def add_file(workspace, name, *, hours=0, days=7, flags=(), held=False, size=1500, member=None):
    """A file the list shows: made `hours` ago, expiring in `days` (negative: expired). Its bytes are not stored: nothing here downloads one, and a revoke of
    a file the storage does not have is a delete of nothing."""
    artifact = Artifact.objects.create(workspace=workspace, member=member or administrator_of(workspace), filename=name, size_bytes=size, expires_at=NOW + timedelta(days=days), secret_flags=list(flags),
                                       safety_status="held" if held else "clear")
    Artifact.objects.filter(pk=artifact.pk).update(created_at=NOW - timedelta(hours=hours))


def connect(user, name):
    """An app the user has authorized and that can still act: what the Connected apps list shows."""
    app = Application.objects.create(name=name, client_type=Application.CLIENT_PUBLIC, authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE, redirect_uris=CALLBACK)
    AccessToken.objects.create(user=user, application=app, token=secrets.token_urlsafe(32), expires=NOW + timedelta(days=7), scope=OAUTH_SCOPE_MCP_USE)


Application.objects.filter(name__in=[APP, LONG_APP, GONE]).delete()   # what an earlier run connected (their tokens go with them)

owner, owner_workspace = make_user(OWNER, "team")
add_key(owner_workspace, "ci-runner", used_hours=2)
add_key(owner_workspace, LONG_KEY)
add_key(owner_workspace, "retired-agent", used_hours=48, active=False)
add_key(owner_workspace, "revoke-me")
add_file(owner_workspace, "a-file-that-works.txt")
add_file(owner_workspace, "deploy.env", hours=1, flags=["stripe_key", "generic_secret"], size=912)
add_file(owner_workspace, "an-expired-file.zip", hours=2, days=-1, size=48_300_000)
add_file(owner_workspace, "a-held-file.csv", hours=3, held=True, size=2_150_000_000)
add_file(owner_workspace, LONG_FILE, hours=4)
add_file(owner_workspace, "revoke-me.txt", hours=5)
for number in range(FILES - 6):
    add_file(owner_workspace, f"older-{number:02d}.txt", hours=6 + number, size=1024 * (number + 1))
for name in (APP, LONG_APP, GONE):
    connect(owner, name)

over, over_workspace = make_user(OVER, "free")
for name in ("agent-one", "agent-two", "agent-three"):
    add_key(over_workspace, name)
add_file(over_workspace, "the-big-one.bin", days=300, size=2 * 1024**3)
add_file(over_workspace, "a-small-one.txt", hours=1)
UsageCounter.objects.create(workspace=over_workspace, year_month=NOW.strftime("%Y-%m"), uploads=150)
over_workspace.stripe_customer_id = "cus_site_checks_over"   # a stored id, never sent anywhere: no Stripe variable is set
over_workspace.save()
Subscription.objects.create(workspace=over_workspace, stripe_customer_id=over_workspace.stripe_customer_id, status="unpaid")

sub, sub_workspace = make_user(SUB, "team")
sub_workspace.stripe_customer_id, sub_workspace.stripe_subscription_id = "cus_site_checks_sub", "sub_site_checks_sub"
sub_workspace.save()
Subscription.objects.create(workspace=sub_workspace, stripe_customer_id="cus_site_checks_sub", stripe_subscription_id="sub_site_checks_sub", status="past_due",
                            current_period_end=NOW + timedelta(days=20))

admin, admin_workspace = make_user(ADMIN, "team")
Invitation.objects.filter(workspace=admin_workspace).delete()   # the team's invitations and people other than its administrator, so that a run is the same every time
Membership.objects.filter(workspace=admin_workspace).exclude(user=admin).delete()
person = Membership.objects.create(workspace=admin_workspace, user=make_account(MEMBER), role=Membership.MEMBER,
                                   upload_allowance=50, storage_allowance_bytes=2 * 1024**3, key_allowance=9)   # nine keys: above the Team plan's five, so a cell is capped
Membership.objects.filter(pk=person.pk).update(offered_administrator_at=NOW)   # a request to take over waits for the member: the Overview notice, the accept page and the administrator's cancel page need it
MemberUsageCounter.objects.create(member=person, year_month=NOW.strftime("%Y-%m"), uploads=12)
APIKey.create_key(admin_workspace, "members-agent", member=person)
add_file(admin_workspace, "members-notes.txt", size=3_500_000, member=person)
add_file(admin_workspace, "admin-notes.txt", hours=1)
raw_token = secrets.token_urlsafe(32)
Invitation.objects.create(workspace=admin_workspace, email=INVITEE, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(), invited_by=admin, expires_at=NOW + Invitation.TTL)

lapsed, lapsed_workspace = make_user(LAPSED, "free")   # a team that ended up on Free with a person still on it
Invitation.objects.filter(workspace=lapsed_workspace).delete()
Membership.objects.filter(workspace=lapsed_workspace).exclude(user=lapsed).delete()
barred = Membership.objects.create(workspace=lapsed_workspace, user=make_account(BARRED), role=Membership.MEMBER)
APIKey.create_key(lapsed_workspace, "barred-members-agent", member=barred)
add_file(lapsed_workspace, "barred-members-file.txt", member=barred)

heir, heir_workspace = make_user(HEIR, "team")   # fake ids, never sent anywhere: no Stripe variable is set
heir_workspace.stripe_customer_id, heir_workspace.stripe_subscription_id, heir_workspace.billing_handed_over_at = "cus_site_checks_heir", "sub_site_checks_heir", NOW
heir_workspace.save()
Subscription.objects.create(workspace=heir_workspace, stripe_customer_id="cus_site_checks_heir", stripe_subscription_id="sub_site_checks_heir", status="active",
                            current_period_end=NOW + timedelta(days=20))

fixtures = {
    "sessions": {"owner": session_for(owner), "over": session_for(over),
                 # a switch to Scale that awaits payment, as billing_page.remember_pending keeps it in the session
                 "sub": session_for(sub, dashboard_pending_switch={"plan": "scale", "invoice_url": "https://invoice.stripe.com/i/site-checks", "at": int(time.time())}),
                 "admin": session_for(admin), "member": session_for(person.user),
                 "lapsed": session_for(lapsed), "barred": session_for(barred.user), "heir": session_for(heir),
                 "sign_out": [session_for(owner) for _ in range(SIGN_OUT_SESSIONS)]},
    "owner": {"keys": 4, "files": FILES, "long_key": LONG_KEY, "revoke_key": "revoke-me", "retired_key": "retired-agent", "long_file": LONG_FILE,
              "revoke_file": "revoke-me.txt", "flags_file": "deploy.env", "expired_file": "an-expired-file.zip", "held_file": "a-held-file.csv",
              "apps": {"plain": APP, "long": LONG_APP, "gone": GONE}},
    "over": {"keys": 3},
    "sub": {"switch_to": "scale"},
    "bar": {"admin_email": lapsed.email, "file": "barred-members-file.txt", "key": "barred-members-agent"},
    "team": {"member_id": person.pk, "invitee": INVITEE, "token": raw_token},
}
Path(os.environ["SITE_SESSION_FILE"]).with_name("dashboard-fixtures.json").write_text(json.dumps(fixtures, indent=1), encoding="utf-8")
print(f"dashboard fixtures written: {8 + SIGN_OUT_SESSIONS} sessions, {Artifact.objects.filter(workspace=owner_workspace).count()} files and "
      f"{APIKey.objects.filter(workspace=owner_workspace).count()} keys for {OWNER}")
