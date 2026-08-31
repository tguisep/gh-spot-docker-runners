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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.types import DeviceRequest

from ghspot.domain.errors import BackendError, ImageNotFoundError
from ghspot.domain.ports.backend import (
    PROTECTED_IMAGE_LABEL,
    ContainerSpec,
    ContainerStatus,
    ContainerUsage,
    HostLoad,
    PruneReport,
    PruneRequest,
)
from ghspot.paths import build_command

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
                    f"Build it with: {build_command(spec.image.rpartition(':')[2])}"
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

    async def host_load(self) -> HostLoad:
        """What the machine looks like right now.

        Two sources, because neither is enough alone. The Engine knows the shape of the host
        it manages — cores, total memory, how many containers are up — but not how hard it is
        working. `/proc` knows that, and is where the daemon already runs.

        Nothing here raises. A load reading that fails must degrade the decision to the
        configured ceilings, not stop the fleet: the whole point of the probe is to be
        careful, and a careful thing that breaks the daemon is worse than no probe at all.
        """

        def run() -> HostLoad:
            cores: int | None = None
            total: int | None = None
            containers: int | None = None
            try:
                info = self._client.info()
            except (APIError, DockerException):
                info = {}
            if isinstance(info, dict):
                cores = _positive(info.get("NCPU"))
                total = _positive(info.get("MemTotal"))
                containers = _positive(info.get("ContainersRunning"), allow_zero=True)

            used = _memory_used(total)
            return HostLoad(
                cpu_percent=_cpu_percent(cores),
                memory_used_bytes=used,
                memory_total_bytes=total,
                containers_running=containers,
                cores=cores,
            )

        return await asyncio.to_thread(run)

    async def usage(self, container_ids: Sequence[str]) -> Mapping[str, ContainerUsage]:
        """Sample CPU and memory for each container.

        One `stats(stream=False)` call per container, run together on the executor: the
        Engine answers each in a hundred milliseconds or so, and sampling a fleet serially
        would make the whole listing feel broken.

        A container that has gone between the listing and this call is skipped rather than
        raising: with ephemeral runners that is the expected case, not a fault.
        """
        if not container_ids:
            return {}

        def sample(container_id: str) -> ContainerUsage | None:
            try:
                raw = self._client.containers.get(container_id).stats(stream=False)
            except (NotFound, APIError, DockerException):
                return None
            return _usage_from_stats(container_id, raw)

        async def one(container_id: str) -> ContainerUsage | None:
            return await asyncio.to_thread(sample, container_id)

        sampled = await asyncio.gather(*(one(cid) for cid in container_ids))
        return {usage.container_id: usage for usage in sampled if usage is not None}

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

    async def prune(self, request: PruneRequest) -> PruneReport:
        """Reclaim what jobs left behind.

        Jobs reach the host's daemon through the mounted socket, so what they build, pull and
        create is the host's to clean up. Docker's own prune endpoints do the work; the care
        here is entirely in what is *excluded*.
        """
        if request.is_noop:
            return PruneReport()

        def run() -> PruneReport:
            containers = images = volumes = 0
            reclaimed = 0
            cache_bytes = 0
            errors: list[str] = []

            def attempt(what: str, action: Any) -> Any:
                try:
                    return action()
                except (APIError, DockerException) as error:
                    errors.append(f"{what}: {error}")
                    return None

            if request.containers_older_than is not None:
                # Stopped only — Docker's container prune never touches a running one.
                result = attempt(
                    "containers",
                    lambda: self._client.containers.prune(
                        filters={"until": _duration(request.containers_older_than)}
                    ),
                )
                if result:
                    containers = len(result.get("ContainersDeleted") or [])
                    reclaimed += int(result.get("SpaceReclaimed") or 0)

            if request.images_older_than is not None:
                # `dangling: False` widens this to unused *tagged* images, which is where a
                # job's build output lives. The label filter is what stops it reclaiming the
                # runner images themselves.
                label_key, _, label_value = PROTECTED_IMAGE_LABEL.partition("=")
                result = attempt(
                    "images",
                    lambda: self._client.images.prune(
                        filters={
                            "until": _duration(request.images_older_than),
                            "dangling": False,
                            "label!": f"{label_key}={label_value}",
                        }
                    ),
                )
                if result:
                    images = len(result.get("ImagesDeleted") or [])
                    reclaimed += int(result.get("SpaceReclaimed") or 0)

            if request.volumes:
                # Anonymous volumes only. A named volume is something somebody chose to
                # create, including any cache a pool is configured with.
                result = attempt(
                    "volumes",
                    lambda: self._client.volumes.prune(filters={"all": "false"}),
                )
                if result:
                    volumes = len(result.get("VolumesDeleted") or [])
                    reclaimed += int(result.get("SpaceReclaimed") or 0)

            if request.build_cache_older_than is not None or request.keep_build_cache_bytes:
                arguments: dict[str, Any] = {}
                if request.build_cache_older_than is not None:
                    arguments["filters"] = {"until": _duration(request.build_cache_older_than)}
                if request.keep_build_cache_bytes is not None:
                    arguments["keep_storage"] = request.keep_build_cache_bytes
                result = attempt("build cache", lambda: self._client.api.prune_builds(**arguments))
                if result:
                    cache_bytes = int(result.get("SpaceReclaimed") or 0)
                    reclaimed += cache_bytes

            return PruneReport(
                containers=containers,
                images=images,
                volumes=volumes,
                build_cache_bytes=cache_bytes,
                reclaimed_bytes=reclaimed,
                errors=tuple(errors),
            )

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
    request = _gpu_request(spec.gpus)
    if request is not None:
        arguments["device_requests"] = [request]
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


