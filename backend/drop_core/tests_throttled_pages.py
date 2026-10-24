"""The 429s of signup and of verification emails (spec 4.9): the shell's throttled page, with Retry-After, the wait said in words, and the same bytes for every visitor refused for the same reason."""
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from drop_core import throttle
from drop_core.models import EmailSendLog, EmailVerification

User = get_user_model()
IP = "9.9.9.9"


def signup_data(name):
    return {"username": name, "password1": "s3cur3pass!!", "password2": "s3cur3pass!!", "email": f"{name}@example.com"}


def page(response):
    """The visible page: from <main> to </main>. A response with no <main> is a bare string, not the shell's page."""
    html = response.content.decode()
    if "<main" not in html:
        raise AssertionError(f"status {response.status_code} with no <main>, the body being {html[:80]!r}: answer with throttle.signup_throttled_response() "
                             "or throttle.verification_throttled_response() (backend/drop_core/views.py), which draw backend/templates/registration/throttled.html on the shell")
    return html[html.index("<main"):html.index("</main>")]


@override_settings(DEBUG=False)
class SignupThrottleTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def setUp(self):
        for _ in range(10):
            EmailSendLog.objects.create(ip_address=IP, purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT)

    def refused(self, name="first"):
        return self.client.post("/accounts/signup/", signup_data(name), REMOTE_ADDR=IP)

    def test_a_tenth_attempt_in_the_hour_gets_the_shells_throttled_page(self):
        response = self.refused()
        html = response.content.decode()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many signup attempts", page(response), "backend/drop_core/views.py: signup() answers with throttle.signup_throttled_response(), not a bare string")
        self.assertIn('<main id="main"', html, "backend/templates/registration/throttled.html: the page is on the shell")
        self.assertIn("<title>Too many signup attempts | Veilfile</title>", html, "backend/templates/registration/throttled.html: the title is the throttle's, then | Veilfile")
        self.assertEqual(response["Retry-After"], "3600", "backend/drop_core/throttle.py: signup_throttled_response() asks for the hour the limit counts")
        self.assertFalse(User.objects.filter(username="first").exists())

    def test_it_is_the_same_page_whoever_asks_and_whatever_they_posted(self):
        first = self.refused("zq-marker-one")
        second = self.refused("zq-marker-two")
        self.assertEqual(first.content, second.content,
                         "backend/templates/registration/throttled.html: the throttled page holds nothing of the request or the visitor")
        self.assertNotIn("csrfmiddlewaretoken", first.content.decode(), "backend/templates/registration/throttled.html: no form, no CSRF token")
        self.assertNotIn("zq-marker", first.content.decode())
        self.assertNotIn(IP, first.content.decode())

    def test_the_wait_reads_as_a_sentence(self):
        self.assertIn("Wait about 60 minutes, then try again.", page(self.refused()),
                      "backend/templates/registration/throttled.html: the sentence is `Wait about {{ retry_wait }}, then try again.`")

    def test_a_visitor_who_is_not_signed_in_is_offered_the_sign_in_pages(self):
        said = page(self.refused())
        self.assertIn('href="/accounts/login/"', said, "backend/templates/registration/throttled.html: sign_in_links is true by default; it shows Log in")
        self.assertIn(">Forgot password?<", said, "backend/templates/registration/throttled.html: sign_in_links is true by default; it shows Forgot password?")
        self.assertNotIn("Back to dashboard", said)

    def test_a_visitor_under_the_limit_still_gets_the_form(self):
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)


