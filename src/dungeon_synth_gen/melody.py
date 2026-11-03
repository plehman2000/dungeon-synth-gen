"""Lead melody: motifs realised over chords, one phrase at a time."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from dungeon_synth_gen.events import BEATS_PER_BAR, NoteEvent
from dungeon_synth_gen.theory import Scale, is_chord_tone, nearest_chord_tone
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
    (4,),
    (4,),
    (2, 2),
    (3, 1),
    (1, 3),
    (-1, 3),
    (-2, 2),
    (2, -1, 1),
    (2, 1, 1),
    (-1, 1, 2),
)
# Dungeon (normal): heavy and unhurried. Halves, quarters and held notes; no skipping dotted figures.
DUNGEON_RHYTHMS: tuple[tuple[float, ...], ...] = (
    (2, 2),
    (3, 1),
    (4,),
    (2, 1, 1),
    (1, 1, 2),
    (1, 3),
    (2, -1, 1),
    (-1, 1, 2),
    (1, 1, 1, 1),
    (3, 0.5, 0.5),
    (2, 0.5, 0.5, 1),
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


_HARSH = (1, 6, 11)  # semitones from a drone note: a minor second, a tritone, a major seventh


def _clashes(scale: Scale, degree: int) -> bool:
    """True if ``degree`` sits a semitone or a tritone from the drone (tonic and fifth, held under every region)."""
    pitch = scale.pitch(degree, 0)
    return any((pitch - scale.pitch(d, 0)) % 12 in _HARSH for d in (0, 4))


def _clear_the_drone(notes: list[_Note], scale: Scale, chords: tuple[int, ...]) -> None:
    """Move held or accented notes that grind against the drone to the nearest chord tone that doesn't.

    Over Phrygian's bII, for instance, the lead holds its fifth instead of its root:
    the dark chord stays in the harmony, and the b2 and b5 only pass by in short notes.
    """
    for i, note in enumerate(notes):
        accented = note.start % 2 == 0 or note.duration >= 0.9
        if not accented or not _clashes(scale, note.degree):
            continue
        chord = chords[min(int(note.start // BEATS_PER_BAR), len(chords) - 1)]
        options = [d for d in range(LOW, HIGH + 1) if is_chord_tone(d, chord) and not _clashes(scale, d)]
        if options:
            notes[i] = replace(note, degree=min(options, key=lambda d: (abs(d - note.degree), d)))


def _smooth_tritones(notes: list[_Note], scale: Scale, chords: tuple[int, ...]) -> None:
    """Re-pick any note a tritone from the one before it, a step or two over.

    A held or accented note stays a chord tone, and the phrase's last note stays
    where the cadence put it (the note before it moves instead).
    """
    def semitones(a: int, b: int) -> int:
        return abs(scale.pitch(a, 0) - scale.pitch(b, 0))

    for i in range(1, len(notes)):
        if semitones(notes[i].degree, notes[i - 1].degree) != 6:
            continue
        fix = i if i < len(notes) - 1 else i - 1
        if fix == 0:
            continue
        note, before = notes[fix], notes[fix - 1].degree
        after = notes[fix + 1].degree if fix + 1 < len(notes) else None
        chord = chords[min(int(note.start // BEATS_PER_BAR), len(chords) - 1)]
        accented = note.start % 2 == 0 or note.duration >= 1
        for degree in (note.degree - 1, note.degree + 1, note.degree - 2, note.degree + 2):
            clean = semitones(degree, before) != 6 and (after is None or semitones(degree, after) != 6)
            if clean and LOW <= degree <= HIGH and (not accented or (is_chord_tone(degree, chord) and not _clashes(scale, degree))):
                notes[fix] = replace(note, degree=degree)
                break


COUNTERS = ("thirds", "line")
_COUNTER_LOW, _COUNTER_HIGH = -5, 4  # the counter voice sits under the lead, around the lead's tonic


def _chord_at(chords: tuple[int, ...], start: float) -> int:
    return chords[min(int(start // BEATS_PER_BAR), len(chords) - 1)]


def _under(scale: Scale, degree: int, chord: int, lead: int | None) -> bool:
    """A note the counter may hold: a chord tone clear of the drone, a third or more under the lead, never its octave."""
    if not (_COUNTER_LOW <= degree <= _COUNTER_HIGH and is_chord_tone(degree, chord)) or _clashes(scale, degree):
        return False
    return lead is None or (degree <= lead - 2 and (lead - degree) % 7 != 0)


def _thirds(notes: list[_Note], scale: Scale, chords: tuple[int, ...]) -> list[_Note]:
    """A second voice under every held lead note: a third below, else a sixth, else a fifth."""
    voice = []
    for note in notes:
        if note.duration < 1:
            continue
        chord = _chord_at(chords, note.start)
        for gap in (2, 5, 4):
            if _under(scale, note.degree - gap, chord, note.degree):
                voice.append(_Note(note.start, note.duration, note.degree - gap))
                break
    return voice


def _line(notes: list[_Note], scale: Scale, chords: tuple[int, ...]) -> list[_Note]:
    """A slow counter-melody in half notes (a whole note in the cadence bar) that moves against the lead.

    Each note is a chord tone under the lead, as close as possible to the one before it; it moves
    the other way when the lead moves, and holds on (one longer note) when that is closest.
    """
    voice: list[_Note] = []
    previous = lead_before = None
    for bar, chord in enumerate(chords):
        last = bar == len(chords) - 1
        for beat in (0,) if last else (0, 2):
            t = bar * BEATS_PER_BAR + beat
            sounding = [n for n in notes if n.start <= t]
            lead = sounding[-1].degree if sounding else None
            span = range(_COUNTER_LOW, _COUNTER_HIGH + 1)
            options = [d for d in span if _under(scale, d, chord, lead)] or [d for d in span if _under(scale, d, chord, None)]
            if not options:
                continue
            heading = 0 if lead is None or lead_before is None else (lead > lead_before) - (lead < lead_before)

            def cost(d: int) -> float:
                if previous is None:
                    return abs(d + 2)  # open a little under the lead's tonic
                motion = (d > previous) - (d < previous)
                return abs(d - previous) + (2 if motion and motion == heading else 0)

            degree = min(options, key=lambda d: (cost(d), d))
            length = BEATS_PER_BAR if last else 2
            if voice and voice[-1].degree == degree and voice[-1].start + voice[-1].duration == t:
                voice[-1] = replace(voice[-1], duration=voice[-1].duration + length)  # linger
            else:
                voice.append(_Note(t, length, degree))
            previous = degree
            lead_before = lead if lead is not None else lead_before
    return voice


def melody_phrase(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], motifs: tuple[Motif, ...],
    anchor: int, resolve: bool, boost: int = 0, shifts: tuple[int, ...] = (), counter: str | None = None,
) -> list[NoteEvent]:
    """Motif bars sequenced from the first bar's opening note, then a cadence bar.

    ``shifts`` moves each bar's starting point that many scale steps from the
    opening note, so a phrase can climb or sink instead of restating itself.
    With ``resolve`` the cadence lands on the tonic. ``counter`` (one of
    ``COUNTERS``) adds a second voice under the lead, on the "counter" track.
    Times are beats from the phrase start.
    """
    notes: list[_Note] = []
    for i, motif in enumerate(motifs):
        shift = shifts[i] if i < len(shifts) else 0
        bar = realize(motif, chords[i], anchor + shift, i * BEATS_PER_BAR)
        if i == 0:
            anchor = bar[0].degree
        notes += bar
    notes += cadence(rng, chords[len(motifs)], notes[-1].degree, len(motifs) * BEATS_PER_BAR, resolve)
    _clear_the_drone(notes, scale, chords)
    _smooth_tritones(notes, scale, chords)

    events = []
    for note in notes:
        beat = note.start % BEATS_PER_BAR
        accent = 10 if beat == 0 else 4 if beat == 2 else 0
        gate = note.duration if note.duration >= 3 else note.duration * 0.9
        velocity = min(127, 84 + accent + boost + rng.randint(-3, 3))
        events.append(NoteEvent("lead", note.start, gate, scale.pitch(note.degree, tonic), velocity))
    if counter is not None:
        voice = _thirds(notes, scale, chords) if counter == "thirds" else _line(notes, scale, chords)
        for note in voice:
            accent = 6 if note.start % BEATS_PER_BAR == 0 else 0
            gate = note.duration if note.duration >= 3 else note.duration * 0.9
            velocity = min(127, 66 + accent + boost // 2)
            events.append(NoteEvent("counter", note.start, gate, scale.pitch(note.degree, tonic), velocity))
    return events
