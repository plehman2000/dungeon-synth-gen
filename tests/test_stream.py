import itertools
import sys
import types
import wave
from dataclasses import replace

import numpy as np
import pytest

from dungeon_synth_gen import stream
from dungeon_synth_gen.arrange import STYLE_WEIGHTS, compose, seeded_style
from dungeon_synth_gen.endless import endless
from dungeon_synth_gen.synth import Renderer
from dungeon_synth_gen.theory import Scale

RATE = 8000


def take(seed, n, **options):
    return list(itertools.islice(endless(seed, **options), n))


def test_endless_is_deterministic():
    assert take(3, 15) == take(3, 15)


def test_phrases_are_contiguous_and_fit_their_scale():
    phrases = take(4, 40)
    for prev, nxt in zip(phrases, phrases[1:]):
        assert nxt.index == prev.index + 1
        assert nxt.section.start_bar == prev.section.start_bar + len(prev.section.chords)
    for p in phrases:
        for e in p.events:
            assert 0 <= e.start < p.beats
            if e.track != "drone":
                assert e.start + e.duration <= p.beats + 1e-9
            if e.track != "perc":
                assert p.scale.contains(e.pitch)


def test_key_changes_keep_the_old_tonic_and_bring_a_new_drone():
    phrases = take(8, 60)
    changes = [(a, b) for a, b in zip(phrases, phrases[1:]) if b.region != a.region]
    assert len(changes) >= 6
    for before, after in changes:
        assert after.scale.contains(before.scale.root)
        assert any(e.track == "drone" for e in after.events)


def test_unpinned_streams_mix_styles_but_never_pick_combat():
    styles = {p.style for seed in range(6) for p in take(seed, 40)}
    assert styles == set(STYLE_WEIGHTS)


def test_seeded_style_reaches_every_random_style():
    assert {seeded_style(s) for s in range(40)} == set(STYLE_WEIGHTS)


def test_ambient_has_no_drums_and_uses_its_own_modes():
    for p in take(5, 20, style="ambient"):
        assert not any(e.track == "perc" for e in p.events)
        assert p.scale.mode in ("ionian", "lydian", "mixolydian", "dorian")


def test_pins_hold_for_the_whole_stream():
    for p in take(6, 25, style="normal", mode="dorian", root=2, tempo=70):
        assert (p.style, p.scale, p.tempo) == ("normal", Scale(2, "dorian"), 70)


def test_combat_stream_stays_in_double_time():
    assert all(120 <= p.tempo <= 168 for p in take(6, 25, style="combat"))


@pytest.mark.parametrize("style", ["normal", "ambient", "combat"])
def test_renderer_output_does_not_depend_on_block_size(style):
    comp = compose(2, style=style)

    def run(blocks):
        r = Renderer(style, sample_rate=RATE, seed=2)
        r.set_tempo(comp.tempo)
        r.schedule(comp.events)
        return np.concatenate([r.render(n) for n in blocks])

    total = int(comp.length_beats * comp.seconds_per_beat * RATE)
    piece = total // 7
    assert np.allclose(run([total]), run([piece] * 7 + [total - 7 * piece]), atol=1e-9)


def test_stream_never_goes_silent():
    audio = np.concatenate([a for _, a in itertools.islice(stream.continuous(7, sample_rate=RATE), 18)])
    window = RATE // 2
    body = audio[4 * RATE :]  # skip the opening drone fade-in
    frames = body[: len(body) // window * window].reshape(-1, window, 2)
    assert np.sqrt((frames**2).mean(axis=(1, 2))).min() > 10 ** (-40 / 20)
    assert np.abs(audio).max() < 1


def test_continuous_chunks_match_phrase_lengths():
    for phrase, audio in itertools.islice(stream.continuous(5, sample_rate=RATE), 6):
        assert len(audio) == round(phrase.beats * 60 / phrase.tempo * RATE)
        assert np.isfinite(audio).all()


def test_record_writes_whole_phrases_past_the_target(tmp_path):
    out = tmp_path / "long.wav"
    seconds = stream.record(out, 1, minutes=0.5, sample_rate=RATE)
    with wave.open(str(out)) as f:
        assert f.getnframes() == round(seconds * RATE)
    assert 30 <= seconds < 30 + 20


def test_play_streams_phrases_until_interrupted(monkeypatch):
    written = []

    class FakeStream:
        def __init__(self, **kwargs):
            assert kwargs["channels"] == 2

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, block):
            assert block.dtype == np.float32
            written.append(len(block))
            if len(written) == 3:
                raise KeyboardInterrupt

    phrase = take(5, 1)[0]
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    monkeypatch.setattr(stream, "continuous", lambda seed, **kw: iter([(phrase, np.zeros((50_000, 2)))]))
    announced = []
    with pytest.raises(KeyboardInterrupt):
        stream.play(5, on_phrase=lambda p, t: announced.append((p.index, t)))
    assert announced == [(0, 0.0)]
    assert written == [int(stream._BLOCK * stream.SAMPLE_RATE)] * 3


