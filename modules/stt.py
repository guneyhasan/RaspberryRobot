"""Türkçe STT: bulut (Groq/OpenAI Whisper API) veya yerel whisper.cpp.

WHISPER_STT_BACKEND:
  groq   — Groq Whisper API
  openai — OpenAI Audio Transcriptions API
  server — yerel whisper-server (model bellekte)
  cli    — her seferinde whisper-cli
"""
from __future__ import annotations

import atexit
import json
import logging
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import numpy as np

import config
from modules import vad

logger = logging.getLogger(__name__)

_server_proc: subprocess.Popen[bytes] | None = None
_server_lock = threading.RLock()

_dialog_lock = threading.RLock()
_dialog_prompt_tail: str = ""


def clear_stt_dialogue_hint() -> None:
    """Konuşma modu kapandığında veya oturum başında Whisper metin ipucunu sıfırla."""
    global _dialog_prompt_tail
    with _dialog_lock:
        _dialog_prompt_tail = ""


def record_dialogue_turn_for_stt(user_text: str, assistant_text: str) -> None:
    """
    Son turu Whisper HTTP `prompt` alanına yansıtır.
    Model ağırlıkları güncellenmez; sadece bir sonraki transkripsiyonda bağlam ipucu verilir.
    """
    global _dialog_prompt_tail
    if not getattr(config, "WHISPER_STT_DIALOG_HINT", True):
        return
    u = " ".join((user_text or "").split())
    a = " ".join((assistant_text or "").split())
    if not u and not a:
        return
    chunk = f"Kullanıcı: {u}\nKanka: {a}"
    mx = max(32, int(getattr(config, "WHISPER_STT_PROMPT_MAX_CHARS", 200)))
    with _dialog_lock:
        _dialog_prompt_tail = chunk[:mx]
        logger.debug("STT: dialog prompt güncellendi (len=%s)", len(_dialog_prompt_tail))


def _uses_server() -> bool:
    return getattr(config, "WHISPER_STT_BACKEND", "cli") == "server"


def _uses_groq() -> bool:
    return getattr(config, "WHISPER_STT_BACKEND", "cli") == "groq"


def _uses_openai() -> bool:
    return getattr(config, "WHISPER_STT_BACKEND", "cli") == "openai"


def _uses_cloud_whisper() -> bool:
    return _uses_groq() or _uses_openai()


def _cloud_whisper_dialog_prompt() -> str:
    if not getattr(config, "WHISPER_STT_DIALOG_HINT", True):
        return ""
    with _dialog_lock:
        tail = _dialog_prompt_tail.strip()
    if not tail:
        return ""
    mx = max(32, int(getattr(config, "WHISPER_STT_PROMPT_MAX_CHARS", 200)))
    return tail[:mx]


def _build_cloud_whisper_multipart(wav_data: bytes, *, model: str, boundary_prefix: str) -> tuple[bytes, str]:
    boundary = f"----{boundary_prefix}{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    def _field(name: str, value: str) -> None:
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf)
        parts.append(value.encode("utf-8") + crlf)

    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(b'Content-Disposition: form-data; name="file"; filename="audio.wav"' + crlf)
    parts.append(b"Content-Type: audio/wav" + crlf + crlf)
    parts.append(wav_data + crlf)

    _field("model", model)
    _field("language", "tr")
    _field("response_format", "json")

    prompt = _cloud_whisper_dialog_prompt()
    if prompt:
        _field("prompt", prompt)

    parts.append(f"--{boundary}--".encode() + crlf)
    return b"".join(parts), boundary


def _transcribe_via_cloud_whisper_api(
    wav_path: Path,
    *,
    provider_label: str,
    api_key: str,
    model: str,
    url: str,
    timeout_sec: float,
    boundary_prefix: str,
) -> tuple[str, float]:
    """OpenAI uyumlu /audio/transcriptions endpoint (Groq veya OpenAI)."""
    if not api_key:
        raise RuntimeError(f"{provider_label} API anahtarı ayarlı değil")

    with open(wav_path, "rb") as f:
        wav_data = f.read()

    body, boundary = _build_cloud_whisper_multipart(wav_data, model=model, boundary_prefix=boundary_prefix)
    req = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "python-requests/2.31.0",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        logger.error("%s STT HTTP %s: %s", provider_label, e.code, err_body)
        raise RuntimeError(f"{provider_label} STT HTTP {e.code}: {err_body}") from e
    except URLError as e:
        logger.error("%s STT bağlantı hatası: %s", provider_label, e)
        raise RuntimeError(f"{provider_label} STT erişilemedi: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("%s STT JSON parse: %s | body=%s", provider_label, e, raw[:400])
        raise RuntimeError(f"{provider_label} STT geçersiz JSON") from e

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"{provider_label} STT: {data['error']}")

    text = (data.get("text") or "").strip() if isinstance(data, dict) else ""
    conf = 0.94 if text else 0.0
    return text, conf


