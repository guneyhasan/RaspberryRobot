"""Robot-HAT ile hareket kontrolü (motor + direksiyon servo).

Bu modül iki amaçla var:
- Robot-HAT kütüphanesi mevcutsa gerçek donanımı sürmek
- Geliştirme/CI ortamında robot_hat yoksa "soft fail" yapıp sistemi çalışır tutmak
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MotionState:
    throttle: int  # -100..100
    steering_deg: float  # servo açı hedefi


_motor = None
_servo = None
_initialized = False
_state = MotionState(throttle=0, steering_deg=float(getattr(config, "STEERING_CENTER_DEG", 0.0)))


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def is_available() -> bool:
    """robot_hat import edilebiliyor mu?"""
    try:
        import robot_hat  # noqa: F401  # type: ignore

        return True
    except Exception:
        return False


def _init_if_needed() -> None:
    global _initialized, _motor, _servo, _state
    if _initialized:
        return

    if not bool(getattr(config, "MOTION_ENABLED", True)):
        logger.warning("Hareket devre dışı (MOTION_ENABLED=0).")
        _initialized = True
        return

    try:
        from robot_hat import Motor, Servo  # type: ignore

        _motor = Motor()
        servo_port = str(getattr(config, "STEERING_SERVO_PORT", "P0"))
        _servo = Servo(servo_port)

        # İlk güvenli durum
        steering_center = float(getattr(config, "STEERING_CENTER_DEG", 0.0))
        _state = MotionState(throttle=0, steering_deg=steering_center)
        _apply_state()

        _initialized = True
        logger.info("Motion hazır: Motor() + Servo(%s)", servo_port)
    except Exception as e:
        _initialized = True
        _motor = None
        _servo = None
        logger.warning("robot_hat motion init başarısız: %s", e)


def _apply_state() -> None:
    """Mevcut _state'i donanıma uygular (varsa)."""
    if _motor is None or _servo is None:
        return

    throttle = int(_clamp(float(_state.throttle), -100.0, 100.0))
    left = int(getattr(config, "DRIVE_MOTOR_LEFT", 0))
    right = int(getattr(config, "DRIVE_MOTOR_RIGHT", 1))

    # robot_hat Motor.wheel(speed, motor_id) bekler. motor_id: 0/1
    _motor.wheel(throttle, left)
    _motor.wheel(throttle, right)

    # robot_hat Servo.angle(deg) bekler. docs: -90..90
    _servo.angle(float(_state.steering_deg))


def stop() -> None:
    """Motorları durdur, direksiyonu merkeze al."""
    global _state
    _init_if_needed()
    center = float(getattr(config, "STEERING_CENTER_DEG", 0.0))
    _state = MotionState(throttle=0, steering_deg=center)
    _apply_state()


def set_throttle(percent: int) -> None:
    """-100..100 arası gaz."""
    global _state
    _init_if_needed()
    pct = int(_clamp(float(percent), -100.0, 100.0))
    _state = MotionState(throttle=pct, steering_deg=_state.steering_deg)
    _apply_state()


def set_steering(deg: float) -> None:
    """Direksiyon servo açısı. Varsayılan sınır config'ten gelir."""
    global _state
    _init_if_needed()
    lo = float(getattr(config, "STEERING_MIN_DEG", -35.0))
    hi = float(getattr(config, "STEERING_MAX_DEG", 35.0))
    d = float(_clamp(float(deg), lo, hi))
    _state = MotionState(throttle=_state.throttle, steering_deg=d)
    _apply_state()


def drive_for(*, throttle: int, steering: float, seconds: float) -> None:
    """Belirli süre sür (bloklar), sonra dur."""
    _init_if_needed()
    set_steering(steering)
    set_throttle(throttle)
    time.sleep(max(0.0, float(seconds)))
    stop()


def safe_stop(reason: str = "") -> None:
    """Hata durumlarında çağrılabilecek stop; exception fırlatmaz."""
    try:
        stop()
        if reason:
            logger.info("Motion safe_stop: %s", reason)
    except Exception:
        logger.exception("Motion safe_stop başarısız")

