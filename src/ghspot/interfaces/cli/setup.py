"""The first-run wizard.

What somebody has after `apt install ghspot` is a package and no idea what to write. This
asks the four questions that cannot be guessed — which credential, where it is, which
repository, what the pool is called — and writes a configuration the daemon accepts.

It deliberately does **not** run from the package's postinst. A Debian install must work
unattended, and a maintainer script that stops to ask questions breaks `apt install -y` on
every machine that ever images itself. The package points here instead.

Nothing here decides anything the configuration file cannot express: the wizard's whole
output is a file an operator could have written, and it says where it put it.
"""

from __future__ import annotations

import asyncio
import grp
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax

from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.config.settings import ConfigError, host_cores
from ghspot.infrastructure.config.settings import load as load_settings
from ghspot.infrastructure.docker.backend import DockerRunnerBackend
from ghspot.interfaces.cli import images
from ghspot.interfaces.cli.render import console, fail, hint
from ghspot.interfaces.cli.scaffold import (
    SYSTEM_DIRECTORY,
    Substitution,
    effective,
    render,
    replace_header,
)
from ghspot.paths import build_command, example_config, runner_sources

IMAGES = ("ubuntu-24.04", "ubuntu-22.04", "rhel-9", "rhel-10")

SERVICE_GROUP = "ghspot"
"""The account the systemd unit runs as, and so the one that has to read what is written."""


@dataclass(frozen=True, slots=True)
class Answers:
    """Everything the wizard asked for, before anything is written."""

    repository: RepositoryTarget
    pool: str
    image: str
    uses_app: bool
    token: str = ""
    app_id: str = ""
    private_key_path: Path | None = None
    docker_socket: bool = True
    max_runners: int = 2
    api_bind: str = ""


def run(config_path: Path, *, force: bool = False) -> int:
    """Ask, write, and say what to do next. Returns a process exit code."""
    if config_path.exists() and not force:
        fail(f"{config_path} already exists")
        hint("pass --force to replace it, or edit it directly")
        return 2

    console.print(
        Panel(
            "This asks the handful of things that cannot be guessed, then writes a\n"
            "configuration and checks the daemon accepts it.\n\n"
            "[dim]Nothing is sent anywhere. Credentials are written to a file of their own,\n"
            "readable only by you.[/dim]",
            title="ghspot setup",
            border_style="dim",
        )
    )

    try:
        answers = _ask(config_path)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]nothing was written[/dim]")
        return 130

    written = _write(config_path, answers)
    console.print()
    console.print(Syntax(effective(config_path.read_text()), "toml", theme="ansi_dark"))
    console.print(
        "[dim]  every other setting is in the file too, commented out with what it does[/dim]"
    )
    console.print(f"[green]written[/green] {config_path}")
    for extra in written:
        console.print(f"[green]written[/green] {extra}")

    if not _validate(config_path):
        return 1

    built = _offer_build(answers)
    _next_steps(config_path, answers, built=built)
    return 0


# -- asking ----------------------------------------------------------------------------


