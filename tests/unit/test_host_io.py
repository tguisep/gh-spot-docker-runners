"""The disk-busy probe: reading `/proc/diskstats`, and turning counters into a rate.

Pure enough to test without a Docker daemon, which is the point of keeping the parsing and
the arithmetic out of the method that talks to the Engine.

Field 13 of a `/proc/diskstats` line (`parts[12]`) is `ms doing I/O` — the counter `iostat`
divides by wall time to print `%util`. Getting that index wrong reads a plausible-looking
number off the wrong column, which is why the fixture keeps the real field positions.
"""

from __future__ import annotations

import pytest

from ghspot.infrastructure.docker.backend import (
    _MAX_IO_WINDOW_SECONDS,
    DockerRunnerBackend,
    _io_sample,
    _io_ticks_for,
    _IoSample,
)

# Shape taken from a real host — an LVM volume on NVMe, which is the arrangement that would
# break a `/proc/mounts` parser. The read and write counters are trimmed to keep the lines
# readable; what the tests turn on is the *position* of `io_ticks` and the major:minor pairs.
#
#          major minor name    r  rm  rs  rms   w  wm  ws  wms  inflight  io_ticks  weighted
DISKSTATS = """
 259       0 nvme0n1 245 63 839 397 391 248 177 605 0 6747683 609938501
 259       1 nvme0n1p1 2673 3551 120478 1264 3 0 10 1 0 119 1269
 252       0 dm-0 267 0 689 638 637 0 176 904 0 7559626 910419800
 252       1 dm-1 256 0 689 603 584 0 176 372 0 21734064 3728066409
""".strip().splitlines()


def backend() -> DockerRunnerBackend:
    """The adapter without its Engine connection: none of this touches Docker."""
    instance = DockerRunnerBackend.__new__(DockerRunnerBackend)
    instance._client = None
    instance._io_sample = None
    return instance


# ---------------------------------------------------------------- finding the device


def test_the_exact_device_wins() -> None:
    """A filesystem on LVM reports the mapper device, and that is the row to read."""
    assert _io_ticks_for(DISKSTATS, 252, 1) == 21734064


def test_a_partition_with_no_row_falls_back_to_its_whole_disk() -> None:
    """An answer about the whole disk is a slightly wider question than was asked, and much
    better than reporting a busy device as idle — which is what a missing row would do."""
    assert _io_ticks_for(DISKSTATS, 259, 4) == 6747683


def test_a_device_that_is_not_there_at_all_is_unknown() -> None:
    assert _io_ticks_for(DISKSTATS, 8, 0) is None


def test_a_truncated_or_junk_line_is_skipped_rather_than_fatal() -> None:
    """`/proc` formats have only ever grown, but a short read or a hand-written fixture must
    not take the probe down with it."""
    lines = ["", "not a diskstats line", " 252  1 dm-1 1 2 3", *DISKSTATS]

    assert _io_ticks_for(lines, 252, 1) == 21734064


def test_a_path_that_does_not_exist_yields_no_sample() -> None:
    assert _io_sample("/definitely/not/a/directory") is None


# ---------------------------------------------------------------- counters into a rate


def test_the_first_probe_of_a_process_reports_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Utilisation is a rate, and a rate needs two readings. Saying "I do not know" is the
    honest answer, and the admission policy reads it as no reason to hold back."""
    instance = backend()
    monkeypatch.setattr(
        "ghspot.infrastructure.docker.backend._io_sample",
        lambda root: _IoSample(at=100.0, io_ticks=0),
    )

    assert instance._io_percent("/var/lib/docker") is None


def _probe(monkeypatch: pytest.MonkeyPatch, samples: list[_IoSample | None]) -> list[float | None]:
    instance = backend()
    monkeypatch.setattr(
        "ghspot.infrastructure.docker.backend._io_sample",
        lambda root: samples.pop(0),
    )
    return [instance._io_percent("/var/lib/docker") for _ in range(2)]


def test_half_the_window_spent_busy_reads_as_fifty_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = _probe(
        monkeypatch,
        [_IoSample(at=100.0, io_ticks=0), _IoSample(at=110.0, io_ticks=5_000)],
    )

    assert readings == [None, 50.0]


def test_a_saturated_device_reads_as_one_hundred(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = _probe(
        monkeypatch,
        [_IoSample(at=100.0, io_ticks=0), _IoSample(at=110.0, io_ticks=10_000)],
    )

    assert readings[1] == 100.0


def test_a_reading_over_the_window_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """io_ticks should never exceed wall time, but it is not incremented against the clock
    this is measured with — and a gauge reading 104% is a gauge nobody believes."""
    readings = _probe(
        monkeypatch,
        [_IoSample(at=100.0, io_ticks=0), _IoSample(at=110.0, io_ticks=10_400)],
    )

    assert readings[1] == 100.0


def test_a_counter_that_went_backwards_is_not_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device was replaced underneath us, or the machine was suspended. Either way there
    is nothing to say, and the sample just taken becomes the new baseline."""
    readings = _probe(
        monkeypatch,
        [_IoSample(at=100.0, io_ticks=9_000), _IoSample(at=110.0, io_ticks=10)],
    )

    assert readings[1] is None


def test_a_window_too_short_to_mean_anything_is_not_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two forced reconciles a fifth of a second apart. The ratio there is mostly rounding,
    and it would swing between 0 and several hundred percent."""
    readings = _probe(
        monkeypatch,
        [_IoSample(at=100.0, io_ticks=0), _IoSample(at=100.2, io_ticks=100)],
    )

    assert readings[1] is None


def test_a_window_too_long_becomes_a_fresh_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe only runs when a launch is wanted and a mark is set, so a quiet host can
    leave hours between samples — and an hour's average is not an answer to "is it busy now"."""
    readings = _probe(
        monkeypatch,
        [
            _IoSample(at=100.0, io_ticks=0),
            _IoSample(at=100.0 + _MAX_IO_WINDOW_SECONDS + 1, io_ticks=10_000_000),
        ],
    )

    assert readings[1] is None


def test_an_unreadable_probe_does_not_poison_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed read must not leave a stale sample behind for the following one to subtract
    from — that would report a rate over a window nobody measured."""
    instance = backend()
    samples: list[_IoSample | None] = [
        _IoSample(at=100.0, io_ticks=0),
        None,
        _IoSample(at=120.0, io_ticks=999_999),
    ]
    monkeypatch.setattr(
        "ghspot.infrastructure.docker.backend._io_sample",
        lambda root: samples.pop(0),
    )

    assert [instance._io_percent("/x") for _ in range(3)] == [None, None, None]
