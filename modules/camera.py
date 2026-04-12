"""libcamera ile görüntü alma ve OpenAI Vision."""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_camera_enabled = True
_last_capture_ts = 0.0


def set_camera_enabled(on: bool) -> None:
    global _camera_enabled
    _camera_enabled = on
    logger.info("Kamera %s", "açık" if on else "kapalı")


def is_camera_enabled() -> bool:
    return _camera_enabled


def capture_image(path: Optional[Path] = None) -> Path:
    if not _camera_enabled:
        raise RuntimeError("Kamera kapalı — önce gözlerini aç.")
    if path is not None:
        out = path
    else:
        fd, name = tempfile.mkstemp(suffix=".jpg", prefix="kanka_cam_")
        os.close(fd)
        out = Path(name)
    r = subprocess.run(
        [
            "libcamera-still",
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
        logger.error("libcamera-still hata: %s", err)
        raise RuntimeError(f"Kamera çekimi başarısız: {err[:300]}")
    if not out.is_file() or out.stat().st_size < 100:
        raise RuntimeError("Boş veya geçersiz görüntü dosyası.")
    return out


def camera_frozen() -> bool:
    """Basit sezgisel: son başarılı çekim çok eskiyse donmuş olabilir."""
    if _last_capture_ts <= 0:
        return False
    return (time.time() - _last_capture_ts) > 300 and _camera_enabled


def look_and_describe(prompt: str | None = None) -> str:
    """Fotoğraf çek ve vision modeli ile Türkçe kısa açıklama."""
    from modules import llm as llm_mod

    llm_mod.ensure_daily_quota()
    img_path = capture_image()
    try:
        data = img_path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
        mime = "image/jpeg"
        url = f"data:{mime};base64,{b64}"
        client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.TIMEOUT_SECONDS)
        user_prompt = prompt or "Bu fotoğrafta ne var? Türkçe kısa açıkla."
        resp = client.chat.completions.create(
            model=config.VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            max_tokens=300,
        )
        llm_mod.bump_request_count()
        return (resp.choices[0].message.content or "").strip()
    finally:
        img_path.unlink(missing_ok=True)
