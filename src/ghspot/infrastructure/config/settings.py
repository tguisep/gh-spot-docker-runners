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
import tomllib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from ghspot.application.commands.provision import RunnerTemplate
from ghspot.application.reconciliation import PoolConfiguration
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.target import RepositoryTarget

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


#: systemd sets this when a unit declares StateDirectory=. Honouring it is what keeps the
#: unit and the daemon agreeing about where state lives without the operator restating it.
STATE_DIRECTORY_ENV = "STATE_DIRECTORY"


def default_state_db() -> Path:
    """Where the projection lives when the configuration does not say.

    Under systemd that is the directory the unit already had created for it. Run by hand it
    is the usual per-user location. Getting this wrong is not obvious: the daemon starts,
    then fails on every tick trying to write somewhere ProtectSystem has made read-only.
    """
    managed = os.environ.get(STATE_DIRECTORY_ENV, "").strip()
    if managed:
        # systemd may hand over a colon-separated list; the first entry is ours.
        return Path(managed.split(":")[0]) / "state.db"
    return Path("~/.local/state/ghspot/state.db").expanduser()


@dataclass(frozen=True, slots=True)
class DaemonSettings:
    poll_interval: timedelta = timedelta(seconds=15)
    state_db: Path = field(default_factory=default_state_db)
    api_bind: str | None = None
    """``host:port`` to serve the REST API on, or ``None`` to run without it."""

    stop_timeout: timedelta = timedelta(seconds=30)
    log_level: str = "INFO"
    log_format: str = "auto"
    """``auto``, ``console`` or ``json``."""


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
    pools: tuple[PoolConfiguration, ...] = field(default=())
    source: Path | None = None

    @property
    def repositories(self) -> list[RepositoryTarget]:
        seen: dict[RepositoryTarget, None] = {}
        for pool in self.pools:
            seen.setdefault(pool.spec.repository, None)
        return list(seen)


def load(path: Path | str | None = None) -> Settings:
    """Load and validate configuration, searching the usual places if no path is given."""
    resolved = _locate(path)
    try:
        raw = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"could not read {resolved}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{resolved} is not valid TOML: {error}") from error

    return from_mapping(raw, source=resolved)


def from_mapping(raw: dict[str, Any], source: Path | None = None) -> Settings:
    """Build settings from an already-parsed mapping. Used by tests and by ``load``."""
    github = _github(_section(raw, "github"))
    daemon = _daemon(_section(raw, "daemon"))

    pool_tables = raw.get("pool", [])
    if isinstance(pool_tables, dict):  # a single [pool] table rather than [[pool]]
        pool_tables = [pool_tables]
    if not isinstance(pool_tables, list) or not pool_tables:
        raise ConfigError("at least one [[pool]] must be configured")

    pools = tuple(_pool(table, index) for index, table in enumerate(pool_tables))
    _reject_duplicate_names(pools)

    return Settings(github=github, daemon=daemon, pools=pools, source=source)


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
        request_timeout=_duration(table.get("request_timeout", "20s"), "github.request_timeout"),
        app_id=str(app_id) if app_id else None,
        private_key_file=(Path(str(private_key_file)).expanduser() if private_key_file else None),
        installation_id=installation_id,
    )


def _daemon(table: dict[str, Any]) -> DaemonSettings:
    log_format = str(table.get("log_format", "auto")).lower()
    if log_format not in {"auto", "console", "json"}:
        raise ConfigError("daemon.log_format must be 'auto', 'console' or 'json'")

    return DaemonSettings(
        poll_interval=_duration(table.get("poll_interval", "15s"), "daemon.poll_interval"),
        state_db=(
            Path(str(table["state_db"])).expanduser()
            if table.get("state_db")
            else default_state_db()
        ),
        api_bind=str(table["api_bind"]) if table.get("api_bind") else None,
        stop_timeout=_duration(table.get("stop_timeout", "30s"), "daemon.stop_timeout"),
        log_level=str(table.get("log_level", "INFO")).upper(),
        log_format=log_format,
    )


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
            min_idle=int(table.get("min_idle", 0)),
            max_runners=int(table.get("max_runners", 2)),
            idle_timeout=_duration(table.get("idle_timeout", "10m"), f"{where}.idle_timeout"),
            max_job_duration=_duration(
                table.get("max_job_duration", "2h"), f"{where}.max_job_duration"
            ),
            max_launch_per_tick=int(table.get("max_launch_per_tick", 2)),
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
        image=str(container["image"]),
        cpus=float(cpus) if cpus is not None else None,
        memory=str(container["memory"]) if container.get("memory") else None,
        mount_docker_socket=bool(container.get("docker_socket", False)),
        volumes={str(key): str(value) for key, value in volumes.items()},
        network=str(container["network"]) if container.get("network") else None,
        environment={str(key): str(value) for key, value in environment.items()},
    )


# -- helpers -------------------------------------------------------------------------


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


def _duration(value: Any, where: str) -> timedelta:
    """Parse ``15s``, ``10m``, ``2h``, or a bare number of seconds."""
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


def _reject_duplicate_names(pools: tuple[PoolConfiguration, ...]) -> None:
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


def _warn_if_world_readable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        import warnings

        warnings.warn(
            f"{path} is readable by other users; run: chmod 600 {path}",
            stacklevel=2,
        )
