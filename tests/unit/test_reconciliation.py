"""The control loop, driven through every way reality can diverge from the plan.

Each of these would otherwise need a Docker daemon, a live repository and a well-timed
`kill -9`. Instead they are a fixture and a few lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest

from ghspot.application.commands.provision import (
    JIT_CONFIG_ENV,
    ProvisionRunner,
    RunnerTemplate,
)
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.reconciliation import PoolConfiguration, ReconciliationService
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.ports.backend import ContainerStatus
from ghspot.domain.ports.forge import ForgeRunner
from tests.fakes.adapters import (
    FakeBackend,
    FakeClock,
    FakeForge,
    InMemoryRunnerRepository,
    RecordingPublisher,
    SequentialIds,
)
from tests.unit.conftest import LABELS, REPO, T0, make_job, make_spec

TEMPLATE = RunnerTemplate(image="ghspot/runner:test", mount_docker_socket=True)


@dataclass
class Harness:
    """Everything wired together with fakes, plus the knobs a test needs."""

    service: ReconciliationService
    provision: ProvisionRunner
    forge: FakeForge
    backend: FakeBackend
    repository: InMemoryRunnerRepository
    clock: FakeClock
    events: RecordingPublisher
    spec: PoolSpec

    def runner_states(self) -> dict[str, RunnerState]:
        return {str(r.id): r.state for r in self.repository.saved.values()}


def build(*specs: PoolSpec) -> Harness:
    spec = specs[0] if specs else make_spec()
    clock = FakeClock(T0)
    forge = FakeForge()
    backend = FakeBackend(now=T0)
    repository = InMemoryRunnerRepository()
    events = RecordingPublisher()
    ids = SequentialIds()

    provision = ProvisionRunner(forge, backend, repository, clock, ids, events)
    retire = RetireRunner(forge, backend, repository, clock, events)
    service = ReconciliationService(
        pools=[PoolConfiguration(spec=s, template=TEMPLATE) for s in (specs or (spec,))],
        forge=forge,
        backend=backend,
        runners=repository,
        clock=clock,
        events=events,
        provision=provision,
        retire=retire,
    )
    return Harness(service, provision, forge, backend, repository, clock, events, spec)


@pytest.fixture
def harness() -> Harness:
    return build()


# ---------------------------------------------------------------- provisioning


async def test_provisioning_puts_only_the_jit_blob_in_the_container(harness: Harness) -> None:
    """The property the whole design rests on: no token ever reaches a container."""
    runner = await harness.provision(harness.spec, TEMPLATE)

    spec = harness.backend.created[0]
    assert spec.environment[JIT_CONFIG_ENV] == f"jit-{runner.github_runner_id}"
    assert not any("token" in key.casefold() or "pat" in key.casefold() for key in spec.environment)
    assert runner.state is RunnerState.STARTING
    assert runner.container_id is not None


async def test_provisioning_registers_before_it_starts_a_container(harness: Harness) -> None:
    """The ordering that makes the crash window recoverable rather than invisible."""
    await harness.provision(harness.spec, TEMPLATE)

    kinds = [type(event).__name__ for event in harness.events.events]
    assert kinds == ["RunnerRegistered", "RunnerStarted"]


async def test_a_container_that_will_not_start_takes_its_registration_with_it(
    harness: Harness,
) -> None:
    """Otherwise GitHub keeps an Offline runner that nothing will ever clear."""
    harness.backend.fail_on.add("create")

    with pytest.raises(Exception, match="fake backend failure"):
        await harness.provision(harness.spec, TEMPLATE)

    assert harness.forge.deleted == [100]
    assert harness.forge.runners == {}
    saved = next(iter(harness.repository.saved.values()))
    assert saved.state is RunnerState.FAILED
    assert saved.failure_reason is not None and "container creation failed" in saved.failure_reason


async def test_the_container_carries_the_labels_that_let_it_be_found_again(
    harness: Harness,
) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)

    labels = harness.backend.created[0].labels
    assert labels["io.ghspot.managed"] == "true"
    assert labels["io.ghspot.runner-id"] == str(runner.id)
    assert labels["io.ghspot.pool"] == "default"
    assert labels["io.ghspot.github-runner-id"] == str(runner.github_runner_id)


# ---------------------------------------------------------------- scaling


async def test_a_queued_job_gets_a_runner(harness: Harness) -> None:
    harness.forge.queued[REPO] = [make_job(1)]

    report = await harness.service.tick()

    assert report.launched == 1
    assert report.queued_jobs == 1
    assert len(harness.backend.containers) == 1


async def test_an_empty_queue_starts_nothing(harness: Harness) -> None:
    report = await harness.service.tick()

    assert report.launched == 0
    assert not report.changed_anything
    assert harness.backend.created == []


async def test_a_second_tick_does_not_double_up_on_the_same_job(harness: Harness) -> None:
    """The runner from tick one is capacity in tick two, even before it comes online."""
    harness.forge.queued[REPO] = [make_job(1)]

    await harness.service.tick()
    second = await harness.service.tick()

    assert second.launched == 0
    assert len(harness.backend.containers) == 1


# ---------------------------------------------------------------- observation


async def test_a_runner_that_connects_becomes_idle(harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.github_runner_id is not None
    harness.forge.bring_online(runner.github_runner_id)

    await harness.service.tick()

    assert harness.runner_states()[str(runner.id)] is RunnerState.IDLE


async def test_a_runner_that_takes_a_job_becomes_busy(harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.github_runner_id is not None
    harness.forge.bring_online(runner.github_runner_id, busy=True)

    await harness.service.tick()

    assert harness.runner_states()[str(runner.id)] is RunnerState.BUSY


async def test_a_finished_runner_is_cleaned_up_on_both_sides(harness: Harness) -> None:
    """The normal end of life: the runner de-registers itself, we remove the container."""
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.github_runner_id is not None
    harness.forge.deregister(runner.github_runner_id)

    await harness.service.tick()

    assert harness.runner_states()[str(runner.id)] is RunnerState.RETIRED
    assert harness.backend.containers == {}
    assert runner.container_id in harness.backend.removed


async def test_an_exited_container_is_retired(harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.container_id is not None
    harness.backend.exit_container(runner.container_id)

    await harness.service.tick()

    assert harness.runner_states()[str(runner.id)] is RunnerState.RETIRED


async def test_a_container_removed_behind_our_back_is_repaired(harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.container_id is not None and runner.github_runner_id is not None
    harness.forge.bring_online(runner.github_runner_id)
    harness.backend.vanish(runner.container_id)

    report = await harness.service.tick()

    assert harness.runner_states()[str(runner.id)] is RunnerState.RETIRED
    assert report.repaired == 1
    assert harness.forge.deleted == [runner.github_runner_id]


# ---------------------------------------------------------------- crash recovery


async def test_a_registration_left_by_a_failed_start_is_reaped(harness: Harness) -> None:
    """The container never started and even the compensating delete failed.

    No active record claims the registration any more, so the stray sweep can reap it at
    once — there is nothing it could be confused with.
    """
    harness.backend.fail_on.add("create")
    harness.forge.fail_on.add("delete_runner")
    with pytest.raises(Exception, match="fake backend failure"):
        await harness.provision(harness.spec, TEMPLATE)
    harness.forge.fail_on.clear()

    report = await harness.service.tick()

    assert harness.forge.deleted == [100]
    assert report.repaired == 1


async def test_a_registration_orphaned_by_a_hard_kill_is_reaped(harness: Harness) -> None:
    """SIGKILL landed between minting the config and starting the container.

    This is the runner that gets stuck `Offline` in the shell-script approach. The record
    survives as REGISTERED, so the stray sweep deliberately leaves it alone — from outside
    it is indistinguishable from a container that is merely slow to boot. The grace period
    is what tells them apart.
    """
    registration = await harness.forge.create_jit_registration(
        REPO, "ghspot-default-orphan", LABELS
    )
    runner = Runner(
        id=RunnerId("orphan"),
        name="ghspot-default-orphan",
        pool="default",
        repository=REPO,
        labels=LABELS,
        created_at=T0,
    )
    runner.register(registration.github_runner_id, at=T0)
    runner.pull_events()
    await harness.repository.save(runner)

    harness.clock.advance(minutes=1)
    assert (await harness.service.tick()).repaired == 0
    assert registration.github_runner_id in harness.forge.runners

    harness.clock.advance(minutes=10)
    report = await harness.service.tick()

    assert report.repaired == 1
    assert registration.github_runner_id not in harness.forge.runners
    assert harness.runner_states()["orphan"] is RunnerState.RETIRED


async def test_a_container_with_no_record_is_adopted(harness: Harness) -> None:
    """Losing the database must cost history, not the fleet."""
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.github_runner_id is not None
    harness.forge.bring_online(runner.github_runner_id, busy=True)
    harness.repository.saved.clear()

    report = await harness.service.tick()

    assert report.repaired == 1
    adopted = next(iter(harness.repository.saved.values()))
    assert adopted.id == runner.id
    assert adopted.github_runner_id == runner.github_runner_id
    assert adopted.state is RunnerState.BUSY
    assert report.launched == 0


async def test_a_stray_registration_of_ours_is_deleted(harness: Harness) -> None:
    harness.forge.runners[900] = ForgeRunner(
        id=900, name="ghspot-default-deadbeef", status="offline", busy=False, labels=LABELS
    )

    report = await harness.service.tick()

    assert harness.forge.deleted == [900]
    assert report.repaired == 1


@pytest.mark.parametrize(
    ("name", "status", "busy"),
    [
        ("my-laptop", "offline", False),  # not ours: no prefix
        ("ghspot-default-abc", "online", False),  # ours, but alive
        ("ghspot-default-abc", "online", True),  # ours, and working
    ],
)
async def test_runners_that_are_not_ours_to_delete_are_left_alone(
    harness: Harness, name: str, status: str, busy: bool
) -> None:
    """Deleting someone else's runner, or one mid-job, is worse than leaving debris."""
    harness.forge.runners[900] = ForgeRunner(
        id=900, name=name, status=status, busy=busy, labels=LABELS
    )

    await harness.service.tick()

    assert harness.forge.deleted == []


