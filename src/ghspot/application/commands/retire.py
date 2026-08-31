"""Take one runner out of existence, on both sides."""

from __future__ import annotations

from contextlib import suppress

from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.domain.ports.backend import RunnerBackend
from ghspot.domain.ports.forge import ForgeClient
from ghspot.domain.ports.repository import RunnerLogArchive, RunnerRepository
from ghspot.domain.ports.system import Clock, EventPublisher

FINAL_LOG_LINES = 500
"""How much of a dying container to keep. Enough to hold a traceback and what led to it,
short of keeping a whole job's output for every runner that ever ran."""


class RetireRunner:
    """Stop a runner's container and remove its registration.

    Every step is best-effort and independently idempotent: whichever half already happened,
    running this again converges on the same end state. That is what lets the reconciler call
    it freely on anything that looks stale.
    """

    def __init__(
        self,
        forge: ForgeClient,
        backend: RunnerBackend,
        runners: RunnerRepository,
        clock: Clock,
        events: EventPublisher,
        stop_timeout_seconds: int = 30,
        archive: RunnerLogArchive | None = None,
    ) -> None:
        self._forge = forge
        self._backend = backend
        self._runners = runners
        self._clock = clock
        self._events = events
        self._stop_timeout = stop_timeout_seconds
        self._archive = archive

    async def __call__(self, runner: Runner, reason: str, *, force: bool = False) -> None:
        """Retire ``runner``.

        With ``force``, the container is killed outright — used for jobs that overran their
        deadline. Otherwise it is signalled and given time to finish what it accepted.
        """
        if runner.container_id:
            with suppress(GhSpotError):
                if force:
                    await self._backend.kill(runner.container_id)
                else:
                    await self._backend.stop(runner.container_id, self._stop_timeout)

            # After stopping and before removing: the container has said everything it is
            # going to, and removal is the moment its output stops existing. A runner that
            # finished a job leaves its log on GitHub, but one that failed leaves nothing —
            # and "failed" with no explanation is the record nobody can act on.
            await self._keep_the_last_of_it(runner)

            with suppress(GhSpotError):
                await self._backend.remove(runner.container_id)

        # Deleting a registration GitHub has already dropped is a no-op by contract, so this
        # runs unconditionally rather than guessing whether the runner de-registered itself.
        if runner.github_runner_id is not None:
            with suppress(GhSpotError):
                await self._forge.delete_runner(runner.repository, runner.github_runner_id)

        now = self._clock.now()
        if runner.state is not RunnerState.RETIRED:
            runner.retire(at=now, reason=reason)
        await self._runners.save(runner)

        events = runner.pull_events()
        if events:
            await self._events.publish(events)

    async def _keep_the_last_of_it(self, runner: Runner) -> None:
        """Copy the container's tail into the archive. Never fatal.

        Retiring is cleanup, and cleanup that fails because a *diagnostic* could not be saved
        leaves a container running and a registration behind — a far worse outcome than the
        missing log it was trying to prevent.
        """
        if self._archive is None or runner.container_id is None:
            return
        with suppress(GhSpotError):
            lines = await self._backend.logs(runner.container_id, tail=FINAL_LOG_LINES)
            if lines.strip():
                await self._archive.store(runner.id, lines)
