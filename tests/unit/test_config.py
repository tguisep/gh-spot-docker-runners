"""Configuration loading: mistakes must fail here, naming the field."""

from __future__ import annotations

import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

from ghspot.infrastructure.config.settings import (
    APP_ID_ENV,
    APP_KEY_ENV,
    TOKEN_ENV,
    ConfigError,
    from_mapping,
    load,
)

MINIMAL = """
[github]
token_file = "/tmp/token"

[[pool]]
name = "default"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""


def parse(text: str) -> object:
    return from_mapping(tomllib.loads(text))


def test_a_minimal_configuration_loads_with_sensible_defaults() -> None:
    settings = parse(MINIMAL)

    assert len(settings.pools) == 1  # type: ignore[attr-defined]
    pool = settings.pools[0]  # type: ignore[attr-defined]
    assert str(pool.spec.repository) == "tguisep/gh-spot-docker-runners"
    assert pool.spec.max_runners == 2
    assert pool.spec.idle_timeout == timedelta(minutes=10)
    assert pool.template.image == "ghspot/runner:ubuntu-24.04"
    assert pool.template.mount_docker_socket is False
    assert settings.daemon.poll_interval == timedelta(seconds=15)  # type: ignore[attr-defined]


def test_the_shipped_example_is_valid() -> None:
    """The example is documentation; if it stops loading, the documentation is wrong."""
    text = Path("config.example.toml").read_text(encoding="utf-8")

    settings = from_mapping(tomllib.loads(text))

    assert [pool.spec.name for pool in settings.pools] == ["default"]
    assert settings.pools[0].template.mount_docker_socket is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("10m", timedelta(minutes=10)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
        ("500ms", timedelta(milliseconds=500)),
        ("45", timedelta(seconds=45)),
        (90, timedelta(seconds=90)),
        ("  15 s  ", timedelta(seconds=15)),
    ],
)
def test_durations_accept_the_obvious_forms(value: object, expected: timedelta) -> None:
    settings = parse(
        MINIMAL.replace('name = "default"', f'name = "default"\nidle_timeout = "{value}"')
    )
    assert settings.pools[0].spec.idle_timeout == expected  # type: ignore[attr-defined]


def test_a_nonsense_duration_names_the_field() -> None:
    with pytest.raises(ConfigError, match="idle_timeout"):
        parse(MINIMAL.replace('name = "default"', 'name = "default"\nidle_timeout = "soon"'))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("", "at least one \\[\\[pool\\]\\]"),
        (MINIMAL.replace('name = "default"', ""), "'name' is required"),
        (MINIMAL.replace('repository = "tguisep/gh-spot-docker-runners"', ""), "'repository'"),
        (MINIMAL.replace('labels = ["self-hosted", "linux"]', "labels = []"), "non-empty list"),
        (MINIMAL.replace('image = "ghspot/runner:ubuntu-24.04"', ""), "needs an 'image'"),
        (
            MINIMAL.replace('repository = "tguisep/gh-spot-docker-runners"', 'repository = "nope"'),
            "owner/name",
        ),
        (
            MINIMAL.replace('name = "default"', 'name = "default"\nmin_idle = 9\nmax_runners = 2'),
            "exceeds",
        ),
    ],
)
def test_a_broken_configuration_is_refused_with_a_useful_message(
    mutation: str, expected: str
) -> None:
    with pytest.raises(ConfigError, match=expected):
        parse(mutation)


def test_two_pools_cannot_share_a_name() -> None:
    doubled = MINIMAL + MINIMAL.split('[github]\ntoken_file = "/tmp/token"\n')[-1]

    with pytest.raises(ConfigError, match="both named"):
        parse(doubled)


def test_a_single_pool_table_is_accepted_as_well_as_an_array() -> None:
    """`[pool]` instead of `[[pool]]` is an easy slip and means the obvious thing."""
    settings = parse(MINIMAL)
    assert settings.pools[0].spec.name == "default"  # type: ignore[attr-defined]


# ---------------------------------------------------------------- credentials


def test_credential_shaped_environment_is_refused(tmp_path: Path) -> None:
    """The property the design rests on must not be undoable by a config typo."""
    text = MINIMAL + '\n[pool.container.environment]\nGH_TOKEN = "ghp_xxx"\n'

    with pytest.raises(ConfigError, match="refusing to put GH_TOKEN"):
        parse(text)


@pytest.mark.parametrize("key", ["MY_SECRET", "db_password", "RUNNER_JITCONFIG"])
def test_every_credential_shape_is_caught(key: str) -> None:
    with pytest.raises(ConfigError, match="refusing to put"):
        parse(MINIMAL + f'\n[pool.container.environment]\n{key} = "x"\n')


def test_ordinary_environment_is_allowed() -> None:
    settings = parse(MINIMAL + '\n[pool.container.environment]\nTZ = "Europe/Paris"\n')

    assert settings.pools[0].template.environment == {"TZ": "Europe/Paris"}  # type: ignore[attr-defined]


def test_the_token_comes_from_the_environment_before_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A systemd unit injects it; there should be no need for a file on disk."""
    token_file = tmp_path / "token"
    token_file.write_text("from-file")
    token_file.chmod(0o600)
    settings = parse(MINIMAL.replace("/tmp/token", str(token_file)))

    monkeypatch.setenv(TOKEN_ENV, "from-env")
    assert settings.github.resolve_token() == "from-env"  # type: ignore[attr-defined]

    monkeypatch.delenv(TOKEN_ENV)
    assert settings.github.resolve_token() == "from-file"  # type: ignore[attr-defined]


