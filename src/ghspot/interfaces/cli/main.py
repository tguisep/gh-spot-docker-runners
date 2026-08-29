"""The operator interface.

Controllers in the MVC sense: parse arguments, call one use case, hand the result to the
renderer. No orchestration happens here — if a command starts making decisions, those belong
in the application layer where they can be tested without a terminal.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Group
from rich.live import Live
from rich.text import Text

from ghspot import __version__
from ghspot.application.dto import PoolView
from ghspot.application.queries.stats import GatherStats
from ghspot.application.queries.views import GetPoolStatus, ListRunners
from ghspot.composition import build, read_only_events, read_only_store
from ghspot.daemon import run_forever
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.pool import PoolSpec
from ghspot.infrastructure.config.settings import ConfigError, Settings, parse_duration
from ghspot.infrastructure.config.settings import load as load_settings
from ghspot.infrastructure.docker.backend import DockerRunnerBackend
from ghspot.infrastructure.logging.setup import configure as configure_logging
from ghspot.infrastructure.system import SystemClock
from ghspot.interfaces.api import dashboard
from ghspot.interfaces.cli import doctor as doctor_module
from ghspot.interfaces.cli import operations, setup
from ghspot.interfaces.cli.render import (
    console,
    fail,
    hint,
    pools_table,
    runners_table,
    stats_tables,
)

app = typer.Typer(
    name="ghspot",
    help="Self-hosted GitHub Actions runners as ephemeral Docker containers.",
    no_args_is_help=True,
    add_completion=False,
)
pool_app = typer.Typer(help="Inspect runner pools.", no_args_is_help=True)
runner_app = typer.Typer(help="Inspect and control individual runners.", no_args_is_help=True)
config_app = typer.Typer(help="Work with the configuration file.", no_args_is_help=True)
app.add_typer(pool_app, name="pool")
app.add_typer(runner_app, name="runner")
app.add_typer(config_app, name="config")

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config.toml.", show_default=False),
]


def _settings(path: Path | None) -> Settings:
    """Load configuration, or exit with a message that says what to do about it."""
    try:
        return load_settings(path)
    except ConfigError as error:
        fail(str(error))
        hint("start from config.example.toml, then run: ghspot config validate")
        raise typer.Exit(code=2) from error


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run an async use case, turning a domain error into a clean exit."""
    try:
        return asyncio.run(coroutine)
    except GhSpotError as error:
        fail(str(error))
        raise typer.Exit(code=1) from error
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


# ---------------------------------------------------------------- top level


WatchOption = Annotated[
    float | None,
    typer.Option(
        "--watch",
        "-w",
        help="Repaint every N seconds until interrupted. Try 2.",
        show_default=False,
        metavar="SECONDS",
    ),
]


def _watch(build: Callable[[], Any], every: float) -> None:
    """Repaint one renderable in place until interrupted.

    This is `watch ghspot ...` without the drawbacks: `watch` re-runs the whole command, so
    every refresh re-reads the configuration and reopens the database, and it strips colour
    unless told not to. Here the process stays up and only the frame changes.
    """
    every = max(0.5, every)
    try:
        with Live(build(), console=console, auto_refresh=False, transient=False) as live:
            while True:
                time.sleep(every)
                live.update(build(), refresh=True)
    except KeyboardInterrupt:
        # Ctrl-C is how this command is meant to end, not a failure.
        raise typer.Exit(code=0) from None


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"ghspot {__version__}")


@app.command()
def daemon(
    config: ConfigOption = None,
    once: Annotated[bool, typer.Option("--once", help="Run one tick and exit.")] = False,
    ticks: Annotated[
        int | None, typer.Option("--ticks", help="Stop after this many ticks.")
    ] = None,
) -> None:
    """Run the reconciliation loop.

    Stops on SIGINT or SIGTERM, finishing the tick it is in. Runners mid-job are left alone:
    killing them would fail builds that were about to pass.
    """
    settings = _settings(config)
    configure_logging(settings.daemon.log_level, settings.daemon.log_format)

    limit = 1 if once else ticks
    # Wiring resolves the credential, so a missing token surfaces here. It has to go through
    # the same handling as everything else, or the commonest misconfiguration there is
    # answers with a traceback instead of the sentence that says how to fix it.
    try:
        application = build(settings)
    except GhSpotError as error:
        fail(str(error))
        hint("ghspot doctor --config <path> checks everything the daemon needs")
        raise typer.Exit(code=1) from error

    _run(run_forever(application, max_ticks=limit))


