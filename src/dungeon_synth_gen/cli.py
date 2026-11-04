"""Command line entry point: ``dungeon-synth-gen --seed 42 --midi``, ``--endless`` or ``--minutes 10``."""

from __future__ import annotations

import argparse
import atexit
import random
import sys
import threading
import time
from pathlib import Path

from dungeon_synth_gen.arrange import STYLES, compose, seeded_style
from dungeon_synth_gen.events import Phrase
from dungeon_synth_gen.midi import write_midi
from dungeon_synth_gen.parts import FLAVOUR_STYLE
from dungeon_synth_gen.synth import SAMPLE_RATE, render, write_wav
from dungeon_synth_gen.theory import MODES, parse_note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dungeon-synth-gen", description="Generate 8-bit dungeon synth: 16-bar pieces, or one endless stream.")
    parser.add_argument("--seed", type=int, help="random seed; the same seed always gives the same piece")
    parser.add_argument("-o", "--out", type=Path, help="WAV path (default: out/dungeon_<seed>.wav)")
    parser.add_argument("--midi", action="store_true", help="also write a .mid file next to the WAV")
    parser.add_argument(
        "--style", choices=STYLES,
        help="normal: slow dungeon crawl; ambient: calm, major-key, no drums; combat: double time with heavy drums."
        " Default: drawn from the seed (normal or ambient), or per key region in the endless stream",
    )
    parser.add_argument(
        "--flavour", "--flavor", choices=list(FLAVOUR_STYLE),
        help="pin one flavour for the whole piece or stream, e.g. doom or brut; implies its style"
        " (combat: brut, darksynth, industrial, doom, chiptrash)",
    )
    parser.add_argument("--mode", choices=sorted(MODES), help="override the seeded mode")
    parser.add_argument("--root", type=parse_note, help="override the seeded tonic, e.g. D, F#, Bb")
    parser.add_argument("--tempo", type=float, help="override the seeded tempo (BPM)")
    parser.add_argument(
        "--intensity", type=float, default=0.5, metavar="0-1",
        help="how much the fight matters, for combat: 0 sparse and sombre, 1 grave and huge (default 0.5)",
    )
    parser.add_argument("--dry", action="store_true", help="skip reverb and echo")
    parser.add_argument("--endless", action="store_true", help="play one continuous, ever-evolving stream until Ctrl+C")
    parser.add_argument("--minutes", type=float, help="write this many minutes of the continuous stream to a WAV")
    parser.add_argument(
        "--switch-at", type=_seconds, metavar="S,S,...",
        help="with --minutes: toggle combat at these times in seconds, e.g. 20,45 (--endless toggles on the c key)",
    )
    args = parser.parse_args(argv)
    if args.switch_at and not args.minutes:
        parser.error("--switch-at needs --minutes")
    if args.flavour:
        own = FLAVOUR_STYLE[args.flavour]
        if args.style and args.style != own:
            parser.error(f"--flavour {args.flavour} is a {own} flavour, not {args.style}")
        args.style = own

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    overrides = {
        "style": args.style, "mode": args.mode, "root": args.root, "tempo": args.tempo, "intensity": args.intensity,
        "flavour": args.flavour,
    }
    if args.endless:
        return _play_endless(seed, use_effects=not args.dry, **overrides)
    if args.minutes:
        suffix = f"_{args.flavour or args.style}" if args.style else ""
        out = args.out or Path("out") / f"dungeon_endless_{seed}{suffix}.wav"
        return _record_endless(out, seed, args.minutes, use_effects=not args.dry, switches=args.switch_at or (), **overrides)

    overrides["style"] = args.style or seeded_style(seed)
    suffix = f"_{args.flavour}" if args.flavour else "" if overrides["style"] == "normal" else f"_{overrides['style']}"
    out = args.out or Path("out") / f"dungeon_{seed}{suffix}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)

    began = time.perf_counter()
    comp = compose(seed, **overrides)
    audio = render(comp, use_effects=not args.dry)
    write_wav(out, audio)
    print(comp.describe())
    print(f"wrote {out} ({len(audio) / SAMPLE_RATE:.1f} s audio, rendered in {time.perf_counter() - began:.1f} s)")
    if args.midi:
        write_midi(comp, out.with_suffix(".mid"))
        print(f"wrote {out.with_suffix('.mid')}")
    return 0


def _print_phrase(phrase: Phrase, seconds: float) -> None:
    minutes, secs = divmod(int(seconds), 60)
    print(f"{minutes:02d}:{secs:02d}  {phrase.describe()}", flush=True)


def _seconds(text: str) -> list[float]:
    return [float(part) for part in text.split(",")]


def _listen_for_toggle(switch) -> bool:
    """Flip ``switch`` on every c keypress, from a daemon thread. False when there is no console to read."""
    if not sys.stdin.isatty():
        return False
    if sys.platform == "win32":
        import msvcrt

        read_key = msvcrt.getwch
    else:
        import termios
        import tty

        fd = sys.stdin.fileno()
        atexit.register(termios.tcsetattr, fd, termios.TCSADRAIN, termios.tcgetattr(fd))
        tty.setcbreak(fd)  # single keypresses; Ctrl+C still interrupts

        def read_key() -> str:
            return sys.stdin.read(1)

    def listen() -> None:
        while True:
            if read_key().lower() == "c":
                switch.toggle()

    threading.Thread(target=listen, daemon=True).start()
    return True


def _play_endless(seed: int, **options) -> int:
    from dungeon_synth_gen.stream import CombatSwitch, play

    switch = CombatSwitch(on=options["style"] == "combat")
    if not _listen_for_toggle(switch):
        switch = None  # no keyboard to flip it, so skip rendering the twin
    hint = "press c to toggle combat, Ctrl+C to stop" if switch else "Ctrl+C to stop"
    print(f"seed {seed}  |  {options['flavour'] or options['style'] or 'mixed styles'}  |  one continuous stream, {hint}\n")
    try:
        play(seed, on_phrase=_print_phrase, switch=switch, **options)
    except KeyboardInterrupt:
        print("stopped")
    return 0


def _record_endless(out: Path, seed: int, minutes: float, **options) -> int:
    from dungeon_synth_gen.stream import record

    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"seed {seed}  |  {options['flavour'] or options['style'] or 'mixed styles'}  |  recording {minutes:g} min of the continuous stream\n")
    began = time.perf_counter()
    seconds = record(out, seed, minutes, on_phrase=_print_phrase, **options)
    print(f"\nwrote {out} ({seconds / 60:.1f} min audio, rendered in {time.perf_counter() - began:.0f} s)")
    return 0
