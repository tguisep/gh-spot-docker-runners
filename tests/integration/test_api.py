"""The REST API, against a wired-up application backed by fakes.

What is checked is the contract a client would depend on: shapes, status codes, and that a
busy runner is not stopped by accident.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ghspot.application.commands.housekeeping import ReclaimHostSpace
from ghspot.application.commands.provision import ProvisionRunner, RunnerTemplate
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.reconciliation import PoolConfiguration, ReconciliationService
from ghspot.composition import Application
from ghspot.domain.model.runner import RunnerState
from ghspot.domain.ports.backend import ContainerUsage, PruneRequest
from ghspot.infrastructure.config.settings import DaemonSettings, GitHubSettings, Settings
from ghspot.interfaces.api.app import create_app
from tests.fakes.adapters import (
    FakeBackend,
    FakeClock,
    FakeForge,
    InMemoryRunnerRepository,
    RecordingPublisher,
    SequentialIds,
)
from tests.unit.conftest import REPO, T0, make_spec

TEMPLATE = RunnerTemplate(image="ghspot/runner:test")


class Harness:
    def __init__(self) -> None:
        self.spec = make_spec(max_runners=3)
        self.clock = FakeClock(T0)
        self.forge = FakeForge()
        self.backend = FakeBackend(now=T0)
        self.repository = InMemoryRunnerRepository()
        self.events = RecordingPublisher()

        provision = ProvisionRunner(
            self.forge, self.backend, self.repository, self.clock, SequentialIds(), self.events
        )
        retire = RetireRunner(self.forge, self.backend, self.repository, self.clock, self.events)
        # A fixed host, so the API's answers do not vary with whatever machine runs the
        # suite — and so a test can assert on the name the daemon reports for itself.
        settings = Settings(
            github=GitHubSettings(),
            daemon=DaemonSettings(poll_interval=timedelta(seconds=15), host="builders-01"),
            pools=(PoolConfiguration(spec=self.spec, template=TEMPLATE),),
        )
        self.settings = settings
        self.application = Application(
            settings=settings,
            forge=self.forge,  # type: ignore[arg-type]
            backend=self.backend,  # type: ignore[arg-type]
            runners=self.repository,  # type: ignore[arg-type]
            events=self.events,  # type: ignore[arg-type]
            reconciler=ReconciliationService(
                pools=settings.pools,
                forge=self.forge,
                backend=self.backend,
                runners=self.repository,
                clock=self.clock,
                events=self.events,
                provision=provision,
                retire=retire,
            ),
            housekeeping=ReclaimHostSpace(
                backend=self.backend,
                clock=self.clock,
                every=timedelta(hours=1),
                request=PruneRequest(),
            ),
            clock=self.clock,  # type: ignore[arg-type]
            provision=provision,
            retire=retire,
        )
        self.provision = provision


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def client(harness: Harness) -> Iterator[TestClient]:
    with TestClient(create_app(harness.application)) as test_client:
        yield test_client


# ---------------------------------------------------------------- status


def test_health_reports_docker_reachability(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["docker"] is True
    assert body["pools"] == 1


def test_health_is_degraded_when_docker_is_unreachable(
    client: TestClient, harness: Harness
) -> None:
    """A daemon that is up but cannot reach Docker is not healthy in any useful sense."""
    harness.backend.fail_on.add("ping")

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["docker"] is False


def test_reconcile_runs_a_tick_on_demand(client: TestClient, harness: Harness) -> None:
    from tests.unit.conftest import make_job

    harness.forge.queued[REPO] = [make_job(1)]

    body = client.post("/reconcile").json()

    assert body["launched"] == 1
    assert body["queued_jobs"] == 1
    assert body["errors"] == []


# ---------------------------------------------------------------- pools


def test_pools_are_listed_with_their_declared_shape(client: TestClient) -> None:
    body = client.get("/pools").json()

    assert len(body) == 1
    assert body[0]["name"] == "default"
    assert body[0]["max_runners"] == 3
    assert body[0]["headroom"] == 3


def test_an_unknown_pool_is_a_404(client: TestClient) -> None:
    response = client.get("/pools/ghost")

    assert response.status_code == 404
    assert "no pool named" in response.json()["detail"]


async def test_a_pool_reports_the_runners_in_it(client: TestClient, harness: Harness) -> None:
    await harness.provision(harness.spec, TEMPLATE)

    body = client.get("/pools/default").json()

    assert body["active"] == 1
    assert body["starting"] == 1
    assert len(body["runners"]) == 1


# ---------------------------------------------------------------- runners


def test_no_runners_is_an_empty_list_not_an_error(client: TestClient) -> None:
    response = client.get("/runners")

    assert response.status_code == 200
    assert response.json() == []


async def test_a_runner_can_be_fetched_by_id_name_or_container(
    client: TestClient, harness: Harness
) -> None:
    """Operators paste whichever column they have to hand."""
    runner = await harness.provision(harness.spec, TEMPLATE)

    for reference in (str(runner.id), runner.name, str(runner.container_id)):
        body = client.get(f"/runners/{reference}").json()
        assert body["id"] == str(runner.id)
        assert body["state"] == "starting"


def test_an_unknown_runner_is_a_404(client: TestClient) -> None:
    response = client.get("/runners/nobody")

    assert response.status_code == 404
    assert "no runner matching" in response.json()["detail"]


async def test_logs_are_returned_for_a_runner(client: TestClient, harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)

    body = client.get(f"/runners/{runner.id}/logs?tail=50").json()

    assert body["runner_id"] == str(runner.id)
    assert runner.container_id in body["lines"]


async def test_a_runner_can_be_stopped(client: TestClient, harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)

    body = client.request("DELETE", f"/runners/{runner.id}").json()

    assert body["state"] == "retired"
    assert harness.backend.containers == {}
    assert harness.forge.deleted == [runner.github_runner_id]


async def test_stopping_a_busy_runner_is_refused_with_409(
    client: TestClient, harness: Harness
) -> None:
    """Stopping it fails somebody's build, so the caller has to say they mean it."""
    runner = await harness.provision(harness.spec, TEMPLATE)
    runner.mark_online(at=T0)
    runner.assign_job(99, at=T0)
    await harness.repository.save(runner)

    response = client.request("DELETE", f"/runners/{runner.id}")

    assert response.status_code == 409
    assert "running job 99" in response.json()["detail"]
    assert harness.backend.containers != {}


