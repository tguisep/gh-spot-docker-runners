"""Wire types.

Separate from the application DTOs on purpose: a JSON field is a promise to whoever wrote a
client against it, and it should not change because an internal dataclass did.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ghspot.application.dto import PoolView, RunnerView, StatsView, TickReport, UsageStats


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
    pools: int
    docker: bool
    """Whether the Docker daemon answered a ping."""

    configured: bool = True
    """False on a fresh install: the daemon is up and nobody has finished filling in the
    configuration. Narrower than `doctor`, which asks whether everything works."""

    setup_reason: str | None = None
    """What is still missing, when `configured` is false."""


class LogsResponse(BaseModel):
    runner_id: str
    lines: str


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
