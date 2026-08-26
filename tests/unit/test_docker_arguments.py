"""The pure parts of the Docker adapter: what gets sent, and how replies are read."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ghspot.domain.ports.backend import ContainerSpec
from ghspot.infrastructure.docker.backend import (
    DOCKER_SOCKET,
    _parse_time,
    _run_arguments,
    _to_status,
)


def spec(**overrides: object) -> ContainerSpec:
    base: dict[str, object] = {
        "image": "ghspot/runner:test",
        "name": "ghspot-default-abc",
        "labels": {"io.ghspot.managed": "true"},
        "environment": {"RUNNER_JIT_CONFIG": "blob"},
    }
    base.update(overrides)
    return ContainerSpec(**base)  # type: ignore[arg-type]


def test_a_runner_container_never_restarts() -> None:
    """A restart would resurrect a runner whose single-use config is already spent."""
    arguments = _run_arguments(spec())

    assert arguments["restart_policy"] == {"Name": "no"}
    assert arguments["auto_remove"] is False
    assert arguments["detach"] is True


def test_the_docker_socket_is_mounted_only_when_asked() -> None:
    assert DOCKER_SOCKET not in _run_arguments(spec())["volumes"]

    volumes = _run_arguments(spec(mount_docker_socket=True))["volumes"]
    assert volumes[DOCKER_SOCKET] == {"bind": DOCKER_SOCKET, "mode": "rw"}


def test_resource_limits_are_translated_to_docker_units() -> None:
    arguments = _run_arguments(spec(cpus=2.5, memory="4g"))

    assert arguments["nano_cpus"] == 2_500_000_000
    assert arguments["mem_limit"] == "4g"


def test_unset_limits_are_omitted_rather_than_sent_as_none() -> None:
    arguments = _run_arguments(spec())

    assert "nano_cpus" not in arguments
    assert "mem_limit" not in arguments
    assert "network" not in arguments


def test_extra_volumes_are_merged_with_the_socket() -> None:
    arguments = _run_arguments(
        spec(volumes={"/srv/cache": "/home/runner/.cache"}, mount_docker_socket=True)
    )

    assert arguments["volumes"]["/srv/cache"] == {"bind": "/home/runner/.cache", "mode": "rw"}
    assert DOCKER_SOCKET in arguments["volumes"]


def _container(*, state: dict[str, object] | None = None, **attrs: object) -> SimpleNamespace:
    merged: dict[str, object] = {
        "StartedAt": "2026-08-26T12:00:00.123456789Z",
        "FinishedAt": "0001-01-01T00:00:00Z",
        "ExitCode": 0,
    }
    merged.update(state or {})
    return SimpleNamespace(
        id=attrs.get("id", "c1"),
        name=attrs.get("name", "ghspot-default-abc"),
        status=attrs.get("status", "running"),
        labels=attrs.get("labels", {"io.ghspot.managed": "true"}),
        attrs={"State": merged},
    )


def test_a_running_container_is_read_correctly() -> None:
    status = _to_status(_container())

    assert status.is_running and not status.has_exited
    assert status.started_at == datetime(2026, 8, 26, 12, 0, 0, 123456, tzinfo=UTC)
    assert status.finished_at is None  # Docker's zero date means "never"
    assert status.labels["io.ghspot.managed"] == "true"


def test_an_exited_container_carries_its_exit_code() -> None:
    status = _to_status(
        _container(status="exited", state={"FinishedAt": "2026-08-26T12:05:00Z", "ExitCode": 137})
    )

    assert status.has_exited
    assert status.exit_code == 137
    assert status.finished_at == datetime(2026, 8, 26, 12, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    ["0001-01-01T00:00:00Z", "", "not-a-date", None, 12345],
)
def test_unusable_timestamps_become_none_rather_than_raising(value: object) -> None:
    """A parse failure here must not take down a reconciliation tick."""
    assert _parse_time(value) is None


def test_a_timestamp_without_a_zone_is_assumed_utc() -> None:
    parsed = _parse_time("2026-08-26T12:00:00")
    assert parsed is not None and parsed.tzinfo is not None


def test_nanosecond_precision_with_an_offset_is_handled() -> None:
    parsed = _parse_time("2026-08-26T12:00:00.123456789+02:00")
    assert parsed is not None and parsed.microsecond == 123456
