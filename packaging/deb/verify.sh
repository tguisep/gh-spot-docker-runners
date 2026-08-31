#!/usr/bin/env bash
# Install a built .deb on a clean system and check it behaves.
#
# Deliberately tested on a *newer* Ubuntu than the one that built it. The package bundles its
# own interpreter precisely so that the host's python version does not matter, and this is
# what proves it: a regression here means the bundling broke and the package would fail on
# any host whose python differs from the builder's.
set -euo pipefail

PACKAGE="${1:?usage: verify.sh path/to/ghspot_x.y.z-1_arch.deb}"
IMAGE="${VERIFY_IMAGE:-ubuntu:26.04}"

[ -f "${PACKAGE}" ] || { echo "no such package: ${PACKAGE}" >&2; exit 1; }

echo "==> verifying $(basename "${PACKAGE}") on ${IMAGE}"

docker run --rm \
    -v "$(cd "$(dirname "${PACKAGE}")" && pwd):/pkg:ro" \
    -e "PACKAGE=/pkg/$(basename "${PACKAGE}")" \
    "${IMAGE}" \
    bash -euo pipefail -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq "${PACKAGE}" >/dev/null 2>&1

        fail() { echo "FAIL: $*" >&2; exit 1; }

        if command -v python3 >/dev/null 2>&1; then
            HOST_PYTHON="$(python3 -V)"
        else
            HOST_PYTHON="none installed"
        fi
        echo "    host python: ${HOST_PYTHON}"

        # The bundled interpreter must carry the application regardless of the host python.
        ghspot version >/dev/null || fail "ghspot does not run"
        echo "    ok: runs ($(ghspot version))"

        [ -f /lib/systemd/system/ghspot.service ] || fail "the systemd unit is missing"
        echo "    ok: unit installed"

        getent passwd ghspot >/dev/null || fail "the ghspot user was not created"
        echo "    ok: service account created"

        [ -d /etc/ghspot/pools.d ] || fail "the pool directory was not created"
        echo "    ok: /etc/ghspot/pools.d exists"

        # Without the sources, `ghspot image build` has nothing to build from and the
        # instruction every hint prints is a dead end.
        [ -x /usr/share/ghspot/images/runner/build.sh ] \
            || fail "the runner image sources are missing or not executable"
        ls /usr/share/ghspot/images/runner/*.Dockerfile >/dev/null 2>&1 \
            || fail "the runner sources are installed without their Dockerfiles"
        # Redirected rather than piped into `grep -q`: grep exits on the first match, and
        # build.sh prints a line at a time, so the writer takes a SIGPIPE partway down the
        # list — which `pipefail` reports as a failure of a command that did exactly its
        # job. The file also separates "the command failed" from "the variant is missing".
        ghspot image list > /tmp/variants.txt \
            || fail "ghspot image list failed: $(cat /tmp/variants.txt)"
        grep -q ubuntu-24.04 /tmp/variants.txt \
            || fail "ghspot image list does not find the packaged sources"
        echo "    ok: runner sources installed and found by ghspot image list"

        # The dashboard is optional in the package, so its absence is reported rather than
        # failed. What must not happen is a directory that exists without the one file the
        # daemon looks for: it would be found, mounted, and 404 on every page.
        if [ -d /usr/share/ghspot/web ]; then
            [ -f /usr/share/ghspot/web/index.html ] \
                || fail "the dashboard is installed without an index.html"
            ls /usr/share/ghspot/web/assets/*.js >/dev/null 2>&1 \
                || fail "the dashboard is installed without its assets"
            echo "    ok: dashboard installed ($(du -sh /usr/share/ghspot/web | cut -f1))"
        else
            echo "    note: packaged without the dashboard (no npm at build time)"
        fi

        # The unit points here; if the two ever disagree the service silently fails to start.
        EXEC="$(sed -n "s|^ExecStart=\([^ ]*\).*|\1|p" /lib/systemd/system/ghspot.service)"
        [ -x "${EXEC}" ] || fail "the unit ExecStart (${EXEC}) is not executable"
        echo "    ok: ExecStart resolves to ${EXEC}"

        ghspot config validate --config /etc/ghspot/config.toml >/dev/null \
            || fail "the shipped config does not load"
        echo "    ok: shipped config validates"

        dpkg-query -W -f="\${Conffiles}" ghspot | grep -q /etc/ghspot/config.toml \
            || fail "the config is not registered as a conffile and would be overwritten"
        echo "    ok: config is a conffile"

        # An edited config must survive reinstalling the package.
        sed -i "s|OWNER/REPOSITORY|tguisep/edited|" /etc/ghspot/config.toml
        dpkg -i "${PACKAGE}" >/dev/null 2>&1
        grep -q "tguisep/edited" /etc/ghspot/config.toml \
            || fail "reinstalling overwrote the edited config"
        echo "    ok: local edits survive reinstall"

        # Removing must not take a token or an app key with it.
        apt-get remove -y -qq ghspot >/dev/null 2>&1
        [ -f /etc/ghspot/config.toml ] || fail "remove deleted the configuration"
        [ ! -e /opt/ghspot/.venv/bin/ghspot ] || fail "remove left the application behind"
        echo "    ok: remove keeps config, drops the application"

        apt-get purge -y -qq ghspot >/dev/null 2>&1
        [ ! -e /etc/ghspot ] || fail "purge left the configuration behind"
        getent passwd ghspot >/dev/null && fail "purge left the service account behind"
        echo "    ok: purge removes everything"
    '

echo "==> verified"
