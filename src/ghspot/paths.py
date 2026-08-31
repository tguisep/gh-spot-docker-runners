"""Where the runner image sources are, and how to build from them.

The "build the runner image" hints used to print `images/runner/build.sh` verbatim, which is
a path only for somebody standing in a clone. On a host installed from the `.deb` it named a
file that did not exist, because the package shipped the daemon and not the sources.

Both halves are fixed here: the package installs the sources, and this module finds them —
in a checkout for a developer, under `/usr/share` for an operator — so `ghspot image build`
works the same way on either.
"""

from __future__ import annotations

import os
from pathlib import Path

REPOSITORY_URL = "https://github.com/tguisep/gh-spot-docker-runners"

PACKAGED = Path("/usr/share/ghspot/images/runner")
"""Where the .deb installs them."""

IN_TREE = Path(__file__).resolve().parents[2] / "images" / "runner"
"""Where they live in a checkout, so a developer needs no install step."""


def runner_sources(override: str | None = None) -> Path | None:
    """The directory holding `build.sh` and the Dockerfiles, or ``None`` when neither is
    installed.

    A directory without a `build.sh` is not a source tree, so a half-copied install reads as
    "not there" rather than as a build that fails on its first line.

    An explicit location — the argument, or ``GHSPOT_RUNNER_IMAGES`` — is the whole answer,
    right or wrong. Falling back from it would mean a typo in the variable silently built
    from some other directory, and the operator would get an image they did not ask for.
    """
    explicit = override or os.environ.get("GHSPOT_RUNNER_IMAGES")
    if explicit:
        named = Path(explicit).expanduser()
        return named if (named / "build.sh").is_file() else None

    for candidate in (PACKAGED, IN_TREE):
        if (candidate / "build.sh").is_file():
            return candidate
    return None


def build_command(variant: str) -> str:
    """What to tell somebody to run to build one runner image.

    Always `ghspot image build`, because that is the one instruction that is true on a
    checkout and on a packaged host alike — and the daemon knows where its own sources are
    better than the operator does.
    """
    return f"ghspot image build {variant}"
