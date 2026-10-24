#!/usr/bin/env python3
"""Vendor the site's three fonts from Google Fonts into backend/static (spec 3.5).

Run by hand when the fonts change; it needs network access and CI never runs it. Its output
is a snapshot: Google may serve different files or ranges on another day, so review the
diff. Everything is downloaded before anything in the repo is replaced, so a failed download
leaves the fonts as they were (and a run killed mid-swap is repaired by the next run). It writes, replacing what was there:

  backend/static/fonts/<family>/<style>-<weight>-<subset>.woff2   one file per Unicode range
  backend/static/fonts/<family>/OFL.txt                           the SIL Open Font License
  backend/static/fonts/ranges.json                                what each face can draw
                                                                  (each file's unicode-range, cut down
                                                                  to the characters it has glyphs for)
  backend/static/css/fonts.css                                    @font-face rules and fallbacks

Usage, from the repo root, in a throwaway virtualenv (fontTools measures the fallbacks;
it is never installed in the app):

  python3 -m venv /tmp/vf-fonts
  /tmp/vf-fonts/bin/pip install -r tools/requirements-fonts.txt
  /tmp/vf-fonts/bin/python tools/vendor_fonts.py

The fallback faces are tuned against Georgia and Courier New. The default paths are
macOS's; pass --georgia, --georgia-italic and --courier to point elsewhere.

  /tmp/vf-fonts/bin/python tools/vendor_fonts.py --ranges-only

rebuilds only ranges.json from the font files and fonts.css already in the repo, with no
network access.
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
from pages.unicode_ranges import parse_ranges  # noqa: E402 (needs the path above; no Django)

FONTS_DIR = REPO / "backend" / "static" / "fonts"
FONTS_CSS = REPO / "backend" / "static" / "css" / "fonts.css"
CSS2_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Cormorant+Garamond:ital,wght@1,300;1,400"
    "&family=Newsreader:ital,opsz,wght@0,6..72,400..500;1,6..72,400..500"
    "&family=IBM+Plex+Mono:wght@400"
    "&display=swap"
)
# Google serves WOFF2 split by Unicode range only to browsers it recognises.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# The licence texts, pinned to a google/fonts commit (their content matched the shipped files
# on 2026-10-09) so a rerun cannot change them unnoticed.
GOOGLE_FONTS_COMMIT = "2eb0b48d5f760f62e286216f0859a8c540dbc1bd"
LICENCES = {
    "cormorant-garamond": f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/ofl/cormorantgaramond/OFL.txt",
    "newsreader": f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/ofl/newsreader/OFL.txt",
    "ibm-plex-mono": f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/ofl/ibmplexmono/OFL.txt",
}
# Which face each role in the design uses (read by the content checks through ranges.json).
ROLES = {
    "display": "cormorant-garamond/italic-300",
    "reading": "newsreader/normal-400-500",
    "mono": "ibm-plex-mono/normal-400",
}
# Fallback faces, measured so text does not jump when the web fonts arrive:
# face key -> (fallback family, font-style, local() sources, which system font to measure)
FALLBACKS = {
    "cormorant-garamond/italic-300": ("Cormorant Garamond Fallback", "italic", ('local("Georgia Italic")', 'local("Georgia-Italic")'), "georgia_italic"),
    "newsreader/normal-400-500": ("Newsreader Fallback", "normal", ('local("Georgia")',), "georgia"),
    "newsreader/italic-400-500": ("Newsreader Fallback", "italic", ('local("Georgia Italic")', 'local("Georgia-Italic")'), "georgia_italic"),
    "ibm-plex-mono/normal-400": ("IBM Plex Mono Fallback", "normal", ('local("Courier New")', 'local("CourierNewPSMT")'), "courier"),
}
SAMPLE = "The quick brown fox jumps over the lazy dog, then rests. 0123456789"
BLOCK = re.compile(r"/\*\s*(?P<subset>[\w-]+)\s*\*/\s*@font-face\s*\{(?P<body>[^}]*)\}")
FACE_RULE = re.compile(r"@font-face\s*\{(?P<body>[^}]*)\}")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def slugify(family):
    return family.lower().replace(" ", "-")


def declaration(body, name):
    match = re.search(rf"{name}\s*:\s*([^;]+);", body)
    if not match:
        raise SystemExit(f"Google's CSS has no {name} in a block: {body.strip()[:120]}")
    return match.group(1).strip()


def spans_of(points):
    """Code points -> sorted (first, last) spans of consecutive points."""
    spans = []
    for point in sorted(points):
        if spans and point == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], point)
        else:
            spans.append((point, point))
    return spans


def drawn(face):
    """What a face can draw: each file's unicode-range, cut down to the characters in the file's
    own cmap. Google's unicode-range names a slice of Unicode, not the glyphs a file has."""
    points = set()
    for file in face["files"]:
        declared = parse_ranges(file["ranges"])
        cmap = TTFont(FONTS_DIR / file["path"]).getBestCmap()
        points.update(point for point in cmap if any(first <= point <= last for first, last in declared))
    return spans_of(points)


