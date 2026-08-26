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
