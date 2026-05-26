"""ALSA kayıt cihazı keşfi ve hızlı açılabilirlik testi."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

# Hoparlör / DAC kartları — mikrofon adayı değil
_SKIP_CARD_HINTS = (
    "hifiberry",
    "snd_rpi_hifiberry",
    "sndrpihifiberry",
    "bcm2835",
    "vc4-hdmi",
    "hdmi",
)

_working_dev: str | None = None
_arecord_list_warned: bool = False


@dataclass(frozen=True)
class CaptureDevice:
    card: int
    device: int
    label: str

    @property
    def plughw(self) -> str:
        return f"plughw:{self.card},{self.device}"


def _run_arecord_list() -> str:
    global _arecord_list_warned
    exe = shutil.which("arecord")
    if not exe and not _arecord_list_warned:
        logger.warning(
            "arecord PATH'te yok (systemd ortamı?). PATH=%r — alsa-utils kurulu mu?",
            os.environ.get("PATH", ""),
        )
        _arecord_list_warned = True
    try:
        r = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout or ""
        if not _arecord_list_warned:
            stderr = (r.stderr or "").strip()
            logger.warning(
                "arecord -l başarısız (rc=%s). stderr=%s",
                r.returncode,
                stderr[:500] if stderr else "(boş)",
            )
            _arecord_list_warned = True
    except (OSError, subprocess.TimeoutExpired) as e:
        if not _arecord_list_warned:
            logger.warning(
                "arecord -l çalıştırılamadı: %s | PATH=%r",
                e,
                os.environ.get("PATH", ""),
            )
            _arecord_list_warned = True
    return ""


def list_capture_devices() -> list[CaptureDevice]:
    out = _run_arecord_list()
    if not out:
        return []

    devices: list[CaptureDevice] = []
    current_card: int | None = None
    for line in out.splitlines():
        m_card = re.match(r"^card (\d+): (.+)$", line)
        if m_card:
            current_card = int(m_card.group(1))
            continue
        m_dev = re.match(r"^\s+device (\d+): (.+)$", line)
        if m_dev and current_card is not None:
            devices.append(
                CaptureDevice(
                    card=current_card,
                    device=int(m_dev.group(1)),
                    label=m_dev.group(2).strip(),
                )
            )
    return devices


def _is_likely_mic(dev: CaptureDevice) -> bool:
    blob = f"{dev.label} card{dev.card}".lower()
    return not any(h in blob for h in _SKIP_CARD_HINTS)


def probe_capture_device(dev: str, sample_rate: int | None = None) -> bool:
    sr = sample_rate or getattr(config, "SAMPLE_RATE", 16000)
    cmd = [
        "arecord",
        "-q",
        "-D",
        dev,
        "-f",
        "S16_LE",
        "-r",
        str(sr),
        "-c",
        "1",
        "-d",
        "1",
        "/dev/null",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=4)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def invalidate_working_input() -> None:
    global _working_dev
    _working_dev = None


def resolve_capture_device(*, rescan: bool = False) -> str | None:
    """
    Çalışan ALSA kayıt cihazını döndürür.
    Sıra: önbellek → .env → USB benzeri kartlar → default → diğerleri.
    """
    global _working_dev

    if rescan:
        _working_dev = None

    if _working_dev and not rescan:
        return _working_dev

    configured = (getattr(config, "AUDIO_INPUT_ALSA_DEVICE", "") or "").strip()
    auto = getattr(config, "AUDIO_INPUT_AUTO_DETECT", True)

    candidates: list[str] = []
    seen: set[str] = set()

    def add(dev: str) -> None:
        d = dev.strip()
        if d and d not in seen:
            seen.add(d)
            candidates.append(d)

    add(configured)
    discovered = list_capture_devices()
    if auto:
        for dev in discovered:
            if _is_likely_mic(dev):
                add(dev.plughw)
        add("default")
        for dev in discovered:
            add(dev.plughw)
    elif not configured:
        add("default")

    for dev in candidates:
        if probe_capture_device(dev):
            if dev != configured and configured:
                logger.warning(
                    "Mikrofon cihazı değişmiş olabilir: .env=%r çalışmıyor, kullanılan=%r "
                    "(arecord -l ile kontrol edip AUDIO_INPUT_ALSA_DEVICE güncelleyin)",
                    configured,
                    dev,
                )
            elif dev != configured:
                logger.info("Mikrofon otomatik seçildi: %r", dev)
            _working_dev = dev
            return dev

    if discovered:
        labels = ", ".join(f"{d.plughw} ({d.label})" for d in discovered)
        logger.error(
            "Hiçbir mikrofon açılamadı. arecord -l: %s | .env AUDIO_INPUT_ALSA_DEVICE=%r",
            labels,
            configured or "(boş)",
        )
    else:
        logger.error(
            "arecord -l boş — USB mikrofon takılı mı? PipeWire: systemctl --user stop pipewire wireplumber"
        )
    return None


def format_capture_device_summary() -> str:
    devices = list_capture_devices()
    if not devices:
        return "arecord -l: cihaz yok"
    parts = [f"{d.plughw} ({d.label})" for d in devices]
    return "; ".join(parts)