def test_a_missing_token_says_both_ways_to_provide_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    settings = parse(MINIMAL.replace('token_file = "/tmp/token"', ""))

    with pytest.raises(ConfigError, match=r"GHSPOT_GITHUB_TOKEN.*token_file"):
        settings.github.resolve_token()  # type: ignore[attr-defined]


def test_an_empty_token_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("   \n")
    token_file.chmod(0o600)
    settings = parse(MINIMAL.replace("/tmp/token", str(token_file)))

    with pytest.raises(ConfigError, match="is empty"):
        settings.github.resolve_token()  # type: ignore[attr-defined]


def test_a_world_readable_token_file_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("ghp_secret")
    token_file.chmod(0o644)
    settings = parse(MINIMAL.replace("/tmp/token", str(token_file)))

    with pytest.warns(UserWarning, match="chmod 600"):
        assert settings.github.resolve_token() == "ghp_secret"  # type: ignore[attr-defined]


# ---------------------------------------------------------------- file discovery


def test_a_missing_file_is_reported_by_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no configuration file at"):
        load(tmp_path / "absent.toml")


def test_invalid_toml_says_so(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not = = toml")

    with pytest.raises(ConfigError, match="not valid TOML"):
        load(path)


def test_loading_by_path_records_where_it_came_from(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL)

    settings = load(path)

    assert settings.source == path
    assert [str(repository) for repository in settings.repositories] == [
        "tguisep/gh-spot-docker-runners"
    ]


def test_repositories_are_deduplicated_across_pools(tmp_path: Path) -> None:
    """Two pools on one repository must not be polled twice per tick."""
    second = """
[[pool]]
name = "heavy"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "heavy"]
[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""
    settings = parse(MINIMAL + second)

    assert len(settings.pools) == 2  # type: ignore[attr-defined]
    assert len(settings.repositories) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------- github app


APP = """
[github]
app_id = "123456"
private_key_file = "{key}"

[[pool]]
name = "default"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""

PEM = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n"


def test_an_app_configuration_is_recognised(tmp_path: Path) -> None:
    key = tmp_path / "app.pem"
    key.write_text(PEM)
    key.chmod(0o600)

    settings = parse(APP.format(key=key))

    assert settings.github.uses_app is True  # type: ignore[attr-defined]
    assert settings.github.app_id == "123456"  # type: ignore[attr-defined]
    assert settings.github.resolve_private_key() == PEM  # type: ignore[attr-defined]


def test_a_token_configuration_is_not_an_app() -> None:
    assert parse(MINIMAL).github.uses_app is False  # type: ignore[attr-defined]


def test_a_private_key_without_an_app_id_is_refused(tmp_path: Path) -> None:
    """Half a GitHub App is a mistake, not a mode."""
    text = APP.format(key=tmp_path / "app.pem").replace('app_id = "123456"', "")

    with pytest.raises(ConfigError, match="needs both"):
        parse(text)


def test_the_app_id_can_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(APP_ID_ENV, "999")
    text = APP.format(key=tmp_path / "app.pem").replace('app_id = "123456"', "")

    assert parse(text).github.app_id == "999"  # type: ignore[attr-defined]


def test_the_private_key_can_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """systemd EnvironmentFile cannot hold newlines, so an escaped form is accepted."""
    monkeypatch.setenv(APP_KEY_ENV, PEM.replace("\n", "\\n"))
    settings = parse(APP.format(key=tmp_path / "absent.pem"))

    assert settings.github.resolve_private_key() == PEM  # type: ignore[attr-defined]


def test_a_missing_private_key_names_both_ways_to_supply_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(APP_KEY_ENV, raising=False)
    settings = parse(APP.format(key=tmp_path / "absent.pem"))

    with pytest.raises(ConfigError, match="could not read the private key"):
        settings.github.resolve_private_key()  # type: ignore[attr-defined]


def test_a_key_file_that_is_not_a_pem_says_where_to_get_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(APP_KEY_ENV, raising=False)
    key = tmp_path / "app.pem"
    key.write_text("oops, this is the app id")
    key.chmod(0o600)
    settings = parse(APP.format(key=key))

    with pytest.raises(ConfigError, match="Private keys"):
        settings.github.resolve_private_key()  # type: ignore[attr-defined]


def test_a_non_numeric_installation_id_is_refused(tmp_path: Path) -> None:
    text = APP.format(key=tmp_path / "app.pem").replace(
        'app_id = "123456"', 'app_id = "1"\ninstallation_id = "not-a-number"'
    )

    with pytest.raises(ConfigError, match="installation_id must be a number"):
        parse(text)


def test_an_installation_id_is_read_as_an_integer(tmp_path: Path) -> None:
    text = APP.format(key=tmp_path / "app.pem").replace(
        'app_id = "123456"', 'app_id = "1"\ninstallation_id = 98765'
    )

    assert parse(text).github.installation_id == 98765  # type: ignore[attr-defined]


# ---------------------------------------------------------------- gpus


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ('gpus = "all"', "all"),
        ('gpus = "ALL"', "all"),
        ("gpus = 2", 2),
        ('gpus = "2"', 2),
        ('gpus = ["0", "1"]', ("0", "1")),
        ("gpus = false", None),
        ('gpus = "none"', None),
    ],
)
def test_a_pool_can_ask_for_gpus(written: str, expected: object) -> None:
    settings = parse(
        MINIMAL.replace(
            'image = "ghspot/runner:ubuntu-24.04"',
            f'image = "ghspot/runner:ubuntu-24.04"\n{written}',
        )
    )

    assert settings.pools[0].template.gpus == expected  # type: ignore[attr-defined]