def _duration(delta: timedelta) -> str:
    """Docker's `until` filter wants a Go duration, and rejects fractional hours."""
    seconds = max(1, int(delta.total_seconds()))
    return f"{seconds}s"


PROC_LOADAVG = Path("/proc/loadavg")
PROC_MEMINFO = Path("/proc/meminfo")


def _cpu_percent(cores: int | None) -> float | None:
    """Load average over one minute, as a percentage of the machine's cores.

    Load average rather than an instantaneous sample: it already covers everything on the
    box, not only containers, and it is the number that says whether work is *queueing* for
    the CPU. An instantaneous reading would let a burst between two ticks go unnoticed.

    It counts uninterruptible sleep too, so heavy disk IO shows here as load. For deciding
    whether to add more work to a struggling machine, that is a feature.
    """
    try:
        first = PROC_LOADAVG.read_text(encoding="utf-8").split()[0]
        load = float(first)
    except (OSError, ValueError, IndexError):
        return None
    if not cores or cores <= 0:
        return None
    return round(100.0 * load / cores, 1)


def _memory_used(total: int | None) -> int | None:
    """Memory in use, taking MemAvailable as the kernel's own answer to "what is free".

    Not `MemTotal - MemFree`: that counts the page cache as used, and a Linux box doing any
    work at all looks 95% full by that measure. MemAvailable is the kernel's estimate of what
    a new process could actually get.
    """
    try:
        lines = PROC_MEMINFO.read_text(encoding="utf-8").splitlines()
        fields = {
            parts[0].rstrip(":"): parts[1]
            for parts in (line.split() for line in lines)
            if len(parts) >= 2
        }
        available = int(fields["MemAvailable"]) * 1024
        readable_total = int(fields["MemTotal"]) * 1024
    except (OSError, KeyError, ValueError):
        return None

    return max(0, (total or readable_total) - available)


def _positive(value: Any, *, allow_zero: bool = False) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < 0 or (value == 0 and not allow_zero):
        return None
    return value


def _usage_from_stats(container_id: str, raw: Any) -> ContainerUsage | None:
    """Turn one `docker stats` sample into a usage reading.

    CPU is a rate and the Engine reports counters, so it has to be derived from the two
    snapshots every sample carries:

        cpu% = (container delta / system delta) * cores * 100

    A first sample on a just-started container has identical snapshots, making the system
    delta zero. That is reported as 0%, not as a division error.

    Memory subtracts the page cache. Without that a job that merely read a large file looks
    like a job that leaked, because Linux charges cache to the cgroup until something else
    needs the pages.
    """
    if not isinstance(raw, dict):
        return None

    cpu = raw.get("cpu_stats") or {}
    previous = raw.get("precpu_stats") or {}
    memory = raw.get("memory_stats") or {}

    used = _number(cpu.get("cpu_usage", {}).get("total_usage"))
    used_before = _number(previous.get("cpu_usage", {}).get("total_usage"))
    cpu_delta = used - used_before
    system_delta = _number(cpu.get("system_cpu_usage")) - _number(previous.get("system_cpu_usage"))
    cores = _number(cpu.get("online_cpus")) or len(
        cpu.get("cpu_usage", {}).get("percpu_usage") or []
    )

    percent = 0.0
    if cpu_delta > 0 and system_delta > 0:
        percent = (cpu_delta / system_delta) * max(1.0, cores) * 100.0

    cache = _number((memory.get("stats") or {}).get("inactive_file"))
    resident = max(0.0, _number(memory.get("usage")) - cache)
    limit = _number(memory.get("limit"))

    return ContainerUsage(
        container_id=container_id,
        cpu_percent=round(percent, 1),
        memory_bytes=int(resident),
        memory_limit_bytes=int(limit) if limit > 0 else None,
    )


def _number(value: Any) -> float:
    """Docker omits a field rather than sending zero, so a missing one must read as zero."""
    return float(value) if isinstance(value, int | float) else 0.0


def _gpu_request(gpus: str | int | tuple[str, ...] | None) -> DeviceRequest | None:
    """Translate a pool's ``gpus`` setting into what the Engine expects.

    The same thing `docker run --gpus` produces. Reaching the GPU still requires the NVIDIA
    Container Toolkit on the host: without it the Engine rejects the request outright, which
    is better than a container starting with no GPU and a job failing later for reasons that
    point nowhere near the configuration.
    """
    if gpus is None:
        return None

    capabilities = [["gpu"]]

    if isinstance(gpus, tuple):
        if not gpus:
            return None
        return DeviceRequest(device_ids=list(gpus), capabilities=capabilities)

    if isinstance(gpus, int):
        # -1 is the Engine's spelling of "every GPU on the host".
        return DeviceRequest(count=gpus, capabilities=capabilities)

    if gpus.strip().casefold() == "all":
        return DeviceRequest(count=-1, capabilities=capabilities)

    raise BackendError(
        f'{gpus!r} is not a GPU selection. Use "all", a count, or a list of device ids.'
    )