def _ask(config_path: Path) -> Answers:
    console.print()
    console.print("[bold]1. The credential[/bold]")
    console.print(
        "  [dim]A token is two minutes' work and fine for a couple of repositories.\n"
        "  A GitHub App has its own rate limit and outlives your account's access —\n"
        "  worth it for anything left running. Either needs Administration: read & write\n"
        "  and Actions: read. See docs/authentication.md.[/dim]"
    )
    uses_app = (
        Prompt.ask("  which", choices=["token", "app"], default="token", console=console) == "app"
    )

    token = app_id = ""
    key_path: Path | None = None
    if uses_app:
        app_id = Prompt.ask("  app id [dim](the number, not the client id)[/dim]", console=console)
        raw = Prompt.ask("  path to the .pem private key", console=console)
        key_path = Path(raw).expanduser()
        if not key_path.is_file():
            console.print(f"  [yellow]note[/yellow] {key_path} is not there yet")
    else:
        token = Prompt.ask(
            "  token [dim](github_pat_… — it is not echoed)[/dim]", password=True, console=console
        )

    console.print()
    console.print("[bold]2. The repository[/bold]")
    repository = _ask_repository()

    console.print()
    console.print("[bold]3. The pool[/bold]")
    pool = Prompt.ask("  name", default="default", console=console)
    image = Prompt.ask("  runner image", choices=list(IMAGES), default=IMAGES[0], console=console)

    cores = host_cores()
    console.print(
        f"  [dim]This machine reports {cores} core(s), so the host will hold at most that\n"
        f"  many runners across every pool unless you say otherwise.[/dim]"
    )
    max_runners = _ask_count("  most runners in this pool", default=min(2, cores))

    console.print()
    console.print(
        "  [dim]Letting jobs use Docker means `docker build` works — and that a job has\n"
        "  effective root on this host. Fine for repositories you control, unacceptable\n"
        "  for one that accepts pull requests from forks. See SECURITY.md.[/dim]"
    )
    # Both of these default to no. Saying yes hands a job root on the host, or puts an
    # unauthenticated API on it — neither is something to acquire by pressing enter past a
    # question, and the paragraph above each one is what somebody should be reading when
    # they turn it on. Turning either on later costs one line in the configuration file.
    docker_socket = Confirm.ask("  let jobs use Docker", default=False, console=console)

    console.print()
    console.print("[bold]4. The dashboard[/bold]")
    console.print(
        "  [dim]Serves the API and the web dashboard on this host. No authentication,\n"
        "  so it binds to localhost; reach it over an SSH tunnel.[/dim]"
    )
    api = Confirm.ask("  serve it", default=False, console=console)

    return Answers(
        repository=repository,
        pool=pool,
        image=image,
        uses_app=uses_app,
        token=token,
        app_id=app_id,
        private_key_path=key_path,
        docker_socket=docker_socket,
        max_runners=max_runners,
        api_bind="127.0.0.1:8770" if api else "",
    )


def _ask_repository() -> RepositoryTarget:
    while True:
        raw = Prompt.ask("  owner/name", console=console)
        try:
            return RepositoryTarget.parse(raw.strip())
        except GhSpotError:
            # Whatever was wrong with it, the answer to a person is the same sentence.
            console.print("  [red]that is not owner/name[/red] — for example tguisep/my-project")


def _ask_count(question: str, *, default: int) -> int:
    while True:
        raw = Prompt.ask(question, default=str(default), console=console)
        if raw.strip().isdigit() and int(raw) >= 1:
            return int(raw)
        console.print("  [red]a whole number, at least 1[/red]")


# -- writing ---------------------------------------------------------------------------


def _substitutions(
    answers: Answers, credential: list[Substitution], system: bool
) -> list[Substitution]:
    """What the wizard asked about, as edits to the reference."""
    edits = [
        *credential,
        Substitution("[[pool]]", "name", f'"{answers.pool}"'),
        Substitution("[[pool]]", "repository", f'"{answers.repository}"'),
        Substitution(
            "[[pool]]",
            "labels",
            f'["self-hosted", "linux", "x64", "{answers.image}"]',
        ),
        Substitution("[[pool]]", "min_idle", "1"),
        Substitution("[[pool]]", "max_runners", str(answers.max_runners)),
        Substitution("[pool.container]", "image", f'"ghspot/runner:{answers.image}"'),
        Substitution("[pool.container]", "docker_socket", str(answers.docker_socket).lower()),
        # Unset these mean "no limit". Inheriting the reference's illustration would cap
        # every job on this host at two cores and 4g, which nobody asked for.
        Substitution("[pool.container]", "cpus", None),
        Substitution("[pool.container]", "memory", None),
    ]
    if answers.api_bind:
        edits.append(Substitution("[daemon]", "api_bind", f'"{answers.api_bind}"'))
    if system:
        # The reference writes into $HOME, which the ghspot service account does not have.
        edits.append(Substitution("[daemon]", "state_db", '"/var/lib/ghspot/state.db"'))
    return edits


