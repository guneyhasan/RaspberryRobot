"""Silero VAD ile mikrofon akışından konuşma segmenti toplama."""
from __future__ import annotations

import logging
import select
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Optional

import numpy as np
import torch

import config
from modules import alsa_devices

logger = logging.getLogger(__name__)

_last_arecord_device_error = False
_active_arecord_proc: subprocess.Popen[bytes] | None = None

_model = None
_utils = None


def _load_silero():
    global _model, _utils
    if _model is not None:
        return _model, _utils
    logger.info("VAD modeli yükleniyor (ilk seferde 30–90 sn sürebilir)...")
    t0 = time.perf_counter()
    try:
        from silero_vad import load_silero_vad  # type: ignore

        model = load_silero_vad()
        _model, _utils = model, None
        logger.info("VAD modeli yüklendi (backend=silero_vad paketi, %.1fs).", time.perf_counter() - t0)
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
    logger.info("VAD modeli yüklendi (backend=torch.hub, %.1fs).", time.perf_counter() - t0)
    return _model, _utils


def warmup_vad() -> None:
    """Açılışta Silero'yu yükle; ilk dinlemede donmayı önler."""
    _load_silero()


def terminate_active_capture() -> None:
    """Ctrl+C veya servis durdurulurken arecord sürecini kapat."""
    global _active_arecord_proc
    proc = _active_arecord_proc
    _active_arecord_proc = None
    if proc is None:
        return
    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass


def _effective_silence_samples(
    speech_samples: int,
    sr: int,
    base_silence_sec: float,
) -> int:
    """Uzun konuşmada daha kısa sessizlikle erken kes."""
    if (
        getattr(config, "VAD_ADAPTIVE_ENDPOINTING", True)
        and speech_samples >= int(getattr(config, "VAD_ADAPTIVE_MIN_SPEECH_SEC", 0.5) * sr)
    ):
        sec = float(getattr(config, "VAD_ADAPTIVE_SILENCE_SEC", 0.35))
        return int(sec * sr)
    return int(base_silence_sec * sr)


def _finalize_segment(
    audio_parts: list[np.ndarray],
    sr: int,
    speech_samples: int,
) -> Optional[np.ndarray]:
    if not audio_parts:
        return None
    out = np.concatenate(audio_parts, axis=0)
    min_samples = int(float(getattr(config, "VAD_MIN_SPEECH_SEC", 0.25)) * sr)
    if speech_samples < min_samples:
        logger.info(
            "VAD: segment çok kısa (speech_samples=%s < min=%s) — atlandı",
            speech_samples,
            min_samples,
        )
        return None
    return out


def _capture_vad_loop(
    read_chunk: Callable[[], Optional[np.ndarray]],
    *,
    sr: int,
    thr: float,
    silence_sec: float,
    max_samples: int,
    idle_timeout_sec: float | None = None,
    on_partial: Callable[[np.ndarray], None] | None = None,
    backend_label: str = "vad",
) -> Optional[np.ndarray]:
    """
    Ortak VAD döngüsü.
    idle_timeout_sec: konuşma başlamadan bu süre dolunca None döner.
    """
    model, _ = _load_silero()
    chunk_samples = 512 if sr == 16000 else 256

    audio_parts: list[np.ndarray] = []
    silence_run = 0
    speaking = False
    speech_samples = 0
    total = 0
    t0 = time.perf_counter()
    t_first_speech: float | None = None
    idle_deadline = (time.monotonic() + idle_timeout_sec) if idle_timeout_sec else None
    last_partial_at = 0.0
    partial_interval = float(getattr(config, "WHISPER_PARTIAL_INTERVAL_SEC", 1.2))

    def vad_prob(chunk: np.ndarray) -> float:
        x = chunk.astype(np.float32) / 32768.0
        if len(x) < chunk_samples:
            x = np.pad(x, (0, chunk_samples - len(x)))
        elif len(x) > chunk_samples:
            x = x[:chunk_samples]
        t = torch.from_numpy(x)
        with torch.no_grad():
            return float(model(t, sr).item())

    while total < max_samples:
        if idle_deadline is not None and not speaking and time.monotonic() >= idle_deadline:
            logger.info("VAD(%s): idle timeout (%.1fs)", backend_label, idle_timeout_sec or 0)
            return None

        mono = read_chunk()
        if mono is None:
            break
        if len(mono) == 0:
            continue
        total += len(mono)

        prob = vad_prob(mono)
        is_speech = prob >= thr

        if is_speech:
            if not speaking:
                t_first_speech = time.perf_counter()
                idle_deadline = None
            speaking = True
            silence_run = 0
            speech_samples += len(mono)
            audio_parts.append(mono)
            if on_partial and speech_samples > sr // 2:
                now = time.monotonic()
                if now - last_partial_at >= partial_interval:
                    last_partial_at = now
                    try:
                        on_partial(np.concatenate(audio_parts, axis=0))
                    except Exception as e:
                        logger.debug("VAD on_partial hatası: %s", e)
        elif speaking:
            audio_parts.append(mono)
            silence_run += len(mono)
            need = _effective_silence_samples(speech_samples, sr, silence_sec)
            if silence_run >= need:
                break

    t1 = time.perf_counter()
    if not audio_parts:
        logger.info(
            "VAD(%s): konuşma yok (elapsed=%0.1fs, total_samples=%s)",
            backend_label,
            t1 - t0,
            total,
        )
        return None

    out = _finalize_segment(audio_parts, sr, speech_samples)
    if out is None:
        return None
    lead = (t_first_speech - t0) if t_first_speech is not None else -1.0
    logger.info(
        "VAD(%s): segment (elapsed=%0.1fs, lead=%0.2fs, out_sec=%0.2f, speech_samples=%s)",
        backend_label,
        t1 - t0,
        lead,
        len(out) / float(sr),
        speech_samples,
    )
    return out


