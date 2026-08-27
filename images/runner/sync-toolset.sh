#!/usr/bin/env python3
"""Compare our package lists against the upstream toolset they came from.

Our Dockerfiles carry a transcription of GitHub's apt toolset. Transcriptions rot: GitHub
adds a package, ours does not, and the first anyone hears of it is a workflow failing on a
missing tool months later — which is exactly how `pipx` was found.

This fetches the pinned revision of actions/runner-images and reports the difference. It
never edits anything: what to adopt, and what to deliberately leave out, is a judgement.

    images/runner/sync-toolset.sh                 # against the pinned revision
    images/runner/sync-toolset.sh --latest        # against upstream main
    images/runner/sync-toolset.sh --update-lock   # repin to main, then report

Exits 1 when upstream has packages we do not, so CI can ask the question periodically.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "upstream.lock.yml"
RAW = "https://raw.githubusercontent.com/actions/runner-images"

# Packages we leave out on purpose. Listed with the reason, so that a future reader diffing
# against upstream does not "restore" them and quietly break the image.
DELIBERATELY_ABSENT = {
    "systemd-coredump": "needs systemd, which a container does not run",
    "pollinate": "an Ubuntu boot-time entropy service; meaningless in a container",
    "haveged": "an entropy daemon the kernel has not needed for years",
}

# Upstream name -> the package we install instead. Without these the report cries wolf on
# every run, and a tool nobody believes is worse than no tool.
EQUIVALENT = {
    "netcat": "netcat-openbsd",  # `netcat` is virtual on 24.04, with no installable candidate
}


def read_lock() -> dict[str, str]:
    """Parse the handful of scalar fields we need, without a YAML dependency."""
    fields: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(revision|repository|ref|retrieved):\s*(\S+)", line)
        if match:
            fields[match.group(1)] = match.group(2)
    if "revision" not in fields:
        sys.exit(f"{LOCK} has no revision")
    return fields


def fetch_toolset(revision: str, path: str) -> dict[str, object]:
    url = f"{RAW}/{revision}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            return json.load(response)
    except Exception as error:  # noqa: BLE001 - the URL is the useful part of any failure
        sys.exit(f"could not fetch {url}: {error}")


def upstream_packages(toolset: dict[str, object]) -> set[str]:
    apt = toolset.get("apt", {})
    assert isinstance(apt, dict)
    return {package for group in apt.values() for package in group}


def our_packages(dockerfile: Path) -> set[str]:
    """Read the package names out of the apt/dnf install block.

    Everything between the group markers and the closing `&&` is a package name; comments
    and continuations are skipped.
    """
    text = dockerfile.read_text(encoding="utf-8")
    match = re.search(r"RUN (?:apt-get update && apt-get|dnf) install.*?(?=\n\n)", text, re.S)
    if not match:
        sys.exit(f"could not find the install block in {dockerfile}")

    found: set[str] = set()
    for raw in match.group(0).splitlines()[1:]:
        line = raw.strip().rstrip("\\").strip()
        if not line or line.startswith("`#") or line.startswith("&&") or "=" in line:
            continue
        found.update(word for word in line.split() if re.fullmatch(r"[a-zA-Z0-9][\w.+-]*", word))
    return found


def latest_revision() -> str:
    result = subprocess.run(
        ["gh", "api", "repos/actions/runner-images/commits/main", "--jq", ".sha"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.exit(f"could not resolve upstream main: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", action="store_true", help="compare against upstream main")
    parser.add_argument("--update-lock", action="store_true", help="repin to main, then report")
    arguments = parser.parse_args()

    lock = read_lock()
    revision = latest_revision() if (arguments.latest or arguments.update_lock) else lock["revision"]

    print(f"upstream actions/runner-images @ {revision[:12]}")
    if revision != lock["revision"]:
        print(f"pinned                        @ {lock['revision'][:12]}")

    toolset = fetch_toolset(revision, "images/ubuntu/toolsets/toolset-2404.json")
    upstream = upstream_packages(toolset)
    ours = our_packages(HERE / "ubuntu.Dockerfile")

    # Treat an upstream package as present when we install its known equivalent.
    satisfied = {name for name, ours_name in EQUIVALENT.items() if ours_name in ours}
    missing = sorted(upstream - ours - set(DELIBERATELY_ABSENT) - satisfied)
    skipped = sorted((upstream & set(DELIBERATELY_ABSENT)))
    extra = sorted(ours - upstream - set(EQUIVALENT.values()))

    print(f"\nupstream declares {len(upstream)} apt packages; we install {len(ours)}\n")

    if missing:
        print(f"upstream has, we do not ({len(missing)}):")
        for package in missing:
            print(f"  + {package}")
    else:
        print("nothing upstream is missing from our images.")

    if satisfied:
        print("\ninstalled under a different name:")
        for package in sorted(satisfied):
            print(f"  = {package:20} -> {EQUIVALENT[package]}")

    if skipped:
        print("\nleft out on purpose:")
        for package in skipped:
            print(f"  - {package:20} {DELIBERATELY_ABSENT[package]}")

    if extra:
        print(f"\nwe install, upstream does not ({len(extra)}):")
        print("  " + " ".join(extra))
        print("  (expected: git, cmake, pipx, python3-pip and friends — see README.md)")

    if arguments.update_lock and revision != lock["revision"]:
        LOCK.write_text(
            re.sub(r"^revision: .*$", f"revision: {revision}", LOCK.read_text(), flags=re.M),
            encoding="utf-8",
        )
        print(f"\nrepinned to {revision[:12]}")

    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
