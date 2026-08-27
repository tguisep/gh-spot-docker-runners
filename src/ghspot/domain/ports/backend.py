"""The port to whatever actually runs a runner. Docker today; the shape allows others."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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


#: Images carrying this label are what runners start from, so housekeeping must never
#: reclaim them however long they sit unused.
PROTECTED_IMAGE_LABEL = "io.ghspot.image=runner"


@dataclass(frozen=True, slots=True)
class PruneRequest:
    """What housekeeping is allowed to reclaim.

    Every age is a floor, not a target: nothing younger is touched, so a job that is still
    running cannot have the image it is using pulled out from under it.
    """

    containers_older_than: timedelta | None = None
    """Stopped containers. Running ones are never touched — a job may have started something
    deliberately, and guessing which is rubbish is how you delete somebody's database."""

    images_older_than: timedelta | None = None
    volumes: bool = False
    build_cache_older_than: timedelta | None = None
    keep_build_cache_bytes: int | None = None

    @property
    def is_noop(self) -> bool:
        return not any(
            (
                self.containers_older_than,
                self.images_older_than,
                self.volumes,
                self.build_cache_older_than,
                self.keep_build_cache_bytes is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class PruneReport:
    """What housekeeping actually reclaimed."""

    containers: int = 0
    images: int = 0
    volumes: int = 0
    build_cache_bytes: int = 0
    reclaimed_bytes: int = 0
    errors: tuple[str, ...] = ()

    @property
    def removed_anything(self) -> bool:
        return bool(self.containers or self.images or self.volumes or self.reclaimed_bytes)


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

    async def prune(self, request: PruneRequest) -> PruneReport:
        """Reclaim what jobs left behind on the host.

        Implementations must never remove an image carrying
        :data:`PROTECTED_IMAGE_LABEL`, nor any running container.
        """
        ...
