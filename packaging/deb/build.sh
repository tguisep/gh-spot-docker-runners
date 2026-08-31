#!/usr/bin/env bash
# Build a .deb for ghspot.
#
# The package bundles its own CPython. That is deliberate: a virtualenv built against the
# builder's python3 hard-codes that interpreter's version and paths, so a package built on
# Ubuntu 24.04 (python3.12) would not run on 26.04 (python3.14). Bundling removes the
# coupling entirely — the package works on any glibc distribution, and upgrading the host's
# python cannot break the daemon.
#
# Must run somewhere /opt/ghspot can be written, because a virtualenv records absolute paths
# and has to be built at its final location. Use build-in-docker.sh for that, or run it on a
# throwaway CI runner.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

VERSION="${VERSION:-$(grep -m1 '^version = ' "${ROOT}/pyproject.toml" | cut -d'"' -f2)}"
REVISION="${REVISION:-1}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
OUT_DIR="${OUT_DIR:-${ROOT}/dist}"

PREFIX="/opt/ghspot"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

echo "==> ghspot ${VERSION}-${REVISION} (${ARCH}), bundling python ${PYTHON_VERSION}"

# --- the interpreter and the application ------------------------------------------------
# Both are built at their final absolute path, then copied into the staging tree, so the
# virtualenv's recorded paths are correct on the target rather than on this machine.

rm -rf "${PREFIX}"
mkdir -p "${PREFIX}"

echo "==> installing a standalone CPython"
UV_PYTHON_INSTALL_DIR="${PREFIX}/python" \
    uv python install --managed-python "${PYTHON_VERSION}" >/dev/null
INTERPRETER="$(UV_PYTHON_INSTALL_DIR="${PREFIX}/python" uv python find --managed-python "${PYTHON_VERSION}")"

echo "==> building the virtualenv"
uv venv --python "${INTERPRETER}" "${PREFIX}/.venv" >/dev/null
VIRTUAL_ENV="${PREFIX}/.venv" uv pip install --quiet "${ROOT}"

# Prove the thing we are about to ship actually runs, before wrapping it in a package.
"${PREFIX}/.venv/bin/ghspot" version

# --- the staging tree -------------------------------------------------------------------

install -d "${STAGE}${PREFIX}"
cp -a "${PREFIX}/python" "${STAGE}${PREFIX}/python"
cp -a "${PREFIX}/.venv" "${STAGE}${PREFIX}/.venv"

# Strip build-time noise that would otherwise double the package size.
find "${STAGE}${PREFIX}" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}${PREFIX}" -type d -name 'tests' -path '*/site-packages/*' -prune -exec rm -rf {} + 2>/dev/null || true

install -d "${STAGE}/usr/bin"
cat > "${STAGE}/usr/bin/ghspot" <<'WRAPPER'
#!/bin/sh
# ghspot lives in its own virtualenv so that it shares nothing with the system python.
exec /opt/ghspot/.venv/bin/ghspot "$@"
WRAPPER
chmod 0755 "${STAGE}/usr/bin/ghspot"

install -d "${STAGE}/lib/systemd/system"
install -m 0644 "${ROOT}/deploy/ghspot.service" "${STAGE}/lib/systemd/system/ghspot.service"

install -d "${STAGE}/etc/ghspot"
install -m 0640 "${HERE}/config.toml" "${STAGE}/etc/ghspot/config.toml"

# Empty on purpose: the directory exists so an operator can drop a pool file in without
# first working out where it goes. dpkg keeps a directory it owns, and `include` in the
# conffile points here, commented out until there is something to include.
install -d "${STAGE}/etc/ghspot/pools.d"

