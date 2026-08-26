"""Pre-flight checks.

Every failure here is one that would otherwise show up as a pool that quietly never starts a
runner. Each check reports what it found and, when it fails, the command that fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from ghspot.composition import Application, build
from ghspot.domain.errors import (
    ForgeError,
    ForgeNotFoundError,
    ForgePermissionError,
    ForgeTokenRejectedError,
    GhSpotError,
)
from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.config.settings import ConfigError, Settings
from ghspot.infrastructure.docker.backend import DOCKER_SOCKET, DockerRunnerBackend
from ghspot.interfaces.cli.render import console


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    remedy: str = ""


async def diagnose(settings: Settings) -> bool:
    """Run every check, print the results, and report whether all of them passed."""
    checks: list[Check] = [_configuration(settings)]
    checks.extend(await _docker(settings))
    checks.extend(await _github(settings))

    for check in checks:
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {escape(check.name)}: {escape(check.detail)}")
        if not check.ok and check.remedy:
            console.print(f"  [dim]{escape(check.remedy)}[/dim]")

    passed = all(check.ok for check in checks)
    console.print()
    console.print(
        "[green]ready[/green]" if passed else "[red]not ready[/red] — fix the above first"
    )
    return passed


def _configuration(settings: Settings) -> Check:
    pools = ", ".join(pool.spec.name for pool in settings.pools)
    return Check(
        name="configuration",
        ok=True,
        detail=f"{settings.source} — {len(settings.pools)} pool(s): {pools}",
    )


async def _docker(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    try:
        backend = DockerRunnerBackend()
        await backend.ping()
    except GhSpotError as error:
        return [
            Check(
                name="docker",
                ok=False,
                detail=str(error),
                remedy="sudo systemctl start docker && sudo usermod -aG docker $USER",
            )
        ]

    checks.append(Check(name="docker", ok=True, detail="daemon reachable"))

    for pool in settings.pools:
        image = pool.template.image
        present = await backend.image_exists(image)
        checks.append(
            Check(
                name=f"image [{pool.spec.name}]",
                ok=present,
                detail=image if present else f"{image} is not present",
                remedy=(
                    "docker build -t "
                    f"{image} --build-arg "
                    'DOCKER_GID="$(getent group docker | cut -d: -f3)" images/runner/'
                ),
            )
        )
        if pool.template.mount_docker_socket:
            checks.append(_socket_check(pool.spec.name))

    return checks


def _socket_check(pool: str) -> Check:
    exists = Path(DOCKER_SOCKET).exists()
    return Check(
        name=f"docker socket [{pool}]",
        ok=exists,
        detail=(
            f"{DOCKER_SOCKET} will be mounted into jobs"
            if exists
            else f"{DOCKER_SOCKET} does not exist"
        ),
        remedy="set docker_socket = false for this pool, or start the Docker daemon",
    )


async def _github(settings: Settings) -> list[Check]:
    try:
        application = build(settings)
    except ConfigError as error:
        return [
            Check(
                name="github token",
                ok=False,
                detail=str(error),
                remedy="export GHSPOT_GITHUB_TOKEN=... or create the token file",
            )
        ]

    checks: list[Check] = [Check(name="github token", ok=True, detail="found")]
    try:
        for repository in settings.repositories:
            checks.append(await _repository(application, repository))
    finally:
        await application.aclose()
    return checks


async def _repository(application: Application, repository: RepositoryTarget) -> Check:
    """Prove the token can actually do the two things the daemon needs.

    Listing runners exercises 'Administration: read'; it is the cheapest call that fails in
    the same way a real registration would.
    """
    name = str(repository)
    try:
        runners = await application.forge.list_runners(repository)
    except ForgeNotFoundError:
        return Check(
            name=f"repository {name}",
            ok=False,
            detail="not found, or the token cannot see it",
            remedy="check the repository name and that the token is scoped to it",
        )
    except ForgeTokenRejectedError as error:
        return Check(
            name=f"repository {name}",
            ok=False,
            detail=str(error),
            remedy="the token is invalid or expired; generate a new one",
        )
    except ForgePermissionError as error:
        return Check(
            name=f"repository {name}",
            ok=False,
            detail=str(error),
            remedy="the token needs 'Administration: read & write' and 'Actions: read'",
        )
    except ForgeError as error:
        return Check(name=f"repository {name}", ok=False, detail=str(error))

    ours = sum(1 for runner in runners if runner.name.startswith("ghspot-"))
    return Check(
        name=f"repository {name}",
        ok=True,
        detail=f"reachable — {len(runners)} runner(s) registered, {ours} ours",
    )
