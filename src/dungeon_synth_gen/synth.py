"""Audio engine: chiptune oscillators, voices, instruments, and a streaming mixer.

Square and saw use PolyBLEP to tame aliasing; the triangle is quantised to 16
levels like the NES triangle channel, then box-filtered so its steps don't
alias; noise comes from a 15-bit LFSR.

Combat voices are darksynth: detuned unison saws through a per-note filter
sweep and tanh drive (filtered first, so the drive doesn't alias), a clean
sine sub, and a sidechain pump that ducks them under every on-beat drum hit.

``Renderer`` holds everything that spans time (sounding voices, filter
memory, echo history, reverb tail), so it can produce audio in blocks of any
size, forever, and the blocks join without seams.
"""

from __future__ import annotations

import functools
import wave
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy import signal

from dungeon_synth_gen import effects
from dungeon_synth_gen.events import Composition, NoteEvent
from dungeon_synth_gen.parts import CRASH, HAT, KICK, LOW_TOM, MID_TOM, SNARE
from dungeon_synth_gen.theory import midi_to_freq

SAMPLE_RATE = 44100
REVERB_LEVEL = 0.4  # puts the room about 5 dB under the dry mix
ECHO_FEEDBACK = 0.45
ECHO_LOWPASS = 1500  # Hz
_TRIANGLE_TAPS = (np.arange(8) + 0.5) / 8 - 0.5  # sub-sample offsets, in samples, averaged per triangle sample
_PUMP_ATTACK = 0.004  # seconds for the sidechain to duck: fast, but slow enough not to click
_PUMP_RECOVERY = 0.2  # beats: time constant of the swell back after each hit
_PUMP_DRUMS = (KICK, SNARE, LOW_TOM, MID_TOM)  # on the beat, these trigger the pump: a kick-snare backbeat pumps every beat


@dataclass(frozen=True)
class Instrument:
    wave: str  # "square" | "saw" | "triangle" | "drums"
    gain: float
    pan: float = 0.0  # -1 left .. 1 right
    duty: float = 0.5
    attack: float = 0.01
    decay: float = 0.1
    sustain: float = 0.8
    release: float = 0.1
    lowpass: float | None = None  # Hz
    vibrato_depth: float = 0.0  # semitones
    vibrato_rate: float = 5.0  # Hz
    vibrato_delay: float = 0.3  # seconds before vibrato fades in
    detune_cents: float = 0.0  # > 0 renders `voices` unison voices spread across +-detune_cents
    voices: int = 2  # unison voice count when detuned
    spread: float = 0.0  # pan distance of the outermost detuned voices
    note_lowpass: float | None = None  # Hz: per-note filter, ahead of the drive
    pluck: float = 0.0  # seconds: each note opens note_lowpass fully, then closes it over this time
    drive: float = 0.0  # tanh distortion on each note; 0 is clean
    sub: float = 0.0  # level of a clean sine at the fundamental, under the driven voices
    duck: float = 0.0  # sidechain depth, 0..1: how far each on-beat drum hit pushes this instrument down
    reverb: float = 0.3  # send level
    echo: float = 0.0  # send level
    linear_release: bool = False  # with attack == release, back-to-back notes crossfade at constant level
    kit: str = "nes"  # drums only: "nes" | "darksynth"


INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "square", gain=0.28, duty=0.5, attack=0.01, decay=0.3, sustain=0.6, release=0.3,
        lowpass=2800, vibrato_depth=0.18, vibrato_rate=5.2, vibrato_delay=0.25, reverb=0.35, echo=0.45,
    ),
    "arp": Instrument(
        "square", gain=0.16, pan=0.3, duty=0.25, attack=0.004, decay=0.3, sustain=0.25, release=0.2,
        lowpass=1700, reverb=0.5, echo=0.12,
    ),
    "bass": Instrument(
        "triangle", gain=0.36, attack=0.008, decay=0.25, sustain=0.85, release=0.12,
        lowpass=2500, reverb=0.08,  # rounds off the 4-bit steps, like the NES output filter
    ),
    "drone": Instrument(
        "saw", gain=0.10, attack=4.0, decay=0.01, sustain=1.0, release=4.0,
        lowpass=500, detune_cents=7, spread=0.6, reverb=0.6, linear_release=True,
    ),
    "perc": Instrument("drums", gain=0.42, lowpass=5000, reverb=0.4),
}
# Combat: darksynth. A five-saw supersaw lead, a distorted reese bass over a
# clean sub, driven saw plucks, and a wide pad pumping hard against the kick.
COMBAT_INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "saw", gain=0.28, attack=0.005, decay=0.25, sustain=0.7, release=0.18, lowpass=5500,
        vibrato_depth=0.08, vibrato_rate=5.5, vibrato_delay=0.3, detune_cents=16, voices=5, spread=0.5,
        note_lowpass=3000, pluck=0.2, drive=2.0, duck=0.25, reverb=0.25, echo=0.3,
    ),
    "arp": Instrument(
        "saw", gain=0.15, pan=0.3, attack=0.002, decay=0.12, sustain=0.35, release=0.08, lowpass=7000,
        detune_cents=8, spread=0.4, note_lowpass=500, pluck=0.07, drive=2.0, duck=0.5, reverb=0.2, echo=0.35,
    ),
    "bass": Instrument(
        "saw", gain=0.25, attack=0.003, decay=0.15, sustain=0.85, release=0.05, lowpass=3000,
        detune_cents=12, spread=0.15, note_lowpass=350, pluck=0.1, drive=3.0, sub=0.8, duck=0.6, reverb=0.05,
    ),
    "drone": Instrument(
        "saw", gain=0.07, attack=1.5, decay=0.01, sustain=1.0, release=1.5, lowpass=1500,
        detune_cents=14, voices=5, spread=0.8, duck=0.85, reverb=0.4, linear_release=True,
    ),
    "perc": replace(INSTRUMENTS["perc"], kit="darksynth", gain=0.3, lowpass=12000, reverb=0.2),
}
# Ambient: soft plucked voices that decay instead of sustaining, a darker
# pad, and more room. The drone keeps attack == release so regions crossfade.
AMBIENT_INSTRUMENTS: dict[str, Instrument] = INSTRUMENTS | {
    "lead": replace(
        INSTRUMENTS["lead"], wave="triangle", gain=0.44, attack=0.004, decay=1.6, sustain=0.0, release=0.8,
        lowpass=2400, vibrato_depth=0.0, reverb=0.6, echo=0.35,
    ),
    "arp": replace(INSTRUMENTS["arp"], duty=0.5, gain=0.115, decay=1.4, sustain=0.0, release=0.6, lowpass=1100, reverb=0.7, echo=0.0),
    "bass": replace(INSTRUMENTS["bass"], gain=0.3, attack=0.02, decay=1.5, sustain=0.4, release=0.5),
    "drone": replace(INSTRUMENTS["drone"], gain=0.08, lowpass=380),
}
# Brut: Carpenter Brut-style darksynth turned up. A seven-saw lead driven
# into fuzz, a screaming reese bass over a full sub that the kick flattens on
# every beat, and short driven plucks ricocheting through the echo.
BRUT_INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "saw", gain=0.26, attack=0.004, decay=0.2, sustain=0.75, release=0.15, lowpass=6500,
        vibrato_depth=0.1, vibrato_rate=6.0, vibrato_delay=0.25, detune_cents=24, voices=7, spread=0.7,
        note_lowpass=2200, pluck=0.3, drive=4.0, duck=0.3, reverb=0.25, echo=0.35,
    ),
    "arp": Instrument(
        "saw", gain=0.13, pan=-0.25, attack=0.001, decay=0.08, sustain=0.3, release=0.05, lowpass=8000,
        detune_cents=10, spread=0.6, note_lowpass=700, pluck=0.05, drive=3.0, duck=0.6, reverb=0.15, echo=0.4,
    ),
    "bass": Instrument(
        "saw", gain=0.27, attack=0.002, decay=0.1, sustain=0.8, release=0.03, lowpass=3500,
        detune_cents=20, voices=3, spread=0.2, note_lowpass=500, pluck=0.07, drive=6.0, sub=1.0, duck=0.8, reverb=0.03,
    ),
    "drone": replace(COMBAT_INSTRUMENTS["drone"], gain=0.08, duck=0.95),
    "perc": replace(COMBAT_INSTRUMENTS["perc"], gain=0.36, lowpass=14000, reverb=0.25),
}
# Industrial: hard square waves, a clipped pulse bass answering the kick on
# the off-beats, and a thin needle of an arp soaked in echo.
INDUSTRIAL_INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "square", gain=0.22, attack=0.003, decay=0.15, sustain=0.6, release=0.1, lowpass=5000,
        detune_cents=8, spread=0.3, note_lowpass=1500, pluck=0.12, drive=3.5, duck=0.3, reverb=0.3, echo=0.45,
    ),
    "arp": Instrument(
        "square", gain=0.11, pan=0.35, duty=0.125, attack=0.001, decay=0.06, sustain=0.1, release=0.04,
        lowpass=6000, drive=2.0, duck=0.5, reverb=0.25, echo=0.5,
    ),
    "bass": Instrument(
        "square", gain=0.25, duty=0.3, attack=0.002, decay=0.08, sustain=0.7, release=0.03, lowpass=3000,
        note_lowpass=450, pluck=0.05, drive=4.0, sub=0.8, duck=0.7, reverb=0.05,
    ),
    "drone": replace(COMBAT_INSTRUMENTS["drone"], lowpass=900),
    "perc": replace(COMBAT_INSTRUMENTS["perc"], gain=0.34, reverb=0.3),
}
# Doom: everything fuzzed and slow. A wide fuzz-pulse lead, a sludge bass
# that growls open on each note, and a cavernous room.
DOOM_INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "square", gain=0.24, duty=0.25, attack=0.01, decay=0.4, sustain=0.8, release=0.4, lowpass=3500,
        vibrato_depth=0.25, vibrato_rate=4.5, vibrato_delay=0.35, detune_cents=10, voices=3, spread=0.5,
        note_lowpass=1800, drive=6.0, duck=0.15, reverb=0.45, echo=0.3,
    ),
    "arp": Instrument(
        "saw", gain=0.11, pan=0.3, attack=0.005, decay=0.5, sustain=0.3, release=0.4, lowpass=2500,
        detune_cents=10, spread=0.6, drive=2.5, duck=0.3, reverb=0.5, echo=0.25,
    ),
    "bass": Instrument(
        "saw", gain=0.29, attack=0.005, decay=0.4, sustain=0.9, release=0.2, lowpass=2000,
        detune_cents=25, voices=3, spread=0.3, note_lowpass=300, pluck=0.25, drive=7.0, sub=1.0, duck=0.5, reverb=0.1,
    ),
    "drone": replace(COMBAT_INSTRUMENTS["drone"], gain=0.09, lowpass=800, duck=0.6),
    "perc": replace(COMBAT_INSTRUMENTS["perc"], gain=0.36, reverb=0.45),
}
# Chiptrash: the NES voices at a sprint, a thin pulse lead with a little bite,
# and the 8-bit kit hit hard.
CHIPTRASH_INSTRUMENTS: dict[str, Instrument] = {
    "lead": replace(
        INSTRUMENTS["lead"], duty=0.125, gain=0.25, decay=0.15, sustain=0.7, release=0.1, lowpass=6000,
        vibrato_depth=0.12, vibrato_rate=6.5, drive=1.5, duck=0.2, reverb=0.2, echo=0.3,
    ),
    "arp": replace(INSTRUMENTS["arp"], gain=0.13, decay=0.08, sustain=0.2, release=0.05, lowpass=5000, echo=0.25),
    "bass": replace(INSTRUMENTS["bass"], gain=0.42, decay=0.1, sustain=0.9, release=0.03, lowpass=4000, duck=0.3),
    "drone": replace(INSTRUMENTS["drone"], gain=0.06),
    "perc": replace(INSTRUMENTS["perc"], gain=0.5, lowpass=9000, reverb=0.25),
}
# Organ: a chapel organ, slow to speak, held chords on a voice that changes chord
# without a four-second swell, and a square pedal bass.
ORGAN_INSTRUMENTS: dict[str, Instrument] = {
    "lead": Instrument(
        "saw", gain=0.2, attack=0.05, decay=0.2, sustain=0.85, release=0.3, lowpass=1900,
        vibrato_depth=0.07, vibrato_rate=5.5, vibrato_delay=0.4, detune_cents=6, reverb=0.6, echo=0.12,
    ),
    "arp": Instrument(
        "square", gain=0.09, pan=0.25, attack=0.03, decay=0.3, sustain=0.7, release=0.3, lowpass=1300, reverb=0.6,
    ),
    "bass": Instrument(
        "square", gain=0.24, attack=0.02, decay=0.2, sustain=0.95, release=0.2, lowpass=700, sub=0.5, reverb=0.25,
    ),
    "drone": Instrument(
        "saw", gain=0.07, attack=0.6, decay=0.01, sustain=1.0, release=0.6, lowpass=900,
        detune_cents=8, spread=0.6, reverb=0.7, linear_release=True,
    ),
    "perc": INSTRUMENTS["perc"],
}
# Harpsichord: bright plucked saws, quick to fade, close and dry.
HARPSICHORD_INSTRUMENTS: dict[str, Instrument] = INSTRUMENTS | {
    "lead": Instrument(
        "saw", gain=0.22, attack=0.002, decay=0.6, sustain=0.0, release=0.25, lowpass=7000,
        note_lowpass=2500, pluck=0.12, reverb=0.35, echo=0.2,
    ),
    "arp": Instrument(
        "saw", gain=0.1, pan=-0.3, attack=0.001, decay=0.35, sustain=0.0, release=0.15, lowpass=6000,
        note_lowpass=1800, pluck=0.08, reverb=0.35, echo=0.1,
    ),
    "bass": replace(INSTRUMENTS["bass"], gain=0.34, decay=0.4, sustain=0.3, release=0.15),
    "drone": replace(INSTRUMENTS["drone"], gain=0.06),
}
# March: a thinner pulse lead, a clipped arp, and the drums up front.
MARCH_INSTRUMENTS: dict[str, Instrument] = INSTRUMENTS | {
    "lead": replace(INSTRUMENTS["lead"], duty=0.25, gain=0.24, lowpass=2600, vibrato_depth=0.12, echo=0.35),
    "arp": replace(INSTRUMENTS["arp"], duty=0.125, decay=0.15, sustain=0.15),
    "perc": replace(INSTRUMENTS["perc"], gain=0.5),
}
# Mist: a slow-swelling choir of detuned saws over a held chord, everything far away.
MIST_INSTRUMENTS: dict[str, Instrument] = AMBIENT_INSTRUMENTS | {
    "lead": Instrument(
        "saw", gain=0.2, attack=0.35, decay=0.5, sustain=0.8, release=1.2, lowpass=1600,
        vibrato_depth=0.1, vibrato_rate=4.5, vibrato_delay=0.6, detune_cents=14, voices=3, spread=0.5,
        reverb=0.75, echo=0.3,
    ),
    "arp": replace(AMBIENT_INSTRUMENTS["arp"], wave="triangle", gain=0.14, decay=2.0, release=1.0, lowpass=1500, reverb=0.75),
    "drone": Instrument(
        "saw", gain=0.07, attack=1.5, decay=0.01, sustain=1.0, release=1.5, lowpass=600,
        detune_cents=10, voices=3, spread=0.8, reverb=0.7, linear_release=True,
    ),
}
# Music box: glassy triangle chimes, an octave up, in a small bright room.
MUSICBOX_INSTRUMENTS: dict[str, Instrument] = AMBIENT_INSTRUMENTS | {
    "lead": Instrument(
        "triangle", gain=0.36, attack=0.001, decay=0.9, sustain=0.0, release=0.6, lowpass=5000, reverb=0.55, echo=0.35,
    ),
    "arp": replace(AMBIENT_INSTRUMENTS["arp"], wave="triangle", gain=0.1, decay=1.0, release=0.5, lowpass=3000, reverb=0.6),
}
# Synth sets by name: one per flavour (parts.FLAVOURS), and each style's original under its own name.
SOUNDS: dict[str, dict[str, Instrument]] = {
    "normal": INSTRUMENTS,
    "ambient": AMBIENT_INSTRUMENTS,
    "crypt": INSTRUMENTS,
    "organ": ORGAN_INSTRUMENTS,
    "harpsichord": HARPSICHORD_INSTRUMENTS,
    "march": MARCH_INSTRUMENTS,
    "harp": AMBIENT_INSTRUMENTS,
    "mist": MIST_INSTRUMENTS,
    "musicbox": MUSICBOX_INSTRUMENTS,
    "combat": COMBAT_INSTRUMENTS,
    "darksynth": COMBAT_INSTRUMENTS,
    "brut": BRUT_INSTRUMENTS,
    "industrial": INDUSTRIAL_INSTRUMENTS,
    "doom": DOOM_INSTRUMENTS,
    "chiptrash": CHIPTRASH_INSTRUMENTS,
}
_CALM_SOUNDS = ("normal", "ambient", "crypt", "organ", "harpsichord", "march", "harp", "mist", "musicbox")


