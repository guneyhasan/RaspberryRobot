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


def check_audio_output_playback() -> tuple[bool, str]:
    """Yapılandırılmış ALSA çıkışında kısa test tonu."""
    import config as cfg

    dev = (getattr(cfg, "AUDIO_OUTPUT_ALSA_DEVICE", "") or "").strip()
    cmd = ["speaker-test", "-t", "wav", "-c", "1", "-l", "1"]
    if dev:
        cmd = ["speaker-test", "-D", dev, "-t", "wav", "-c", "1", "-l", "1"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if r.returncode == 0:
            return True, f"speaker-test ok ({dev or 'default'})"
        err = (r.stderr or r.stdout or "").strip()[:300]
        return False, f"speaker-test failed ({dev or 'default'}): {err}"
    except FileNotFoundError:
        return True, "speaker-test yok (atlandı)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def check_audio_input() -> bool:
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and "card" in (r.stdout or "").lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_bluetooth_tools() -> tuple[bool, str]:
    import config as cfg

    if not getattr(cfg, "BLUETOOTH_ENABLED", True):
        return True, "bt_disabled"
    try:
        r = subprocess.run(
            ["which", "bluetoothctl"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode != 0:
            return False, "bluetoothctl yok"
    except (OSError, subprocess.TimeoutExpired):
        return False, "bluetoothctl kontrol edilemedi"
    try:
        subprocess.run(
            ["bluealsa-aplay", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.warning("bluealsa-aplay bulunamadı — kulaklık modu çalışmayabilir.")
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("bluealsa-aplay kontrolü başarısız.")
    return True, "ok"


def run_preflight() -> tuple[bool, str]:
    if not check_audio_output():
        return False, "Ses çıkışı (aplay -l) bulunamadı."
    play_ok, play_msg = check_audio_output_playback()
    if not play_ok:
        logger.warning("Hoparlör testi başarısız: %s", play_msg)
    if not check_audio_input():
        return False, "Mikrofon (arecord -l) bulunamadı."
    bt_ok, bt_msg = check_bluetooth_tools()
    if not bt_ok:
        logger.warning("Bluetooth ön kontrol: %s", bt_msg)
    if not tts.internet_available():
        logger.warning("İnternet yok — offline TTS kullanılacak.")
    return True, "ok"
