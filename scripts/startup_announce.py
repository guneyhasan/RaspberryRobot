#!/usr/bin/env python3
"""Açılış anonsu — Piper öncelikli (ağ gerekmez)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from modules import tts  # noqa: E402


def main() -> None:
    try:
        tts.speak(config.STARTUP_PHRASE, prefer_online=False)
    except Exception as e:
        print(f"[startup_announce] Piper/anons hatası: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
