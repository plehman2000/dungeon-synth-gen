"""Streaming effects: lowpass, ping-pong echo, and convolution reverb.

Each effect keeps its state between calls (filter memory, echo history,
reverb tail), so audio can be processed in blocks of any size and the joined
output matches processing it all at once.
"""

from __future__ import annotations

import numpy as np
from scipy import signal


class Lowpass:
    """Butterworth lowpass over (samples, channels) blocks."""

    def __init__(self, cutoff: float, sample_rate: int, order: int = 2, channels: int = 2):
        self._sos = signal.butter(order, min(cutoff, 0.45 * sample_rate), fs=sample_rate, output="sos")
        self._state = np.zeros((self._sos.shape[0], 2, channels))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self._state = signal.sosfilt(self._sos, x, axis=0, zi=self._state)
        return y


class PingPongEcho:
    """Echoes of a mono send that bounce right, left, right... Returns only the repeats."""

    def __init__(self, feedback: float, delay: int = 1):
        self.feedback = feedback
        self.delay = delay  # samples; may change between blocks
        self._history = np.zeros((0, 2))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        d = self.delay
        history = self._history[-d:]
        y = np.zeros((d + len(x), 2))
        y[d - len(history) : d] = history
        y[d:, 0] = x
        # Each delay-long block depends only on the block before it.
        for start in range(d, len(y), d):
            end = min(start + d, len(y))
            y[start:end] += self.feedback * y[start - d : end - d, ::-1]
        self._history = y[-d:].copy()
        wet = y[d:]
        wet[:, 0] -= x
        return wet


class Reverb:
    """Mono in, stereo out convolution, overlap-adding each block's tail into the next."""

    def __init__(self, impulse: np.ndarray):
        self._impulse = impulse
        self._tail = np.zeros((0, 2))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        wet = signal.fftconvolve(x[:, None], self._impulse, axes=0)
        wet[: len(self._tail)] += self._tail
        self._tail = wet[len(x) :]
        return wet[: len(x)]


def impulse_response(sample_rate: int, seed: int, seconds: float = 3.2, rt60: float = 2.8, predelay: float = 0.03) -> np.ndarray:
    """Stereo IR for a large stone room: a dark long tail over a short bright one."""
    rng = np.random.default_rng(seed % 2**32)
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate
    long_tail = 10 ** (-3 * t / rt60)
    short_tail = 10 ** (-3 * t / (rt60 * 0.25))
    ir = np.zeros((int(predelay * sample_rate) + n, 2))
    for ch in range(2):
        noise = rng.standard_normal(n)
        dark = Lowpass(1800, sample_rate, channels=1)(noise[:, None])[:, 0]
        ir[-n:, ch] = dark * long_tail + 0.3 * noise * short_tail
        ir[:, ch] /= np.sqrt(np.sum(ir[:, ch] ** 2))
    return ir
