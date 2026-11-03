"""Town and battle: one song built from two progression pools, on the generator's synth engine.

Town (D major, 80 BPM): slow harmony, one chord per 2 bars, maj7 / 6 / 9 voicings.
    title   IVmaj7 - Imaj9        the floating loop that never quite stops
    lift    Imaj7 - II            the Lydian lift (II is major: G#)
    village I6 - vi7 - IVmaj7 - Vadd9
    Flute (soft filtered triangle), plucked harp, wide pad, round bass.

Battle (D minor, 140 BPM): fast harmony, a chord every 2 beats, no pretty extensions.
    crawl   i - bII               Phrygian half-step (power chords)
    tritone i - #iv(dim)          unstable, chaotic
    gothic  i | v(half-dim 7) - V7   builds, then resolves to i
    Gritty: low distorted brass stabs, a dark horn line, staccato low strings, a reese
    bass and a distorted drone underneath, timpani, and a gated military snare.

Then the title loop returns, and the song ends on Imaj9.

Run:  uv run --no-sync python songs/town_and_battle.py      -> out/town_and_battle.wav
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from dungeon_synth_gen.events import NoteEvent
from dungeon_synth_gen.parts import CRASH, KICK, LOW_TOM, MID_TOM, SNARE
from dungeon_synth_gen.synth import SAMPLE_RATE, Instrument, Renderer, write_wav

SEED = 7
TOWN_BPM, BATTLE_BPM = 80, 140
OUT = Path("out/town_and_battle.wav")

TOWN = {
    "flute": Instrument(
        "triangle", gain=0.30, attack=0.06, decay=0.3, sustain=0.8, release=0.35, lowpass=1600,
        vibrato_depth=0.15, vibrato_rate=5.0, vibrato_delay=0.35, reverb=0.5, echo=0.3,
    ),
    "harp": Instrument(
        "saw", gain=0.10, pan=-0.3, attack=0.002, decay=1.4, sustain=0.0, release=0.5, lowpass=3500,
        note_lowpass=700, pluck=0.06, reverb=0.55, echo=0.15,
    ),
    "pad": Instrument(
        "saw", gain=0.05, attack=1.5, decay=0.01, sustain=1.0, release=2.5, lowpass=900,
        detune_cents=10, voices=4, spread=0.7, reverb=0.6,
    ),
    "bass": Instrument("triangle", gain=0.28, attack=0.03, decay=0.5, sustain=0.7, release=0.6, lowpass=500, reverb=0.15),
}
# Battle is gritty, not bright: everything sits low, is detuned wide, distorted hard and filtered dark.
BATTLE = {
    "brass": Instrument(
        "saw", gain=0.12, attack=0.008, decay=0.2, sustain=0.5, release=0.1, lowpass=2800,
        detune_cents=14, voices=4, spread=0.5, note_lowpass=650, pluck=0.08, drive=3.0, reverb=0.25,
    ),
    "horn": Instrument(
        "saw", gain=0.15, attack=0.02, decay=0.3, sustain=0.85, release=0.2, lowpass=2600,
        detune_cents=12, voices=3, spread=0.3, note_lowpass=900, pluck=0.3, drive=2.6,
        vibrato_depth=0.05, vibrato_delay=0.4, reverb=0.3, echo=0.15,
    ),
    "cello": Instrument(
        "saw", gain=0.13, attack=0.003, decay=0.08, sustain=0.3, release=0.04, lowpass=2000,
        detune_cents=8, spread=0.3, note_lowpass=400, pluck=0.04, drive=2.6, reverb=0.1,
    ),
    "reese": Instrument(  # distorted, slowly beating bass under each chord
        "saw", gain=0.14, attack=0.01, decay=0.3, sustain=0.9, release=0.08, lowpass=1800,
        detune_cents=18, spread=0.15, note_lowpass=300, pluck=0.15, drive=3.5, sub=0.7, reverb=0.05,
    ),
    "grind": Instrument(  # a wide distorted drone that never lets up
        "saw", gain=0.05, attack=0.6, decay=0.01, sustain=1.0, release=1.5, lowpass=1200,
        detune_cents=22, voices=5, spread=0.9, drive=2.0, reverb=0.4,
    ),
    "timpani": Instrument("drums", gain=0.5, kit="darksynth", lowpass=6000, reverb=0.45),
    "snare": Instrument("drums", gain=0.26, kit="darksynth", pan=0.1, lowpass=8000, reverb=0.25),  # gated, not 8-bit
}

# Town chords: (bass note, pad voicing), MIDI. The pad's notes are also the flute's landing tones.
TOWN_CHORDS = {
    "IVmaj7": (43, (55, 59, 62, 66)),  # G B D F#
    "Imaj9": (38, (54, 57, 61, 64)),  # F# A C# E over D
    "Imaj7": (38, (50, 54, 57, 61)),  # D F# A C#
    "II": (40, (52, 56, 59, 66)),  # E G# B, add9 F#
    "I6": (38, (54, 57, 59, 62)),  # F# A B D
    "vi7": (47, (57, 59, 62, 66)),  # A B D F#
    "Vadd9": (45, (52, 57, 59, 61)),  # E A B C#
}
D_MAJOR = {2, 4, 6, 7, 9, 11, 1}
D_LYDIAN = {2, 4, 6, 8, 9, 11, 1}

# Battle chords: (cello root, brass voicing, horn tones). Power chords where the rules call for them.
BATTLE_CHORDS = {
    "i": (38, (50, 57, 62), (2, 5, 9)),  # D5
    "bII": (39, (51, 58, 63), (3, 7, 10)),  # Eb5
    "#iv°": (44, (56, 59, 62), (8, 11, 2)),  # G# B D
    "vø7": (45, (57, 60, 63, 67), (9, 0, 3, 7)),  # A C Eb G
    "V7": (45, (57, 61, 64, 67), (9, 1, 4, 7)),  # A C# E G
}

TOWN_RHYTHMS = ((2, 1, 1), (3, 1), (1.5, 0.5, 2), (1, 1, 2), (2, 2), (2.5, 0.5, 1))
CADENCES = ((4,), (2, 2), (3, 1))


def note(track: str, start: float, length: float, pitch: int, velocity: int) -> NoteEvent:
    return NoteEvent(track, start, length, pitch, max(1, min(127, velocity)))


# ---------------------------------------------------------------- town


def flute_line(rng: random.Random, chords: list[str], scale: set[int], low: int = 64, high: int = 83) -> list[NoteEvent]:
    """Long notes: chord tones (extensions included) on strong beats, scale steps between.

    Each 4-bar phrase reuses its first rhythm in bar 3 and closes on a long note,
    so the line answers itself.
    """
    events, pitch = [], 74
    ladder = [p for p in range(low - 2, high + 3) if p % 12 in scale]
    for phrase in range(0, len(chords), 4):
        r1, r2 = rng.sample(TOWN_RHYTHMS, 2)
        for bar, rhythm in enumerate((r1, r2, r1, rng.choice(CADENCES))):
            index = phrase + bar
            if index >= len(chords):
                break
            tones = {p % 12 for p in TOWN_CHORDS[chords[index]][1]}
            t = 0.0
            for length in rhythm:
                strong = t in (0, 2) or length >= 2
                if strong:
                    near = [p for p in range(low, high + 1) if p % 12 in tones and abs(p - pitch) <= 7]
                    if len(near) > 1 and rng.random() < 0.7:
                        near = [p for p in near if p != pitch] or near
                    pitch = rng.choices(near, [1 / (1 + abs(p - pitch)) for p in near])[0]
                else:
                    step = rng.choice((-1, 1)) if low + 4 < pitch < high - 4 else (1 if pitch <= low + 4 else -1)
                    pitch = ladder[min(len(ladder) - 1, max(0, ladder.index(min(ladder, key=lambda p: abs(p - pitch))) + step))]
                accent = 10 if t == 0 else 0
                events.append(note("flute", index * 4 + t, length * 0.95, pitch, 80 + accent + rng.randint(-4, 4)))
                t += length
    return events


def town_section(rng: random.Random, chords: list[str], scale: set[int], flute_from: int = 0) -> tuple[list[NoteEvent], int]:
    """``chords`` has one entry per bar; each chord lasts 2 bars."""
    events = []
    for bar, name in enumerate(chords):
        bass, pad = TOWN_CHORDS[name]
        start = bar * 4
        if bar % 2 == 0:  # a new chord: pad and bass hold it for both bars
            events += [note("pad", start, 8, p, 70) for p in pad]
            events.append(note("bass", start, 7.6, bass, 85))
        else:
            events.append(note("bass", start, 3.6, bass, 65))
        tones = sorted(pad) + [pad[0] + 12, pad[1] + 12]
        for step, i in enumerate((0, 1, 2, 3, 4, 5, 4, 2)):  # slow eighth-note harp roll
            events.append(note("harp", start + step * 0.5, 1.5, tones[i], 62 + (12 if step == 0 else 0)))
    melody = flute_line(rng, chords, scale)
    events += [e for e in melody if e.start >= flute_from * 4]
    return events, len(chords)


# ---------------------------------------------------------------- battle

# Military snare, sixteenth grid: (step, velocity). Heavy hits with quiet ghost notes, not a rattle.
SNARE_BAR = ((0, 105), (3, 35), (4, 100), (7, 35), (8, 105), (11, 35), (12, 100), (14, 45), (15, 55))

# Horn lines, one bar per tuple: (beat, length, pitch). They creep by half steps against the chords.
CRAWL_HORN = (
    ((0, 1.5, 69), (2, 1.5, 70)),  # A | Bb
    ((0, 1, 69), (1, 1, 74), (2, 2, 75)),  # A D | Eb
    ((0, 1.5, 74), (2, 1.5, 75)),  # D | Eb
    ((0, 1, 74), (1, 1, 77), (2, 2, 79)),  # D F | G
)
TRITONE_HORN = (
    ((0, 2, 69), (2, 2, 68)),  # A | G#
    ((0, 1, 77), (1, 1, 74), (2, 2, 71)),  # F D | B  (tritone F-B)
)
GOTHIC_HORN = (
    ((0, 1, 74), (1, 1, 77), (2, 2, 81)),  # D F A
    ((0, 1, 79), (1, 1, 75), (2, 1, 73), (3, 1, 76)),  # G Eb | C# E  -> back to D
)


def battle_bars(changes: list[tuple[str, ...]], horn: tuple, gallop: bool = False) -> list[NoteEvent]:
    """One bar per entry of ``changes``: a chord per 2 beats (or per beat, when the tuple has four)."""
    events = []
    for bar, chords in enumerate(changes):
        start = bar * 4
        span = 4 / len(chords)
        for c, name in enumerate(chords):
            root, brass, _ = BATTLE_CHORDS[name]
            t0 = start + c * span
            for hit in (0, 0.75, 1.5)[: 3 if span >= 2 else 1]:  # brass stabs
                events += [note("brass", t0 + hit, 0.3, p - 12, 100 if hit == 0 else 85) for p in brass]  # an octave down
            events.append(note("timpani", t0, 0.25, LOW_TOM if c % 2 == 0 else MID_TOM, 110 if c == 0 else 90))
            events.append(note("reese", t0, span * 0.95, root, 105))
            figure = ((0, 0.5), (0.5, 0.25), (0.75, 0.25)) if gallop else ((0, 0.5), (0.5, 0.5))
            for beat in range(int(span)):
                for i, (t, length) in enumerate(figure):
                    octave = 12 if (beat + i) % 4 == 2 else 0
                    events.append(note("cello", t0 + beat + t, length * 0.6, root + 12 + octave, 105 if t == 0 and beat == 0 else 85))
        events.append(note("timpani", start, 0.25, KICK, 100))
        events += [note("snare", start + step / 4, 0.25, SNARE, v) for step, v in SNARE_BAR]
        events += [note("horn", start + b, length * 0.95, p - 12, 100) for b, length, p in horn[bar % len(horn)]]
    events += [note("grind", 0, len(changes) * 4, p, 90) for p in (50, 57)]  # D3 + A3, above the reese
    events.append(note("timpani", 0, 0.25, CRASH, 95))
    return events


def alarm_bar() -> list[NoteEvent]:
    """One bar of timpani and snare rolling up from quiet, into the fight."""
    events = []
    for step in range(16):
        events.append(note("timpani", step / 4, 0.25, MID_TOM if step % 2 else LOW_TOM, 50 + step * 4))
        events.append(note("snare", step / 4 + 0.125, 0.25, SNARE, 40 + step * 5))
    return events


def final_hit() -> list[NoteEvent]:
    _, brass, _ = BATTLE_CHORDS["i"]
    events = [note("brass", 0, 2.5, p - 12, 115) for p in brass + (74,)]
    events += [note("cello", 0, 2.5, 50, 115), note("horn", 0, 3, 62, 110), note("reese", 0, 3, 38, 120)]
    events += [note("timpani", 0, 0.25, KICK, 120), note("timpani", 0, 0.25, LOW_TOM, 120), note("timpani", 0, 0.25, CRASH, 110)]
    return events


# ---------------------------------------------------------------- song


def main() -> None:
    rng = random.Random(SEED)
    title_chords = ["IVmaj7", "IVmaj7", "Imaj9", "Imaj9"] * 2
    lift_chords = ["Imaj7", "Imaj7", "II", "II"] * 2
    village_chords = ["I6", "I6", "vi7", "vi7", "IVmaj7", "IVmaj7", "Vadd9", "Vadd9"]

    plan: list[tuple[str, dict, float, list[NoteEvent], float]] = []  # (label, instruments, bpm, events, bars)
    title, n = town_section(rng, title_chords, D_MAJOR, flute_from=4)
    plan.append(("town: title loop   IVmaj7 - Imaj9", TOWN, TOWN_BPM, title, n))
    lift, n = town_section(rng, lift_chords, D_LYDIAN)
    plan.append(("town: Lydian lift  Imaj7 - II", TOWN, TOWN_BPM, lift, n))
    village, n = town_section(rng, village_chords, D_MAJOR)
    plan.append(("town: village      I6 - vi7 - IVmaj7 - Vadd9", TOWN, TOWN_BPM, village, n))

    plan.append(("battle: alarm", BATTLE, BATTLE_BPM, alarm_bar(), 1))
    plan.append(("battle: crawl      i - bII", BATTLE, BATTLE_BPM, battle_bars([("i", "bII")] * 8, CRAWL_HORN), 8))
    plan.append(("battle: tritone    i - #iv dim", BATTLE, BATTLE_BPM, battle_bars([("i", "#iv°")] * 4, TRITONE_HORN), 4))
    gothic = [("i", "i"), ("vø7", "V7")] * 4
    plan.append(("battle: gothic     i | v m7b5 - V7", BATTLE, BATTLE_BPM, battle_bars(gothic, GOTHIC_HORN, gallop=True), 8))
    plan.append(("battle: final hit  i", BATTLE, BATTLE_BPM, final_hit(), 2))

    outro, _ = town_section(random.Random(SEED), ["IVmaj7", "IVmaj7", "Imaj9", "Imaj9"], D_MAJOR, flute_from=0)
    outro = [e for e in outro if e.track != "flute" or e.start < 8] + [note("pad", 16, 8, p, 60) for p in TOWN_CHORDS["Imaj9"][1]]
    outro.append(note("bass", 16, 8, 38, 70))
    plan.append(("town: title returns, ends on Imaj9", TOWN, TOWN_BPM, outro, 6))

    renderer = Renderer(sample_rate=SAMPLE_RATE, seed=SEED)
    chunks, seconds = [], 0.0
    for label, instruments, bpm, events, bars in plan:
        renderer.instruments = instruments  # notes already sounding keep their own instrument
        renderer.set_tempo(bpm)
        renderer.schedule(sorted(events, key=lambda e: (e.start, e.track, e.pitch)))
        n = round(bars * 4 * 60 / bpm * SAMPLE_RATE)
        chunks.append(renderer.render(n))
        minutes, secs = divmod(int(seconds), 60)
        print(f"{minutes}:{secs:02d}  {label}")
        seconds += n / SAMPLE_RATE
    chunks.append(renderer.render(int(5 * SAMPLE_RATE)))  # let the reverb ring out

    mix = np.concatenate(chunks)
    mix[-SAMPLE_RATE:] *= np.linspace(1, 0, SAMPLE_RATE)[:, None]
    mix = np.tanh(1.6 * mix / np.abs(mix).max())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_wav(OUT, mix * (0.89 / np.abs(mix).max()))
    print(f"wrote {OUT} ({len(mix) / SAMPLE_RATE:.0f} s)")


if __name__ == "__main__":
    main()
