"""Arrangement engine: palettes of musical material, and phrases built from them.

A palette is everything one key region shares: scale, progressions, motifs,
and accompaniment choices. ``compose`` lays one palette out as a fixed
16-bar piece (intro -> theme -> contrast -> return); ``endless.endless``
keeps drawing palettes and phrases forever.

The theme and return share their opening chords and motif bars, so the
return is recognisable; the return then cadences on i.

Styles: "normal" is the slow dungeon crawl. "combat" plays the same seeded
material (same chords and melody) in double time, over driving bass and
heavy drums in every section. "ambient" is calm and open: major-side modes,
soft plucked voices, slow ringing arpeggios, sparse melodies, no drums.
Unless a style is asked for, it is drawn from ``STYLE_WEIGHTS``; combat is
left out because it belongs to game state, not chance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import NamedTuple

from dungeon_synth_gen import melody, parts, theory
from dungeon_synth_gen.events import BEATS_PER_BAR, Composition, NoteEvent, Section
from dungeon_synth_gen.parts import ROLES, Groove
from dungeon_synth_gen.theory import Scale
from dungeon_synth_gen.variation import Motif, vary

_DUNGEON_MODES = {"aeolian": 3, "phrygian": 3, "dorian": 2, "harmonic": 2}
_MODE_WEIGHTS = {
    "normal": _DUNGEON_MODES,
    "combat": _DUNGEON_MODES,
    "ambient": {"ionian": 3, "lydian": 2, "mixolydian": 2, "dorian": 1},
}
_ROOTS = ("D", "E", "A", "C", "G", "F#", "B")
TEMPO_RANGE = (60, 84)
TEMPO_SCALE = {"normal": 1, "combat": 2, "ambient": 1}  # for the opening draw; each groove then sets its own
STYLES = tuple(TEMPO_SCALE)
STYLE_WEIGHTS = {"normal": 2, "ambient": 1}
_RHYTHMS = {"normal": melody.MOTIF_RHYTHMS, "combat": melody.MOTIF_RHYTHMS, "ambient": melody.AMBIENT_RHYTHMS}
_EVOLVE = 0.25  # chance a region's theme grows out of the last region's instead of starting fresh


def stream(seed: int, name: str) -> random.Random:
    """Independent RNG per concern, so changing one part leaves the others intact."""
    return random.Random(f"{seed}/{name}")


def draw_style(rng: random.Random) -> str:
    return rng.choices(list(STYLE_WEIGHTS), weights=list(STYLE_WEIGHTS.values()))[0]


def seeded_style(seed: int) -> str:
    """The style a seed gets when none is asked for."""
    return draw_style(stream(seed, "style"))


def draw_mode(rng: random.Random, style: str) -> str:
    weights = _MODE_WEIGHTS[style]
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def initial_params(
    seed: int, style: str = "normal", mode: str | None = None, root: int | None = None, tempo: float | None = None
) -> tuple[Scale, float]:
    """The seeded key and tempo. Overrides win, without shifting any other draw."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style!r}")
    rng = stream(seed, "params")
    drawn_mode = draw_mode(rng, style)
    drawn_root = theory.parse_note(rng.choice(_ROOTS))
    drawn_tempo = rng.randint(*TEMPO_RANGE) * TEMPO_SCALE[style]
    return Scale(drawn_root if root is None else root, mode or drawn_mode), tempo or drawn_tempo


@dataclass(frozen=True)
class Palette:
    scale: Scale
    theme: tuple[int, ...]
    contrast: tuple[int, ...]
    cadence: int  # chord before the return's final i
    theme_motif: Motif
    contrast_motif: Motif
    answer_motif: Motif  # the theme's second idea, so a theme phrase need not just restate one bar
    theme_anchor: int  # scale degree the theme starts near
    contrast_anchor: int
    groove: Groove

    @property
    def lead_tonic(self) -> int:
        return theory.place(self.scale.root, 60)

    @property
    def arp_tonic(self) -> int:
        return theory.place(self.scale.root, 48)

    @property
    def bass_tonic(self) -> int:
        return theory.place(self.scale.root, 38)  # D2..C#3

    @property
    def drone_tonic(self) -> int:
        return self.bass_tonic + 12

    def chords(self, name: str, rng: random.Random) -> tuple[int, ...]:
        role = ROLES[name]
        if name == "development":
            return theory.contrast_progression(rng, self.scale)  # fresh harmony for the motif to wander over
        if role == "contrast":
            return self.contrast
        if role == "return":
            return (self.theme[0], self.theme[1], self.cadence, 0)
        return self.theme


