import random

import pytest

from dungeon_synth_gen.melody import MOTIF_RHYTHMS, random_motif
from dungeon_synth_gen.variation import TRANSFORMS, Motif


def heights(m: Motif) -> list[int]:
    out = [0]
    for step in m.steps:
        out.append(out[-1] + step)
    return out


def test_motif_rhythms_fill_one_bar():
    assert all(sum(abs(d) for d in r) == 4 for r in MOTIF_RHYTHMS)


@pytest.mark.parametrize("name", sorted(TRANSFORMS))
def test_transforms_keep_bar_length_and_valid_steps(name):
    rng = random.Random(3)
    for _ in range(200):
        motif = random_motif(rng)
        varied = TRANSFORMS[name](motif, rng)  # Motif() validates the step count
        assert varied.length == motif.length == 4


def test_invert_mirrors_contour():
    m = Motif((1, 1, 1, 1), (1, 2, -1))
    assert heights(TRANSFORMS["invert"](m, random.Random())) == [0, -1, -3, -2]


def test_retrograde_reverses_contour():
    m = Motif((2, 1, 1), (2, -1))
    r = TRANSFORMS["retrograde"](m, random.Random())
    assert r.rhythm == (1, 1, 2)
    assert heights(r) == [h - heights(m)[-1] for h in reversed(heights(m))]


def test_split_and_merge_preserve_surrounding_notes():
    m = Motif((2, 1, 1), (2, -1))
    split = TRANSFORMS["split"](m, random.Random(1))
    assert split.note_count == 4
    assert heights(split)[-1] == heights(m)[-1]
    merged = TRANSFORMS["merge"](Motif((1, 1, 2), (1, 1)), random.Random(1))
    assert merged.rhythm == (2, 2) and merged.steps == (2,)
