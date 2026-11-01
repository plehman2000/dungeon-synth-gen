import random

import pytest

from dungeon_synth_gen import theory
from dungeon_synth_gen.theory import MODES, Scale


def test_pitch_wraps_octaves():
    d_minor = Scale(theory.parse_note("D"), "aeolian")
    assert [d_minor.pitch(d, 62) for d in range(8)] == [62, 64, 65, 67, 69, 70, 72, 74]
    assert d_minor.pitch(-1, 62) == 60


def test_parse_note_accepts_sharps_and_flats():
    assert theory.parse_note("f#") == theory.parse_note("Gb") == 6
    with pytest.raises(ValueError):
        theory.parse_note("H")


def test_triad_qualities():
    harmonic = Scale(2, "harmonic")
    assert harmonic.triad_quality(0) == "minor"
    assert harmonic.triad_quality(4) == "major"  # raised 7th gives a major V
    assert harmonic.triad_quality(2) == "aug"
    assert Scale(2, "aeolian").triad_quality(1) == "dim"
    assert Scale(2, "phrygian").roman(1) == "bII"


def test_nearest_chord_tone_is_always_one_step_away():
    for chord in range(7):
        for degree in range(-7, 14):
            tone = theory.nearest_chord_tone(degree, chord)
            assert theory.is_chord_tone(tone, chord)
            assert abs(tone - degree) <= 1


@pytest.mark.parametrize("mode", sorted(MODES))
def test_progressions_avoid_diminished_chords_and_meet_constraints(mode):
    scale = Scale(4, mode)
    rng = random.Random(0)
    for _ in range(50):
        theme = theory.theme_progression(rng, scale)
        contrast = theory.contrast_progression(rng, scale)
        assert theme[0] == 0 and theme[-1] != 0
        assert contrast[0] != 0 and contrast[-1] in theory.CADENCE_CHORDS[mode]
        assert all(scale.triad_quality(c) != "dim" for c in theme + contrast)
