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

        # Node, for the dashboard. Not the apt package: Ubuntu 24.04 ships node 18 and
        # the dashboard builds with vite 8, so apt would leave a container where
        # build.sh silently packages without a dashboard — which is how every release so
        # far shipped one that 404s at /ui.
        #
        # The version comes from .mise.toml, so the container and a developer machine build
        # the dashboard with the same node rather than drifting apart quietly.
        NODE_VERSION="$(sed -n "s/^node *= *\"\([0-9.]*\)\"/\1/p" .mise.toml)"
        [ -n "${NODE_VERSION}" ] || { echo "no node version in .mise.toml" >&2; exit 1; }
        case "$(dpkg --print-architecture)" in
            amd64) NODE_ARCH=x64 ;;
            arm64) NODE_ARCH=arm64 ;;
            *) echo "no node build for $(dpkg --print-architecture)" >&2; exit 1 ;;
        esac
        NODE_DIR="node-v${NODE_VERSION}-linux-${NODE_ARCH}"
        curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_DIR}.tar.xz" \
            | tar -xJ -C /opt
        export PATH="/opt/${NODE_DIR}/bin:${PATH}"
        echo "==> node $(node --version), npm $(npm --version)"

        packaging/deb/build.sh
    '