def _write(config_path: Path, answers: Answers) -> list[Path]:
    """Write the configuration, and the credential beside it. Returns the extra files."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    extra: list[Path] = []
    system = config_path.parent == SYSTEM_DIRECTORY

    credential: list[Substitution] = []
    if answers.uses_app:
        credential.append(Substitution("[github]", "token_file", None))
        credential.append(Substitution("[github]", "app_id", f'"{answers.app_id}"'))
        if answers.private_key_path is not None:
            credential.append(
                Substitution("[github]", "private_key_file", f'"{answers.private_key_path}"')
            )
            if system:
                _warn_if_the_service_cannot_read(answers.private_key_path)
    else:
        token_file = config_path.parent / "token"
        _write_secret(token_file, answers.token)
        if system:
            _share_with_the_service(token_file)
        extra.append(token_file)
        credential.append(Substitution("[github]", "token_file", f'"{token_file}"'))

    reference = example_config()
    if reference is None:
        # The reference is a documentation file, and a wizard that fails because one is
        # missing is worse than one that writes the short form.
        config_path.write_text(_minimal(answers, credential, system))
    else:
        rendered = render(
            reference.read_text(encoding="utf-8"), _substitutions(answers, credential, system)
        )
        config_path.write_text(replace_header(rendered))

    if system:
        # write_text creates 0644 root:root, undoing the 0640 root:ghspot the package set on
        # the file it shipped — so the wizard was widening the configuration every time.
        _share_with_the_service(config_path)
    return extra


def _share_with_the_service(path: Path) -> None:
    """Give the daemon's account read access to what the wizard just wrote as root.

    `sudo ghspot setup` writes as root; the unit runs as `ghspot`. Without this the sequence
    the wizard itself prints — setup, then `systemctl enable --now` — ends in

        could not read the token from /etc/ghspot/token: [Errno 13] Permission denied

    which is the wizard's own next step failing on the wizard's own output. The package
    already keeps /etc/ghspot/env and the shipped configuration at root:ghspot 0640; these
    are the files created after it ran.
    """
    if os.geteuid() != 0:
        return
    try:
        group = grp.getgrnam(SERVICE_GROUP).gr_gid
    except KeyError:
        return  # not a packaged host: there is no service account to share with
    try:
        os.chown(path, 0, group)
        path.chmod(0o640)
    except OSError as error:
        hint(f"could not let {SERVICE_GROUP} read {path}: {error}")
        hint(f"the daemon runs as {SERVICE_GROUP}: chown root:{SERVICE_GROUP} {path}")


def _warn_if_the_service_cannot_read(path: Path) -> None:
    """Say so when the daemon will not be able to open a file it was pointed at.

    An App's private key is pointed at, never copied, so its permissions are not ours to
    change — but the daemon failing on it looks exactly like the token failure, three
    commands later and with nothing connecting the two.
    """
    try:
        info = path.expanduser().stat()
        group = grp.getgrnam(SERVICE_GROUP).gr_gid
    except (OSError, KeyError):
        return

    readable = bool(info.st_mode & stat.S_IROTH) or (
        info.st_gid == group and bool(info.st_mode & stat.S_IRGRP)
    )
    if not readable:
        hint(f"{path} is not readable by {SERVICE_GROUP}, which the daemon runs as")
        hint(f"fix with: sudo chown root:{SERVICE_GROUP} {path} && sudo chmod 640 {path}")


def _minimal(answers: Answers, credential: list[Substitution], system: bool) -> str:
    """The short form, for when config.example.toml is not installed."""
    lines = [
        "# Written by `ghspot setup`. Edit freely — it is an ordinary configuration file.",
        "# Every setting, with what it means: config.example.toml",
        "",
        "[github]",
        *(f"{item.key} = {item.value}" for item in credential if item.value is not None),
        "",
        "[daemon]",
    ]
    if system:
        lines.append('state_db = "/var/lib/ghspot/state.db"')
    if answers.api_bind:
        lines.append(f'api_bind = "{answers.api_bind}"   # dashboard at /ui, on this host only')
    lines += [
        "",
        "[[pool]]",
        f'name = "{answers.pool}"',
        f'repository = "{answers.repository}"',
        f'labels = ["self-hosted", "linux", "x64", "{answers.image}"]',
        f"max_runners = {answers.max_runners}",
        "min_idle = 1          # one warm runner takes container boot off the first job",
        "",
        "[pool.container]",
        f'image = "ghspot/runner:{answers.image}"',
        f"docker_socket = {str(answers.docker_socket).lower()}",
        "",
    ]
    return "\n".join(lines)


def _write_secret(path: Path, contents: str) -> None:
    """Create the file with its mode before anything is written to it.

    The other order leaves the secret briefly world-readable, which is the whole reason
    `docs/authentication.md` tells an operator to use `install -m 600`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(handle, "w") as file:
        file.write(contents)


