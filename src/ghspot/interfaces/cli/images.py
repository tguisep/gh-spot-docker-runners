"""Building the runner images from wherever their sources happen to be installed.

The CLI does not reimplement the build. `images/runner/build.sh` stays the one place that
knows which Dockerfile a variant uses, which base image it starts from, and that the host's
`docker` group id has to be built in — so the image a developer builds from a checkout and
the one an operator builds from the package come out of the same file.

What this adds is the part an operator was missing: finding that file. Before, every hint
printed `images/runner/build.sh`, which is a path only inside a clone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ghspot import paths
from ghspot.interfaces.cli.render import fail, hint


def _locate(override: str | None) -> Path | None:
    found = paths.runner_sources(override)
    if found is None:
        fail("the runner image sources are not installed")
        hint(
            f"reinstall the package, clone {paths.REPOSITORY_URL}, "
            "or point GHSPOT_RUNNER_IMAGES at a copy"
        )
    return found


def _run(script: Path, *arguments: str) -> int:
    """Hand the terminal to the build.

    Output is inherited rather than captured: a `docker build` is minutes of layer progress,
    and buffering it until the end would leave an operator staring at nothing wondering
    whether it hung.
    """
    try:
        return subprocess.run([str(script), *arguments], check=False).returncode
    except OSError as error:
        fail(f"could not run {script}: {error}")
        return 1


def build(variant: str | None = None, *, sources: str | None = None) -> int:
    """Build one runner image, or every variant when none is named."""
    found = _locate(sources)
    if found is None:
        return 2
    return _run(found / "build.sh", *([variant] if variant else []))


def variants(*, sources: str | None = None) -> int:
    """List the variants the sources can build."""
    found = _locate(sources)
    if found is None:
        return 2
    return _run(found / "build.sh", "--list")
