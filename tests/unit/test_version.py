"""The version is declared in two places and must agree.

release-please updates both on a release. If they ever drift, the package claims one version
while the code reports another, and `ghspot version` stops meaning anything.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import ghspot

ROOT = Path(__file__).resolve().parents[2]


def declared_in_pyproject() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version: str = data["project"]["version"]
    return version


def test_the_package_and_pyproject_agree() -> None:
    assert ghspot.__version__ == declared_in_pyproject()


def test_release_please_can_find_the_version_line() -> None:
    """The generic updater rewrites the line carrying this marker; without it, nothing moves.

    Losing the annotation would not fail anything at release time — it would quietly leave
    __version__ behind while pyproject moved on.
    """
    source = (ROOT / "src" / "ghspot" / "__init__.py").read_text(encoding="utf-8")

    marked = [line for line in source.splitlines() if "x-release-please-version" in line]

    assert marked, "src/ghspot/__init__.py has lost its x-release-please-version marker"
    assert ghspot.__version__ in marked[0]


def test_the_manifest_matches_the_current_version() -> None:
    """release-please reads the next version from here, not from pyproject."""
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))

    assert manifest["."] == declared_in_pyproject()


def test_the_release_configuration_targets_this_package() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    package = config["packages"]["."]

    assert package["release-type"] == "python"
    assert "src/ghspot/__init__.py" in package["extra-files"]
