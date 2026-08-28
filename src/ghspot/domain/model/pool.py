"""Runner pools: the declared shape of a fleet, and the live fleet itself."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from ghspot.domain.errors import InvalidPoolSpecError, PoolAtCapacityError
from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget

_POOL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


class ProcessManager(StrEnum):
    """How many runners to keep, and when.

    php-fpm's `pm`, and for the same reason: "keep four warm", "keep a band warm" and "start
    one only when there is work" are three different intentions, and a pool that spells them
    out is a pool whose behaviour you can predict from its configuration.
    """

    STATIC = "static"
    """Exactly `max_runners`, always up. Nothing is reaped for being idle. The fastest possible
    first job, paid for continuously."""

    DYNAMIC = "dynamic"
    """Keep between `min_idle` and `max_idle` warm, growing to cover the queue and shrinking
    when it empties. The default, and what the daemon has always done."""

    ONDEMAND = "ondemand"
    """Nothing warm. A runner starts when a job is queued and goes away after `idle_timeout`.
    Cheapest, and every job pays container boot time."""


@dataclass(frozen=True, slots=True)
class PoolSpec:
    """What the operator asked for: one pool's desired shape.

    This is configuration turned into a value object, so a malformed ``config.toml`` fails
    at load time with a domain error rather than halfway through a reconciliation tick.
    """

    name: str
    repository: RepositoryTarget
    labels: LabelSet
    pm: ProcessManager = ProcessManager.DYNAMIC
    """How runners are kept, borrowed wholesale from php-fpm's `pm`.

    The three shapes an operator actually wants, named rather than assembled by hand out of
    `min_idle` and `idle_timeout` — where the same intent could be written three ways and
    two of them would be subtly wrong."""

    min_idle: int = 0
    max_idle: int | None = None
    """Warm runners above this are reaped without waiting for `idle_timeout`. php-fpm's
    `pm.max_spare_servers`. ``None`` means only the timeout bounds them, which is what the
    daemon did before this existed — a burst could leave the whole pool warm for the full
    timeout."""

    max_runners: int = 2
    idle_timeout: timedelta = timedelta(minutes=10)
    max_job_duration: timedelta = timedelta(hours=2)
    priority: int = 1
    """This pool's **share** of scarce host capacity, relative to the others.

    A weight, not a rank: a pool at 10 gets twice as many of the contested slots as one at 5,
    not all of them. Slots are interleaved rather than handed out in blocks, so the lighter
    pool starts runners throughout instead of waiting for the heavier one to be satisfied —
    on a fleet that is always busy, "wait your turn" and "never" would otherwise be the same
    thing.

    Only consulted when the host cannot satisfy every pool at once; with capacity to spare it
    changes nothing. A pool that stops wanting runners drops out and its share is
    redistributed, so this describes how contention is settled, not a fixed quota."""

    max_launch_per_tick: int = 2
    """Ceiling on how many runners may be started in a single tick, to avoid a thundering herd
    when a large matrix lands all at once."""

    requires_labels: LabelSet | None = None
    """Labels a job must ask for *by name* before this pool will serve it.

    Label matching is a subset rule, so a pool carrying extra labels serves jobs that never
    mentioned them: a pool labelled ``self-hosted, linux, x64, gpu`` will happily take a job
    asking only for ``self-hosted, linux, x64``. For an ordinary pool that is the point. For
    a scarce or expensive one — a GPU, a machine with a licence attached — it means the thing
    you were protecting gets spent on work that never wanted it.

    Naming a label here inverts the rule for that label: the job must have asked."""

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
        # A required label the pool does not carry can never match, so the pool would sit
        # idle while its jobs queued — a configuration mistake worth catching at load.
        if self.requires_labels is not None and not self.labels.satisfies(self.requires_labels):
            missing = [label for label in self.requires_labels if label not in self.labels]
            raise InvalidPoolSpecError(
                f"pool {self.name!r}: requires_labels names {missing}, which this pool does "
                "not carry, so it could never serve anything"
            )

    def can_serve(self, job: QueuedJob) -> bool:
        """Whether a job belongs to this pool.

        Same repository, labels this pool carries, and — if the pool demands any — labels the
        job asked for explicitly.
        """
        if job.repository != self.repository:
            return False
        if not self.labels.satisfies(job.labels):
            return False
        if self.requires_labels is None:
            return True
        return job.labels.satisfies(self.requires_labels)


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
