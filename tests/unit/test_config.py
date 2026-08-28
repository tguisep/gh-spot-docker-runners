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


# ---------------------------------------------------------------- pm


def with_pool(*lines: str) -> str:
    return MINIMAL.replace(
        'labels = ["self-hosted", "linux"]',
        'labels = ["self-hosted", "linux"]\n' + "\n".join(lines),
    )


def test_a_pool_manages_its_runners_dynamically_unless_it_says_otherwise() -> None:
    assert parse(MINIMAL).pools[0].spec.pm.value == "dynamic"  # type: ignore[attr-defined]


@pytest.mark.parametrize("mode", ["static", "dynamic", "ondemand"])
def test_every_mode_is_accepted(mode: str) -> None:
    settings = parse(with_pool(f'pm = "{mode}"'))

    assert settings.pools[0].spec.pm.value == mode  # type: ignore[attr-defined]


def test_a_mode_that_does_not_exist_lists_the_ones_that_do() -> None:
    with pytest.raises(ConfigError, match="static, dynamic, ondemand"):
        parse(with_pool('pm = "adaptive"'))


@pytest.mark.parametrize("key", ["min_idle = 2", "max_idle = 2", 'idle_timeout = "5m"'])
def test_static_refuses_the_knobs_that_would_do_nothing(key: str) -> None:
    """php-fpm refuses pm.min_spare_servers under static for the same reason: a setting that
    is quietly doing nothing is worse than one that will not load."""
    with pytest.raises(ConfigError, match="does nothing under"):
        parse(with_pool('pm = "static"', key))


@pytest.mark.parametrize("key", ["min_idle = 2", "max_idle = 2"])
def test_ondemand_refuses_the_warm_pool_knobs(key: str) -> None:
    with pytest.raises(ConfigError, match="does nothing under"):
        parse(with_pool('pm = "ondemand"', key))


def test_ondemand_keeps_its_idle_timeout() -> None:
    """It decides how long a spent runner lingers, which ondemand very much still has."""
    settings = parse(with_pool('pm = "ondemand"', 'idle_timeout = "3m"'))

    assert settings.pools[0].spec.idle_timeout.total_seconds() == 180  # type: ignore[attr-defined]


def test_dynamic_takes_a_band() -> None:
    settings = parse(with_pool('pm = "dynamic"', "min_idle = 1", "max_idle = 4"))

    spec = settings.pools[0].spec  # type: ignore[attr-defined]
    assert (spec.min_idle, spec.max_idle) == (1, 4)


def test_a_band_that_is_upside_down_is_refused() -> None:
    """The pool would start runners to reach min_idle and reap them for exceeding max_idle,
    every tick, forever."""
    with pytest.raises(ConfigError, match="below min_idle"):
        parse(with_pool("min_idle = 4", "max_idle = 2"))


# ---------------------------------------------------------------- include


# `include` sits above the first table on purpose: in TOML a bare key belongs to whichever
# table precedes it, so written lower down it would become `github.include`.
MAIN = """
include = "pools.d/*.toml"

[github]
token_file = "/tmp/token"
"""

