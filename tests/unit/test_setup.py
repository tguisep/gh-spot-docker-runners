"""The first-run wizard.

Driven through its own prompts with scripted answers, because what matters is the file it
leaves behind — an operator's first configuration is the one they are least able to debug.
"""

from __future__ import annotations

import stat
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.config.settings import load
from ghspot.interfaces.cli import images
from ghspot.interfaces.cli import setup as wizard
from ghspot.interfaces.cli.main import app

runner = CliRunner()

TOKEN_ANSWERS = "token\nghp_pretend\ntguisep/my-project\nbuilders\nubuntu-24.04\n3\ny\ny\n"


@pytest.fixture(autouse=True)
def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable Docker, unless a test says otherwise.

    The wizard asks whether the runner image is already built. Left alone, every test here
    would answer that from whatever the machine running them happens to have pulled, and the
    build offer would appear on a developer's box and not in CI.
    """
    monkeypatch.setattr(wizard, "_image_present", lambda _: None)


def test_it_writes_a_configuration_the_daemon_accepts(tmp_path: Path) -> None:
    """The only assertion that really matters: the wizard cannot produce a file its own
    parser rejects."""
    config = tmp_path / "config.toml"

    result = runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert result.exit_code == 0
    settings = load(config)
    assert [pool.spec.name for pool in settings.pools] == ["builders"]
    assert str(settings.pools[0].spec.repository) == "tguisep/my-project"
    assert settings.pools[0].spec.max_runners == 3


def test_the_token_is_written_to_a_file_only_its_owner_can_read(tmp_path: Path) -> None:
    """Created with its mode before anything is written to it, or the secret is briefly
    world-readable — the reason the docs tell operators to use `install -m 600`."""
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    token = tmp_path / "token"
    assert token.read_text() == "ghp_pretend"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


def test_the_token_is_not_written_into_the_configuration_itself(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert "ghp_pretend" not in config.read_text()


def test_an_app_is_pointed_at_rather_than_copied(tmp_path: Path) -> None:
    """The private key stays where it is: copying it would make a second thing to protect."""
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\n")
    config = tmp_path / "config.toml"

    result = runner.invoke(
        app,
        ["setup", "-c", str(config)],
        input=f"app\n123456\n{key}\ntguisep/p\ngpu\nrhel-9\n1\nn\nn\n",
    )

    assert result.exit_code == 0
    body = config.read_text()
    assert 'app_id = "123456"' in body
    assert str(key) in body
    assert "BEGIN PRIVATE KEY" not in body


def test_it_refuses_to_overwrite_what_is_already_there(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("# somebody's work\n")

    result = runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert result.exit_code == 2
    assert config.read_text() == "# somebody's work\n"


def test_force_replaces_it(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("# somebody's work\n")

    result = runner.invoke(app, ["setup", "-c", str(config), "--force"], input=TOKEN_ANSWERS)

    assert result.exit_code == 0
    assert "[[pool]]" in config.read_text()


def test_a_repository_that_is_not_owner_slash_name_is_asked_again(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"

    result = runner.invoke(
        app,
        ["setup", "-c", str(config)],
        input="token\nghp_x\nnonsense\ntguisep/second-try\np\nubuntu-24.04\n1\nn\nn\n",
    )

    assert result.exit_code == 0
    assert str(load(config).pools[0].spec.repository) == "tguisep/second-try"


@pytest.mark.parametrize(("answer", "expected"), [("y", True), ("n", False)])
def test_the_docker_socket_is_asked_about_rather_than_assumed(
    tmp_path: Path, answer: str, expected: bool
) -> None:
    """It hands a job root on the host, so it is a question with a warning attached."""
    config = tmp_path / "config.toml"

    runner.invoke(
        app,
        ["setup", "-c", str(config)],
        input=f"token\nghp_x\ntguisep/p\np\nubuntu-24.04\n1\n{answer}\nn\n",
    )

    assert load(config).pools[0].template.mount_docker_socket is expected


def test_nothing_is_written_when_the_answers_run_out(tmp_path: Path) -> None:
    """Ctrl-D half way through leaves the machine as it was found."""
    config = tmp_path / "config.toml"

    result = runner.invoke(app, ["setup", "-c", str(config)], input="token\n")

    assert result.exit_code == 130
    assert not config.exists()


def test_the_build_hint_is_a_command_and_not_a_path(tmp_path: Path) -> None:
    """The first instruction used to be a bare `images/runner/build.sh`, which resolves only
    for somebody standing in a clone — and never on a host installed from the .deb. It is now
    a ghspot command, which is true in both places because the daemon finds its own sources."""
    config = tmp_path / "config.toml"

    result = runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert result.exit_code == 0
    assert "ghspot image build ubuntu-24.04" in result.output
    assert "images/runner/build.sh" not in result.output


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        (Path("/etc/ghspot"), "sudo ghspot doctor"),
        (Path("/home/someone/.config/ghspot"), "ghspot doctor"),
    ],
)
def test_only_a_system_configuration_is_checked_with_sudo(
    directory: Path,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`/etc/ghspot/config.toml` is root:ghspot 0640 and the checks want the Docker socket,
    so that step needs the privilege the systemctl one always asked for — the wizard's own
    sudo is gone by the time the operator types it. A configuration in $HOME must not ask."""
    answers = wizard.Answers(
        repository=RepositoryTarget.parse("tguisep/my-project"),
        pool="builders",
        image="ubuntu-24.04",
        uses_app=False,
    )

    wizard._next_steps(directory / "config.toml", answers, built=True)

    line = next(row for row in capsys.readouterr().out.splitlines() if "ghspot doctor" in row)
    assert expected in line
    assert ("sudo ghspot doctor" in line) == (expected.startswith("sudo"))


