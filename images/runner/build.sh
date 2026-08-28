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
#
# Order matters: a variant built FROM another variant must come after it, so that building
# every variant in one pass works from an empty cache.
VARIANTS="
ubuntu-24.04:ubuntu.Dockerfile:ubuntu:24.04
ubuntu-22.04:ubuntu.Dockerfile:ubuntu:22.04
rhel-9:rhel.Dockerfile:almalinux:9
rhel-10:rhel.Dockerfile:almalinux:10
jetson-r32:jetson.Dockerfile:ghspot/runner:ubuntu-22.04
"

# Variants that only make sense on one architecture.
#
# jetson-r32 is arm64 because a Jetson is arm64. Built on x86-64 it produces an image no
# Jetson can run, and the failure arrives much later as "exec format error" on the board.
ARCH_REQUIRED="
jetson-r32:aarch64
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

check_arch() {
    local name="$1" line want have
    have="$(uname -m)"
    while IFS= read -r line; do
        [ -n "${line}" ] || continue
        [ "${line%%:*}" = "${name}" ] || continue
        want="${line#*:}"

        [ "${have}" = "${want}" ] && return 0

        cat >&2 <<MSG
error: ${name} is an ${want} image and this machine is ${have}.

  Built here it would produce an image the target cannot execute, which shows up on the
  board as "exec format error" long after the build succeeded.

  Build it on the Jetson itself, or cross-build with binfmt and buildx:
      docker run --privileged --rm tonistiigi/binfmt --install arm64
      docker buildx build --platform linux/arm64 \\
          --file images/runner/jetson.Dockerfile \\
          --build-arg BASE_IMAGE=ghspot/runner:ubuntu-22.04 \\
          --tag ghspot/runner:${name} images/runner/
MSG
        return 1
    done <<< "$(echo "${ARCH_REQUIRED}" | grep -v '^$')"
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

# A variant built FROM another variant needs that one present first. Building it here means
# `build.sh jetson-r32` works on a clean machine instead of failing on a missing base.
ensure_base() {
    local base="$1" line name
    case "${base}" in "${REGISTRY}:"*) ;; *) return 0 ;; esac
    docker image inspect "${base}" >/dev/null 2>&1 && return 0

    name="${base#"${REGISTRY}":}"
    echo "--> ${base} is not built yet; building it first"
    while IFS= read -r line; do
        [ "${line%%:*}" = "${name}" ] || continue
        rest="${line#*:}"
        build_one "${name}" "${rest%%:*}" "${rest#*:}"
        return $?
    done <<< "$(variants)"

    echo "error: ${base} is not a known variant" >&2
    return 1
}

build_one() {
    local name="$1" dockerfile="$2" base="$3"
    echo "==> ${REGISTRY}:${name}  (${base}, docker gid ${DOCKER_GID})"

    check_microarch "${name}" || return 1
    check_arch "${name}" || return 1
    ensure_base "${base}" || return 1

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

    if [ -z "${wanted}" ]; then
        # Building everything: a variant this machine cannot produce is skipped with a
        # reason, not treated as a failure. Asking for one by name still fails loudly.
        if ! check_arch "${name}" 2>/dev/null || ! check_microarch "${name}" 2>/dev/null; then
            echo "--> skipping ${name}: not buildable on this machine"
            found=1
            continue
        fi
        build_one "${name}" "${dockerfile}" "${base}"
        found=1
    elif [ "${wanted}" = "${name}" ]; then
        build_one "${name}" "${dockerfile}" "${base}"
        found=1
    fi
done <<< "$(variants)"

if [ "${found}" -eq 0 ]; then
    echo "unknown variant: ${wanted}" >&2
    list >&2
    exit 1
fi