# -- checking --------------------------------------------------------------------------


def _image_present(image: str) -> bool | None:
    """Whether the runner image is already built, or ``None`` when Docker cannot say.

    Three answers, not two. "Docker is not reachable" must not read as "not built", because
    the wizard would then offer a build that cannot start — and `doctor`, one step later,
    reports the real problem properly.
    """

    async def ask() -> bool:
        backend = DockerRunnerBackend()
        await backend.ping()
        return await backend.image_exists(image)

    try:
        return asyncio.run(ask())
    except GhSpotError:
        return None


def _offer_build(answers: Answers) -> bool:
    """Offer to build the runner image now. Returns whether it is there afterwards.

    "Build the runner image" has always been the first thing the wizard tells somebody to do
    next, and it is the one step nothing works without: a pool whose image is missing starts
    no runners and says so only in the daemon's log. Now that ghspot can build it, asking is
    better than instructing.

    Only ever an offer. It is minutes of work on a machine the operator may not want busy
    yet, and declining leaves the instruction in the list exactly as before.
    """
    image = f"ghspot/runner:{answers.image}"
    present = _image_present(image)

    if present:
        console.print(f"\n[green]have[/green] {image} — already built")
        return True
    if present is None or runner_sources() is None:
        return False

    console.print()
    try:
        wanted = Confirm.ask(f"Build {image} now? A few minutes", default=True, console=console)
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False

    if not wanted:
        return False

    console.print()
    return images.build(answers.image) == 0


def _validate(config_path: Path) -> bool:
    """Whether the daemon accepts what was just written."""
    try:
        load_settings(config_path)
    except ConfigError as error:
        fail(str(error))
        hint("the file is on disk; fix it and run: ghspot config validate")
        return False
    return True


def _next_steps(config_path: Path, answers: Answers, *, built: bool) -> None:
    # A configuration under /etc is root:ghspot 0640 and the checks want the Docker socket,
    # so the next command an operator types — in a shell where the wizard's sudo has already
    # expired — needs its own.
    system = config_path.parent == SYSTEM_DIRECTORY

    steps: list[tuple[str, str]] = []
    if not built:
        steps.append(("build the runner image", build_command(answers.image)))
    steps.append(("check everything", f"{'sudo ' if system else ''}ghspot doctor -c {config_path}"))
    steps.append(
        (
            "start it",
            "sudo systemctl enable --now ghspot" if system else "ghspot daemon",
        )
    )
    if answers.api_bind:
        steps.append(("the dashboard", f"http://{answers.api_bind}/ui"))

    console.print()
    console.print("[bold]Next[/bold]")
    if built:
        console.print(f"  [green]done[/green]  the runner image ghspot/runner:{answers.image}")
    for number, (label, command) in enumerate(steps, start=1):
        console.print(f"  {number}. {label:<25}{command}")

    console.print()
    labels = escape(f'runs-on: [self-hosted, linux, x64, "{answers.image}"]'.replace('"', ""))
    # Escaped: a label list is square brackets, which Rich reads as a style tag — and the
    # one line telling somebody what to paste into their workflow is the line that vanishes.
    console.print(f"  [dim]Point a workflow at:  {labels}[/dim]")