def new_palette(rng: random.Random, scale: Scale, style: str, previous: Palette | None = None) -> Palette:
    """Fresh material in ``scale``. Given ``previous``, the theme sometimes evolves from its theme."""
    theme = theory.theme_progression(rng, scale)
    contrast = theory.contrast_progression(rng, scale)
    cadence = rng.choice(theory.CADENCE_CHORDS[scale.mode])
    rhythms = _RHYTHMS[style]
    # The theme sometimes evolves from the last one, unless the style changed under it; mostly a region is new music.
    if previous is not None and previous.groove.style == style and rng.random() < _EVOLVE:
        motif = previous.theme_motif
        for _ in range(2):
            _, motif = vary(motif, rng)
    else:
        motif = melody.random_motif(rng, rhythms=rhythms)
    contrast_motif = melody.random_motif(rng, avoid_rhythm=motif.rhythm, rhythms=rhythms)
    theme_anchor = rng.choice((2, 4, 4, 7))
    contrast_anchor = rng.choice((7, 9))
    answer = melody.random_motif(rng, avoid_rhythm=motif.rhythm, rhythms=rhythms)
    groove = parts.draw_groove(rng, style)  # last, so the style never shifts the melody's draws
    return Palette(
        scale, theme, contrast, cadence, motif, contrast_motif, answer, theme_anchor, contrast_anchor, groove
    )


class PartRngs(NamedTuple):
    melody: random.Random
    bass: random.Random

    @classmethod
    def for_seed(cls, seed: int) -> PartRngs:
        return cls(stream(seed, "melody"), stream(seed, "bass"))


# The first three bars of a phrase, by letter: A the theme motif, B its answer, C the contrast
# motif; lower case is a fresh variation of that letter. A theme always opens on A, so it stays
# recognisable; what follows changes from phrase to phrase.
_THEME_SHAPES = ("AAa", "ABA", "ABa", "AaB", "AAB", "ABb", "AaA")
_CONTRAST_SHAPES = ("CCc", "CcC", "Ccc", "CcB")
# Where each bar starts, in scale steps from the phrase's opening note: level, climbing or sinking.
_CONTOURS = ((0, 0, 0), (0, 2, 0), (0, 2, 4), (0, -2, 0), (0, 1, 2), (0, 0, -2), (0, -2, -4), (0, 3, 1))


def _bars(shape: str, letters: dict[str, Motif], rng: random.Random) -> tuple[Motif, ...]:
    return tuple(letters[c] if c.isupper() else vary(letters[c.upper()], rng)[1] for c in shape)


def _lead_plan(
    rng: random.Random, pal: Palette, name: str
) -> tuple[tuple[Motif, ...], int, int, tuple[int, ...]] | None:
    """(motifs for the first three bars, starting degree, velocity boost, bar shifts), or None for no lead."""
    theme, contrast = pal.theme_motif, pal.contrast_motif
    if name == "intro":
        return None
    if name == "variation":
        theme = vary(theme, rng)[1]
    letters = {"A": theme, "B": pal.answer_motif, "C": contrast}
    contour = rng.choice(_CONTOURS)
    if name == "contrast":
        return _bars(rng.choice(_CONTRAST_SHAPES), letters, rng), pal.contrast_anchor, 6, contour
    if name == "development":
        developed = vary(theme, rng)[1]
        anchor = (pal.theme_anchor + pal.contrast_anchor) // 2
        return (developed, vary(developed, rng)[1], vary(contrast, rng)[1]), anchor, 3, contour
    return _bars(rng.choice(_THEME_SHAPES), letters, rng), pal.theme_anchor, 0, contour