def write_ranges(faces):
    ranges = {
        "generated_by": "tools/vendor_fonts.py",
        "roles": ROLES,
        "faces": {key: drawn(face) for key, face in sorted(faces.items())},
    }
    (FONTS_DIR / "ranges.json").write_text(json.dumps(ranges, indent=1) + "\n", encoding="utf-8")


def faces_in_fonts_css():
    """The faces fonts.css declares, each with its files and their unicode-ranges (for --ranges-only)."""
    faces = {}
    for match in FACE_RULE.finditer(FONTS_CSS.read_text(encoding="utf-8")):
        body = match.group("body")
        if "unicode-range" not in body:
            continue  # a fallback face: local() sources, no files of ours
        family = declaration(body, "font-family").strip("'\"")
        key = f"{slugify(family)}/{declaration(body, 'font-style')}-{declaration(body, 'font-weight').replace(' ', '-')}"
        path = re.search(r'url\("\.\./fonts/([^"]+)"\)', body).group(1)
        faces.setdefault(key, {"files": []})["files"].append({"path": path, "ranges": declaration(body, "unicode-range")})
    return faces


def download(css, target_dir):
    """Fetch every font file Google's CSS names, and the licences, into target_dir; return the faces."""
    faces = {}
    for match in BLOCK.finditer(css):
        body, subset = match.group("body"), match.group("subset")
        family = declaration(body, "font-family").strip("'\"")
        style = declaration(body, "font-style")
        weight = declaration(body, "font-weight")
        url = re.search(r"url\((\S+?)\)", body).group(1)
        key = f"{slugify(family)}/{style}-{weight.replace(' ', '-')}"
        relative = f"{key}-{subset}.woff2"
        target = target_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(url))
        face = faces.setdefault(key, {"family": family, "style": style, "weight": weight, "files": []})
        face["files"].append({"subset": subset, "path": relative, "ranges": declaration(body, "unicode-range")})
    missing = [key for key in [*ROLES.values(), *FALLBACKS] if key not in faces]
    if missing:
        raise SystemExit(f"Google returned no files for {missing}; check CSS2_URL.")
    for family_slug, url in LICENCES.items():
        (target_dir / family_slug / "OFL.txt").write_bytes(fetch(url))
    return faces


def average_width(font):
    cmap, metrics = font.getBestCmap(), font["hmtx"]
    widths = [metrics[cmap[ord(char)]][0] for char in SAMPLE if ord(char) in cmap]
    return sum(widths) / len(widths)


def metric_overrides(web, fallback):
    """CSS descriptors that make `fallback` take the space `web` will (the method Next.js and Capsize use)."""
    web_em, fallback_em = web["head"].unitsPerEm, fallback["head"].unitsPerEm
    size_adjust = (average_width(web) / web_em) / (average_width(fallback) / fallback_em)
    hhea = web["hhea"]

    def percent(value):
        return f"{value / (web_em * size_adjust) * 100:.2f}%"

    return {
        "size-adjust": f"{size_adjust * 100:.2f}%",
        "ascent-override": percent(hhea.ascent),
        "descent-override": percent(abs(hhea.descent)),
        "line-gap-override": percent(hhea.lineGap),
    }


