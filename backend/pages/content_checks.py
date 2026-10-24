"""Checks for the site's written content: Journal posts, Docs pages, legal pages and shell templates.

Every check returns Problems that name the file, the line, what is wrong and how to fix it,
so an agent can act on a failure without reading this module. tests_content.py runs them over
every content file; AGENTS.md ("Site recipes") points writers at that test.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pages.fonts import FAMILY_NAMES

POST_REQUIRED = ("title", "date", "slug", "description")
POST_OPTIONAL = ("image", "image_alt")
DOC_REQUIRED = ("title", "slug", "seo_title", "description", "order")
TITLE_MAX = 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 70, 160
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")
RAW_TAG = re.compile(r"<(?!https?://|mailto:)(/?[A-Za-z][A-Za-z0-9-]*|!--)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
# Docs pages that carry raw HTML, and why. Remove an entry when its page has none left (a test fails while an entry is stale).
RAW_HTML_ALLOWED = {
    "backend/docs/pages/quickstart.md": "the shell switcher (five tabs over one command each) and the upload command: each command sits beside its prompt, "
                                        "outside what Copy copies, and Markdown has no way to write tabs. The markup carries the data-tabs and data-copy "
                                        "attributes that static/js/behaviors/tabs.js and copy.js run (a fenced block needs none of this: render_reading "
                                        "gives it a code block with its own Copy)",
}


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    problem: str
    fix: str

    def __str__(self):
        return f"{self.path}:{self.line}: {self.problem}. Fix: {self.fix}"


@dataclass(frozen=True)
class Field:
    value: str
    line: int


def read_file(path, repo):
    """The file's text with line endings normalised, plus problems with the file itself."""
    name = str(Path(path).relative_to(repo))
    data = Path(path).read_bytes()
    problems = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "", [Problem(name, 1, "the file is not UTF-8", "save it as UTF-8")]
    if text.startswith("\ufeff"):
        problems.append(Problem(name, 1, "the file starts with a byte-order mark", "save it as UTF-8 without a BOM"))
        text = text[1:]
    if "\r" in text:
        line = text[:text.index("\r")].count("\n") + 1
        problems.append(Problem(name, line, "the file has Windows line endings", "save it with LF line endings"))
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, problems


def _front_matter(path, lines):
    """({key: Field}, index of the first body line, problems)."""
    if not lines or lines[0] != "---":
        return {}, 0, [Problem(path, 1, "the file does not start with a front matter block",
                               "start it with a line '---', the key: value lines, then another '---'")]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, 0, [Problem(path, 1, "the front matter block is never closed", "add a line '---' after the last key: value line")]
    fields, problems = {}, []
    for index in range(1, end):
        raw = lines[index]
        if not raw.strip():
            continue
        key, colon, value = raw.partition(":")
        if not colon:
            problems.append(Problem(path, index + 1, f"the front matter line {raw!r} has no colon", "write it as key: value"))
            continue
        key = key.strip()
        if key in fields:
            problems.append(Problem(path, index + 1, f"'{key}' appears twice in the front matter", "keep one"))
        fields[key] = Field(value.strip(), index + 1)
    return fields, end + 1, problems


def _scan(lines, start):
    """([(line number, text, in_code_fence)] for the body, (line, fence) of a block never closed or None).

    As in CommonMark, a code block opened by ``` or ~~~ (three or more) ends only at a line of
    the same character, at least as long, with nothing after it.
    """
    rows, opening = [], None  # opening: (line number, fence) of the code block we are in
    for index in range(start, len(lines)):
        match = FENCE.match(lines[index])
        if match and opening is None:
            opening = (index + 1, match.group(1))
        elif (match and opening and match.group(1)[0] == opening[1][0] and len(match.group(1)) >= len(opening[1])
              and not match.group(2).strip()):
            opening = None
        else:
            rows.append((index + 1, lines[index], opening is not None))
            continue
        rows.append((index + 1, lines[index], True))
    return rows, opening


def _body(lines, start):
    return _scan(lines, start)[0]


