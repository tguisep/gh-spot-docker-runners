"""Bring one runner into existence.

Two systems have to be changed — GitHub and Docker — and there is no transaction across
them. The ordering below is chosen so that every point at which the daemon can die leaves
drift the reconciler can *see* and repair, rather than drift it cannot distinguish from
someone else's runner.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field

from ghspot.application.bookkeeping import labels_for
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.runner import Runner, runner_name_for
from ghspot.domain.ports.backend import ContainerSpec, RunnerBackend
from ghspot.domain.ports.forge import ForgeClient
from ghspot.domain.ports.repository import RunnerRepository
from ghspot.domain.ports.system import Clock, EventPublisher, IdGenerator

#: The environment variable the runner image reads its just-in-time config from.
JIT_CONFIG_ENV = "RUNNER_JIT_CONFIG"


@dataclass(frozen=True, slots=True)
class RunnerTemplate:
    """How a pool's containers are built.

    Deliberately separate from :class:`~ghspot.domain.model.pool.PoolSpec`: the domain has
    opinions about how many runners to keep, none about how much memory a container gets.
    """

    image: str
    cpus: float | None = None
    memory: str | None = None
    gpus: str | int | tuple[str, ...] | None = None
    mount_docker_socket: bool = False
    volumes: Mapping[str, str] = field(default_factory=dict)
    network: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    """Extra variables for the job environment. The just-in-time config is added separately
    and must not be set here."""


class ProvisionRunner:
    """Mint credentials, start a container, and keep the two tied together."""

    def __init__(
        self,
        forge: ForgeClient,
        backend: RunnerBackend,
        runners: RunnerRepository,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        host: str = "",
    ) -> None:
        self._forge = forge
        self._backend = backend
        self._runners = runners
        self._clock = clock
        self._ids = ids
        self._host = host
        self._events = events

    async def __call__(self, spec: PoolSpec, template: RunnerTemplate) -> Runner:
        now = self._clock.now()
        runner_id = self._ids.new_runner_id()

        runner = Runner(
            id=runner_id,
            name=runner_name_for(spec.name, runner_id, self._host),
            pool=spec.name,
            repository=spec.repository,
            labels=spec.labels,
            created_at=now,
        )
        # Persisted while still PENDING. If the process dies before the next line, the record
        # names a runner that exists nowhere else — harmless, and reaped as a stale pending.
        await self._runners.save(runner)

        registration = await self._forge.create_jit_registration(
            repository=spec.repository,
            name=runner.name,
            labels=spec.labels,
        )
        runner.register(registration.github_runner_id, at=self._clock.now())
        # Persisted *before* the container exists. This is the crash-critical window: GitHub
        # now lists a runner with no container behind it. The record is what lets the next
        # tick recognise it as ours and delete it, instead of leaving it Offline forever.
        await self._runners.save(runner)
        await self._flush(runner)

        try:
            container_id = await self._backend.create(
                self._container_spec(runner, template, registration.encoded_config)
            )
        except Exception as error:
            await self._abandon(runner, reason=f"container creation failed: {error}")
            raise

        runner.attach_container(container_id, at=self._clock.now())
        await self._runners.save(runner)
        await self._flush(runner)
        return runner

    def _container_spec(
        self, runner: Runner, template: RunnerTemplate, encoded_config: str
    ) -> ContainerSpec:
        return ContainerSpec(
            image=template.image,
            name=runner.name,
            labels=labels_for(runner),
            # The only credential the container ever sees, scoped to this one runner.
            environment={**template.environment, JIT_CONFIG_ENV: encoded_config},
            cpus=template.cpus,
            memory=template.memory,
            gpus=template.gpus,
            mount_docker_socket=template.mount_docker_socket,
            volumes=template.volumes,
            network=template.network,
        )

    async def _abandon(self, runner: Runner, reason: str) -> None:
        """Undo the registration for a runner whose container never started.

        Best-effort: if this fails too, the record still names the orphan and the next tick
        will try again.
        """
        if runner.github_runner_id is not None:
            with suppress(GhSpotError):
                await self._forge.delete_runner(runner.repository, runner.github_runner_id)
        runner.fail(at=self._clock.now(), reason=reason)
        await self._runners.save(runner)
        await self._flush(runner)

    async def _flush(self, runner: Runner) -> None:
        events = runner.pull_events()
        if events:
            await self._events.publish(events)