def build_phrase(
    rngs: PartRngs, pal: Palette, name: str, chords: tuple[int, ...], next_chord: int, ending: bool = False
) -> list[NoteEvent]:
    """Every part except the drone for one section, in beats from its start.

    ``next_chord`` opens the following phrase; ``ending`` closes a fixed piece.
    """
    role = ROLES[name]
    events = []
    lead = _lead_plan(rngs.melody, pal, name)
    if lead is not None:
        motifs, anchor, boost, shifts = lead
        lead_notes = melody.melody_phrase(
            rngs.melody, pal.scale, pal.lead_tonic + 12 * pal.groove.lead_octave, chords, motifs, anchor,
            resolve=role == "return", boost=boost, shifts=shifts,
        )
        events += lead_notes
        if pal.groove.style == "combat" and pal.groove.intensity >= 0.75:
            # A grave fight doubles the lead an octave down: weight under the line, not a scream over it.
            events += [replace(e, pitch=e.pitch - 12, velocity=max(1, round(e.velocity * 0.6))) for e in lead_notes]
    if pal.groove.style == "combat" and pal.groove.intensity >= 0.75:
        events += parts.pad(pal.scale, pal.arp_tonic, chords)
    elif pal.groove.pad:
        events += parts.pad(pal.scale, pal.arp_tonic, chords, pal.groove.pad, 52)
    events += parts.arpeggio(pal.scale, pal.arp_tonic, chords, pal.groove, role, ending)
    events += parts.bassline(rngs.bass, pal.scale, pal.bass_tonic, chords, pal.groove, role, next_chord, ending)
    events += parts.percussion(pal.groove, role, len(chords), ending, rngs.bass)
    return events


def event_order(e: NoteEvent) -> tuple[float, str, int]:
    return (e.start, e.track, e.pitch)


def in_combat(
    pal: Palette, style: str, mode: str | None, intensity: float, chords: tuple[tuple[int, ...], ...]
) -> Palette:
    """The palette as a fight this hard plays it; calm styles pass through.

    Combat keeps the palette's degrees in a grimmer mode that every one of
    ``chords`` (all the bars it will play in this key) can stand, unless the
    mode is pinned, and reshapes its groove.
    """
    if style != "combat":
        return pal
    x = parts.level(intensity)
    used = [c for bars in chords for c in bars]
    scale = pal.scale if mode is not None else theory.darken(pal.scale, x, used)
    return replace(pal, scale=scale, groove=parts.intensify(pal.groove, x))


def playable(pal: Palette, chords: tuple[int, ...]) -> tuple[int, ...]:
    """The chords as combat plays them: any that its mode turned diminished or augmented
    gives way to the nearest one that stayed major or minor. Calm palettes pass through."""
    if pal.groove.style != "combat":
        return chords

    def fix(chord: int) -> int:
        for alt in (chord, chord - 2, chord + 2, chord - 1, chord + 1, 0):
            if pal.scale.triad_quality(alt % 7) in ("major", "minor"):
                return alt % 7
        return 0

    return tuple(fix(c) for c in chords)


def compose(
    seed: int, *, style: str = "normal", mode: str | None = None, root: int | None = None, tempo: float | None = None,
    intensity: float = 0.5,
) -> Composition:
    scale, drawn = initial_params(seed, style, mode, root, tempo)
    palette_rng = stream(seed, "palette")
    pal = new_palette(palette_rng, scale, style)
    rngs = PartRngs.for_seed(seed)
    names = ("intro", "theme", "contrast", "return")
    sections = [Section(name, 4 * i, pal.chords(name, palette_rng)) for i, name in enumerate(names)]
    pal = in_combat(pal, style, mode, intensity, tuple(s.chords for s in sections))
    sections = [replace(s, chords=playable(pal, s.chords)) for s in sections]
    scale = pal.scale
    tempo = tempo or drawn / TEMPO_SCALE[style] * pal.groove.tempo

    comp = Composition(
        seed=seed, tempo=tempo, scale=scale, sections=sections, style=style, sound=pal.groove.sound,
        intensity=pal.groove.intensity,
    )
    events = parts.drone(scale, pal.drone_tonic, comp.length_beats)
    for i, section in enumerate(sections):
        is_last = i == len(sections) - 1
        next_chord = 0 if is_last else sections[i + 1].chords[0]
        offset = section.start_bar * BEATS_PER_BAR
        phrase = build_phrase(rngs, pal, section.name, section.chords, next_chord, ending=is_last)
        events += [replace(e, start=e.start + offset) for e in phrase]
    comp.events = sorted(events, key=event_order)
    return comp
