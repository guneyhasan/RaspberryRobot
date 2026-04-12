"""Wake word: Wyoming openWakeWord (TCP), Porcupine veya transkript tabanlı geçiş."""
from __future__ import annotations

import logging
import os

import numpy as np

import config

logger = logging.getLogger(__name__)

_porcupine = None


def _init_porcupine():
    global _porcupine
    if _porcupine is not None:
        return _porcupine
    if not config.PICOVOICE_ACCESS_KEY:
        return None
    import pvporcupine

    kw_path_str = os.getenv("PORCUPINE_KEYWORD_PATH", "").strip()
    if kw_path_str:
        _porcupine = pvporcupine.create(
            access_key=config.PICOVOICE_ACCESS_KEY,
            keyword_paths=[kw_path_str],
        )
        return _porcupine
    builtin = os.getenv("PORCUPINE_BUILTIN_KEYWORD", "computer").strip().lower()
    if builtin in pvporcupine.KEYWORDS:
        _porcupine = pvporcupine.create(
            access_key=config.PICOVOICE_ACCESS_KEY,
            keywords=[builtin],
        )
        logger.warning(
            "Porcupine yerleşik anahtar kelime: %s. Üretim için PORCUPINE_KEYWORD_PATH (.ppn) kullanın.",
            builtin,
        )
        return _porcupine
    logger.error("Porcupine için PORCUPINE_KEYWORD_PATH veya geçerli PORCUPINE_BUILTIN_KEYWORD gerekli.")
    return None


def audio_matches_porcupine(pcm_int16: np.ndarray, sample_rate: int) -> bool:
    pv = _init_porcupine()
    if pv is None:
        return False
    if sample_rate != pv.sample_rate:
        logger.warning(
            "Porcupine örnek hızı %s bekliyor, gelen %s — atlanıyor.",
            pv.sample_rate,
            sample_rate,
        )
        return False
    frame = pv.frame_length
    audio = pcm_int16.astype(np.int16).flatten()
    for i in range(0, len(audio) - frame + 1, frame):
        chunk = audio[i : i + frame]
        if len(chunk) < frame:
            break
        if pv.process(chunk.tobytes()) >= 0:
            return True
    return False


def _use_wyoming_openwakeword() -> bool:
    return bool(config.WYOMING_OPENWAKEWORD_URI and config.WYOMING_OPENWAKEWORD_URI.strip())


def passes_wake_gate(pcm_int16: np.ndarray, sample_rate: int | None = None) -> bool:
    """
    True ise STT'ye geçilir.
    Öncelik: Wyoming openWakeWord (ayrı süreç) → Porcupine → yoksa True (transkript wake).
    """
    sr = sample_rate or config.SAMPLE_RATE
    if _use_wyoming_openwakeword():
        from modules import wyoming_openwakeword_client as wy_oww

        return wy_oww.wyoming_openwakeword_match(pcm_int16, sr)
    if config.PICOVOICE_ACCESS_KEY:
        pv = _init_porcupine()
        if pv is not None:
            return audio_matches_porcupine(pcm_int16, sr)
    logger.debug("Ses tabanlı wake kapalı — STT sonrası metin kontrolü kullanılabilir.")
    return True


def audio_wake_enabled() -> bool:
    """Ses tabanlı wake (Wyoming OWW veya Porcupine) aktif mi?"""
    if _use_wyoming_openwakeword():
        return True
    if config.PICOVOICE_ACCESS_KEY:
        return True
    return False


def transcript_has_wake_phrase(text: str) -> bool:
    t = text.lower().strip()
    if not config.WAKE_PHRASES:
        return True
    return any(p in t for p in config.WAKE_PHRASES)
