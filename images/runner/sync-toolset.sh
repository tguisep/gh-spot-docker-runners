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

# Upstream (Debian) name -> the RHEL package providing the same thing. GitHub publishes no
# RHEL images, so our RHEL variants are a second transcription — and a second transcription
# rots the same way the first does. Checking only Ubuntu left half the images unwatched.
RHEL_EQUIVALENT = {
    "dnsutils": "bind-utils",
    "dpkg-dev": "dpkg",
    "fonts-noto-color-emoji": "google-noto-color-emoji-fonts",
    "g++": "gcc-c++",
    "iproute2": "iproute",
    "iputils-ping": "iputils",
    "libicu-dev": "libicu-devel",
    "libnss3-tools": "nss-tools",
    "libsqlite3-dev": "sqlite-devel",
    "libssl-dev": "openssl-devel",
    "libyaml-dev": "libyaml-devel",
    "locales": "glibc-langpack-en",
    "netcat": "nmap-ncat",
    "openssh-client": "openssh-clients",
    "p7zip-full": "p7zip",
    "p7zip-rar": "p7zip-plugins",
    "pkg-config": "pkgconf-pkg-config",
    "shellcheck": "ShellCheck",
    "sqlite3": "sqlite",
    "ssh": "openssh-clients",
    "xvfb": "xorg-x11-server-Xvfb",
    "xz-utils": "xz",
}

# Upstream packages with no RHEL packaging, and why. Absent from the RHEL variants on
# purpose, so the report should not keep asking about them.
RHEL_UNAVAILABLE = {
    "mediainfo": "RPM Fusion only",
    "sphinxsearch": "RPM Fusion only",
    "python-is-python3": "a Debian convention; RHEL has no equivalent package",
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


# Words that appear inside an install command without being packages.
_NOT_A_PACKAGE = {
    "RUN", "apt-get", "dnf", "install", "update", "clean", "all", "rm", "rf", "true",
    "y", "nodocs", "allowerasing", "no", "recommends", "npm", "version", "bash",
    "install_weak_deps", "strict", "setopt", "cache", "lists", "var", "lib", "apt",
}


def our_packages(dockerfile: Path) -> set[str]:
    """Read package names out of every install command in a Dockerfile.

    All of them, not the first: the RHEL image installs in three passes (EPEL, the toolset,
    then the packages whose names differ between RHEL 9 and 10), and reading only the first
    silently reported that it installs nothing at all.
    """
    text = dockerfile.read_text(encoding="utf-8")

    # Join line continuations so each RUN is one string.
    joined = re.sub(r"\\\n\s*", " ", text)

    found: set[str] = set()
    for line in joined.splitlines():
        if not line.startswith("RUN ") or " install" not in line:
            continue
        # Drop the inline `# ...` group markers, then take only the segments that actually
        # install something: Ubuntu's form is `apt-get update && apt-get install ...`, so the
        # first segment is not the interesting one.
        cleaned = re.sub(r"`#[^`]*`", " ", line)
        for segment in cleaned.split("&&"):
            if " install" not in segment:
                continue
            # `install -d` / `-m` / `-o` is coreutils creating a directory, not a package
            # manager. Its arguments are paths and user names, which would otherwise be
            # read as packages named `runner` and `0755`.
            if re.search(r"\binstall\s+-[dmo]\b", segment):
                continue
            # The NodeSource bootstrap pipes a script through bash; nothing in it is a
            # package name we chose.
            if "nodesource" in segment:
                continue

            found.update(
                word
                for word in segment.split()
                # Package names may carry capitals (ShellCheck, xorg-x11-server-Xvfb).
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9.+_-]*", word)
                and word not in _NOT_A_PACKAGE
            )

    # A silent parse failure would report every upstream package as missing, which is worse
    # than no report at all. These files install dozens of things; far fewer means the
    # parser has stopped understanding them.
    if len(found) < 40:
        sys.exit(f"only read {len(found)} packages from {dockerfile}; the parser needs fixing")
    return found


def latest_revision() -> str:
    result = subprocess.run(
        ["gh", "api", "repos/actions/runner-images/commits/main", "--jq", ".sha"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.exit(f"could not resolve upstream main: {result.stderr.strip()}")
    return result.stdout.strip()


def report_rhel(upstream: set[str]) -> list[str]:
    """Check the RHEL transcription covers the same upstream toolset."""
    theirs = our_packages(HERE / "rhel.Dockerfile")

    missing: list[str] = []
    for package in sorted(upstream):
        if package in DELIBERATELY_ABSENT or package in RHEL_UNAVAILABLE:
            continue
        candidate = RHEL_EQUIVALENT.get(package, package)
        if candidate not in theirs:
            missing.append(f"{package} (expected {candidate})" if candidate != package else package)

    print(f"\nrhel variants install {len(theirs)} packages")
    if missing:
        print(f"upstream has, the rhel images do not ({len(missing)}):")
        for entry in missing:
            print(f"  + {entry}")
    else:
        print("nothing upstream is missing from the rhel images either.")

    unavailable = sorted(set(RHEL_UNAVAILABLE) & upstream)
    if unavailable:
        print("\nno rhel packaging:")
        for package in unavailable:
            print(f"  - {package:20} {RHEL_UNAVAILABLE[package]}")

    return missing


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

    rhel_missing = report_rhel(upstream)

    if arguments.update_lock and revision != lock["revision"]:
        LOCK.write_text(
            re.sub(r"^revision: .*$", f"revision: {revision}", LOCK.read_text(), flags=re.M),
            encoding="utf-8",
        )
        print(f"\nrepinned to {revision[:12]}")

    return 1 if (missing or rhel_missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