# ---------------------------------------------------------------- reaping


async def test_an_idle_runner_is_reaped_after_its_timeout(harness: Harness) -> None:
    spec = make_spec(idle_timeout=timedelta(minutes=10))
    harness_ = build(spec)
    runner = await harness_.provision(spec, TEMPLATE)
    assert runner.github_runner_id is not None
    harness_.forge.bring_online(runner.github_runner_id)

    await harness_.service.tick()
    harness_.clock.advance(minutes=30)
    report = await harness_.service.tick()

    assert report.retired == 1
    assert harness_.runner_states()[str(runner.id)] is RunnerState.RETIRED
    assert harness_.forge.deleted == [runner.github_runner_id]


async def test_a_hung_job_is_killed(harness: Harness) -> None:
    spec = make_spec(max_job_duration=timedelta(hours=1))
    harness_ = build(spec)
    runner = await harness_.provision(spec, TEMPLATE)
    assert runner.github_runner_id is not None and runner.container_id is not None
    harness_.forge.bring_online(runner.github_runner_id, busy=True)
    await harness_.service.tick()

    harness_.clock.advance(hours=2)
    report = await harness_.service.tick()

    assert report.terminated == 1
    assert runner.container_id in harness_.backend.killed


# ---------------------------------------------------------------- resilience


