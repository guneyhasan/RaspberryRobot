"""Silero VAD ile mikrofon akışından konuşma segmenti toplama."""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import torch

import config

logger = logging.getLogger(__name__)

_model = None
_utils = None


def _load_silero():
    global _model, _utils
    if _model is not None:
        return _model, _utils
    # Öncelik: pip ile gelen `silero-vad` paketi (internet gerektirmez).
    # Fallback: torch.hub (ilk kurulumda GitHub'a çıkar, yavaş bağlantıda timeout yapabilir).
    try:
        from silero_vad import load_silero_vad  # type: ignore

        model = load_silero_vad()
        _model, _utils = model, None
        logger.info("VAD modeli yüklendi (backend=silero_vad paketi).")
        return _model, _utils
    except Exception as e:
        logger.warning("silero_vad paketinden yüklenemedi, torch.hub denenecek: %s", e)

    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
        onnx=False,
        trust_repo=True,
    )
    _model, _utils = model, utils
    logger.info("VAD modeli yüklendi (backend=torch.hub).")
    return _model, _utils


def record_utterance(
    sample_rate: int | None = None,
    vad_threshold: float | None = None,
    silence_end_sec: float | None = None,
    max_sec: float | None = None,
) -> Optional[np.ndarray]:
    """
    Konuşma bitene kadar (sessizlik sonrası) kayıt döndürür.
    int16 mono numpy array veya None (sessizlik / iptal).
    """
    sr = sample_rate or config.SAMPLE_RATE
    thr = vad_threshold if vad_threshold is not None else config.VAD_THRESHOLD
    silence = silence_end_sec if silence_end_sec is not None else config.SILENCE_END_SEC
    max_dur = max_sec if max_sec is not None else config.MAX_UTTERANCE_SEC

    model, _utils = _load_silero()

    chunk_samples = 512 if sr == 16000 else 256
    silence_samples = int(silence * sr)
    max_samples = int(max_dur * sr)

    audio_parts: list[np.ndarray] = []
    silence_run = 0
    speaking = False
    total = 0

    def vad_prob(chunk: np.ndarray) -> float:
        x = chunk.astype(np.float32) / 32768.0
        if len(x) < chunk_samples:
            x = np.pad(x, (0, chunk_samples - len(x)))
        elif len(x) > chunk_samples:
            x = x[:chunk_samples]
        t = torch.from_numpy(x)
        with torch.no_grad():
            return float(model(t, sr).item())

    def read_with_arecord() -> Optional[np.ndarray]:
        """
        ALSA cihazından `arecord` ile ham PCM (S16_LE) okuyup VAD ile segment çıkarır.
        sounddevice yanlış cihaz seçtiğinde en güvenilir yöntem.
        """
        dev = config.AUDIO_INPUT_ALSA_DEVICE
        if not dev:
            return None

        bytes_per_chunk = int(chunk_samples * 2)  # S16_LE mono
        cmd = [
            "arecord",
            "-q",
            "-D",
            dev,
            "-r",
            str(sr),
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-t",
            "raw",
        ]
        logger.info("VAD kayıt backend=arecord device=%s sr=%s", dev, sr)

        audio_parts: list[np.ndarray] = []
        silence_run = 0
        speaking = False
        total = 0
        t0 = time.perf_counter()
        t_first_speech: float | None = None

        p: subprocess.Popen[bytes] | None = None
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert p.stdout is not None

            while total < max_samples:
                b = p.stdout.read(bytes_per_chunk)
                if not b or len(b) < bytes_per_chunk:
                    break
                mono = np.frombuffer(b, dtype=np.int16).copy()
                total += len(mono)

                prob = vad_prob(mono)
                is_speech = prob >= thr

                if is_speech:
                    if not speaking:
                        t_first_speech = time.perf_counter()
                    speaking = True
                    silence_run = 0
                    audio_parts.append(mono)
                elif speaking:
                    audio_parts.append(mono)
                    silence_run += len(mono)
                    if silence_run >= silence_samples:
                        break
        except Exception as e:
            logger.exception("arecord/VAD hatası: %s", e)
            return None
        finally:
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
                try:
                    p.wait(timeout=1)
                except Exception:
                    pass

        t1 = time.perf_counter()
        if not audio_parts:
            logger.info("VAD(arecord): konuşma yok (elapsed=%0.1fs, total_samples=%s)", t1 - t0, total)
            return None
        out = np.concatenate(audio_parts, axis=0)
        lead = (t_first_speech - t0) if t_first_speech is not None else -1.0
        logger.info(
            "VAD(arecord): segment çıktı (elapsed=%0.1fs, lead_to_speech=%0.2fs, total_samples=%s, out_samples=%s, out_sec=%0.2f)",
            t1 - t0,
            lead,
            total,
            len(out),
            len(out) / float(sr),
        )
        return out

    try:
        if config.AUDIO_INPUT_ALSA_DEVICE:
            return read_with_arecord()

        device = None
        if config.AUDIO_INPUT_DEVICE:
            # sounddevice accepts index (int) or substring name (str)
            device = int(config.AUDIO_INPUT_DEVICE) if config.AUDIO_INPUT_DEVICE.isdigit() else config.AUDIO_INPUT_DEVICE
            logger.info("Mikrofon cihazı seçildi (AUDIO_INPUT_DEVICE=%r)", config.AUDIO_INPUT_DEVICE)
        logger.info("VAD kayıt backend=sounddevice device=%r sr=%s", device, sr)
        with sd.InputStream(
            channels=1,
            samplerate=sr,
            dtype="int16",
            blocksize=chunk_samples,
            device=device,
        ) as stream:
            t0 = time.perf_counter()
            t_first_speech: float | None = None
            while total < max_samples:
                data, _ = stream.read(chunk_samples)
                mono = data[:, 0].copy()
                total += len(mono)

                prob = vad_prob(mono)
                is_speech = prob >= thr

                if is_speech:
                    if not speaking:
                        t_first_speech = time.perf_counter()
                    speaking = True
                    silence_run = 0
                    audio_parts.append(mono)
                elif speaking:
                    audio_parts.append(mono)
                    silence_run += len(mono)
                    if silence_run >= silence_samples:
                        break
    except Exception as e:
        logger.exception("Mikrofon/VAD hatası: %s", e)
        return None

    t1 = time.perf_counter()
    if not audio_parts:
        logger.info("VAD(sounddevice): konuşma yok (elapsed=%0.1fs, total_samples=%s)", t1 - t0, total)
        return None
    out = np.concatenate(audio_parts, axis=0)
    lead = (t_first_speech - t0) if t_first_speech is not None else -1.0
    logger.info(
        "VAD(sounddevice): segment çıktı (elapsed=%0.1fs, lead_to_speech=%0.2fs, total_samples=%s, out_samples=%s, out_sec=%0.2f)",
        t1 - t0,
        lead,
        total,
        len(out),
        len(out) / float(sr),
    )
    return out


def save_wav_int16(path, audio: np.ndarray, sample_rate: int | None = None) -> None:
    import wave

    sr = sample_rate or config.SAMPLE_RATE
    path = str(path)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.astype(np.int16).tobytes())
