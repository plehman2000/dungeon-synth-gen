import numpy as np

from dungeon_synth_gen.events import NoteEvent
from dungeon_synth_gen.parts import KICK, SNARE
from dungeon_synth_gen.synth import COMBAT_INSTRUMENTS, Renderer, _unison, drum

SR = 22050
BPM = 120
PAD = [NoteEvent("drone", 0, 4, 50, 100)]


def render(events, beats=4.0):
    r = Renderer("combat", SR, use_effects=False)
    r.set_tempo(BPM)
    r.schedule(events)
    return r.render(int(beats * 60 / BPM * SR))


def centroid(x: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(x[:, 0] * np.hanning(len(x))))
    return (spectrum * np.fft.rfftfreq(len(x), 1 / SR)).sum() / spectrum.sum()


def test_unison_spreads_voices_evenly_at_equal_power():
    lead = COMBAT_INSTRUMENTS["lead"]
    cents, pans, gain = _unison(lead)
    assert len(cents) == lead.voices == 5
    assert np.allclose(cents, -cents[::-1]) and cents.max() == lead.detune_cents
    assert np.isclose(len(cents) * gain**2, 2 * 0.7**2)  # same power as a detuned pair


def test_on_beat_kick_pumps_the_pad():
    kick = [NoteEvent("perc", 2, 0.25, KICK, 110)]
    pumped = render(PAD + kick) - render(kick)
    steady = render(PAD)
    beat = int(2 * 60 / BPM * SR)
    just_after = slice(beat + int(0.01 * SR), beat + int(0.03 * SR))
    assert np.abs(pumped[just_after]).max() < 0.5 * np.abs(steady[just_after]).max()
    assert np.allclose(pumped[:beat], steady[:beat])


def test_off_beat_kick_does_not_pump():
    kick = [NoteEvent("perc", 2.5, 0.25, KICK, 110)]
    assert np.allclose(render(PAD + kick) - render(kick), render(PAD))


def test_pluck_opens_bright_then_closes():
    note = render([NoteEvent("arp", 0, 1, 57, 110)], beats=1)
    early, late = note[int(0.005 * SR) : int(0.03 * SR)], note[int(0.3 * SR) : int(0.4 * SR)]
    assert centroid(early) > 1.5 * centroid(late)


def test_darksynth_snare_tail_holds_then_gates_shut():
    snare = drum(SNARE, 127, SR, "darksynth")
    rms = lambda a, b: np.sqrt((snare[int(a * SR) : int(b * SR)] ** 2).mean())
    assert rms(0.1, 0.2) > 0.2 * rms(0, 0.03)  # a reverb tail that holds up
    assert rms(0.26, 0.4) < 0.05 * rms(0.1, 0.2)  # then the gate slams shut (only the crack's own decay is left)


def test_drum_cache_is_per_kit_and_sample_rate():
    assert len(drum(KICK, 100, 8000)) != len(drum(KICK, 100, SR))
    assert not np.allclose(drum(KICK, 100, SR)[:500], drum(KICK, 100, SR, "darksynth")[:500])
    assert np.allclose(drum(KICK, 50, SR, "darksynth"), drum(KICK, 100, SR, "darksynth") / 2)
