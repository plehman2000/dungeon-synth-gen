import pytest

from dungeon_synth_gen.arrange import STYLES, compose
from dungeon_synth_gen.parts import DRUM_PATTERNS, FLAVOURS


def test_same_seed_same_piece():
    assert compose(42).events == compose(42).events


def test_different_seeds_differ():
    assert compose(1).events != compose(2).events


def test_override_changes_only_that_parameter():
    base, slower = compose(5), compose(5, tempo=50)
    assert slower.tempo == 50
    assert slower.events == base.events


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("seed", range(25))
def test_structure(seed, style):
    comp = compose(seed, style=style)
    assert comp.bars == 16
    assert [s.name for s in comp.sections] == ["intro", "theme", "contrast", "return"]
    for e in comp.events:
        assert 0 <= e.start < comp.length_beats
        assert e.start + e.duration <= comp.length_beats + 1e-9
        assert 1 <= e.velocity <= 127
    for track in ("lead", "arp", "bass", "drone"):
        assert all(comp.scale.contains(e.pitch) for e in comp.track(track)), track


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("seed", range(25))
def test_lead_enters_after_intro_and_ends_on_tonic(seed, style):
    comp = compose(seed, style=style)
    lead = comp.track("lead")
    assert min(e.start for e in lead) >= 16
    last = max(lead, key=lambda e: e.start)
    assert last.pitch % 12 == comp.scale.root
    assert comp.sections[-1].chords[-1] == 0


def test_contrast_section_has_drums():
    comp = compose(9)
    contrast = [e for e in comp.track("perc") if 32 <= e.start < 48]
    assert len(contrast) >= 4 * min(len(p) for p in DRUM_PATTERNS.values())


@pytest.mark.parametrize("seed", range(10))
def test_combat_is_the_same_piece_at_its_flavours_tempo(seed):
    normal, combat = compose(seed), compose(seed, style="combat")
    assert combat.tempo == FLAVOURS[combat.sound].tempo * normal.tempo
    assert (combat.scale, combat.sections) == (normal.scale, normal.sections)
    assert combat.track("lead") == normal.track("lead")
    assert combat.track("bass") != normal.track("bass")


@pytest.mark.parametrize("seed", range(10))
def test_combat_drums_every_section_and_hit_harder(seed):
    normal, combat = compose(seed), compose(seed, style="combat")
    perc = combat.track("perc")
    assert all(any(s.start_bar * 4 <= e.start < s.start_bar * 4 + 16 for e in perc) for s in combat.sections)
    assert len(perc) > 1.5 * len(normal.track("perc"))


def test_tempo_override_wins_in_combat():
    assert compose(5, style="combat", tempo=150).tempo == 150


def test_unknown_style_is_rejected():
    with pytest.raises(ValueError):
        compose(1, style="stealth")
