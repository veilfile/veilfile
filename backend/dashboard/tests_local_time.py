"""static/js/behaviors/local-time.js (spec 4.8): the server's UTC text becomes the visitor's own time. The script is read here, not run (these tests have no browser);
the dashboard's browser checks run it."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from dashboard.tests_filters import render

JS = Path(settings.BASE_DIR) / "static" / "js"


class LocalTimeScriptTests(SimpleTestCase):
    """static/js/behaviors/local-time.js (spec 4.8): the server's UTC text becomes the visitor's own time. The script is read here, not run (no browser in
    these tests); the browser checks of the dashboard run it."""
    longMessage = False
    FILE = "backend/static/js/behaviors/local-time.js"

    def setUp(self):
        self.script = (JS / "behaviors" / "local-time.js").read_text(encoding="utf-8")

    def test_it_finds_the_elements_utc_time_writes_and_reads_the_machine_readable_time(self):
        html = render("{{ value|utc_time }}", value="2026-10-22T13:58:55+00:00")
        self.assertIn(" data-local>", html)
        self.assertIn('document.querySelectorAll("time[data-local]")', self.script, f"{self.FILE}: it rewrites every <time data-local>, the markup utc_time writes")
        self.assertIn("new Date(time.dateTime)", self.script, f"{self.FILE}: it reads the datetime attribute, never the UTC text")

    def test_it_formats_in_the_visitors_own_zone_and_names_it(self):
        self.assertIn('new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" })', self.script,
                      f"{self.FILE}: Intl.DateTimeFormat in the visitor's locale: the date, the time to the minute and the zone's short name (timeZoneName: \"short\")")
        options = re.search(r"new Intl\.DateTimeFormat\(undefined, \{(.*?)\}\)", self.script, re.S)
        self.assertTrue(options, f"{self.FILE}: the formatter's options are an object literal")
        self.assertEqual(sorted(re.findall(r"(\w+)\s*:", options.group(1))), ["day", "hour", "minute", "month", "timeZoneName", "year"],
                         f"{self.FILE}: exactly these options: no dateStyle or timeStyle (they cannot be combined with timeZoneName) and no timeZone, which would pin one zone for every visitor")
        self.assertNotRegex(self.script, r"\btimeZone\s*:", f"{self.FILE}: no timeZone option: the zone is the visitor's own (timeZoneName only says which it is)")

    def test_a_time_it_cannot_read_keeps_its_utc_text_and_no_title_is_added(self):
        self.assertIn("if (!Number.isNaN(moment.getTime())) time.textContent = format.format(moment);", self.script,
                      f"{self.FILE}: a time that does not parse keeps the text the server wrote")
        self.assertNotIn("title", self.script.lower(), f"{self.FILE}: no title attribute; a tooltip is not read on a phone or by keyboard, and the UTC text is for visitors with no script")

    def test_it_runs_on_load_and_after_each_change_in_place(self):
        self.assertIn("\nlocalTimes();\n", self.script, f"{self.FILE}: run once on load (a module runs after the page is parsed)")
        self.assertIn('document.addEventListener("veilfile:swapped", localTimes);', self.script, f"{self.FILE}: run after each change in place, delegated from the document")
        self.assertIn('new CustomEvent("veilfile:swapped")', (JS / "shell.js").read_text(encoding="utf-8"), "backend/static/js/shell.js fires veilfile:swapped after a swap")
