"""Merkezi ayarlar — yol haritası ROBOT_KANKA ile uyumlu."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = PROJECT_ROOT / "models"

PROFILE_PATH = DATA_DIR / "cihan_profile.json"
OFFLINE_RESPONSES_PATH = DATA_DIR / "offline_responses.json"
CONVERSATIONS_PATH = DATA_DIR / "last_conversations.txt"

WHISPER_CPP_DIR = Path(os.getenv("WHISPER_CPP_DIR", str(PROJECT_ROOT / "whisper.cpp")))
WHISPER_BINARY = Path(os.getenv("WHISPER_BINARY", str(WHISPER_CPP_DIR / "main")))
WHISPER_MODEL = Path(os.getenv("WHISPER_MODEL", str(WHISPER_CPP_DIR / "models" / "ggml-small.bin")))

PIPER_BINARY = os.getenv("PIPER_BINARY", "piper")
PIPER_MODEL_DIR = Path(os.getenv("PIPER_MODEL_DIR", str(MODELS_DIR / "tr_TR-ahmet-medium")))

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
SILENCE_END_SEC = float(os.getenv("SILENCE_END_SEC", "1.5"))
MAX_UTTERANCE_SEC = float(os.getenv("MAX_UTTERANCE_SEC", "30"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
TTS_VOICE = os.getenv("TTS_VOICE", "spruce")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "8"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "2"))

MAX_DAILY_REQUESTS = int(os.getenv("MAX_DAILY_REQUESTS", "500"))
REQUEST_COUNTER_FILE = LOGS_DIR / "daily_request_count.txt"

WAKE_PHRASES = tuple(
    p.strip().lower()
    for p in os.getenv("WAKE_PHRASES", "kanka,cihan,hey robot").split(",")
    if p.strip()
)

PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY", "")

# rhasspy/wyoming-openwakeword — ör. tcp://127.0.0.1:10400 (ayrı süreç veya Docker)
WYOMING_OPENWAKEWORD_URI = os.getenv("WYOMING_OPENWAKEWORD_URI", "").strip()

BASE_SYSTEM_PROMPT = """Sen "Kanka" adlı bir yapay zeka robottasın. Fiziksel varlığın var: tekerlekli bir robot gövdesi, iki kamera gözün ve sesle iletişim kuruyorsun.

Kullanıcın: Cihan
Ona "kanka" diye hitap edebilirsin.

Karakter:
- Samimi, yargılamayan, sakin bir dil kullan
- Kısa, sıcak ve net cümleler kur (uzun vaaz yok)
- Mizahı sever ama baskı yaratma
- Anksiyete/panik anlarında sakinleştirici ve kısa konuş
- "Komut okuyan robot" gibi değil, gerçek bir kanka gibi davran

Teknik kısıtlamalar:
- Cevapların maksimum 2-3 cümle olsun (sesli konuşma için ideal)
- Markdown, liste, başlık kullanma (ses olarak okunacak)
- Sadece düz, doğal Türkçe cümleler

Kanka modu örneği:
"Kanka buradayım. Yanındayım. Çay koyayım mı? Biraz gülmek ister misin? Bugün dünyayı kurtarmıyoruz, tamam mı?"
"""

STARTUP_PHRASE = os.getenv("STARTUP_PHRASE", "Hazırım Cihan.")
