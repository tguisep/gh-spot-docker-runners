"""Building the runner images through the CLI.

The point of `ghspot image build` is that it works on a host that has no clone, so what
these check is the finding — not the building, which is `docker` and belongs to a real
machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghspot import paths
from ghspot.interfaces.cli.main import app
from ghspot.interfaces.cli.setup import IMAGES

runner = CliRunner()


def _sources(directory: Path) -> Path:
    """A stand-in for images/runner: a build.sh that reports how it was called."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "build.sh"
    script.write_text('#!/usr/bin/env bash\necho "called with: $*"\n')
    script.chmod(0o755)
    return directory


def test_the_packaged_sources_are_found_before_a_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An installed host has no clone; /usr/share is where the .deb puts them."""
    packaged = _sources(tmp_path / "share")
    monkeypatch.setattr(paths, "PACKAGED", packaged)
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    assert paths.runner_sources() == packaged


def test_a_checkout_is_found_when_nothing_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = _sources(tmp_path / "images" / "runner")
    monkeypatch.setattr(paths, "PACKAGED", tmp_path / "nowhere")
    monkeypatch.setattr(paths, "IN_TREE", checkout)

    assert paths.runner_sources() == checkout


def test_a_directory_without_a_build_script_is_not_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A half-copied install must read as "not there", not as a build that dies on line one."""
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(paths, "PACKAGED", tmp_path / "empty")
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    assert paths.runner_sources() is None


def test_an_explicit_location_is_the_whole_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Falling back from a typo in GHSPOT_RUNNER_IMAGES would build from somewhere the
    operator never named, and hand them an image they did not ask for."""
    monkeypatch.setattr(paths, "PACKAGED", _sources(tmp_path / "share"))
    monkeypatch.setenv("GHSPOT_RUNNER_IMAGES", str(tmp_path / "typo"))

    assert paths.runner_sources() is None


def test_build_runs_the_script_with_the_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(paths, "PACKAGED", _sources(tmp_path / "share"))
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    result = runner.invoke(app, ["image", "build", "rhel-9"])

    assert result.exit_code == 0
    assert "called with: rhel-9" in capfd.readouterr().out


def test_build_without_a_variant_builds_everything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """`build.sh` with no argument means every variant, and the CLI must not invent one."""
    monkeypatch.setattr(paths, "PACKAGED", _sources(tmp_path / "share"))
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    result = runner.invoke(app, ["image", "build"])

    assert result.exit_code == 0
    assert capfd.readouterr().out.strip() == "called with:"


def test_list_asks_the_script_rather_than_restating_the_variants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """The variant table lives in build.sh. Parsing a copy of it here is how the two drift."""
    monkeypatch.setattr(paths, "PACKAGED", _sources(tmp_path / "share"))
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    result = runner.invoke(app, ["image", "list"])

    assert result.exit_code == 0
    assert "called with: --list" in capfd.readouterr().out


def test_missing_sources_say_so_instead_of_failing_obscurely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "PACKAGED", tmp_path / "nowhere")
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "also-nowhere")

    result = runner.invoke(app, ["image", "build", "rhel-9"])

    assert result.exit_code == 2
    assert "runner image sources are not installed" in result.output


def test_the_real_variants_are_buildable_names(capfd: pytest.CaptureFixture) -> None:
    """The wizard offers a fixed list of images; every one has to be a variant build.sh
    knows, or setup writes a configuration whose image can never be built."""
    result = runner.invoke(app, ["image", "list"])

    assert result.exit_code == 0
    listed = capfd.readouterr().out
    for image in IMAGES:
        assert image in listed


def test_build_reports_a_script_it_cannot_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = tmp_path / "share"
    sources.mkdir()
    (sources / "build.sh").write_text("#!/usr/bin/env bash\n")  # present, not executable
    monkeypatch.setattr(paths, "PACKAGED", sources)
    monkeypatch.setattr(paths, "IN_TREE", tmp_path / "nowhere")

    result = runner.invoke(app, ["image", "build", "rhel-9"])

    assert result.exit_code == 1
    assert "could not run" in result.output
