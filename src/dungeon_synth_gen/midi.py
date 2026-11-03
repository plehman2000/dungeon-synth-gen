"""Standard MIDI File (format 1) export, no dependencies."""

from __future__ import annotations

from pathlib import Path

from dungeon_synth_gen.events import Composition

CHANNELS = {"lead": 0, "counter": 4, "arp": 1, "bass": 2, "drone": 3, "perc": 9}
PROGRAMS = {"lead": 80, "counter": 80, "arp": 80, "bass": 38, "drone": 89}  # GM: square lead, synth bass, warm pad


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return kind + len(data).to_bytes(4, "big") + data


def to_midi(comp: Composition, ppq: int = 480) -> bytes:
    tempo = round(60_000_000 / comp.tempo).to_bytes(3, "big")
    conductor = b"\x00\xff\x51\x03" + tempo + b"\x00\xff\x58\x04\x04\x02\x18\x08\x00\xff\x2f\x00"
    tracks = [conductor]
    for name, channel in CHANNELS.items():
        events = comp.track(name)
        if not events:
            continue
        messages = []  # (tick, note-offs before note-ons, bytes)
        for e in events:
            on = round(e.start * ppq)
            off = max(on + 1, round((e.start + e.duration) * ppq))
            velocity = max(1, min(127, e.velocity))
            messages.append((on, 1, bytes([0x90 | channel, e.pitch, velocity])))
            messages.append((off, 0, bytes([0x80 | channel, e.pitch, 0])))
        messages.sort(key=lambda m: (m[0], m[1]))
        data = bytearray(b"\x00\xff\x03" + _vlq(len(name)) + name.encode())
        if name in PROGRAMS:
            data += bytes([0x00, 0xC0 | channel, PROGRAMS[name]])
        last = 0
        for tick, _, message in messages:
            data += _vlq(tick - last) + message
            last = tick
        data += b"\x00\xff\x2f\x00"
        tracks.append(bytes(data))
    header = (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + ppq.to_bytes(2, "big")
    return _chunk(b"MThd", header) + b"".join(_chunk(b"MTrk", t) for t in tracks)


def write_midi(comp: Composition, path: Path) -> None:
    path.write_bytes(to_midi(comp))
