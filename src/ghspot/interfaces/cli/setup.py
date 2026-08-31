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
from ghspot.interfaces.cli.render import console, fail, hint
from ghspot.paths import build_command

IMAGES = ("ubuntu-24.04", "ubuntu-22.04", "rhel-9", "rhel-10")


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
    console.print(Syntax(config_path.read_text(), "toml", theme="ansi_dark"))
    console.print(f"[green]written[/green] {config_path}")
    for extra in written:
        console.print(f"[green]written[/green] {extra}")

    return _verify(config_path, answers)


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
    docker_socket = Confirm.ask("  let jobs use Docker", default=True, console=console)

    console.print()
    console.print("[bold]4. The dashboard[/bold]")
    console.print(
        "  [dim]Serves the API and the web dashboard on this host. No authentication,\n"
        "  so it binds to localhost; reach it over an SSH tunnel.[/dim]"
    )
    api = Confirm.ask("  serve it", default=True, console=console)

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


def _write(config_path: Path, answers: Answers) -> list[Path]:
    """Write the configuration, and the credential beside it. Returns the extra files."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    extra: list[Path] = []

    credential: list[str] = []
    if answers.uses_app:
        credential = [f'app_id = "{answers.app_id}"']
        if answers.private_key_path is not None:
            credential.append(f'private_key_file = "{answers.private_key_path}"')
    else:
        token_file = config_path.parent / "token"
        _write_secret(token_file, answers.token)
        extra.append(token_file)
        credential = [f'token_file = "{token_file}"']

    lines = [
        "# Written by `ghspot setup`. Edit freely — it is an ordinary configuration file.",
        "# Every setting, with what it means: config.example.toml",
        "",
        "[github]",
        *credential,
        "",
        "[daemon]",
    ]
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
    config_path.write_text("\n".join(lines))
    return extra


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


def _verify(config_path: Path, answers: Answers) -> int:
    try:
        load_settings(config_path)
    except ConfigError as error:
        fail(str(error))
        hint("the file is on disk; fix it and run: ghspot config validate")
        return 1

    # A configuration under /etc is root:ghspot 0640 and the checks want the Docker socket,
    # so the next command an operator types — in a shell where the wizard's sudo has already
    # expired — needs its own. Step 3 always knew this; step 2 did not.
    system = config_path.parent == Path("/etc/ghspot")
    doctor = f"{'sudo ' if system else ''}ghspot doctor -c {config_path}"

    console.print()
    console.print("[bold]Next[/bold]")
    console.print(f"  1. build the runner image   {build_command(answers.image)}")
    console.print(f"  2. check everything         {doctor}")
    console.print(
        "  3. start it                 sudo systemctl enable --now ghspot"
        if system
        else "  3. start it                 ghspot daemon"
    )
    if answers.api_bind:
        console.print(f"  4. the dashboard            http://{answers.api_bind}/ui")
    console.print()
    labels = escape(f'runs-on: [self-hosted, linux, x64, "{answers.image}"]'.replace('"', ""))
    # Escaped: a label list is square brackets, which Rich reads as a style tag — and the
    # one line telling somebody what to paste into their workflow is the line that vanishes.
    console.print(f"  [dim]Point a workflow at:  {labels}[/dim]")
    return 0
