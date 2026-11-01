"""Variation engine: motif transformations that keep a bar's length intact.

A motif is a one-bar rhythm plus the diatonic steps between its notes. It has
no absolute pitch; ``melody.realize`` anchors it to a chord. Every transform
returns a new motif whose rhythm still fills exactly the same span.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Motif:
    rhythm: tuple[float, ...]  # beats per event; negative values are rests
    steps: tuple[int, ...]  # scale steps between consecutive sounding notes

    def __post_init__(self) -> None:
        if len(self.steps) != max(self.note_count - 1, 0):
            raise ValueError(f"{self.note_count} notes need {self.note_count - 1} steps, got {self.steps}")

    @property
    def note_count(self) -> int:
        return sum(1 for d in self.rhythm if d > 0)

    @property
    def length(self) -> float:
        return sum(abs(d) for d in self.rhythm)

    def note_index(self, position: int) -> int:
        """Index among sounding notes of the event at ``position`` in the rhythm."""
        return sum(1 for d in self.rhythm[:position] if d > 0)


def invert(m: Motif, rng: random.Random) -> Motif:
    return Motif(m.rhythm, tuple(-s for s in m.steps))


def retrograde(m: Motif, rng: random.Random) -> Motif:
    return Motif(m.rhythm[::-1], tuple(-s for s in reversed(m.steps)))


def split_note(m: Motif, rng: random.Random) -> Motif:
    """Divide one long note into two halves, the second a step away."""
    candidates = [i for i, d in enumerate(m.rhythm) if d >= 1]
    if not candidates:
        return m
    i = rng.choice(candidates)
    k = m.note_index(i)
    half = m.rhythm[i] / 2
    step = rng.choice((1, -1))
    steps = list(m.steps)
    # The new note sits `step` above note k, so the step onward shrinks by it.
    if k < len(steps):
        steps[k] -= step
    steps.insert(k, step)
    return Motif(m.rhythm[:i] + (half, half) + m.rhythm[i + 1 :], tuple(steps))


def merge_notes(m: Motif, rng: random.Random) -> Motif:
    """Join two adjacent short notes into one."""
    pairs = [i for i in range(len(m.rhythm) - 1) if 0 < m.rhythm[i] <= 1 and 0 < m.rhythm[i + 1] <= 1]
    if not pairs:
        return m
    i = rng.choice(pairs)
    k = m.note_index(i)
    steps = list(m.steps)
    # Note k absorbs note k+1; the step after that now starts from note k.
    joined = steps.pop(k)
    if k < len(steps):
        steps[k] += joined
    return Motif(m.rhythm[:i] + (m.rhythm[i] + m.rhythm[i + 1],) + m.rhythm[i + 2 :], tuple(steps))


def neighbor_tone(m: Motif, rng: random.Random) -> Motif:
    """Move one note after the first a step up or down; the others stay put."""
    if m.note_count < 2:
        return m
    k = rng.randrange(1, m.note_count)
    step = rng.choice((1, -1))
    steps = list(m.steps)
    steps[k - 1] += step
    if k < len(steps):
        steps[k] -= step
    return Motif(m.rhythm, tuple(steps))


TRANSFORMS: dict[str, Callable[[Motif, random.Random], Motif]] = {
    "invert": invert,
    "retrograde": retrograde,
    "split": split_note,
    "merge": merge_notes,
    "neighbor": neighbor_tone,
}


def vary(m: Motif, rng: random.Random, avoid: tuple[str, ...] = ()) -> tuple[str, Motif]:
    """Apply a random transform (not one named in ``avoid``) that changes ``m``."""
    names = [n for n in TRANSFORMS if n not in avoid]
    rng.shuffle(names)
    for name in names:
        varied = TRANSFORMS[name](m, rng)
        if varied != m:
            return name, varied
    return "neighbor", neighbor_tone(m, rng)
