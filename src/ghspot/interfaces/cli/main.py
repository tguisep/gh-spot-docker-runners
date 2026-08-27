"""The operator interface.

Controllers in the MVC sense: parse arguments, call one use case, hand the result to the
renderer. No orchestration happens here — if a command starts making decisions, those belong
in the application layer where they can be tested without a terminal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer

from ghspot import __version__
from ghspot.application.dto import PoolView
from ghspot.application.queries.views import GetPoolStatus, ListRunners
from ghspot.composition import build, read_only_store
from ghspot.daemon import run_forever
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.pool import PoolSpec
from ghspot.infrastructure.config.settings import ConfigError, Settings
from ghspot.infrastructure.config.settings import load as load_settings
from ghspot.infrastructure.logging.setup import configure as configure_logging
from ghspot.infrastructure.system import SystemClock
from ghspot.interfaces.cli import doctor as doctor_module
from ghspot.interfaces.cli import operations
from ghspot.interfaces.cli.render import console, fail, hint, pools_table, runners_table

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


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Check that everything the daemon needs is in place.

    Run this first. It is faster to be told the token lacks a scope than to watch a pool
    quietly fail to start runners.
    """
    settings = _settings(config)
    ok = bool(_run(doctor_module.diagnose(settings)))
    raise typer.Exit(code=0 if ok else 1)


# ---------------------------------------------------------------- pools


@pool_app.command("list")
def pool_list(config: ConfigOption = None) -> None:
    """List the configured pools and what they currently hold."""
    settings = _settings(config)
    query = GetPoolStatus(read_only_store(settings), SystemClock())
    views = _run(query([pool.spec for pool in settings.pools]))
    console.print(pools_table(views))


@pool_app.command("status")
def pool_status(
    name: Annotated[str | None, typer.Argument(help="Pool name.")] = None,
    config: ConfigOption = None,
) -> None:
    """Show one pool, or all of them, with their runners."""
    settings = _settings(config)
    specs = [pool.spec for pool in settings.pools]
    if name is not None:
        specs = [spec for spec in specs if spec.name == name]
        if not specs:
            fail(f"no pool named {name!r}")
            hint(f"configured pools: {', '.join(p.spec.name for p in settings.pools)}")
            raise typer.Exit(code=2)

    query = GetPoolStatus(read_only_store(settings), SystemClock())
    for view in _run(query(specs)):
        console.print(pools_table([view]))
        if view.runners:
            console.print(runners_table(view.runners))
        else:
            console.print("[dim]no runners[/dim]")
        console.print()


# ---------------------------------------------------------------- runners


@runner_app.command("list")
def runner_list(
    pool: Annotated[str | None, typer.Option("--pool", help="Restrict to one pool.")] = None,
    all_runners: Annotated[
        bool, typer.Option("--all", help="Include retired and failed runners.")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """List runners from the local projection.

    Reads the state database only, so this still works when the token has expired or Docker
    is down — which is exactly when you want to look.
    """
    settings = _settings(config)
    query = ListRunners(read_only_store(settings), SystemClock())
    views = _run(query(pool, include_terminal=all_runners))
    if not views:
        console.print("[dim]no runners[/dim]")
        return
    console.print(runners_table(views))


@runner_app.command("logs")
def runner_logs(
    runner_id: Annotated[str, typer.Argument(help="Runner id, or its container id.")],
    tail: Annotated[int, typer.Option("--tail", "-n", help="Lines to show.")] = 200,
    config: ConfigOption = None,
) -> None:
    """Show a runner's container output."""
    settings = _settings(config)
    output = _run(operations.runner_logs(settings, runner_id, tail))
    console.print(output or "[dim]no output[/dim]")


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
    console.print()
    console.print(pools_table([_declared(pool.spec) for pool in settings.pools]))


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
