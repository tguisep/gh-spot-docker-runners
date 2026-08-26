"""Wire types.

Separate from the application DTOs on purpose: a JSON field is a promise to whoever wrote a
client against it, and it should not change because an internal dataclass did.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ghspot.application.dto import PoolView, RunnerView, TickReport


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


class HealthResponse(BaseModel):
    status: str
    version: str
    pools: int
    docker: bool
    """Whether the Docker daemon answered a ping."""


class LogsResponse(BaseModel):
    runner_id: str
    lines: str


class ErrorResponse(BaseModel):
    detail: str