def test_the_written_file_carries_every_setting_not_just_the_answers(tmp_path: Path) -> None:
    """The wizard used to write eighteen lines and say nothing about the thirty settings it
    had not asked about — on the file somebody is least equipped to go and research."""
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    written = config.read_text()
    for setting in ("[capacity]", "[housekeeping]", "requires_labels", "priority", "pm ="):
        assert setting in written


def test_what_was_not_asked_about_keeps_the_daemon_s_own_behaviour(tmp_path: Path) -> None:
    """Filling the file in must not smuggle in settings nobody chose. The reference sets
    cpus and memory to illustrate them; inherited, they would cap every job on the host."""
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    pool = load(config).pools[0]
    assert pool.template.cpus is None
    assert pool.template.memory is None
    # These the reference does set, and its values are the code's own defaults — so the file
    # says out loud what the daemon would have done in silence.
    assert pool.spec.idle_timeout == timedelta(minutes=10)
    assert pool.spec.max_job_duration == timedelta(hours=2)


def test_the_illustrated_second_pool_stays_an_illustration(tmp_path: Path) -> None:
    """The reference ends with a whole commented-out [[pool]]. A rewrite that reached it
    would set the example's repository and image too, and the operator would find a second
    pool they never asked for."""
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    settings = load(config)
    assert [pool.spec.name for pool in settings.pools] == ["builders"]


