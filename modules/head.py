"""Robot-HAT ile kamera kafası (pan/tilt) servo kontrolü.

PiCar-X tipik eşleşme:
- P0: pan (sağ/sol)
- P1: tilt (yukarı/aşağı)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeadState:
    pan_deg: float
    tilt_deg: float


_pan = None
_tilt = None
_initialized = False
_state = HeadState(
    pan_deg=float(getattr(config, "HEAD_PAN_CENTER_DEG", 0.0)),
    tilt_deg=float(getattr(config, "HEAD_TILT_CENTER_DEG", 0.0)),
)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def is_available() -> bool:
    try:
        import robot_hat  # noqa: F401  # type: ignore

        return True
    except Exception:
        return False


def _init_if_needed() -> None:
    global _initialized, _pan, _tilt
    if _initialized:
        return

    if not bool(getattr(config, "HEAD_ENABLED", True)):
        logger.warning("Kafa servo devre dışı (HEAD_ENABLED=0).")
        _initialized = True
        return

    try:
        from robot_hat import Servo  # type: ignore

        pan_port = str(getattr(config, "HEAD_PAN_SERVO_PORT", "P0"))
        tilt_port = str(getattr(config, "HEAD_TILT_SERVO_PORT", "P1"))
        _pan = Servo(pan_port)
        _tilt = Servo(tilt_port)
        _initialized = True
        center()
        logger.info("Head hazır: pan=%s tilt=%s", pan_port, tilt_port)
    except Exception as e:
        _initialized = True
        _pan = None
        _tilt = None
        logger.warning("robot_hat head init başarısız: %s", e)


def _apply() -> None:
    if _pan is None or _tilt is None:
        return
    _pan.angle(float(_state.pan_deg))
    _tilt.angle(float(_state.tilt_deg))


def get_state() -> HeadState:
    return _state


def set_pan(deg: float) -> None:
    global _state
    _init_if_needed()
    lo = float(getattr(config, "HEAD_PAN_MIN_DEG", -90.0))
    hi = float(getattr(config, "HEAD_PAN_MAX_DEG", 90.0))
    d = float(_clamp(float(deg), lo, hi))
    _state = HeadState(pan_deg=d, tilt_deg=_state.tilt_deg)
    _apply()


def set_tilt(deg: float) -> None:
    global _state
    _init_if_needed()
    lo = float(getattr(config, "HEAD_TILT_MIN_DEG", -45.0))
    hi = float(getattr(config, "HEAD_TILT_MAX_DEG", 45.0))
    d = float(_clamp(float(deg), lo, hi))
    _state = HeadState(pan_deg=_state.pan_deg, tilt_deg=d)
    _apply()


def center() -> None:
    global _state
    _init_if_needed()
    pan = float(getattr(config, "HEAD_PAN_CENTER_DEG", 0.0))
    tilt = float(getattr(config, "HEAD_TILT_CENTER_DEG", 0.0))
    _state = HeadState(pan_deg=pan, tilt_deg=tilt)
    _apply()


def nudge(*, pan_delta: float = 0.0, tilt_delta: float = 0.0) -> None:
    """Mevcut pozisyona göre küçük adım."""
    _init_if_needed()
    set_pan(_state.pan_deg + float(pan_delta))
    set_tilt(_state.tilt_deg + float(tilt_delta))


def safe_center(reason: str = "") -> None:
    try:
        center()
        if reason:
            logger.info("Head safe_center: %s", reason)
    except Exception:
        logger.exception("Head safe_center başarısız")

