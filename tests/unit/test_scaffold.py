"""Filling in the commented reference.

The renderer is line surgery on a file that ships as documentation, so what these pin down
is the ways line surgery goes wrong: matching too much, matching too little, and mangling
the commentary that is the reason for doing it this way at all.
"""

from __future__ import annotations

from ghspot.interfaces.cli.scaffold import (
    Substitution,
    effective,
    render,
    replace_header,
)

REFERENCE = """\
# ghspot
#
# Copy this somewhere.

[github]
token_file = "~/.config/ghspot/token"
# app_id = "123456"

[daemon]
# api_bind = "127.0.0.1:8770"
state_db = "~/.local/state/ghspot/state.db"

[[pool]]
name = "default"
max_runners = 4       # hard ceiling for this pool

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
cpus = 2.0

# A second pool, as an illustration.
#
# [[pool]]
# name = "rhel"
# [pool.container]
# image = "ghspot/runner:rhel-9"
"""


def test_a_live_setting_is_replaced() -> None:
    out = render(REFERENCE, [Substitution("[[pool]]", "name", '"builders"')])

    assert 'name = "builders"' in out
    assert 'name = "default"' not in out


def test_a_commented_setting_is_turned_on() -> None:
    """That is how the reference spells "off by default", and answering the question about
    it has to be enough to switch it on."""
    out = render(REFERENCE, [Substitution("[daemon]", "api_bind", '"0.0.0.0:8770"')])

    assert 'api_bind = "0.0.0.0:8770"' in out
    assert "# api_bind" not in out


def test_turning_a_commented_setting_on_does_not_echo_it_as_its_own_comment() -> None:
    """The whole line is the commented-out assignment, not a trailing note about one."""
    out = render(REFERENCE, [Substitution("[daemon]", "api_bind", '"0.0.0.0:8770"')])

    assert 'api_bind = "0.0.0.0:8770"' in out
    assert out.count("127.0.0.1:8770") == 0


def test_the_trailing_commentary_survives_a_replacement() -> None:
    """The explanation beside a setting is the reason for filling in the reference rather
    than writing a file of our own."""
    out = render(REFERENCE, [Substitution("[[pool]]", "max_runners", "3")])

    assert "max_runners = 3   # hard ceiling for this pool" in out


def test_a_setting_can_be_commented_out() -> None:
    out = render(REFERENCE, [Substitution("[pool.container]", "cpus", None)])

    assert "# cpus = 2.0" in out


def test_a_substitution_is_confined_to_its_own_section() -> None:
    """`name` appears under [[pool]] and again in the illustration below."""
    out = render(REFERENCE, [Substitution("[github]", "token_file", '"/etc/ghspot/token"')])

    assert 'name = "default"' in out


def test_the_illustration_at_the_end_is_left_alone() -> None:
    """It sits under the last real section header, so a rewrite that fired more than once
    would set the example's image as well as the real one."""
    out = render(REFERENCE, [Substitution("[pool.container]", "image", '"ghspot/runner:rhel-9"')])

    assert '# image = "ghspot/runner:rhel-9"' in out
    assert out.count('image = "ghspot/runner:rhel-9"') == 2  # the real one, and the comment


def test_a_key_the_reference_does_not_have_changes_nothing() -> None:
    out = render(REFERENCE, [Substitution("[daemon]", "not_a_setting", '"x"')])

    assert out.strip() == REFERENCE.strip()


def test_the_header_says_where_the_file_came_from() -> None:
    out = replace_header(REFERENCE)

    assert "Copy this somewhere" not in out
    assert "ghspot setup" in out
    assert "[github]" in out


def test_effective_shows_only_what_is_on() -> None:
    out = effective(REFERENCE)

    assert 'token_file = "~/.config/ghspot/token"' in out
    assert "app_id" not in out
    assert "illustration" not in out
