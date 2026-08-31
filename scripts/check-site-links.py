#!/usr/bin/env python3
"""Every internal link in the built site points at a page that exists.

Astro resolves relative links between content files at build time, which means a renamed or
moved page turns its inbound links into 404s *silently* — the build stays green, because from
its point of view nothing is wrong. This is the check that turns that into a red one.

    python3 scripts/check-site-links.py site/dist
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = "/gh-spot-docker-runners"


def main(root: Path) -> int:
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")

    pages = {
        "/" + str(page.parent.relative_to(root)).strip(".").strip("/") + "/"
        for page in root.rglob("index.html")
    }
    pages = {path if path != "//" else "/" for path in pages}

    broken: set[tuple[str, str]] = set()
    checked = 0
    for html in root.rglob("*.html"):
        for href in re.findall(rf'href="({re.escape(BASE)}[^"#?]*)"', html.read_text()):
            checked += 1
            path = href[len(BASE) :] or "/"
            if not path.endswith("/"):
                # An asset rather than a page: it either exists on disk or it does not.
                if (root / path.lstrip("/")).exists():
                    continue
                path += "/"
            if path not in pages:
                broken.add((str(html.relative_to(root)), href))

    anchors = check_anchors(Path("site/src/content/docs"))

    for page, href in sorted(broken):
        print(f"  {page} -> {href}")
    for problem in anchors:
        print(f"  {problem}")
    print(
        f"{checked} internal link(s) checked, {len(broken)} broken; "
        f"{len(anchors)} dangling in-page anchor(s)"
    )
    return 1 if broken or anchors else 0


def check_anchors(source: Path) -> list[str]:
    """In-page `](#thing)` links, against the headings of the page they are on.

    These render as `href="#thing"` with no path, so the check above never sees them — and
    splitting one page into two is exactly what turns a working anchor into a dead one.
    """
    if not source.is_dir():
        return []

    def slugs(text: str) -> set[str]:
        found = set()
        for line in text.splitlines():
            heading = re.match(r"^#{1,6} (.+)$", line)
            if heading:
                slug = re.sub(r"[`*_\[\]()]", "", heading.group(1).lower())
                slug = re.sub(r"[^a-z0-9 -]", "", slug)
                found.add(re.sub(r"\s+", "-", slug.strip()))
        return found

    dangling = []
    for page in sorted(source.rglob("*.md")):
        text = page.read_text()
        here = slugs(text)
        for anchor in re.findall(r"\]\((#[^)]+)\)", text):
            if anchor[1:] not in here:
                dangling.append(f"{page.relative_to(source)} -> {anchor}")
    return dangling


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "site/dist")))
