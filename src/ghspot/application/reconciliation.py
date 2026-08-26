"""The control loop.

One pass observes what actually exists — containers in Docker, runners on GitHub, records in
the store — reconstructs the fleet from that, and moves it toward what was declared. It is the
whole design in one method: nothing is remembered that cannot be re-derived, so a crash costs
at most one tick.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ghspot.application import bookkeeping
from ghspot.application.commands.provision import ProvisionRunner, RunnerTemplate
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.dto import TickReport
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.pool import PoolSpec, RunnerPool
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.policy.scaling import plan_scaling
from ghspot.domain.ports.backend import ContainerStatus, RunnerBackend
from ghspot.domain.ports.forge import ForgeClient, ForgeRunner
from ghspot.domain.ports.repository import RunnerRepository
from ghspot.domain.ports.system import Clock, EventPublisher

#: How long a runner may sit registered-but-containerless before it is treated as the debris
#: of a crash. Generous enough to never race a slow container start.
REGISTRATION_GRACE = timedelta(minutes=5)

#: Runner names this daemon mints all start here, so a foreign runner in the same repository
#: is never mistaken for our orphan and deleted.
NAME_PREFIX = "ghspot-"


@dataclass(frozen=True, slots=True)
class PoolConfiguration:
    """One pool as configured: the declared shape plus how to build its containers."""

    spec: PoolSpec
    template: RunnerTemplate


class ReconciliationService:
    def __init__(
        self,
        pools: Sequence[PoolConfiguration],
        forge: ForgeClient,
        backend: RunnerBackend,
        runners: RunnerRepository,
        clock: Clock,
        events: EventPublisher,
        provision: ProvisionRunner,
        retire: RetireRunner,
    ) -> None:
        self._pools = list(pools)
        self._forge = forge
        self._backend = backend
        self._runners = runners
        self._clock = clock
        self._events = events
        self._provision = provision
        self._retire_runner = retire

    async def tick(self) -> TickReport:
        """One reconciliation pass over every configured pool.

        Failures are collected rather than raised: one unreachable repository must not stop
        the other pools from being reconciled.
        """
        started = self._clock.now()
        launched = retired = terminated = repaired = 0
        queued_total = 0
        errors: list[str] = []
        notes: list[str] = []

        containers = await self._owned_containers(errors)
        demand_by_repository: dict[RepositoryTarget, Sequence[QueuedJob]] = {}

        for configuration in self._pools:
            spec = configuration.spec
            try:
                demand = await self._demand_for(spec.repository, demand_by_repository)
                pool, repairs = await self._observe(configuration, containers)
                repaired += repairs

                servable = [job for job in demand if spec.can_serve(job)]
                queued_total += len(servable)

                plan = plan_scaling(pool, demand, self._clock.now())
                notes.extend(f"[{spec.name}] {reason}" for reason in plan.reasons)

                for runner_id in plan.terminate:
                    if await self._retire_by_id(pool, runner_id, "job overran", force=True):
                        terminated += 1

                for runner_id in plan.retire:
                    if await self._retire_by_id(pool, runner_id, "idle timeout", force=False):
                        retired += 1

                for _ in range(plan.launch):
                    await self._provision(spec, configuration.template)
                    launched += 1

            except GhSpotError as error:
                errors.append(f"[{spec.name}] {error}")

        return TickReport(
            started_at=started,
            duration_seconds=(self._clock.now() - started).total_seconds(),
            launched=launched,
            retired=retired,
            terminated=terminated,
            repaired=repaired,
            queued_jobs=queued_total,
            errors=errors,
            notes=notes,
        )

    # -- observation ----------------------------------------------------------------

    async def _observe(
        self,
        configuration: PoolConfiguration,
        containers: Mapping[RunnerId, ContainerStatus],
    ) -> tuple[RunnerPool, int]:
        """Rebuild one pool from reality, repairing drift as it is found.

        Returns the reconstructed pool and how many divergences were corrected.
        """
        spec = configuration.spec
        now = self._clock.now()
        repaired = 0

        records = {
            runner.id: runner
            for runner in await self._runners.list_for_pool(spec.name)
            if not runner.is_terminal
        }
        forge_runners = {
            runner.id: runner for runner in await self._forge.list_runners(spec.repository)
        }
        pool_containers = {
            runner_id: status
            for runner_id, status in containers.items()
            if bookkeeping.pool_from(status.labels) == spec.name
        }

        # A container we own with no record behind it: the store was lost or rolled back.
        # Adopting it is what makes the database a projection rather than the truth.
        for runner_id, status in pool_containers.items():
            if runner_id not in records:
                adopted = self._adopt(spec, runner_id, status, now)
                await self._runners.save(adopted)
                records[runner_id] = adopted
                repaired += 1

        pool = RunnerPool(spec=spec)
        for runner in records.values():
            container = pool_containers.get(runner.id)
            listed = (
                forge_runners.get(runner.github_runner_id)
                if runner.github_runner_id is not None
                else None
            )
            if await self._settle(runner, container, listed, now):
                repaired += 1
            if not runner.is_terminal:
                pool.admit(runner)

        repaired += await self._delete_stray_registrations(spec, forge_runners, records)
        return pool, repaired

    async def _settle(
        self,
        runner: Runner,
        status: ContainerStatus | None,
        listed: ForgeRunner | None,
        now: datetime,
    ) -> bool:
        """Move one record onto what was actually observed.

        Returns whether this counted as repairing drift, as opposed to routine progress.
        """
        # Registered, no container, and past the grace period: the daemon died in the window
        # between minting the config and starting the container. GitHub is holding a runner
        # that will never come online — exactly the stuck-Offline entry the shell script had
        # to be run by hand to clear.
        if runner.state is RunnerState.REGISTERED and status is None:
            if runner.time_in_state(now) > REGISTRATION_GRACE.total_seconds():
                await self._retire(runner, "registered but never started")
                return True
            return False

        if runner.state is RunnerState.PENDING:
            if runner.time_in_state(now) > REGISTRATION_GRACE.total_seconds():
                await self._retire(runner, "never registered")
                return True
            return False

        if status is None:
            # The container is gone. If GitHub has also dropped the runner, the job finished
            # normally; otherwise something removed the container behind our back.
            await self._retire(runner, "container gone" if listed else "job finished")
            return listed is not None

        if status.has_exited:
            await self._retire(runner, "container exited")
            return False

        if listed is None:
            # Running container, but GitHub no longer lists the runner: a just-in-time runner
            # de-registers itself the moment its job ends, so this is the normal end of life
            # seen a moment before the process exits.
            await self._retire(runner, "de-registered by the forge")
            return False

        if listed.busy:
            runner.assign_job(None, at=now)
        elif listed.is_online:
            runner.mark_online(at=now)

        await self._runners.save(runner)
        await self._flush(runner)
        return False

    async def _delete_stray_registrations(
        self,
        spec: PoolSpec,
        forge_runners: Mapping[int, ForgeRunner],
        records: Mapping[RunnerId, Runner],
    ) -> int:
        """Delete runners GitHub lists that this daemon minted but no longer tracks.

        Scoped to the name prefix and to offline runners, so a runner someone registered by
        hand — or one that is mid-job — is never touched.
        """
        known = {runner.github_runner_id for runner in records.values()}
        repaired = 0

        for listed in forge_runners.values():
            if listed.id in known or not listed.name.startswith(NAME_PREFIX):
                continue
            if listed.is_online or listed.busy:
                continue
            try:
                await self._forge.delete_runner(spec.repository, listed.id)
                repaired += 1
            except GhSpotError:
                pass

        return repaired

    def _adopt(
        self,
        spec: PoolSpec,
        runner_id: RunnerId,
        status: ContainerStatus,
        now: datetime,
    ) -> Runner:
        """Rebuild a record from what the container itself carries."""
        return Runner(
            id=runner_id,
            name=status.name,
            pool=spec.name,
            repository=spec.repository,
            labels=spec.labels,
            created_at=bookkeeping.created_at_from(status.labels) or now,
            state=RunnerState.IDLE if status.is_running else RunnerState.FAILED,
            state_changed_at=status.started_at or now,
            github_runner_id=bookkeeping.github_runner_id_from(status.labels),
            container_id=status.id,
        )

    # -- helpers --------------------------------------------------------------------

    async def _owned_containers(self, errors: list[str]) -> dict[RunnerId, ContainerStatus]:
        try:
            listed = await self._backend.list_owned(bookkeeping.OWNED_SELECTOR)
        except GhSpotError as error:
            errors.append(f"container backend unreachable: {error}")
            return {}

        found: dict[RunnerId, ContainerStatus] = {}
        for status in listed:
            runner_id = bookkeeping.runner_id_from(status.labels)
            if runner_id is not None:
                found[runner_id] = status
        return found

    async def _demand_for(
        self,
        repository: RepositoryTarget,
        cache: dict[RepositoryTarget, Sequence[QueuedJob]],
    ) -> Sequence[QueuedJob]:
        """Queued jobs for a repository, fetched once however many pools serve it."""
        if repository not in cache:
            cache[repository] = await self._forge.list_queued_jobs(repository)
        return cache[repository]

    async def _retire_by_id(
        self, pool: RunnerPool, runner_id: RunnerId, reason: str, *, force: bool
    ) -> bool:
        runner = pool.get(runner_id)
        if runner is None:
            return False
        await self._retire(runner, reason, force=force)
        pool.discard(runner_id)
        return True

    async def _retire(self, runner: Runner, reason: str, *, force: bool = False) -> None:
        await self._retire_runner(runner, reason, force=force)

    async def _flush(self, runner: Runner) -> None:
        events = runner.pull_events()
        if events:
            await self._events.publish(events)
