#!/usr/bin/env python3
"""Groq Whisper STT hız testi — Pi'de çalıştırın:
   python3 scripts/test_groq_stt.py
"""
import sys
import time
from pathlib import Path

# Proje köküne ekle
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

print(f"WHISPER_STT_BACKEND = {config.WHISPER_STT_BACKEND}")
print(f"GROQ_STT_MODEL      = {config.GROQ_STT_MODEL}")
print(f"GROQ_API_KEY        = {'***' + config.GROQ_API_KEY[-4:] if config.GROQ_API_KEY else 'YOK!'}")
print()

if not config.GROQ_API_KEY:
    print("HATA: GROQ_API_KEY .env'de yok. Ekleyin ve tekrar deneyin.")
    sys.exit(1)

if config.WHISPER_STT_BACKEND != "groq":
    print(f"UYARI: backend '{config.WHISPER_STT_BACKEND}' — Groq değil.")
    print("  .env'e WHISPER_STT_BACKEND=groq ekleyin ya da GROQ_API_KEY ayarlayın.")

# 1 saniyelik sessiz test sesi oluştur
import numpy as np
import tempfile
from modules import vad as vad_mod

sr = 16000
silence = np.zeros(sr, dtype=np.int16)  # 1s sessizlik
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    test_wav = Path(f.name)
vad_mod.save_wav_int16(test_wav, silence, sr)

print("Sessizlik testi (API erişimi kontrolü)...")
t0 = time.perf_counter()
from modules.stt import _transcribe_via_groq
try:
    text, conf = _transcribe_via_groq(test_wav)
    elapsed = time.perf_counter() - t0
    print(f"  Süre: {elapsed:.2f}s | Metin: '{text}' | Conf: {conf:.2f}")
    print()
    if elapsed < 2.0:
        print("✓ Groq STT çalışıyor ve hızlı!")
    else:
        print("⚠ API yanıtı yavaş, internet bağlantısını kontrol edin.")
except Exception as e:
    print(f"HATA: {e}")
finally:
    test_wav.unlink(missing_ok=True)

print()
print("Gerçek mikrofon testi (3s kayıt + transkript)...")
print("Hazır olunca bir şeyler söyleyin...")
from modules.stt import listen_and_transcribe

t0 = time.perf_counter()
text, conf = listen_and_transcribe(require_wake=False)
total = time.perf_counter() - t0

print(f"  Toplam süre: {total:.2f}s")
print(f"  Metin: '{text}'")
print(f"  Güven: {conf:.2f}")
