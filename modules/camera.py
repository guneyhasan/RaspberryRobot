"""libcamera ile görüntü alma ve OpenAI Vision."""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_camera_enabled = False   # Başlangıçta kapalı; "gözlerini aç" komutu ile açılır
_last_capture_ts = 0.0
_camera_open_ts: float = 0.0    # Kamera en son ne zaman açıldı
_last_vision_ts: float = 0.0    # En son "bak/ne görüyorsun" komutu ne zaman işlendi


def set_camera_enabled(on: bool) -> None:
    global _camera_enabled, _camera_open_ts
    _camera_enabled = on
    if on:
        _camera_open_ts = time.time()
    logger.info("Kamera %s", "açık" if on else "kapalı")


def seconds_since_opened() -> float:
    """Kamera şu an açıksa açılalı kaç saniye geçti."""
    if not _camera_enabled or _camera_open_ts <= 0:
        return 0.0
    return time.time() - _camera_open_ts


def record_vision_event() -> None:
    """Her başarılı görüntü yorumlamasından sonra çağrılır."""
    global _last_vision_ts
    _last_vision_ts = time.time()


def seconds_since_last_vision() -> float:
    """Son vision komutundan bu yana kaç saniye geçti (hiç yoksa inf)."""
    if _last_vision_ts <= 0:
        return float("inf")
    return time.time() - _last_vision_ts


def is_camera_enabled() -> bool:
    return _camera_enabled


def _still_binary() -> str:
    """Yeni imajlarda rpicam-still, eskilerde libcamera-still bulunur."""
    for name in ("rpicam-still", "libcamera-still"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError(
        "Kamera aracı bulunamadı (rpicam-still / libcamera-still). "
        "Kurulum: sudo apt update && sudo apt install -y rpicam-apps "
        "(veya: libcamera-apps)"
    )


def capture_image(path: Optional[Path] = None) -> Path:
    if not _camera_enabled:
        raise RuntimeError("Kamera kapalı — önce gözlerini aç.")
    if path is not None:
        out = path
    else:
        fd, name = tempfile.mkstemp(suffix=".jpg", prefix="kanka_cam_")
        os.close(fd)
        out = Path(name)
    still = _still_binary()
    r = subprocess.run(
        [
            still,
            "-o",
            str(out),
            "--immediate",
            "--nopreview",
            "-n",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    global _last_capture_ts
    _last_capture_ts = time.time()
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        logger.error("%s hata: %s", still, err)
        raise RuntimeError(f"Kamera çekimi başarısız: {err[:300]}")
    if not out.is_file() or out.stat().st_size < 100:
        raise RuntimeError("Boş veya geçersiz görüntü dosyası.")
    return out


def camera_frozen() -> bool:
    """Basit sezgisel: son başarılı çekim çok eskiyse donmuş olabilir."""
    if _last_capture_ts <= 0:
        return False
    return (time.time() - _last_capture_ts) > 300 and _camera_enabled


def _vision_messages(b64: str, user_prompt: str) -> list:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]


def look_and_describe(prompt: str | None = None) -> str:
    """Fotoğraf çek ve vision modeli ile Türkçe kısa açıklama.
    Önce Groq vision, yoksa OpenAI vision kullanılır.
    """
    from modules import llm as llm_mod

    llm_mod.ensure_daily_quota()
    img_path = capture_image()
    try:
        data = img_path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
        user_prompt = prompt or "Bu fotoğrafta ne var? Türkçe, 1-2 kısa cümleyle açıkla."
        messages = _vision_messages(b64, user_prompt)

        # ── Groq vision (önce dene) ──────────────────────────────────────────
        if config.GROQ_API_KEY:
            try:
                from groq import Groq
                groq_vision_model = getattr(config, "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
                client_groq = Groq(api_key=config.GROQ_API_KEY)
                resp = client_groq.chat.completions.create(
                    model=groq_vision_model,
                    messages=messages,
                    max_tokens=300,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    llm_mod.bump_request_count()
                    record_vision_event()
                    logger.info("Vision: Groq (%s) kullanıldı", groq_vision_model)
                    return text
            except Exception as e:
                logger.warning("Groq vision başarısız, OpenAI'ye geçiliyor: %s", e)

        # ── OpenAI vision (yedek) ────────────────────────────────────────────
        if config.OPENAI_API_KEY:
            client_oai = OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.TIMEOUT_SECONDS)
            resp = client_oai.chat.completions.create(
                model=config.VISION_MODEL,
                messages=messages,
                max_tokens=300,
            )
            llm_mod.bump_request_count()
            record_vision_event()
            logger.info("Vision: OpenAI (%s) kullanıldı", config.VISION_MODEL)
            return (resp.choices[0].message.content or "").strip()

        raise RuntimeError("Vision için ne Groq ne de OpenAI anahtarı var.")
    finally:
        img_path.unlink(missing_ok=True)
