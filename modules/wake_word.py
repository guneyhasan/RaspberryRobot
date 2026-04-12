"""Wake word: Porcupine (özel .ppn), OpenWakeWord veya geçiş modu."""
from __future__ import annotations

import logging
import os

import numpy as np

import config

logger = logging.getLogger(__name__)

_porcupine = None
_oww_model = None


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


def _init_openwakeword():
    global _oww_model
    if _oww_model is not None:
        return _oww_model
    try:
        from openwakeword.model import Model

        models = os.getenv("OPENWAKEWORD_MODELS", "alexa").strip().split(",")
        models = [m.strip() for m in models if m.strip()]
        _oww_model = Model(wakeword_models=models)
        return _oww_model
    except Exception as e:
        logger.debug("OpenWakeWord yüklenemedi: %s", e)
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


def audio_matches_openwakeword(pcm_int16: np.ndarray, sample_rate: int) -> bool:
    m = _init_openwakeword()
    if m is None:
        return False
    try:
        arr = pcm_int16.astype(np.int16).flatten()
        step = 1280
        for i in range(0, len(arr) - step + 1, step):
            chunk = arr[i : i + step]
            preds = m.predict(chunk)
            if preds and max(preds.values(), default=0.0) > 0.5:
                return True
    except Exception as e:
        logger.debug("OWW predict hatası: %s", e)
    return False


def _use_openwakeword() -> bool:
    return os.getenv("USE_OPENWAKEWORD", "0").lower() in ("1", "true", "yes")


def passes_wake_gate(pcm_int16: np.ndarray, sample_rate: int | None = None) -> bool:
    """
    True ise STT'ye geçilir.
    Porcupine / OpenWakeWord kapalıysa True (STT sonrası metin tabanlı wake önerilir).
    """
    sr = sample_rate or config.SAMPLE_RATE
    if config.PICOVOICE_ACCESS_KEY:
        pv = _init_porcupine()
        if pv is not None:
            return audio_matches_porcupine(pcm_int16, sr)
    if _use_openwakeword():
        oww = _init_openwakeword()
        if oww is not None:
            return audio_matches_openwakeword(pcm_int16, sr)
    logger.debug("Ses tabanlı wake kapalı — STT sonrası metin kontrolü kullanılabilir.")
    return True


def audio_wake_enabled() -> bool:
    """Ses tabanlı wake (Porcupine veya USE_OPENWAKEWORD=1) aktif mi?"""
    if config.PICOVOICE_ACCESS_KEY:
        return True
    return _use_openwakeword() and _init_openwakeword() is not None


def transcript_has_wake_phrase(text: str) -> bool:
    t = text.lower().strip()
    if not config.WAKE_PHRASES:
        return True
    return any(p in t for p in config.WAKE_PHRASES)