@app.command("setup")
def setup_command(
    config: ConfigOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing configuration.")
    ] = False,
) -> None:
    """Write a first configuration by answering a few questions.

    What somebody has after installing the package is a package and no idea what to write.
    This asks the things that cannot be guessed — which credential, which repository, what
    the pool is called — and leaves an ordinary configuration file behind.
    """
    destination = Path(config) if config is not None else _setup_destination()
    raise typer.Exit(code=setup.run(destination, force=force))


def _setup_destination() -> Path:
    """Where a first configuration should go, given who is running the wizard.

    Root gets the system location the package and the unit already agree on. Anyone else
    gets their own, because writing under /etc as a normal user fails at the last step,
    after every question has been answered.
    """
    if os.geteuid() == 0 and Path("/etc/ghspot").is_dir():
        return Path("/etc/ghspot/config.toml")
    return Path("~/.config/ghspot/config.toml").expanduser()


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Check that everything the daemon needs is in place.

    Run this first. It is faster to be told the token lacks a scope than to watch a pool
    quietly fail to start runners.
    """
    settings = _settings(config)
    ok = bool(_run(doctor_module.diagnose(settings)))
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def stats(
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            "-s",
            help="Window to report on, as a duration: 24h, 7d, 30m. Default: everything.",
            show_default=False,
        ),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Report what the fleet did: runners, jobs, failures and time spent.

    Counted from the event log, so it covers runners that are long gone. Like the other
    query commands it reads only the state database, and works with Docker down.
    """
    settings = _settings(config)

    start = None
    if since is not None:
        try:
            window = parse_duration(since, "--since")
        except ConfigError as error:
            fail(str(error))
            raise typer.Exit(code=2) from error
        start = SystemClock().now() - window

    query = GatherStats(read_only_events(settings), read_only_store(settings), SystemClock())
    for block in stats_tables(_run(query(start))):
        console.print(block)


# ---------------------------------------------------------------- pools


@pool_app.command("list")
def pool_list(watch: WatchOption = None, config: ConfigOption = None) -> None:
    """List the configured pools and what they currently hold."""
    settings = _settings(config)
    specs = [pool.spec for pool in settings.pools]

    def frame() -> Any:
        query = GetPoolStatus(read_only_store(settings), SystemClock())
        return pools_table(_run(query(specs)))

    if watch is not None:
        _watch(frame, watch)
        return
    console.print(frame())


@pool_app.command("status")
def pool_status(
    name: Annotated[str | None, typer.Argument(help="Pool name.")] = None,
    watch: WatchOption = None,
    config: ConfigOption = None,
) -> None:
    """Show one pool, or all of them, with their runners.

    ``--watch 2`` repaints in place, which is what `watch ghspot pool status` is reaching
    for — without re-reading the configuration and reopening the database every two seconds,
    and without losing the colours.
    """
    settings = _settings(config)
    specs = [pool.spec for pool in settings.pools]
    if name is not None:
        specs = [spec for spec in specs if spec.name == name]
        if not specs:
            fail(f"no pool named {name!r}")
            hint(f"configured pools: {', '.join(p.spec.name for p in settings.pools)}")
            raise typer.Exit(code=2)

    def frame() -> Any:
        query = GetPoolStatus(read_only_store(settings), SystemClock())
        blocks: list[Any] = []
        for view in _run(query(specs)):
            blocks.append(pools_table([view]))
            blocks.append(
                runners_table(view.runners) if view.runners else Text("no runners", style="dim")
            )
        return Group(*blocks)

    if watch is not None:
        _watch(frame, watch)
        return
    console.print(frame())


# ---------------------------------------------------------------- runners


