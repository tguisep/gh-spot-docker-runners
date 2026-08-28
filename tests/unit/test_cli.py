"""The CLI, through Typer's runner.

These check the controller contract: the right exit code, and a message that says what to do
next. Rendering is exercised incidentally; orchestration is tested elsewhere, because none of
it lives here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

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
