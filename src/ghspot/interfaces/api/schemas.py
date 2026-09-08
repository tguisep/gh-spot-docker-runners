"""Wire types.

Separate from the application DTOs on purpose: a JSON field is a promise to whoever wrote a
client against it, and it should not change because an internal dataclass did.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ghspot.application.dto import (
    HostPressureView,
    PoolPressureView,
    PoolView,
    QueueEntryView,
    QueueView,
    RunnerView,
    StatsView,
    TickReport,
    UsageStats,
)


class RunnerResponse(BaseModel):
    id: str
    name: str
    pool: str
    repository: str
    state: str
    labels: list[str]
    created_at: datetime
    github_runner_id: int | None = None
    container_id: str | None = None
    current_job_id: int | None = None
    age_seconds: float = 0.0
    time_in_state_seconds: float = 0.0
    failure_reason: str | None = None

    cpu_percent: float | None = None
    """Null unless the request asked for usage: sampling costs a call per container."""

    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None

    @classmethod
    def of(cls, view: RunnerView) -> RunnerResponse:
        return cls(
            id=view.id,
            name=view.name,
            pool=view.pool,
            repository=view.repository,
            state=view.state.value,
            labels=view.labels,
            created_at=view.created_at,
            github_runner_id=view.github_runner_id,
            container_id=view.container_id,
            current_job_id=view.current_job_id,
            age_seconds=round(view.age_seconds, 1),
            time_in_state_seconds=round(view.time_in_state_seconds, 1),
            failure_reason=view.failure_reason,
            cpu_percent=view.cpu_percent,
            memory_bytes=view.memory_bytes,
            memory_limit_bytes=view.memory_limit_bytes,
            memory_percent=(None if view.memory_percent is None else round(view.memory_percent, 1)),
        )


class PoolResponse(BaseModel):
    name: str
    repository: str
    labels: list[str]
    min_idle: int
    max_runners: int
    idle: int = 0
    busy: int = 0
    starting: int = 0
    active: int = 0
    headroom: int = 0
    queued_jobs: int = 0
    oldest_wait_seconds: float = 0.0
    runners: list[RunnerResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view: PoolView) -> PoolResponse:
        return cls(
            name=view.name,
            repository=view.repository,
            labels=view.labels,
            min_idle=view.min_idle,
            max_runners=view.max_runners,
            idle=view.idle,
            busy=view.busy,
            starting=view.starting,
            active=view.active,
            headroom=view.headroom,
            queued_jobs=view.queued_jobs,
            oldest_wait_seconds=round(view.oldest_wait_seconds, 1),
            runners=[RunnerResponse.of(runner) for runner in view.runners],
        )


class TickResponse(BaseModel):
    started_at: datetime
    duration_seconds: float
    launched: int
    retired: int
    terminated: int
    repaired: int
    queued_jobs: int
    errors: list[str]
    notes: list[str]

    @classmethod
    def of(cls, report: TickReport) -> TickResponse:
        return cls(
            started_at=report.started_at,
            duration_seconds=round(report.duration_seconds, 3),
            launched=report.launched,
            retired=report.retired,
            terminated=report.terminated,
            repaired=report.repaired,
            queued_jobs=report.queued_jobs,
            errors=report.errors,
            notes=report.notes,
        )


class UsageResponse(BaseModel):
    """One group's usage. Derived values are sent rather than left to the client, so a
    dashboard and `ghspot stats` cannot disagree about what a failure rate means."""

    key: str
    runners: int
    jobs: int
    failed: int
    completed: int
    idle_runners: int
    failure_rate: float
    busy_seconds: float
    alive_seconds: float
    mean_busy_seconds: float
    mean_wait_seconds: float
    utilisation: float
    live: int

    @classmethod
    def of(cls, stats: UsageStats) -> UsageResponse:
        return cls(
            key=stats.key,
            runners=stats.runners,
            jobs=stats.jobs,
            failed=stats.failed,
            completed=stats.completed,
            idle_runners=stats.idle_runners,
            failure_rate=round(stats.failure_rate, 4),
            busy_seconds=round(stats.busy_seconds, 3),
            alive_seconds=round(stats.alive_seconds, 3),
            mean_busy_seconds=round(stats.mean_busy_seconds, 3),
            mean_wait_seconds=round(stats.mean_wait_seconds, 3),
            utilisation=round(stats.utilisation, 4),
            live=stats.live,
        )


class FailureCount(BaseModel):
    reason: str
    count: int


class StatsResponse(BaseModel):
    host: str = ""
    """The machine these numbers are about — each daemon counts only its own runners."""

    since: datetime | None
    until: datetime
    events_read: int
    total: UsageResponse
    by_repository: list[UsageResponse]
    by_pool: list[UsageResponse]
    failures: list[FailureCount]

    @classmethod
    def of(cls, view: StatsView) -> StatsResponse:
        return cls(
            host=view.host,
            since=view.since,
            until=view.until,
            events_read=view.events_read,
            total=UsageResponse.of(view.total),
            by_repository=[UsageResponse.of(row) for row in view.by_repository],
            by_pool=[UsageResponse.of(row) for row in view.by_pool],
            failures=[FailureCount(reason=reason, count=count) for reason, count in view.failures],
        )


class HealthResponse(BaseModel):
    status: str
    version: str
    host: str = ""
    """The machine this daemon runs on.

    Several hosts can serve one repository, and each daemon answers only for its own. Without
    this a client polling three of them cannot tell their answers apart.
    """

    pools: int
    docker: bool
    """Whether the Docker daemon answered a ping."""

    configured: bool = True
    """False on a fresh install: the daemon is up and nobody has finished filling in the
    configuration. Narrower than `doctor`, which asks whether everything works."""

    config_stale: bool = False
    """The configuration on disk has been edited since the daemon read it.

    The daemon builds pools, labels and clients from settings once, at startup, so an edit
    changes nothing until it restarts. Without this the operator adds a label, watches the
    dashboard keep showing the old one, and cannot tell a stale process from a bad file."""

    setup_reason: str | None = None
    """What is still missing, when `configured` is false."""


class LogsResponse(BaseModel):
    runner_id: str
    lines: str

    source: Literal["container", "archive", "none"] = "container"
    """Where these lines came from.

    ``container`` is the live view. ``archive`` is the tail kept when the runner was retired
    and its container removed — the same output, but frozen and no longer growing. ``none``
    means there is nothing: an empty string used to be the answer to all three, so a retired
    runner looked identical to one that had simply printed nothing yet."""

    reason: str | None = None
    """Why there is nothing, when ``source`` is ``none``."""


class JobLogsResponse(BaseModel):
    """The forge's log for the job a runner is running, when the forge has one."""

    runner_id: str
    job_id: int | None
    available: bool
    """False while the job is still running: GitHub writes the log when it finishes, so
    there is nothing to fetch yet. Distinct from an empty log."""

    lines: str