@runner_app.command("list")
def runner_list(
    pool: Annotated[str | None, typer.Option("--pool", help="Restrict to one pool.")] = None,
    all_runners: Annotated[
        bool, typer.Option("--all", help="Include retired and failed runners.")
    ] = False,
    usage: Annotated[
        bool,
        typer.Option("--usage", "-u", help="Sample CPU and memory for each container."),
    ] = False,
    watch: WatchOption = None,
    config: ConfigOption = None,
) -> None:
    """List runners from the local projection.

    Reads the state database only, so this still works when the token has expired or Docker
    is down — which is exactly when you want to look. ``--usage`` is the exception: it asks
    Docker for a sample per container, so it needs a reachable daemon.
    """
    settings = _settings(config)

    def frame() -> Any:
        # The Docker connection is only made when a sample was asked for, so the command
        # keeps working with Docker down in every other case.
        backend = DockerRunnerBackend() if usage else None
        query = ListRunners(read_only_store(settings), SystemClock(), backend)
        views = _run(query(pool, include_terminal=all_runners, with_usage=usage))
        if not views:
            return Text("no runners", style="dim")
        return runners_table(views, usage=usage)

    if watch is not None:
        _watch(frame, watch)
        return
    console.print(frame())


@runner_app.command("logs")
def runner_logs(
    runner_id: Annotated[str, typer.Argument(help="Runner id, or its container id.")],
    tail: Annotated[int, typer.Option("--tail", "-n", help="Lines to show.")] = 200,
    job: Annotated[
        bool,
        typer.Option("--job", "-j", help="GitHub's log for the job, instead of the container's."),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Show a runner's output.

    By default the container's, which is the job as it happens: the runner prints its work to
    stdout. ``--job`` asks GitHub for its own log instead — written when the job **finishes**,
    so a running job has none, and it outlives the container, which a just-in-time runner
    takes with it seconds after the job ends.
    """
    settings = _settings(config)

    if not job:
        output = _run(operations.runner_logs(settings, runner_id, tail))
        console.print(output or "[dim]no output[/dim]")
        return

    job_id, from_forge = _run(operations.job_logs(settings, runner_id, tail))
    if job_id is None:
        console.print("[dim]this runner is not running a job[/dim]")
        return
    if from_forge is None:
        console.print(
            f"[dim]job {job_id} has not finished, so GitHub has no log for it yet. "
            f"The container's output is the live view: ghspot runner logs {runner_id}[/dim]"
        )
        return
    console.print(from_forge or "[dim]no output[/dim]")


@runner_app.command("stop")
def runner_stop(
    runner_id: Annotated[str, typer.Argument(help="Runner id.")],
    force: Annotated[
        bool, typer.Option("--force", help="Kill it even if it is running a job.")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Retire a runner, removing its container and its registration.

    Without ``--force`` a busy runner is refused: stopping it fails somebody's build.
    """
    settings = _settings(config)
    _run(operations.stop_runner(settings, runner_id, force=force))
    console.print(f"retired [bold]{runner_id}[/bold]")


# ---------------------------------------------------------------- config


@config_app.command("validate")
def config_validate(config: ConfigOption = None) -> None:
    """Load the configuration and report what it means."""
    settings = _settings(config)

    console.print(f"[green]ok[/green] {settings.source}")
    console.print(f"  poll interval  {settings.daemon.poll_interval.total_seconds():.0f}s")
    console.print(f"  state database {settings.daemon.state_db}")
    console.print(f"  repositories   {', '.join(str(r) for r in settings.repositories)}")
    console.print(f"  api            {_api_summary(settings)}")
    console.print()
    console.print(pools_table([_declared(pool.spec) for pool in settings.pools]))


def _api_summary(settings: Settings) -> str:
    """What `api_bind` actually got you, since setting it is otherwise unconfirmable.

    Whether the dashboard is there is the half an operator cannot check from the config file
    at all: it ships in the package, and a package built without a node toolchain has none.
    """
    bind = settings.daemon.api_bind
    if not bind:
        return "[dim]not served (set api_bind to serve the API and the dashboard)[/dim]"

    root = dashboard.find_root()
    if root is None:
        return f"http://{bind} — [yellow]no dashboard installed, so /ui will 404[/yellow]"
    return f"http://{bind}  ·  dashboard http://{bind}/ui  [dim]({root})[/dim]"


def _declared(spec: PoolSpec) -> PoolView:
    """A pool as configured, before any runner exists — what `validate` has to show."""
    return PoolView(
        name=spec.name,
        repository=str(spec.repository),
        labels=spec.labels.as_list(),
        min_idle=spec.min_idle,
        max_runners=spec.max_runners,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
