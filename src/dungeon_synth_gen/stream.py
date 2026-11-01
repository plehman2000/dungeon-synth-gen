"""Continuous playback and recording of the endless stream.

One Renderer runs for the whole stream, so voices, filters, echo and reverb
carry across phrase boundaries: the output is one unbroken signal, produced
a phrase at a time.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np

from dungeon_synth_gen.endless import endless
from dungeon_synth_gen.events import Phrase
from dungeon_synth_gen.synth import SAMPLE_RATE, Renderer, write_wav

_BLOCK = 0.25  # seconds per sound-card write, so Ctrl+C is handled promptly
_FADE_OUT = 3.0  # seconds, when a recording stops

OnPhrase = Callable[[Phrase, float], None]  # (phrase, seconds since the stream began)


def continuous(seed: int, *, sample_rate: int = SAMPLE_RATE, use_effects: bool = True, **options) -> Iterator[tuple[Phrase, np.ndarray]]:
    """Every phrase of the stream with its audio, forever. ``options`` go to ``endless``."""
    renderer = Renderer(sample_rate=sample_rate, seed=seed, use_effects=use_effects)
    for phrase in endless(seed, **options):
        renderer.set_style(phrase.style)
        renderer.set_tempo(phrase.tempo)
        yield phrase, renderer.play(phrase.events, phrase.beats)


def record(
    path: Path, seed: int, minutes: float, *, sample_rate: int = SAMPLE_RATE, use_effects: bool = True,
    on_phrase: OnPhrase | None = None, **options,
) -> float:
    """Write the first ``minutes`` of the stream to a WAV (whole phrases, faded out). Returns seconds written."""
    target = minutes * 60 * sample_rate
    written = 0

    def blocks() -> Iterator[np.ndarray]:
        nonlocal written
        for phrase, audio in continuous(seed, sample_rate=sample_rate, use_effects=use_effects, **options):
            if on_phrase:
                on_phrase(phrase, written / sample_rate)
            written += len(audio)
            if written >= target:
                fade = min(len(audio), int(_FADE_OUT * sample_rate))
                audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
                yield audio
                return
            yield audio

    write_wav(path, blocks(), sample_rate)
    return written / sample_rate


def play(seed: int, *, use_effects: bool = True, on_phrase: OnPhrase | None = None, **options) -> None:
    """Play the stream until Ctrl+C, rendering up to two phrases ahead of the sound card."""
    import sounddevice as sd

    ahead: queue.Queue[tuple[Phrase, np.ndarray] | BaseException] = queue.Queue(maxsize=2)

    def render_ahead() -> None:
        try:
            for item in continuous(seed, use_effects=use_effects, **options):
                ahead.put(item)
        except BaseException as exc:  # surface render errors in the playing thread
            ahead.put(exc)

    threading.Thread(target=render_ahead, daemon=True).start()
    block = int(_BLOCK * SAMPLE_RATE)
    played = 0
    with sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype="float32", latency="high") as out:
        while True:
            try:
                item = ahead.get(timeout=0.5)  # a timeout keeps Ctrl+C responsive while waiting
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                raise item
            phrase, audio = item
            if on_phrase:
                on_phrase(phrase, played / SAMPLE_RATE)
            audio = audio.astype(np.float32)
            for start in range(0, len(audio), block):
                out.write(audio[start : start + block])
            played += len(audio)