# Switching between normal and combat mid-stream


@pytest.mark.parametrize("style", [None, "normal", "combat"])
def test_twins_leave_the_stream_unchanged_and_share_its_lead(style):
    paired = take(11, 12, style=style, twins=True)
    assert [replace(p, twin=None) for p in paired] == take(11, 12, style=style)
    for p in paired:
        t = p.twin
        assert t.style == ("normal" if p.style == "combat" else "combat")
        assert (t.section, t.scale, t.beats) == (p.section, p.scale, p.beats)
        assert t.tempo == p.tempo * (0.5 if p.style == "combat" else 2)
        assert [e for e in t.events if e.track == "lead"] == [e for e in p.events if e.track == "lead"]
        assert any(e.track == "drone" for e in t.events) == any(e.track == "drone" for e in p.events)


def fake_twinned(n_phrases=3, length=800):
    """Real phrases; their own version plays a 100 Hz sine, the combat twin (half as long) a 50 Hz cosine."""
    for p in take(8, n_phrases, style="normal", twins=True):
        t = np.arange(length) / RATE
        own = np.repeat(np.sin(2 * np.pi * 100 * t)[:, None], 2, axis=1)
        tc = np.arange(length // 2) / RATE
        twin = np.repeat(0.5 * np.cos(2 * np.pi * 50 * tc)[:, None], 2, axis=1)
        yield p, own, twin


def test_cursor_switch_keeps_the_beat_and_crossfades_without_a_click():
    switch = stream.CombatSwitch()
    cursor = stream.Cursor(fake_twinned(), switch, RATE)
    before = cursor.read(300)  # 300 of 800 samples: 37.5% into the phrase
    switch.toggle()
    after = cursor.read(500)
    assert cursor.phrase.style == "combat"
    fade = int(stream._CROSSFADE * RATE)
    # Lands 37.5% into the 400-sample twin, then plays it untouched once the fade is done.
    twin = next(fake_twinned())[2]
    assert np.allclose(after[fade:100], twin[150 + fade : 250])
    joined = np.concatenate([before, after])[:, 0]
    assert np.abs(np.diff(joined[290:300 + fade + 10])).max() < 0.1  # a hard cut here would jump by ~0.9


def test_cursor_crosses_phrases_in_whichever_version_plays():
    switch = stream.CombatSwitch(on=True)
    cursor = stream.Cursor(fake_twinned(), switch, RATE)
    first = cursor.phrase
    assert len(cursor.read(400)) == 400  # exactly one twin phrase
    assert cursor.phrase.index == first.index + 1 and cursor.remaining() == 400


def test_record_switches_at_the_given_times(tmp_path):
    shown = []
    out = tmp_path / "switching.wav"
    seconds = stream.record(
        out, 8, minutes=0.4, sample_rate=RATE, style="normal", switches=[5, 12],
        on_phrase=lambda p, t: shown.append((round(t, 3), p.style)),
    )
    assert (5.0, "combat") in shown and (12.0, "normal") in shown
    assert [s for _, s in shown[: shown.index((5.0, "combat"))]] == ["normal"] * shown.index((5.0, "combat"))
    with wave.open(str(out)) as f:
        assert f.getnframes() == round(seconds * RATE)
    assert seconds >= 24


def test_play_follows_the_switch(monkeypatch):
    written = []
    switch = stream.CombatSwitch()

    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, block):
            written.append(block.copy())
            if len(written) == 1:
                switch.toggle()
            if len(written) == 3:
                raise KeyboardInterrupt

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    monkeypatch.setattr(stream, "twinned", lambda seed, **kw: fake_twinned(length=100_000))
    with pytest.raises(KeyboardInterrupt):
        stream.play(5, switch=switch)
    calm, combat = next(fake_twinned(length=100_000))[1:]
    block = int(stream._BLOCK * stream.SAMPLE_RATE)
    assert np.allclose(written[0], calm[:block])
    assert np.allclose(written[2], combat[block // 2 + block : block // 2 + 2 * block])
