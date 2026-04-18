"""TTS: OpenAI (çevrimiçi) veya Piper (çevrimdışı) + hoparlör oynatma."""
from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


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


def play_audio_file(path: Path) -> None:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    cmd = ["aplay", "-q"]
    if config.AUDIO_OUTPUT_ALSA_DEVICE:
        cmd.extend(["-D", config.AUDIO_OUTPUT_ALSA_DEVICE])
    cmd.append(str(path))
    r = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )
    # aplay normalde sessiz; debug için gerektiğinde stderr'i loglamak isteriz
    if r.stderr:
        logger.debug("aplay stderr: %s", r.stderr.decode("utf-8", errors="replace")[-500:])


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


def synthesize_piper_to_wav_file(text: str) -> Path:
    """
    Piper çıktısını bir WAV dosyasına yazar.
    Not: Piper pipe kullanımında genelde `--output-raw` gerekir; burada `--output_file` ile
    dosya üreterek oynatmayı stabil hale getiriyoruz.
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


def speak(text: str, prefer_online: bool = True) -> tuple[str, float]:
    """
    Metni sese çevirip oynatır.
    Returns: ("openai-spruce" | "piper", süre_saniye)
    """
    t0 = time.perf_counter()
    used = "piper"
    if prefer_online and internet_available() and config.OPENAI_API_KEY:
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
    else:
        wav_path = synthesize_piper_to_wav_file(text)
        try:
            play_audio_file(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
    duration = time.perf_counter() - t0
    return used, duration
