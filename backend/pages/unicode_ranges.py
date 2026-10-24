"""CSS unicode-range values as (first, last) code point spans.

Plain Python with no Django, so tools/vendor_fonts.py imports it too: the app and the tool that
writes ranges.json must read unicode-range the same way.
"""


def parse_ranges(text):
    """'U+0000-00FF, U+0131, U+4??' -> [(0, 255), (305, 305), (1024, 1279)]

    A ? stands for any hex digit, as CSS allows; empty parts (a trailing comma) are skipped.
    """
    spans = []
    for part in text.split(","):
        part = part.strip().upper()
        if not part:
            continue
        if part.startswith("U+"):
            part = part[2:]
        first, _, last = part.partition("-")
        if "?" in first:
            spans.append((int(first.replace("?", "0"), 16), int(first.replace("?", "F"), 16)))
        else:
            spans.append((int(first, 16), int(last or first, 16)))
    return spans


def merge(spans):
    """Sort and join touching or overlapping (first, last) spans."""
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged
