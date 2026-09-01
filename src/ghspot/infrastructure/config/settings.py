"""Configuration loading.

A malformed ``config.toml`` must fail here, with a message naming the field, rather than
halfway through a reconciliation tick. Everything is parsed into domain value objects at load
time, so the pool invariants are checked before the daemon claims to have started.

The token is never a configuration *value*: it comes from a file or the environment, so it
cannot end up in a config file someone commits.
"""

from __future__ import annotations

import os
import re
import socket
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from ghspot.application.commands.housekeeping import parse_size
from ghspot.application.commands.provision import RunnerTemplate
from ghspot.application.reconciliation import PoolConfiguration
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.pool import PoolSpec, ProcessManager
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.policy.admission import CapacityLimits

TOKEN_ENV = "GHSPOT_GITHUB_TOKEN"
APP_ID_ENV = "GHSPOT_GITHUB_APP_ID"
APP_KEY_ENV = "GHSPOT_GITHUB_APP_PRIVATE_KEY"
CONFIG_ENV = "GHSPOT_CONFIG"

DEFAULT_CONFIG_PATHS = (
    Path("./config.toml"),
    Path("~/.config/ghspot/config.toml"),
    Path("/etc/ghspot/config.toml"),
)

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$", re.IGNORECASE)
_UNITS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