def _transcribe_via_groq(wav_path: Path) -> tuple[str, float]:
    """Groq Whisper API ile hızlı bulut STT (~0.2–0.4s, yerel yerine)."""
    return _transcribe_via_cloud_whisper_api(
        wav_path,
        provider_label="Groq",
        api_key=getattr(config, "GROQ_API_KEY", ""),
        model=getattr(config, "GROQ_STT_MODEL", "whisper-large-v3-turbo"),
        url="https://api.groq.com/openai/v1/audio/transcriptions",
        timeout_sec=float(getattr(config, "GROQ_STT_TIMEOUT_SEC", 15.0)),
        boundary_prefix="grqSTT",
    )


def _transcribe_via_openai(wav_path: Path) -> tuple[str, float]:
    """OpenAI Audio Transcriptions API (whisper-1 vb.)."""
    return _transcribe_via_cloud_whisper_api(
        wav_path,
        provider_label="OpenAI",
        api_key=getattr(config, "OPENAI_API_KEY", ""),
        model=getattr(config, "OPENAI_STT_MODEL", "whisper-1"),
        url="https://api.openai.com/v1/audio/transcriptions",
        timeout_sec=float(getattr(config, "OPENAI_STT_TIMEOUT_SEC", 30.0)),
        boundary_prefix="oaiSTT",
    )


def _server_base() -> str:
    return getattr(config, "WHISPER_SERVER_BASE_URL", "http://127.0.0.1:8777").rstrip("/")


def _http_get_json(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except (URLError, HTTPError, json.JSONDecodeError, OSError) as e:
        logger.debug("STT server GET %s: %s", url, e)
        return None


def _wait_server_ready(timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    health = urljoin(_server_base() + "/", "health")
    while time.monotonic() < deadline:
        j = _http_get_json(health, timeout=min(5.0, max(0.5, deadline - time.monotonic())))
        if j and j.get("status") == "ok":
            return True
        time.sleep(0.15)
    return False


def _start_managed_server_locked() -> None:
    global _server_proc
    if not _uses_server():
        return
    if not getattr(config, "WHISPER_SERVER_SPAWN", True):
        return
    bin_path = getattr(config, "WHISPER_SERVER_BINARY", None)
    if not bin_path or not Path(bin_path).is_file():
        raise FileNotFoundError(f"whisper-server ikilisi yok: {bin_path}")
    if not config.WHISPER_MODEL.is_file():
        raise FileNotFoundError(f"Whisper model yok: {config.WHISPER_MODEL}")

    if _server_proc is not None and _server_proc.poll() is None:
        return

    if _server_proc is not None:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=5)
        except Exception:
            pass
        _server_proc = None

    cmd = [
        str(bin_path),
        "-m",
        str(config.WHISPER_MODEL),
        "-l",
        "tr",
        "-nt",
        "-t",
        str(config.WHISPER_THREADS),
        "--host",
        config.WHISPER_SERVER_HOST,
        "--port",
        str(config.WHISPER_SERVER_PORT),
    ]
    if getattr(config, "WHISPER_FAST_DECODE", True):
        # Açılış varsayılanları da hafif olsun; her istekte multipart ile tekrar gönderilir.
        cmd.extend(["-bo", "1", "-bs", "1", "-nf"])
    logger.info("STT: whisper-server başlatılıyor: %s", " ".join(cmd))
    _server_proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    ok = _wait_server_ready(config.WHISPER_SERVER_START_TIMEOUT_SEC)
    if not ok:
        err = ""
        if _server_proc.stderr:
            try:
                err = _server_proc.stderr.read(4000).decode("utf-8", errors="replace")
            except Exception:
                pass
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=3)
        except Exception:
            pass
        _server_proc = None
        raise RuntimeError(f"whisper-server hazır olmadı (health). stderr (ilk 4k): {err[:4000]}")


