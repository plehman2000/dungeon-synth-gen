# dungeon-synth-gen

Procedural 8-bit dungeon synth. Play one continuous, never-repeating stream, or render a reproducible 16-bar piece (about 60 s) per seed to WAV, with optional MIDI export.

```
uv sync
uv run dungeon-synth-gen --endless             # one continuous stream until Ctrl+C
uv run dungeon-synth-gen --minutes 10          # 10 min of that stream -> out/dungeon_endless_<seed>.wav
uv run dungeon-synth-gen --seed 42 --midi      # one 16-bar piece -> out/dungeon_42[_style].wav + .mid
uv run dungeon-synth-gen --mode phrygian --root D --tempo 64
uv run dungeon-synth-gen --seed 42 --style combat  # -> out/dungeon_42_combat.wav
uv run pytest
```

## Continuous stream

`--endless` does not chain separate songs. A single renderer runs for the whole stream, so notes, filters, echo and reverb carry across every boundary.

- Music comes in key regions of 4-6 phrases (about a minute each): intro, theme, variation, contrast, development, return.
- Each region draws a new palette. Its theme usually evolves from the previous region's theme through the variation engine.
- Keys move only to neighbours that contain the old tonic. The drone of one region crossfades into the next at constant level.
- With no `--style`, each region picks normal or ambient; combat is never chosen at random.
- `--style`, `--mode`, `--root` and `--tempo` pin that parameter for the whole stream.
- The same `--seed` always gives the same stream.

## Styles

| `--style` | Sound |
| --- | --- |
| `normal` | Slow dungeon crawl, 60-84 BPM, minor modes, drums only from the theme on |
| `ambient` | Calm and open: major-side modes (ionian, lydian, mixolydian, some dorian), a soft plucked triangle lead, slow ringing arpeggios, long notes and rests, no drums |
| `combat` | The same seed's chords and melody in double time (120-168 BPM), with eighth-note and gallop bass, backbeat snare and double kicks in every section, fills between sections, and darksynth voices: a five-saw supersaw lead, a distorted reese bass over a clean sub, driven saw plucks, and a wide pad, all pumping against the drums like a sidechained mix. An 80s darksynth kit: a hard clipped kick, big toms, and a snare with a gated reverb tail |

Without `--style`, a fixed piece's style is drawn from its seed: normal 2/3, ambient 1/3. Combat pieces are 16 bars like the others, so they run about 30 s.

## Pipeline

`Musical parameters → Composition generator → Note events → Synthesizer → Audio`

| Module | Role |
| --- | --- |
| `theory.py` | Minor and major-side modes, scale degrees, triads, Markov chord progressions, common-tone key changes |
| `variation.py` | Motif transforms: invert, retrograde, split, merge, neighbor tone |
| `melody.py` | Motifs realised over chords, sequenced, with cadences, one phrase at a time |
| `parts.py` | Drone, bassline, arpeggio, percussion (GM drum notes), per style and section role |
| `arrange.py` | Palettes (shared material per key region), phrase builder, the fixed 16-bar form |
| `endless.py` | Endless form: key regions, style and key changes, motif evolution |
| `synth.py` | PolyBLEP square/saw, 4-bit NES triangle, LFSR noise, ADSR, unison/drive/filter-sweep voices, sidechain pump, streaming renderer and mixer |
| `effects.py` | Stateful lowpass, ping-pong echo, overlap-add convolution reverb |
| `stream.py` | Live playback (renders ahead of the sound card) and long recordings |
| `midi.py` | Standard MIDI File export |

`--root` and `--tempo` transpose or retime the same seeded piece. `--mode` can also change its chords, since each mode allows different ones.
