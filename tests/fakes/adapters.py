"""In-memory stand-ins for every port.

These exist so the reconciliation loop can be driven through drift and crash scenarios that
would otherwise need a Docker daemon, a live repository and a well-timed `kill -9`.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import Runner, RunnerId
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.ports.backend import ContainerSpec, ContainerStatus
from ghspot.domain.ports.forge import ForgeRunner, JitRegistration


class FakeClock:
    """A clock that only moves when a test says so."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **offset: float) -> datetime:
        self._now += timedelta(**offset)
        return self._now


class SequentialIds:
    def __init__(self, prefix: str = "runner") -> None:
        self._counter = itertools.count(1)
        self._prefix = prefix

    def new_runner_id(self) -> RunnerId:
        return RunnerId(f"{self._prefix}{next(self._counter):04d}")


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, events: Sequence[DomainEvent]) -> None:
        self.events.extend(events)

    def of_type(self, kind: type[DomainEvent]) -> list[DomainEvent]:
        return [event for event in self.events if isinstance(event, kind)]


class InMemoryRunnerRepository:
    def __init__(self) -> None:
        self.saved: dict[RunnerId, Runner] = {}

    async def save(self, runner: Runner) -> None:
        self.saved[runner.id] = runner

    async def get(self, runner_id: RunnerId) -> Runner | None:
        return self.saved.get(runner_id)

    async def list_active(self) -> Sequence[Runner]:
        return [runner for runner in self.saved.values() if not runner.is_terminal]

    async def list_all(self) -> Sequence[Runner]:
        return list(self.saved.values())

    async def list_for_pool(self, pool: str) -> Sequence[Runner]:
        return [runner for runner in self.saved.values() if runner.pool == pool]

    async def delete(self, runner_id: RunnerId) -> None:
        self.saved.pop(runner_id, None)


@dataclass
class FakeForge:
    """A GitHub stand-in that remembers what it was told.

    ``fail_on`` makes any method raise, which is how the crash-recovery tests put the daemon
    down at a chosen instant.
    """

    runners: dict[int, ForgeRunner] = field(default_factory=dict)
    queued: dict[RepositoryTarget, list[QueuedJob]] = field(default_factory=dict)
    fail_on: set[str] = field(default_factory=set)
    unreachable: set[RepositoryTarget] = field(default_factory=set)
    """Repositories that answer with an error — a deleted repo, or a token missing a scope."""

    deleted: list[int] = field(default_factory=list)
    minted: list[str] = field(default_factory=list)
    _next_id: itertools.count[int] = field(default_factory=lambda: itertools.count(100))

    def _guard(self, method: str, repository: RepositoryTarget | None = None) -> None:
        if method in self.fail_on:
            raise GhSpotError(f"fake forge failure in {method}")
        if repository is not None and repository in self.unreachable:
            raise GhSpotError(f"repository {repository} is not reachable")

    async def create_jit_registration(
        self,
        repository: RepositoryTarget,
        name: str,
        labels: LabelSet,
        work_folder: str = "_work",
    ) -> JitRegistration:
        self._guard("create_jit_registration")
        github_id = next(self._next_id)
        self.runners[github_id] = ForgeRunner(
            id=github_id, name=name, status="offline", busy=False, labels=labels
        )
        self.minted.append(name)
        return JitRegistration(
            github_runner_id=github_id, name=name, encoded_config=f"jit-{github_id}"
        )

    async def list_runners(self, repository: RepositoryTarget) -> Sequence[ForgeRunner]:
        self._guard("list_runners", repository)
        return list(self.runners.values())

    async def delete_runner(self, repository: RepositoryTarget, github_runner_id: int) -> None:
        self._guard("delete_runner")
        self.runners.pop(github_runner_id, None)
        self.deleted.append(github_runner_id)

    async def list_queued_jobs(self, repository: RepositoryTarget) -> Sequence[QueuedJob]:
        self._guard("list_queued_jobs", repository)
        return list(self.queued.get(repository, []))

    async def rate_limit_reset_at(self) -> datetime | None:
        return None

    async def aclose(self) -> None:
        return None

    # -- helpers a test uses to simulate GitHub's side --------------------------------

    def bring_online(self, github_runner_id: int, *, busy: bool = False) -> None:
        listed = self.runners[github_runner_id]
        self.runners[github_runner_id] = replace(listed, status="online", busy=busy)

    def deregister(self, github_runner_id: int) -> None:
        """What a just-in-time runner does to itself the moment its job ends."""
        self.runners.pop(github_runner_id, None)


@dataclass
class FakeBackend:
    """A Docker stand-in holding containers in a dict."""

    containers: dict[str, ContainerStatus] = field(default_factory=dict)
    fail_on: set[str] = field(default_factory=set)
    created: list[ContainerSpec] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    images: set[str] = field(default_factory=lambda: {"ghspot/runner:test"})
    now: datetime = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    _next_id: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def _guard(self, method: str) -> None:
        if method in self.fail_on:
            raise GhSpotError(f"fake backend failure in {method}")

    async def create(self, spec: ContainerSpec) -> str:
        self._guard("create")
        container_id = f"c{next(self._next_id):04d}"
        self.created.append(spec)
        self.containers[container_id] = ContainerStatus(
            id=container_id,
            name=spec.name,
            state="running",
            labels=dict(spec.labels),
            started_at=self.now,
        )
        return container_id

    async def stop(self, container_id: str, timeout_seconds: int = 30) -> None:
        self._guard("stop")
        existing = self.containers.get(container_id)
        if existing is not None:
            self.containers[container_id] = replace(existing, state="exited", exit_code=0)

    async def kill(self, container_id: str) -> None:
        self._guard("kill")
        self.killed.append(container_id)
        existing = self.containers.get(container_id)
        if existing is not None:
            self.containers[container_id] = replace(existing, state="exited", exit_code=137)

    async def remove(self, container_id: str) -> None:
        self._guard("remove")
        self.removed.append(container_id)
        self.containers.pop(container_id, None)

    async def inspect(self, container_id: str) -> ContainerStatus | None:
        self._guard("inspect")
        return self.containers.get(container_id)

    async def list_owned(self, label_selector: Mapping[str, str]) -> Sequence[ContainerStatus]:
        self._guard("list_owned")
        return [
            status
            for status in self.containers.values()
            if all(status.labels.get(key) == value for key, value in label_selector.items())
        ]

    async def logs(self, container_id: str, tail: int = 200) -> str:
        self._guard("logs")
        return f"logs for {container_id}"

    async def image_exists(self, image: str) -> bool:
        return image in self.images

    async def ping(self) -> bool:
        self._guard("ping")
        return True

    # -- helpers a test uses to simulate Docker's side --------------------------------

    def exit_container(self, container_id: str, exit_code: int = 0) -> None:
        existing = self.containers[container_id]
        self.containers[container_id] = replace(
            existing, state="exited", exit_code=exit_code, finished_at=self.now
        )

    def vanish(self, container_id: str) -> None:
        """Someone ran `docker rm -f` behind the daemon's back."""
        self.containers.pop(container_id, None)
