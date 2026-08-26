"""Runner pools: the declared shape of a fleet, and the live fleet itself."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta

from ghspot.domain.errors import InvalidPoolSpecError, PoolAtCapacityError
from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget

_POOL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class PoolSpec:
    """What the operator asked for: one pool's desired shape.

    This is configuration turned into a value object, so a malformed ``config.toml`` fails
    at load time with a domain error rather than halfway through a reconciliation tick.
    """

    name: str
    repository: RepositoryTarget
    labels: LabelSet
    min_idle: int = 0
    max_runners: int = 2
    idle_timeout: timedelta = timedelta(minutes=10)
    max_job_duration: timedelta = timedelta(hours=2)
    max_launch_per_tick: int = 2
    """Ceiling on how many runners may be started in a single tick, to avoid a thundering herd
    when a large matrix lands all at once."""

    def __post_init__(self) -> None:
        if not _POOL_NAME.match(self.name):
            raise InvalidPoolSpecError(
                f"pool name {self.name!r} must be lowercase alphanumeric with hyphens, "
                "1 to 32 characters"
            )
        if self.min_idle < 0:
            raise InvalidPoolSpecError(f"pool {self.name!r}: min_idle cannot be negative")
        if self.max_runners < 1:
            raise InvalidPoolSpecError(f"pool {self.name!r}: max_runners must be at least 1")
        if self.min_idle > self.max_runners:
            raise InvalidPoolSpecError(
                f"pool {self.name!r}: min_idle ({self.min_idle}) exceeds "
                f"max_runners ({self.max_runners})"
            )
        if self.idle_timeout <= timedelta(0):
            raise InvalidPoolSpecError(f"pool {self.name!r}: idle_timeout must be positive")
        if self.max_job_duration <= timedelta(0):
            raise InvalidPoolSpecError(f"pool {self.name!r}: max_job_duration must be positive")
        if self.max_launch_per_tick < 1:
            raise InvalidPoolSpecError(
                f"pool {self.name!r}: max_launch_per_tick must be at least 1"
            )

    def can_serve(self, job: QueuedJob) -> bool:
        """Whether a job belongs to this pool: same repository, and labels this pool carries."""
        return job.repository == self.repository and self.labels.satisfies(job.labels)


@dataclass(slots=True)
class RunnerPool:
    """A pool's declared shape together with the runners currently in it.

    Assembled fresh each tick from what Docker and GitHub actually report, so it is a view of
    reality rather than a cache of it.
    """

    spec: PoolSpec
    _runners: dict[RunnerId, Runner] = field(default_factory=dict)

    @classmethod
    def of(cls, spec: PoolSpec, runners: Iterable[Runner] = ()) -> RunnerPool:
        pool = cls(spec=spec)
        for runner in runners:
            pool.admit(runner)
        return pool

    @property
    def name(self) -> str:
        return self.spec.name

    def admit(self, runner: Runner) -> None:
        """Add a runner to the pool, refusing to exceed the ceiling."""
        if runner.id in self._runners:
            self._runners[runner.id] = runner
            return
        if runner.is_active and self.active_count >= self.spec.max_runners:
            raise PoolAtCapacityError(self.spec.name, self.spec.max_runners)
        self._runners[runner.id] = runner

    def discard(self, runner_id: RunnerId) -> None:
        """Forget a runner, whether or not it was known."""
        self._runners.pop(runner_id, None)

    def get(self, runner_id: RunnerId) -> Runner | None:
        return self._runners.get(runner_id)

    def __iter__(self) -> Iterator[Runner]:
        return iter(self._runners.values())

    def __len__(self) -> int:
        return len(self._runners)

    # -- counts the scaling policy reads -------------------------------------------

    def in_state(self, *states: RunnerState) -> list[Runner]:
        wanted = frozenset(states)
        return [runner for runner in self._runners.values() if runner.state in wanted]

    @property
    def active(self) -> list[Runner]:
        """Runners still occupying a slot."""
        return [runner for runner in self._runners.values() if runner.is_active]

    @property
    def available(self) -> list[Runner]:
        """Runners that can take a queued job now or very shortly."""
        return [runner for runner in self._runners.values() if runner.is_available]

    @property
    def active_count(self) -> int:
        return len(self.active)

    @property
    def available_count(self) -> int:
        return len(self.available)

    @property
    def headroom(self) -> int:
        """How many more runners the pool may start before hitting its ceiling."""
        return max(0, self.spec.max_runners - self.active_count)
