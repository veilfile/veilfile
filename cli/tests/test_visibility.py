"""`veilfile push --visibility` (team seats PR E3)."""

import unittest
from pathlib import Path

from . import test_veilfile as base

URL = "https://veilfile.com/a/abc123"


class PushVisibilityTests(unittest.TestCase):
    # CliTestCase's setup and helpers, without its own tests, which subclassing would run a second time.
    setUp = base.CliTestCase.setUp
    run_cli = base.CliTestCase.run_cli
    _write_key_file = base.CliTestCase._write_key_file

    def push(self, extra, answer):
        self._write_key_file()
        target = Path(self.tmp.name) / "notes.txt"
        target.write_bytes(b"hello veilfile")
        http = base.ScriptedHttp([(201, answer)])
        code, out, err = self.run_cli(["push", str(target), *extra], http)
        return code, out, err, http

    def test_the_option_sends_the_field(self):
        _, _, _, http = self.push(["--visibility", "team"], {"url": URL, "secret_flags": [], "visibility": "team"})
        body = http.requests[0][3]
        self.assertIn(b'name="visibility"', body)
        self.assertIn(b"\r\n\r\nteam\r\n", body)

    def test_without_the_option_the_request_is_todays(self):
        code, out, err, http = self.push([], {"url": URL, "secret_flags": []})
        self.assertNotIn(b'name="visibility"', http.requests[0][3])
        self.assertEqual((code, out, err), (0, URL + "\n", ""))

    def test_a_bad_value_is_refused_before_any_request(self):
        code, _, _, http = self.push(["--visibility", "public"], {"url": URL, "secret_flags": []})
        self.assertEqual(code, 2)
        self.assertEqual(http.requests, [])

    def test_the_answer_says_who_can_see_it(self):
        for value, sentence in (("team", "Visible to: your team."), ("only_me", "Visible to: only you.")):
            with self.subTest(value=value):
                code, out, err, _ = self.push(["--visibility", value], {"url": URL, "secret_flags": [], "visibility": value})
                self.assertEqual((code, out), (0, URL + "\n"), "stdout keeps the URL alone")
                self.assertIn(sentence, err)

    def test_an_answer_without_visibility_says_it_was_not_applied(self):
        code, out, err, _ = self.push(["--visibility", "team"], {"url": URL, "secret_flags": []})
        self.assertEqual((code, out), (0, URL + "\n"))
        self.assertIn("The server did not apply --visibility.", err)
        _, _, err, _ = self.push([], {"url": URL, "secret_flags": []})
        self.assertEqual(err, "", "without the option, nothing new is printed")


if __name__ == "__main__":
    unittest.main()
