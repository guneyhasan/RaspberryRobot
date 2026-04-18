"""Yerel whisper.cpp ile Türkçe STT."""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
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
            "-t",
            str(config.WHISPER_THREADS),
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

    t0 = time.perf_counter()
    logger.info("STT: dinleme başlıyor (sr=%s, vad_thr=%.2f, silence_end=%.1fs, max=%.1fs)", config.SAMPLE_RATE, config.VAD_THRESHOLD, config.SILENCE_END_SEC, config.MAX_UTTERANCE_SEC)
    audio = vad.record_utterance()
    t1 = time.perf_counter()
    if audio is None:
        logger.info("STT: VAD konuşma bulamadı (elapsed=%0.1fs)", t1 - t0)
        return "", 0.0
    if len(audio) < 1000:
        logger.info("STT: çok kısa segment (samples=%s, elapsed=%0.1fs) — atlandı", len(audio), t1 - t0)
        return "", 0.0

    logger.info("STT: segment alındı (samples=%s, sec=%0.2f, elapsed=%0.1fs)", len(audio), len(audio) / float(config.SAMPLE_RATE), t1 - t0)

    t_w0 = time.perf_counter()
    wake_ok = wake_word.passes_wake_gate(audio)
    t_w1 = time.perf_counter()
    if not wake_ok:
        logger.info("STT: wake gate geçmedi (elapsed=%0.1fs) — atlandı", t_w1 - t_w0)
        return "", 0.0
    logger.info("STT: wake gate geçti (elapsed=%0.1fs)", t_w1 - t_w0)

    t_tr0 = time.perf_counter()
    text, conf = transcribe_pcm(audio)
    t_tr1 = time.perf_counter()
    logger.info('STT: whisper çıktı (elapsed=%0.1fs, conf=%.2f) text="%s"', t_tr1 - t_tr0, conf, " ".join(text.split())[:220])
    return text, conf
