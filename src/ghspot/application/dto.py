"""Data the interfaces read, kept separate from the aggregates they are derived from.

The CLI and the API render these; neither touches a domain object, so the domain stays free
to change shape without breaking a table column or a JSON field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ghspot.domain.model.runner import RunnerState


@dataclass(frozen=True, slots=True)
class RunnerView:
    """One runner, as an operator sees it."""

    id: str
    name: str
    pool: str
    repository: str
    state: RunnerState
    labels: list[str]
    created_at: datetime
    github_runner_id: int | None = None
    container_id: str | None = None
    current_job_id: int | None = None
    age_seconds: float = 0.0
    time_in_state_seconds: float = 0.0
    failure_reason: str | None = None

    cpu_percent: float | None = None
    """Across all cores, as `docker stats` reports it. ``None`` when not sampled — which is
    the default, because sampling costs a call per container and most reads do not need it."""

    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None

    @property
    def memory_percent(self) -> float | None:
        if self.memory_bytes is None or not self.memory_limit_bytes:
            return None
        return 100.0 * self.memory_bytes / self.memory_limit_bytes

    @property
    def short_container_id(self) -> str:
        return (self.container_id or "")[:12]


@dataclass(frozen=True, slots=True)
class PoolView:
    """One pool's declared shape and what it currently holds."""

    name: str
    repository: str
    labels: list[str]
    min_idle: int
    max_runners: int
    idle: int = 0
    busy: int = 0
    starting: int = 0
    active: int = 0
    queued_jobs: int = 0
    runners: list[RunnerView] = field(default_factory=list)

    @property
    def headroom(self) -> int:
        return max(0, self.max_runners - self.active)


@dataclass(frozen=True, slots=True)
class UsageStats:
    """What a repository, or a pool, cost and delivered over a window.

    Counted from the event log rather than from the runners table: the table is pruned and
    its rows are deleted as runners retire, so anything derived from it would quietly stop
    covering the period an operator is asking about.
    """

    key: str
    """The repository or pool these numbers are for. Empty for the total row."""

    runners: int = 0
    """Registered with GitHub. Every runner starts here, so it is the denominator."""

    jobs: int = 0
    """Runners that were handed a job. A just-in-time runner takes at most one, so this is
    also the number of jobs served."""

    failed: int = 0
    completed: int = 0

    busy_seconds: float = 0.0
    """Summed time between taking a job and the runner going away: the machine time actually
    spent on CI."""

    alive_seconds: float = 0.0
    """Summed time between registration and the runner going away, whether it worked or not.
    The gap against `busy_seconds` is what idle capacity costs."""

    wait_seconds: float = 0.0
    """Summed time between registration and being handed a job — what `min_idle` buys down."""

    waits_counted: int = 0
    """How many runners contributed to `wait_seconds`. Not every runner gets a job."""

    live: int = 0
    """Runners in this group right now, from the projection rather than the log."""

    @property
    def failure_rate(self) -> float:
        return self.failed / self.runners if self.runners else 0.0

    @property
    def idle_runners(self) -> int:
        """Registered, never given a job, and already gone. Capacity that earned nothing."""
        return max(0, self.completed + self.failed - self.jobs)

    @property
    def mean_busy_seconds(self) -> float:
        return self.busy_seconds / self.jobs if self.jobs else 0.0

    @property
    def mean_wait_seconds(self) -> float:
        return self.wait_seconds / self.waits_counted if self.waits_counted else 0.0

    @property
    def utilisation(self) -> float:
        """Busy time as a share of time alive. Low means runners are sitting idle."""
        return self.busy_seconds / self.alive_seconds if self.alive_seconds else 0.0


@dataclass(frozen=True, slots=True)
class StatsView:
    """Everything `ghspot stats` renders."""

    since: datetime | None
    """Start of the window, or ``None`` when it covers the whole log."""

    until: datetime
    total: UsageStats
    by_repository: list[UsageStats] = field(default_factory=list)
    by_pool: list[UsageStats] = field(default_factory=list)
    failures: list[tuple[str, int]] = field(default_factory=list)
    """Failure reasons, commonest first. The point of the whole report when something is
    wrong, and empty when nothing is."""

    events_read: int = 0
    """How many log records the numbers came from. Zero means the window is empty, which is
    a different thing from a fleet that did nothing."""


@dataclass(frozen=True, slots=True)
class TickReport:
    """What one reconciliation pass did, for the logs and for `ghspot pool status`."""

    started_at: datetime
    duration_seconds: float = 0.0
    launched: int = 0
    retired: int = 0
    terminated: int = 0
    repaired: int = 0
    """Drift corrected: orphan registrations deleted, stray containers removed."""

    queued_jobs: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed_anything(self) -> bool:
        return bool(self.launched or self.retired or self.terminated or self.repaired)
