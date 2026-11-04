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
    "interlude": "intro",  # a breather before the return: the lead rests
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

REST = -1  # in an arpeggio pattern: a gap
# Eighth-note arpeggio patterns over (root, third, fifth, octave).
ARP_PATTERNS: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 1, 0, 1, 2, 1),
    (0, 2, 1, 2, 0, 2, 1, 2),
    (0, 1, 2, 3, 0, 1, 2, 3),
    (0, 2, 3, 2, 1, 2, 3, 2),
    (0, 1, 2, 3, 2, 1, 0, 1),
    (3, 2, 1, 0, 1, 2, 3, 2),
    (0, 2, REST, 2, 1, 2, REST, 3),
    (0, REST, 1, 2, REST, 2, 3, 2),
    (0, 1, 2, 1, 3, 2, 1, 2),
)
# Combat arpeggios are power chords over (root, fifth, octave, third): the third,
# which is what makes a chord sound bright or sad, only flickers past.
GRIM_ARP_PATTERNS: tuple[tuple[int, ...], ...] = (
    (0, 0, 2, 0, 1, 0, 2, 1),
    (0, 2, 1, 2, 0, 2, 1, 0),
    (0, 1, 2, 1, 0, 1, 3, 1),
    (0, 0, 1, 0, 0, 0, 2, 1),
    (2, 1, 0, 1, 2, 1, 0, 1),
    (0, 0, REST, 0, 2, 0, REST, 1),
    (0, 2, 0, 1, 0, 2, 0, 3),
)
# Which of a phrase's two arpeggio patterns each bar plays: A its own, B the other one.
_ARP_SHAPES = ("AAAA", "AABA", "ABAB", "AAAB", "ABAA")
_ARP_VELOCITY = {"intro": 54, "theme": 60, "contrast": 68, "return": 62}

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
    # The gravest moment of a fight: half time, every hit given room to land.
    "anthem": ((0, KICK, 124), (6, KICK, 88), (8, SNARE, 126), (10, KICK, 92), (14, LOW_TOM, 96)),
}
# Replace the second half of a section's last bar. Combat draws one per phrase, and None
# (no fill: the groove plays straight through) is one of the draws.
_FILLS: dict[str, tuple[tuple[tuple[int, int, int], ...] | None, ...]] = {
    "normal": (
        ((8, MID_TOM, 80), (10, MID_TOM, 85), (12, LOW_TOM, 95), (13, LOW_TOM, 100), (14, LOW_TOM, 105), (15, LOW_TOM, 110)),
        # A snare pickup.
        ((8, KICK, 90), (12, SNARE, 70), (14, SNARE, 80), (15, SNARE, 90)),
        # Toms answering the kick.
        ((8, LOW_TOM, 90), (11, LOW_TOM, 75), (12, KICK, 95), (14, MID_TOM, 85), (15, LOW_TOM, 95)),
        None,
    ),
    "combat": (
        # Snare roll into the toms.
        ((8, SNARE, 80), (9, SNARE, 70), (10, SNARE, 90), (11, SNARE, 80),
         (12, MID_TOM, 100), (13, MID_TOM, 100), (14, LOW_TOM, 110), (15, LOW_TOM, 115)),
        # Down the toms.
        ((8, MID_TOM, 105), (9, MID_TOM, 90), (10, MID_TOM, 100), (12, LOW_TOM, 110), (13, LOW_TOM, 95),
         (14, LOW_TOM, 115), (15, KICK, 100)),
        # Unison stabs.
        ((8, KICK, 122), (8, SNARE, 118), (11, KICK, 110), (12, KICK, 122), (12, SNARE, 120), (14, SNARE, 100),
         (15, SNARE, 110)),
        # A snare build in sixteenths.
        tuple((i, SNARE, 70 + 7 * (i - 8)) for i in range(8, 16)),
        # One hit, then the floor drops out.
        ((8, KICK, 124), (8, CRASH, 100)),
        # Kicks answering the snare.
        ((8, KICK, 115), (10, KICK, 110), (11, KICK, 100), (12, SNARE, 120), (13, KICK, 100), (14, SNARE, 110),
         (15, SNARE, 120)),
        None,
    ),
}
# Per style: roles that end on a fill, and roles that open on a crash. The patterns come from the flavour.
_DRUM_ACCENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "normal": (("contrast",), ("return",)),
    "combat": (("intro", "theme", "contrast"), ("theme", "contrast", "return")),
    "ambient": ((), ()),
}
_ENDING_HIT = ((0, KICK, 110), (0, CRASH, 90))
# Hits added to every second bar, drawn per phrase, so a groove answers itself instead of looping one bar.
_VARIATIONS: dict[str, tuple[tuple[tuple[int, int, int], ...] | None, ...]] = {
    "normal": (((15, LOW_TOM, 55),), ((10, KICK, 65),), ((6, LOW_TOM, 50), (7, LOW_TOM, 45)), None),
    "combat": (
        ((14, KICK, 95),), ((7, SNARE, 45), (15, SNARE, 50)), ((10, KICK, 100), (11, KICK, 85)),
        ((6, KICK, 90), (14, SNARE, 70)), ((3, KICK, 85),), None,
    ),
    "ambient": (None,),
}