def test_a_system_configuration_does_not_keep_state_in_a_home_it_has_not_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference puts state_db under ~, which the ghspot service account does not have."""
    # setup imports the constant by value, so the binding to patch is its own.
    monkeypatch.setattr(wizard, "SYSTEM_DIRECTORY", tmp_path)
    config = tmp_path / "config.toml"

    runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert 'state_db = "/var/lib/ghspot/state.db"' in config.read_text()


def test_an_app_replaces_the_token_line_rather_than_leaving_both(tmp_path: Path) -> None:
    """Two credentials in one file is a configuration whose behaviour depends on which the
    loader happens to prefer."""
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\n")
    config = tmp_path / "config.toml"

    runner.invoke(
        app,
        ["setup", "-c", str(config)],
        input=f"app\n123456\n{key}\ntguisep/my-project\nbuilders\nubuntu-24.04\n3\ny\ny\n",
    )

    written = config.read_text()
    assert 'app_id = "123456"' in written
    assert f'private_key_file = "{key}"' in written
    assert "\ntoken_file =" not in written


def test_a_missing_reference_still_writes_a_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.example.toml is a documentation file. A wizard that fails because one is not
    installed is worse than one that writes the short form."""
    monkeypatch.setattr(wizard, "example_config", lambda: None)
    config = tmp_path / "config.toml"

    result = runner.invoke(app, ["setup", "-c", str(config)], input=TOKEN_ANSWERS)

    assert result.exit_code == 0
    assert load(config).pools[0].spec.max_runners == 3


BUILD_ANSWERS = TOKEN_ANSWERS + "y\n"


def test_a_missing_image_is_offered_and_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Building the runner image is the wizard's own first next-step, and the one nothing
    works without — a pool whose image is missing starts no runners and says so only in the
    daemon's log."""
    built: list[str | None] = []

    def record(variant: str | None, *, sources: str | None = None) -> int:
        built.append(variant)
        return 0

    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(images, "build", record)

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=BUILD_ANSWERS)

    assert result.exit_code == 0
    assert built == ["ubuntu-24.04"]


def test_a_built_image_drops_the_step_that_asks_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(images, "build", lambda _: 0)

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=BUILD_ANSWERS)

    assert "build the runner image" not in result.output
    assert "1. check everything" in result.output


def test_declining_leaves_the_instruction_where_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is minutes of work on a machine somebody may not want busy yet. Saying no has to
    put them back exactly where the wizard used to leave everyone."""
    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(images, "build", lambda _: pytest.fail("should not have built"))

    result = runner.invoke(
        app, ["setup", "-c", str(tmp_path / "config.toml")], input=TOKEN_ANSWERS + "n\n"
    )

    assert result.exit_code == 0
    assert "1. build the runner image" in result.output


def test_a_failed_build_keeps_the_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting it as done because it was attempted would send the operator to `doctor`
    looking for a different problem."""
    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(images, "build", lambda _: 1)

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=BUILD_ANSWERS)

    assert "1. build the runner image" in result.output


def test_an_image_that_is_already_there_is_not_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "_image_present", lambda _: True)
    monkeypatch.setattr(images, "build", lambda _: pytest.fail("should not have built"))

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=TOKEN_ANSWERS)

    assert "already built" in result.output
    assert "build the runner image" not in result.output


def test_unreachable_docker_is_not_an_unbuilt_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offering a build that cannot start is worse than not offering: `doctor`, one step
    later, reports the real problem properly."""
    monkeypatch.setattr(wizard, "_image_present", lambda _: None)
    monkeypatch.setattr(images, "build", lambda _: pytest.fail("should not have built"))

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=TOKEN_ANSWERS)

    assert "Build ghspot/runner" not in result.output
    assert "1. build the runner image" in result.output


def test_nothing_is_offered_without_sources_to_build_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(wizard, "runner_sources", lambda: None)
    monkeypatch.setattr(images, "build", lambda _: pytest.fail("should not have built"))

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=TOKEN_ANSWERS)

    assert "Build ghspot/runner" not in result.output
    assert "1. build the runner image" in result.output


def test_a_configuration_the_daemon_rejects_is_not_followed_by_a_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation comes first. Spending several minutes on an image for a configuration that
    was never going to load is the wrong order to find that out in."""
    monkeypatch.setattr(wizard, "_image_present", lambda _: False)
    monkeypatch.setattr(images, "build", lambda _: pytest.fail("should not have built"))
    monkeypatch.setattr(wizard, "_validate", lambda _: False)

    result = runner.invoke(app, ["setup", "-c", str(tmp_path / "config.toml")], input=BUILD_ANSWERS)

    assert result.exit_code == 1
