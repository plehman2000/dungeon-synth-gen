"""Continuous playback and recording of the endless stream.

One Renderer runs for the whole stream, so voices, filters, echo and reverb
carry across phrase boundaries: the output is one unbroken signal, produced
a phrase at a time.

A switchable stream renders every phrase twice, in its own style and as its
twin in the other gear (see ``endless``), on two renderers that both hear
every phrase. Flipping the ``CombatSwitch`` jumps playback to the same beat in
the other version, which is already rendered, so the change is instant.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import numpy as np

from dungeon_synth_gen.endless import endless
from dungeon_synth_gen.events import Phrase
from dungeon_synth_gen.synth import SAMPLE_RATE, Renderer, write_wav

_BLOCK = 0.1  # seconds per sound-card write: bounds how late Ctrl+C or a combat switch can land
_FADE_OUT = 3.0  # seconds, when a recording stops
_CROSSFADE = 0.03  # seconds of equal-power crossfade when the switch flips

OnPhrase = Callable[[Phrase, float], None]  # (phrase, seconds since the stream began)
Twinned = tuple[Phrase, np.ndarray, np.ndarray]  # (phrase, its audio, its twin's audio)


class CombatSwitch:
    """Combat on or off for a switchable stream. Flip it from any thread; playback follows within one block."""

    def __init__(self, on: bool = False):
        self.on = on

    def toggle(self) -> bool:
        self.on = not self.on
        return self.on


def _play(renderer: Renderer, phrase: Phrase) -> np.ndarray:
    renderer.set_style(phrase.voice)
    renderer.set_tempo(phrase.tempo)
    return renderer.play(phrase.events, phrase.beats)


def continuous(seed: int, *, sample_rate: int = SAMPLE_RATE, use_effects: bool = True, **options) -> Iterator[tuple[Phrase, np.ndarray]]:
    """Every phrase of the stream with its audio, forever. ``options`` go to ``endless``."""
    renderer = Renderer(sample_rate=sample_rate, seed=seed, use_effects=use_effects)
    for phrase in endless(seed, **options):
        yield phrase, _play(renderer, phrase)


def twinned(seed: int, *, sample_rate: int = SAMPLE_RATE, use_effects: bool = True, **options) -> Iterator[Twinned]:
    """Every phrase with audio for both gears. Both renderers hear every phrase, so a switch lands in a live mix."""
    own = Renderer(sample_rate=sample_rate, seed=seed, use_effects=use_effects)
    twin = Renderer(sample_rate=sample_rate, seed=seed, use_effects=use_effects)
    for phrase in endless(seed, twins=True, **options):
        yield phrase, _play(own, phrase), _play(twin, phrase.twin)


class Cursor:
    """Reads a twinned stream as one signal, following a CombatSwitch.

    A phrase and its twin cover the same beats at different tempos, so a flip
    keeps the musical position: it jumps to the same fraction of the phrase in
    the other version and crossfades into it.
    """

    def __init__(self, items: Iterator[Twinned], switch: CombatSwitch, sample_rate: int = SAMPLE_RATE):
        self._items, self._switch = items, switch
        self._pairs: deque[Twinned] = deque()
        ramp = np.linspace(0, np.pi / 2, max(1, int(_CROSSFADE * sample_rate)))[:, None]
        self._ramp_out, self._ramp_in = np.cos(ramp), np.sin(ramp)
        self._fading: tuple[np.ndarray, np.ndarray] | None = None  # (old version's faded-out tail, fade-in gains) still to mix in
        self.combat = switch.on
        self._offset = 0  # samples into the current phrase's playing version

    def _pair(self, i: int) -> Twinned:
        while i >= len(self._pairs):
            self._pairs.append(next(self._items))
        return self._pairs[i]

    def _version(self, i: int, combat: bool) -> tuple[Phrase, np.ndarray]:
        phrase, own, twin = self._pair(i)
        return (phrase, own) if (phrase.style == "combat") == combat else (phrase.twin, twin)

    def _follow(self) -> None:
        """Adopt the switch: jump to the same point in the other version and queue a crossfade from this one."""
        if self._switch.on == self.combat:
            return
        old = len(self._version(0, self.combat)[1])
        tail, _, _ = self._read(self.combat, self._offset, len(self._ramp_out))
        self._fading = (tail * self._ramp_out, self._ramp_in)
        self.combat = not self.combat
        new = len(self._version(0, self.combat)[1])
        self._offset = min(round(self._offset / old * new), new - 1)

    @property
    def phrase(self) -> Phrase:
        """The phrase about to play, in the version the switch asks for."""
        self._follow()
        return self._version(0, self.combat)[0]

    def remaining(self) -> int:
        """Samples left in the current phrase, in the version the switch asks for."""
        self._follow()
        return len(self._version(0, self.combat)[1]) - self._offset

    def _read(self, combat: bool, offset: int, n: int) -> tuple[np.ndarray, int, int]:
        """``n`` samples of one version from ``offset`` in the current phrase: (audio, phrases crossed, new offset)."""
        chunks, i = [], 0
        while n > 0:
            audio = self._version(i, combat)[1]
            chunk = audio[offset : offset + n]
            chunks.append(chunk)
            n -= len(chunk)
            offset += len(chunk)
            if offset >= len(audio):
                i, offset = i + 1, 0
        return np.concatenate(chunks), i, offset

    def read(self, n: int) -> np.ndarray:
        """The next ``n`` samples (n > 0), switching versions first if the switch has flipped."""
        self._follow()
        out, crossed, self._offset = self._read(self.combat, self._offset, n)
        if self._fading is not None:
            tail, gains = self._fading
            f = min(len(tail), n)
            out[:f] = tail[:f] + out[:f] * gains[:f]
            self._fading = (tail[f:], gains[f:]) if f < len(tail) else None
        for _ in range(crossed):
            self._pairs.popleft()
        return out


def record(
    path: Path, seed: int, minutes: float, *, sample_rate: int = SAMPLE_RATE, use_effects: bool = True,
    on_phrase: OnPhrase | None = None, switches: Sequence[float] = (), **options,
) -> float:
    """Write the first ``minutes`` of the stream to a WAV (whole phrases, faded out). Returns seconds written.

    ``switches`` are times in seconds at which combat toggles, starting from off
    (or on, for a combat stream).
    """
    if switches:
        return _record_switching(path, seed, minutes, sample_rate, use_effects, on_phrase, switches, options)
    target = minutes * 60 * sample_rate
    written = 0

    def blocks() -> Iterator[np.ndarray]:
        nonlocal written
        for phrase, audio in continuous(seed, sample_rate=sample_rate, use_effects=use_effects, **options):
            if on_phrase:
                on_phrase(phrase, written / sample_rate)
            written += len(audio)
            if written >= target:
                yield _faded(audio, sample_rate)
                return
            yield audio

    write_wav(path, blocks(), sample_rate)
    return written / sample_rate


def _faded(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    fade = min(len(audio), int(_FADE_OUT * sample_rate))
    audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return audio


def _record_switching(
    path: Path, seed: int, minutes: float, sample_rate: int, use_effects: bool, on_phrase: OnPhrase | None,
    switches: Sequence[float], options: dict,
) -> float:
    switch = CombatSwitch(on=options.get("style") == "combat")
    cursor = Cursor(twinned(seed, sample_rate=sample_rate, use_effects=use_effects, **options), switch, sample_rate)
    flips = deque(round(s * sample_rate) for s in sorted(switches))
    target = minutes * 60 * sample_rate
    written = 0

    def blocks() -> Iterator[np.ndarray]:
        nonlocal written
        shown = None
        while True:
            while flips and flips[0] <= written:
                flips.popleft()
                switch.toggle()
            if on_phrase and cursor.phrase is not shown:
                shown = cursor.phrase
                on_phrase(shown, written / sample_rate)
            # Read up to the end of the phrase or the next flip, whichever comes first.
            n = rest = cursor.remaining()
            if flips:
                n = min(n, flips[0] - written)
            last = written + n >= target and n == rest
            audio = cursor.read(n)
            written += len(audio)
            if last:
                yield _faded(audio, sample_rate)
                return
            yield audio

    write_wav(path, blocks(), sample_rate)
    return written / sample_rate


def play(
    seed: int, *, use_effects: bool = True, on_phrase: OnPhrase | None = None, switch: CombatSwitch | None = None,
    **options,
) -> None:
    """Play the stream until Ctrl+C, rendering up to two phrases ahead of the sound card.

    With a ``switch``, both versions of every phrase are rendered ahead, and
    flipping it changes gear within one block.
    """
    import sounddevice as sd

    ahead: queue.Queue = queue.Queue(maxsize=2)

    def render_ahead() -> None:
        try:
            source = twinned if switch is not None else continuous
            for item in source(seed, use_effects=use_effects, **options):
                ahead.put(item)
        except BaseException as exc:  # surface render errors in the playing thread
            ahead.put(exc)

    def take():
        while True:
            try:
                item = ahead.get(timeout=0.5)  # a timeout keeps Ctrl+C responsive while waiting
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                raise item
            return item

    threading.Thread(target=render_ahead, daemon=True).start()
    block = int(_BLOCK * SAMPLE_RATE)
    played = 0
    with sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype="float32", latency="high") as out:
        if switch is None:
            while True:
                phrase, audio = take()
                if on_phrase:
                    on_phrase(phrase, played / SAMPLE_RATE)
                audio = audio.astype(np.float32)
                for start in range(0, len(audio), block):
                    out.write(audio[start : start + block])
                played += len(audio)
        cursor = Cursor(iter(take, None), switch)
        shown = None
        while True:
            if on_phrase and cursor.phrase is not shown:
                shown = cursor.phrase
                on_phrase(shown, played / SAMPLE_RATE)
            audio = cursor.read(min(block, cursor.remaining()))  # stop at phrase ends, so announcements land on time
            out.write(audio.astype(np.float32))
            played += len(audio)
