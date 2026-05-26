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
        ("bluetooth aç", True),
        ("kanka bluetooth ac", True),
        ("bluetooth kulaklık modunu aç", False),
        ("kulaklik modunu ac", False),
        ("kanka 2 numaraya bağlan", True),
        ("iki numaraya baglan", True),
        ("iki numaraya eslestir", True),
        ("yeniden tara", True),
        ("bluetooth modundan çık", True),
        ("kanka bluetooth modundan cik", True),
        ("bluetooth kapat", False),
        ("kanka bluetooth kulaklık modunu kapat", False),
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
