#!/usr/bin/env bash
# Build the .deb inside a container.
#
# The build writes to /opt/ghspot, because a virtualenv records absolute paths and must be
# created at its final location. A container makes that safe on a developer machine and
# reproducible everywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${IMAGE:-ubuntu:24.04}"

mkdir -p "${ROOT}/dist"

docker run --rm \
    -v "${ROOT}:/src:ro" \
    -v "${ROOT}/dist:/out" \
    -e "VERSION=${VERSION:-}" \
    -e "REVISION=${REVISION:-1}" \
    -e "PYTHON_VERSION=${PYTHON_VERSION:-3.12}" \
    -e OUT_DIR=/out \
    "${IMAGE}" \
    bash -euo pipefail -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends \
            ca-certificates curl xz-utils fakeroot dpkg-dev >/dev/null

        curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null
        export PATH="/root/.local/bin:${PATH}"

        # /src is read-only so the build cannot alter the working tree.
        cp -a /src /build
        cd /build
        packaging/deb/build.sh
    '
