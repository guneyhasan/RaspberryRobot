"""Robot-HAT ile hareket kontrolü (motor + direksiyon servo).

Bu modül iki amaçla var:
- Robot-HAT kütüphanesi mevcutsa gerçek donanımı sürmek
- Geliştirme/CI ortamında robot_hat yoksa "soft fail" yapıp sistemi çalışır tutmak
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
import os
from typing import Optional, Protocol

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MotionState:
    throttle: int  # -100..100
    steering_deg: float  # servo açı hedefi


class _DriveBackend(Protocol):
    def set_speed(self, speed: int) -> None: ...

    def stop(self) -> None: ...


_drive: Optional[_DriveBackend] = None
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
    global _initialized, _drive, _servo, _state
    if _initialized:
        return

    if not bool(getattr(config, "MOTION_ENABLED", True)):
        logger.warning("Hareket devre dışı (MOTION_ENABLED=0).")
        _initialized = True
        return

    try:
        from robot_hat import Servo  # type: ignore

        servo_port = str(getattr(config, "STEERING_SERVO_PORT", "P0"))
        _servo = Servo(servo_port)

        # Sürüş backend seçimi:
        # 1) Robot-HAT v4 docs: `Motors()` ve motors[1].speed(...)
        # 2) Bazı sürümlerde `Motor()` + wheel(speed, id)
        # 3) Bazı sürümlerde `Motor(pwm, dir)` ile tek motor nesnesi (sol/sağ ayrı)

        class _MotorsBackend:
            def __init__(self) -> None:
                # robot_hat==? bazı sürümlerde robot_hat.motor.Motors içinde `owner=User`
                # kullanılıyor ama `User` globali tanımlı değil (NameError).
                # Burada modül içine User enjekte ederek Motors()'u çalışır hale getiriyoruz.
                import robot_hat.motor as motor_mod  # type: ignore

                if not hasattr(motor_mod, "User"):
                    setattr(motor_mod, "User", os.getenv("USER", "pi"))

                from robot_hat import Motors  # type: ignore

                self._motors = Motors(db=str(getattr(config, "MOTORS_DB_PATH", "/tmp/robot_hat_motors.config")))
                self._l = int(getattr(config, "DRIVE_MOTOR_LEFT", 1))
                self._r = int(getattr(config, "DRIVE_MOTOR_RIGHT", 2))

            def set_speed(self, speed: int) -> None:
                self._motors[self._l].speed(speed)
                self._motors[self._r].speed(speed)

            def stop(self) -> None:
                self._motors.stop()

        class _WheelBackend:
            def __init__(self) -> None:
                from robot_hat import Motor  # type: ignore

                self._motor = Motor()
                self._l = int(getattr(config, "DRIVE_MOTOR_LEFT", 0))
                self._r = int(getattr(config, "DRIVE_MOTOR_RIGHT", 1))

            def set_speed(self, speed: int) -> None:
                self._motor.wheel(speed, self._l)
                self._motor.wheel(speed, self._r)

            def stop(self) -> None:
                self._motor.wheel(0, self._l)
                self._motor.wheel(0, self._r)

        class _PinBackend:
            def __init__(self) -> None:
                from robot_hat import Motor, PWM, Pin  # type: ignore

                lpwm = str(getattr(config, "DRIVE_LEFT_PWM", "")).strip()
                ldir = str(getattr(config, "DRIVE_LEFT_DIR", "")).strip()
                rpwm = str(getattr(config, "DRIVE_RIGHT_PWM", "")).strip()
                rdir = str(getattr(config, "DRIVE_RIGHT_DIR", "")).strip()
                if not (lpwm and ldir and rpwm and rdir):
                    raise RuntimeError(
                        "Motor(pwm, dir) backend için DRIVE_LEFT_PWM/DRIVE_LEFT_DIR/DRIVE_RIGHT_PWM/DRIVE_RIGHT_DIR gerekli."
                    )
                # Bu sürümde Motor, pwm için PWM nesnesi bekliyor.
                self._left = Motor(PWM(lpwm), Pin(ldir))
                self._right = Motor(PWM(rpwm), Pin(rdir))

            def set_speed(self, speed: int) -> None:
                # Bu API genelde speed(-100..100) veya speed(-1..1) olabilir.
                # Robot-HAT tarafında ölçek 0..100 yaygın; biz -100..100 gönderiyoruz.
                if hasattr(self._left, "speed"):
                    self._left.speed(speed)
                    self._right.speed(speed)
                else:
                    # En kötü ihtimal: wheel benzeri
                    self._left.wheel(speed)
                    self._right.wheel(speed)

            def stop(self) -> None:
                self.set_speed(0)

        backend_errs: list[str] = []
        _drive = None
        # Öncelik:
        # - PiCar-X'te en deterministik yol: Motor(pwm, dir) (config'te varsayılan pinler var)
        # - Motors() bazı kurulumlarda /opt/robot_hat yazma izni ister → PermissionError
        # - Wheel backend yalnızca Motor() destekleyen sürümlerde çalışır
        for ctor in (_PinBackend, _MotorsBackend, _WheelBackend):
            try:
                _drive = ctor()
                break
            except Exception as e:
                backend_errs.append(f"{ctor.__name__}: {type(e).__name__}: {e}")

        if _drive is None:
            raise RuntimeError("Drive backend seçilemedi: " + " | ".join(backend_errs))

        # İlk güvenli durum
        steering_center = float(getattr(config, "STEERING_CENTER_DEG", 0.0))
        _state = MotionState(throttle=0, steering_deg=steering_center)
        _apply_state()

        _initialized = True
        logger.info("Motion hazır: drive_backend=%s + Servo(%s)", type(_drive).__name__ if _drive else "none", servo_port)
    except Exception as e:
        _initialized = True
        _drive = None
        _servo = None
        logger.warning("robot_hat motion init başarısız: %s", e)


def _apply_state() -> None:
    """Mevcut _state'i donanıma uygular (varsa)."""
    if _drive is None or _servo is None:
        return

    throttle = int(_clamp(float(_state.throttle), -100.0, 100.0))
    _drive.set_speed(throttle)

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