class ErrorResponse(BaseModel):
    detail: str


class QueueEntryResponse(BaseModel):
    """One queued job, and the one thing standing in front of it."""

    job_id: int
    run_id: int
    repository: str
    workflow: str
    job_name: str
    title: str
    labels: list[str]

    url: str
    """The job's page on the forge, as the forge itself reported it. Empty if it gave none."""

    work_class: str
    """What kind of work this is: `default-branch`, `manual`, `pull-request`, `branch`,
    `draft` or `scheduled`. Derived from the run, never declared by the workflow."""

    urgency: int
    """What that class is worth. The job's own rank — distinct from `priority` below, which
    is the serving pool's weight."""

    pool: str
    """Empty when no configured pool serves this job's labels."""

    priority: int
    position: int
    """1-based place in its pool's line, oldest first. 0 when no pool serves it."""

    reason: str
    detail: str
    waiting_seconds: float
    delayed: bool
    """False when a runner is free or starting for it — then the wait is GitHub's dispatch."""

    @classmethod
    def of(cls, view: QueueEntryView) -> QueueEntryResponse:
        return cls(
            job_id=view.job_id,
            run_id=view.run_id,
            repository=view.repository,
            workflow=view.workflow,
            job_name=view.job_name,
            title=view.title,
            labels=view.labels,
            url=view.url,
            work_class=view.work_class.value,
            urgency=view.urgency,
            pool=view.pool,
            priority=view.priority,
            position=view.position,
            reason=view.reason.value,
            detail=view.detail,
            waiting_seconds=round(view.waiting_seconds, 1),
            delayed=view.is_delayed,
        )


