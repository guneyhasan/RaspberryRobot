#!/usr/bin/env python3
"""Bluetooth oturumu komut eşleme duman testi (ağ/BT donanımı gerekmez)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import bluetooth_session  # noqa: E402


def main() -> int:
    cases = [
        ("kanka bluetooth kulaklık modunu aç", True),
        ("kulaklik modunu ac", True),
        ("kanka 2 numaraya bağlan", True),
        ("iki numaraya baglan", True),
        ("kanka bluetooth kulaklık modunu kapat", True),
        ("merhaba kanka", False),
    ]
    ok = 0
    for text, expect_handled in cases:
        handled, replies = bluetooth_session.handle_turn(text)
        status = handled == expect_handled
        print(f"{'OK' if status else 'FAIL'}: {text!r} -> handled={handled} replies={len(replies)}")
        if status:
            ok += 1
    print(f"{ok}/{len(cases)} passed")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