@functools.cache
def _sound(name: str, intensity: float) -> dict[str, Instrument]:
    """A combat synth set for a fight this hard, 0.5 as written.

    Easier is smaller: less drive, darker filters, a shallower pump. Harder is
    bigger, not dirtier: a little less drive, wider unison, a bigger room.
    """
    instruments = SOUNDS[name]
    k = intensity - 0.5
    if name in _CALM_SOUNDS or k == 0:
        return instruments

    def push(inst: Instrument) -> Instrument:
        if inst.wave == "drums":
            return replace(inst, gain=inst.gain * (1 + 0.3 * k), reverb=min(1.0, inst.reverb + 0.3 * max(k, 0)))
        if k < 0:
            return replace(
                inst,
                drive=inst.drive * (1 + 0.8 * k),
                note_lowpass=inst.note_lowpass and inst.note_lowpass * (1 + k),
                duck=inst.duck * (1 + 0.6 * k),
                detune_cents=inst.detune_cents * (1 + 0.6 * k),
            )
        return replace(
            inst,
            drive=inst.drive * (1 - 0.6 * k),
            note_lowpass=inst.note_lowpass and inst.note_lowpass * (1 + 0.6 * k),
            detune_cents=inst.detune_cents * (1 + 0.4 * k),
            reverb=min(1.0, inst.reverb + 0.4 * k),
        )

    return {track: push(inst) for track, inst in instruments.items()}


