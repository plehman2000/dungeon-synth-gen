"""Endless composition: an unbroken chain of phrases that drifts through keys and styles.

Phrases are grouped into key regions. Each region draws a style (unless one
is pinned), a fresh palette (its theme usually evolves from the last
region's) and a short form such as intro -> theme -> contrast -> development
-> return. Keys only move to neighbours that still contain the old tonic,
and each region's drone is timed to crossfade into the next one's, so there
is never a seam.

With ``twins=True`` every phrase also carries its twin in the other gear:
combat, or normal while the region is combat. A twin shares the phrase's
palette, chords and lead (its part RNGs are cloned, so the stream's own
draws never change) and swaps in that style's groove and tempo.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator
from dataclasses import replace

from dungeon_synth_gen import arrange, parts, theory
from dungeon_synth_gen.events import BEATS_PER_BAR, NoteEvent, Phrase, Section
from dungeon_synth_gen.theory import Scale

_TEMPO_STEPS = (-4, -2, 0, 0, 2, 4)  # BPM drift between regions, before the style's tempo scale
_KEEP_STYLE = 0.5  # chance a region keeps the previous region's style
_KEEP_MODE = 0.6  # chance a region keeps the previous mode, when the style allows it


def _form(rng: random.Random, first: bool) -> list[str]:
    plan = ["intro"] if first or rng.random() < 0.6 else []
    plan.append("theme")
    if rng.random() < 0.5:
        plan.append("variation")
    plan.append("contrast")
    if rng.random() < 0.5:
        plan.append("development")
    plan.append("return")
    return plan


def _ordered(events: list[NoteEvent]) -> tuple[NoteEvent, ...]:
    return tuple(sorted(events, key=arrange.event_order))


def twin_style(style: str) -> str:
    """The gear a style's twin plays in."""
    return "normal" if style == "combat" else "combat"


def _clone(rngs: arrange.PartRngs) -> arrange.PartRngs:
    copies = []
    for rng in rngs:
        copy = random.Random()
        copy.setstate(rng.getstate())
        copies.append(copy)
    return arrange.PartRngs(*copies)


def endless(
    seed: int, *, style: str | None = None, mode: str | None = None, root: int | None = None, tempo: float | None = None,
    twins: bool = False,
) -> Iterator[Phrase]:
    """Phrases forever. Any of ``style``, ``mode``, ``root``, ``tempo`` pins that parameter for the whole stream.

    The stream opens in the same style, key and tempo as ``compose(seed)`` with that seed's style.
    """
    current_style = style or arrange.seeded_style(seed)
    scale, bpm = arrange.initial_params(seed, current_style, mode, root, tempo)
    form = arrange.stream(seed, "form")
    palette_rng = arrange.stream(seed, "palette")
    twin_rng = arrange.stream(seed, "twin")
    rngs = arrange.PartRngs.for_seed(seed)
    pal = arrange.new_palette(palette_rng, scale, current_style)
    index = bar = 0

    for region in itertools.count():
        plan = _form(form, first=region == 0)
        chords = [pal.chords(name, palette_rng) for name in plan]
        region_beats = sum(len(c) for c in chords) * BEATS_PER_BAR
        if twins:
            gear = twin_style(current_style)
            twin_pal = replace(pal, groove=parts.draw_groove(twin_rng, gear))
            twin_bpm = bpm / arrange.TEMPO_SCALE[current_style] * arrange.TEMPO_SCALE[gear]
        for i, name in enumerate(plan):
            is_last = i == len(plan) - 1
            next_chord = 0 if is_last else chords[i + 1][0]
            twin_rngs = _clone(rngs) if twins else None
            events = arrange.build_phrase(rngs, pal, name, chords[i], next_chord)
            # Held for the whole region; it releases exactly as the next region's drone fades in.
            drone = parts.drone(pal.scale, pal.drone_tonic, region_beats) if i == 0 else []
            section = Section(name, bar, chords[i])
            twin = None
            if twins:
                twin_events = arrange.build_phrase(twin_rngs, twin_pal, name, chords[i], next_chord) + drone
                twin = Phrase(index, region, section, pal.scale, twin_bpm, gear, _ordered(twin_events))
            yield Phrase(index, region, section, pal.scale, bpm, current_style, _ordered(events + drone), twin)
            index += 1
            bar += len(chords[i])

        previous_style = current_style
        if style is None and form.random() >= _KEEP_STYLE:
            current_style = arrange.draw_style(form)
        keep_mode = current_style == previous_style and form.random() < _KEEP_MODE
        next_mode = mode or (pal.scale.mode if keep_mode else arrange.draw_mode(form, current_style))
        next_scale = Scale(root, next_mode) if root is not None else theory.modulate(form, pal.scale, next_mode)
        if tempo is None:
            factor = arrange.TEMPO_SCALE[current_style]
            low, high = (t * factor for t in arrange.TEMPO_RANGE)
            bpm = bpm / arrange.TEMPO_SCALE[previous_style] * factor
            bpm = min(high, max(low, bpm + form.choice(_TEMPO_STEPS) * factor))
        pal = arrange.new_palette(palette_rng, next_scale, current_style, previous=pal)
