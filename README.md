# dungeon-synth-gen

Procedural 8-bit dungeon synth. Play one continuous, never-repeating stream, or render a reproducible 16-bar piece (about 60 s) per seed to WAV, with optional MIDI export.

```
uv sync
uv run dungeon-synth-gen --endless             # one continuous stream until Ctrl+C
uv run dungeon-synth-gen --minutes 10          # 10 min of that stream -> out/dungeon_endless_<seed>.wav
uv run dungeon-synth-gen --seed 42 --midi      # one 16-bar piece -> out/dungeon_42[_style].wav + .mid
uv run dungeon-synth-gen --mode phrygian --root D --tempo 64
uv run dungeon-synth-gen --seed 42 --style combat  # -> out/dungeon_42_combat.wav
uv run dungeon-synth-gen --seed 42 --style combat --intensity 1  # the same fight, desperate
uv run dungeon-synth-gen --minutes 2 --switch-at 20,45  # combat on at 0:20, off at 0:45
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

## Switching to combat mid-stream

During `--endless`, press `c` to toggle combat on and off. The switch is instant, because the other version is already rendered:

- Every phrase is pregenerated twice: as written, and as its twin in the other gear (combat, or normal when the stream is pinned to combat).
- A twin keeps the phrase's chords, key and lead line, and swaps in that style's groove, synths and double (or half) tempo.
- Two renderers run side by side and both hear every phrase, so the idle version's reverb and drone are already sounding when you switch to it.
- A switch jumps to the same musical point in the other version, with a 30 ms crossfade, within 0.1 s of the keypress.

`--minutes` with `--switch-at 20,45,...` writes the same thing to a WAV, toggling at those seconds. From Python, pass a `stream.CombatSwitch` to `stream.play(seed, switch=...)` and call `.toggle()` (or set `.on`) from any thread, for example from a game loop.

## Styles

| `--style` | Sound |
| --- | --- |
| `normal` | Slow dungeon crawl, 60-84 BPM, minor modes, drums only from the theme on |
| `ambient` | Calm and open: major-side modes (ionian, lydian, mixolydian, some dorian), a soft plucked triangle lead, slow ringing arpeggios, long notes and rests, no drums |
| `combat` | The same seed's chords and melody, faster, with drums in every section and fills between them. Each key region draws one of five flavours (below), so a long fight keeps changing gear |

Without `--style`, a fixed piece's style is drawn from its seed: normal 2/3, ambient 1/3. Combat pieces are 16 bars like the others, so they run 25-40 s.

### Combat flavours

| Flavour | Tempo | Sound |
| --- | --- | --- |
| `brut` (most likely) | 2x | Carpenter Brut-style darksynth: sixteenth-note bass, octave bounces and climbing riffs on a distorted reese over a full sub, four on the floor with a gated snare, a seven-saw lead driven into fuzz, and a hard sidechain pump |
| `darksynth` | 2x | Eighth-note drive and gallop bass, a five-saw supersaw lead, driven saw plucks, backbeat and thrash drums |
| `industrial` | 1.75x | Clipped square waves, off-beat pulse bass answering the kick, sixteenth hats, an echoing needle arp |
| `doom` | 1.5x | Half-time snare, sludge bass that drops to the seventh, fuzz-pulse lead, a cavernous room |
| `chiptrash` | 2.25x | NES voices at a sprint: triangle gallop bass, thin pulse lead, d-beat and blast beats on the 8-bit kit |

Every section draws its bass and drum patterns from its flavour's pools, and each flavour has its own synths and mastering (`parts.FLAVOURS`, `synth.SOUNDS`).

Combat is never in a major key: it plays the calm music's chords and melody on the same tonic in phrygian or harmonic minor (`theory.darken`), and its arpeggios are power chords where the third only flickers past.

### Intensity

`--intensity 0-1` (or `intensity=` on `compose` / `endless`, which also takes a function read before every phrase, so a game can raise it as a fight gets harder) shapes combat only, in quarter steps:

| | 0 | 0.5 (default) | 1 |
| --- | --- | --- | --- |
| Mode | sombre minor | phrygian / harmonic minor | phrygian |
| Drums | downbeats and snares | the pattern as drawn | sixteenth hats, double kicks, a crash every other bar, fills between |
| Bass and arps | thinned to eighths | as drawn | sixteenths |
| Lead | | | doubled an octave up |
| Synths | less drive and pump, darker filters | as written | more drive, wider unison, open filters, deeper pump |
| Tempo | -8% | | +8% |

## Pipeline

`Musical parameters → Composition generator → Note events → Synthesizer → Audio`

| Module | Role |
| --- | --- |
| `theory.py` | Minor and major-side modes, scale degrees, triads, Markov chord progressions, common-tone key changes |
| `variation.py` | Motif transforms: invert, retrograde, split, merge, neighbor tone |
| `melody.py` | Motifs realised over chords, sequenced, with cadences, one phrase at a time |
| `parts.py` | Drone, bassline, arpeggio, percussion (GM drum notes), per style and section role |
| `arrange.py` | Palettes (shared material per key region), phrase builder, the fixed 16-bar form |
| `endless.py` | Endless form: key regions, style and key changes, motif evolution, twin phrases for switching |
| `synth.py` | PolyBLEP square/saw, 4-bit NES triangle, LFSR noise, ADSR, unison/drive/filter-sweep voices, sidechain pump, streaming renderer and mixer |
| `effects.py` | Stateful lowpass, ping-pong echo, overlap-add convolution reverb |
| `stream.py` | Live playback (renders ahead of the sound card), long recordings, and the instant combat switch |
| `midi.py` | Standard MIDI File export |

`--root` and `--tempo` transpose or retime the same seeded piece. `--mode` can also change its chords, since each mode allows different ones.