# Master saturation: gentle rounds off drum peaks, combat squashes harder.
_DRIVE = {**dict.fromkeys(_CALM_SOUNDS, 1.6), "combat": 2.4, "darksynth": 2.4, "brut": 3.0, "industrial": 2.8, "doom": 2.6, "chiptrash": 2.2}
# Typical raw-mix peak per sound. A stream can't normalise to a peak it hasn't
# heard yet, so it saturates against this fixed reference instead.
_STREAM_REFERENCE = {
    "normal": 0.85, "ambient": 0.75, "crypt": 0.85, "organ": 1.0, "harpsichord": 0.37, "march": 0.8,
    "harp": 0.75, "mist": 0.6, "musicbox": 0.6, "combat": 1.05, "darksynth": 1.05,
    "brut": 1.1, "industrial": 1.2, "doom": 1.45, "chiptrash": 0.85,
}


def _blep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """PolyBLEP correction for a unit step at phase 0."""
    out = np.zeros_like(t)
    lo = t < dt
    x = t[lo] / dt[lo]
    out[lo] = 2 * x - x * x - 1
    hi = t > 1 - dt
    x = (t[hi] - 1) / dt[hi]
    out[hi] = x * x + 2 * x + 1
    return out


def oscillator(wave_name: str, phase: np.ndarray, dt: np.ndarray, duty: float = 0.5) -> np.ndarray:
    """One waveform sample per phase value (in cycles); ``dt`` is cycles per sample."""
    t = phase % 1.0
    if wave_name == "square":
        y = np.where(t < duty, 1.0, -1.0) + _blep(t, dt) - _blep((t - duty) % 1.0, dt)
        return y - (2 * duty - 1)  # remove the DC offset of narrow pulses
    if wave_name == "saw":
        return 2 * t - 1 - _blep(t, dt)
    if wave_name == "triangle":
        # Averaging the staircase across each sample lets a step land between
        # samples; snapped to the sample grid, the steps alias into a grainy hiss.
        sub = (t[..., None] + dt[..., None] * _TRIANGLE_TAPS) % 1.0
        tri = 1 - 4 * np.abs(sub - 0.5)
        return (np.round((tri + 1) * 7.5) / 7.5 - 1).mean(axis=-1)
    raise ValueError(f"unknown waveform: {wave_name!r}")