class PoolPressureResponse(BaseModel):
    pool: str
    repository: str
    priority: int
    queued: int
    available: int
    active: int
    max_runners: int
    launching: int
    wanted: int
    blocked_by: str
    oldest_wait_seconds: float = 0.0

    @classmethod
    def of(cls, view: PoolPressureView) -> PoolPressureResponse:
        return cls(
            pool=view.pool,
            repository=view.repository,
            priority=view.priority,
            queued=view.queued,
            available=view.available,
            active=view.active,
            max_runners=view.max_runners,
            launching=view.launching,
            wanted=view.wanted,
            blocked_by=view.blocked_by,
            oldest_wait_seconds=round(view.oldest_wait_seconds, 1),
        )


class HostPressureResponse(BaseModel):
    """Measured load beside the limits it is judged against. Null means unread."""

    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    io_percent: float | None = None
    """How busy Docker's disk is, not how full. Null on a daemon's first probe: it is a rate,
    and a rate needs two readings to exist."""

    containers_running: int | None = None
    cpu_high_water: float | None = None
    memory_high_water: float | None = None
    disk_high_water: float | None = None
    io_high_water: float | None = None
    max_containers: int | None = None
    max_cpus: float | None = None
    max_memory_bytes: int | None = None
    holding: str = ""

    @classmethod
    def of(cls, view: HostPressureView) -> HostPressureResponse:
        return cls(
            cpu_percent=view.cpu_percent,
            memory_percent=view.memory_percent,
            disk_percent=view.disk_percent,
            io_percent=view.io_percent,
            containers_running=view.containers_running,
            cpu_high_water=view.cpu_high_water,
            memory_high_water=view.memory_high_water,
            disk_high_water=view.disk_high_water,
            io_high_water=view.io_high_water,
            max_containers=view.max_containers,
            max_cpus=view.max_cpus,
            max_memory_bytes=view.max_memory_bytes,
            holding=view.holding,
        )


class QueueResponse(BaseModel):
    """What the daemon's last tick saw waiting, and why none of it has started.

    `taken_at` and `stale` are part of the answer, not metadata about it: a client that
    renders `entries` without them will show a stopped daemon as an empty queue.
    """

    taken_at: datetime | None = None
    age_seconds: float = 0.0
    stale: bool = False
    total: int = 0
    delayed: int = 0
    longest_wait_seconds: float = 0.0
    entries: list[QueueEntryResponse] = Field(default_factory=list)
    pools: list[PoolPressureResponse] = Field(default_factory=list)
    host: HostPressureResponse = Field(default_factory=HostPressureResponse)
    notes: list[str] = Field(default_factory=list)
    unreadable: list[str] = Field(default_factory=list)
    """Repositories whose queue the last tick could not read."""

    @classmethod
    def of(cls, view: QueueView) -> QueueResponse:
        return cls(
            taken_at=view.taken_at,
            age_seconds=round(view.age_seconds, 1),
            stale=view.stale,
            total=view.total,
            delayed=view.delayed,
            longest_wait_seconds=round(view.longest_wait_seconds, 1),
            entries=[QueueEntryResponse.of(entry) for entry in view.entries],
            pools=[PoolPressureResponse.of(pool) for pool in view.pools],
            host=HostPressureResponse.of(view.host),
            notes=view.notes,
            unreadable=view.unreadable,
        )
