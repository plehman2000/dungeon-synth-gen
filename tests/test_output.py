import wave

import numpy as np
import pytest

from dungeon_synth_gen.arrange import STYLES, compose
from dungeon_synth_gen.cli import main
from dungeon_synth_gen.events import TRACKS
from dungeon_synth_gen.midi import to_midi
from dungeon_synth_gen.synth import SAMPLE_RATE, render


def test_midi_has_header_and_one_track_per_part():
    comp = compose(3, style="combat")  # every combat flavour has drums
    data = to_midi(comp)
    parts = 1 + sum(1 for name in TRACKS if comp.track(name))  # conductor + every part that plays
    assert data[:4] == b"MThd"
    assert int.from_bytes(data[10:12], "big") == parts >= 6
    assert data.count(b"MTrk") == parts


@pytest.mark.parametrize("style", STYLES)
def test_render_is_deterministic_and_in_range(style):
    comp = compose(11, style=style)
    a = render(comp, sample_rate=8000)
    b = render(comp, sample_rate=8000)
    assert np.array_equal(a, b)
    assert a.shape[1] == 2
    assert np.isfinite(a).all()
    assert 0.5 < np.abs(a).max() <= 0.9


def test_cli_writes_wav_and_midi(tmp_path):
    out = tmp_path / "piece.wav"
    assert main(["--seed", "4", "-o", str(out), "--midi", "--dry"]) == 0
    with wave.open(str(out)) as f:
        assert f.getnchannels() == 2 and f.getframerate() == SAMPLE_RATE
        assert f.getnframes() / SAMPLE_RATE > 30
    assert out.with_suffix(".mid").read_bytes()[:4] == b"MThd"


def test_cli_combat_style(tmp_path):
    out = tmp_path / "fight.wav"
    assert main(["--seed", "4", "--style", "combat", "-o", str(out), "--dry"]) == 0
    with wave.open(str(out)) as f:
        assert f.getnframes() / SAMPLE_RATE > 20
