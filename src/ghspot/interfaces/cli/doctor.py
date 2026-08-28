"""Pre-flight checks.

Every failure here is one that would otherwise show up as a pool that quietly never starts a
runner. Each check reports what it found and, when it fails, the command that fixes it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from ghspot.composition import build_forge
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
from ghspot.infrastructure.github.client import GitHubClient
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
        return [Check(name="docker", ok=False, detail=str(error), remedy=_docker_remedy(error))]

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
        if pool.template.gpus is not None:
            checks.append(_gpu_check(pool.spec.name, pool.template.gpus))
        if pool.template.runtime is not None:
            checks.append(await _runtime_check(backend, pool.spec.name, pool.template.runtime))

    return checks


TEGRA_RELEASE = Path("/etc/nv_tegra_release")
"""Present only on a Jetson. JetPack writes it; a desktop with an NVIDIA card does not."""


def _tegra_release() -> str | None:
    """The L4T release this Jetson runs, or ``None`` when this is not a Jetson.

    The file's first line reads ``# R32 (release), REVISION: 7.1, GCID: ...``.
    """
    try:
        head = TEGRA_RELEASE.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = head.split()
    release = next((part for part in parts if part.startswith("R") and part[1:].isdigit()), "")
    revision = ""
    if "REVISION:" in parts:
        index = parts.index("REVISION:")
        if index + 1 < len(parts):
            revision = parts[index + 1].rstrip(",")
    return f"L4T {release}.{revision}".replace(" .", " ") if release else "L4T"


def _gpu_check(pool: str, gpus: object) -> Check:
    """Whether the host can actually hand a GPU to a container.

    Without the NVIDIA Container Toolkit the Engine refuses the device request, so every
    runner in the pool fails to start — with an error about device requests that says
    nothing about a missing toolkit.
    """
    tegra = _tegra_release()
    if tegra is not None:
        return Check(
            name=f"gpu [{pool}]",
            ok=False,
            detail=(
                f"this pool asks for {gpus}, but {tegra} has no device-request API — "
                "the Engine refuses --gpus on a Jetson"
            ),
            remedy=(
                "remove 'gpus' from this pool and set runtime = \"nvidia\" instead; "
                "JetPack grants the GPU through its own container runtime"
            ),
        )

    toolkit = shutil.which("nvidia-ctk") or shutil.which("nvidia-container-runtime")
    driver = shutil.which("nvidia-smi")

    if toolkit and driver:
        return Check(name=f"gpu [{pool}]", ok=True, detail=f"requesting {gpus}")

    missing = "the NVIDIA Container Toolkit" if not toolkit else "the NVIDIA driver"
    return Check(
        name=f"gpu [{pool}]",
        ok=False,
        detail=f"this pool asks for {gpus}, but {missing} is not installed",
        remedy=(
            "install it, then: sudo nvidia-ctk runtime configure --runtime=docker "
            "&& sudo systemctl restart docker  —  or remove 'gpus' from this pool"
        ),
    )


async def _runtime_check(backend: DockerRunnerBackend, pool: str, runtime: str) -> Check:
    """Whether the Engine knows the runtime this pool asks for.

    Asking for an unregistered runtime fails at every container creation with an error that
    names the runtime and says nothing about how to register it.
    """
    try:
        registered = await backend.runtimes()
    except GhSpotError as error:
        return Check(name=f"runtime [{pool}]", ok=False, detail=str(error))

    tegra = _tegra_release()
    if runtime in registered:
        detail = f"{runtime} registered with the Engine"
        return Check(
            name=f"runtime [{pool}]", ok=True, detail=f"{detail} ({tegra})" if tegra else detail
        )

    known = ", ".join(sorted(registered)) or "none"
    remedy = f"the Engine knows: {known}"
    if runtime == "nvidia":
        remedy = (
            "install nvidia-container-runtime, then register it in /etc/docker/daemon.json "
            'under "runtimes" and: sudo systemctl restart docker'
            + (f"  —  the Engine currently knows: {known}" if registered else "")
        )
    return Check(
        name=f"runtime [{pool}]",
        ok=False,
        detail=f"this pool asks for the {runtime!r} runtime, which the Engine does not have",
        remedy=remedy,
    )


def _docker_remedy(error: Exception) -> str:
    """Advice that matches which way Docker is unreachable.

    'Permission denied' and 'no such file' need different fixes, and telling someone to add
    themselves to a group without mentioning that it does not apply to the running shell
    sends them round the loop a second time.
    """
    message = str(error).casefold()
    if "permission denied" in message or "permissionerror" in message:
        return (
            "sudo usermod -aG docker $USER  —  then run 'newgrp docker', "
            "or log out and back in: group changes do not apply to the current shell"
        )
    return "sudo systemctl start docker  (and check: systemctl status docker)"


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
    # Only the forge client, never the whole application: the GitHub checks do not need
    # Docker, and building it here would make an unreachable daemon abort the report that
    # is supposed to tell you the daemon is unreachable.
    try:
        forge = build_forge(settings)
    except (ConfigError, GhSpotError) as error:
        return [
            Check(
                name="github auth",
                ok=False,
                detail=str(error),
                remedy=(
                    "set GHSPOT_GITHUB_TOKEN, or configure [github].app_id with "
                    "private_key_file for a GitHub App"
                ),
            )
        ]

    checks: list[Check] = [Check(name="github auth", ok=True, detail=forge.describe_auth())]
    try:
        # For a GitHub App this is the first call that actually signs a JWT and exchanges it,
        # so a bad key or a wrong app id surfaces here rather than an hour into a run.
        for repository in settings.repositories:
            checks.append(await _repository(forge, repository))
    finally:
        await forge.aclose()
    return checks


async def _repository(forge: GitHubClient, repository: RepositoryTarget) -> Check:
    """Prove the token can actually do the two things the daemon needs.

    Listing runners exercises 'Administration: read'; it is the cheapest call that fails in
    the same way a real registration would.
    """
    name = str(repository)
    try:
        runners = await forge.list_runners(repository)
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
