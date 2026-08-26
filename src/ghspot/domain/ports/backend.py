"""The port to whatever actually runs a runner. Docker today; the shape allows others."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """How to start one runner container.

    Everything variable about a runner lives here, which is what makes an alternative
    isolation model — Docker-in-Docker, a VM, a different host — a new adapter rather than a
    change to the calling code.
    """

    image: str
    name: str
    labels: Mapping[str, str]
    """Bookkeeping labels stamped onto the container so the daemon can find its own work again
    after a restart."""

    environment: Mapping[str, str] = field(default_factory=dict)
    """Includes the just-in-time config blob. Never a token."""

    cpus: float | None = None
    memory: str | None = None
    mount_docker_socket: bool = False
    volumes: Mapping[str, str] = field(default_factory=dict)
    network: str | None = None
    extra_hosts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContainerStatus:
    """A container as the backend currently reports it."""

    id: str
    name: str
    state: str
    """``created``, ``running``, ``exited``, ``dead`` — the backend's own vocabulary."""

    labels: Mapping[str, str]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def has_exited(self) -> bool:
        return self.state in {"exited", "dead"}


class RunnerBackend(Protocol):
    """Container lifecycle operations, all of them idempotent where that is meaningful."""

    async def create(self, spec: ContainerSpec) -> str:
        """Create and start a container, returning its id."""
        ...

    async def stop(self, container_id: str, timeout_seconds: int = 30) -> None:
        """Signal the container to stop, allowing it to drain. Quiet if already gone."""
        ...

    async def kill(self, container_id: str) -> None:
        """Stop the container immediately. Quiet if already gone."""
        ...

    async def remove(self, container_id: str) -> None:
        """Delete the container and its writable layer. Quiet if already gone."""
        ...

    async def inspect(self, container_id: str) -> ContainerStatus | None:
        """The container's current status, or ``None`` if it no longer exists."""
        ...

    async def list_owned(self, label_selector: Mapping[str, str]) -> Sequence[ContainerStatus]:
        """Every container carrying the given labels, running or not.

        This is how the daemon rediscovers its own fleet after a restart.
        """
        ...

    async def logs(self, container_id: str, tail: int = 200) -> str:
        """Recent output from a container, for the CLI and the API."""
        ...

    async def image_exists(self, image: str) -> bool:
        """Whether the runner image is present locally."""
        ...
