"""Lead melody: motifs realised over chords, one phrase at a time."""

from __future__ import annotations

import random
from dataclasses import dataclass

from dungeon_synth_gen.events import BEATS_PER_BAR, NoteEvent
from dungeon_synth_gen.theory import Scale, nearest_chord_tone
from dungeon_synth_gen.variation import Motif

MOTIF_RHYTHMS: tuple[tuple[float, ...], ...] = (
    (1, 1, 1, 1),
    (1.5, 0.5, 1, 1),
    (1, 0.5, 0.5, 2),
    (2, 1, 1),
    (1.5, 0.5, 2),
    (0.5, 0.5, 1, 2),
    (1, 1, 2),
    (1, 0.5, 0.5, 1, 1),
    (1.5, 0.5, 1, -1),
    (0.5, 0.5, 0.5, 0.5, 2),
    (3, 0.5, 0.5),
)
# Ambient: long notes and rests, so the melody leaves room to breathe.
AMBIENT_RHYTHMS: tuple[tuple[float, ...], ...] = (
    (2, 2),
    (3, 1),
    (1, 3),
    (2, 1, 1),
    (1, 1, 2),
    (-1, 1, 2),
    (2, -1, 1),
    (1.5, 0.5, 2),
    (-2, 1, 1),
)
CADENCE_RHYTHMS: tuple[tuple[float, ...], ...] = ((4,), (1, 3), (0.5, 0.5, 3), (2, 2))

_STEP_CHOICES = (-1, 1, -2, 2, 0, -3, 3)
_STEP_WEIGHTS = (30, 30, 10, 10, 6, 4, 4)
LOW, HIGH = -3, 11  # lead range in scale degrees around the lead tonic


@dataclass(frozen=True)
class _Note:
    start: float
    duration: float
    degree: int


def random_motif(
    rng: random.Random, avoid_rhythm: tuple[float, ...] | None = None, rhythms: tuple[tuple[float, ...], ...] = MOTIF_RHYTHMS
) -> Motif:
    rhythm = rng.choice([r for r in rhythms if r != avoid_rhythm])
    steps: list[int] = []
    height = 0
    for _ in range(sum(1 for d in rhythm if d > 0) - 1):
        if steps and abs(steps[-1]) >= 2:
            step = -1 if steps[-1] > 0 else 1  # step back to fill the gap a leap left
        else:
            step = rng.choices(_STEP_CHOICES, _STEP_WEIGHTS)[0]
            if abs(height + step) > 4:
                step = -step
        steps.append(step)
        height += step
    return Motif(rhythm, tuple(steps))


def _reflect(degree: int) -> int:
    if degree > HIGH:
        return 2 * HIGH - degree
    if degree < LOW:
        return 2 * LOW - degree
    return degree


def realize(motif: Motif, chord: int, anchor: int, bar_start: float) -> list[_Note]:
    """Place ``motif`` in a bar over ``chord``, starting near degree ``anchor``.

    The first note, notes on beats 1 and 3, and long notes snap to the nearest
    chord tone so the melody agrees with the harmony; notes between them pass
    freely through the scale.
    """
    notes: list[_Note] = []
    degree = nearest_chord_tone(anchor, chord)
    steps = iter(motif.steps)
    t = 0.0
    for d in motif.rhythm:
        if d < 0:
            t -= d
            continue
        if notes:
            step = next(steps)
            degree = _reflect(degree + step)
            if t % 2 == 0 or d >= 1.5:
                degree = nearest_chord_tone(degree, chord, step)
        notes.append(_Note(bar_start + t, d, degree))
        t += d
    return notes


def cadence(rng: random.Random, chord: int, previous: int, bar_start: float, resolve: bool) -> list[_Note]:
    """Close a phrase by stepping into a chord tone (the tonic, if ``resolve``) and holding it."""
    if resolve:
        target = min((0, 7), key=lambda d: abs(d - previous))
        rhythm = rng.choice(CADENCE_RHYTHMS[1:])
    else:
        target = nearest_chord_tone(previous, chord, -1)
        rhythm = rng.choice(CADENCE_RHYTHMS)
    direction = 1 if previous >= target else -1  # approach from the side we came from
    notes = []
    t = 0.0
    for i, d in enumerate(rhythm):
        notes.append(_Note(bar_start + t, d, target + direction * (len(rhythm) - 1 - i)))
        t += d
    return notes


def melody_phrase(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], motifs: tuple[Motif, ...],
    anchor: int, resolve: bool, boost: int = 0,
) -> list[NoteEvent]:
    """Motif bars sequenced from the first bar's opening note, then a cadence bar.

    With ``resolve`` the cadence lands on the tonic. Times are beats from the
    phrase start.
    """
    notes: list[_Note] = []
    for i, motif in enumerate(motifs):
        bar = realize(motif, chords[i], anchor, i * BEATS_PER_BAR)
        if i == 0:
            anchor = bar[0].degree
        notes += bar
    notes += cadence(rng, chords[len(motifs)], notes[-1].degree, len(motifs) * BEATS_PER_BAR, resolve)

    events = []
    for note in notes:
        beat = note.start % BEATS_PER_BAR
        accent = 10 if beat == 0 else 4 if beat == 2 else 0
        gate = note.duration if note.duration >= 3 else note.duration * 0.9
        velocity = min(127, 84 + accent + boost + rng.randint(-3, 3))
        events.append(NoteEvent("lead", note.start, gate, scale.pitch(note.degree, tonic), velocity))
    return events
