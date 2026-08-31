"""The CLI, through Typer's runner.

These check the controller contract: the right exit code, and a message that says what to do
next. Rendering is exercised incidentally; orchestration is tested elsewhere, because none of
it lives here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghspot.domain.model.runner import RunnerState
from ghspot.infrastructure.config.settings import load
from ghspot.interfaces.cli import operations
from ghspot.interfaces.cli.main import app
from tests.unit.conftest import make_runner

runner = CliRunner()

CONFIG = """
[github]
token_file = "{token}"

[daemon]
state_db = "{db}"

[[pool]]
name = "default"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux", "x64"]
max_runners = 3

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""


@pytest.fixture
def config(tmp_path: Path) -> Path:
    token = tmp_path / "token"
    token.write_text("ghp_fake")
    token.chmod(0o600)
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.format(token=token, db=tmp_path / "state.db"))
    return path


def test_version_prints_the_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "ghspot" in result.stdout


def test_bare_invocation_shows_help_rather_than_an_error() -> None:
    result = runner.invoke(app, [])

    assert "self-hosted" in result.stdout.lower()


def test_validate_reports_the_configuration(config: Path) -> None:
    result = runner.invoke(app, ["config", "validate", "-c", str(config)])

    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert "default" in result.stdout
    assert "tguisep/gh-spot" in result.stdout


def test_a_missing_config_exits_two_and_points_at_the_example(tmp_path: Path) -> None:
    """Exit 2 is 'you asked for something impossible', distinct from a runtime failure."""
    result = runner.invoke(app, ["config", "validate", "-c", str(tmp_path / "nope.toml")])

    assert result.exit_code == 2
    assert "no configuration file at" in result.output
    assert "config.example.toml" in result.output


