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
        print("Pil okunamadı.")
        print("")
        print("Hızlı teşhis:")
        print("- `robot_hat import failed: No module named ...` görüyorsanız, robot-hat'ın bağımlılığı eksiktir (örn. gpiozero).")
        print("- venv içindeyseniz, sistemde kurulu robot-hat/bağımlılıkları venv tarafından görünmüyor olabilir.")
        print("- SunFounder robot-hat'ı `sudo python3 setup.py install` ile kurduysanız,")
        print("  bu genelde sistem site-packages'e kurulur ve venv bunu görmeyebilir.")
        print("")
        print("Çözüm seçenekleri:")
        print("0) Eksik bağımlılığı kurun (bu hatada gpiozero):")
        print("   pip install gpiozero")
        print("")
        print("1) Venv'i sistem paketlerini görecek şekilde oluşturun:")
        print("   python3 -m venv --system-site-packages venv")
        print("")
        print("2) Robot-HAT'ı venv'e kurun (önerilen):")
        print("   pip install 'git+https://github.com/sunfounder/robot-hat.git'")
        print("")
        print("Debug için:")
        print("   python3 scripts/battery_test.py --debug")
        return 2
    print(f"voltage={r.voltage:.2f}V percent={r.percent}%")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true", help="Sürekli ölç (Ctrl+C ile çık)")
    p.add_argument("--interval", type=float, default=10.0, help="Watch aralığı (saniye)")
    p.add_argument("--debug", action="store_true", help="robot_hat import/attribute debug yazdır")
    args = p.parse_args()

    if args.debug:
        try:
            import robot_hat  # type: ignore

            print(f"robot_hat imported: {robot_hat!r}")
            try:
                from robot_hat import utils  # type: ignore

                print(f"robot_hat.utils imported: {utils!r}")
                print("has utils.get_battery_voltage:", hasattr(utils, "get_battery_voltage"))
                if hasattr(utils, "get_battery_voltage"):
                    print("utils.get_battery_voltage() ->", utils.get_battery_voltage())
            except Exception as e:
                print("robot_hat.utils import/call failed:", type(e).__name__, e)
        except Exception as e:
            print("robot_hat import failed:", type(e).__name__, e)
        print("")

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

