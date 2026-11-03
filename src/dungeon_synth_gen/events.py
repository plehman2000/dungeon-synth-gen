"""MIDI-style note events: the hand-off between composition and synthesis."""

from __future__ import annotations

from dataclasses import dataclass, field

from dungeon_synth_gen.theory import Scale

BEATS_PER_BAR = 4
TRACKS = ("lead", "counter", "arp", "bass", "drone", "perc")


@dataclass(frozen=True, slots=True)
class NoteEvent:
    track: str
    start: float  # beats from the start of the piece
    duration: float  # beats
    pitch: int  # MIDI note number; GM drum map on the perc track
    velocity: int  # 1-127


@dataclass(frozen=True)
class Section:
    name: str  # "intro" | "theme" | "variation" | "contrast" | "development" | "return"
    start_bar: int
    chords: tuple[int, ...]  # one chord root (scale degree) per bar


@dataclass(frozen=True)
class Phrase:
    """One section of an endless stream; event times are beats from the phrase start."""

    index: int
    region: int  # key region: a run of phrases sharing a key and palette
    section: Section
    scale: Scale
    tempo: float
    style: str
    events: tuple[NoteEvent, ...]
    # The same phrase in the other gear (combat, or normal for a combat region):
    # same chords and lead, its own groove and tempo, so playback can jump across.
    twin: Phrase | None = None
    sound: str = ""  # synth set (synth.SOUNDS); empty means the style's own
    intensity: float = 0.5  # 0-1: how hard the fight is, for the synths (synth.Renderer.set_style)

    @property
    def voice(self) -> str:
        return self.sound or self.style

    @property
    def beats(self) -> float:
        return len(self.section.chords) * BEATS_PER_BAR

    def describe(self) -> str:
        chords = "  ".join(f"{self.scale.roman(c):<5}" for c in self.section.chords)
        return f"{self.voice:<10} {self.scale.name:<16} {self.tempo:>3g} BPM  {self.section.name:<12} {chords}"


@dataclass
class Composition:
    seed: int
    tempo: float
    scale: Scale
    sections: list[Section]
    style: str = "normal"  # "normal" | "combat" | "ambient"
    events: list[NoteEvent] = field(default_factory=list)
    sound: str = ""  # synth set (synth.SOUNDS); set by compose
    intensity: float = 0.5

    def __post_init__(self):
        self.sound = self.sound or self.style

    @property
    def bars(self) -> int:
        return sum(len(s.chords) for s in self.sections)

    @property
    def length_beats(self) -> float:
        return self.bars * BEATS_PER_BAR

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.tempo

    def track(self, name: str) -> list[NoteEvent]:
        return [e for e in self.events if e.track == name]

    def describe(self) -> str:
        lines = [f"seed {self.seed}  |  {self.style}  |  {self.scale.name}  |  {self.tempo:g} BPM  |  {self.bars} bars"]
        for s in self.sections:
            chords = "  ".join(f"{self.scale.roman(c):<5}" for c in s.chords)
            lines.append(f"  bars {s.start_bar + 1:>2}-{s.start_bar + len(s.chords):<2} {s.name:<9} {chords}")
        return "\n".join(lines)
