from __future__ import annotations

from dataclasses import replace

import pytest

from tests.unit.conftest import T0, at, make_job


def test_a_job_reports_how_long_it_has_waited() -> None:
    job = make_job(1, queued_at=T0)
    assert job.waiting_for(at(minutes=3)) == pytest.approx(180.0)


def test_a_clock_that_went_backwards_never_yields_a_negative_wait() -> None:
    job = make_job(1, queued_at=at(minutes=5))
    assert job.waiting_for(T0) == 0.0


def test_a_job_renders_as_repository_and_name() -> None:
    assert str(make_job(7)) == "tguisep/gh-spot-docker-runners#job 7"


def test_a_named_job_uses_its_name() -> None:
    named = replace(make_job(7), job_name="build")
    assert str(named) == "tguisep/gh-spot-docker-runners#build"