@functools.cache
def _lfsr(short: bool) -> np.ndarray:
    """NES-style noise: a 15-bit LFSR, tapped at bit 6 for the metallic short mode."""
    tap = 6 if short else 1
    register = 1
    out = np.empty(32767)
    for i in range(len(out)):
        bit = (register ^ (register >> tap)) & 1
        register = (register >> 1) | (bit << 14)
        out[i] = 1.0 if register & 1 else -1.0
    return out


def noise(t: np.ndarray, rate: float, short: bool = False) -> np.ndarray:
    seq = _lfsr(short)
    return seq[(t * rate).astype(np.int64) % len(seq)]


def envelope(inst: Instrument, gate: float, t: np.ndarray) -> np.ndarray:
    """ADSR at times ``t`` (seconds from note-on); the release starts at ``gate``."""
    a, d = max(inst.attack, 1e-4), max(inst.decay, 1e-4)
    points, levels = [0.0, a, a + d], [0.0, 1.0, inst.sustain]
    env = np.interp(t, points, levels)
    after = t >= gate
    if after.any():
        held = np.interp(gate, points, levels)
        x = (t[after] - gate) / inst.release
        env[after] = held * (np.maximum(0.0, 1 - x) if inst.linear_release else np.exp(-6.9 * x))
    return env


# Pitched drums: (resting Hz, sweep Hz, sweep time, decay time).
_DRUM_BODIES = {KICK: (48, 120, 0.025, 0.16), LOW_TOM: (72, 70, 0.05, 0.28), MID_TOM: (98, 80, 0.045, 0.22)}
# Darksynth kit: sine bodies, swept harder and longer, then saturated.
_DARK_BODIES = {KICK: (50, 180, 0.018, 0.22), LOW_TOM: (85, 70, 0.04, 0.38), MID_TOM: (125, 90, 0.035, 0.3)}
_GATE = 0.22  # seconds the snare's gated reverb holds before it slams shut


def drum(pitch: int, velocity: int, sample_rate: int, kit: str = "nes") -> np.ndarray:
    return _drum_shape(pitch, kit, sample_rate) * (velocity / 127)


