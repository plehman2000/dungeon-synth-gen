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
    # Darksynth: relentless sixteenths, an octave bounce, and riffs that climb into the next bar.
    "sixteenths": tuple((i / 4, 0.25, 0) for i in range(16)),
    "bounce": tuple((i / 4, 0.25, 7 if i % 4 == 2 else 0) for i in range(16)),
    "riff": tuple((i / 4, 0.25, d) for i, d in enumerate((0, 0, 7, 0, 0, 0, 4, 0, 0, 0, 7, 0, 5, 4, 2, 1))),
    "stomp": ((0, 0.75, 0), (0.75, 0.25, 0), (1, 0.5, 0), (1.5, 0.5, 7), (2, 0.75, 0), (2.75, 0.25, 0), (3, 0.5, 4), (3.5, 0.5, 6)),
    # Industrial: the bass answers the kick on the off-beats.
    "offbeat": tuple((i + 0.5, 0.45, 7 * (i == 3)) for i in range(4)),
    "offsixteenths": tuple((i / 4, 0.22, 0) for i in range(16) if i % 4),
    # Doom: slow and heavy, dropping to the seventh below.
    "sludge": ((0, 2.5, 0), (2.5, 0.5, 0), (3, 1, -1)),
}
# Calm styles: grooves the theme and return pick from, and fixed patterns for the other roles.
# Combat draws everything from its flavour (FLAVOURS).
_BASS_PLANS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "normal": (("halves", "pulse"), {"intro": "whole", "contrast": "walk"}),
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
_ARP_TIMING = {"normal": (0.5, 0.45), "ambient": (1.0, 2.0)}  # combat's come from its flavour

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
    # Four on the floor under a backbeat, off-beat hats: the darksynth workhorse.
    "four": (
        (0, KICK, 118), (2, HAT, 60), (4, KICK, 110), (4, SNARE, 112), (6, HAT, 60),
        (8, KICK, 115), (10, HAT, 60), (12, KICK, 110), (12, SNARE, 115), (14, HAT, 65),
    ),
    "floor16": (
        (0, KICK, 120), (4, KICK, 110), (4, SNARE, 110), (8, KICK, 116), (11, KICK, 85), (12, KICK, 110), (12, SNARE, 118),
        *((i, HAT, 70 if i % 2 else 40) for i in range(16)),
    ),
    # Half time: the snare only on beat three, so the riff feels twice as heavy.
    "halftime": ((0, KICK, 118), (3, KICK, 80), (6, KICK, 95), (8, SNARE, 120), (10, KICK, 90), (14, LOW_TOM, 85)),
    "dbeat": (
        (0, KICK, 115), (3, KICK, 95), (4, SNARE, 108), (8, KICK, 112), (10, KICK, 95), (12, SNARE, 112),
        *((i, HAT, 55) for i in range(0, 16, 2)),
    ),
    "blast": tuple(hit for i in range(0, 16, 2) for hit in ((i, KICK, 110), (i + 1, SNARE, 90 + 6 * (i % 4 == 2)))),
    "ebm": (
        (0, KICK, 118), (4, KICK, 112), (4, SNARE, 105), (8, KICK, 116), (12, KICK, 112), (12, SNARE, 108),
        *((i, HAT, 75 if i % 4 == 2 else 35) for i in range(16)),
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
    "combat": ({}, ("intro", "theme", "contrast"), ("theme", "contrast", "return")),  # its patterns are in FLAVOURS
    "ambient": ({}, (), ()),  # no drums at all
}
_ENDING_HIT = ((0, KICK, 110), (0, CRASH, 90))


@dataclass(frozen=True)
class Flavour:
    """One kind of combat music: its synths (``synth.SOUNDS``), tempo, and the patterns each role draws from."""

    tempo: float  # times the calm tempo
    bass: dict[str, tuple[str, ...]]  # role -> bass patterns to draw from
    drums: dict[str, tuple[str, ...]]  # role -> drum patterns to draw from
    arp: tuple[float, float]  # (beats per arpeggio note, beats each note rings)


