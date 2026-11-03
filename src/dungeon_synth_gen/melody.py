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
    (0.75, 0.25, 1, 2),
    (1, 1, 0.5, 0.5, 1),
    (2, 0.5, 0.5, 1),
    (0.5, 1, 0.5, 2),
    (1, -0.5, 0.5, 2),
    (-1, 1, 1, 1),
    (0.5, 0.5, 1, 0.5, 0.5, 1),
    (1.5, 1.5, 1),
    (1, 1.5, 0.5, 1),
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
# Every one ends on a held note; a negative value is a rest.
CADENCE_RHYTHMS: tuple[tuple[float, ...], ...] = (
    (4,), (1, 3), (0.5, 0.5, 3), (2, 2), (1, 1, 2), (1.5, 0.5, 2), (-1, 1, 2), (0.75, 0.25, 3), (0.5, 0.5, 1, 2), (-2, 2),
)
# Ways into the final note, as steps from it, last note first: straight down a scale,
# a turn round it, a fall from a leap.
_APPROACHES: tuple[tuple[int, ...], ...] = ((1, 2, 3), (-1, 1, 2), (1, -1, 0), (2, 1, 3), (3, 4, 2), (-1, -2, 1))

_STEP_CHOICES = (-1, 1, -2, 2, 0, -3, 3)
_STEP_WEIGHTS = (30, 30, 10, 10, 6, 4, 4)
LOW, HIGH = -3, 9  # lead range in scale degrees around the lead tonic: up to about a tenth above it


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

    The first note, notes on beats 1 and 3, and anything a quarter or longer
    snap to the nearest chord tone, so the melody agrees with the harmony. Only
    short notes pass between them, and only by step: a note outside the chord
    that would be leapt into and out of snaps too, since that is what sounds wrong.
    """
    notes: list[_Note] = []
    degree = nearest_chord_tone(anchor, chord)
    steps = list(motif.steps)
    t = 0.0
    for d in motif.rhythm:
        if d < 0:
            t -= d
            continue
        if notes:
            step = steps[len(notes) - 1]
            following = steps[len(notes)] if len(notes) < len(steps) else 0
            degree = _reflect(degree + step)
            leapt = abs(step) >= 2 and abs(following) >= 2
            if t % 2 == 0 or d >= 1 or leapt:
                degree = nearest_chord_tone(degree, chord, step)
        notes.append(_Note(bar_start + t, d, degree))
        t += d
    return notes


def cadence(rng: random.Random, chord: int, previous: int, bar_start: float, resolve: bool) -> list[_Note]:
    """Close a phrase on a chord tone (the tonic, if ``resolve``), reached by one of ``_APPROACHES``, and hold it.

    Approach notes that land on beat 1 or 3, or are held, are chord tones too.
    """
    if resolve:
        target = min((0, 7), key=lambda d: abs(d - previous))
        rhythm = rng.choice([r for r in CADENCE_RHYTHMS if sum(1 for d in r if d > 0) > 1])
    else:
        target = nearest_chord_tone(previous, chord, -1)
        rhythm = rng.choice(CADENCE_RHYTHMS)
    direction = 1 if previous >= target else -1  # approach from the side we came from
    approach = rng.choice(_APPROACHES)
    sounding = sum(1 for d in rhythm if d > 0)
    notes = []
    t = 0.0
    for d in rhythm:
        if d < 0:
            t -= d
            continue
        before = sounding - 1 - len(notes)  # notes still to come before the final one
        offset = approach[before - 1] if before else 0
        degree = target + direction * offset
        if before and (t % 2 == 0 or d >= 1):  # an approach note on a strong beat, or a quarter or longer, sits in the chord
            degree = nearest_chord_tone(degree, chord, direction)
        notes.append(_Note(bar_start + t, d, degree))
        t += d
    return notes


def melody_phrase(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], motifs: tuple[Motif, ...],
    anchor: int, resolve: bool, boost: int = 0, shifts: tuple[int, ...] = (),
) -> list[NoteEvent]:
    """Motif bars sequenced from the first bar's opening note, then a cadence bar.

    ``shifts`` moves each bar's starting point that many scale steps from the
    opening note, so a phrase can climb or sink instead of restating itself.
    With ``resolve`` the cadence lands on the tonic. Times are beats from the
    phrase start.
    """
    notes: list[_Note] = []
    for i, motif in enumerate(motifs):
        shift = shifts[i] if i < len(shifts) else 0
        bar = realize(motif, chords[i], anchor + shift, i * BEATS_PER_BAR)
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
