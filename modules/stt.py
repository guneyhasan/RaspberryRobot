"""Yerel whisper.cpp ile Türkçe STT."""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import config
from modules import vad

logger = logging.getLogger(__name__)


def transcribe_pcm(pcm_int16: np.ndarray, sample_rate: int | None = None) -> tuple[str, float]:
    """
    whisper.cpp main ile transkripsiyon.
    Döndürür: (metin, güven_tahmini) — güven whisper çıktısından heuristik veya 1.0.
    """
    sr = sample_rate or config.SAMPLE_RATE
    if config.WHISPER_BINARY.is_file() is False:
        raise FileNotFoundError(f"Whisper binary yok: {config.WHISPER_BINARY}")
    if not config.WHISPER_MODEL.is_file():
        raise FileNotFoundError(f"Whisper model yok: {config.WHISPER_MODEL}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)
    try:
        vad.save_wav_int16(wav_path, pcm_int16, sr)
        cmd = [
            str(config.WHISPER_BINARY),
            "-m",
            str(config.WHISPER_MODEL),
            "-f",
            str(wav_path),
            "-l",
            "tr",
            "-nt",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            logger.error("whisper.cpp hata %s: %s", r.returncode, err)
            raise RuntimeError(f"whisper.cpp başarısız: {err[:500]}")

        raw = (r.stdout or "").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        text = lines[-1] if lines else ""
        text = re.sub(r"^\[[^\]]+\]\s*", "", text)
        conf = 0.94 if text else 0.0
        return text, conf
    finally:
        wav_path.unlink(missing_ok=True)


def listen_and_transcribe() -> tuple[str, float]:
    """VAD → ses tabanlı wake (varsa) → whisper."""
    from modules import wake_word

    audio = vad.record_utterance()
    if audio is None or len(audio) < 1000:
        return "", 0.0
    if not wake_word.passes_wake_gate(audio):
        return "", 0.0
    return transcribe_pcm(audio)
