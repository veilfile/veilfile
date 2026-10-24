"""client_ip() prefers CF-Connecting-IP (VEILFILE_CLIENT_IP_HEADER) when the
request arrived from a Cloudflare edge address, then the trusted-hop
X-Forwarded-For logic, then REMOTE_ADDR, and never returns non-IP text.
Every throttle call site goes through client_ip."""

import re
from pathlib import Path

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import email_verification
from .email_verification import client_ip, throttle_ip
from .models import EmailSendLog

CLIENT = "81.2.69.160"
CLIENT6 = "2a02:6b8:b081:1234:5678:9abc:def0:1234"
EDGE = "172.69.4.12"
SPOOF = "6.6.6.6"
INTERNAL = "10.217.3.4"


class ClientIpHeaderTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def ip(self, remote="10.217.0.9", **headers):
        extra = {"REMOTE_ADDR": remote}
        for name, value in headers.items():
            extra[name] = value
        return client_ip(self.factory.get("/", **extra))

    def test_cf_header_wins_over_spoofed_xff(self):
        for xff in (
            f"{SPOOF}, {CLIENT}, {EDGE}",
            f"{SPOOF}, {SPOOF}, {SPOOF}, {EDGE}",
            f"{SPOOF}, {EDGE}, {INTERNAL}",
        ):
            self.assertEqual(
                self.ip(HTTP_CF_CONNECTING_IP=CLIENT, HTTP_X_FORWARDED_FOR=xff),
                CLIENT,
            )

    def test_cf_header_ignores_other_spoofed_headers(self):
        self.assertEqual(
            self.ip(
                HTTP_CF_CONNECTING_IP=CLIENT,
                HTTP_X_FORWARDED_FOR=f"{SPOOF}, {EDGE}",
                HTTP_X_REAL_IP=SPOOF,
                HTTP_TRUE_CLIENT_IP=SPOOF,
                HTTP_FORWARDED=f"for={SPOOF}",
                HTTP_X_CLIENT_IP=SPOOF,
            ),
            CLIENT,
        )

    def test_cf_header_ipv6(self):
        via_cf = f"{SPOOF}, {EDGE}"
        self.assertEqual(
            self.ip(HTTP_CF_CONNECTING_IP=CLIENT6, HTTP_X_FORWARDED_FOR=via_cf), CLIENT6
        )
        self.assertEqual(
            throttle_ip(self.ip(HTTP_CF_CONNECTING_IP=CLIENT6, HTTP_X_FORWARDED_FOR=via_cf)),
            "2a02:6b8:b081:1234::/64",
        )

    def test_zone_scoped_ipv6_is_never_a_client_ip(self):
        # 'fe80::1%eth0' parses as an IPv6Address; no internet client has a
        # zone index, so it must fall through instead of reaching audit fields.
        self.assertEqual(
            self.ip(HTTP_CF_CONNECTING_IP="fe80::1%eth0", HTTP_X_FORWARDED_FOR=f"{CLIENT}, {EDGE}"),
            CLIENT,
        )
        self.assertEqual(self.ip(remote="fe80::1%eth0"), None)

    def test_cf_header_is_normalized(self):
        self.assertEqual(
            self.ip(
                HTTP_CF_CONNECTING_IP=" 2A02:6B8:B081:1234:0:0:0:1 ",
                HTTP_X_FORWARDED_FOR=f"{SPOOF}, {EDGE}",
            ),
            "2a02:6b8:b081:1234::1",
        )

    def test_invalid_cf_header_falls_back_to_xff_then_remote(self):
        for bad in ("garbage", "1.2.3.4, 5.6.7.8", f"{CLIENT}:443", "", "<script>"):
            self.assertEqual(
                self.ip(HTTP_CF_CONNECTING_IP=bad, HTTP_X_FORWARDED_FOR=f"{SPOOF}, {CLIENT}, {EDGE}"),
                CLIENT,
            )
            self.assertEqual(self.ip(remote="8.8.8.8", HTTP_CF_CONNECTING_IP=bad), "8.8.8.8")
        self.assertIsNone(self.ip(remote="", HTTP_CF_CONNECTING_IP="garbage"))

    def test_absent_cf_header_xff_layouts(self):
        self.assertEqual(self.ip(HTTP_X_FORWARDED_FOR=CLIENT), CLIENT)
        self.assertEqual(self.ip(HTTP_X_FORWARDED_FOR=f"{CLIENT}, {EDGE}"), CLIENT)
        self.assertEqual(self.ip(HTTP_X_FORWARDED_FOR=f"{SPOOF}, {CLIENT}, {EDGE}"), CLIENT)
        self.assertEqual(
            self.ip(HTTP_X_FORWARDED_FOR=f"{SPOOF}, {CLIENT}, {EDGE}, {INTERNAL}, 10.1.1.1"),
            CLIENT,
        )
        self.assertEqual(self.ip(HTTP_X_FORWARDED_FOR=f"{CLIENT6}, {EDGE}"), CLIENT6)

    def test_absent_cf_header_never_returns_text(self):
        self.assertEqual(
            self.ip(remote="10.0.0.7", HTTP_X_FORWARDED_FOR=f"evil<b>, {EDGE}"), "10.0.0.7"
        )

    @override_settings(VEILFILE_CLIENT_IP_HEADER="")
    def test_empty_setting_disables_cf_header(self):
        self.assertEqual(
            self.ip(HTTP_CF_CONNECTING_IP=SPOOF, HTTP_X_FORWARDED_FOR=f"{CLIENT}, {EDGE}"),
            CLIENT,
        )

    @override_settings(VEILFILE_CLIENT_IP_HEADER="X-Edge-Client")
    def test_header_name_is_configurable(self):
        self.assertEqual(
            self.ip(
                HTTP_X_EDGE_CLIENT=CLIENT,
                HTTP_CF_CONNECTING_IP=SPOOF,
                HTTP_X_FORWARDED_FOR=f"{SPOOF}, {EDGE}",
            ),
            CLIENT,
        )

    @override_settings(VEILFILE_TRUSTED_PROXY_HOPS=0)
    def test_cf_header_independent_of_hop_setting(self):
        self.assertEqual(
            self.ip(HTTP_CF_CONNECTING_IP=CLIENT, HTTP_X_FORWARDED_FOR=f"{SPOOF}, {EDGE}"),
            CLIENT,
        )

    def test_cf_header_ignored_when_peer_is_not_cloudflare(self):
        # A request that reached the origin without Cloudflare can carry a
        # forged header; the proxy-appended peer (last public hop) gives it away.
        # The address used instead is that peer (8.8.4.4 below), never an entry
        # left of it, even the one two places from the right: the client typed
        # those (see DirectToOriginTests).
        forged = "9.9.9.9"
        for xff, expected in (
            (SPOOF, SPOOF),
            (f"{CLIENT}, 8.8.4.4", "8.8.4.4"),
            (f"{CLIENT}, 8.8.4.4, {INTERNAL}", "8.8.4.4"),
            (f"{CLIENT}, {EDGE}, 8.8.4.4", "8.8.4.4"),
        ):
            with self.subTest(xff=xff):
                got = self.ip(HTTP_CF_CONNECTING_IP=forged, HTTP_X_FORWARDED_FOR=xff)
                self.assertNotEqual(got, forged)
                self.assertEqual(got, expected)

    def test_cf_header_ignored_without_a_forwarded_chain(self):
        self.assertEqual(self.ip(HTTP_CF_CONNECTING_IP="9.9.9.9"), "10.217.0.9")

    def test_untrusted_header_warns_once_per_process(self):
        email_verification._warned_untrusted_client_ip_header = False
        with self.assertLogs("drop_core.email_verification", "WARNING") as logs:
            self.ip(HTTP_CF_CONNECTING_IP="9.9.9.9", HTTP_X_FORWARDED_FOR=SPOOF)
            self.ip(HTTP_CF_CONNECTING_IP="9.9.9.9", HTTP_X_FORWARDED_FOR=SPOOF)
        self.assertEqual(len(logs.records), 1)
        self.assertNotIn("9.9.9.9", logs.output[0])

    def test_cloudflare_ranges(self):
        from ipaddress import ip_address

        for ip in (EDGE, "104.16.0.1", "2606:4700::1", "2a06:98c0::5"):
            self.assertTrue(email_verification._is_cloudflare(ip_address(ip)), ip)
        for ip in ("8.8.4.4", SPOOF, CLIENT, INTERNAL, "2001:db8::1"):
            self.assertFalse(email_verification._is_cloudflare(ip_address(ip)), ip)