@dataclass(frozen=True)
class Flavour:
    """One kind of music within a style: its synths (``synth.SOUNDS``), tempo, and the patterns each role draws from."""

    tempo: float  # times the region's base tempo
    bass: dict[str, tuple[str, ...]]  # role -> bass patterns to draw from
    drums: dict[str, tuple[str, ...]]  # role -> drum patterns to draw from; a missing role is silent
    arp: tuple[float, float]  # (beats per arpeggio note, beats each note rings)
    pad: tuple[int, ...] = ()  # chord degrees held under every bar, on the drone's voice; empty for none
    lead_octave: int = 0  # octaves the lead sits above its usual register


# Every region draws one, so the music keeps changing colour, and a long fight keeps changing gear.
FLAVOURS: dict[str, Flavour] = {
    # Calm. The original: NES square lead, triangle bass, a heartbeat kick.
    "crypt": Flavour(
        0.85,
        {"intro": ("whole",), "theme": ("halves", "pulse"), "contrast": ("walk",), "return": ("halves", "pulse")},
        {"theme": ("heartbeat",), "contrast": ("march", "war"), "return": ("heartbeat",)},
        (0.5, 0.45),
    ),
    # A slow chapel organ: held chords, a pedal bass, no drums.
    "organ": Flavour(
        0.75,
        {"intro": ("whole",), "theme": ("whole", "halves"), "contrast": ("halves",), "return": ("whole", "halves")},
        {},
        (1.0, 0.95),
        pad=(0, 2, 4),
    ),
    # Plucked and quick: sixteenth-note harpsichord arps over a walking bass.
    "harpsichord": Flavour(
        1.0,
        {"intro": ("halves",), "theme": ("walk", "pulse"), "contrast": ("walk",), "return": ("walk", "halves")},
        {},
        (0.25, 0.2),
    ),
    # Toms and a snare: the dungeon on the move.
    "march": Flavour(
        0.95,
        {"intro": ("pulse",), "theme": ("pulse", "walk"), "contrast": ("walk",), "return": ("pulse",)},
        {"intro": ("heartbeat",), "theme": ("march", "war"), "contrast": ("war",), "return": ("march",)},
        (0.5, 0.3),
    ),
    # Ambient. Soft plucks ringing over one another.
    "harp": Flavour(
        0.85,
        {"intro": ("whole",), "theme": ("whole",), "contrast": ("halves",), "return": ("whole",)},
        {},
        (1.0, 2.0),
    ),
    # A slow-swelling choir of saws over a held chord.
    "mist": Flavour(
        0.7,
        {"intro": ("whole",), "theme": ("whole",), "contrast": ("whole",), "return": ("whole",)},
        {},
        (2.0, 3.0),
        pad=(0, 4, 9),
    ),
    # A music box an octave up, chiming over eighths.
    "musicbox": Flavour(
        0.85,
        {"intro": ("whole",), "theme": ("whole",), "contrast": ("halves",), "return": ("whole",)},
        {},
        (1.0, 2.0),
        lead_octave=1,
    ),
    # Combat.
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
# Which flavours each style draws, weighted.
STYLE_FLAVOURS: dict[str, dict[str, float]] = {
    "normal": {"crypt": 2, "organ": 1.5, "harpsichord": 1.5, "march": 1},
    "ambient": {"harp": 1.5, "mist": 1.5, "musicbox": 1},
    "combat": {"brut": 4, "darksynth": 2, "industrial": 2, "doom": 1.5, "chiptrash": 1},
}
FLAVOUR_WEIGHTS = STYLE_FLAVOURS["combat"]
FLAVOUR_STYLE = {name: style for style, weights in STYLE_FLAVOURS.items() for name in weights}  # the style each flavour belongs to


@dataclass(frozen=True)
class Groove:
    """Accompaniment choices shared by every phrase in a key region."""

    style: str  # "normal" | "combat" | "ambient"
    bass: dict[str, str]  # bass pattern per role
    drums: dict[str, str]  # drum pattern per role
    arps: tuple[tuple[int, ...], tuple[int, ...]]  # (main, contrast) arpeggio patterns
    sound: str  # the synth set (synth.SOUNDS): the flavour's name
    tempo: float  # times the region's base tempo
    arp: tuple[float, float]  # (beats per arpeggio note, beats each note rings)
    intensity: float = 0.5  # 0-1, set per phrase by ``intensify``; 0.5 plays the groove as drawn
    pad: tuple[int, ...] = ()  # chord degrees held under every bar
    lead_octave: int = 0


def level(intensity: float) -> float:
    """Intensity snapped to quarters, so a fight's music moves in audible steps (and synth caches stay small)."""
    return round(min(1.0, max(0.0, intensity)) * 4) / 4


# When it matters the bass follows the chord roots: a tonic pedal grinding under
# the flat second is tension, and a grave moment wants weight.
_FOLLOW = {"pedal": "sixteenths", "pedalgallop": "gallop"}
# How each bass pattern thins in an easier fight.
_SPARSER = {
    "sixteenths": "drive", "bounce": "octaves", "pedal": "drive", "riff": "octaves", "pedalgallop": "gallop",
    "offsixteenths": "offbeat", "stomp": "sludge", "gallop": "drive",
}


def intensify(groove: Groove, intensity: float) -> Groove:
    """The groove for one phrase of a fight this hard.

    Harder means heavier, not busier: from 0.75 the arps slow to eighths and
    ring on, and the drums and pads (``percussion``, ``pad``) take the weight.
    At 0.25 and below the bass and arps thin out.
    """
    x = level(intensity)
    bass, (step, ring) = groove.bass, groove.arp
    if x >= 0.75:
        bass = {role: _FOLLOW.get(p, p) for role, p in bass.items()}
        step, ring = max(step, 0.5), max(ring, 0.9)
    elif x <= 0.25:
        bass = {role: _SPARSER.get(p, p) for role, p in bass.items()}
        step, ring = max(step, 0.5), max(ring, 0.45)
    return replace(groove, bass=bass, arp=(step, ring), intensity=x)


def draw_groove(rng: random.Random, style: str, keep: str | None = None) -> Groove:
    """A groove in ``style``: a fresh flavour, or the ``keep`` flavour (patterns redrawn) to stay in the same sound."""
    arps = tuple(rng.sample(GRIM_ARP_PATTERNS if style == "combat" else ARP_PATTERNS, 2))
    weights = STYLE_FLAVOURS[style]
    name = keep or rng.choices(list(weights), weights=list(weights.values()))[0]
    flavour = FLAVOURS[name]
    bass = {role: rng.choice(p) for role, p in flavour.bass.items()}
    drums = {role: rng.choice(p) for role, p in flavour.drums.items()}
    return Groove(style, bass, drums, arps, name, flavour.tempo, flavour.arp, pad=flavour.pad, lead_octave=flavour.lead_octave)


def pad(
    scale: Scale, tonic: int, chords: tuple[int, ...], degrees: tuple[int, ...] = (0, 4, 7), velocity: int = 64
) -> list[NoteEvent]:
    """Chords (power chords unless ``degrees`` says otherwise) held under each run of the same chord, on the drone's voice."""
    events = []
    bar = 0
    while bar < len(chords):
        run = 1
        while bar + run < len(chords) and chords[bar + run] == chords[bar]:
            run += 1
        root = fold(chords[bar])
        for degree in degrees:
            events.append(NoteEvent("drone", bar * BEATS_PER_BAR, run * BEATS_PER_BAR, scale.pitch(root + degree, tonic), velocity))
        bar += run
    return events


def drone(scale: Scale, tonic: int, beats: float) -> list[NoteEvent]:
    """Tonic and fifth held for ``beats``."""
    return [NoteEvent("drone", 0.0, beats, scale.pitch(d, tonic), 72) for d in (0, 4)]


def bassline(
    rng: random.Random, scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str,
    next_chord: int, ending: bool = False,
) -> list[NoteEvent]:
    """One phrase of bass; ``next_chord`` opens the following phrase, for approach notes.

    Some phrases turn around in their last beat: a step (or, in combat, a sixteenth-note
    run) leading into the next phrase's root, instead of the pattern going round again.
    """
    pattern = groove.bass[role]
    turn = not ending and rng.random() < _TURNAROUND
    events = []
    for bar, chord in enumerate(chords):
        is_last = bar == len(chords) - 1
        root = fold(chord)
        next_root = fold(next_chord if is_last else chords[bar + 1])
        notes = BASS_STYLES["whole" if ending and is_last else pattern]
        if turn and is_last:
            notes = _turnaround(rng, notes, next_root, groove.style == "combat")
        for start, length, offset in notes:
            if offset == "approach":
                degree = next_root + rng.choice((1, -1))
            elif isinstance(offset, tuple):  # ("to", steps): that many scale steps from the next root
                degree = next_root + offset[1]
            else:
                degree = 0 if offset == "tonic" else root + offset
            velocity = 96 if start == 0 else 84 if start % 0.5 == 0 else 72  # sixteenths between the eighths sit back
            events.append(NoteEvent("bass", bar * BEATS_PER_BAR + start, length * 0.95, scale.pitch(degree, tonic), velocity))
    return events


_TURNAROUND = 0.4  # chance a phrase's bass leads into the next one


def _turnaround(rng: random.Random, notes, next_root: int, combat: bool):
    """The bar's notes up to beat four, then a way into ``next_root``: one step, or a three-note run."""
    kept = [(s, min(length, 3 - s), offset) for s, length, offset in notes if s < 3]
    side = rng.choice((1, -1))
    if abs(next_root + 3 * side) > 6:
        side = -side
    if combat:
        return (*kept, (3, 0.25, ("to", 3 * side)), (3.25, 0.25, ("to", 2 * side)), (3.5, 0.5, ("to", side)))
    return (*kept, (3, 1, ("to", side)))


def arpeggio(
    scale: Scale, tonic: int, chords: tuple[int, ...], groove: Groove, role: str, ending: bool = False,
    rng: random.Random | None = None,
) -> list[NoteEvent]:
    """One phrase of arpeggio. With ``rng``, bars trade between the phrase's two patterns (``_ARP_SHAPES``)."""
    main, contrast = groove.arps
    own, other = (contrast, main) if role == "contrast" else (main, contrast)
    shape = rng.choice(_ARP_SHAPES) if rng is not None else "AAAA"
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
        pattern = own if shape[bar % len(shape)] == "A" else other
        for step in range(int(BEATS_PER_BAR / step_beats)):
            tone = pattern[step % len(pattern)]
            if tone == REST:
                continue
            accent = 12 if step == 0 else 6 if step * step_beats == 2 else 0
            pitch = scale.pitch(tones[tone], tonic)
            length = min(ring, (len(chords) - bar) * BEATS_PER_BAR - step * step_beats)  # stay inside the phrase
            events.append(NoteEvent("arp", start + step * step_beats, length, pitch, velocity + accent))
    return events


def _fill(style: str, rng: random.Random | None) -> tuple[tuple[int, int, int], ...] | None:
    fills = _FILLS[style]
    return fills[0] if rng is None or len(fills) == 1 else rng.choice(fills)


def _drums_at(pattern: tuple[tuple[int, int, int], ...], intensity: float) -> tuple[tuple[int, int, int], ...]:
    """A bar pattern for a fight this hard: only the downbeats and snares when easy, the half-time anthem at the top."""
    if intensity <= 0.25:
        return tuple(p for p in pattern if p[0] % 4 == 0 or p[1] == SNARE)
    if intensity >= 1:
        return DRUM_PATTERNS["anthem"]
    return pattern


def percussion(
    groove: Groove, role: str, bars: int, ending: bool = False, rng: random.Random | None = None
) -> list[NoteEvent]:
    """Drums for one phrase; ``rng`` draws which fill closes it, where the style has more than one."""
    if role not in groove.drums:
        return []
    filled, crashed = _DRUM_ACCENTS[groove.style]
    base = _drums_at(DRUM_PATTERNS[groove.drums[role]], groove.intensity)
    grave = groove.intensity >= 0.75  # every bar opens on a crash
    extra = rng.choice(_VARIATIONS[groove.style]) if rng is not None else None
    if extra is None or not 0.25 < groove.intensity < 1:
        extra = ()
    answer = base + tuple(hit for hit in extra if hit[:2] not in {p[:2] for p in base})
    events = []
    for bar in range(bars):
        pattern = answer if bar % 2 else base
        if ending and bar == bars - 1:
            pattern = _ENDING_HIT
        elif role in filled and bar == bars - 1 and (fill := _fill(groove.style, rng)) is not None:
            pattern = tuple(p for p in pattern if p[0] < 8) + fill
            if grave:
                pattern += ((0, CRASH, 95),)
        elif (bar == 0 and role in crashed) or grave:
            pattern = pattern + ((0, CRASH, 95 if grave else 75),)
        for step, drum, velocity in pattern:
            events.append(NoteEvent("perc", bar * BEATS_PER_BAR + step / 4, 0.25, drum, velocity))
    return events
