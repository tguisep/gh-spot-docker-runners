#!/usr/bin/env bash
# Check that a built runner image honours its contract and carries the toolset.
#
#   images/runner/verify.sh ubuntu-24.04
#
# The tool list is the one GitHub installs on its own ubuntu images
# (actions/runner-images: images/ubuntu/toolsets/toolset-2404.json), checked by binary name
# rather than package name so the two families can be compared directly.
#
# This exists because a missing tool does not fail the build — it fails somebody's workflow,
# later, with an error that points nowhere near the image.
set -euo pipefail

VARIANT="${1:?usage: verify.sh <variant>}"
IMAGE="${REGISTRY:-ghspot/runner}:${VARIANT}"

# Present on every variant. A gap here is a bug.
REQUIRED="
gcc g++ make cmake autoconf automake libtoolize m4 flex bison swig patchelf pkg-config
curl wget git ssh rsync aria2c
python3 pip3 pipx uv uvx mise node npm
jq gh shellcheck file tree parallel time sqlite3 hg
tar unzip zip bzip2 xz gzip brotli lz4 pigz 7z
gpg rpm fakeroot sudo getfacl
nc telnet ftp sshpass
"

# Absent on some releases, by the distribution's choice rather than ours.
OPTIONAL="upx Xvfb"

# The lists above are written multi-line for readability; the inner shell needs them flat.
REQUIRED="$(echo ${REQUIRED} | tr -s '[:space:]' ' ')"
OPTIONAL="$(echo ${OPTIONAL} | tr -s '[:space:]' ' ')"

echo "==> ${IMAGE}"

fail() { echo "FAIL: $*" >&2; exit 1; }

# An arm64 image cannot be introspected on an x86-64 host without emulation, and every check
# below runs the image. Say so once rather than failing eight times with "exec format error".
IMAGE_ARCH="$(docker image inspect --format '{{.Architecture}}' "${IMAGE}" 2>/dev/null || true)"
HOST_ARCH="$(docker version --format '{{.Server.Arch}}' 2>/dev/null || true)"
if [ -n "${IMAGE_ARCH}" ] && [ -n "${HOST_ARCH}" ] && [ "${IMAGE_ARCH}" != "${HOST_ARCH}" ]; then
    if ! docker run --rm --entrypoint true "${IMAGE}" 2>/dev/null; then
        echo "    skipped: ${IMAGE_ARCH} image on a ${HOST_ARCH} host, and no emulation is set up" >&2
        echo "    install it with: docker run --privileged --rm tonistiigi/binfmt --install ${IMAGE_ARCH}" >&2
        exit 0
    fi
    echo "    note: ${IMAGE_ARCH} image running under emulation; this will be slow"
fi

# --- the contract ----------------------------------------------------------------------

docker run --rm "${IMAGE}" >/dev/null 2>&1 && fail "started without RUNNER_JIT_CONFIG"
test "$(docker run --rm "${IMAGE}" >/dev/null 2>&1; echo $?)" = "64" \
    || fail "wrong exit code without a configuration"
echo "    ok: refuses to start without a configuration"

if docker run --rm --entrypoint env "${IMAGE}" | grep -qiE 'token|jitconfig'; then
    fail "a credential-shaped variable is baked into the image"
fi
echo "    ok: carries no credential"

test "$(docker run --rm --entrypoint id "${IMAGE}" -u)" != "0" || fail "runs as root"
echo "    ok: runs unprivileged"

EXPECTED_GID="$(getent group docker | cut -d: -f3)"
ACTUAL_GID="$(docker run --rm --entrypoint sh "${IMAGE}" -c 'getent group docker | cut -d: -f3')"
test "${EXPECTED_GID}" = "${ACTUAL_GID}" \
    || fail "docker gid ${ACTUAL_GID} does not match the host's ${EXPECTED_GID}"
echo "    ok: docker gid matches the host (${ACTUAL_GID})"

docker run --rm --entrypoint sh "${IMAGE}" -c 'test -f run.sh' \
    || fail "the runner payload is missing"
echo "    ok: runner payload present"

# --- the toolset -----------------------------------------------------------------------

MISSING="$(docker run --rm --entrypoint sh "${IMAGE}" -c "
    for t in ${REQUIRED}; do command -v \"\$t\" >/dev/null 2>&1 || printf '%s ' \"\$t\"; done
")"
[ -z "${MISSING}" ] || fail "missing required tools: ${MISSING}"
echo "    ok: every required tool present"

# pipx has to be usable *by the runner user*, not merely present: upstream owns its
# directories as root, and a job installing a tool at runtime is not root.
# Being on PATH is not the same as being usable by the runner user, which is what a job is.
docker run --rm --entrypoint sh "${IMAGE}" -c '
    pipx install "poetry==2.1.1" >/dev/null 2>&1 && command -v poetry >/dev/null
' || fail "the runner user cannot pipx install"
echo "    ok: the runner user can pipx install"

docker run --rm --entrypoint sh "${IMAGE}" -c '
    uvx --quiet ruff@0.14.4 --version >/dev/null 2>&1
' || fail "the runner user cannot uvx"
echo "    ok: the runner user can uvx"

docker run --rm --entrypoint sh "${IMAGE}" -c '
    mise use -g node@22 >/dev/null 2>&1
' || fail "the runner user cannot use mise"
echo "    ok: the runner user can use mise"

ABSENT="$(docker run --rm --entrypoint sh "${IMAGE}" -c "
    for t in ${OPTIONAL}; do command -v \"\$t\" >/dev/null 2>&1 || printf '%s ' \"\$t\"; done
")"
if [ -n "${ABSENT}" ]; then
    echo "    note: not packaged for this release: ${ABSENT}"
fi

docker run --rm --entrypoint sh "${IMAGE}" -c '
    printf "    versions: python %s, node %s, gcc %s, git %s\n" \
        "$(python3 -V | cut -d" " -f2)" "$(node -v)" "$(gcc -dumpversion)" \
        "$(git --version | cut -d" " -f3)"
'

# --- Tegra -------------------------------------------------------------------------------

# The Jetson variant's whole reason to exist is finding the driver the runtime mounts in at
# start. Both mechanisms are checked: ld.so.cache is generated at build time, when the tegra
# directories are still empty, so LD_LIBRARY_PATH is what actually makes libcuda.so.1
# resolvable. Without it the image looks fine and every CUDA job fails.
case "${VARIANT}" in
    jetson-*)
        docker run --rm --entrypoint sh "${IMAGE}" -c \
            'test -f /etc/ld.so.conf.d/nvidia-tegra.conf' \
            || fail "no tegra entry in ld.so.conf.d"
        echo "    ok: tegra paths in ld.so.conf.d"

        docker run --rm --entrypoint sh "${IMAGE}" -c \
            'case ":$LD_LIBRARY_PATH:" in *:/usr/lib/aarch64-linux-gnu/tegra:*) ;; *) exit 1 ;; esac' \
            || fail "LD_LIBRARY_PATH does not carry the tegra directory"
        echo "    ok: LD_LIBRARY_PATH carries the tegra directory"
        ;;
esac

echo "==> verified"
