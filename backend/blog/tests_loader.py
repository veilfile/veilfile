"""The Journal's loader: neighbours, reading time and the size of a header image (spec 4.6)."""
import struct
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from blog import loader


def png_header(width, height):
    """The first 29 bytes of a PNG: its signature and IHDR chunk, all that image_size() reads."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
            + b"\x08\x06\x00\x00\x00")


def words(count):
    """A text of count words, for reading-time tests."""
    return " ".join(["word"] * count)


class FakeJournal:
    """Mixin for a test case: an empty posts directory and an empty static directory, in temporary
    places, that the loader reads instead of backend/blog/posts/ and backend/static/."""

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.posts_dir = Path(directory.name) / "posts"
        self.static_dir = Path(directory.name) / "static"
        self.posts_dir.mkdir()
        (self.static_dir / "img").mkdir(parents=True)
        for name, path in (("POSTS_DIR", self.posts_dir), ("STATIC_DIR", self.static_dir)):
            patcher = mock.patch.object(loader, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_post(self, slug, day, body="Body.", **front_matter):
        """backend/blog/posts/<slug>.md as the loader reads it. The title is the slug as words
        ("my-post" is "My post"); front_matter adds lines such as image."""
        header = {"title": slug.replace("-", " ").capitalize(), "date": day, "slug": slug,
                  "description": f"The post called {slug}.", **front_matter}
        lines = "".join(f"{key}: {value}\n" for key, value in header.items())
        (self.posts_dir / f"{slug}.md").write_text(f"---\n{lines}---\n\n{body}\n", encoding="utf-8")


class NeighbourTests(FakeJournal, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.write_post("first", "2026-01-10")
        self.write_post("second", "2026-02-10")
        self.write_post("third", "2026-03-10")

    def around(self, slug):
        older, newer = loader.neighbours(slug)
        return (older.slug if older else None, newer.slug if newer else None)

    def test_a_post_between_two_others_has_both_neighbours(self):
        self.assertEqual(self.around("second"), ("first", "third"))

    def test_the_newest_post_has_only_an_older_neighbour(self):
        self.assertEqual(self.around("third"), ("second", None))

    def test_the_oldest_post_has_only_a_newer_neighbour(self):
        self.assertEqual(self.around("first"), (None, "second"))

    def test_a_lone_post_has_no_neighbours(self):
        (self.posts_dir / "first.md").unlink()
        (self.posts_dir / "third.md").unlink()
        self.assertEqual(self.around("second"), (None, None))

    def test_a_post_dated_in_the_future_is_never_a_neighbour(self):
        self.write_post("later", "2999-01-01")
        self.assertEqual(self.around("third"), ("second", None),
                         "backend/blog/loader.py: neighbours() must walk load_posts(), which hides posts dated in the future")
        self.assertEqual(self.around("later"), (None, None))

    def test_an_unknown_slug_has_no_neighbours(self):
        self.assertEqual(self.around("nope"), (None, None))

    def test_posts_on_one_day_still_form_one_chain(self):
        self.write_post("also-second", "2026-02-10")
        chain = [post.slug for post in loader.load_posts()]
        self.assertEqual(len(chain), 4)
        for index, slug in enumerate(chain):
            with self.subTest(slug=slug):
                older = chain[index + 1] if index + 1 < len(chain) else None
                newer = chain[index - 1] if index else None
                self.assertEqual(self.around(slug), (older, newer))


class ReadingMinutesTests(SimpleTestCase):
    def test_two_hundred_words_a_minute_rounded_up(self):
        for count, minutes in ((1, 1), (200, 1), (201, 2), (400, 2), (401, 3)):
            with self.subTest(words=count):
                self.assertEqual(loader.reading_minutes(words(count)), minutes)

    def test_a_post_with_no_words_still_takes_a_minute(self):
        self.assertEqual(loader.reading_minutes(""), 1)

    def test_the_marks_markdown_adds_are_not_words(self):
        body = "## Heading\n\n- " + words(199) + "\n\n---\n\n> \n\n| - | - |\n|---|---|\n"
        self.assertEqual(loader.reading_minutes(body), 1,
                         "backend/blog/loader.py: reading_minutes() counts only runs of characters that hold a letter or a digit")

    def test_code_is_read_too(self):
        body = "```\n" + words(200) + "\n```\n\nThen one more line."
        self.assertEqual(loader.reading_minutes(body), 2,
                         "backend/blog/loader.py: reading_minutes() counts the words in code blocks, which a reader reads")


class ImageSizeTests(FakeJournal, SimpleTestCase):
    def test_a_png_reports_its_width_and_height(self):
        (self.static_dir / "img" / "wide.png").write_bytes(png_header(1200, 630))
        self.assertEqual(loader.image_size("img/wide.png"), (1200, 630))

    def test_a_file_that_is_not_a_png_reports_nothing(self):
        (self.static_dir / "img" / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.assertIsNone(loader.image_size("img/mark.svg"))

    def test_a_png_cut_short_reports_nothing(self):
        (self.static_dir / "img" / "cut.png").write_bytes(png_header(10, 10)[:20])
        self.assertIsNone(loader.image_size("img/cut.png"))

    def test_a_missing_file_a_folder_or_nothing_reports_nothing(self):
        for name in ("img/nope.png", "img", "", "img/a\0b.png"):
            with self.subTest(name=name):
                self.assertIsNone(loader.image_size(name))

    def test_a_path_that_leaves_backend_static_reports_nothing(self):
        (self.static_dir.parent / "outside.png").write_bytes(png_header(10, 10))
        self.assertIsNone(loader.image_size("../outside.png"))
        self.assertIsNone(loader.image_size(str(self.static_dir.parent / "outside.png")))