def ensure_whisper_backend_ready() -> None:
    """Uygulama açılışında veya ilk STT öncesi: server modunda süreç + model yüklemesi."""
    if _uses_groq():
        logger.info("STT: backend=groq (Groq Whisper API) — yerel sunucu başlatılmıyor")
        return
    if _uses_openai():
        if not config.OPENAI_API_KEY:
            raise RuntimeError("WHISPER_STT_BACKEND=openai ama OPENAI_API_KEY tanımlı değil")
        logger.info(
            "STT: backend=openai (OpenAI Whisper API, model=%s) — yerel sunucu başlatılmıyor",
            getattr(config, "OPENAI_STT_MODEL", "whisper-1"),
        )
        return
    if not _uses_server():
        return
    with _server_lock:
        if getattr(config, "WHISPER_SERVER_SPAWN", True):
            _start_managed_server_locked()
        elif not _wait_server_ready(3.0):
            raise RuntimeError(
                f"WHISPER_SERVER_SPAWN=0 ama {_server_base()}/health yanıt vermiyor. "
                "whisper-server'ı elle başlatın veya SPAWN=1 yapın."
            )


def shutdown_whisper_server_if_managed() -> None:
    global _server_proc
    with _server_lock:
        if _server_proc is None:
            return
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=8)
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
        _server_proc = None


atexit.register(shutdown_whisper_server_if_managed)


def _multipart_inference_body(wav_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----sttBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    def add_file(name: str, filename: str, content_type: str, data: bytes) -> None:
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode() + crlf
        )
        parts.append(f"Content-Type: {content_type}".encode() + crlf + crlf)
        parts.append(data + crlf)

    def add_field(name: str, value: str) -> None:
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf)
        parts.append(value.encode("utf-8") + crlf)

    add_file("file", "utterance.wav", "audio/wav", wav_bytes)
    add_field("language", "tr")
    add_field("response_format", "json")
    add_field("no_timestamps", "true")
    if getattr(config, "WHISPER_FAST_DECODE", True):
        add_field("temperature", "0.0")
        add_field("temperature_inc", "0.2")
        add_field("best_of", "1")
        add_field("beam_size", "1")
        if getattr(config, "WHISPER_STT_CARRY_CONTEXT", False):
            add_field("no_context", "false")
        else:
            add_field("no_context", "true")
    if getattr(config, "WHISPER_STT_DIALOG_HINT", True):
        with _dialog_lock:
            tail = _dialog_prompt_tail.strip()
        if tail:
            mx = max(32, int(getattr(config, "WHISPER_STT_PROMPT_MAX_CHARS", 200)))
            add_field("prompt", tail[:mx])
    parts.append(f"--{boundary}--".encode() + crlf)
    body = b"".join(parts)
    ctype = f"multipart/form-data; boundary={boundary}"
    return body, ctype


def _transcribe_via_server(wav_path: Path) -> tuple[str, float]:
    to = getattr(config, "WHISPER_SERVER_INFER_TIMEOUT_SEC", 120.0)
    with open(wav_path, "rb") as f:
        wav_data = f.read()
    body, ctype = _multipart_inference_body(wav_data)
    infer_url = urljoin(_server_base() + "/", "inference")
    req = Request(infer_url, data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urlopen(req, timeout=to) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        logger.error("whisper-server HTTP %s: %s", e.code, err_body)
        raise RuntimeError(f"whisper-server HTTP {e.code}: {err_body}") from e
    except URLError as e:
        logger.error("whisper-server bağlantı hatası: %s", e)
        raise RuntimeError(f"whisper-server erişilemedi: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("whisper-server JSON parse: %s | body=%s", e, raw[:500])
        raise RuntimeError("whisper-server geçersiz JSON") from e

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"whisper-server: {data.get('error')}")
    text = ""
    if isinstance(data, dict):
        text = (data.get("text") or "").strip()
    conf = 0.94 if text else 0.0
    return text, conf


def transcribe_pcm_cli(pcm_int16: np.ndarray, sample_rate: int | None = None) -> tuple[str, float]:
    """Her çağrıda whisper-cli alt süreci (model her seferinde yüklenir)."""
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
        if getattr(config, "WHISPER_FAST_DECODE", True):
            cmd.extend(["-bo", "1", "-bs", "1", "-nf"])
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


def transcribe_pcm(pcm_int16: np.ndarray, sample_rate: int | None = None) -> tuple[str, float]:
    """
    Bulut Whisper API (Groq/OpenAI), whisper-server (kalıcı model) veya whisper-cli.
    """
    global _server_proc
    sr = sample_rate or config.SAMPLE_RATE

    # ── Bulut Whisper API (Groq / OpenAI) ───────────────────────────────────
    if _uses_cloud_whisper():
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
        try:
            vad.save_wav_int16(wav_path, pcm_int16, sr)
            if _uses_groq():
                return _transcribe_via_groq(wav_path)
            return _transcribe_via_openai(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)

    # ── Yerel whisper-server / whisper-cli ──────────────────────────────────
    if not config.WHISPER_MODEL.is_file():
        raise FileNotFoundError(f"Whisper model yok: {config.WHISPER_MODEL}")

    if not _uses_server():
        return transcribe_pcm_cli(pcm_int16, sr)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)
    try:
        vad.save_wav_int16(wav_path, pcm_int16, sr)
        with _server_lock:
            if _server_proc is not None and _server_proc.poll() is not None:
                logger.warning("STT: whisper-server düşmüş, yeniden başlatılıyor")
                _server_proc = None
            if getattr(config, "WHISPER_SERVER_SPAWN", True):
                _start_managed_server_locked()
            elif not _wait_server_ready(0.5):
                raise RuntimeError(
                    f"whisper-server hazır değil: {_server_base()}/health — "
                    "WHISPER_SERVER_SPAWN=0 ise süreci elle başlatın."
                )
        return _transcribe_via_server(wav_path)
    finally:
        wav_path.unlink(missing_ok=True)