class ThrottleCallSiteTests(TestCase):
    def test_no_direct_forwarded_header_reads(self):
        root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"HTTP_X_FORWARDED_FOR|HTTP_CF_CONNECTING_IP|X-Forwarded-For\"\)")
        offenders = []
        for path in root.rglob("*.py"):
            if path.name.startswith("tests") or "migrations" in path.parts:
                continue
            if path.name == "email_verification.py":
                continue
            if pattern.search(path.read_text()):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_no_raw_remote_addr_throttle_keys(self):
        root = Path(__file__).resolve().parent
        for name in ("throttle.py", "device.py", "oauth.py"):
            self.assertNotIn("REMOTE_ADDR", (root / name).read_text(), name)

    def test_signup_records_cf_ip_not_spoofed_xff(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "cfuser",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "cfuser@example.com",
            },
            REMOTE_ADDR="10.217.0.9",
            HTTP_X_FORWARDED_FOR=f"{SPOOF}, {SPOOF}, {EDGE}",
            HTTP_CF_CONNECTING_IP=CLIENT,
        )
        self.assertEqual(response.status_code, 302)
        ips = set(EmailSendLog.objects.values_list("ip_address", flat=True))
        self.assertEqual(ips, {CLIENT})

    def test_password_reset_throttle_keyed_on_cf_ip(self):
        # An attacker rotating X-Forwarded-For must not escape the per-IP limit.
        from . import throttle

        limit = throttle.PASSWORD_RESET_MAX_PER_IP
        for i in range(limit + 1):
            self.client.post(
                reverse("password_reset"),
                {"email": f"u{i}@example.com"},
                HTTP_X_FORWARDED_FOR=f"9.9.9.{i}, 9.9.8.{i}, {EDGE}",
                HTTP_CF_CONNECTING_IP=CLIENT,
                REMOTE_ADDR="10.217.0.9",
            )
        self.assertFalse(throttle.password_reset_ip_allowed(CLIENT))