def test_pools_ask_for_no_gpu_unless_they_say_so() -> None:
    assert parse(MINIMAL).pools[0].template.gpus is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("written", ['gpus = "lots"', "gpus = []", "gpus = -1"])
def test_a_nonsense_gpu_selection_is_refused(written: str) -> None:
    with pytest.raises(ConfigError, match=r"(?i)gpu"):
        parse(
            MINIMAL.replace(
                'image = "ghspot/runner:ubuntu-24.04"',
                f'image = "ghspot/runner:ubuntu-24.04"\n{written}',
            )
        )


def test_asking_for_zero_gpus_means_none() -> None:
    """`0` and `false` say the same thing, and refusing one of them would be pedantry."""
    settings = parse(
        MINIMAL.replace(
            'image = "ghspot/runner:ubuntu-24.04"',
            'image = "ghspot/runner:ubuntu-24.04"\ngpus = 0',
        )
    )

    assert settings.pools[0].template.gpus is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------- requires_labels


def test_a_pool_can_demand_labels_be_asked_for() -> None:
    settings = parse(
        MINIMAL.replace(
            'labels = ["self-hosted", "linux"]',
            'labels = ["self-hosted", "linux", "gpu-a100"]\nrequires_labels = ["gpu-a100"]',
        )
    )

    required = settings.pools[0].spec.requires_labels  # type: ignore[attr-defined]
    assert required is not None
    assert required.as_list() == ["gpu-a100"]


def test_pools_demand_nothing_unless_they_say_so() -> None:
    assert parse(MINIMAL).pools[0].spec.requires_labels is None  # type: ignore[attr-defined]


def test_requiring_a_label_the_pool_does_not_carry_is_refused() -> None:
    with pytest.raises(ConfigError, match="could never serve"):
        parse(
            MINIMAL.replace(
                'labels = ["self-hosted", "linux"]',
                'labels = ["self-hosted", "linux"]\nrequires_labels = ["gpu-a100"]',
            )
        )


def test_requires_labels_must_be_a_list() -> None:
    with pytest.raises(ConfigError, match="must be a list"):
        parse(
            MINIMAL.replace(
                'labels = ["self-hosted", "linux"]',
                'labels = ["self-hosted", "linux"]\nrequires_labels = "gpu-a100"',
            )
        )