@functools.cache
def _drum_shape(pitch: int, kit: str, sample_rate: int) -> np.ndarray:
    """One hit at full velocity. Callers scale a copy, so the cached array is never written."""
    if kit == "darksynth":
        return _dark_drum(pitch, sample_rate)
    t = np.arange(int((2.5 if pitch == CRASH else 0.7) * sample_rate)) / sample_rate
    if pitch in _DRUM_BODIES:
        rest, sweep, sweep_time, decay = _DRUM_BODIES[pitch]
        freq = rest + sweep * np.exp(-t / sweep_time)
        body = oscillator("triangle", np.cumsum(freq) / sample_rate, freq / sample_rate)
        return body * np.exp(-t / decay) + 0.15 * noise(t, 8000) * np.exp(-t / 0.02)
    if pitch == SNARE:
        body = oscillator("triangle", 190 * t, np.full_like(t, 190 / sample_rate))
        return 0.7 * noise(t, 12000) * np.exp(-t / 0.08) + 0.5 * body * np.exp(-t / 0.04)
    if pitch == HAT:
        return 0.5 * noise(t, 30000, short=True) * np.exp(-t / 0.02)
    if pitch == CRASH:
        return 0.45 * noise(t, 26000) * np.exp(-t / 0.7)
    raise ValueError(f"no drum voice for MIDI note {pitch}")


def _filtered(x: np.ndarray, cutoff: float, kind: str, sample_rate: int) -> np.ndarray:
    return signal.sosfilt(signal.butter(2, min(cutoff, 0.45 * sample_rate), kind, fs=sample_rate, output="sos"), x)


def _dark_drum(pitch: int, sample_rate: int) -> np.ndarray:
    """80s darksynth kit: a hard clipped kick, big toms, and a snare with a gated reverb tail."""
    sr = sample_rate
    t = np.arange(int((2.0 if pitch == CRASH else 0.6) * sr)) / sr
    white = noise(t, sr)  # one LFSR step per sample: plain white noise
    if pitch in _DARK_BODIES:
        rest, sweep, sweep_time, decay = _DARK_BODIES[pitch]
        freq = rest + sweep * np.exp(-t / sweep_time)
        body = np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t / decay)
        click = _filtered(white, 3000, "highpass", sr) * np.exp(-t / 0.003)
        return np.tanh(3 * (body + 0.4 * click)) / np.tanh(3)
    if pitch == SNARE:
        body = (np.sin(2 * np.pi * 185 * t) + 0.6 * np.sin(2 * np.pi * 330 * t)) * np.exp(-t / 0.05)
        crack = _filtered(white, 1200, "highpass", sr) * np.exp(-t / 0.06)
        gate = np.interp(t, [0, 0.008, _GATE, _GATE + 0.015], [0, 1, 0.75, 0])  # holds, then slams shut
        tail = _filtered(_filtered(white, 400, "highpass", sr), 7000, "lowpass", sr) * gate
        return np.tanh(2 * (0.5 * body + 0.6 * crack + 0.35 * tail)) / np.tanh(2)
    if pitch == HAT:
        return 0.6 * _filtered(noise(t, sr, short=True), 7000, "highpass", sr) * np.exp(-t / 0.035)
    if pitch == CRASH:
        return 0.5 * _filtered(white, 4000, "highpass", sr) * np.exp(-t / 0.9)
    raise ValueError(f"no drum voice for MIDI note {pitch}")


def _pan(pan: float) -> np.ndarray:
    angle = (np.clip(pan, -1, 1) + 1) * np.pi / 4
    return np.array([np.cos(angle), np.sin(angle)])


def _unison(inst: Instrument) -> tuple[np.ndarray, np.ndarray, float]:
    """Per-voice detune (cents) and pan, and the gain of each voice."""
    if not inst.detune_cents:
        return np.zeros(1), np.full(1, inst.pan), 1.0
    spread = np.linspace(-1, 1, inst.voices)
    gain = 0.7 * (2 / inst.voices) ** 0.5  # equal power: a detuned pair plays at 0.7 each
    return spread * inst.detune_cents, inst.pan + spread * inst.spread, gain


