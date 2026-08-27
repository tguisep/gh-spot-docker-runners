"""The Docker adapter against a real daemon.

Marked ``docker`` so the default suite stays hermetic. These use a tiny image rather than the
runner image: what is under test is the adapter, not the runner.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from ghspot.application.bookkeeping import MANAGED, OWNED_SELECTOR, RUNNER_ID
from ghspot.domain.errors import ImageNotFoundError
from ghspot.domain.ports.backend import ContainerSpec
from ghspot.infrastructure.docker.backend import DockerRunnerBackend

pytestmark = pytest.mark.docker

IMAGE = "busybox:latest"


@pytest.fixture
async def backend() -> DockerRunnerBackend:
    instance = DockerRunnerBackend()
    if not await instance.image_exists(IMAGE):
        pytest.skip(f"{IMAGE} is not present; pull it to run these tests")
    return instance


@pytest.fixture
async def cleanup(backend: DockerRunnerBackend) -> AsyncIterator[list[str]]:
    created: list[str] = []
    yield created
    for container_id in created:
        await backend.remove(container_id)


def make_spec(runner_id: str, command_holds: bool = True) -> ContainerSpec:
    return ContainerSpec(
        image=IMAGE,
        name=f"ghspot-test-{runner_id}",
        labels={MANAGED: "true", RUNNER_ID: runner_id, "io.ghspot.pool": "test"},
        environment={"RUNNER_JIT_CONFIG": "not-a-real-blob"},
    )


async def test_a_container_can_be_created_inspected_and_removed(
    backend: DockerRunnerBackend, cleanup: list[str]
) -> None:
    runner_id = uuid.uuid4().hex[:12]
    container_id = await backend.create(make_spec(runner_id))
    cleanup.append(container_id)

    status = await backend.inspect(container_id)

    assert status is not None
    assert status.labels[RUNNER_ID] == runner_id
    assert status.name == f"ghspot-test-{runner_id}"

    await backend.remove(container_id)
    assert await backend.inspect(container_id) is None


async def test_owned_containers_are_found_by_label(
    backend: DockerRunnerBackend, cleanup: list[str]
) -> None:
    """This is how the daemon rediscovers its fleet after a restart."""
    runner_id = uuid.uuid4().hex[:12]
    container_id = await backend.create(make_spec(runner_id))
    cleanup.append(container_id)

    owned = await backend.list_owned(OWNED_SELECTOR)

    assert container_id in {status.id for status in owned}


async def test_lifecycle_operations_on_a_missing_container_are_quiet(
    backend: DockerRunnerBackend,
) -> None:
    """The reconciler calls these on anything it suspects is stale."""
    ghost = "0" * 64

    await backend.stop(ghost)
    await backend.kill(ghost)
    await backend.remove(ghost)

    assert await backend.inspect(ghost) is None
    assert await backend.logs(ghost) == ""


async def test_stopping_a_container_lets_it_exit(
    backend: DockerRunnerBackend, cleanup: list[str]
) -> None:
    container_id = await backend.create(make_spec(uuid.uuid4().hex[:12]))
    cleanup.append(container_id)

    await backend.stop(container_id, timeout_seconds=2)

    status = await backend.inspect(container_id)
    assert status is not None and status.has_exited


async def test_a_missing_image_says_how_to_build_it(backend: DockerRunnerBackend) -> None:
    spec = ContainerSpec(
        image="ghspot/does-not-exist:ever",
        name=f"ghspot-test-{uuid.uuid4().hex[:12]}",
        labels={MANAGED: "true"},
    )

    with pytest.raises(ImageNotFoundError, match=r"build\.sh"):
        await backend.create(spec)


async def test_the_daemon_answers_a_ping(backend: DockerRunnerBackend) -> None:
    assert await backend.ping() is True