POOL_FILE = """
[[pool]]
name = "{name}"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""


def write_tree(root: Path, main: str = MAIN, **files: str) -> Path:
    """A main configuration file with a pools.d beside it."""
    config = root / "config.toml"
    config.write_text(main)
    pools = root / "pools.d"
    pools.mkdir(exist_ok=True)
    for name, text in files.items():
        (pools / f"{name}.toml").write_text(text)
    return config


def test_pools_are_gathered_from_the_included_directory(tmp_path: Path) -> None:
    config = write_tree(
        tmp_path, web=POOL_FILE.format(name="web"), gpu=POOL_FILE.format(name="gpu")
    )

    settings = load(config)

    assert {pool.spec.name for pool in settings.pools} == {"web", "gpu"}


def test_files_are_merged_rather_than_overriding_one_another(tmp_path: Path) -> None:
    """Every pool found is a pool that runs. A definition silently replaced by a file later
    in the alphabet is not something anyone would debug quickly."""
    main = MAIN + POOL_FILE.format(name="in-the-main-file")
    config = write_tree(tmp_path, main=main, extra=POOL_FILE.format(name="from-pools-d"))

    settings = load(config)

    assert {pool.spec.name for pool in settings.pools} == {"in-the-main-file", "from-pools-d"}


def test_a_duplicate_name_is_fatal_and_names_both_files(tmp_path: Path) -> None:
    config = write_tree(tmp_path, a=POOL_FILE.format(name="web"), b=POOL_FILE.format(name="web"))

    with pytest.raises(ConfigError, match="two pools are both named 'web'") as raised:
        load(config)

    assert "a.toml" in str(raised.value) and "b.toml" in str(raised.value)


def test_a_pool_defined_twice_across_the_main_file_and_a_pool_file_is_fatal(
    tmp_path: Path,
) -> None:
    main = MAIN + POOL_FILE.format(name="web")
    config = write_tree(tmp_path, main=main, web=POOL_FILE.format(name="web"))

    with pytest.raises(ConfigError, match="two pools are both named 'web'"):
        load(config)


def test_files_are_read_in_sorted_order(tmp_path: Path) -> None:
    """So the fleet a host ends up with does not depend on what order a directory returns."""
    config = write_tree(
        tmp_path,
        zulu=POOL_FILE.format(name="zulu"),
        alpha=POOL_FILE.format(name="alpha"),
        mike=POOL_FILE.format(name="mike"),
    )

    names = [pool.spec.name for pool in load(config).pools]

    assert names == ["alpha", "mike", "zulu"]


@pytest.mark.parametrize(
    "section",
    [
        '[github]\ntoken_file = "/tmp/t"',
        '[daemon]\npoll_interval = "30s"',
        'include = "other/*.toml"',
    ],
)
def test_global_configuration_is_refused_in_a_pool_file(tmp_path: Path, section: str) -> None:
    """php-fpm keeps [global] out of php-fpm.d for the same reason: otherwise which file wins
    becomes a question, and the answer is not obvious from either of them."""
    config = write_tree(tmp_path, web=section + "\n" + POOL_FILE.format(name="web"))

    with pytest.raises(ConfigError, match="belongs in the main configuration file"):
        load(config)


def test_an_included_file_with_no_pool_is_refused(tmp_path: Path) -> None:
    """It is almost always a typo in a file whose whole purpose is to define one."""
    config = write_tree(tmp_path, empty="# nothing here yet\n")

    with pytest.raises(ConfigError, match="at least one"):
        load(config)


def test_a_pattern_matching_nothing_is_not_an_error_by_itself(tmp_path: Path) -> None:
    """An empty pools.d on a host still being set up is a normal state."""
    main = MAIN + POOL_FILE.format(name="only-one")
    config = write_tree(tmp_path, main=main)

    assert [pool.spec.name for pool in load(config).pools] == ["only-one"]


def test_no_pools_anywhere_still_says_so(tmp_path: Path) -> None:
    config = write_tree(tmp_path)

    with pytest.raises(ConfigError, match="at least one"):
        load(config)


def test_a_relative_pattern_resolves_beside_the_main_file(tmp_path: Path) -> None:
    """Not against the working directory: a daemon started by systemd has no useful one."""
    nested = tmp_path / "etc"
    nested.mkdir()
    config = write_tree(nested, web=POOL_FILE.format(name="web"))

    assert [pool.spec.name for pool in load(config).pools] == ["web"]


def test_several_patterns_are_allowed(tmp_path: Path) -> None:
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "gpu.toml").write_text(POOL_FILE.format(name="gpu"))
    main = MAIN.replace(
        'include = "pools.d/*.toml"', 'include = ["pools.d/*.toml", "extra/*.toml"]'
    )
    config = write_tree(tmp_path, main=main, web=POOL_FILE.format(name="web"))

    assert {pool.spec.name for pool in load(config).pools} == {"web", "gpu"}


def test_an_include_written_below_a_table_header_is_refused(tmp_path: Path) -> None:
    """TOML would make it `github.include`, and the daemon would start with none of the pools
    the operator wrote. A directive that silently does nothing is the worst outcome here."""
    main = '[github]\ntoken_file = "/tmp/token"\ninclude = "pools.d/*.toml"\n'
    config = write_tree(tmp_path, main=main, web=POOL_FILE.format(name="web"))

    with pytest.raises(ConfigError, match="Move it above the first"):
        load(config)


# ---------------------------------------------------------------- capacity


def test_a_host_with_no_capacity_section_is_unlimited() -> None:
    """Configuration written before this section existed must behave as it always did."""
    capacity = parse(MINIMAL).capacity  # type: ignore[attr-defined]

    assert capacity.max_containers is None
    assert capacity.max_cpus is None
    assert capacity.max_memory_bytes is None
    assert capacity.has_backpressure is False


def test_every_ceiling_is_read() -> None:
    settings = parse(
        MINIMAL
        + """
[capacity]
max_containers = 8
max_cpus = 12.5
max_memory = "24g"
cpu_high_water = 85
memory_high_water = 90
"""
    )

    capacity = settings.capacity  # type: ignore[attr-defined]
    assert capacity.max_containers == 8
    assert capacity.max_cpus == 12.5
    assert capacity.max_memory_bytes == 24 * 1024**3
    assert capacity.cpu_high_water == 85
    assert capacity.memory_high_water == 90


@pytest.mark.parametrize(
    "written",
    [
        "max_containers = 0",
        "max_cpus = 0",
        "cpu_high_water = 0",
        "cpu_high_water = 140",
        'max_memory = "lots"',
    ],
)
def test_a_nonsense_limit_is_refused_at_load_time(written: str) -> None:
    with pytest.raises(ConfigError, match=r"(?i)capacity"):
        parse(MINIMAL + f"\n[capacity]\n{written}\n")


def test_a_pool_can_be_given_a_priority() -> None:
    settings = parse(
        MINIMAL.replace(
            'labels = ["self-hosted", "linux"]', 'labels = ["self-hosted", "linux"]\npriority = 10'
        )
    )

    assert settings.pools[0].spec.priority == 10  # type: ignore[attr-defined]


def test_pools_share_a_priority_unless_they_say_otherwise() -> None:
    assert parse(MINIMAL).pools[0].spec.priority == 0  # type: ignore[attr-defined]
