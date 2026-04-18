#!/usr/bin/env python3
"""Robot-HAT pil testi: voltaj + yüzde.

Kullanım:
  python3 scripts/battery_test.py
  python3 scripts/battery_test.py --watch
  python3 scripts/battery_test.py --watch --interval 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import battery  # noqa: E402


def print_once() -> int:
    r = battery.read_battery()
    if r is None:
        print("Pil okunamadı (robot_hat utils get_battery_voltage çalışmıyor olabilir).")
        return 2
    print(f"voltage={r.voltage:.2f}V percent={r.percent}%")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true", help="Sürekli ölç (Ctrl+C ile çık)")
    p.add_argument("--interval", type=float, default=10.0, help="Watch aralığı (saniye)")
    args = p.parse_args()

    if not args.watch:
        return print_once()

    try:
        while True:
            rc = print_once()
            if rc != 0:
                return rc
            time.sleep(max(0.5, float(args.interval)))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

