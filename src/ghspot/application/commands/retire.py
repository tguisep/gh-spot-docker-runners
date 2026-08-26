"""Take one runner out of existence, on both sides."""

from __future__ import annotations

from contextlib import suppress

from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.domain.ports.backend import RunnerBackend
from ghspot.domain.ports.forge import ForgeClient
from ghspot.domain.ports.repository import RunnerRepository
from ghspot.domain.ports.system import Clock, EventPublisher


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
    ) -> None:
        self._forge = forge
        self._backend = backend
        self._runners = runners
        self._clock = clock
        self._events = events
        self._stop_timeout = stop_timeout_seconds

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
