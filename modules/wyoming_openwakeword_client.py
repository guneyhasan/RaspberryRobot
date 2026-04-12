"""
Wyoming protokolü ile rhasspy/wyoming-openwakeword sunucusuna bağlanır.
openWakeWord + tflite bu süreçte çalışır; Robot Kanka venv'inde sadece `wyoming` paketi gerekir.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


def _parse_wake_names() -> Optional[list[str]]:
    import os

    raw = os.getenv("WYOMING_WAKE_NAMES", "").strip()
    if not raw:
        return None
    names = [x.strip() for x in raw.split(",") if x.strip()]
    return names or None


async def _detect_async(pcm_bytes: bytes, sample_rate: int, uri: str) -> bool:
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncClient
    from wyoming.wake import Detect, Detection, NotDetected

    names = _parse_wake_names()
    client = AsyncClient.from_uri(uri)
    await client.connect()
    stop_pumping = asyncio.Event()
    detected = False

    async def pump_chunks() -> None:
        await client.write_event(Detect(names=names).event())
        await client.write_event(
            AudioStart(rate=sample_rate, width=2, channels=1, timestamp=0).event()
        )
        chunk_sz = 3200
        for i in range(0, len(pcm_bytes), chunk_sz):
            if stop_pumping.is_set():
                return
            sl = pcm_bytes[i : i + chunk_sz]
            await client.write_event(
                AudioChunk(
                    rate=sample_rate,
                    width=2,
                    channels=1,
                    audio=sl,
                    timestamp=None,
                ).event()
            )
            await asyncio.sleep(0)
        if not stop_pumping.is_set():
            await client.write_event(AudioStop().event())

    async def read_responses() -> None:
        nonlocal detected
        while True:
            ev = await client.read_event()
            if ev is None:
                return
            if Detection.is_type(ev.type):
                detected = True
                stop_pumping.set()
                return
            if NotDetected.is_type(ev.type):
                return

    try:
        await asyncio.gather(pump_chunks(), read_responses())
    finally:
        await client.disconnect()

    return detected


def wyoming_openwakeword_match(pcm_int16: np.ndarray, sample_rate: int) -> bool:
    """PCM int16 mono segmentini Wyoming openWakeWord sunucusuna gönderir."""
    uri = config.WYOMING_OPENWAKEWORD_URI
    if not uri:
        return False
    pcm_bytes = pcm_int16.astype(np.int16, copy=False).tobytes()
    if len(pcm_bytes) < 320:
        return False
    try:
        return asyncio.run(_detect_async(pcm_bytes, sample_rate, uri))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e).lower():
            logger.warning("Wyoming: asyncio.run iç içe — segment atlanıyor: %s", e)
            return False
        raise
    except Exception as e:
        logger.warning("Wyoming OpenWakeWord sunucu hatası: %s", e)
        return False
