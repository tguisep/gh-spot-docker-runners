#!/usr/bin/env bash
# Build a runner image.
#
#   images/runner/build.sh                 # every variant
#   images/runner/build.sh ubuntu-24.04    # just one
#
# The variant name is also the image tag and the label a workflow targets, so the three
# cannot drift apart:
#
#   image  ghspot/runner:ubuntu-24.04
#   label  ubuntu-24.04
#   job    runs-on: [self-hosted, ubuntu-24.04]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-ghspot/runner}"

# variant -> dockerfile:base-image
VARIANTS="
ubuntu-24.04:ubuntu.Dockerfile:ubuntu:24.04
ubuntu-22.04:ubuntu.Dockerfile:ubuntu:22.04
rhel-9:rhel.Dockerfile:almalinux:9
rhel-10:rhel.Dockerfile:almalinux:10
"

# The mounted Docker socket is only usable by the unprivileged runner user if the group id
# inside the image matches the host's. Detected here so nobody has to remember the flag.
DOCKER_GID="${DOCKER_GID:-$(getent group docker | cut -d: -f3)}"
DOCKER_GID="${DOCKER_GID:-999}"

variants() {
    echo "${VARIANTS}" | grep -v '^$'
}

list() {
    echo "Available variants:"
    variants | while IFS= read -r line; do
        printf '  %-14s  %s\n' "${line%%:*}" "$(echo "${line}" | cut -d: -f3-)"
    done
}

build_one() {
    local name="$1" dockerfile="$2" base="$3"
    echo "==> ${REGISTRY}:${name}  (${base}, docker gid ${DOCKER_GID})"

    local build_arg
    case "${dockerfile}" in
        ubuntu.Dockerfile) build_arg="UBUNTU_VERSION=${base#ubuntu:}" ;;
        *)                 build_arg="BASE_IMAGE=${base}" ;;
    esac

    docker build \
        --file "${HERE}/${dockerfile}" \
        --build-arg "${build_arg}" \
        --build-arg "DOCKER_GID=${DOCKER_GID}" \
        --tag "${REGISTRY}:${name}" \
        "${HERE}"
}

wanted="${1:-}"
found=0

while IFS= read -r line; do
    [ -n "${line}" ] || continue
    name="${line%%:*}"
    rest="${line#*:}"
    dockerfile="${rest%%:*}"
    base="${rest#*:}"

    if [ -z "${wanted}" ] || [ "${wanted}" = "${name}" ]; then
        build_one "${name}" "${dockerfile}" "${base}"
        found=1
    fi
done <<< "$(variants)"

if [ "${found}" -eq 0 ]; then
    echo "unknown variant: ${wanted}" >&2
    list >&2
    exit 1
fi