def test_a_broken_config_names_the_problem(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[github]\n[[pool]]\nname = "x"\n')

    result = runner.invoke(app, ["config", "validate", "-c", str(path)])

    assert result.exit_code == 2
    assert "'repository' is required" in result.output


def test_pool_list_shows_configured_pools(config: Path) -> None:
    result = runner.invoke(app, ["pool", "list", "-c", str(config)])

    assert result.exit_code == 0
    assert "default" in result.stdout


def test_pool_status_for_an_unknown_pool_lists_the_real_ones(config: Path) -> None:
    result = runner.invoke(app, ["pool", "status", "ghost", "-c", str(config)])

    assert result.exit_code == 2
    assert "no pool named" in result.output
    assert "default" in result.output


def test_pool_status_reports_an_empty_pool(config: Path) -> None:
    result = runner.invoke(app, ["pool", "status", "-c", str(config)])

    assert result.exit_code == 0
    assert "no runners" in result.stdout


def test_runner_list_works_without_a_token_or_docker(config: Path) -> None:
    """This is the command you reach for when things are broken, so it must not need them."""
    result = runner.invoke(app, ["runner", "list", "-c", str(config)])

    assert result.exit_code == 0
    assert "no runners" in result.stdout


def test_help_lists_every_command_group() -> None:
    result = runner.invoke(app, ["--help"])

    for command in ("daemon", "doctor", "pool", "runner", "config"):
        assert command in result.stdout


def test_the_daemon_reports_a_missing_credential_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commonest misconfiguration there is, and it used to answer with a stack trace.

    Wiring the application resolves the credential, and that happened outside the command's
    error handling.
    """
    monkeypatch.delenv("GHSPOT_GITHUB_TOKEN", raising=False)
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.format(token=tmp_path / "absent-token", db=tmp_path / "state.db"))

    result = runner.invoke(app, ["daemon", "--once", "-c", str(path)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "token" in result.output
    assert "doctor" in result.output


# ---------------------------------------------------------------- stats


def test_stats_reports_an_empty_window_rather_than_nothing(config: Path) -> None:
    """A quiet fleet and a broken command must not look the same."""
    result = runner.invoke(app, ["stats", "-c", str(config)])

    assert result.exit_code == 0
    assert "nothing recorded" in result.output


def test_stats_refuses_a_window_that_is_not_a_duration(config: Path) -> None:
    result = runner.invoke(app, ["stats", "--since", "banana", "-c", str(config)])

    assert result.exit_code == 2
    assert "not a duration" in result.output


def test_stats_reads_the_log_the_daemon_wrote(config: Path, tmp_path: Path) -> None:
    """End to end through the real store: the numbers reach the table."""
    import asyncio

    from ghspot.domain.model import events as domain_events
    from ghspot.domain.model.target import RepositoryTarget
    from ghspot.infrastructure.persistence.sqlite import SqliteEventLog

    from .conftest import at

    log = SqliteEventLog(tmp_path / "state.db")
    asyncio.run(
        log.append(
            [
                domain_events.RunnerRegistered(
                    occurred_at=at(minutes=0),
                    runner_id="r1",
                    runner_name="ghspot-default-r1",
                    github_runner_id=1,
                    repository=RepositoryTarget("tguisep", "gh-spot-docker-runners"),
                    pool="default",
                ),
                domain_events.RunnerTookJob(occurred_at=at(minutes=1), runner_id="r1", job_id=7),
                domain_events.RunnerRetired(
                    occurred_at=at(minutes=11), runner_id="r1", reason="job finished"
                ),
            ]
        )
    )

    result = runner.invoke(app, ["stats", "-c", str(config)])

    assert result.exit_code == 0
    assert "3 event(s) read" in result.output
    # The repository name is asserted through the numbers instead: Rich folds a long name
    # across lines at the test console's width, so the string is not contiguous.
    assert "10m00s" in result.output  # busy: took the job at +1m, retired at +11m
    assert "1m00s" in result.output  # wait: registered at +0, took the job at +1m


# ---------------------------------------------------------------- stopping everything


def test_stop_needs_a_runner_or_all(config: Path) -> None:
    """Neither is a mistake worth guessing at: one empties the host, the other does not."""
    result = runner.invoke(app, ["runner", "stop", "-c", str(config)])

    assert result.exit_code == 2
    assert "Give an id, or --all" in result.output


def test_stop_refuses_both_at_once(config: Path) -> None:
    result = runner.invoke(app, ["runner", "stop", "abc", "--all", "-c", str(config)])

    assert result.exit_code == 2
    assert "not both" in result.output


def test_pool_without_all_is_refused(config: Path) -> None:
    """It would silently do nothing, which is the worst answer to a command about stopping
    things."""
    result = runner.invoke(app, ["runner", "stop", "abc", "--pool", "x", "-c", str(config)])

    assert result.exit_code == 2
    assert "only means something with --all" in result.output


def test_stopping_everything_when_there_is_nothing(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*_args: object, **_kwargs: object) -> tuple[list[str], list[str], int]:
        return [], [], 0

    monkeypatch.setattr(operations, "stop_every_runner", nothing)

    result = runner.invoke(app, ["runner", "stop", "--all", "-c", str(config)])

    assert result.exit_code == 0
    assert "nothing to stop" in result.output


def test_stopping_everything_says_what_comes_back(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the number: min_idle is a floor the daemon maintains, so emptying a pool
    that asks for one warm runner lasts exactly one poll interval. An operator who does not
    know that runs the command twice and concludes it did not work."""

    async def two(*_args: object, **_kwargs: object) -> tuple[list[str], list[str], int]:
        return ["ghspot-default-a", "ghspot-default-b"], [], 1

    monkeypatch.setattr(operations, "stop_every_runner", two)

    result = runner.invoke(app, ["runner", "stop", "--all", "-c", str(config)])

    assert result.exit_code == 0
    assert "ghspot-default-a" in result.output
    assert "will start 1 again" in result.output


def test_a_busy_runner_is_named_rather_than_silently_skipped(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def one_busy(*_args: object, **_kwargs: object) -> tuple[list[str], list[str], int]:
        return [], ["ghspot-default-busy"], 0

    monkeypatch.setattr(operations, "stop_every_runner", one_busy)

    result = runner.invoke(app, ["runner", "stop", "--all", "-c", str(config)])

    assert "ghspot-default-busy" in result.output
    assert "--force" in result.output


class _Fleet:
    """Enough of an Application for `stop_every_runner`: a repository, retire, and aclose."""

    def __init__(self, runners: list[object]) -> None:
        self.runners = _Repository(runners)
        self.retired: list[tuple[str, bool]] = []
        self.closed = False

    async def retire(self, runner: object, reason: str, *, force: bool = False) -> None:
        self.retired.append((runner.name, force))  # type: ignore[attr-defined]

    async def aclose(self) -> None:
        self.closed = True


class _Repository:
    def __init__(self, runners: list[object]) -> None:
        self._runners = runners

    async def list_active(self) -> list[object]:
        return self._runners


@pytest.mark.anyio
async def test_stop_every_runner_leaves_busy_ones_alone(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = _Fleet(
        [
            make_runner("a", state=RunnerState.IDLE),
            make_runner("b", state=RunnerState.BUSY),
            make_runner("c", state=RunnerState.IDLE),
        ]
    )
    monkeypatch.setattr(operations, "build", lambda _settings: fleet)
    settings = load(config)

    retired, refused, coming_back = await operations.stop_every_runner(settings, force=False)

    assert len(retired) == 2
    assert len(refused) == 1
    assert fleet.closed is True
    # This pool sets no min_idle, so nothing comes back on its own.
    assert coming_back == 0


@pytest.mark.anyio
async def test_force_takes_the_busy_one_too(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet = _Fleet([make_runner("b", state=RunnerState.BUSY)])
    monkeypatch.setattr(operations, "build", lambda _settings: fleet)

    retired, refused, _ = await operations.stop_every_runner(load(config), force=True)

    assert len(retired) == 1 and refused == []
    assert fleet.retired[0][1] is True


@pytest.mark.anyio
async def test_min_idle_is_reported_as_what_comes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number an operator needs: emptying a pool that keeps one warm lasts one tick."""
    token = tmp_path / "token"
    token.write_text("ghp_fake")
    token.chmod(0o600)
    path = tmp_path / "config.toml"
    path.write_text(
        CONFIG.format(token=token, db=tmp_path / "state.db").replace(
            "max_runners = 3", "max_runners = 3\nmin_idle = 2"
        )
    )
    monkeypatch.setattr(
        operations, "build", lambda _settings: _Fleet([make_runner("a", state=RunnerState.IDLE)])
    )

    _retired, _refused, coming_back = await operations.stop_every_runner(load(path), force=False)

    assert coming_back == 2
