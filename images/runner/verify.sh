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
python3 pip3 pipx node npm
jq shellcheck file tree parallel time sqlite3 hg
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
docker run --rm --entrypoint sh "${IMAGE}" -c '
    pipx install "poetry==2.1.1" >/dev/null 2>&1 && command -v poetry >/dev/null
' || fail "the runner user cannot pipx install"
echo "    ok: the runner user can pipx install"

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

echo "==> verified"
