from __future__ import annotations

import pytest

from ghspot.domain.errors import InvalidLabelSetError
from ghspot.domain.model.labels import LabelSet


def test_matching_is_a_subset_rule() -> None:
    """GitHub's rule: a runner takes a job when it carries every label the job asks for."""
    runner = LabelSet.of("self-hosted", "linux", "x64", "home-vm")

    assert runner.satisfies(LabelSet.of("self-hosted", "linux"))
    assert runner.satisfies(runner)
    assert not runner.satisfies(LabelSet.of("self-hosted", "gpu"))


def test_matching_ignores_case() -> None:
    assert LabelSet.of("Self-Hosted", "Linux").satisfies(LabelSet.of("self-hosted", "linux"))


def test_duplicates_collapse_but_the_written_form_survives() -> None:
    labels = LabelSet.of("Linux", "linux", "LINUX")
    assert len(labels) == 1
    assert labels.as_list() == ["Linux"]


def test_membership_and_iteration() -> None:
    labels = LabelSet.of("self-hosted", "linux")
    assert "SELF-HOSTED" in labels
    assert "gpu" not in labels
    assert set(labels) == {"self-hosted", "linux"}
    assert str(labels) == "self-hosted, linux"


@pytest.mark.parametrize("bad", ["", "   ", "has space", "tab\there", "null\x00byte", "x" * 256])
def test_labels_github_would_reject_are_refused(bad: str) -> None:
    with pytest.raises(InvalidLabelSetError):
        LabelSet.of(bad)


def test_an_empty_set_is_refused() -> None:
    """A runner with no labels can never be targeted, so it is a configuration error."""
    with pytest.raises(InvalidLabelSetError):
        LabelSet.from_iterable([])