@override_settings(DEBUG=False)
class VerificationThrottleTests(TestCase):
    longMessage = False

    def signed_in(self, name):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345678")
        client = Client()
        client.force_login(user)
        return user, client

    def arrange(self, user, reason):
        """Put the user in the state resend_allowed() refuses for this reason."""
        record, _ = EmailVerification.objects.get_or_create(user=user, defaults={"email": user.email})
        if reason == "too_soon":
            record.last_sent_at = timezone.now()
        else:
            record.last_sent_at = timezone.now() - timedelta(hours=2)
        record.save()
        if reason == "account_limit":
            for _ in range(5):
                EmailSendLog.objects.create(email=user.email, user=user, purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT)
        if reason == "address_limit":
            for _ in range(5):
                EmailSendLog.objects.create(email=user.email, purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT)

    def refused(self, name, reason, action="resend"):
        user, client = self.signed_in(name)
        self.arrange(user, reason)
        data = {"action": action}
        if action == "update_email":
            data.update(email=user.email, password="pw12345678")
        return client.post("/accounts/verify-email/", data)

    def test_each_reason_gets_the_shells_throttled_page_and_its_own_wait(self):
        for reason, title, wait, said in (
            ("too_soon", "Verification email already sent", "60", "Wait about 1 minute, then try again."),
            ("account_limit", "Too many verification emails", "3600", "Wait about 60 minutes, then try again."),
            ("address_limit", "Too many verification emails", "86400", "Wait about 24 hours, then try again."),
        ):
            with self.subTest(reason=reason):
                response = self.refused(f"user-{reason}".replace("_", "-"), reason)
                self.assertEqual(response.status_code, 429)
                self.assertIn(title, page(response), "backend/drop_core/views.py: email_verification() answers with throttle.verification_throttled_response(), not a bare string")
                self.assertIn('<main id="main"', response.content.decode(), "backend/templates/registration/throttled.html: the page is on the shell")
                self.assertIn(f"<title>{title} | Veilfile</title>", response.content.decode(), "backend/templates/registration/throttled.html: the title is the throttle's, then | Veilfile")
                self.assertEqual(response["Retry-After"], wait, f"backend/drop_core/throttle.py: VERIFICATION_RETRY_AFTER[{reason!r}] is {wait}")
                self.assertIn(said, page(response), f"backend/drop_core/throttle.py: wait_in_words({wait}) must give the words in \"{said}\"")

    def test_changing_the_address_is_refused_the_same_way(self):
        response = self.refused("changer", "too_soon", action="update_email")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Verification email already sent", page(response))
        self.assertEqual(response["Retry-After"], "60")

    def test_the_page_is_the_same_for_every_visitor_refused_for_one_reason(self):
        for reason in ("too_soon", "account_limit", "address_limit"):
            with self.subTest(reason=reason):
                first = self.refused(f"first-{reason}".replace("_", "-"), reason)
                second = self.refused(f"second-{reason}".replace("_", "-"), reason)
                self.assertEqual(first.content, second.content,
                                 "backend/templates/registration/throttled.html: the throttled page holds nothing of the visitor or their address")
                self.assertNotIn("csrfmiddlewaretoken", first.content.decode())
                self.assertNotIn("example.com", first.content.decode())

    def test_a_signed_in_visitor_is_offered_the_way_back_not_the_sign_in_pages(self):
        for reason in ("too_soon", "account_limit", "address_limit"):
            with self.subTest(reason=reason):
                said = page(self.refused(f"back-{reason}".replace("_", "-"), reason))
                self.assertIn('<p class="form-links"><a class="label interactive" href="/app/">Back to dashboard</a></p>', said,
                              "backend/drop_core/throttle.py: verification_throttled_response() passes sign_in_links=False, and "
                              "backend/templates/registration/throttled.html then offers the dashboard as a secondary link")
                self.assertNotIn("/accounts/login/", said, "backend/templates/registration/throttled.html: a signed-in visitor is not sent to the sign-in page")
                self.assertNotIn("Forgot password?", said)

    def test_a_day_is_hours_and_one_minute_is_singular(self):
        said = page(self.refused("daily", "address_limit"))
        self.assertNotIn("1440", said, "backend/drop_core/throttle.py: wait_in_words() says hours from two hours up")
        self.assertIsNone(re.search(r"\b1 minutes\b", page(self.refused("quick", "too_soon"))),
                          "backend/drop_core/throttle.py: wait_in_words() says \"1 minute\", singular")


class WaitInWordsTests(SimpleTestCase):
    longMessage = False

    def test_minutes_under_two_hours_then_hours(self):
        for seconds, words in (
            (0, "1 minute"), (30, "1 minute"), (60, "1 minute"), (119, "1 minute"), (120, "2 minutes"), (900, "15 minutes"),
            (3600, "60 minutes"), (7140, "119 minutes"), (7200, "2 hours"), (10800, "3 hours"), (86400, "24 hours"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(throttle.wait_in_words(seconds), words,
                                 f"backend/drop_core/throttle.py: wait_in_words({seconds}) must be \"{words}\" (whole minutes under 120, else whole hours)")