def _unclosed_fence(path, lines, start):
    opening = _scan(lines, start)[1]
    if opening is None:
        return []
    line, fence = opening
    return [Problem(path, line, "the code block opened here is never closed, so the rest of the page shows as code",
                    f"add a line {fence} after the code")]


def _keys(path, fields, required, optional, kind):
    problems = [Problem(path, 1, f"the front matter has no '{key}'", f"add a line '{key}: ...' between the '---' lines")
                for key in required if key not in fields]
    allowed = (*required, *optional)
    problems += [Problem(path, field.line, f"'{key}' is not a {kind} key", f"use one of: {', '.join(allowed)}")
                 for key, field in fields.items() if key not in allowed]
    return problems


def _length(path, fields, key, low, high, what):
    if key not in fields:
        return []
    size = len(fields[key].value)
    span = f"{high} or fewer" if low == 0 else f"between {low} and {high}"
    if size == 0:
        return [Problem(path, fields[key].line, f"the {what} is empty", f"write a {what} of {span} characters")]
    if size < low or size > high:
        return [Problem(path, fields[key].line, f"the {what} is {size} characters",
                        f"keep it {span} so search results show all of it")]
    return []


def _slug(path, fields):
    if "slug" not in fields:
        return []
    slug, line, file_name = fields["slug"].value, fields["slug"].line, Path(path).name
    problems = []
    if not SLUG.match(slug):
        problems.append(Problem(path, line, f"the slug '{slug}' is not lowercase words joined by hyphens",
                                "use only a-z, 0-9 and single hyphens, like where-agents-put-screenshots"))
    if f"{slug}.md" != file_name:
        problems.append(Problem(path, line, f"the slug '{slug}' does not match the file name '{file_name}'",
                                f"rename the file to {slug}.md, or change the slug to {Path(path).stem}"))
    return problems


def _date(path, fields):
    if "date" not in fields:
        return []
    value, line = fields["date"].value, fields["date"].line
    if not DATE.match(value):
        return [Problem(path, line, f"the date '{value}' is not YYYY-MM-DD", "write it like 2026-11-03")]
    try:
        date.fromisoformat(value)
    except ValueError:
        return [Problem(path, line, f"the date '{value}' is not a real date", "check the month and day")]
    return []


def _order(path, fields):
    if "order" in fields and not fields["order"].value.isdigit():
        return [Problem(path, fields["order"].line, f"the order '{fields['order'].value}' is not a whole number",
                        "use a whole number; pages are listed from the smallest")]
    return []


def _front_matter_image(path, fields, static_dir):
    problems = []
    if "image" in fields:
        if not fields.get("image_alt", Field("", 0)).value:
            problems.append(Problem(path, fields["image"].line, "'image' needs 'image_alt'",
                                    "add a line 'image_alt: ...' with one sentence describing the image"))
        if not (Path(static_dir) / fields["image"].value).is_file():
            problems.append(Problem(path, fields["image"].line, f"{fields['image'].value} does not exist under backend/static",
                                    "put the image in backend/static/img/journal/ and use that path, like img/journal/x.png"))
    elif "image_alt" in fields:
        problems.append(Problem(path, fields["image_alt"].line, "'image_alt' has no 'image'", "add 'image' or remove 'image_alt'"))
    return problems


def _raw_html(path, rows):
    if path in RAW_HTML_ALLOWED:
        return []
    problems = []
    for number, text, fenced in rows:
        if fenced:
            continue
        match = RAW_TAG.search(CODE_SPAN.sub("", text))
        if match:
            problems.append(Problem(path, number, f"raw HTML <{match.group(1)}> in the body",
                                    "write it in Markdown; the template decides how everything looks"))
    return problems


def _first_heading(path, fields, rows):
    if "title" not in fields:
        return []
    for number, text, fenced in rows:
        heading = None if fenced else HEADING.match(text)
        if heading:
            def plain(value):
                return value.casefold().rstrip(".:!?").strip()
            if plain(heading.group(1)) == plain(fields["title"].value):
                return [Problem(path, number, "the first heading repeats the title", "delete the heading; the page shows the title already")]
            return []
    return []


