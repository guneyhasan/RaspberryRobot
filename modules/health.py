"""İnternet ve ses cihazı kontrolleri (Python tarafı)."""
from __future__ import annotations

import logging
import subprocess

from modules import tts

logger = logging.getLogger(__name__)


def check_audio_output() -> bool:
    try:
        r = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and "card" in (r.stdout or "").lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_audio_input() -> bool:
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and "card" in (r.stdout or "").lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_preflight() -> tuple[bool, str]:
    if not check_audio_output():
        return False, "Ses çıkışı (aplay -l) bulunamadı."
    if not check_audio_input():
        return False, "Mikrofon (arecord -l) bulunamadı."
    if not tts.internet_available():
        logger.warning("İnternet yok — offline TTS kullanılacak.")
    return True, "ok"