class _Tone:
    """One note with all its unison voices, synthesised lazily for whichever block it overlaps.

    Signal path: voices -> per-note filter sweep -> drive -> + sub -> envelope.
    The filter's memory lives on the note, so blocks join seamlessly.
    """

    __slots__ = ("inst", "start", "end", "gate", "freqs", "gains", "sub_gains", "phase", "sample_rate", "_filter")

    def __init__(self, inst: Instrument, start: int, gate: int, freq: float, level: float, sample_rate: int):
        self.inst, self.start, self.gate, self.sample_rate = inst, start, gate, sample_rate
        self.end = start + gate + int(inst.release * sample_rate)
        cents, pans, gain = _unison(inst)
        self.freqs = freq * 2 ** (cents / 1200)
        self.gains = _pan(pans).T * (gain * level)  # (voices, 2)
        self.sub_gains = _pan(inst.pan) * level
        self.phase = np.zeros(len(cents))
        self._filter = effects.Lowpass(inst.note_lowpass, sample_rate) if inst.note_lowpass else None

    def add_to(self, out: np.ndarray, block_start: int) -> None:
        lo, hi = max(block_start, self.start), min(block_start + len(out), self.end)
        if hi <= lo:
            return
        sr, inst = self.sample_rate, self.inst
        n = np.arange(lo, hi)
        t = (n - self.start) / sr
        step = self.freqs / sr  # cycles per sample, per voice
        if inst.vibrato_depth:
            fade_in = np.clip((t - inst.vibrato_delay) / 0.3, 0, 1)
            bend = 2 ** (inst.vibrato_depth * fade_in * np.sin(2 * np.pi * inst.vibrato_rate * t) / 12)
            dt = bend[:, None] * step
            phase = self.phase + np.cumsum(dt, axis=0) - dt
            self.phase = phase[-1] + dt[-1]
        else:
            # Phase from absolute time: two notes of one pitch stay in phase, so
            # a drone handing over to an identical drone crossfades without beating.
            dt = np.broadcast_to(step, (hi - lo, len(step)))
            phase = n[:, None] * step
        y = oscillator(inst.wave, phase, dt, inst.duty) @ self.gains
        if self._filter is not None:
            closed = self._filter(y)
            y = closed + np.exp(-t / inst.pluck)[:, None] * (y - closed) if inst.pluck else closed
        if inst.drive:
            y = np.tanh(inst.drive * y) / np.tanh(inst.drive)
        if inst.sub:
            y += inst.sub * np.sin(2 * np.pi * phase.mean(axis=1))[:, None] * self.sub_gains
        out[lo - block_start : hi - block_start] += y * envelope(inst, self.gate / sr, t)[:, None]


class _Sample:
    """A pre-rendered voice (drum hits)."""

    __slots__ = ("inst", "start", "end", "samples")

    def __init__(self, inst: Instrument, start: int, mono: np.ndarray, gains: np.ndarray):
        self.inst, self.start, self.end = inst, start, start + len(mono)
        self.samples = mono[:, None] * gains

    def add_to(self, out: np.ndarray, block_start: int) -> None:
        lo, hi = max(block_start, self.start), min(block_start + len(out), self.end)
        if hi > lo:
            out[lo - block_start : hi - block_start] += self.samples[lo - self.start : hi - self.start]