def _body_images(path, rows, static_dir):
    problems = []
    for number, text, fenced in rows:
        if fenced:
            continue
        for alt, source in IMAGE.findall(CODE_SPAN.sub("", text)):
            if not alt.strip():
                problems.append(Problem(path, number, "an image has no alt text",
                                        "write a sentence describing it between the brackets: ![A failing checkout page](...)"))
            if source.startswith("/static/") and not (Path(static_dir) / source[len("/static/"):]).is_file():
                problems.append(Problem(path, number, f"{source} does not exist",
                                        "put the file under backend/static/ or fix the path"))
    return problems


def _missing(path, line, char, family):
    return Problem(path, line, f"'{char}' (U+{ord(char):04X}) is not in the {family} font",
                   "replace it with a character the font has, or ask the owner to ship a font range that covers it")


def _characters(path, fields, rows, fonts, title_keys):
    problems, seen = [], set()

    def check(text, role, line):
        for char in text:
            if char in "\t" or ord(char) < 32 or (line, char, role) in seen:
                continue
            if not fonts.covers(role, char):
                seen.add((line, char, role))
                problems.append(_missing(path, line, char, FAMILY_NAMES[role]))

    for key, field in fields.items():
        check(field.value, "display" if key in title_keys else "reading", field.line)
    for number, text, fenced in rows:
        if fenced:
            check(text, "mono", number)
            continue
        for match in CODE_SPAN.finditer(text):
            check(match.group(2), "mono", number)
        check(CODE_SPAN.sub("", text), "reading", number)
    return problems


def check_post(path, text, fonts, static_dir):
    lines = text.split("\n")
    fields, start, problems = _front_matter(path, lines)
    rows = _body(lines, start)
    problems += _keys(path, fields, POST_REQUIRED, POST_OPTIONAL, "post")
    problems += _length(path, fields, "title", 0, TITLE_MAX, "title")
    problems += _length(path, fields, "description", DESCRIPTION_MIN, DESCRIPTION_MAX, "description")
    problems += _slug(path, fields) + _date(path, fields) + _front_matter_image(path, fields, static_dir)
    problems += _raw_html(path, rows) + _first_heading(path, fields, rows) + _body_images(path, rows, static_dir)
    problems += _unclosed_fence(path, lines, start)
    problems += _characters(path, fields, rows, fonts, title_keys=("title",))
    return problems


def check_doc_page(path, text, fonts):
    lines = text.split("\n")
    fields, start, problems = _front_matter(path, lines)
    rows = _body(lines, start)
    problems += _keys(path, fields, DOC_REQUIRED, (), "Docs page")
    problems += _length(path, fields, "title", 0, TITLE_MAX, "title")
    problems += _length(path, fields, "seo_title", 0, TITLE_MAX, "seo_title")
    problems += _length(path, fields, "description", DESCRIPTION_MIN, DESCRIPTION_MAX, "description")
    problems += _slug(path, fields) + _order(path, fields)
    problems += _raw_html(path, rows) + _first_heading(path, fields, rows) + _unclosed_fence(path, lines, start)
    problems += _characters(path, fields, rows, fonts, title_keys=("title",))
    return problems


def check_legal_document(path, text, fonts):
    """Legal text is the owner's (AGENTS.md invariant 8): check only that the fonts can draw it."""
    return _characters(path, {}, _body(text.split("\n"), 0), fonts, title_keys=())


def check_template_characters(path, text, fonts):
    """Shell templates mix all three faces, so every character must be in every face."""
    problems = []
    for number, line in enumerate(text.split("\n"), start=1):
        for char in sorted(set(line)):
            if ord(char) < 128:
                continue
            for role in ("display", "reading", "mono"):
                if not fonts.covers(role, char):
                    problems.append(_missing(path, number, char, FAMILY_NAMES[role]))
                    break
    return problems
