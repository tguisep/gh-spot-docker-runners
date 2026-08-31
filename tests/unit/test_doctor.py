"""`ghspot doctor`.

Its whole purpose is to report on a broken environment, so the case that matters most is the
one where something *is* broken: it must finish the report rather than fall over on the first
failure and hide the rest.
"""

from __future__ import annotations

import pwd
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghspot.domain.errors import BackendError, ForgeAuthError
from ghspot.infrastructure.config.settings import load
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


def test_a_credential_the_service_account_cannot_read_is_a_failure(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run under sudo — which is how the wizard says to run it — every file check passes as
    root while the daemon, which runs as `ghspot`, cannot start at all. "ready" has to mean
    the daemon is ready, not that root is."""
    monkeypatch.setattr(doctor_module, "SYSTEM_CONFIG_DIRECTORY", config.parent)
    monkeypatch.setattr(pwd, "getpwnam", lambda _: _Passwd(uid=61000, gid=61000))

    settings = load(config)
    checks = doctor_module._service_account(settings)

    assert [check.ok for check in checks] == [False]
    assert "cannot be read by ghspot" in checks[0].detail
    assert "chown root:ghspot" in checks[0].remedy


def test_a_credential_the_service_account_can_read_passes(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = config.parent / "token"
    token.chmod(0o640)
    monkeypatch.setattr(doctor_module, "SYSTEM_CONFIG_DIRECTORY", config.parent)
    monkeypatch.setattr(pwd, "getpwnam", lambda _: _Passwd(uid=61000, gid=token.stat().st_gid))

    checks = doctor_module._service_account(load(config))

    assert [check.ok for check in checks] == [True]


def test_a_configuration_outside_etc_has_no_service_account_to_check(config: Path) -> None:
    """Anywhere else the file is meant for whoever is running this, and nothing runs as
    anybody else."""
    assert doctor_module._service_account(load(config)) == []


class _Passwd:
    """Enough of a pwd entry for the check, so the test does not need a real ghspot user."""

    def __init__(self, *, uid: int, gid: int) -> None:
        self.pw_uid = uid
        self.pw_gid = gid
