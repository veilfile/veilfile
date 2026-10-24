"""Create a staff user and a signed-in session for the site checks; CI and local runs only.

Run from backend/ inside Django, with SITE_SESSION_FILE naming where to write the session key:
  SITE_SESSION_FILE=../site-tests/session.txt python manage.py shell \
      -c "exec(open('../site-tests/make_staff_session.py').read())"

It refuses to run unless DEBUG is on and the database is SQLite or on this machine, so a
production DATABASE_URL left in the environment never gets a staff login planted in it.
"""
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection

if not settings.DEBUG or (connection.vendor != "sqlite"
                          and connection.settings_dict.get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1")):
    raise SystemExit("make_staff_session.py runs only against a local or CI database: set DJANGO_DEBUG=True, "
                     "unset DATABASE_URL (or point it at localhost), and try again.")

user, _ = get_user_model().objects.get_or_create(username="site-checks", defaults={"email": "site-checks@example.com"})
user.is_staff = True
user.set_unusable_password()
user.save()
session = SessionStore()
session[SESSION_KEY] = str(user.pk)
# The session must name a configured backend: Django ignores sessions
# whose backend is not in AUTHENTICATION_BACKENDS (the EmailOrUsername
# login backend replaced ModelBackend in settings).
session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
session[HASH_SESSION_KEY] = user.get_session_auth_hash()
session.create()
Path(os.environ["SITE_SESSION_FILE"]).write_text(session.session_key, encoding="utf-8")
