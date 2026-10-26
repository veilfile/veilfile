"""Apply PR 7's approved words to the tree.

Usage, from the repository root:
  python3 -I docs/superpowers/plans/2026-10-25-site-redesign-pr7-apply.py GROUP [--check]
GROUP is chapters, docs, journal-llms, forms-device-errors, dashboard-frames or all.
--check applies nothing and reports whether every item would apply.
--pins applies nothing and lists the test lines (backend/**/tests*.py, site-tests/*.py) that assert a
sentence an item removes, so they can be changed to the new words first.
--verify applies nothing and confirms that every applied item's new words are in its file.

Each "replace" item's old text must occur exactly once in its file when it is applied;
"insert_before" puts new text before an anchor that occurs once; "replace_body" swaps
everything after a Markdown file's front matter. "covered" items are already inside a
whole rewrite, and "unchanged" items change nothing. A file is written only when all
of its items applied, and the script exits 1 naming every item that did not.
"""
import json
import re
import sys
from pathlib import Path

WORDS = Path(__file__).with_name("2026-10-25-site-redesign-pr7-words.json")


def apply(text, item):
    mode = item["mode"]
    if mode == "replace":
        n = text.count(item["old"])
        if n != 1:
            raise ValueError(f"old text found {n} times")
        return text.replace(item["old"], item["new"])
    if mode == "insert_before":
        n = text.count(item["anchor"])
        if n != 1:
            raise ValueError(f"anchor found {n} times")
        if item["new"].strip() in text:
            raise ValueError("new text is already there")
        return text.replace(item["anchor"], item["new"] + item["anchor"])
    if mode == "replace_body":
        if not text.startswith("---\n"):
            raise ValueError("no front matter")
        end = text.index("\n---\n", 4) + len("\n---\n")
        return text[:end] + "\n" + item["new"].lstrip("\n")
    return text


def removed_sentences(item):
    """Sentences of at least 15 characters in the old text that the new text no longer contains."""
    def plain(t):
        t = re.sub(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}|<[^>]+>", " ", t, flags=re.S)
        return " ".join(t.split())
    new = plain(item["new"])
    parts = re.split(r"(?<=[.?!])\s+", plain(item["old"]))
    return [p for p in parts if len(p) >= 15 and p not in new]


def pins(items):
    tests = sorted(Path("backend").glob("**/tests*.py")) + sorted(Path("site-tests").glob("*.py"))
    texts = {t: t.read_text(encoding="utf-8").splitlines() for t in tests}
    found = 0
    for item in items:
        if item["mode"] in ("covered", "unchanged"):
            continue
        for sentence in removed_sentences(item):
            probe = sentence.rstrip(".?!")[:60]
            for t, lines in texts.items():
                for n, line in enumerate(lines, 1):
                    if probe in line:
                        found += 1
                        print(f"{item['id']}: {t}:{n}: {probe!r}")
    print(f"{found} test lines assert words these items remove")


def verify(items):
    missing = 0
    for item in items:
        if item["mode"] in ("covered", "unchanged"):
            continue
        text = Path(item["file"]).read_text(encoding="utf-8")
        new = item["new"].strip()
        if new and new not in text:
            missing += 1
            print(f"NOT APPLIED: {item['id']} ({item['file']})")
    print(f"{len([i for i in items if i['mode'] not in ('covered', 'unchanged')]) - missing} applied items verified; {missing} missing")
    return 1 if missing else 0


def main(argv):
    if not argv or argv[0] not in {"chapters", "docs", "journal-llms", "forms-device-errors", "dashboard-frames", "all"}:
        print(__doc__, file=sys.stderr)
        return 2
    group, check = argv[0], "--check" in argv[1:]
    items = [i for i in json.loads(WORDS.read_text()) if group == "all" or i["group"] == group]
    if "--pins" in argv[1:]:
        pins(items)
        return 0
    if "--verify" in argv[1:]:
        return verify(items)
    by_file, failed = {}, []
    for item in items:
        if item["mode"] in ("covered", "unchanged"):
            continue
        path = Path(item["file"])
        text = by_file.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
        try:
            by_file[path] = apply(text, item)
        except ValueError as e:
            failed.append(f"{item['id']} ({item['file']}): {e}")
            by_file[path] = text
    bad_files = {f.split(" (", 1)[1].rstrip("):").split(")")[0] for f in failed}
    for path, text in by_file.items():
        if not check and str(path) not in bad_files:
            path.write_text(text, encoding="utf-8")
    applied = sum(1 for i in items if i["mode"] not in ("covered", "unchanged"))
    print(f"{group}: {applied - len(failed)} of {applied} applied{' (check only)' if check else ''}; "
          f"{sum(1 for i in items if i['mode'] == 'covered')} covered by a whole rewrite; "
          f"{sum(1 for i in items if i['mode'] == 'unchanged')} unchanged")
    for f in failed:
        print("FAILED:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