class DirectToOriginTests(TestCase):
    """A request that reached the platform without passing Cloudflare has no Cloudflare entry at the end of its
    X-Forwarded-For. The entry the platform's proxy appended (the last public one) is the client; everything left of it was
    typed by the client. Counting TRUSTED_PROXY_HOPS positions from the right there would land on an entry the client chose,
    and rotating it would give every per-IP throttle a fresh budget (or burn a victim's)."""

    longMessage = False
    PEER = "8.8.4.4"  # appended by the platform's proxy: the real connecting address

    def setUp(self):
        self.factory = RequestFactory()

    def ip(self, xff, remote="10.217.0.9", **headers):
        extra = {"REMOTE_ADDR": remote, "HTTP_X_FORWARDED_FOR": xff, **headers}
        return client_ip(self.factory.get("/", **extra))

    def test_entries_left_of_a_non_cloudflare_peer_are_never_the_client(self):
        for xff in (
            f"{SPOOF}, {self.PEER}",
            f"{SPOOF}, {SPOOF}, {self.PEER}",
            f"{SPOOF}, {CLIENT}, {self.PEER}",
            f"{SPOOF}, {self.PEER}, {INTERNAL}, 10.1.1.1",
        ):
            with self.subTest(xff=xff):
                self.assertEqual(
                    self.ip(xff), self.PEER,
                    f"{xff!r}: the last public entry is the client when it is not a Cloudflare address, not an entry to its left",
                )

    def test_rotating_the_client_typed_entry_keeps_one_identity(self):
        seen = {self.ip(f"7.7.7.{i}, {self.PEER}") for i in range(50)}
        self.assertEqual(seen, {self.PEER}, "a client rotating the entry it types must stay one throttle identity")

    def test_a_forged_cloudflare_looking_entry_left_of_the_peer_changes_nothing(self):
        self.assertEqual(self.ip(f"{EDGE}, {SPOOF}, {self.PEER}"), self.PEER)
        self.assertEqual(self.ip(f"{SPOOF}, {self.PEER}", HTTP_CF_CONNECTING_IP=SPOOF), self.PEER)

    def test_the_hop_count_does_not_reach_past_the_peer_without_cloudflare(self):
        for hops in (1, 2, 3, 5):
            with self.subTest(hops=hops), override_settings(VEILFILE_TRUSTED_PROXY_HOPS=hops):
                self.assertEqual(self.ip(f"{SPOOF}, {SPOOF}, {SPOOF}, {self.PEER}"), self.PEER)

    def test_unparseable_peer_falls_back_to_remote_addr(self):
        self.assertEqual(self.ip(f"{SPOOF}, garbage", remote="10.0.0.7"), "10.0.0.7")

    def test_through_cloudflare_the_hop_logic_is_unchanged(self):
        self.assertEqual(self.ip(f"{SPOOF}, {CLIENT}, {EDGE}"), CLIENT)
        self.assertEqual(self.ip(f"{SPOOF}, {SPOOF}, {CLIENT}, {EDGE}, {INTERNAL}"), CLIENT)
        self.assertEqual(self.ip(f"{CLIENT6}, {EDGE}"), CLIENT6)
        self.assertEqual(self.ip(f"{CLIENT}, {EDGE}"), CLIENT)

    def test_a_lone_entry_is_the_client(self):
        self.assertEqual(self.ip(self.PEER), self.PEER)
