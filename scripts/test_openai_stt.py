#!/usr/bin/env python3
"""OpenAI Whisper STT hız testi — Pi'de veya geliştirme makinesinde:
   python3 scripts/test_openai_stt.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

print(f"WHISPER_STT_BACKEND = {config.WHISPER_STT_BACKEND}")
print(f"OPENAI_STT_MODEL    = {config.OPENAI_STT_MODEL}")
print(
    f"OPENAI_API_KEY      = {'***' + config.OPENAI_API_KEY[-4:] if config.OPENAI_API_KEY else 'YOK!'}"
)

if not config.OPENAI_API_KEY:
    print("HATA: OPENAI_API_KEY .env'de yok. Ekleyin ve tekrar deneyin.")
    sys.exit(1)

if config.WHISPER_STT_BACKEND != "openai":
    print(f"UYARI: backend '{config.WHISPER_STT_BACKEND}' — OpenAI değil.")
    print("  .env'e WHISPER_STT_BACKEND=openai ekleyin veya OPENAI_STT_AUTO=1 yapın.")

test_wav = ROOT / "data" / "test_utterance.wav"
if not test_wav.is_file():
    print(f"HATA: test WAV yok: {test_wav}")
    print("  Önce kısa bir kayıt alın veya mevcut bir .wav dosyasını bu yola koyun.")
    sys.exit(1)

from modules.stt import _transcribe_via_openai  # noqa: E402

t0 = time.perf_counter()
try:
    text, conf = _transcribe_via_openai(test_wav)
    elapsed = time.perf_counter() - t0
    print(f"Süre: {elapsed:.2f}s | conf={conf:.2f}")
    print(f'Metin: "{text}"')
    if elapsed < 8 and text:
        print("✓ OpenAI STT çalışıyor.")
    elif text:
        print("✓ Metin alındı (süre yavaş olabilir — ağ/Pi).")
    else:
        print("✗ Boş metin döndü.")
        sys.exit(1)
except Exception as e:
    print(f"HATA: {e}")
    sys.exit(1)