# Every combat region draws one, so a long fight keeps changing gear.
FLAVOURS: dict[str, Flavour] = {
    # Carpenter Brut territory: sixteenth-note distorted saw bass, four on the floor, gated snare, hard pump.
    "brut": Flavour(
        2.0,
        {"intro": ("sixteenths", "bounce"), "theme": ("sixteenths", "bounce", "riff"),
         "contrast": ("riff", "stomp", "gallop"), "return": ("bounce", "riff", "sixteenths")},
        {"intro": ("four", "pound"), "theme": ("four", "floor16"), "contrast": ("floor16", "halftime", "four"),
         "return": ("four", "floor16")},
        (0.25, 0.22),
    ),
    "darksynth": Flavour(
        2.0,
        {"intro": ("drive",), "theme": ("drive", "octaves"), "contrast": ("gallop", "stomp"), "return": ("drive", "octaves")},
        {"intro": ("pound",), "theme": ("drive", "four"), "contrast": ("thrash", "war"), "return": ("drive",)},
        (0.5, 0.45),
    ),
    "industrial": Flavour(
        1.75,
        {"intro": ("offbeat",), "theme": ("offbeat", "offsixteenths"), "contrast": ("offsixteenths", "sixteenths"),
         "return": ("offbeat", "offsixteenths")},
        {"intro": ("ebm",), "theme": ("ebm", "four"), "contrast": ("ebm", "dbeat"), "return": ("ebm",)},
        (0.25, 0.15),
    ),
    "doom": Flavour(
        1.5,
        {"intro": ("sludge",), "theme": ("sludge", "stomp"), "contrast": ("stomp", "gallop"), "return": ("sludge", "halves")},
        {"intro": ("halftime",), "theme": ("halftime", "pound"), "contrast": ("war", "dbeat"), "return": ("halftime",)},
        (1.0, 0.9),
    ),
    "chiptrash": Flavour(
        2.25,
        {"intro": ("gallop",), "theme": ("gallop", "octaves", "bounce"), "contrast": ("riff", "drive"),
         "return": ("gallop", "octaves")},
        {"intro": ("dbeat",), "theme": ("dbeat", "thrash"), "contrast": ("blast", "thrash"), "return": ("dbeat",)},
        (0.25, 0.2),
    ),
}
FLAVOUR_WEIGHTS = {"brut": 4, "darksynth": 2, "industrial": 2, "doom": 1.5, "chiptrash": 1}


@dataclass(frozen=True)
class Groove:
    """Accompaniment choices shared by every phrase in a key region."""

    style: str  # "normal" | "combat" | "ambient"
    bass: dict[str, str]  # bass pattern per role
    drums: dict[str, str]  # drum pattern per role
    arps: tuple[tuple[int, ...], tuple[int, ...]]  # (main, contrast) arpeggio patterns
    sound: str  # the synth set (synth.SOUNDS): the style's name, or a combat flavour's
    tempo: float  # times the calm tempo
    arp: tuple[float, float]  # (beats per arpeggio note, beats each note rings)


def draw_groove(rng: random.Random, style: str) -> Groove:
    arps = tuple(rng.sample(ARP_PATTERNS, 2))
    if style == "combat":
        name = rng.choices(list(FLAVOUR_WEIGHTS), weights=list(FLAVOUR_WEIGHTS.values()))[0]
        flavour = FLAVOURS[name]
        bass = {role: rng.choice(p) for role, p in flavour.bass.items()}
        drums = {role: rng.choice(p) for role, p in flavour.drums.items()}
        return Groove(style, bass, drums, arps, name, flavour.tempo, flavour.arp)
    choices, fixed = _BASS_PLANS[style]
    main = rng.choice(choices)
    bass = {role: fixed.get(role, main) for role in ("intro", "theme", "contrast", "return")}
    drums = {role: p if isinstance(p, str) else rng.choice(p) for role, p in _DRUM_PLANS[style][0].items()}
    return Groove(style, bass, drums, arps, style, 1.0, _ARP_TIMING[style])


def drone(scale: Scale, tonic: int, beats: float) -> list[NoteEvent]:
    """Tonic and fifth held for ``beats``."""
    return [NoteEvent("drone", 0.0, beats, scale.pitch(d, tonic), 72) for d in (0, 4)]


def bassline(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str,
    next_chord: int, ending: bool = False,
) -> list[NoteEvent]:
    """One phrase of bass; ``next_chord`` opens the following phrase, for approach notes."""
    pattern = groove.bass[role]
    events = []
    for bar, chord in enumerate(chords):
        is_last = bar == len(chords) - 1
        root = fold(chord)
        next_root = fold(next_chord if is_last else chords[bar + 1])
        for start, length, offset in BASS_STYLES["whole" if ending and is_last else pattern]:
            degree = next_root + rng.choice((1, -1)) if offset == "approach" else root + offset
            velocity = 96 if start == 0 else 84 if start % 0.5 == 0 else 72  # sixteenths between the eighths sit back
            events.append(NoteEvent("bass", bar * BEATS_PER_BAR + start, length * 0.95, scale.pitch(degree, tonic), velocity))
    return events


def arpeggio(scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str, ending: bool = False) -> list[NoteEvent]:
    main, contrast = groove.arps
    pattern = contrast if role == "contrast" else main
    velocity = _ARP_VELOCITY[role]
    step_beats, ring = groove.arp
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