async def test_forcing_stops_a_busy_runner(client: TestClient, harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    runner.mark_online(at=T0)
    runner.assign_job(99, at=T0)
    await harness.repository.save(runner)

    response = client.request("DELETE", f"/runners/{runner.id}?force=true")

    assert response.status_code == 200
    assert runner.container_id in harness.backend.killed


async def test_terminal_runners_are_hidden_unless_asked_for(
    client: TestClient, harness: Harness
) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    runner.retire(at=T0, reason="done")
    await harness.repository.save(runner)

    assert client.get("/runners").json() == []
    assert len(client.get("/runners?include_terminal=true").json()) == 1


# ---------------------------------------------------------------- contract


def test_the_openapi_document_is_generated(client: TestClient) -> None:
    """It is the API's documentation; if it stops generating, nobody can write a client."""
    document = client.get("/openapi.json").json()

    assert document["info"]["title"] == "ghspot"
    for path in ("/health", "/pools", "/runners", "/reconcile"):
        assert path in document["paths"]


def test_the_docs_page_warns_that_there_is_no_authentication(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "no authentication" in document["info"]["description"].lower()


async def test_a_runner_response_rounds_its_durations(client: TestClient, harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    harness.clock.advance(seconds=90.456)

    body = client.get(f"/runners/{runner.id}").json()

    assert body["age_seconds"] == 90.5
    assert body["state"] == RunnerState.STARTING.value


# ---------------------------------------------------------------- stats


def test_stats_reports_what_the_fleet_did(client: TestClient, harness: Harness) -> None:
    """The endpoint reads the same log the CLI does, and sends derived values with it."""
    from ghspot.domain.model import events as domain_events

    written = [
        domain_events.RunnerRegistered(
            occurred_at=T0,
            runner_id="r1",
            runner_name="ghspot-default-r1",
            github_runner_id=1,
            repository=REPO,
            pool="default",
        ),
        domain_events.RunnerTookJob(
            occurred_at=T0 + timedelta(minutes=1), runner_id="r1", job_id=7
        ),
        domain_events.RunnerRetired(
            occurred_at=T0 + timedelta(minutes=11), runner_id="r1", reason="job finished"
        ),
    ]
    harness.events.events.extend(written)

    body = client.get("/stats").json()

    assert body["events_read"] == 3
    assert body["total"]["runners"] == 1
    assert body["total"]["jobs"] == 1
    assert body["total"]["busy_seconds"] == 600.0
    assert body["total"]["mean_wait_seconds"] == 60.0
    assert [row["key"] for row in body["by_repository"]] == [str(REPO)]
    assert [row["key"] for row in body["by_pool"]] == ["default"]
    assert body["failures"] == []


def test_stats_honours_a_window(client: TestClient, harness: Harness) -> None:
    """An event older than the window is outside the report, not merely unsorted."""
    from ghspot.domain.model import events as domain_events

    harness.events.events.append(
        domain_events.RunnerRegistered(
            occurred_at=T0 - timedelta(days=30),
            runner_id="old",
            runner_name="ghspot-default-old",
            github_runner_id=1,
            repository=REPO,
            pool="default",
        )
    )

    assert client.get("/stats").json()["events_read"] == 1
    assert client.get("/stats", params={"since_seconds": 3600}).json()["events_read"] == 0


def test_a_negative_window_is_refused(client: TestClient) -> None:
    assert client.get("/stats", params={"since_seconds": -1}).status_code == 422


# ---------------------------------------------------------------- usage


async def test_usage_is_not_sampled_unless_asked(client: TestClient, harness: Harness) -> None:
    """A list read constantly must not put a `docker stats` call behind every request."""
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.container_id is not None
    harness.backend.samples[runner.container_id] = ContainerUsage(
        container_id=runner.container_id, cpu_percent=99.0, memory_bytes=1
    )

    body = client.get("/runners").json()

    assert body[0]["cpu_percent"] is None
    assert body[0]["memory_bytes"] is None


async def test_usage_is_attached_when_asked(client: TestClient, harness: Harness) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    assert runner.container_id is not None
    harness.backend.samples[runner.container_id] = ContainerUsage(
        container_id=runner.container_id,
        cpu_percent=137.5,
        memory_bytes=300_000_000,
        memory_limit_bytes=2_000_000_000,
    )

    body = client.get("/runners", params={"usage": "true"}).json()

    assert body[0]["cpu_percent"] == 137.5
    assert body[0]["memory_bytes"] == 300_000_000
    assert body[0]["memory_percent"] == 15.0


async def test_a_runner_with_no_sample_keeps_nulls(client: TestClient, harness: Harness) -> None:
    """A container that went between the listing and the sample reads as unmeasured, not as
    idle. The fake reports nothing for it, which is what the real backend does."""
    await harness.provision(harness.spec, TEMPLATE)

    body = client.get("/runners", params={"usage": "true"}).json()

    assert body[0]["cpu_percent"] is None
    assert body[0]["memory_percent"] is None


# ---------------------------------------------------------------- job logs


async def test_a_running_job_has_no_forge_log_yet(client: TestClient, harness: Harness) -> None:
    """GitHub writes the log when the job finishes, so a job in progress has none. That is
    the normal state, not an error — and it must not read as an empty log."""
    runner = await harness.provision(harness.spec, TEMPLATE)
    runner.mark_online(at=T0)
    runner.assign_job(job_id=4242, at=T0)
    await harness.repository.save(runner)

    body = client.get(f"/runners/{runner.id}/job-logs").json()

    assert body["job_id"] == 4242
    assert body["available"] is False
    assert body["lines"] == ""


async def test_the_forge_log_appears_once_the_job_is_done(
    client: TestClient, harness: Harness
) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)
    runner.mark_online(at=T0)
    runner.assign_job(job_id=4242, at=T0)
    await harness.repository.save(runner)
    harness.forge.job_output[4242] = "2026-08-28T14:24:17Z Run actions/checkout@v4"

    body = client.get(f"/runners/{runner.id}/job-logs").json()

    assert body["available"] is True
    assert "actions/checkout" in body["lines"]


async def test_a_runner_with_no_job_is_answered_plainly(
    client: TestClient, harness: Harness
) -> None:
    runner = await harness.provision(harness.spec, TEMPLATE)

    body = client.get(f"/runners/{runner.id}/job-logs").json()

    assert body["job_id"] is None
    assert body["available"] is False


# ---------------------------------------------------------------- first run


def test_health_says_when_nobody_has_finished_configuring(client: TestClient) -> None:
    """A fresh install is a daemon that is up, answering, and pointing at nothing useful.
    The dashboard needs to be able to tell that from a working one."""
    body = client.get("/health").json()

    assert body["configured"] is False
    assert body["setup_reason"]


def test_being_unconfigured_is_not_the_same_as_being_unhealthy(client: TestClient) -> None:
    """`status` is about whether the daemon can operate. Folding the two together would make
    every fresh install look like a broken one to anything watching this endpoint."""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["configured"] is False


def test_health_and_stats_both_name_the_host(client: TestClient) -> None:
    """Several hosts can serve one repository, and each daemon answers only for its own. A
    client polling three of them cannot otherwise tell their answers apart."""
    assert client.get("/health").json()["host"] == "builders-01"
    assert client.get("/stats").json()["host"] == "builders-01"
