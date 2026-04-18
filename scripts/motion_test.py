#!/usr/bin/env python3
"""Robot-HAT hareket hızlı testi.

Kullanım (Pi üzerinde):
  source venv/bin/activate
  python3 scripts/motion_test.py
"""

from __future__ import annotations

import time

from modules import motion


def main() -> None:
    print("robot_hat available:", motion.is_available())
    print("STOP")
    motion.safe_stop("test_start")
    time.sleep(0.5)

    print("FORWARD 1.0s")
    motion.drive_for(throttle=55, steering=0.0, seconds=1.0)
    time.sleep(0.5)

    print("LEFT 0.8s")
    motion.drive_for(throttle=55, steering=-25.0, seconds=0.8)
    time.sleep(0.5)

    print("RIGHT 0.8s")
    motion.drive_for(throttle=55, steering=25.0, seconds=0.8)
    time.sleep(0.5)

    print("BACKWARD 1.0s")
    motion.drive_for(throttle=-45, steering=0.0, seconds=1.0)

    print("DONE / STOP")
    motion.safe_stop("test_end")


if __name__ == "__main__":
    main()

