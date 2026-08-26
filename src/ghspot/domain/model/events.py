"""Domain events.

Aggregates record what happened rather than logging it, so the application layer decides
what to do with the facts — write them to the projection, emit a structured log line, or
one day publish them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ghspot.domain.model.target import RepositoryTarget


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for everything an aggregate records."""

    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RunnerRegistered(DomainEvent):
    """A just-in-time configuration was minted; GitHub now knows about this runner."""

    runner_id: str
    runner_name: str
    github_runner_id: int
    repository: RepositoryTarget


@dataclass(frozen=True, slots=True)
class RunnerStarted(DomainEvent):
    """A container was created and started for a registered runner."""

    runner_id: str
    container_id: str


@dataclass(frozen=True, slots=True)
class RunnerCameOnline(DomainEvent):
    """The runner process connected to GitHub and is waiting for work."""

    runner_id: str


@dataclass(frozen=True, slots=True)
class RunnerTookJob(DomainEvent):
    """The runner was assigned a job. With just-in-time runners this happens at most once."""

    runner_id: str
    job_id: int | None


@dataclass(frozen=True, slots=True)
class RunnerRetired(DomainEvent):
    """The runner is gone from both Docker and GitHub."""

    runner_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RunnerFailed(DomainEvent):
    """The runner could not be brought up, or died in a way that needs cleaning up."""

    runner_id: str
    reason: str