# The dashboard, when a node toolchain is available to build it. Optional on purpose: the
# daemon serves whatever it finds and runs normally with nothing here, so a machine without
# node still produces a working package rather than failing the build. CI has node, so
# released packages always carry it.
if command -v npm >/dev/null 2>&1; then
    echo "==> building the dashboard"
    (cd "${ROOT}/web" && npm ci --silent && npm run build --silent)
    install -d "${STAGE}/usr/share/ghspot/web"
    cp -a "${ROOT}/web/dist/." "${STAGE}/usr/share/ghspot/web/"
    echo "    dashboard: $(du -sh "${STAGE}/usr/share/ghspot/web" | cut -f1)"
else
    echo "==> no npm on PATH; packaging without the dashboard"
    echo "    the daemon will serve the API only, and say so at /ui"
fi

# The runner image sources. A few Dockerfiles and a shell script — small enough that
# shipping them costs nothing, and without them `ghspot image build` on an installed host
# has nothing to build from and every "build the runner image" hint names a missing file.
install -d "${STAGE}/usr/share/ghspot/images/runner"
install -m 0644 "${ROOT}/images/runner/"*.Dockerfile "${STAGE}/usr/share/ghspot/images/runner/"
install -m 0644 "${ROOT}/images/runner/upstream.lock.yml" \
    "${STAGE}/usr/share/ghspot/images/runner/"
for script in build.sh verify.sh entrypoint.sh sync-toolset.sh; do
    install -m 0755 "${ROOT}/images/runner/${script}" \
        "${STAGE}/usr/share/ghspot/images/runner/${script}"
done
echo "    runner sources: $(du -sh "${STAGE}/usr/share/ghspot/images" | cut -f1)"

install -d "${STAGE}/usr/share/doc/ghspot"
install -m 0644 "${ROOT}/config.example.toml" "${STAGE}/usr/share/doc/ghspot/config.example.toml"
install -m 0644 "${ROOT}/LICENSE" "${STAGE}/usr/share/doc/ghspot/copyright"
gzip -9 -c "${ROOT}/README.md" > "${STAGE}/usr/share/doc/ghspot/README.md.gz"

# --- control metadata -------------------------------------------------------------------

install -d "${STAGE}/DEBIAN"
INSTALLED_SIZE="$(du -sk "${STAGE}" | cut -f1)"

cat > "${STAGE}/DEBIAN/control" <<CONTROL
Package: ghspot
Version: ${VERSION}-${REVISION}
Section: devel
Priority: optional
Architecture: ${ARCH}
Maintainer: Thomas Guiseppin <thomas82710@gmail.com>
Installed-Size: ${INSTALLED_SIZE}
Depends: adduser, ca-certificates
Recommends: docker-ce | docker.io
Homepage: https://github.com/tguisep/gh-spot-docker-runners
Description: Self-hosted GitHub Actions runners as ephemeral Docker containers
 ghspot turns one Linux host into an on-demand GitHub Actions runner fleet. It
 watches your repositories for queued jobs, starts a fresh container per job,
 and tears it down on both sides when the job finishes.
 .
 Runners are registered with just-in-time configurations, so no credential ever
 enters a job container. A reconciliation loop continuously converges Docker and
 GitHub onto the declared configuration, repairing drift left by crashes.
 .
 This package bundles its own Python interpreter and does not use the system one.
CONTROL

# dpkg must not overwrite an edited configuration on upgrade.
echo "/etc/ghspot/config.toml" > "${STAGE}/DEBIAN/conffiles"

for script in postinst prerm postrm; do
    install -m 0755 "${HERE}/${script}" "${STAGE}/DEBIAN/${script}"
done

# --- build ------------------------------------------------------------------------------

mkdir -p "${OUT_DIR}"
PACKAGE="${OUT_DIR}/ghspot_${VERSION}-${REVISION}_${ARCH}.deb"
fakeroot dpkg-deb --build --root-owner-group -Zxz "${STAGE}" "${PACKAGE}" >/dev/null

rm -rf "${PREFIX}"
echo "==> ${PACKAGE} ($(du -h "${PACKAGE}" | cut -f1))"