def _read_subprocess_stderr(proc: subprocess.Popen[bytes], limit: int = 800) -> str:
    if proc.stderr is None:
        return ""
    try:
        r, _, _ = select.select([proc.stderr], [], [], 0.05)
        if not r:
            return ""
        raw = proc.stderr.read(limit)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _capture_arecord(
    sr: int,
    thr: float,
    silence_sec: float,
    max_samples: int,
    idle_timeout_sec: float | None,
    on_partial: Callable[[np.ndarray], None] | None,
    *,
    device: str | None = None,
) -> Optional[np.ndarray]:
    dev = (device or config.AUDIO_INPUT_ALSA_DEVICE or "").strip()
    if not dev:
        return None

    chunk_samples = 512 if sr == 16000 else 256
    bytes_per_chunk = int(chunk_samples * 2)
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

    _load_silero()

    p: subprocess.Popen[bytes] | None = None
    stdout = None
    arecord_failed = False
    global _last_arecord_device_error, _active_arecord_proc
    _last_arecord_device_error = False
    read_timeout = float(getattr(config, "VAD_ARECORD_READ_TIMEOUT_SEC", 0.25))
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _active_arecord_proc = p
        assert p.stdout is not None
        stdout = p.stdout
        time.sleep(0.08)
        if p.poll() is not None:
            err = _read_subprocess_stderr(p)
            _last_arecord_device_error = True
            alsa_devices.invalidate_working_input()
            logger.error(
                "arecord hemen kapandı (device=%s). %s stderr: %s",
                dev,
                alsa_devices.format_capture_device_summary(),
                err or "(boş)",
            )
            return None

        read_buf = bytearray()

        def read_chunk() -> Optional[np.ndarray]:
            nonlocal arecord_failed, read_buf
            global _last_arecord_device_error
            if arecord_failed or p is None:
                return None
            while len(read_buf) < bytes_per_chunk:
                try:
                    r, _, _ = select.select([stdout], [], [], read_timeout)  # type: ignore[list-item]
                except (ValueError, OSError):
                    arecord_failed = True
                    return None
                if not r:
                    return np.array([], dtype=np.int16)
                chunk = stdout.read(bytes_per_chunk - len(read_buf))  # type: ignore[union-attr]
                if chunk:
                    read_buf.extend(chunk)
                elif p.poll() is not None:
                    arecord_failed = True
                    _last_arecord_device_error = True
                    alsa_devices.invalidate_working_input()
                    err = _read_subprocess_stderr(p)
                    logger.error(
                        "arecord akışı kesildi (device=%s). stderr: %s",
                        dev,
                        err or "(boş)",
                    )
                    return None
            out = np.frombuffer(bytes(read_buf[:bytes_per_chunk]), dtype=np.int16).copy()
            del read_buf[:bytes_per_chunk]
            return out

        return _capture_vad_loop(
            read_chunk,
            sr=sr,
            thr=thr,
            silence_sec=silence_sec,
            max_samples=max_samples,
            idle_timeout_sec=idle_timeout_sec,
            on_partial=on_partial,
            backend_label="arecord",
        )
    except Exception as e:
        logger.exception("arecord/VAD hatası: %s", e)
        return None
    finally:
        _active_arecord_proc = None
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
            try:
                p.wait(timeout=1)
            except Exception:
                pass