async def test_one_broken_pool_does_not_stop_the_others() -> None:
    """A repository that is unreachable must not starve every other pool on the host."""
    broken = make_spec(name="broken", repository=RepositoryTarget("someone", "gone"))
    working = make_spec(name="working")
    harness_ = build(broken, working)
    harness_.forge.unreachable.add(broken.repository)
    harness_.forge.queued[REPO] = [make_job(1)]

    report = await harness_.service.tick()

    assert report.errors and "broken" in report.errors[0]
    assert report.launched == 1


async def test_an_unreachable_container_backend_is_reported_not_raised(
    harness: Harness,
) -> None:
    harness.backend.fail_on.add("list_owned")

    report = await harness.service.tick()

    assert any("container backend unreachable" in error for error in report.errors)


async def test_the_report_describes_what_happened(harness: Harness) -> None:
    harness.forge.queued[REPO] = [make_job(1), make_job(2)]

    report = await harness.service.tick()

    assert report.started_at == T0
    assert report.changed_anything
    assert report.queued_jobs == 2
    assert any("queued job" in note for note in report.notes)


# ---------------------------------------------------------------- conformance


def test_the_fakes_still_satisfy_the_ports() -> None:
    """If a port grows a method, the fakes must grow it too or the tests lie."""
    from ghspot.domain.ports.backend import RunnerBackend
    from ghspot.domain.ports.forge import ForgeClient
    from ghspot.domain.ports.repository import RunnerRepository
    from ghspot.domain.ports.system import Clock, EventPublisher, IdGenerator

    forge: ForgeClient = FakeForge()
    backend: RunnerBackend = FakeBackend()
    repository: RunnerRepository = InMemoryRunnerRepository()
    clock: Clock = FakeClock(T0)
    ids: IdGenerator = SequentialIds()
    events: EventPublisher = RecordingPublisher()

    assert all(x is not None for x in (forge, backend, repository, clock, ids, events))


def test_container_status_reports_its_own_lifecycle() -> None:
    running = ContainerStatus(id="c1", name="n", state="running", labels={})
    exited = ContainerStatus(id="c2", name="n", state="exited", labels={})

    assert running.is_running and not running.has_exited
    assert exited.has_exited and not exited.is_running
