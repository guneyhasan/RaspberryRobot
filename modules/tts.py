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
    subprocess.run(
        ["aplay", "-q", str(path)],
        check=True,
        capture_output=True,
    )


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
    d = config.PIPER_MODEL_DIR
    onnx = list(d.glob("*.onnx"))
    if not onnx:
        raise FileNotFoundError(f"Piper .onnx bulunamadı: {d}")
    model = onnx[0]
    json_path = model.with_suffix(".onnx.json")
    if not json_path.is_file():
        json_path = next(d.glob("*.json"), None)
    return model, json_path


def synthesize_piper(text: str) -> bytes:
    model, json_path = _find_piper_model()
    cmd = [config.PIPER_BINARY, "--model", str(model)]
    if json_path and json_path.is_file():
        cmd.extend(["--config", str(json_path)])
    r = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Piper hatası: {err}")
    return r.stdout


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
        wav_bytes = synthesize_piper(text)
        play_audio_wav_bytes(wav_bytes)
    duration = time.perf_counter() - t0
    return used, duration
