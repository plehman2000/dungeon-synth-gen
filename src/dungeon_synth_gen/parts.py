"""Accompaniment generators: drone, bassline, arpeggio, and percussion.

Each generator writes one phrase, with times in beats from the phrase start,
so phrases can be laid end to end for a fixed piece or chained forever.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from dungeon_synth_gen.events import BEATS_PER_BAR, NoteEvent
from dungeon_synth_gen.theory import Scale, fold

# General MIDI drum notes, so exported .mid files play on any GM drum kit.
KICK, SNARE, HAT, LOW_TOM, MID_TOM, CRASH = 36, 38, 42, 41, 45, 49

# Every section plays one of four roles; sections that only appear in the
# endless stream borrow the role of the section they develop.
ROLES = {
    "intro": "intro",
    "theme": "theme",
    "variation": "theme",
    "contrast": "contrast",
    "development": "contrast",
    "return": "return",
}

# Bass bar patterns: (start beat, length, degree above the chord root).
# "approach" is a scale step next to the following bar's root.
BASS_STYLES: dict[str, tuple[tuple[float, float, int | str], ...]] = {
    "whole": ((0, 4, 0),),
    "halves": ((0, 2, 0), (2, 2, 4)),
    "pulse": ((0, 1.5, 0), (1.5, 0.5, 0), (2, 2, 0)),
    "walk": ((0, 1.5, 0), (1.5, 0.5, 0), (2, 1, 4), (3, 1, "approach")),
    # Combat grooves.
    "drive": tuple((i / 2, 0.5, 0) for i in range(8)),
    "octaves": tuple((i / 2, 0.5, 7 * (i % 2)) for i in range(8)),
    "gallop": tuple((beat + t, d, 0) for beat in range(4) for t, d in ((0, 0.5), (0.5, 0.25), (0.75, 0.25))),
}
# Per style: grooves the theme and return pick from, and fixed patterns for the other roles.
_BASS_PLANS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "normal": (("halves", "pulse"), {"intro": "whole", "contrast": "walk"}),
    "combat": (("drive", "octaves"), {"intro": "drive", "contrast": "gallop"}),
    "ambient": (("whole", "halves"), {"intro": "whole", "contrast": "halves"}),
}

# Eighth-note arpeggio patterns over (root, third, fifth, octave).
ARP_PATTERNS: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 1, 0, 1, 2, 1),
    (0, 2, 1, 2, 0, 2, 1, 2),
    (0, 1, 2, 3, 0, 1, 2, 3),
    (0, 2, 3, 2, 1, 2, 3, 2),
    (0, 1, 2, 3, 2, 1, 0, 1),
)
_ARP_VELOCITY = {"intro": 54, "theme": 60, "contrast": 68, "return": 62}
# Per style: (beats per arpeggio note, beats each note rings). Ambient lets slow notes overlap like a sustain pedal.
_ARP_TIMING = {"normal": (0.5, 0.45), "combat": (0.5, 0.45), "ambient": (1.0, 2.0)}

# Drum bar patterns on a sixteenth-note grid: (step, drum, velocity).
DRUM_PATTERNS: dict[str, tuple[tuple[int, int, int], ...]] = {
    "heartbeat": ((0, KICK, 100), (2, KICK, 70), (8, LOW_TOM, 60)),
    "march": (
        (0, KICK, 105), (4, HAT, 35), (6, LOW_TOM, 70),
        (8, KICK, 95), (12, SNARE, 80), (14, LOW_TOM, 55),
    ),
    "war": (
        (0, LOW_TOM, 110), (3, LOW_TOM, 70), (6, LOW_TOM, 80),
        (8, KICK, 100), (12, SNARE, 85), (13, MID_TOM, 60), (15, MID_TOM, 70),
    ),
    # Combat patterns: a backbeat snare and far more kicks.
    "pound": ((0, KICK, 110), (4, KICK, 90), (6, LOW_TOM, 70), (8, KICK, 105), (12, KICK, 90), (14, LOW_TOM, 80)),
    "drive": (
        (0, KICK, 112), (2, HAT, 45), (4, SNARE, 100), (6, HAT, 45), (7, KICK, 80),
        (8, KICK, 108), (10, KICK, 85), (10, HAT, 45), (12, SNARE, 105), (14, HAT, 50),
    ),
    "thrash": (
        (0, KICK, 115), (2, KICK, 90), (4, KICK, 95), (4, SNARE, 105), (6, KICK, 90),
        (8, KICK, 110), (10, KICK, 90), (12, KICK, 95), (12, SNARE, 110), (14, KICK, 90),
    ),
}
# Replace the second half of a section's last bar.
_FILLS = {
    "normal": ((8, MID_TOM, 80), (10, MID_TOM, 85), (12, LOW_TOM, 95), (13, LOW_TOM, 100), (14, LOW_TOM, 105), (15, LOW_TOM, 110)),
    "combat": (
        (8, SNARE, 80), (9, SNARE, 70), (10, SNARE, 90), (11, SNARE, 80),
        (12, MID_TOM, 100), (13, MID_TOM, 100), (14, LOW_TOM, 110), (15, LOW_TOM, 115),
    ),
}
# Per style: drum pattern per role (a tuple is a seeded choice; missing roles stay silent),
# roles that end on a fill, and roles that open on a crash.
_DRUM_PLANS: dict[str, tuple[dict[str, str | tuple[str, ...]], tuple[str, ...], tuple[str, ...]]] = {
    "normal": ({"theme": "heartbeat", "contrast": ("march", "war"), "return": "heartbeat"}, ("contrast",), ("return",)),
    "combat": (
        {"intro": "pound", "theme": "drive", "contrast": ("thrash", "war"), "return": "drive"},
        ("intro", "theme", "contrast"),
        ("theme", "contrast", "return"),
    ),
    "ambient": ({}, (), ()),  # no drums at all
}
_ENDING_HIT = ((0, KICK, 110), (0, CRASH, 90))


@dataclass(frozen=True)
class Groove:
    """Accompaniment choices shared by every phrase in a key region."""

    style: str  # "normal" | "combat"
    bass: str  # bass pattern for the theme and return
    drums: dict[str, str]  # drum pattern per role
    arps: tuple[tuple[int, ...], tuple[int, ...]]  # (main, contrast) arpeggio patterns


def draw_groove(rng: random.Random, style: str) -> Groove:
    arps = tuple(rng.sample(ARP_PATTERNS, 2))
    bass = rng.choice(_BASS_PLANS[style][0])
    drums = {role: p if isinstance(p, str) else rng.choice(p) for role, p in _DRUM_PLANS[style][0].items()}
    return Groove(style, bass, drums, arps)


def drone(scale: Scale, tonic: int, beats: float) -> list[NoteEvent]:
    """Tonic and fifth held for ``beats``."""
    return [NoteEvent("drone", 0.0, beats, scale.pitch(d, tonic), 72) for d in (0, 4)]


def bassline(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str,
    next_chord: int, ending: bool = False,
) -> list[NoteEvent]:
    """One phrase of bass; ``next_chord`` opens the following phrase, for approach notes."""
    pattern = _BASS_PLANS[groove.style][1].get(role, groove.bass)
    events = []
    for bar, chord in enumerate(chords):
        is_last = bar == len(chords) - 1
        root = fold(chord)
        next_root = fold(next_chord if is_last else chords[bar + 1])
        for start, length, offset in BASS_STYLES["whole" if ending and is_last else pattern]:
            degree = next_root + rng.choice((1, -1)) if offset == "approach" else root + offset
            velocity = 96 if start == 0 else 84
            events.append(NoteEvent("bass", bar * BEATS_PER_BAR + start, length * 0.95, scale.pitch(degree, tonic), velocity))
    return events


def arpeggio(scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str, ending: bool = False) -> list[NoteEvent]:
    main, contrast = groove.arps
    pattern = contrast if role == "contrast" else main
    velocity = _ARP_VELOCITY[role]
    step_beats, ring = _ARP_TIMING[groove.style]
    events = []
    for bar, chord in enumerate(chords):
        root = fold(chord)
        tones = (root, root + 2, root + 4, root + 7)
        start = bar * BEATS_PER_BAR
        if ending and bar == len(chords) - 1:
            # Final bar of a fixed piece: roll the chord upward and let it ring.
            for beat, tone in enumerate(tones):
                events.append(NoteEvent("arp", start + beat, BEATS_PER_BAR - beat, scale.pitch(tone, tonic), velocity))
            continue
        for step in range(int(BEATS_PER_BAR / step_beats)):
            accent = 12 if step == 0 else 6 if step * step_beats == 2 else 0
            pitch = scale.pitch(tones[pattern[step % len(pattern)]], tonic)
            length = min(ring, (len(chords) - bar) * BEATS_PER_BAR - step * step_beats)  # stay inside the phrase
            events.append(NoteEvent("arp", start + step * step_beats, length, pitch, velocity + accent))
    return events


def percussion(groove: Groove, role: str, bars: int, ending: bool = False) -> list[NoteEvent]:
    if role not in groove.drums:
        return []
    _, filled, crashed = _DRUM_PLANS[groove.style]
    base = DRUM_PATTERNS[groove.drums[role]]
    events = []
    for bar in range(bars):
        pattern = base
        if ending and bar == bars - 1:
            pattern = _ENDING_HIT
        elif bar == 0 and role in crashed:
            pattern = pattern + ((0, CRASH, 75),)
        elif role in filled and bar == bars - 1:
            pattern = tuple(p for p in pattern if p[0] < 8) + _FILLS[groove.style]
        for step, drum, velocity in pattern:
            events.append(NoteEvent("perc", bar * BEATS_PER_BAR + step / 4, 0.25, drum, velocity))
    return events
