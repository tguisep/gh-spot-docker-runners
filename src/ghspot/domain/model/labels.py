"""Runner labels, and the matching rule that decides which jobs a runner can take."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Self

from ghspot.domain.errors import InvalidLabelSetError

# GitHub rejects labels containing whitespace or control characters, and caps their length.
_LABEL = re.compile(r"^[^\s\x00-\x1f]{1,255}$")


@dataclass(frozen=True, slots=True)
class LabelSet:
    """An immutable, case-insensitive set of runner labels.

    GitHub matches labels case-insensitively, so labels are folded on construction while the
    originally written form is kept for display.
    """

    _folded: frozenset[str]
    _display: tuple[str, ...]

    @classmethod
    def of(cls, *labels: str) -> Self:
        return cls.from_iterable(labels)

    @classmethod
    def from_iterable(cls, labels: Iterable[str]) -> Self:
        display: list[str] = []
        folded: set[str] = set()

        for raw in labels:
            label = raw.strip()
            if not _LABEL.match(label):
                raise InvalidLabelSetError(f"{raw!r} is not a valid runner label")
            if label.casefold() not in folded:
                folded.add(label.casefold())
                display.append(label)

        if not folded:
            raise InvalidLabelSetError("a label set must contain at least one label")

        return cls(_folded=frozenset(folded), _display=tuple(display))

    def satisfies(self, required: LabelSet) -> bool:
        """Whether a runner carrying these labels may take a job requiring ``required``.

        GitHub's rule: every label the job asks for must be present on the runner. Extra
        labels on the runner are fine.
        """
        return required._folded <= self._folded

    def __contains__(self, label: str) -> bool:
        return label.strip().casefold() in self._folded

    def __iter__(self) -> Iterator[str]:
        return iter(self._display)

    def __len__(self) -> int:
        return len(self._folded)

    def __str__(self) -> str:
        return ", ".join(self._display)

    def as_list(self) -> list[str]:
        """The labels in the form they were written, for sending to the API."""
        return list(self._display)
