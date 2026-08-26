#!/usr/bin/env bash
# Run one job with a just-in-time configuration, then exit.
#
# There is no registration step, no token, and no cleanup to perform: the config blob is
# already scoped to this runner, and the runner de-registers itself when its job ends. If
# this script grows a decision in it, that decision belongs in the daemon instead.
set -euo pipefail

if [[ -z "${RUNNER_JIT_CONFIG:-}" ]]; then
    echo "entrypoint: RUNNER_JIT_CONFIG is not set; the daemon must supply it" >&2
    exit 64
fi

# Forward SIGTERM so `docker stop` lets the runner finish the job it has accepted rather
# than abandoning it half-done.
runner_pid=""
forward() {
    if [[ -n "${runner_pid}" ]]; then
        kill -TERM "${runner_pid}" 2>/dev/null || true
        wait "${runner_pid}"
    fi
}
trap forward TERM INT

cd /home/runner

./run.sh --jitconfig "${RUNNER_JIT_CONFIG}" &
runner_pid=$!
wait "${runner_pid}"