def _partial_on_audio(pcm: np.ndarray) -> None:
    """Deneysel: konuşma sırasında kısmi transkript → Whisper prompt ipucu."""
    if not getattr(config, "WHISPER_PARTIAL_HINT", False):
        return
    min_samples = int(0.8 * config.SAMPLE_RATE)
    if len(pcm) < min_samples:
        return
    try:
        text, _ = transcribe_pcm(pcm)
        text = " ".join((text or "").split())
        if not text:
            return
        mx = max(32, int(getattr(config, "WHISPER_STT_PROMPT_MAX_CHARS", 200)))
        with _dialog_lock:
            global _dialog_prompt_tail
            _dialog_prompt_tail = f"Kullanıcı: {text}"[:mx]
        logger.debug('STT partial hint: "%s"', text[:80])
    except Exception as e:
        logger.debug("STT partial atlandı: %s", e)


def _vad_on_partial():
    if getattr(config, "WHISPER_PARTIAL_HINT", False):
        return _partial_on_audio
    return None


def _transcribe_audio_segment(audio: np.ndarray, *, require_wake: bool) -> tuple[str, float]:
    from modules import wake_word

    if len(audio) < 1000:
        logger.info("STT: çok kısa segment (samples=%s) — atlandı", len(audio))
        return "", 0.0

    if require_wake:
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
    logger.info(
        'STT: whisper çıktı (elapsed=%0.1fs, conf=%.2f) text="%s"',
        t_tr1 - t_tr0,
        conf,
        " ".join(text.split())[:220],
    )
    return text, conf


def listen_for_speech_and_transcribe(
    timeout_sec: float,
    *,
    require_wake: bool = True,
    vad_threshold: float | None = None,
) -> tuple[str, float]:
    """Süre dolana kadar bekler; konuşma varsa transkribe eder."""
    t0 = time.perf_counter()
    audio = vad.listen_for_speech(
        timeout_sec,
        vad_threshold=vad_threshold,
        on_partial=_vad_on_partial(),
    )
    t1 = time.perf_counter()
    if audio is None:
        logger.info("STT: idle timeout / konuşma yok (elapsed=%0.1fs)", t1 - t0)
        return "", 0.0
    return _transcribe_audio_segment(audio, require_wake=require_wake)


def listen_and_transcribe(*, require_wake: bool = True) -> tuple[str, float]:
    """VAD → ses tabanlı wake (varsa) → whisper."""
    t0 = time.perf_counter()
    logger.info(
        "STT: dinleme başlıyor (backend=%s, sr=%s, vad_thr=%.2f, silence_end=%.1fs, max=%.1fs)",
        getattr(config, "WHISPER_STT_BACKEND", "cli"),
        config.SAMPLE_RATE,
        config.VAD_THRESHOLD,
        config.SILENCE_END_SEC,
        config.MAX_UTTERANCE_SEC,
    )
    audio = vad.record_utterance(on_partial=_vad_on_partial())
    t1 = time.perf_counter()
    if audio is None:
        logger.info("STT: VAD konuşma bulamadı (elapsed=%0.1fs)", t1 - t0)
        return "", 0.0
    logger.info(
        "STT: segment alındı (samples=%s, sec=%0.2f, elapsed=%0.1fs)",
        len(audio),
        len(audio) / float(config.SAMPLE_RATE),
        t1 - t0,
    )
    return _transcribe_audio_segment(audio, require_wake=require_wake)
