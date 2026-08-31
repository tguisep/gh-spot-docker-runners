"""Writing a first configuration by filling in the commented reference.

The wizard used to emit eighteen lines: the four answers it asked for and nothing else. It
parsed, it ran, and it told an operator nothing about the thirty settings it had not
mentioned — the capacity ceilings, the housekeeping, the pool modes. The file that arrives
after `apt install` is the one somebody is least equipped to research, and it arrived empty.

So the output starts from `config.example.toml` instead, with the answers substituted into
it. There is no second copy of the prose to fall behind: the explanation an operator reads
beside a setting is the shipped one, and a setting added to the reference appears in the next
configuration the wizard writes without anybody remembering to add it here.

What is substituted is only what was asked. Everything else keeps exactly the value the
reference gives it — which for `idle_timeout`, `max_job_duration` and `max_launch_per_tick`
is the code's own default, so the file says out loud what the daemon would have done anyway.
The two exceptions are `cpus` and `memory`: unset they mean "no limit", and inheriting the
reference's illustration would cap every job at two cores on a machine nobody asked about.
Those are commented out, which is what "not asked" has to mean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SYSTEM_DIRECTORY = Path("/etc/ghspot")

HEADER = """\
# ghspot — written by `ghspot setup` from the commented reference.
#
# An ordinary configuration file: edit it freely. Every setting the daemon has is here, so
# there is nothing to go and look up — what the wizard asked about is set, and the rest is
# commented out at the value it would have used anyway.
#
# After editing:  ghspot config validate
"""


@dataclass(frozen=True, slots=True)
class Substitution:
    """One key of the reference, and what the wizard does to it."""

    section: str
    key: str
    value: str | None
    """The replacement value, or ``None`` to comment the line out."""


def _split_comment(line: str) -> tuple[str, str]:
    """Split a line into its code and its trailing comment.

    Quote-aware, because `state_db = "~/.local/state/ghspot/state.db"` has no comment and a
    naive `partition("#")` would find one in the day a path contained a fragment.
    """
    quote: str | None = None
    for index, character in enumerate(line):
        if quote is not None:
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character == "#":
            return line[:index].rstrip(), line[index:]
    return line.rstrip(), ""


def _is_key(line: str, key: str) -> bool:
    """Whether this line assigns `key`, commented out or not."""
    return re.match(rf"^\s*#?\s*{re.escape(key)}\s*=", line) is not None


def _apply(line: str, substitution: Substitution) -> str:
    if substitution.value is None:
        stripped = line.lstrip()
        return line if stripped.startswith("#") else f"# {line}"

    # A reference line may already be commented out — that is how the file spells "off by
    # default". Strip the marker before looking for a trailing comment, or the assignment
    # being replaced is mistaken for one and echoed back after its own replacement.
    body = line.lstrip()
    if body.startswith("#"):
        body = body.lstrip("#").lstrip()

    _, comment = _split_comment(body)
    rendered = f"{substitution.key} = {substitution.value}"
    return f"{rendered}   {comment}" if comment else rendered


def render(reference: str, substitutions: list[Substitution]) -> str:
    """The reference with each substitution applied to the first line that matches it.

    First line only, and each substitution used once: the reference ends with a whole second
    `[[pool]]` commented out as an illustration, and a rewrite that reached it would set the
    example's image and repository as well as the real pool's.
    """
    pending = {(item.section, item.key): item for item in substitutions}
    section = ""
    out: list[str] = []

    for line in reference.splitlines():
        if re.match(r"^\[.+\]$", line.strip()) and not line.lstrip().startswith("#"):
            section = line.strip()

        for (where, key), item in list(pending.items()):
            if where == section and _is_key(line, key):
                line = _apply(line, item)
                del pending[(where, key)]
                break

        out.append(line)

    return "\n".join(out) + "\n"


def replace_header(text: str) -> str:
    """Swap the reference's "Copy this to..." preamble for one that says where it came from."""
    lines = text.splitlines()
    end = 0
    while end < len(lines) and lines[end].startswith("#"):
        end += 1
    return HEADER + "\n".join(lines[end:]) + "\n"


def effective(text: str) -> str:
    """Just the settings that are on.

    The written file is the whole reference, which is the point — but printing two hundred
    lines back at somebody buries the four next steps under the commentary they were given
    the file to read later.
    """
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
