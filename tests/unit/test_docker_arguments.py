"""The pure parts of the Docker adapter: what gets sent, and how replies are read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ghspot.domain.errors import BackendError
from ghspot.domain.ports.backend import ContainerSpec
from ghspot.infrastructure.docker import backend as backend_module
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


# ---------------------------------------------------------------- gpus


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("all", {"Count": -1, "DeviceIDs": []}),
        ("ALL", {"Count": -1, "DeviceIDs": []}),
        (2, {"Count": 2, "DeviceIDs": []}),
        (("0", "GPU-abc"), {"Count": 0, "DeviceIDs": ["0", "GPU-abc"]}),
    ],
)
def test_a_gpu_selection_becomes_a_device_request(
    setting: object, expected: dict[str, object]
) -> None:
    """The same shape `docker run --gpus` produces."""
    requests = _run_arguments(spec(gpus=setting))["device_requests"]

    assert len(requests) == 1
    sent = dict(requests[0])
    assert sent["Capabilities"] == [["gpu"]]
    for key, value in expected.items():
        assert sent[key] == value


def test_no_gpu_means_no_device_request() -> None:
    """A container handed a GPU it does not need holds it against every other runner."""
    assert "device_requests" not in _run_arguments(spec())


def test_an_empty_id_list_asks_for_nothing() -> None:
    assert "device_requests" not in _run_arguments(spec(gpus=()))


def test_a_nonsense_selection_is_refused() -> None:
    with pytest.raises(BackendError, match="not a GPU selection"):
        _run_arguments(spec(gpus="lots"))


# ---------------------------------------------------------------- host load


def test_cpu_is_load_average_against_the_core_count(tmp_path: Path) -> None:
    """Load average, not an instantaneous sample: it covers everything on the box, and it is
    the number that says whether work is queueing for the CPU."""
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("2.00 1.50 1.20 2/431 1234\n")

    with patch.object(backend_module, "PROC_LOADAVG", loadavg):
        assert backend_module._cpu_percent(4) == 50.0
        assert backend_module._cpu_percent(2) == 100.0


def test_cpu_is_unknown_when_the_core_count_is(tmp_path: Path) -> None:
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("2.00 1.50 1.20 2/431 1234\n")

    with patch.object(backend_module, "PROC_LOADAVG", loadavg):
        assert backend_module._cpu_percent(None) is None


def test_cpu_is_unknown_rather_than_zero_when_proc_cannot_be_read(tmp_path: Path) -> None:
    """Unknown must not read as idle: the policy treats zero as "plenty of room"."""
    with patch.object(backend_module, "PROC_LOADAVG", tmp_path / "absent"):
        assert backend_module._cpu_percent(4) is None


def test_memory_in_use_excludes_what_the_kernel_would_give_back(tmp_path: Path) -> None:
    """MemTotal - MemFree counts the page cache as used, and a Linux box doing any work at
    all looks 95% full by that measure."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16000000 kB\nMemFree:          200000 kB\nMemAvailable:    8000000 kB\n"
    )

    with patch.object(backend_module, "PROC_MEMINFO", meminfo):
        used = backend_module._memory_used(16000000 * 1024)

    assert used == 8000000 * 1024


def test_memory_is_unknown_when_proc_cannot_be_read(tmp_path: Path) -> None:
    with patch.object(backend_module, "PROC_MEMINFO", tmp_path / "absent"):
        assert backend_module._memory_used(1024) is None


def test_a_field_docker_omitted_is_not_invented() -> None:
    assert backend_module._positive(None) is None
    assert backend_module._positive(0) is None
    assert backend_module._positive(0, allow_zero=True) == 0
    assert backend_module._positive(True) is None  # a bool is not a count
    assert backend_module._positive(8) == 8
