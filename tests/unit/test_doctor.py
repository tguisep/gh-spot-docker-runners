"""`ghspot doctor`.

Its whole purpose is to report on a broken environment, so the case that matters most is the
one where something *is* broken: it must finish the report rather than fall over on the first
failure and hide the rest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghspot.domain.errors import BackendError, ForgeAuthError
from ghspot.interfaces.cli import doctor as doctor_module
from ghspot.interfaces.cli.main import app

runner = CliRunner()

CONFIG = """
[github]
token_file = "{token}"

[daemon]
state_db = "{db}"

[[pool]]
name = "default"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
docker_socket = true
"""


@pytest.fixture
def config(tmp_path: Path) -> Path:
    token = tmp_path / "token"
    token.write_text("ghp_fake")
    token.chmod(0o600)
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.format(token=token, db=tmp_path / "state.db"))
    return path


def test_an_unreachable_docker_does_not_abort_the_report(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this file exists for.

    The GitHub checks used to build the whole application, Docker backend included, so an
    unreachable daemon raised out of the command — hiding every other check, in exactly the
    situation the command is meant to diagnose.
    """

    def unreachable() -> None:
        raise BackendError("could not reach the Docker daemon: Permission denied")

    monkeypatch.setattr(doctor_module, "DockerRunnerBackend", lambda: unreachable())

    result = runner.invoke(app, ["doctor", "-c", str(config)])

    assert result.exit_code == 1
    assert "✗ docker" in result.output
    # The report continued past the failure.
    assert "configuration" in result.output
    assert "github auth" in result.output
    assert "not ready" in result.output
    assert "Traceback" not in result.output


def test_a_permission_error_says_the_group_change_needs_a_new_shell() -> None:
    """Adding yourself to a group does not affect the running shell.

    Omitting that sends someone round the same loop a second time.
    """
    remedy = doctor_module._docker_remedy(
        BackendError("Connection aborted, PermissionError(13, 'Permission denied')")
    )

    assert "usermod -aG docker" in remedy
    assert "newgrp docker" in remedy or "log out" in remedy


def test_a_stopped_daemon_is_told_to_start_it_not_to_change_groups() -> None:
    remedy = doctor_module._docker_remedy(
        BackendError("Connection aborted, FileNotFoundError(2, 'No such file or directory')")
    )

    assert "systemctl start docker" in remedy
    assert "usermod" not in remedy


def test_a_missing_credential_is_reported_not_raised(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor_module,
        "build_forge",
        lambda _settings: (_ for _ in ()).throw(ForgeAuthError("no GitHub token")),
    )

    result = runner.invoke(app, ["doctor", "-c", str(config)])

    assert result.exit_code == 1
    assert "github auth" in result.output
    assert "Traceback" not in result.output


def test_doctor_exits_zero_only_when_everything_passes(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit code is what a provisioning script will branch on."""

    class Reachable:
        async def ping(self) -> bool:
            return True

        async def image_exists(self, image: str) -> bool:
            return True

    class Forge:
        def describe_auth(self) -> str:
            return "personal access token"

        async def list_runners(self, repository: object) -> list[object]:
            return []

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(doctor_module, "DockerRunnerBackend", Reachable)
    monkeypatch.setattr(doctor_module, "build_forge", lambda _settings: Forge())

    result = runner.invoke(app, ["doctor", "-c", str(config)])

    assert result.exit_code == 0
    assert "ready" in result.output


# ---------------------------------------------------------------- Jetson


TEGRA_LINE = (
    "# R32 (release), REVISION: 7.1, GCID: 30718123, BOARD: t210ref, "
    "EABI: aarch64, DATE: Sat Feb 19 17:05:08 UTC 2022\n"
)


def _as_tegra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    release = tmp_path / "nv_tegra_release"
    release.write_text(TEGRA_LINE)
    monkeypatch.setattr(doctor_module, "TEGRA_RELEASE", release)


def test_a_jetson_is_recognised_by_its_release_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _as_tegra(monkeypatch, tmp_path)

    assert doctor_module._tegra_release() == "L4T R32.7.1"


def test_a_desktop_is_not_a_jetson(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor_module, "TEGRA_RELEASE", tmp_path / "absent")

    assert doctor_module._tegra_release() is None


def test_asking_a_jetson_for_gpus_is_refused_with_the_alternative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Engine has no device-request API on Tegra, so `gpus` can only ever fail there."""
    _as_tegra(monkeypatch, tmp_path)

    check = doctor_module._gpu_check("gpu", "all")

    assert not check.ok
    assert "no device-request API" in check.detail
    assert 'runtime = "nvidia"' in check.remedy


async def test_an_unregistered_runtime_names_what_the_engine_does_have() -> None:
    class Engine:
        async def runtimes(self) -> frozenset[str]:
            return frozenset({"runc"})

    check = await doctor_module._runtime_check(Engine(), "gpu", "nvidia")  # type: ignore[arg-type]

    assert not check.ok
    assert "nvidia" in check.detail
    assert "runc" in check.remedy


async def test_a_registered_runtime_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _as_tegra(monkeypatch, tmp_path)

    class Engine:
        async def runtimes(self) -> frozenset[str]:
            return frozenset({"runc", "nvidia"})

    check = await doctor_module._runtime_check(Engine(), "gpu", "nvidia")  # type: ignore[arg-type]

    assert check.ok
    assert "L4T R32.7.1" in check.detail
