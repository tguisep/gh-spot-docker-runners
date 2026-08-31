"""Finding out which job a runner ran, after the fact."""

from __future__ import annotations

from ghspot.domain.model.runner import Runner
from ghspot.domain.ports.forge import ForgeClient
from ghspot.domain.ports.repository import RunnerRepository


class FindJobForRunner:
    """The job a runner took, searched for once and then remembered.

    The reconciler never learns this. GitHub's runner list reports *that* a runner is busy
    without saying which job it took, and correlating the two on every tick would cost
    requests forever for something only a human reading logs ever wants.

    So it is asked for on demand — and written back to the record, because the logs page polls
    every ten seconds and a search per poll would spend the hourly budget on one open tab.
    """

    def __init__(self, forge: ForgeClient, runners: RunnerRepository) -> None:
        self._forge = forge
        self._runners = runners

    async def __call__(self, runner: Runner) -> int | None:
        if runner.current_job_id is not None:
            return runner.current_job_id
        if runner.github_runner_id is None:
            # Never registered, so it cannot have been handed a job. Searching would be a
            # walk through recent history to prove something the record already knows.
            return None

        found = await self._forge.find_job_for_runner(runner.repository, runner.name)
        if found is None:
            return None

        runner.remember_job(found)
        await self._runners.save(runner)
        return found
