"""A link to a file that is not there (spec 4.9): one page, the same bytes and headers for every kind of miss, and the file's bytes untouched when it is found."""
from contextlib import nullcontext
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from drop_core.models import APIKey, Artifact, get_or_create_workspace
from drop_core.services import revoke_artifact, upload_artifact
from drop_core.storage import get_storage
from pages import errors
from pages.tests_errors import SECRET, main, manifest_with_nothing_in_it

User = get_user_model()
UNKNOWN_TOKEN = "u" * 43
WORDS = "If this link was shared with you, its moment may have passed."


class NoSuchKey(Exception):
    """What the S3 and R2 client raises for an object that is gone."""

    response = {"Error": {"Code": "NoSuchKey", "Message": "gone"}}


def everything_a_reader_gets(response):
    """The status, the body, every header and every cookie of a response: what two answers must share to be told apart by no one."""
    return (response.status_code, response.content, sorted((name.lower(), value) for name, value in response.headers.items()),
            sorted(response.cookies.keys()))


@override_settings(DEBUG=False)
class ArtifactMissTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def setUp(self):
        user = User.objects.create_user(username="owner", password="pw12345678")
        self.workspace = get_or_create_workspace(user)
        self.key, _raw = APIKey.create_key(self.workspace, "key")
        self.user = user

    def artifact(self, name="note.txt"):
        return upload_artifact(self.workspace, self.key, name, b"hello", "text/plain")

    def misses(self):
        """{kind: the address of a link that leads nowhere for that reason}. Each is set up and left in place for the request."""
        gone_object = self.artifact("gone-object.txt")
        get_storage().delete(gone_object.token)  # the row is there, the stored file is not
        expired = self.artifact("expired.txt")
        Artifact.objects.filter(id=expired.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        held = self.artifact("held.txt")
        Artifact.objects.filter(id=held.id).update(safety_status="held")
        revoked = self.artifact("revoked.txt")
        revoked_token = revoked.token
        revoke_artifact(self.workspace, revoked.id)
        no_such_key = self.artifact("no-such-key.txt")
        return {
            "unknown": f"/a/{UNKNOWN_TOKEN}",
            "expired": f"/a/{expired.token}",
            "revoked": f"/a/{revoked_token}",
            "held": f"/a/{held.token}",
            "object missing": f"/a/{gone_object.token}",
            "no such key": f"/a/{no_such_key.token}",
        }

    def get_each(self):
        """{kind: response}, every miss fetched the same way."""
        answers = {}
        for kind, address in self.misses().items():
            if kind == "no such key":
                with mock.patch("drop_core.storage.LocalStorage.open", side_effect=NoSuchKey()):
                    answers[kind] = self.client.get(address)
            elif kind == "object missing":
                with self.assertLogs("drop_core.views", level="WARNING"):
                    answers[kind] = self.client.get(address)
            else:
                answers[kind] = self.client.get(address)
        return answers

    def test_every_kind_of_miss_is_the_same_answer_to_the_byte(self):
        answers = self.get_each()
        expected = everything_a_reader_gets(answers["unknown"])
        self.assertEqual(expected[0], 404, "backend/drop_core/views.py: a miss on /a/<token> answers 404 (the CLIs and docs/API.md depend on it)")
        for kind, response in answers.items():
            with self.subTest(kind=kind):
                self.assertEqual(everything_a_reader_gets(response), expected,
                                 f"backend/drop_core/views.py: serve_artifact() must answer a {kind} link with pages.errors.artifact_miss(), "
                                 "and nothing else; the status, body, headers and cookies must equal an unknown token's")

    def test_the_page_says_only_the_spec_words(self):
        response = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        html = response.content.decode()
        self.assertIn("<h1 class=\"chapter__title\">Nothing here.</h1>", html, "backend/templates/404.html: the heading for a file link is Nothing here.")
        self.assertIn(f"<p>{WORDS}</p>", html, "backend/templates/404.html: the sentence for a file link is the spec's (4.9)")
        self.assertIn("<title>Nothing here | Veilfile</title>", html)
        self.assertIn('<main id="main"', html, "backend/templates/404.html: the page is on the shell")
        for text in (UNKNOWN_TOKEN, "expired", "revoked", "held", "found", "Not found", "/a/"):
            with self.subTest(text=text):
                self.assertNotIn(text, main(html), "backend/templates/404.html: the page must not say which kind of miss it is, or echo the address")

    def test_it_is_as_private_as_a_file_that_is_found(self):
        response = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store", "backend/pages/errors.py: artifact_miss() sets the headers a found file has")
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow", "backend/pages/errors.py: artifact_miss() sets the headers a found file has")
        self.assertIn('<meta name="robots" content="noindex">', response.content.decode())

    def test_it_never_reads_the_session(self):
        # The page is drawn with no request, so a signed-in visitor, a visitor with a stale cookie and a visitor with none
        # all get the same bytes, no Vary header, and the one query that looks the token up.
        anonymous = self.client.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertNotIn("Vary", anonymous.headers, "backend/pages/errors.py: artifact_miss() must draw the page with no request; the shell's menu reads the session, which adds Vary: Cookie")
        signed_in = Client()
        signed_in.force_login(self.user)
        with self.assertNumQueries(1):
            response = signed_in.get(f"/a/{UNKNOWN_TOKEN}")
        self.assertEqual(everything_a_reader_gets(response), everything_a_reader_gets(anonymous),
                         "backend/pages/errors.py: artifact_miss() must not read the session; a signed-in visitor sees the same page")
        stale = Client()
        stale.cookies["sessionid"] = "not-a-session"
        with self.assertNumQueries(1):
            self.assertEqual(everything_a_reader_gets(stale.get(f"/a/{UNKNOWN_TOKEN}")), everything_a_reader_gets(anonymous))

    @override_settings(DEBUG=True)
    def test_with_debug_on_the_miss_is_still_this_page(self):
        # Django answers a raised Http404 with its technical page under DEBUG, which prints the token and which kind of
        # miss it was. The view returns the page instead of raising, so the dev server and the browser checks see it too.
        for kind, address in self.misses().items():
            if kind == "no such key":
                continue
            with self.subTest(kind=kind):
                with self.assertLogs("drop_core.views", level="WARNING") if kind == "object missing" else nullcontext():
                    response = self.client.get(address)
                html = response.content.decode()
                self.assertEqual(response.status_code, 404)
                self.assertIn("Nothing here.", html, "backend/drop_core/views.py: serve_artifact() returns artifact_miss(); it must not raise Http404, which DEBUG answers with a page that names the token")
                self.assertNotIn(address.rsplit("/", 1)[1], html)

    def test_the_one_log_line_is_the_redacted_one(self):
        token = self.artifact().token
        Artifact.objects.filter(token=token).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertLogs("django.request", level="WARNING") as logs:
            response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(logs.output, ["WARNING:django.request:Not Found: /a/<redacted>"])

    def test_a_link_with_a_slash_or_more_is_the_ordinary_404_for_a_live_token_and_an_unknown_one_alike(self):
        live = self.artifact().token
        first = self.client.get(f"/a/{live}/")
        second = self.client.get(f"/a/{UNKNOWN_TOKEN}/")
        self.assertEqual(first.status_code, 404)
        self.assertIn("Not found.", first.content.decode())
        self.assertEqual(everything_a_reader_gets(first), everything_a_reader_gets(second),
                         "backend/templates/404.html: the 404 for an address that does not resolve must not depend on the token in it")

    def test_a_file_that_is_found_is_the_files_bytes_and_nothing_else(self):
        artifact = self.artifact()
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"hello")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Content-Security-Policy"], "sandbox allow-downloads")

    def test_a_storage_outage_is_still_the_plain_503(self):
        artifact = self.artifact()
        with mock.patch("drop_core.storage.LocalStorage.open", side_effect=RuntimeError("no bucket")):
            with self.assertLogs("drop_core", level="ERROR"):
                response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503, "backend/drop_core/views.py: a storage outage is a 503, not a miss; the link is still valid")
        self.assertEqual(response["Retry-After"], "30")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertNotIn("<html", response.content.decode())