def main():
    parser = argparse.ArgumentParser(description="Vendor the site's fonts into backend/static.")
    parser.add_argument("--georgia", default="/System/Library/Fonts/Supplemental/Georgia.ttf")
    parser.add_argument("--georgia-italic", default="/System/Library/Fonts/Supplemental/Georgia Italic.ttf")
    parser.add_argument("--courier", default="/System/Library/Fonts/Supplemental/Courier New.ttf")
    parser.add_argument("--ranges-only", action="store_true",
                        help="rebuild ranges.json from the font files and fonts.css already in the repo, offline")
    args = parser.parse_args()
    retired = FONTS_DIR.with_name(".fonts-retired")
    if not FONTS_DIR.exists() and retired.exists():
        # A run died between moving the old fonts aside and moving the new ones in: put them back.
        retired.rename(FONTS_DIR)
        print(f"Restored {FONTS_DIR} from {retired.name}, left by an interrupted run.")
    if args.ranges_only:
        faces = faces_in_fonts_css()
        write_ranges(faces)
        print(f"Wrote ranges.json for {len(faces)} faces.")
        return
    system_fonts = {"georgia": args.georgia, "georgia_italic": args.georgia_italic, "courier": args.courier}
    for name, path in system_fonts.items():
        if not Path(path).is_file():
            raise SystemExit(f"Cannot measure the fallback: {path} does not exist. Pass --{name.replace('_', '-')}.")

    # Download everything into a staging folder first; the fonts in the repo are replaced only
    # once every file has arrived, so a failed download leaves them as they were.
    for stale in FONTS_DIR.parent.glob(".vendor-fonts-*"):
        shutil.rmtree(stale, ignore_errors=True)  # left by a run that was killed
    css = fetch(CSS2_URL).decode("utf-8")
    staging = Path(tempfile.mkdtemp(prefix=".vendor-fonts-", dir=FONTS_DIR.parent))
    staging.chmod(0o755)  # mkdtemp makes it private; the fonts directory is not
    try:
        faces = download(css, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(retired, ignore_errors=True)  # the fonts dir exists (restored above), so this is stale
    if FONTS_DIR.exists():
        FONTS_DIR.rename(retired)
    staging.rename(FONTS_DIR)
    shutil.rmtree(retired, ignore_errors=True)

    blocks = ["/* Generated by tools/vendor_fonts.py: rerun it rather than editing this file. */"]
    for key in sorted(faces):
        face = faces[key]
        for file in face["files"]:
            blocks.append(
                "@font-face {\n"
                f'  font-family: "{face["family"]}";\n'
                f"  font-style: {face['style']};\n"
                f"  font-weight: {face['weight']};\n"
                "  font-display: swap;\n"
                f'  src: url("../fonts/{file["path"]}") format("woff2");\n'
                f"  unicode-range: {file['ranges']};\n"
                "}"
            )
    for key, (fallback_family, style, sources, system_font) in FALLBACKS.items():
        latin = next(file for file in faces[key]["files"] if file["subset"] == "latin")
        overrides = metric_overrides(TTFont(FONTS_DIR / latin["path"]), TTFont(system_fonts[system_font]))
        descriptors = "".join(f"  {name}: {value};\n" for name, value in overrides.items())
        blocks.append(
            "@font-face {\n"
            f'  font-family: "{fallback_family}";\n'
            f"  font-style: {style};\n"
            f"  src: {', '.join(sources)};\n"
            f"{descriptors}"
            "}"
        )
    FONTS_CSS.parent.mkdir(parents=True, exist_ok=True)
    FONTS_CSS.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    write_ranges(faces)
    print(f"Wrote {sum(len(face['files']) for face in faces.values())} font files for {len(faces)} faces.")


if __name__ == "__main__":
    main()
