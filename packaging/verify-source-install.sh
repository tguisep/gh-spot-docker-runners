#!/usr/bin/env bash
# Run the install sequence exactly as docs/operations.md documents it, on a clean system.
#
# This exists because the documented commands were wrong three times running: `uv sync`
# leaves nothing on PATH, the systemd unit pointed at a virtualenv nothing created, and
# `python3 -m venv` on Debian and Ubuntu produces a virtualenv with no pip in it. Every one
# of those passed review and failed on a real machine. Reasoning about install instructions
# does not work; running them does.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${VERIFY_IMAGE:-ubuntu:26.04}"

echo "==> verifying the documented source install on ${IMAGE}"

docker run --rm -v "${ROOT}:/src:ro" "${IMAGE}" bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends python3 sudo adduser >/dev/null 2>&1
    cp -a /src /repo && cd /repo

    fail() { echo "FAIL: $*" >&2; exit 1; }

    echo "    host python: $(python3 -V)"

    useradd --system --home /opt/ghspot --shell /usr/sbin/nologin ghspot
    groupadd -f docker && usermod -aG docker ghspot

    # The step that was missing: without this, the virtualenv has no pip.
    apt-get install -y -qq python3-venv >/dev/null 2>&1

    mkdir -p /opt/ghspot
    python3 -m venv /opt/ghspot/.venv
    [ -x /opt/ghspot/.venv/bin/pip ] || fail "the virtualenv has no pip (python3-venv missing?)"
    echo "    ok: virtualenv has pip"

    /opt/ghspot/.venv/bin/pip install --quiet "$PWD"
    chown -R ghspot:ghspot /opt/ghspot
    /opt/ghspot/.venv/bin/ghspot version >/dev/null || fail "the installed ghspot does not run"
    echo "    ok: installs and runs ($(/opt/ghspot/.venv/bin/ghspot version))"

    # The unit and the install path must agree, or the service silently fails to start.
    EXEC="$(sed -n "s|^ExecStart=\([^ ]*\).*|\1|p" deploy/ghspot.service)"
    [ -x "${EXEC}" ] || fail "the unit ExecStart (${EXEC}) does not exist after a documented install"
    echo "    ok: unit ExecStart resolves to ${EXEC}"

    sudo -u ghspot "${EXEC}" version >/dev/null || fail "the ghspot service user cannot run it"
    echo "    ok: the service user can run it"

    cp config.example.toml /etc/ghspot-config.toml
    sudo -u ghspot "${EXEC}" config validate --config /etc/ghspot-config.toml >/dev/null \
        || fail "the shipped example config does not load"
    echo "    ok: the example config validates"
'

echo "==> verified"
