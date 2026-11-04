"""Music theory engine: modes, scale degrees, triads, and chord progressions.

Pitches inside the generator are *scale degrees*: integers where 0 is the
tonic, 7 is the tonic an octave up, and -1 is the note below the tonic.
Working in degrees keeps every melodic transformation diatonic for free.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

MODES: dict[str, tuple[int, ...]] = {
    "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "harmonic": (0, 2, 3, 5, 7, 8, 11),
    # Major-side modes, for the calm ambient style.
    "ionian": (0, 2, 4, 5, 7, 9, 11),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
}

# Weighted Markov transitions between chord roots, as 0-based scale degrees
# (0 = i, 1 = ii/bII, ..., 6 = VII). Targets that form a diminished triad in
# the active mode are dropped when sampling.
_TRANSITIONS: dict[int, dict[int, float]] = {
    0: {1: 1.0, 2: 1.0, 3: 3.0, 4: 1.5, 5: 3.0, 6: 3.0},
    1: {0: 4.0, 4: 1.0, 6: 1.0},
    2: {3: 2.0, 5: 2.0, 6: 2.0, 0: 1.0},
    3: {0: 2.0, 4: 2.0, 6: 2.0, 5: 1.0, 1: 1.0},
    4: {0: 4.0, 5: 2.0, 3: 1.0},
    5: {6: 3.0, 3: 2.0, 4: 2.0, 2: 1.0, 1: 1.0, 0: 1.0},
    6: {0: 3.0, 2: 2.0, 5: 2.0, 1: 1.0},
}
# Mode flavor: Phrygian leans on bII <-> i, harmonic minor on its major V.
_MODE_BOOST: dict[str, dict[tuple[int, int], float]] = {
    "phrygian": {(0, 1): 4.0, (1, 0): 3.0, (6, 1): 2.0},
    "harmonic": {(3, 4): 3.0, (5, 4): 3.0},
    "dorian": {(0, 3): 2.0, (3, 0): 2.0},
    "ionian": {(0, 3): 2.0, (3, 4): 2.0, (4, 0): 2.0, (5, 3): 2.0, (1, 4): 2.0},
    "lydian": {(0, 1): 4.0, (1, 0): 3.0},
    "mixolydian": {(0, 6): 4.0, (6, 0): 3.0, (6, 3): 2.0},
}

# Chords that resolve to i at the end of a phrase.
CADENCE_CHORDS: dict[str, tuple[int, ...]] = {
    "aeolian": (6, 4),
    "phrygian": (1, 6),
    "dorian": (3, 6),
    "harmonic": (4,),
    "ionian": (4, 3),
    "lydian": (1, 4),
    "mixolydian": (6, 3),
}
# Every mode combat may play in: never a major one.
GRIM_MODES = frozenset(("phrygian", "harmonic", "aeolian", "dorian"))

# Chords a phrase may pause on before the theme comes back around.
_HALF_CADENCE_CHORDS = (1, 3, 4, 6)
# Chords that open the contrasting section away from the tonic.
_CONTRAST_OPENINGS = (2, 3, 5)


def parse_note(name: str) -> int:
    """Pitch class (0-11) for a note name such as ``"D"``, ``"F#"`` or ``"Bb"``."""
    key = _FLATS.get(name.strip().capitalize(), name.strip().capitalize())
    try:
        return NOTE_NAMES.index(key)
    except ValueError:
        raise ValueError(f"unknown note name: {name!r}") from None


def place(pitch_class: int, low: int) -> int:
    """Lowest MIDI note at or above ``low`` with the given pitch class."""
    return low + (pitch_class - low) % 12


def midi_to_freq(note: float) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12)


@dataclass(frozen=True)
class Scale:
    root: int  # pitch class of the tonic, 0-11
    mode: str

    @property
    def name(self) -> str:
        return f"{NOTE_NAMES[self.root]} {self.mode}"

    def pitch(self, degree: int, tonic: int) -> int:
        """MIDI note for ``degree``, where ``tonic`` is the MIDI note of degree 0."""
        octave, step = divmod(degree, 7)
        return tonic + 12 * octave + MODES[self.mode][step]

    def contains(self, note: int) -> bool:
        return (note - self.root) % 12 in MODES[self.mode]

    def triad_quality(self, degree: int) -> str:
        root = self.pitch(degree, 0)
        third = self.pitch(degree + 2, 0) - root
        fifth = self.pitch(degree + 4, 0) - root
        if fifth == 6:
            return "dim"
        if fifth == 8:
            return "aug"
        return "major" if third == 4 else "minor"

    def roman(self, degree: int) -> str:
        """Roman numeral for the triad on ``degree``, relative to natural minor or major."""
        numeral = ("I", "II", "III", "IV", "V", "VI", "VII")[degree % 7]
        quality = self.triad_quality(degree)
        if quality in ("minor", "dim"):
            numeral = numeral.lower()
        reference = "ionian" if MODES[self.mode][2] == 4 else "aeolian"
        offset = MODES[self.mode][degree % 7] - MODES[reference][degree % 7]
        accidental = "b" if offset < 0 else "#" if offset > 0 else ""
        suffix = {"dim": "o", "aug": "+"}.get(quality, "")
        return accidental + numeral + suffix


def darken(scale: Scale, modes: frozenset[str] = GRIM_MODES) -> Scale:
    """Combat's key: a mode in ``modes`` stays as it is; any other turns plain minor on the same tonic.

    No exotic remapping: the calm music's own notes, only never major. A flavour can
    narrow ``modes``, as brut does to aeolian and dorian.
    """
    if scale.mode in modes:
        return scale
    return Scale(scale.root, "aeolian" if "aeolian" in modes else min(modes))


def is_chord_tone(degree: int, chord: int) -> bool:
    return (degree - chord) % 7 in (0, 2, 4)


def nearest_chord_tone(degree: int, chord: int, direction: int = 1) -> int:
    """Closest triad tone of ``chord`` to ``degree``; ties go ``direction`` first."""
    first = 1 if direction >= 0 else -1
    for distance in range(4):
        for candidate in (degree + first * distance, degree - first * distance):
            if is_chord_tone(candidate, chord):
                return candidate
    raise AssertionError("unreachable: every degree is within one step of a triad tone")


def fold(chord: int) -> int:
    """Chord root moved into -3..3 so voicings stay close to the tonic."""
    return chord if chord <= 3 else chord - 7


def _next_chord_weights(scale: Scale, current: int) -> dict[int, float]:
    row = dict(_TRANSITIONS[current])
    for (src, dst), boost in _MODE_BOOST.get(scale.mode, {}).items():
        if src == current:
            row[dst] = row.get(dst, 0.0) + boost
    weights = {}
    for chord, weight in row.items():
        quality = scale.triad_quality(chord)
        if quality != "dim":
            weights[chord] = weight * (0.3 if quality == "aug" else 1.0)
    return weights


def sample_progression(
    rng: random.Random,
    scale: Scale,
    start: int,
    ends: tuple[int, ...] | None = None,
    length: int = 4,
    tries: int = 500,
) -> tuple[int, ...]:
    """Random walk over the chord graph from ``start``.

    With ``ends`` given, the last chord must be one of them. Walks that use
    fewer than three distinct chords are rejected as too static.
    """
    for _ in range(tries):
        prog = [start]
        while len(prog) < length:
            weights = _next_chord_weights(scale, prog[-1])
            prog.append(rng.choices(list(weights), weights=list(weights.values()))[0])
        if (ends is None or prog[-1] in ends) and len(set(prog)) >= min(3, length):
            return tuple(prog)
    raise ValueError(f"no {length}-chord progression from {start} to {ends} in {scale.name}")


def usable(scale: Scale, chords: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(c for c in chords if scale.triad_quality(c) != "dim")


def theme_progression(rng: random.Random, scale: Scale) -> tuple[int, ...]:
    """Four bars from i that pause on a half cadence, so the phrase can repeat."""
    return sample_progression(rng, scale, 0, usable(scale, _HALF_CADENCE_CHORDS))


def contrast_progression(rng: random.Random, scale: Scale) -> tuple[int, ...]:
    """Four bars that open away from i and end on a chord leading back to it."""
    start = rng.choice(usable(scale, _CONTRAST_OPENINGS))
    return sample_progression(rng, scale, start, CADENCE_CHORDS[scale.mode])


# Key moves in semitones, weighted. Up a fourth or fifth always keeps the old
# tonic in the new scale, whatever the mode; the others only in some modes.
_MODULATIONS = {5: 3.0, 7: 3.0, -3: 2.0, -2: 1.0, 2: 1.0}


def modulate(rng: random.Random, scale: Scale, mode: str) -> Scale:
    """A new key in ``mode`` that still contains the old tonic, for a smooth move."""
    options = {}
    for shift, weight in _MODULATIONS.items():
        candidate = Scale((scale.root + shift) % 12, mode)
        if candidate.contains(scale.root):
            options[candidate] = weight
    return rng.choices(list(options), weights=list(options.values()))[0]
