#!/usr/bin/env python3
"""Tek kare çek; isteğe bağlı OpenAI vision (OPENAI_API_KEY gerekir)."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import camera  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", type=Path, default=Path("/tmp/kanka_camera_test.jpg"))
    p.add_argument("--vision", action="store_true", help="OpenAI ile kısa Türkçe açıklama")
    args = p.parse_args()

    path = camera.capture_image(args.output)
    print(f"Kaydedildi: {path}")
    if args.vision:
        try:
            desc = camera.look_and_describe()
            print(desc)
        except Exception as e:
            print(f"Vision hatası: {e}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
