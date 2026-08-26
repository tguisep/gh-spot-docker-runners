"""The Docker adapter.

The Docker SDK is synchronous, so every call is pushed onto a worker thread. The port is
async because the daemon is, not because Docker is.

Removal is deliberately ours rather than ``auto_remove``: a container that deletes itself the
instant it exits takes its exit code with it, and the reconciler needs to see that a runner
finished before deciding what to do about it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from ghspot.domain.errors import BackendError, ImageNotFoundError
from ghspot.domain.ports.backend import ContainerSpec, ContainerStatus

DOCKER_SOCKET = "/var/run/docker.sock"


class DockerRunnerBackend:
    """A :class:`~ghspot.domain.ports.backend.RunnerBackend` backed by a Docker daemon."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or _connect()

    async def create(self, spec: ContainerSpec) -> str:
        def run() -> str:
            try:
                container = self._client.containers.run(**_run_arguments(spec))
            except ImageNotFound as error:
                raise ImageNotFoundError(
                    f"the runner image {spec.image!r} is not present. "
                    f"Build it with: docker build -t {spec.image} images/runner/"
                ) from error
            except (APIError, DockerException) as error:
                raise BackendError(f"could not start {spec.name!r}: {error}") from error
            return str(container.id)

        return await asyncio.to_thread(run)

    async def stop(self, container_id: str, timeout_seconds: int = 30) -> None:
        await self._act(container_id, lambda c: c.stop(timeout=timeout_seconds), "stop")

    async def kill(self, container_id: str) -> None:
        await self._act(container_id, lambda c: c.kill(), "kill")

    async def remove(self, container_id: str) -> None:
        await self._act(container_id, lambda c: c.remove(force=True, v=True), "remove")

    async def inspect(self, container_id: str) -> ContainerStatus | None:
        def run() -> ContainerStatus | None:
            try:
                return _to_status(self._client.containers.get(container_id))
            except NotFound:
                return None
            except (APIError, DockerException) as error:
                raise BackendError(f"could not inspect {container_id}: {error}") from error

        return await asyncio.to_thread(run)

    async def list_owned(self, label_selector: Mapping[str, str]) -> Sequence[ContainerStatus]:
        filters = {"label": [f"{key}={value}" for key, value in label_selector.items()]}

        def run() -> list[ContainerStatus]:
            try:
                containers = self._client.containers.list(all=True, filters=filters)
            except (APIError, DockerException) as error:
                raise BackendError(f"could not list containers: {error}") from error
            return [_to_status(container) for container in containers]

        return await asyncio.to_thread(run)

    async def logs(self, container_id: str, tail: int = 200) -> str:
        def run() -> str:
            try:
                container = self._client.containers.get(container_id)
                raw = container.logs(tail=tail, stdout=True, stderr=True)
            except NotFound:
                return ""
            except (APIError, DockerException) as error:
                raise BackendError(f"could not read logs for {container_id}: {error}") from error
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

        return await asyncio.to_thread(run)

    async def image_exists(self, image: str) -> bool:
        def run() -> bool:
            try:
                self._client.images.get(image)
            except (ImageNotFound, NotFound):
                return False
            except (APIError, DockerException) as error:
                raise BackendError(f"could not inspect the image {image!r}: {error}") from error
            return True

        return await asyncio.to_thread(run)

    async def ping(self) -> bool:
        """Whether the daemon is reachable — used by ``ghspot doctor``."""

        def run() -> bool:
            try:
                return bool(self._client.ping())
            except (APIError, DockerException) as error:
                raise BackendError(f"the Docker daemon is not reachable: {error}") from error

        return await asyncio.to_thread(run)

    async def _act(self, container_id: str, action: Any, verb: str) -> None:
        """Apply an action to a container, treating an absent container as success.

        Every lifecycle operation is called by the reconciler on things it merely suspects are
        stale, so 'already gone' is the expected outcome as often as not.
        """

        def run() -> None:
            try:
                action(self._client.containers.get(container_id))
            except NotFound:
                return
            except (APIError, DockerException) as error:
                raise BackendError(f"could not {verb} {container_id}: {error}") from error

        await asyncio.to_thread(run)


def _connect() -> Any:
    try:
        return docker.from_env()
    except DockerException as error:
        raise BackendError(
            f"could not reach the Docker daemon: {error}. "
            "Is it running, and is this user in the 'docker' group?"
        ) from error


def _run_arguments(spec: ContainerSpec) -> dict[str, Any]:
    volumes: dict[str, dict[str, str]] = {
        source: {"bind": destination, "mode": "rw"} for source, destination in spec.volumes.items()
    }
    if spec.mount_docker_socket:
        volumes[DOCKER_SOCKET] = {"bind": DOCKER_SOCKET, "mode": "rw"}

    arguments: dict[str, Any] = {
        "image": spec.image,
        "name": spec.name,
        "detach": True,
        "labels": dict(spec.labels),
        "environment": dict(spec.environment),
        "volumes": volumes,
        # The daemon owns the lifecycle. A restart policy would resurrect a just-in-time
        # runner whose config is already spent, and it would come up unable to register.
        "restart_policy": {"Name": "no"},
        "auto_remove": False,
    }
    if spec.cpus is not None:
        arguments["nano_cpus"] = int(spec.cpus * 1_000_000_000)
    if spec.memory is not None:
        arguments["mem_limit"] = spec.memory
    if spec.network is not None:
        arguments["network"] = spec.network
    if spec.extra_hosts:
        arguments["extra_hosts"] = dict(spec.extra_hosts)
    return arguments


def _to_status(container: Any) -> ContainerStatus:
    state = container.attrs.get("State", {}) if isinstance(container.attrs, dict) else {}
    return ContainerStatus(
        id=str(container.id),
        name=str(container.name),
        state=str(container.status),
        labels=dict(container.labels or {}),
        started_at=_parse_time(state.get("StartedAt")),
        finished_at=_parse_time(state.get("FinishedAt")),
        exit_code=state.get("ExitCode") if isinstance(state.get("ExitCode"), int) else None,
    )


def _parse_time(value: object) -> datetime | None:
    """Docker reports an unset timestamp as a zero date rather than omitting it."""
    if not isinstance(value, str) or value.startswith("0001-01-01"):
        return None
    try:
        # Docker's nanosecond precision is finer than fromisoformat accepts before 3.11's
        # relaxations cover it; truncating to microseconds is lossless for our purposes.
        cleaned = value.replace("Z", "+00:00")
        if "." in cleaned:
            head, _, tail = cleaned.partition(".")
            fraction, sign, offset = _split_offset(tail)
            cleaned = f"{head}.{fraction[:6]}{sign}{offset}"
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _split_offset(tail: str) -> tuple[str, str, str]:
    for sign in ("+", "-"):
        if sign in tail:
            fraction, _, offset = tail.partition(sign)
            return fraction, sign, offset
    return tail, "", ""
