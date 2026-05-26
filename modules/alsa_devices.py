"""ALSA kayıt cihazı keşfi ve hızlı açılabilirlik testi."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
    card_id: str = ""

    @property
    def plughw(self) -> str:
        return f"plughw:{self.card},{self.device}"

    @property
    def plug_card(self) -> str | None:
        """Reboot'ta kart numarası değişse de çalışır: plughw:CARD=Device,DEV=0"""
        cid = (self.card_id or "").strip()
        if not cid:
            return None
        return f"plughw:CARD={cid},DEV={self.device}"


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
                "arecord -l çalıştırılamadı: %s | PATH=%r | HOME=%r",
                e,
                os.environ.get("PATH", ""),
                os.environ.get("HOME", ""),
            )
            _arecord_list_warned = True
    return ""


def _parse_arecord_list(out: str) -> list[CaptureDevice]:
    devices: list[CaptureDevice] = []
    current_card: int | None = None
    current_card_id = ""
    for line in out.splitlines():
        m_card = re.match(r"^card (\d+):\s*(.+)$", line)
        if m_card:
            current_card = int(m_card.group(1))
            rest = m_card.group(2).strip()
            m_id = re.match(r"^(\S+)\s+\[(.+)\]\s*$", rest)
            current_card_id = m_id.group(1) if m_id else rest.split()[0] if rest else ""
            continue
        m_dev = re.match(r"^\s+device (\d+): (.+)$", line)
        if m_dev and current_card is not None:
            devices.append(
                CaptureDevice(
                    card=current_card,
                    device=int(m_dev.group(1)),
                    label=m_dev.group(2).strip(),
                    card_id=current_card_id,
                )
            )
    return devices


def _list_cards_from_proc() -> list[CaptureDevice]:
    """arecord -l boşken /proc/asound/cards ile USB vb. kartları bul."""
    path = Path("/proc/asound/cards")
    if not path.is_file():
        return []
    devices: list[CaptureDevice] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(\d+)\s+\[([^\]]+)\]", line)
        if not m:
            continue
        card = int(m.group(1))
        card_id = m.group(2).strip()
        label = line.split(":", 1)[-1].strip() if ":" in line else card_id
        devices.append(
            CaptureDevice(card=card, device=0, label=label, card_id=card_id)
        )
    return devices


def list_capture_devices() -> list[CaptureDevice]:
    out = _run_arecord_list()
    devices = _parse_arecord_list(out) if out else []
    if devices:
        return devices
    proc_devs = _list_cards_from_proc()
    if proc_devs:
        logger.info(
            "arecord -l boş; /proc/asound/cards üzerinden %d kart bulundu",
            len(proc_devs),
        )
    return proc_devs


def _is_likely_mic(dev: CaptureDevice) -> bool:
    blob = f"{dev.label} {dev.card_id} card{dev.card}".lower()
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


def _discovery_retry_settings() -> tuple[int, float]:
    retries = int(getattr(config, "MIC_DISCOVERY_RETRIES", 15) or 15)
    interval = float(getattr(config, "MIC_DISCOVERY_INTERVAL_SEC", 2.0) or 2.0)
    return max(1, retries), max(0.2, interval)


def _build_candidates(
    configured: str,
    auto: bool,
    discovered: list[CaptureDevice],
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(dev: str) -> None:
        d = dev.strip()
        if d and d not in seen:
            seen.add(d)
            candidates.append(d)

    add(configured)
    if auto:
        for dev in discovered:
            if _is_likely_mic(dev):
                plug = dev.plug_card
                if plug:
                    add(plug)
                add(dev.plughw)
        add("default")
        for dev in discovered:
            add(dev.plughw)
            plug = dev.plug_card
            if plug:
                add(plug)
    elif not configured:
        add("default")
    return candidates


def _try_resolve_once(*, rescan: bool) -> str | None:
    global _working_dev

    if rescan:
        _working_dev = None

    if _working_dev and not rescan:
        return _working_dev

    configured = (getattr(config, "AUDIO_INPUT_ALSA_DEVICE", "") or "").strip()
    auto = getattr(config, "AUDIO_INPUT_AUTO_DETECT", True)
    discovered = list_capture_devices()
    candidates = _build_candidates(configured, auto, discovered)

    for dev in candidates:
        if probe_capture_device(dev):
            if dev != configured and configured:
                logger.warning(
                    "Mikrofon: .env=%r çalışmıyor, kullanılan=%r",
                    configured,
                    dev,
                )
            elif dev != configured:
                logger.info("Mikrofon otomatik seçildi: %r", dev)
            _working_dev = dev
            return dev

    if discovered:
        labels = ", ".join(
            f"{d.plug_card or d.plughw} ({d.label})" for d in discovered
        )
        logger.error(
            "Hiçbir mikrofon açılamadı. Keşif: %s | .env=%r",
            labels,
            configured or "(boş)",
        )
    else:
        logger.error(
            "Kayıt cihazı yok (arecord -l ve /proc/asound/cards). "
            "USB mic / audio grubu / MIC_SETTLE_SEC. PATH=%r HOME=%r",
            os.environ.get("PATH", ""),
            os.environ.get("HOME", ""),
        )
    return None


def resolve_capture_device(*, rescan: bool = False, allow_wait: bool = False) -> str | None:
    """
    Çalışan ALSA kayıt cihazını döndürür.
    AUTO_DETECT=1: plughw:CARD=… (reboot'ta stabil) → plughw:N,0 → default.
    allow_wait: True yalnızca boot/preflight (ana döngüde False — hızlı SKIP).
    """
    retries, interval = _discovery_retry_settings()
    if not allow_wait:
        retries = 1
    for attempt in range(1, retries + 1):
        dev = _try_resolve_once(rescan=rescan or attempt > 1)
        if dev:
            return dev
        if attempt < retries:
            logger.info(
                "Mikrofon henüz hazır değil (%s/%s), %.1fs sonra tekrar…",
                attempt,
                retries,
                interval,
            )
            time.sleep(interval)
    return None


def format_capture_device_summary() -> str:
    devices = list_capture_devices()
    if not devices:
        return "arecord -l ve /proc/asound/cards: cihaz yok"
    parts = []
    for d in devices:
        stable = d.plug_card or d.plughw
        parts.append(f"{stable} ({d.label})")
    return "; ".join(parts)
