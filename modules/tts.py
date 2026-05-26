"""TTS: OpenAI (çevrimiçi) veya Piper (çevrimdışı) + hoparlör oynatma."""
from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_speaking = threading.Event()
_runtime_output_device: Optional[str] = None
_output_lock = threading.Lock()
_stop_flag = threading.Event()
_child_procs: list[subprocess.Popen] = []
_child_lock = threading.Lock()


class TtsInterrupted(Exception):
    """stop_speaking() ile kesildi."""


def set_output_device(device: str | None) -> None:
    """Runtime ALSA çıkışı (config üzerine yazar). None = config varsayılanına dön."""
    global _runtime_output_device
    with _output_lock:
        if device is None or not str(device).strip():
            _runtime_output_device = None
        else:
            _runtime_output_device = str(device).strip()


def get_output_device() -> str | None:
    with _output_lock:
        if _runtime_output_device:
            return _runtime_output_device
    base = (config.AUDIO_OUTPUT_ALSA_DEVICE or "").strip()
    return base or None


def _effective_output_device() -> str | None:
    return get_output_device()


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.TIMEOUT_SECONDS)
    return _client


def internet_available(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False


def _speaker_fallback_device() -> str:
    """bluealsa PCM yokken TTS için hoparlör ALSA cihazı."""
    bt_sp = (getattr(config, "BLUETOOTH_SPEAKER_ALSA_DEVICE", "") or "").strip()
    if bt_sp:
        return bt_sp
    base = (config.AUDIO_OUTPUT_ALSA_DEVICE or "").strip()
    return base or "plughw:0,0"


def _rebuild_aplay_cmd(cmd: list[str], device: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(cmd):
        if cmd[i] == "-D" and i + 1 < len(cmd):
            out.extend(["-D", device])
            i += 2
            continue
        out.append(cmd[i])
        i += 1
    if "-D" not in out and cmd and cmd[0] == "aplay":
        insert_at = 2 if len(cmd) > 1 and cmd[1] == "-q" else 1
        out = cmd[:insert_at] + ["-D", device] + cmd[insert_at:]
    return out


def _aplay_cmd_base(*, rate: int | None = None, channels: int = 1, raw: bool = False) -> list[str]:
    cmd = ["aplay", "-q"]
    dev = _effective_output_device()
    if dev:
        cmd.extend(["-D", dev])
    if raw and rate is not None:
        cmd.extend(["-r", str(rate), "-f", "S16_LE", "-c", str(channels)])
    return cmd


def _register_child(proc: subprocess.Popen) -> None:
    with _child_lock:
        _child_procs.append(proc)


def _unregister_child(proc: subprocess.Popen) -> None:
    with _child_lock:
        try:
            _child_procs.remove(proc)
        except ValueError:
            pass


def stop_speaking() -> None:
    """Çalan TTS'i kes (Bluetooth liste barge-in vb.)."""
    _stop_flag.set()
    with _child_lock:
        procs = list(_child_procs)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
    _speaking.clear()


def clear_stop_flag() -> None:
    _stop_flag.clear()


def _wait_proc(proc: subprocess.Popen, *, timeout: float = 120) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _stop_flag.is_set():
            try:
                proc.kill()
            except OSError:
                pass
            raise TtsInterrupted("TTS kesildi")
        rc = proc.poll()
        if rc is not None:
            return rc
        time.sleep(0.05)
    try:
        proc.kill()
    except OSError:
        pass
    raise RuntimeError("aplay timeout")


def _run_aplay(cmd: list[str], *, context: str = "aplay", allow_fallback: bool = True) -> None:
    dev_before = _effective_output_device() or ""
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _register_child(p)
    try:
        rc = _wait_proc(p, timeout=120)
    except TtsInterrupted:
        raise
    finally:
        _unregister_child(p)
    r = subprocess.CompletedProcess(cmd, rc, stderr=p.stderr.read() if p.stderr else b"")
    if r.returncode == 0:
        return
    err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
    rc = r.returncode
    dev = dev_before or "(varsayılan)"

    if allow_fallback and dev_before.startswith("bluealsa"):
        fb = _speaker_fallback_device()
        logger.warning(
            "%s bluealsa başarısız, hoparlöre düşülüyor: %s (önceki=%s)",
            context,
            fb,
            dev_before,
        )
        set_output_device(fb)
        p2 = subprocess.Popen(_rebuild_aplay_cmd(cmd, fb), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _register_child(p2)
        try:
            rc2 = _wait_proc(p2, timeout=120)
        except TtsInterrupted:
            raise
        finally:
            _unregister_child(p2)
        if rc2 == 0:
            return
        err = (p2.stderr.read() if p2.stderr else b"").decode("utf-8", errors="replace").strip()
        rc = rc2
        dev = fb

    logger.error(
        "%s başarısız (rc=%s, device=%s): %s",
        context,
        rc,
        dev,
        err[-500:] if err else "(stderr boş)",
    )
    raise RuntimeError(f"{context} rc={rc} device={dev}: {err[:200]}")


def play_audio_file(path: Path) -> None:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    cmd = _aplay_cmd_base()
    cmd.append(str(path))
    _run_aplay(cmd, context="aplay dosya")


def play_audio_wav_bytes(data: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        tmp = Path(f.name)
    try:
        play_audio_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def synthesize_openai_tts(text: str) -> bytes:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY tanımlı değil")
    client = _get_client()
    resp = client.audio.speech.create(
        model="tts-1",
        voice=config.TTS_VOICE,
        input=text,
        response_format="mp3",
    )
    return resp.content


def _find_piper_model() -> tuple[Path, Optional[Path]]:
    """Önce PIPER_MODEL_DIR, yoksa models/ kökünde düz .onnx (Rhasspy indirme düzeni)."""
    if config.PIPER_MODEL_PATH:
        p = Path(config.PIPER_MODEL_PATH).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"PIPER_MODEL_PATH dosyası bulunamadı: {p}")
        json_path = p.with_suffix(".onnx.json")
        if not json_path.is_file():
            parent = p.parent
            json_path = next(parent.glob("*.onnx.json"), None) or next(parent.glob("*.json"), None)
        return p, json_path if json_path and json_path.is_file() else None

    dirs = [config.PIPER_MODEL_DIR, config.MODELS_DIR]
    seen: set[Path] = set()
    search_dirs = []
    for d in dirs:
        d = Path(d).resolve()
        if d in seen:
            continue
        seen.add(d)
        search_dirs.append(d)

    onnx: list[Path] = []
    for d in search_dirs:
        if d.is_dir():
            onnx = sorted(d.glob("*.onnx"))
            if onnx:
                break
    if not onnx:
        raise FileNotFoundError(
            f"Piper .onnx bulunamadı. Şunlardan birine koyun: "
            f"{config.PIPER_MODEL_DIR} veya {config.MODELS_DIR}"
        )
    model = onnx[0]
    json_path = model.with_suffix(".onnx.json")
    if not json_path.is_file():
        parent = model.parent
        json_path = next(parent.glob("*.onnx.json"), None) or next(parent.glob("*.json"), None)
    return model, json_path if json_path and json_path.is_file() else None


def _piper_sample_rate() -> int:
    """Piper model JSON'dan sample rate oku (varsayılan 22050)."""
    try:
        _, json_path = _find_piper_model()
        if json_path and json_path.is_file():
            import json as _json
            data = _json.loads(json_path.read_text(encoding="utf-8"))
            return int(data.get("audio", {}).get("sample_rate", 22050))
    except Exception:
        pass
    return 22050


def _speak_piper_streaming(text: str, *, _allow_bt_fallback: bool = True) -> float:
    """
    Piper --output-raw → aplay doğrudan boru hattı.
    Sentez başlar başlamaz ses çalmaya başlar; geçici WAV dosyası yazmaz.
    WAV-dosyası yöntemine göre ~0.5–1s daha erken ses duyulur.
    """
    model, json_path = _find_piper_model()
    sr = _piper_sample_rate()

    cmd_piper = [config.PIPER_BINARY, "--model", str(model), "--output-raw"]
    if json_path and json_path.is_file():
        cmd_piper.extend(["--config", str(json_path)])

    cmd_aplay = _aplay_cmd_base(rate=sr, channels=1, raw=True)
    dev = _effective_output_device() or "(varsayılan)"

    t0 = time.perf_counter()
    p_piper: subprocess.Popen | None = None
    p_aplay: subprocess.Popen | None = None
    try:
        p_piper = subprocess.Popen(
            cmd_piper,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        p_aplay = subprocess.Popen(
            cmd_aplay,
            stdin=p_piper.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _register_child(p_piper)
        _register_child(p_aplay)
        # Parent'ın read-end referansını kapat; aplay sahiplensin, EOF doğru gelsin.
        assert p_piper.stdout is not None
        p_piper.stdout.close()

        assert p_piper.stdin is not None
        p_piper.stdin.write(text.encode("utf-8"))
        p_piper.stdin.close()

        try:
            rc_piper = _wait_proc(p_piper, timeout=30)
            rc_aplay = _wait_proc(p_aplay, timeout=30)
        finally:
            _unregister_child(p_piper)
            _unregister_child(p_aplay)

        if rc_piper != 0:
            stderr_b = b""
            if p_piper.stderr:
                try:
                    stderr_b = p_piper.stderr.read(800)
                except Exception:
                    pass
            err = stderr_b.decode("utf-8", errors="replace")
            raise RuntimeError(f"Piper streaming hatası (rc={rc_piper}): {err[:300]}")

        if rc_aplay != 0:
            if _allow_bt_fallback and (dev if dev != "(varsayılan)" else "").startswith("bluealsa"):
                fb = _speaker_fallback_device()
                logger.warning(
                    "Piper streaming bluealsa hatası, hoparlöre düşülüyor: %s",
                    fb,
                )
                set_output_device(fb)
                return _speak_piper_streaming(text, _allow_bt_fallback=False)
            err_b = b""
            if p_aplay.stderr:
                try:
                    err_b = p_aplay.stderr.read(800)
                except Exception:
                    pass
            err = err_b.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"aplay streaming hatası (rc={rc_aplay}, device={dev}): {err[:300]}"
            )
    except subprocess.TimeoutExpired:
        for p in (p_piper, p_aplay):
            if p is not None:
                try:
                    p.kill()
                except Exception:
                    pass
        raise RuntimeError("Piper streaming timeout")
    except TtsInterrupted:
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        for p in (p_piper, p_aplay):
            if p is not None:
                try:
                    p.kill()
                except Exception:
                    pass
        raise RuntimeError(f"Piper streaming genel hata: {exc}") from exc

    return time.perf_counter() - t0


def synthesize_piper_to_wav_file(text: str) -> Path:
    """
    Piper çıktısını bir WAV dosyasına yazar (yedek yol; normalde streaming kullanılır).
    """
    model, json_path = _find_piper_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = Path(f.name)
    cmd = [config.PIPER_BINARY, "--model", str(model), "--output_file", str(out)]
    if json_path and json_path.is_file():
        cmd.extend(["--config", str(json_path)])
    r = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        out.unlink(missing_ok=True)
        raise RuntimeError(f"Piper hatası: {err}")
    if not out.is_file() or out.stat().st_size < 100:
        out.unlink(missing_ok=True)
        raise RuntimeError("Piper WAV üretmedi (boş çıktı).")
    return out


def is_speaking() -> bool:
    return _speaking.is_set()


def speak(text: str, prefer_online: bool = True) -> tuple[str, float]:
    """
    Metni sese çevirip oynatır.
    Returns: ("openai-spruce" | "piper", süre_saniye)
    """
    t0 = time.perf_counter()
    clear_stop_flag()
    _speaking.set()
    try:
        return _speak_inner(text, prefer_online, t0)
    except TtsInterrupted:
        return "piper", time.perf_counter() - t0
    finally:
        _speaking.clear()


def _speak_inner(text: str, prefer_online: bool, t0: float) -> tuple[str, float]:
    # Online TTS: yalnızca anahtar VE internet varsa dene (kısa devre: anahtar yoksa hiç bakma)
    if prefer_online and config.OPENAI_API_KEY and internet_available():
        try:
            audio = synthesize_openai_tts(text)
            used = f"openai-{config.TTS_VOICE}"
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(audio)
                mp3 = Path(f.name)
            wav_path = mp3.with_suffix(".wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(mp3), str(wav_path), "-loglevel", "error"],
                    check=True,
                    capture_output=True,
                )
                play_audio_file(wav_path)
            finally:
                mp3.unlink(missing_ok=True)
                wav_path.unlink(missing_ok=True)
            duration = time.perf_counter() - t0
            return used, duration
        except Exception as e:
            logger.warning("Online TTS başarısız, Piper streaming'e geçiliyor: %s", e)

    # Piper streaming: --output-raw | aplay (dosyasız, ses daha erken başlar)
    try:
        duration = _speak_piper_streaming(text)
        return "piper", duration
    except Exception as e:
        logger.warning("Piper streaming başarısız, WAV dosyası yöntemine geçiliyor: %s", e)
        wav_path = synthesize_piper_to_wav_file(text)
        try:
            play_audio_file(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
        duration = time.perf_counter() - t0
        return "piper", duration


def speak_sentences(
    sentences: list[str],
    *,
    prefer_online: bool = True,
    pause_between: float | None = None,
) -> tuple[str, float]:
    """Cümleleri sırayla seslendirir (ilk cümle daha erken duyulur)."""
    pause = pause_between if pause_between is not None else float(getattr(config, "TTS_SENTENCE_PAUSE_SEC", 0.1))
    t0 = time.perf_counter()
    last_kind = "piper"
    _speaking.set()
    try:
        first = True
        for sent in sentences:
            s = sent.strip()
            if not s:
                continue
            if not first and pause > 0:
                time.sleep(pause)
            first = False
            last_kind, _ = _speak_inner(s, prefer_online, time.perf_counter())
    finally:
        _speaking.clear()
    return last_kind, time.perf_counter() - t0
