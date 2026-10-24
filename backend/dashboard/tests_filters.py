"""The dashboard's template filters: utc_time, a time written in UTC for static/js/behaviors/local-time.js to rewrite, and bytes, a size in words."""
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase, override_settings

FILE = "backend/dashboard/templatetags/dashboard.py"
TEMPLATES = Path(settings.BASE_DIR) / "templates"
# The built-in filters that would print a time in the site's zone (with no zone named) or a size in other words than the dashboard's.
BUILT_IN_FILTERS = re.compile(r"\|\s*(date|time|timesince|timeuntil|naturaltime|naturalday|filesizeformat)\b")


def render(source, **context):
    return engines["django"].from_string("{% load dashboard %}" + source).render(context)


def utc_time(value, *words):
    """{{ value|utc_time }}, or {{ value|utc_time:words }} with the words for no time."""
    return render("{{ value|utc_time:words }}" if words else "{{ value|utc_time }}", value=value, words=words[0] if words else None)


def time_element(attribute, words):
    return f'<time datetime="{attribute}" data-local>{words}</time>'


class UtcTimeTests(SimpleTestCase):
    longMessage = False

    def test_a_time_is_written_in_utc_inside_a_time_element_the_script_can_find(self):
        moment = datetime(2026, 10, 22, 13, 58, 55, 802591, tzinfo=timezone.utc)
        self.assertEqual(utc_time(moment), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"),
                         f"{FILE}: utc_time writes <time datetime=\"YYYY-MM-DDTHH:MMZ\" data-local>D Mon YYYY, HH:MM UTC</time>, with no seconds and no microseconds")

    def test_the_json_endpoints_timestamps_have_microseconds_and_a_plus_zero_offset(self):
        # Every timestamp the API answers is datetime.isoformat() of a UTC-aware value: 2026-10-22T13:58:55.802591+00:00. The dashboard's views hand these strings on.
        self.assertEqual(utc_time("2026-10-22T13:58:55.802591+00:00"), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"),
                         f"{FILE}: utc_time takes the ISO strings the JSON endpoints answer with, microseconds and +00:00 included")

    def test_a_string_ending_in_z_or_with_no_zone_or_a_date_alone_is_read(self):
        self.assertEqual(utc_time("2026-10-22T13:58:55Z"), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"))
        self.assertEqual(utc_time("2026-10-22T13:58:55"), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"), f"{FILE}: no zone means UTC")
        self.assertEqual(utc_time("2026-10-22"), time_element("2026-10-22T00:00Z", "22 Oct 2026, 00:00 UTC"))

    @override_settings(TIME_ZONE="Asia/Tokyo")
    def test_the_sites_own_zone_is_not_used(self):
        # With settings.TIME_ZONE at Asia/Tokyo, a Django template would write 22:58 for this moment. The page says UTC, so the filter converts.
        summer = datetime(2026, 10, 22, 22, 58, tzinfo=ZoneInfo("Asia/Tokyo"))   # UTC+9
        winter = datetime(2026, 12, 1, 22, 5, tzinfo=ZoneInfo("Asia/Tokyo"))     # UTC+9, all year
        self.assertEqual(utc_time(summer), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"), f"{FILE}: convert to UTC with astimezone, never format in the current zone")
        self.assertEqual(utc_time(winter), time_element("2026-12-01T13:05Z", "1 Dec 2026, 13:05 UTC"))

    def test_another_offset_is_converted_and_can_change_the_day(self):
        self.assertEqual(utc_time("2026-10-22T23:30:00+05:30"), time_element("2026-10-22T18:00Z", "22 Oct 2026, 18:00 UTC"))
        self.assertEqual(utc_time("2026-10-22T23:59:59-04:00"), time_element("2026-10-23T03:59Z", "23 Oct 2026, 03:59 UTC"))

    def test_a_naive_datetime_is_taken_for_utc(self):
        self.assertEqual(utc_time(datetime(2026, 1, 5, 0, 0)), time_element("2026-01-05T00:00Z", "5 Jan 2026, 00:00 UTC"),
                         f"{FILE}: a datetime with no zone is UTC (the database gives aware values; this keeps a stray naive one from failing)")

    def test_seconds_are_cut_not_rounded(self):
        self.assertEqual(utc_time(datetime(2026, 10, 22, 13, 58, 59, 999999, tzinfo=timezone.utc)), time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC"))

    def test_the_day_has_no_leading_zero_and_the_minutes_do(self):
        self.assertEqual(utc_time(datetime(2026, 3, 4, 7, 5, tzinfo=timezone.utc)), time_element("2026-03-04T07:05Z", "4 Mar 2026, 07:05 UTC"))

    def test_every_month_is_in_english_whatever_the_language(self):
        words = [utc_time(datetime(2026, month, 1, tzinfo=timezone.utc)).split(">")[1].split(" ")[1] for month in range(1, 13)]
        self.assertEqual(words, ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])

    def test_nothing_gives_nothing_or_the_words_the_page_asks_for(self):
        for value in (None, "", "not a time", date(2026, 10, 22), 12345):
            with self.subTest(value=value):
                self.assertEqual(utc_time(value), "", f"{FILE}: no time, or one it cannot read, is an empty string, so a template can write its own words with |default")
                self.assertEqual(utc_time(value, "Never"), "Never", f"{FILE}: utc_time:\"Never\" writes the words it is given when there is no time")
        self.assertEqual(render('{{ value|utc_time|default:"Never" }}', value=None), "Never")

    def test_the_words_given_for_no_time_are_escaped(self):
        self.assertEqual(render("{{ value|utc_time:words }}", value=None, words="<b>Never</b>"), "&lt;b&gt;Never&lt;/b&gt;")

    def test_the_element_is_not_escaped_twice(self):
        html = render("<p>{{ value|utc_time }}</p>", value=datetime(2026, 10, 22, 13, 58, tzinfo=timezone.utc))
        self.assertEqual(html, "<p>" + time_element("2026-10-22T13:58Z", "22 Oct 2026, 13:58 UTC") + "</p>", f"{FILE}: utc_time returns safe markup")


class BytesTests(SimpleTestCase):
    longMessage = False

    def say(self, value):
        return render("{{ value|bytes }}", value=value)

    def test_under_a_kilobyte_is_a_count_of_bytes(self):
        for value, words in ((0, "0 B"), (1, "1 B"), (999, "999 B"), (1023, "1023 B")):
            with self.subTest(value=value):
                self.assertEqual(self.say(value), words, f"{FILE}: under 1024 the size is N B")

    def test_the_base_is_1024_with_one_decimal(self):
        for value, words in ((1024, "1.0 KB"), (1100, "1.1 KB"), (1536, "1.5 KB"), (15 * 1024 + 512, "15.5 KB"), (1024 ** 2, "1.0 MB"),
                             (int(2.5 * 1024 ** 2), "2.5 MB"), (1024 ** 3, "1.0 GB"), (25 * 1024 ** 3, "25.0 GB")):
            with self.subTest(value=value):
                self.assertEqual(self.say(value), words, f"{FILE}: sizes are 1024-based with one decimal: x.x KB, x.x MB, x.x GB")

    def test_a_size_that_rounds_up_to_the_next_unit_is_written_in_it(self):
        # 1048575 B is 1023.999 KB, which one decimal writes as 1024.0 KB; it reads 1.0 MB.
        self.assertEqual(self.say(1024 ** 2 - 1), "1.0 MB", f"{FILE}: never write 1024.0 KB; step up a unit when the rounded figure reaches 1024")
        self.assertEqual(self.say(1024 ** 3 - 1), "1.0 GB")

    def test_there_is_no_unit_above_a_gigabyte(self):
        self.assertEqual(self.say(5000 * 1024 ** 3), "5000.0 GB", f"{FILE}: GB is the largest unit")

    def test_a_number_in_a_string_is_read_and_anything_else_is_nothing(self):
        self.assertEqual(self.say("2048"), "2.0 KB")
        for value in (None, "", "many", -1, float("nan"), float("inf"), [1]):
            with self.subTest(value=value):
                self.assertEqual(self.say(value), "", f"{FILE}: bytes of anything that is not a size of zero or more is an empty string")


class FilterConventionTests(SimpleTestCase):
    longMessage = False

    def test_a_dashboard_template_writes_times_with_utc_time_and_sizes_with_bytes(self):
        for path in sorted((TEMPLATES / "dashboard").rglob("*.html")):
            found = BUILT_IN_FILTERS.findall(path.read_text(encoding="utf-8"))
            with self.subTest(template=path.name):
                self.assertEqual(found, [], f"backend/templates/dashboard/{path.name}: write a time as {{{{ when|utc_time }}}} and a size as {{{{ size|bytes }}}} ({{% load dashboard %}}). "
                                 "The built-in date and time filters print the site's zone, with no zone named, where the dashboard says UTC and lets the visitor's browser say its own")

    def test_the_check_finds_what_it_names(self):
        for text in ('{{ a|date:"Y" }}', "{{ a|time }}", "{{ a | timesince }}", "{{ a|naturaltime }}", "{{ a|filesizeformat }}"):
            with self.subTest(text=text):
                self.assertTrue(BUILT_IN_FILTERS.search(text), "backend/dashboard/tests_filters.py: BUILT_IN_FILTERS must find each built-in time and size filter")
        for text in ("{{ a|utc_time }}", "{{ a|bytes }}", "{{ updated }}", "<time datetime=\"x\">"):
            with self.subTest(text=text):
                self.assertFalse(BUILT_IN_FILTERS.search(text), "backend/dashboard/tests_filters.py: BUILT_IN_FILTERS must not find the dashboard's own filters or a word that starts like one")
