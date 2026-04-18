#!/usr/bin/env python3
"""Kurulu robot_hat sürümünü ve API'yi hızlıca probe eder.

Pi üzerinde (venv içinde) çalıştır:
  python3 scripts/robot_hat_probe.py
"""

from __future__ import annotations

import inspect
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _try(label: str, fn):
    print(f"\n== {label} ==")
    try:
        return fn()
    except Exception as e:
        print("ERROR:", type(e).__name__, e)
        traceback.print_exc(limit=8)
        return None


def main() -> None:
    rh = _try("import robot_hat", lambda: __import__("robot_hat"))
    if rh is None:
        return
    print("robot_hat module:", rh)
    print("robot_hat file:", getattr(rh, "__file__", None))
    print("robot_hat attrs sample:", sorted([a for a in dir(rh) if a[0].isupper()])[:40])

    def motor_sig():
        from robot_hat import Motor  # type: ignore

        print("Motor:", Motor)
        try:
            print("Motor.__init__ signature:", inspect.signature(Motor.__init__))
        except Exception:
            print("Motor.__init__ signature: <unavailable>")
        try:
            print("Motor signature:", inspect.signature(Motor))
        except Exception:
            print("Motor signature: <unavailable>")

    _try("Motor signature", motor_sig)

    def motors_try():
        from robot_hat import Motors  # type: ignore

        print("Motors:", Motors)
        try:
            print("Motors.__init__ signature:", inspect.signature(Motors.__init__))
        except Exception:
            print("Motors.__init__ signature: <unavailable>")
        m = Motors()
        print("Motors() instance:", m)
        return m

    _try("Motors() init", motors_try)

    def servo_sig():
        from robot_hat import Servo  # type: ignore

        print("Servo:", Servo)
        try:
            print("Servo.__init__ signature:", inspect.signature(Servo.__init__))
        except Exception:
            print("Servo.__init__ signature: <unavailable>")

    _try("Servo signature", servo_sig)


if __name__ == "__main__":
    main()