class Renderer:
    """Turns scheduled note events into a continuous stereo mix, block by block.

    Each instrument mixes through its own bus (filter, gain, sends), so after a
    style change the old style's notes ring out through their own sound while
    new notes use the new one.
    """

    def __init__(self, style: str = "normal", sample_rate: int = SAMPLE_RATE, seed: int = 0, use_effects: bool = True):
        self.sample_rate = sample_rate
        self.use_effects = use_effects
        self.set_style(style)
        self.now = 0  # index of the next sample render() produces
        self.seconds_per_beat = 0.5
        self._voices: list[_Tone | _Sample] = []
        self._buses: dict[Instrument, effects.Lowpass | None] = {}  # every instrument used so far, with its filter
        self._kicks: list[tuple[int, float]] = []  # (start sample, recovery in samples) of pump triggers
        if use_effects:
            self._echo = effects.PingPongEcho(ECHO_FEEDBACK)
            self._echo_filter = effects.Lowpass(ECHO_LOWPASS, sample_rate)
            self._reverb = effects.Reverb(effects.impulse_response(sample_rate, seed))

    def set_style(self, style: str, intensity: float = 0.5) -> None:
        """Instruments and mastering for whatever is scheduled from now on: a style, or any name in ``SOUNDS``.

        ``intensity`` (0-1) pushes a combat set harder or eases it off; calm sets ignore it.
        """
        self.style = style
        self.intensity = 0.5 if style in _CALM_SOUNDS else intensity
        self.instruments = _sound(style, self.intensity)

    def set_tempo(self, bpm: float) -> None:
        self.seconds_per_beat = 60.0 / bpm
        if self.use_effects:
            self._echo.delay = round(0.75 * self.seconds_per_beat * self.sample_rate)  # dotted eighth

    def schedule(self, events: list[NoteEvent] | tuple[NoteEvent, ...]) -> None:
        """Queue events whose times are beats from the current position."""
        sr, spb = self.sample_rate, self.seconds_per_beat
        for e in events:
            inst = self.instruments[e.track]
            if inst not in self._buses:
                self._buses[inst] = effects.Lowpass(inst.lowpass, sr) if inst.lowpass else None
            start = self.now + round(e.start * spb * sr)
            if inst.wave == "drums":
                self._voices.append(_Sample(inst, start, drum(e.pitch, e.velocity, sr, inst.kit), _pan(inst.pan)))
                if e.pitch in _PUMP_DRUMS and e.start % 1 == 0:
                    self._kicks.append((start, _PUMP_RECOVERY * spb * sr))
                continue
            gate = round(e.duration * spb * sr)
            self._voices.append(_Tone(inst, start, gate, midi_to_freq(e.pitch), e.velocity / 127, sr))

    def _pump(self, n: int) -> np.ndarray:
        """Sidechain envelope for the next ``n`` samples: 1 just after each trigger, swelling back to 0."""
        env = np.zeros(n)
        attack = _PUMP_ATTACK * self.sample_rate
        for kick, recovery in self._kicks:
            lo, hi = max(kick - self.now, 0), min(kick - self.now + int(10 * recovery), n)
            if lo < hi:
                since = np.arange(lo, hi) + (self.now - kick)
                env[lo:hi] = np.maximum(env[lo:hi], np.minimum(1, since / attack) * np.exp(-since / recovery))
        self._kicks = [(k, r) for k, r in self._kicks if k + int(10 * r) > self.now + n]
        return env

    def render(self, n: int) -> np.ndarray:
        """The next ``n`` samples of the raw (unmastered) mix, shape (n, 2)."""
        buses = {inst: np.zeros((n, 2)) for inst in self._buses}
        for voice in self._voices:
            voice.add_to(buses[voice.inst], self.now)
        pump = self._pump(n)
        self.now += n
        self._voices = [v for v in self._voices if v.end > self.now]

        mix = np.zeros((n, 2))
        reverb_send = np.zeros(n)
        echo_send = np.zeros(n)
        for inst, track in buses.items():
            lowpass = self._buses[inst]
            if lowpass is not None:
                track = lowpass(track)
            track *= inst.gain
            if inst.duck:
                track *= 1 - inst.duck * pump[:, None]
            mix += track
            reverb_send += track.sum(axis=1) * inst.reverb
            echo_send += track.sum(axis=1) * inst.echo
        if self.use_effects:
            echo = self._echo_filter(self._echo(echo_send))
            reverb_send += echo.sum(axis=1) * 0.3
            mix += echo + REVERB_LEVEL * self._reverb(reverb_send)
        return mix

    def play(self, events: tuple[NoteEvent, ...], beats: float) -> np.ndarray:
        """Schedule ``events`` from the current position and render ``beats`` of mastered audio."""
        self.schedule(events)
        return self.master(self.render(round(beats * self.seconds_per_beat * self.sample_rate)))

    def master(self, mix: np.ndarray) -> np.ndarray:
        """Stateless soft limiter for streams: the same curve on every block, peaks stay under 1."""
        # A fight that matters sits a little forward (about 1.5 dB at 1), an easy one a little back.
        drive = _DRIVE[self.style]
        reference = _STREAM_REFERENCE[self.style] * (1 - 0.3 * (self.intensity - 0.5))
        return np.tanh(drive * mix / reference) * (0.89 / np.tanh(drive))


def render(comp: Composition, sample_rate: int = SAMPLE_RATE, use_effects: bool = True, tail: float = 4.5) -> np.ndarray:
    """A whole fixed piece plus its reverb tail, peak-normalised. Shape (samples, 2)."""
    renderer = Renderer(comp.sound, sample_rate, comp.seed, use_effects)
    renderer.set_style(comp.sound, comp.intensity)
    renderer.set_tempo(comp.tempo)
    renderer.schedule(comp.events)
    mix = renderer.render(int((comp.length_beats * comp.seconds_per_beat + tail) * sample_rate))
    return _master(mix, sample_rate, _DRIVE[comp.sound])


def _master(mix: np.ndarray, sample_rate: int, drive: float) -> np.ndarray:
    fade = sample_rate
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = np.abs(mix).max()
    if peak == 0:
        return mix
    mix = np.tanh(drive * mix / peak)
    return mix * (0.89 / np.abs(mix).max())


def write_wav(path: Path, audio: np.ndarray | Iterable[np.ndarray], sample_rate: int = SAMPLE_RATE) -> None:
    """Write stereo audio, given whole or as an iterable of blocks written as they arrive."""
    blocks = [audio] if isinstance(audio, np.ndarray) else audio
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        for block in blocks:
            f.writeframes((np.clip(block, -1, 1) * 32767).astype("<i2").tobytes())
