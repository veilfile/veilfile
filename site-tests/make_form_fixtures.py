"""Make what the forms part of the browser checks needs; CI and local runs only.

Run from backend/ inside Django, with SITE_SESSION_FILE as for make_staff_session.py; the fixtures are written beside it, as form-fixtures.json:
  SITE_SESSION_FILE=../site-tests/session.txt python manage.py shell -c "exec(open('../site-tests/make_form_fixtures.py').read())"

It makes verified users and their sessions (one with the longest values the models take), a user whose verification link went a moment ago, a user to reset, device codes,
OAuth clients registered through /o/register/, the links of the reset and verification emails, and the 404 and the 500 as production draws them: the dev server runs with
DEBUG on, which never calls the site's error handlers, so those two are drawn here with DEBUG off, by the whole request stack, and the checks serve those bytes.
A run first deletes what an earlier one left. It refuses to run unless DEBUG is on and the database is SQLite or on this machine: it sets a known password on users.
"""
import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import Client, override_settings
from django.urls import path, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from oauth2_provider.models import get_application_model

from drop_core import device
from drop_core.email_verification import make_verification_token
from drop_core.models import APIKey, DeviceCode, EmailSendLog, EmailVerification, get_or_create_workspace

if not settings.DEBUG or (connection.vendor != "sqlite"
                          and connection.settings_dict.get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1")):
    raise SystemExit("make_form_fixtures.py runs only against a local or CI database: set DJANGO_DEBUG=True, "
                     "unset DATABASE_URL (or point it at localhost), and try again.")

User, Application = get_user_model(), get_application_model()
PASSWORD = "site-checks-not-a-secret-1"   # every fixture user's password: a development database only
CODE_IP = "198.51.100.7"   # where the fixture device codes say they came from: how a re-run finds its own
CALLBACK, STATE = "http://127.0.0.1:9876/callback", "site-checks-state"
APP, LONG_APP = "Site checks app", "SiteChecksLongName" + "W" * 237   # 255 characters, no space: the longest name a client registers
LONG_EMAIL = "l" * 64 + "@" + "d" * 63 + "." + "e" * 63 + "." + "f" * 57 + ".com"   # 254 characters, every label at its longest


def make_user(username, email, verified=None, last_sent_at=None):
    """A user with the fixtures' password and, unless verified is None, an email record (verified or pending)."""
    user, _ = User.objects.get_or_create(username=username)
    user.email, user.is_active = email, True
    user.set_password(PASSWORD)
    user.save()
    if verified is None:
        EmailVerification.objects.filter(user=user).delete()
    else:
        EmailVerification.objects.update_or_create(user=user, defaults={"email": email, "is_verified": verified, "last_sent_at": last_sent_at,
                                                                         "verified_at": timezone.now() if verified else None})
    return user


def session_for(user):
    """A signed-in session, as make_staff_session.py makes one; the key is what the browser's cookie holds."""
    session = SessionStore()
    session[SESSION_KEY], session[BACKEND_SESSION_KEY] = str(user.pk), settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


def make_code(user_agent, lasts=timedelta(days=1)):
    """A pending device code, as the terminal asks for one; it lasts a day, so a slow run does not meet an expired one."""
    code, _secret, display = device._issue_device_code(CODE_IP, user_agent)
    code.expires_at = timezone.now() + lasts
    code.save(update_fields=["expires_at"])
    return display


def authorize_url(name):
    """A client registered as one registers itself (POST /o/register/), and the address that asks a user to authorize it (PKCE with the RFC 7636 example challenge)."""
    reply = Client().post("/o/register/", data=json.dumps({"client_name": name, "redirect_uris": [CALLBACK], "grant_types": ["authorization_code"],
                                                           "token_endpoint_auth_method": "none"}), content_type="application/json")
    if reply.status_code != 201:
        raise SystemExit(f"POST /o/register/ answered {reply.status_code}: the OAuth client could not be made")
    return "/o/authorize/?" + urlencode({"response_type": "code", "client_id": reply.json()["client_id"], "redirect_uri": CALLBACK, "scope": "mcp:use", "state": STATE,
                                         "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "code_challenge_method": "S256"})


def bake_error_pages():
    """The 404 and the 500 as production draws them: DEBUG off, plain /static/ URLs (no manifest here, as in the test runner), the whole request stack and the site's own
    handlers. A view that raises, added to the URLs for the moment, stands for the 500."""
    import config.urls as site_urls

    def fails(request):
        raise RuntimeError("site checks: a view that fails")

    boom = path("site-checks-boom/", fails)
    plain = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    baked, was = {}, logging.root.manager.disable
    site_urls.urlpatterns.append(boom)
    logging.disable(logging.CRITICAL)   # the 500 is logged as the error it is
    try:
        with override_settings(DEBUG=False, STORAGES=plain):
            for name, address in (("not_found", "/no-such-page-leak-check-7c1e/"), ("server_error", "/site-checks-boom/")):   # the marker: no error page may print its address
                reply = Client(raise_request_exception=False).get(address)
                baked[name] = {"path": address, "status": reply.status_code, "body": reply.content.decode("utf-8"),
                               "headers": {key: value for key, value in reply.items() if key.lower() != "content-length"}}
    finally:
        logging.disable(was)
        site_urls.urlpatterns.remove(boom)
    if (baked["not_found"]["status"], baked["server_error"]["status"]) != (404, 500):
        raise SystemExit("the error pages were not drawn as a 404 and a 500")
    return baked


# What an earlier run left, so that a run is the same every time.
DeviceCode.objects.filter(requester_ip=CODE_IP).delete()
EmailSendLog.objects.filter(purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT, ip_address__in=["127.0.0.1", "::1"]).delete()
EmailSendLog.objects.filter(user__username__startswith="site-").delete()
Application.objects.filter(name__in=[APP, LONG_APP]).delete()

verified = make_user("site-verified", "site-verified@example.com", verified=True)
long_user = make_user("site-long-" + "u" * 140, LONG_EMAIL, verified=True)   # 150 characters, the longest username
pending = make_user("site-pending", "p" + LONG_EMAIL[1:], verified=False, last_sent_at=timezone.now() + timedelta(hours=1))   # a resend is always too soon: no mail is sent
reset = make_user("site-reset", "site-reset@example.com", verified=False)
APIKey.objects.filter(workspace__owner__in=[verified, long_user]).delete()
APIKey.create_key(get_or_create_workspace(long_user), "K" * 255)   # the free plan allows one key: the long user is at the limit

fixtures = {
    "sessions": {"verified": session_for(verified), "long": session_for(long_user), "pending": session_for(pending)},
    "accounts": {"verified": {"username": verified.username, "password": PASSWORD}},
    "device": {"consent": make_code("veilfile-cli/0.4.0 (site checks)"), "long": make_code("U" * 256),
               "expired": make_code("veilfile-cli/0.4.0 (site checks)", lasts=timedelta(seconds=-30)), "unknown": "ZZZZ-2222",
               "approve": make_code("veilfile-cli/0.4.0 (site checks)"), "deny": make_code("veilfile-cli/0.4.0 (site checks)")},   # these two are used up by being approved and denied
    "oauth": {"app": APP, "authorize": authorize_url(APP), "long": authorize_url(LONG_APP),
              "unknown": "/o/authorize/?response_type=code&client_id=site-checks-no-such-client&redirect_uri=" + CALLBACK},
    "reset": {"link": reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(reset.pk)), default_token_generator.make_token(reset)])},
    "verify": {"link": reverse("verify-email", args=[make_verification_token(reset, reset.email)])},
    "errors": bake_error_pages(),
}
Path(os.environ["SITE_SESSION_FILE"]).with_name("form-fixtures.json").write_text(json.dumps(fixtures, indent=1), encoding="utf-8")
print(f"form fixtures written: {len(fixtures['sessions'])} sessions, {DeviceCode.objects.filter(requester_ip=CODE_IP).count()} device codes, "
      f"{len(fixtures['errors'])} error pages drawn with DEBUG off")