def _capture_sounddevice(
    sr: int,
    thr: float,
    silence_sec: float,
    max_samples: int,
    idle_timeout_sec: float | None,
    on_partial: Callable[[np.ndarray], None] | None,
) -> Optional[np.ndarray]:
    try:
        import sounddevice as sd
    except Exception as e:
        logger.error("sounddevice import edilemedi (PortAudio?): %s", e)
        return None

    chunk_samples = 512 if sr == 16000 else 256
    device = None
    if config.AUDIO_INPUT_DEVICE:
        device = (
            int(config.AUDIO_INPUT_DEVICE)
            if config.AUDIO_INPUT_DEVICE.isdigit()
            else config.AUDIO_INPUT_DEVICE
        )
    logger.info("VAD kayıt backend=sounddevice device=%r sr=%s", device, sr)

    try:
        with sd.InputStream(
            channels=1,
            samplerate=sr,
            dtype="int16",
            blocksize=chunk_samples,
            device=device,
        ) as stream:

            def read_chunk() -> Optional[np.ndarray]:
                data, _ = stream.read(chunk_samples)
                return data[:, 0].copy()

            return _capture_vad_loop(
                read_chunk,
                sr=sr,
                thr=thr,
                silence_sec=silence_sec,
                max_samples=max_samples,
                idle_timeout_sec=idle_timeout_sec,
                on_partial=on_partial,
                backend_label="sounddevice",
            )
    except Exception as e:
        logger.exception("Mikrofon/VAD hatası: %s", e)
        return None


def _capture_audio(
    *,
    sample_rate: int | None = None,
    vad_threshold: float | None = None,
    silence_end_sec: float | None = None,
    max_sec: float | None = None,
    idle_timeout_sec: float | None = None,
    on_partial: Callable[[np.ndarray], None] | None = None,
) -> Optional[np.ndarray]:
    sr = sample_rate or config.SAMPLE_RATE
    thr = vad_threshold if vad_threshold is not None else config.VAD_THRESHOLD
    silence = silence_end_sec if silence_end_sec is not None else config.SILENCE_END_SEC
    max_dur = max_sec if max_sec is not None else config.MAX_UTTERANCE_SEC
    max_samples = int(max_dur * sr)

    if sys.platform == "linux" or (config.AUDIO_INPUT_ALSA_DEVICE or "").strip():
        for rescan in (False, True):
            dev = alsa_devices.resolve_capture_device(rescan=rescan)
            if not dev:
                return None
            result = _capture_arecord(
                sr, thr, silence, max_samples, idle_timeout_sec, on_partial, device=dev
            )
            if not _last_arecord_device_error:
                return result
        return None
    return _capture_sounddevice(sr, thr, silence, max_samples, idle_timeout_sec, on_partial)


def record_utterance(
    sample_rate: int | None = None,
    vad_threshold: float | None = None,
    silence_end_sec: float | None = None,
    max_sec: float | None = None,
    on_partial: Callable[[np.ndarray], None] | None = None,
) -> Optional[np.ndarray]:
    """Konuşma bitene kadar kayıt; konuşma yoksa None."""
    return _capture_audio(
        sample_rate=sample_rate,
        vad_threshold=vad_threshold,
        silence_end_sec=silence_end_sec,
        max_sec=max_sec,
        idle_timeout_sec=None,
        on_partial=on_partial,
    )


def listen_for_speech(
    timeout_sec: float,
    *,
    sample_rate: int | None = None,
    vad_threshold: float | None = None,
    silence_end_sec: float | None = None,
    max_sec: float | None = None,
    on_partial: Callable[[np.ndarray], None] | None = None,
) -> Optional[np.ndarray]:
    """
    En fazla timeout_sec bekler; konuşma başlarsa segment tamamlanana kadar devam eder.
    Süre dolunca konuşma yoksa None.
    """
    return _capture_audio(
        sample_rate=sample_rate,
        vad_threshold=vad_threshold,
        silence_end_sec=silence_end_sec,
        max_sec=max_sec,
        idle_timeout_sec=timeout_sec,
        on_partial=on_partial,
    )


def save_wav_int16(path, audio: np.ndarray, sample_rate: int | None = None) -> None:
    import wave

    sr = sample_rate or config.SAMPLE_RATE
    path = str(path)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.astype(np.int16).tobytes())
