#!/usr/bin/env bash
# Build a runner image.
#
#   images/runner/build.sh                 # every variant
#   images/runner/build.sh ubuntu-24.04    # just one
#   images/runner/build.sh --list          # the variants and their base images
#
# On an installed host the same thing, without needing a clone:
#
#   ghspot image build ubuntu-24.04
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

# Variants whose base image will not run on an older CPU.
#
# RHEL 10 raised its baseline to the x86-64-v3 microarchitecture level (AVX2, BMI, FMA).
# Without it glibc aborts immediately with "Fatal glibc error: CPU does not support
# x86-64-v3", from inside the first RUN — which looks like a broken Dockerfile rather than a
# machine that cannot run this distribution at all.
MICROARCH_REQUIRED="
rhel-10:x86-64-v3
"

supports_microarch() {
    local level="$1" loader
    [ "$(uname -m)" = "x86_64" ] || return 0   # the levels are an x86-64 concept

    for loader in /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-x86-64.so.2; do
        if [ -x "${loader}" ]; then
            "${loader}" --help 2>/dev/null | grep -q "${level} (supported" && return 0
            return 1
        fi
    done
    return 0   # cannot tell; let the build speak for itself
}

check_microarch() {
    local name="$1" line level
    while IFS= read -r line; do
        [ -n "${line}" ] || continue
        [ "${line%%:*}" = "${name}" ] || continue
        level="${line#*:}"

        supports_microarch "${level}" && return 0

        cat >&2 <<MSG
error: ${name} needs the ${level} microarchitecture level, which this CPU does not report.

  Its base image aborts on the first command with
      Fatal glibc error: CPU does not support ${level}

  This is usually a virtual machine presenting a generic CPU rather than the real one:
      Proxmox / QEMU   set the CPU type to "host"
      VMware           enable the host CPU feature passthrough
      libvirt          <cpu mode='host-passthrough'/>

  Check what the machine actually reports with:
      /lib64/ld-linux-x86-64.so.2 --help | grep x86-64-v

  If the physical CPU genuinely predates ${level} (roughly pre-2015 Intel, pre-2017 AMD),
  this variant cannot run here. Use rhel-9, which has no such requirement.
MSG
        return 1
    done <<< "$(echo "${MICROARCH_REQUIRED}" | grep -v '^$')"
    return 0
}

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

    check_microarch "${name}" || return 1

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

# `ghspot image list` asks for this rather than parsing the table out of this file, so the
# variants stay declared in exactly one place.
case "${wanted}" in
    -l|--list|list) list; exit 0 ;;
esac

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