@override_settings(DEBUG=False)
class ArtifactMissThatCannotBeDrawnTests(TestCase):
    longMessage = False

    def setUp(self):
        user = User.objects.create_user(username="owner", password="pw12345678")
        self.workspace = get_or_create_workspace(user)
        self.key, _raw = APIKey.create_key(self.workspace, "key")

    def expired_token(self):
        artifact = upload_artifact(self.workspace, self.key, "late.txt", b"hello", "text/plain")
        Artifact.objects.filter(id=artifact.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        return artifact.token

    def test_the_fallback_is_the_same_for_every_kind_of_miss_and_has_the_spec_words(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()), self.assertLogs("pages.errors", level="ERROR"):
            unknown = self.client.get(f"/a/{UNKNOWN_TOKEN}")
            expired = self.client.get(f"/a/{self.expired_token()}")
        self.assertEqual(unknown.status_code, 404, "backend/pages/errors.py: artifact_miss() keeps the 404 when the page cannot be drawn")
        self.assertIn(WORDS, unknown.content.decode())
        self.assertIn("<h1>Nothing here.</h1>", unknown.content.decode())
        self.assertEqual(everything_a_reader_gets(unknown), everything_a_reader_gets(expired))
        self.assertEqual(unknown["Cache-Control"], "no-store")
        self.assertEqual(unknown["X-Robots-Tag"], "noindex, nofollow")

    def test_the_log_of_that_failure_holds_no_token(self):
        # The token is in the path the view was given, in the Resolver404 quoted for a slash, and in the message of the
        # FileNotFoundError that a missing stored file raises: the log must carry none of them.
        artifact = upload_artifact(self.workspace, self.key, "late.txt", b"hello", "text/plain")
        token = artifact.token
        with mock.patch("drop_core.storage.LocalStorage.open", side_effect=FileNotFoundError(f"/media/artifacts/{token}")), \
                mock.patch("pages.errors.render_to_string", side_effect=RuntimeError("template failed")), \
                self.assertLogs("drop_core.views", level="WARNING"), \
                self.assertLogs("pages.errors", level="ERROR") as missing_object:
            response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 404)
        with mock.patch("pages.registry.menu_groups", side_effect=RuntimeError("menu failed")), \
                self.assertLogs("pages.errors", level="ERROR") as resolver_404:
            self.client.get(f"/a/{token}/more")
        for output in (missing_object.output, resolver_404.output):
            with self.subTest(log=output[0][:60]):
                self.assertNotIn(token, "\n".join(output),
                                 "backend/pages/errors.py: log_failure() must cut the exception's __context__; it quotes the token "
                                 "(the stored file's name, the Resolver404's path) and this logger does not redact")
        self.assertIn("template failed", "\n".join(missing_object.output), "the failure itself is still logged, with its traceback")
