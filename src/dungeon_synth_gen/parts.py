"""Accompaniment generators: drone, bassline, arpeggio, and percussion.

Each generator writes one phrase, with times in beats from the phrase start,
so phrases can be laid end to end for a fixed piece or chained forever.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

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
# "approach" is a scale step next to the following bar's root; "tonic" is the key's tonic, whatever the chord.
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
    "riff": tuple((i / 4, 0.25, d) for i, d in enumerate((0, 0, 7, 0, 0, 0, 1, 0, 0, 0, 7, 0, 6, 4, 3, 1))),
    # The low tonic hammered in sixteenths, the chord root only on the beat: the harmony moves, the floor never does.
    "pedal": tuple((i / 4, 0.25, 0 if i % 4 == 0 else "tonic") for i in range(16)),
    "pedalgallop": tuple(
        (beat + t, d, 0 if t == 0 else "tonic") for beat in range(4) for t, d in ((0, 0.5), (0.5, 0.25), (0.75, 0.25))
    ),
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
# Combat arpeggios are power chords over (root, fifth, octave, third): the third,
# which is what makes a chord sound bright or sad, only flickers past.
GRIM_ARP_PATTERNS: tuple[tuple[int, ...], ...] = (
    (0, 0, 2, 0, 1, 0, 2, 1),
    (0, 2, 1, 2, 0, 2, 1, 0),
    (0, 1, 2, 1, 0, 1, 3, 1),
    (0, 0, 1, 0, 0, 0, 2, 1),
    (2, 1, 0, 1, 2, 1, 0, 1),
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
        {"intro": ("pedal", "sixteenths"), "theme": ("pedal", "sixteenths", "bounce", "riff"),
         "contrast": ("riff", "stomp", "pedalgallop"), "return": ("pedal", "bounce", "riff")},
        {"intro": ("four", "pound"), "theme": ("four", "floor16"), "contrast": ("floor16", "halftime", "four"),
         "return": ("four", "floor16")},
        (0.25, 0.22),
    ),
    "darksynth": Flavour(
        2.0,
        {"intro": ("drive",), "theme": ("drive", "octaves", "pedal"), "contrast": ("gallop", "pedalgallop", "stomp"),
         "return": ("drive", "octaves")},
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
    intensity: float = 0.5  # 0-1, set per phrase by ``intensify``; 0.5 plays the groove as drawn


def level(intensity: float) -> float:
    """Intensity snapped to quarters, so a fight's music moves in audible steps (and synth caches stay small)."""
    return round(min(1.0, max(0.0, intensity)) * 4) / 4


# How each bass pattern thickens in a harder fight, and thins in an easier one.
_DENSER = {
    "drive": "sixteenths", "octaves": "bounce", "gallop": "pedalgallop", "offbeat": "offsixteenths",
    "sludge": "stomp", "halves": "drive", "stomp": "riff",
}
_SPARSER = {
    "sixteenths": "drive", "bounce": "octaves", "pedal": "drive", "riff": "octaves", "pedalgallop": "gallop",
    "offsixteenths": "offbeat", "stomp": "sludge", "gallop": "drive",
}


def intensify(groove: Groove, intensity: float) -> Groove:
    """The groove for one phrase of a fight this hard: denser bass and faster arps from 0.75, sparser at 0.25 and below."""
    x = level(intensity)
    bass, (step, ring) = groove.bass, groove.arp
    if x >= 0.75:
        bass = {role: _DENSER.get(p, p) for role, p in bass.items()}
        step, ring = min(step, 0.25), min(ring, 0.22)
    elif x <= 0.25:
        bass = {role: _SPARSER.get(p, p) for role, p in bass.items()}
        step, ring = max(step, 0.5), max(ring, 0.45)
    return replace(groove, bass=bass, arp=(step, ring), intensity=x)


def draw_groove(rng: random.Random, style: str) -> Groove:
    arps = tuple(rng.sample(GRIM_ARP_PATTERNS if style == "combat" else ARP_PATTERNS, 2))
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
            if offset == "approach":
                degree = next_root + rng.choice((1, -1))
            else:
                degree = 0 if offset == "tonic" else root + offset
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
        tones = (root, root + 4, root + 7, root + 2) if groove.style == "combat" else (root, root + 2, root + 4, root + 7)
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


def _drums_at(pattern: tuple[tuple[int, int, int], ...], intensity: float) -> tuple[tuple[int, int, int], ...]:
    """A bar pattern for a fight this hard: only the downbeats and snares when easy; sixteenth hats and double kicks when hard."""
    if intensity <= 0.25:
        return tuple(p for p in pattern if p[0] % 4 == 0 or p[1] == SNARE)
    if intensity < 0.75:
        return pattern
    taken = {(step, drum) for step, drum, _ in pattern}
    kicks = (2, 10) if intensity < 1 else (2, 6, 10, 14)
    extra = [(s, KICK, 82) for s in kicks if (s, KICK) not in taken]
    extra += [(s, HAT, 62 if s % 2 else 40) for s in range(16) if (s, HAT) not in taken]
    return pattern + tuple(extra)


def percussion(groove: Groove, role: str, bars: int, ending: bool = False) -> list[NoteEvent]:
    if role not in groove.drums:
        return []
    _, filled, crashed = _DRUM_PLANS[groove.style]
    base = _drums_at(DRUM_PATTERNS[groove.drums[role]], groove.intensity)
    flat_out = groove.intensity >= 0.875  # a crash on every bar, a fill every other bar
    events = []
    for bar in range(bars):
        pattern = base
        if ending and bar == bars - 1:
            pattern = _ENDING_HIT
        elif (bar == 0 and role in crashed) or (flat_out and bar % 2 == 0):
            pattern = pattern + ((0, CRASH, 75 + 25 * flat_out),)
        elif role in filled and (bar == bars - 1 or (flat_out and bar % 2 == 1)):
            pattern = tuple(p for p in pattern if p[0] < 8) + _FILLS[groove.style]
        for step, drum, velocity in pattern:
            events.append(NoteEvent("perc", bar * BEATS_PER_BAR + step / 4, 0.25, drum, velocity))
    return events
