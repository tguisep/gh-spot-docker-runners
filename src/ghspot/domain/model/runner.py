"""The Runner aggregate: one ephemeral runner, from minted credentials to reaped container."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

from ghspot.domain.errors import IllegalStateTransitionError
from ghspot.domain.model.events import (
    DomainEvent,
    RunnerCameOnline,
    RunnerFailed,
    RunnerRegistered,
    RunnerStarted,
    RunnerTookJob,
)
from ghspot.domain.model.events import (
    RunnerRetired as RunnerRetiredEvent,
)
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget

#: Our own identity for a runner, independent of GitHub's numeric id and Docker's container id.
#: It exists before either of those do, which is what makes crash recovery possible.
RunnerId = NewType("RunnerId", str)

_POOL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


class RunnerState(StrEnum):
    """Where a runner is in its life.

    The order matters only for display; the legal moves are declared in ``_TRANSITIONS``.
    """

    PENDING = "pending"
    """Decided on, nothing created yet."""

    REGISTERED = "registered"
    """A just-in-time config exists on GitHub. No container yet — the crash-critical window."""

    STARTING = "starting"
    """A container exists and is booting the runner process."""

    IDLE = "idle"
    """Connected to GitHub, long-polling for work."""

    BUSY = "busy"
    """Executing a job. A just-in-time runner reaches this at most once."""

    DRAINING = "draining"
    """Asked to stop; finishing the job it already accepted."""

    RETIRED = "retired"
    """Gone from Docker and from GitHub. Terminal."""

    FAILED = "failed"
    """Broken in a way that needs cleaning up. Terminal for scheduling purposes."""


_TRANSITIONS: dict[RunnerState, frozenset[RunnerState]] = {
    RunnerState.PENDING: frozenset({RunnerState.REGISTERED, RunnerState.FAILED}),
    RunnerState.REGISTERED: frozenset(
        {RunnerState.STARTING, RunnerState.RETIRED, RunnerState.FAILED}
    ),
    RunnerState.STARTING: frozenset(
        {RunnerState.IDLE, RunnerState.BUSY, RunnerState.RETIRED, RunnerState.FAILED}
    ),
    RunnerState.IDLE: frozenset(
        {RunnerState.BUSY, RunnerState.DRAINING, RunnerState.RETIRED, RunnerState.FAILED}
    ),
    RunnerState.BUSY: frozenset({RunnerState.DRAINING, RunnerState.RETIRED, RunnerState.FAILED}),
    RunnerState.DRAINING: frozenset({RunnerState.RETIRED, RunnerState.FAILED}),
    # Terminal states. FAILED still allows RETIRED so cleanup can close the record out.
    RunnerState.RETIRED: frozenset(),
    RunnerState.FAILED: frozenset({RunnerState.RETIRED}),
}

#: States in which a runner is able to pick up a queued job, now or shortly.
AVAILABLE_STATES = frozenset({RunnerState.REGISTERED, RunnerState.STARTING, RunnerState.IDLE})

#: States in which a runner still consumes a slot in its pool.
ACTIVE_STATES = frozenset(
    {
        RunnerState.PENDING,
        RunnerState.REGISTERED,
        RunnerState.STARTING,
        RunnerState.IDLE,
        RunnerState.BUSY,
        RunnerState.DRAINING,
    }
)


def runner_name_for(pool: str, runner_id: RunnerId) -> str:
    """The name a runner is registered under on GitHub.

    Derived from our own id so it is unique by construction, and prefixed so a human reading
    the runner list on github.com can tell where it came from.
    """
    if not _POOL_NAME.match(pool):
        raise ValueError(f"{pool!r} is not a valid pool name")
    return f"ghspot-{pool}-{runner_id[:12]}"


@dataclass(slots=True)
class Runner:
    """One ephemeral runner.

    The aggregate owns its own state machine: every move goes through a named method that
    refuses illegal transitions and records what happened. Nothing outside assigns to
    ``state`` directly.
    """

    id: RunnerId
    name: str
    pool: str
    repository: RepositoryTarget
    labels: LabelSet
    created_at: datetime
    state: RunnerState = RunnerState.PENDING
    state_changed_at: datetime | None = None
    github_runner_id: int | None = None
    container_id: str | None = None
    current_job_id: int | None = None
    failure_reason: str | None = None
    _events: list[DomainEvent] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.state_changed_at is None:
            self.state_changed_at = self.created_at

    # -- transitions ---------------------------------------------------------------

    def register(self, github_runner_id: int, at: datetime) -> None:
        """Record that a just-in-time config was minted for this runner."""
        self._move_to(RunnerState.REGISTERED, at)
        self.github_runner_id = github_runner_id
        self._record(
            RunnerRegistered(
                occurred_at=at,
                runner_id=self.id,
                runner_name=self.name,
                github_runner_id=github_runner_id,
                repository=self.repository,
            )
        )

    def attach_container(self, container_id: str, at: datetime) -> None:
        """Record that a container was created and started for this runner."""
        self._move_to(RunnerState.STARTING, at)
        self.container_id = container_id
        self._record(RunnerStarted(occurred_at=at, runner_id=self.id, container_id=container_id))

    def mark_online(self, at: datetime) -> None:
        """The runner process connected to GitHub and is waiting for work."""
        if self.state is RunnerState.IDLE:
            return
        self._move_to(RunnerState.IDLE, at)
        self._record(RunnerCameOnline(occurred_at=at, runner_id=self.id))

    def assign_job(self, job_id: int | None, at: datetime) -> None:
        """GitHub handed this runner a job.

        ``job_id`` is optional because the runner list reports *that* a runner is busy
        without saying which job it took. Correlating the two costs extra API calls that
        buy nothing the reconciler needs.
        """
        if self.state is RunnerState.BUSY and (job_id is None or self.current_job_id == job_id):
            return
        self._move_to(RunnerState.BUSY, at)
        self.current_job_id = job_id
        self._record(RunnerTookJob(occurred_at=at, runner_id=self.id, job_id=job_id))

    def drain(self, at: datetime) -> None:
        """Ask the runner to stop once its current job finishes."""
        if self.state is RunnerState.DRAINING:
            return
        self._move_to(RunnerState.DRAINING, at)

    def retire(self, at: datetime, reason: str) -> None:
        """The container is gone and GitHub no longer lists the runner."""
        if self.state is RunnerState.RETIRED:
            return
        self._move_to(RunnerState.RETIRED, at)
        self._record(RunnerRetiredEvent(occurred_at=at, runner_id=self.id, reason=reason))

    def fail(self, at: datetime, reason: str) -> None:
        """Something went wrong that leaves this runner unusable."""
        if self.state is RunnerState.FAILED:
            return
        self._move_to(RunnerState.FAILED, at)
        self.failure_reason = reason
        self._record(RunnerFailed(occurred_at=at, runner_id=self.id, reason=reason))

    # -- queries -------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether this runner still occupies a slot in its pool."""
        return self.state in ACTIVE_STATES

    @property
    def is_available(self) -> bool:
        """Whether this runner can take a queued job now or very shortly."""
        return self.state in AVAILABLE_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in {RunnerState.RETIRED, RunnerState.FAILED}

    def time_in_state(self, now: datetime) -> float:
        """Seconds spent in the current state, never negative."""
        assert self.state_changed_at is not None  # set in __post_init__
        return max(0.0, (now - self.state_changed_at).total_seconds())

    def idle_for(self, now: datetime) -> float:
        """Seconds spent waiting for work; zero unless the runner is idle."""
        return self.time_in_state(now) if self.state is RunnerState.IDLE else 0.0

    def busy_for(self, now: datetime) -> float:
        """Seconds spent on the current job; zero unless the runner is working."""
        return (
            self.time_in_state(now)
            if self.state in {RunnerState.BUSY, RunnerState.DRAINING}
            else 0.0
        )

    # -- events --------------------------------------------------------------------

    def pull_events(self) -> list[DomainEvent]:
        """Take the recorded events, leaving the aggregate clean."""
        events, self._events = self._events, []
        return events

    # -- internals -----------------------------------------------------------------

    def _move_to(self, target: RunnerState, at: datetime) -> None:
        if target not in _TRANSITIONS[self.state]:
            raise IllegalStateTransitionError(self.name, self.state.value, target.value)
        self.state = target
        self.state_changed_at = at

    def _record(self, event: DomainEvent) -> None:
        self._events.append(event)