class ConfigError(GhSpotError):
    """The configuration file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class DaemonSettings:
    poll_interval: timedelta = timedelta(seconds=15)
    state_db: Path = Path("~/.local/state/ghspot/state.db")
    api_bind: str | None = None
    """``host:port`` to serve the REST API on, or ``None`` to run without it."""

    host: str = field(default_factory=socket.gethostname)
    """What to call this machine when reporting.

    Several hosts can serve one repository, and each daemon only ever sees its own runners —
    so every number this daemon reports is a number about *this* box, and a report that does
    not say which box that was is a report you cannot compare with the next one.

    Defaults to the system hostname. Settable because a hostname is often either meaningless
    (a cloud instance id) or not unique (a container's), and the name an operator uses for a
    machine is the one they want to read in a report.
    """

    stop_timeout: timedelta = timedelta(seconds=30)
    log_level: str = "INFO"
    log_format: str = "auto"
    """``auto``, ``console`` or ``json``."""


@dataclass(frozen=True, slots=True)
class HousekeepingSettings:
    """What the daemon reclaims from the host, and how often.

    Jobs reach the host's Docker daemon through the mounted socket, so what they build, pull
    and create outlives them. This bounds that; it does not eliminate it. A job that leaves a
    container *running* is never touched, because telling that apart from something the
    operator started deliberately is not possible.
    """

    enabled: bool = True
    every: timedelta = timedelta(hours=1)
    containers_older_than: timedelta | None = timedelta(hours=1)
    images_older_than: timedelta | None = timedelta(hours=24)
    volumes: bool = True
    build_cache_older_than: timedelta | None = timedelta(hours=24)
    keep_build_cache: str | None = "10g"
    """Build cache below this is kept, because discarding it makes every rebuild cold."""


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    """How to reach GitHub, and how to prove who we are.

    Two authentication modes. A GitHub App is preferred where available: its rate limit
    belongs to the installation rather than to a person, its permissions are the app's rather
    than everything the person can reach, and its tokens expire on their own.
    """

    api_url: str = "https://api.github.com"
    token_file: Path | None = None
    request_timeout: timedelta = timedelta(seconds=20)

    app_id: str | None = None
    private_key_file: Path | None = None
    installation_id: int | None = None
    """Optional: discovered from the app's installations when left unset."""

    @property
    def uses_app(self) -> bool:
        return self.app_id is not None

    def resolve_private_key(self) -> str:
        """The App private key, from the environment or the configured PEM file."""
        from_env = os.environ.get(APP_KEY_ENV, "").strip()
        if from_env:
            # systemd EnvironmentFile cannot hold newlines, so an escaped form is accepted.
            return from_env.replace("\\n", "\n")

        if self.private_key_file is None:
            raise ConfigError(
                f"a GitHub App needs a private key: set {APP_KEY_ENV} or [github].private_key_file"
            )

        path = self.private_key_file.expanduser()
        try:
            key = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(f"could not read the private key at {path}: {error}") from error

        if "PRIVATE KEY" not in key:
            raise ConfigError(
                f"{path} does not look like a PEM private key. Download it from the app's "
                "settings page under 'Private keys'."
            )
        _warn_if_world_readable(path)
        return key

    def resolve_token(self) -> str:
        """Read the token from the environment, else from ``token_file``.

        The environment wins so that a systemd unit can inject it without a file on disk.
        """
        from_env = os.environ.get(TOKEN_ENV, "").strip()
        if from_env:
            return from_env

        if self.token_file is None:
            raise ConfigError(
                f"no GitHub token: set {TOKEN_ENV} or point [github].token_file at a file"
            )

        path = self.token_file.expanduser()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ConfigError(f"could not read the token from {path}: {error}") from error

        if not token:
            raise ConfigError(f"the token file {path} is empty")
        _warn_if_world_readable(path)
        return token


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the daemon needs, validated."""

    github: GitHubSettings
    daemon: DaemonSettings
    housekeeping: HousekeepingSettings = field(default_factory=HousekeepingSettings)
    capacity: CapacityLimits = field(default_factory=CapacityLimits)
    pools: tuple[PoolConfiguration, ...] = field(default=())
    source: Path | None = None

    sources: tuple[Path, ...] = ()
    """Every file that was read, including the pool files an `include` pulled in."""

    loaded_mtime: float = 0.0
    """Newest mtime across `sources` when this was loaded, for spotting later edits."""

    @property
    def repositories(self) -> list[RepositoryTarget]:
        seen: dict[RepositoryTarget, None] = {}
        for pool in self.pools:
            seen.setdefault(pool.spec.repository, None)
        return list(seen)


#: Top-level keys a pool file may not carry. Global configuration belongs in the main file:
#: otherwise which file wins becomes a question, and the answer is never obvious from either
#: of them.
GLOBAL_SECTIONS = ("github", "daemon", "housekeeping", "include")


PLACEHOLDER_REPOSITORY = "OWNER/REPOSITORY"
"""What the packaged configuration ships with, so a fresh install is recognisable as one."""


def unconfigured(settings: Settings) -> str | None:
    """Why this host is not ready to run anything, or ``None`` when it is.

    Distinct from `ghspot doctor`, which asks whether everything *works* — Docker, the
    image, the token's permissions. This asks the narrower question the installer cares
    about: has anyone finished filling the file in. A daemon can be up, answering, and still
    be pointing at OWNER/REPOSITORY.
    """
    for pool in settings.pools:
        if str(pool.spec.repository) == PLACEHOLDER_REPOSITORY:
            return f"pool {pool.spec.name!r} still points at the packaged {PLACEHOLDER_REPOSITORY}"

    github = settings.github
    try:
        if github.uses_app:
            github.resolve_private_key()
        else:
            github.resolve_token()
    except GhSpotError as error:
        return str(error)
    return None


def load(path: Path | str | None = None) -> Settings:
    """Load and validate configuration, searching the usual places if no path is given."""
    resolved = _locate(path)
    raw = _read(resolved)
    included = _include(raw, resolved)
    settings = from_mapping(raw, source=resolved, included=included)

    # Every file that contributed, so the daemon can later notice it is running configuration
    # somebody has since edited. Pools live in their own files too, and a change to one of
    # those is exactly as invisible as a change to the main one.
    read = (resolved, *(origin for _table, origin in included))
    return replace(settings, sources=read, loaded_mtime=_newest_mtime(read))


def _newest_mtime(paths: Sequence[Path]) -> float:
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def changed_on_disk(settings: Settings) -> bool:
    """Whether the configuration has been edited since the daemon read it.

    The daemon builds everything from settings once, at startup: pools, labels, the forge
    client, the backend. Editing the file changes none of it, and nothing said so — the
    operator adds a label, watches the dashboard keep showing the old one, and has no way to
    tell a stale process from a mistake in the file.
    """
    if not settings.sources:
        return False
    return _newest_mtime(settings.sources) > settings.loaded_mtime


def from_mapping(
    raw: dict[str, Any],
    source: Path | None = None,
    included: Sequence[tuple[dict[str, Any], Path]] = (),
) -> Settings:
    """Build settings from an already-parsed mapping. Used by tests and by ``load``."""
    github = _github(_section(raw, "github"))
    daemon = _daemon(_section(raw, "daemon"))
    housekeeping = _housekeeping(_section(raw, "housekeeping"))

    here = source or Path("configuration")
    defined: list[tuple[dict[str, Any], Path]] = [
        (table, here) for table in _pool_tables(raw, here)
    ]
    defined += list(included)

    if not defined:
        raise ConfigError("at least one [[pool]] must be configured")

    pools = tuple(_pool(table, index) for index, (table, _origin) in enumerate(defined))
    _reject_duplicate_names(pools, [origin for _table, origin in defined])

    return Settings(
        github=github,
        daemon=daemon,
        housekeeping=housekeeping,
        capacity=_capacity(_section(raw, "capacity")),
        pools=pools,
        source=source,
    )


def _pool_tables(raw: dict[str, Any], origin: Path) -> list[dict[str, Any]]:
    tables = raw.get("pool", [])
    if isinstance(tables, dict):  # a single [pool] table rather than [[pool]]
        tables = [tables]
    if not isinstance(tables, list):
        raise ConfigError(f"{origin}: 'pool' must be a list of [[pool]] tables")
    return tables


def _read(path: Path) -> dict[str, Any]:
    try:
        parsed: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"could not read {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    return parsed


def _include(raw: dict[str, Any], source: Path) -> list[tuple[dict[str, Any], Path]]:
    """Expand ``include`` into the pools its glob matches.

        include = "pools.d/*.toml"

    Four rules:

    * The glob is expanded and the matches are **sorted**, so the fleet a host ends up with
      does not depend on the order a directory happens to return.
    * Files are **merged, never overridden**. Every pool found is a pool that runs; there is
      no last-one-wins, because a pool silently replaced by a file later in the alphabet is
      not a thing anyone would debug quickly.
    * A **duplicate name is fatal**, naming both files.
    * A pool file carries **pools only**. Global sections belong in the main file.

    A pattern that matches nothing is not an error by itself — an empty ``pools.d`` on a host
    still being set up is a normal state, and the "at least one pool" check already covers
    the case where that leaves nothing at all.
    """
    patterns = raw.get("include")
    if patterns in (None, "", []):
        _reject_misplaced_include(raw, source)
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not all(isinstance(one, str) for one in patterns):
        raise ConfigError("'include' must be a glob, or a list of globs")

    found: list[tuple[dict[str, Any], Path]] = []
    for pattern in patterns:
        for path in _matches(str(pattern), source):
            parsed = _read(path)
            for key in GLOBAL_SECTIONS:
                if key in parsed:
                    raise ConfigError(
                        f"{path}: [{key}] belongs in the main configuration file, not in an "
                        "included one. Included files define pools."
                    )
            tables = _pool_tables(parsed, path)
            if not tables:
                raise ConfigError(f"{path}: an included file must define at least one [[pool]]")
            found += [(table, path) for table in tables]
    return found


def _reject_misplaced_include(raw: dict[str, Any], source: Path) -> None:
    """Catch ``include`` written below a table header, where TOML swallows it.

    A bare key belongs to whatever table precedes it, so this:

        [github]
        token_file = "..."
        include = "pools.d/*.toml"

    is `github.include`, not an include — and without this it would load, run, and quietly
    serve none of the pools the operator wrote. A misplaced directive that does nothing is
    the worst outcome available, so it is an error that says where to move it.
    """
    for name, section in raw.items():
        if isinstance(section, dict) and "include" in section:
            raise ConfigError(
                f"{source}: 'include' is inside [{name}], where TOML puts any key written "
                f"below a table header. Move it above the first [section] in the file."
            )


def _matches(pattern: str, source: Path) -> list[Path]:
    """Files a pattern names, sorted. Relative patterns resolve beside the main file."""
    candidate = Path(pattern).expanduser()
    if candidate.is_absolute():
        root, glob = Path(candidate.anchor), str(candidate.relative_to(candidate.anchor))
    else:
        root, glob = source.resolve().parent, str(candidate)

    try:
        return sorted(path for path in root.glob(glob) if path.is_file())
    except (OSError, ValueError) as error:
        raise ConfigError(
            f"'include' pattern {pattern!r} could not be expanded: {error}"
        ) from error


# -- sections ------------------------------------------------------------------------


def _github(table: dict[str, Any]) -> GitHubSettings:
    token_file = table.get("token_file")
    private_key_file = table.get("private_key_file")
    app_id = table.get("app_id") or os.environ.get(APP_ID_ENV) or None

    installation_id = table.get("installation_id")
    if installation_id is not None:
        try:
            installation_id = int(installation_id)
        except (TypeError, ValueError) as error:
            raise ConfigError("github.installation_id must be a number") from error

    if app_id is None and private_key_file is not None:
        raise ConfigError(
            "[github].private_key_file is set but app_id is not. A GitHub App needs both."
        )

    return GitHubSettings(
        api_url=str(table.get("api_url", "https://api.github.com")).rstrip("/"),
        token_file=Path(str(token_file)).expanduser() if token_file else None,
        request_timeout=parse_duration(
            table.get("request_timeout", "20s"), "github.request_timeout"
        ),
        app_id=str(app_id) if app_id else None,
        private_key_file=(Path(str(private_key_file)).expanduser() if private_key_file else None),
        installation_id=installation_id,
    )


def _daemon(table: dict[str, Any]) -> DaemonSettings:
    log_format = str(table.get("log_format", "auto")).lower()
    if log_format not in {"auto", "console", "json"}:
        raise ConfigError("daemon.log_format must be 'auto', 'console' or 'json'")

    # An empty string is a mistake, not a choice: it would leave every report unlabelled,
    # which is the exact thing naming the host is for.
    host = str(table.get("host") or socket.gethostname()).strip()
    if not host:
        raise ConfigError("daemon.host cannot be empty")

    return DaemonSettings(
        poll_interval=parse_duration(table.get("poll_interval", "15s"), "daemon.poll_interval"),
        state_db=Path(str(table.get("state_db", "~/.local/state/ghspot/state.db"))).expanduser(),
        api_bind=str(table["api_bind"]) if table.get("api_bind") else None,
        stop_timeout=parse_duration(table.get("stop_timeout", "30s"), "daemon.stop_timeout"),
        log_level=str(table.get("log_level", "INFO")).upper(),
        log_format=log_format,
        host=host,
    )


def _housekeeping(table: dict[str, Any]) -> HousekeepingSettings:
    def age(key: str, default: str | None) -> timedelta | None:
        value = table.get(key, default)
        if value in (None, False, "never", ""):
            return None
        return parse_duration(value, f"housekeeping.{key}")

    return HousekeepingSettings(
        enabled=bool(table.get("enabled", True)),
        every=parse_duration(table.get("every", "1h"), "housekeeping.every"),
        containers_older_than=age("containers_older_than", "1h"),
        images_older_than=age("images_older_than", "24h"),
        volumes=bool(table.get("volumes", True)),
        build_cache_older_than=age("build_cache_older_than", "24h"),
        keep_build_cache=(
            str(table["keep_build_cache"]) if table.get("keep_build_cache") else None
        ),
    )


def _priority(value: Any, where: str, name: str) -> int:
    """A pool's share of contested capacity. A weight, so the floor is 1, not 0.

    Zero is refused rather than quietly treated as one: a weight of nothing has no meaning
    in a proportional split, and somebody writing it means "never", which is spelled by
    giving the other pools a much larger number.
    """
    if _unset(value):
        return 1
    try:
        weight = int(str(value))
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{where} ({name}): 'priority' must be a whole number") from error
    if weight < 1:
        raise ConfigError(
            f"{where} ({name}): 'priority' is a share of contested capacity, so it starts "
            "at 1. A pool at 10 gets twice the slots of one at 5, not all of them."
        )
    return weight


UNLIMITED = "unlimited"
"""Written where a number would go, to lift a ceiling that otherwise has a default."""


def host_cores() -> int:
    """Cores this machine reports, and never less than one.

    A module-level function so a test can replace it: the default it feeds is a number the
    suite has to be able to pin down.
    """
    return os.cpu_count() or 1


def _says_unlimited(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == UNLIMITED


def _unset(value: Any) -> bool:
    """Whether a key was left out, as opposed to given a value.

    Written with identity checks because ``0 == False`` in Python: ``value in (None, False)``
    would read ``max_containers = 0`` as "not configured", which is the opposite of what
    anyone writing that means, and it would be silent.
    """
    return value is None or value is False or (isinstance(value, str) and not value.strip())


def _capacity(table: dict[str, Any]) -> CapacityLimits:
    """Ceilings on what the host may commit, and how hard it may already be working.

    Everything is optional and unset means unlimited, so a configuration written before this
    section existed behaves exactly as it did.
    """

    def count(key: str) -> int | None:
        value = table.get(key)
        if isinstance(value, str) and value.strip().lower() == UNLIMITED:
            return None
        if _unset(value):
            return None
        number = int(str(value))
        if number < 1:
            raise ConfigError(f"capacity.{key} must be at least 1, or unset for no limit")
        return number

    def cpus(key: str) -> float | None:
        value = table.get(key)
        if _unset(value):
            return None
        number = float(str(value))
        if number <= 0:
            raise ConfigError(f"capacity.{key} must be greater than 0, or unset for no limit")
        return number

    def water(key: str) -> float | None:
        value = table.get(key)
        if _unset(value):
            return None
        number = float(str(value))
        if not 1 <= number <= 100:
            raise ConfigError(f"capacity.{key} is a percentage: use a number between 1 and 100")
        return number

    memory = table.get("max_memory")
    try:
        limit = None if _unset(memory) else parse_size(str(memory))
    except ValueError as error:
        raise ConfigError(f"capacity.max_memory: {error}") from error

    containers = count("max_containers")
    if containers is None and not _says_unlimited(table.get("max_containers")):
        # One runner per core, unless told otherwise. A host with no ceiling at all will
        # cheerfully start a container per queued job until it stops responding, and the
        # first thing an operator learns is that the machine is gone. Cores is not a
        # measurement of anything — it is a defensible number the box can name for itself.
        containers = host_cores()

    try:
        return CapacityLimits(
            max_containers=containers,
            max_cpus=cpus("max_cpus"),
            max_memory_bytes=limit,
            cpu_high_water=water("cpu_high_water"),
            memory_high_water=water("memory_high_water"),
            disk_high_water=water("disk_high_water"),
        )
    except (TypeError, ValueError) as error:
        raise ConfigError(f"capacity: {error}") from error


def _pool(table: dict[str, Any], index: int) -> PoolConfiguration:
    where = f"pool[{index}]"
    name = _required(table, "name", where)
    repository = _required(table, "repository", where)
    labels = table.get("labels")
    if not isinstance(labels, list) or not labels:
        raise ConfigError(f"{where} ({name}): 'labels' must be a non-empty list")

    container = table.get("container", {})
    if not isinstance(container, dict):
        raise ConfigError(f"{where} ({name}): [pool.container] must be a table")

    image = container.get("image")
    if not image:
        raise ConfigError(f"{where} ({name}): [pool.container] needs an 'image'")

    try:
        spec = PoolSpec(
            name=str(name),
            repository=RepositoryTarget.parse(str(repository)),
            labels=LabelSet.from_iterable(str(label) for label in labels),
            max_runners=int(table.get("max_runners", 2)),
            idle_timeout=parse_duration(table.get("idle_timeout", "10m"), f"{where}.idle_timeout"),
            max_job_duration=parse_duration(
                table.get("max_job_duration", "2h"), f"{where}.max_job_duration"
            ),
            max_launch_per_tick=int(table.get("max_launch_per_tick", 2)),
            **_process_manager(table, where, str(name)),
            priority=_priority(table.get("priority"), where, str(name)),
            requires_labels=_required_labels(table.get("requires_labels"), where, str(name)),
        )
    except GhSpotError as error:
        raise ConfigError(f"{where}: {error}") from error
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{where} ({name}): {error}") from error

    return PoolConfiguration(spec=spec, template=_template(container, where, str(name)))


def _template(container: dict[str, Any], where: str, name: str) -> RunnerTemplate:
    volumes = container.get("volumes", {})
    if not isinstance(volumes, dict):
        raise ConfigError(f"{where} ({name}): 'volumes' must be a table of host = container")

    environment = container.get("environment", {})
    if not isinstance(environment, dict):
        raise ConfigError(f"{where} ({name}): 'environment' must be a table")
    _reject_credential_environment(environment, where, name)

    cpus = container.get("cpus")
    return RunnerTemplate(
        gpus=_gpus(container.get("gpus"), where, name),
        image=str(container["image"]),
        cpus=float(cpus) if cpus is not None else None,
        memory=str(container["memory"]) if container.get("memory") else None,
        mount_docker_socket=bool(container.get("docker_socket", False)),
        volumes={str(key): str(value) for key, value in volumes.items()},
        network=str(container["network"]) if container.get("network") else None,
        environment={str(key): str(value) for key, value in environment.items()},
    )


# -- helpers -------------------------------------------------------------------------


def _process_manager(table: dict[str, Any], where: str, name: str) -> dict[str, Any]:
    """Read `pm` and the knobs that belong to it.

    Knobs that do not apply to the chosen mode are refused rather than ignored. A setting
    that is quietly doing nothing is worse than one that will not load: the pool behaves
    unlike its configuration and nothing says so.
    """
    written = table.get("pm")
    if written in (None, ""):
        mode = ProcessManager.DYNAMIC
    else:
        try:
            mode = ProcessManager(str(written).strip().lower())
        except ValueError:
            allowed = ", ".join(one.value for one in ProcessManager)
            raise ConfigError(
                f"{where} ({name}): {written!r} is not a process manager. Use one of: {allowed}"
            ) from None

    def refuse(keys: tuple[str, ...]) -> None:
        for key in keys:
            if key in table:
                raise ConfigError(
                    f"{where} ({name}): '{key}' does nothing under pm = \"{mode.value}\". "
                    + _PM_ADVICE[mode]
                )

    if mode is ProcessManager.STATIC:
        refuse(("min_idle", "max_idle", "idle_timeout"))
        return {"pm": mode}

    if mode is ProcessManager.ONDEMAND:
        refuse(("min_idle", "max_idle"))
        return {"pm": mode}

    min_idle = int(table.get("min_idle", 0))
    max_idle = table.get("max_idle")
    if max_idle in (None, ""):
        return {"pm": mode, "min_idle": min_idle}

    ceiling = int(max_idle)
    if ceiling < min_idle:
        raise ConfigError(
            f"{where} ({name}): max_idle={ceiling} is below min_idle={min_idle}, so the pool "
            "would start runners and immediately reap them"
        )
    return {"pm": mode, "min_idle": min_idle, "max_idle": ceiling}


_PM_ADVICE = {
    ProcessManager.STATIC: (
        'static keeps exactly max_runners up and never reaps them; use pm = "dynamic" for a band.'
    ),
    ProcessManager.ONDEMAND: (
        "ondemand keeps nothing warm; idle_timeout still decides how long a spent runner lingers."
    ),
    ProcessManager.DYNAMIC: "",
}


def _required_labels(value: Any, where: str, name: str) -> LabelSet | None:
    """Labels a job must ask for by name before this pool will serve it."""
    if value in (None, False, "", []):
        return None
    if not isinstance(value, list):
        raise ConfigError(f"{where} ({name}): 'requires_labels' must be a list")
    try:
        return LabelSet.from_iterable(str(label) for label in value)
    except GhSpotError as error:
        raise ConfigError(f"{where} ({name}): {error}") from error


def _gpus(value: Any, where: str, name: str) -> str | int | tuple[str, ...] | None:
    """Read a pool's GPU selection: ``"all"``, a count, or a list of device ids."""
    if value in (None, False, "", "none"):
        return None
    if isinstance(value, bool):
        return "all" if value else None
    if isinstance(value, int):
        if value < 1:
            raise ConfigError(f"{where} ({name}): 'gpus' must be at least 1, or \"all\"")
        return value
    if isinstance(value, list):
        ids = tuple(str(item) for item in value if str(item).strip())
        if not ids:
            raise ConfigError(f"{where} ({name}): 'gpus' is an empty list")
        return ids
    text = str(value).strip()
    if text.casefold() == "all":
        return "all"
    if text.isdigit():
        return int(text)
    raise ConfigError(
        f"{where} ({name}): {value!r} is not a GPU selection. "
        'Use "all", a count, or a list of device ids.'
    )


def _locate(path: Path | str | None) -> Path:
    if path is not None:
        resolved = Path(path).expanduser()
        if not resolved.is_file():
            raise ConfigError(f"no configuration file at {resolved}")
        return resolved

    from_env = os.environ.get(CONFIG_ENV)
    if from_env:
        return _locate(from_env)

    for candidate in DEFAULT_CONFIG_PATHS:
        expanded = candidate.expanduser()
        if expanded.is_file():
            return expanded

    searched = ", ".join(str(candidate) for candidate in DEFAULT_CONFIG_PATHS)
    raise ConfigError(f"no configuration file found. Looked in: {searched}")


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table")
    return table


def _required(table: dict[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not value:
        raise ConfigError(f"{where}: '{key}' is required")
    return str(value)


def parse_duration(value: Any, where: str) -> timedelta:
    """Parse ``15s``, ``10m``, ``2h``, or a bare number of seconds.

    Public because `ghspot stats --since` takes the same grammar the configuration does, and
    two implementations of "10m" would eventually disagree.
    """
    if isinstance(value, timedelta):
        return value
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))

    match = _DURATION.match(str(value))
    if not match:
        raise ConfigError(
            f"{where}: {value!r} is not a duration. Use forms like '30s', '10m', '2h'."
        )
    amount, unit = match.groups()
    return timedelta(seconds=float(amount) * _UNITS[(unit or "s").lower()])


def _reject_duplicate_names(
    pools: tuple[PoolConfiguration, ...], origins: Sequence[Path] = ()
) -> None:
    """Two pools of one name is fatal, and the message names both files.

    Refusing to start beats picking one: which definition won would be invisible in the
    running fleet.
    """
    if origins and len(origins) == len(pools):
        first: dict[str, Path] = {}
        for pool, origin in zip(pools, origins, strict=True):
            name = pool.spec.name
            if name in first:
                raise ConfigError(f"two pools are both named {name!r}: {first[name]} and {origin}")
            first[name] = origin
        return

    seen: set[str] = set()
    for pool in pools:
        if pool.spec.name in seen:
            raise ConfigError(f"two pools are both named {pool.spec.name!r}")
        seen.add(pool.spec.name)


def _reject_credential_environment(environment: dict[str, Any], where: str, name: str) -> None:
    """Refuse to pass anything token-shaped into a runner container.

    The single property this project is built around is that a job never sees a credential.
    Configuration should not be able to undo it by accident.
    """
    suspicious = sorted(
        key
        for key in environment
        if any(word in str(key).casefold() for word in ("token", "secret", "password", "jitconfig"))
    )
    if suspicious:
        raise ConfigError(
            f"{where} ({name}): refusing to put {', '.join(suspicious)} into a runner "
            "container. Job secrets belong in GitHub Actions secrets, not in the runner's "
            "environment."
        )


#: The account the systemd unit runs as. A credential it cannot read stops the daemon dead,
#: so group-readable *by this group* is the packaged layout rather than a mistake.
SERVICE_GROUP = "ghspot"


def _warn_if_world_readable(path: Path) -> None:
    """Complain about a credential more people can read than need to.

    Not simply "anything but 0600". The package deliberately installs credentials 0640
    root:ghspot, because the daemon runs as ``ghspot`` and a file only root can read stops it
    starting. Warning about that told an operator to run ``chmod 600`` — which would break the
    very service the check exists to protect.
    """
    try:
        info = path.stat()
    except OSError:
        return

    mode = info.st_mode
    if mode & 0o007:
        complaint = "is readable by everybody"
    elif mode & 0o020:
        complaint = "is writable by its group"
    elif mode & 0o040 and not _is_service_group(info.st_gid):
        complaint = "is readable by a group other than the daemon's"
    else:
        return

    import warnings

    warnings.warn(
        f"{path} {complaint}; run: chown root:{SERVICE_GROUP} {path} && chmod 640 {path}",
        stacklevel=2,
    )


def _is_service_group(gid: int) -> bool:
    try:
        import grp

        return bool(grp.getgrnam(SERVICE_GROUP).gr_gid == gid)
    except (KeyError, ImportError):
        # No packaged service account here, so there is no group that legitimately needs it.
        return False
