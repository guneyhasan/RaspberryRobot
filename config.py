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

WHISPER_CPP_DIR = Path(os.getenv("WHISPER_CPP_DIR", str(PROJECT_ROOT / "whisper.cpp"))).resolve()


def _find_whisper_binary(base: Path) -> Path:
    """Eski `main` veya CMake `build/.../whisper-cli` (klasör yapısı sürüme göre değişir)."""
    env = os.getenv("WHISPER_BINARY", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
        # .env'de eski/hatalı yol varsa yok sayıp aşağıda otomatik ara
    rels = (
        "build/bin/whisper-cli",
        "build/bin/main",
        "main",
        "build/bin/whisper",
        "build/whisper-cli",
        "build/whisper",
        "build/Release/whisper-cli",
        "build/Debug/whisper-cli",
    )
    for rel in rels:
        c = (base / rel).resolve()
        if c.is_file():
            return c
    bdir = base / "build"
    if bdir.is_dir():
        candidates: list[Path] = []
        for name in ("whisper-cli", "whisper"):
            for p in bdir.rglob(name):
                if p.is_file() and p.name == name:
                    try:
                        if p.stat().st_size < 30_000:
                            continue
                    except OSError:
                        continue
                    candidates.append(p.resolve())
        if candidates:
            return min(candidates, key=lambda x: (len(x.parts), str(x)))
    return (base / "build" / "bin" / "whisper-cli").resolve()


def _find_whisper_model(base: Path) -> Path:
    env = os.getenv("WHISPER_MODEL", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    models_dir = base / "models"
    if models_dir.is_dir():
        for name in (
            "ggml-small.bin",
            "ggml-small-q5_0.bin",
            "ggml-small-q8_0.bin",
        ):
            c = (models_dir / name).resolve()
            if c.is_file():
                return c
    return (base / "models" / "ggml-small.bin").resolve()


WHISPER_BINARY = _find_whisper_binary(WHISPER_CPP_DIR)
WHISPER_MODEL = _find_whisper_model(WHISPER_CPP_DIR)
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "4"))

PIPER_BINARY = os.getenv("PIPER_BINARY", "piper")
PIPER_MODEL_DIR = Path(os.getenv("PIPER_MODEL_DIR", str(MODELS_DIR / "tr_TR-ahmet-medium")))
PIPER_MODEL_PATH = os.getenv("PIPER_MODEL_PATH", "").strip()

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
SILENCE_END_SEC = float(os.getenv("SILENCE_END_SEC", "1.5"))
MAX_UTTERANCE_SEC = float(os.getenv("MAX_UTTERANCE_SEC", "30"))

# sounddevice (PortAudio) input device selection.
# Example: AUDIO_INPUT_DEVICE="USB PnP Sound Device" or AUDIO_INPUT_DEVICE="2"
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "").strip()

# ALSA input device override (arecord -D). Example: "plughw:3,0"
# If set, VAD will read audio via `arecord` instead of PortAudio/sounddevice.
AUDIO_INPUT_ALSA_DEVICE = os.getenv("AUDIO_INPUT_ALSA_DEVICE", "").strip()

# ALSA output device override (aplay -D). Example: "hb" (from ~/.asoundrc) or "plughw:0,0"
# If empty, `aplay` uses the default ALSA device.
AUDIO_OUTPUT_ALSA_DEVICE = os.getenv("AUDIO_OUTPUT_ALSA_DEVICE", "").strip()

# Battery monitoring (Robot-HAT voltage → %).
# 2S Li-ion pack typical: 8.4V full, ~6.4V empty (under load değişir).
BATTERY_VOLTAGE_MIN = float(os.getenv("BATTERY_VOLTAGE_MIN", "6.4"))
BATTERY_VOLTAGE_MAX = float(os.getenv("BATTERY_VOLTAGE_MAX", "8.4"))
BATTERY_POLL_SEC = float(os.getenv("BATTERY_POLL_SEC", "20"))
BATTERY_CRITICAL_PERCENT = int(os.getenv("BATTERY_CRITICAL_PERCENT", "10"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
TTS_VOICE = os.getenv("TTS_VOICE", "spruce")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "8"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "2"))

# LLM provider selection:
# - If only one of OPENAI_API_KEY / GROQ_API_KEY is set → auto
# - If both are set → LLM_PROVIDER controls ("openai" or "groq"), default=openai
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_STREAM = os.getenv("GROQ_STREAM", "0").strip().lower() in ("1", "true", "yes", "on")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.6"))
GROQ_TOP_P = float(os.getenv("GROQ_TOP_P", "1"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()

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

# Debug veya geliştirme sırasında wake kelimesi zorunluluğunu kapatmak için:
# REQUIRE_WAKE_PHRASE=0 → "kanka" demeden de cevap verir.
REQUIRE_WAKE_PHRASE = os.getenv("REQUIRE_WAKE_PHRASE", "1").strip().lower() not in ("0", "false", "no", "off")

# Konuşma modu: "hey kanka" ile aç, "görüşürüz kanka" ile kapat.
CONVERSATION_ACTIVATE_PHRASES = tuple(
    p.strip().lower()
    for p in os.getenv("CONVERSATION_ACTIVATE_PHRASES", "hey kanka").split(",")
    if p.strip()
)
CONVERSATION_DEACTIVATE_PHRASES = tuple(
    p.strip().lower()
    for p in os.getenv("CONVERSATION_DEACTIVATE_PHRASES", "görüşürüz kanka,gorusuruz kanka").split(",")
    if p.strip()
)

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
- API anahtarı, sağlayıcı seçimi, bağlantı durumu, entegrasyon hatası var/yok gibi altyapı detaylarını kendiliğinden iddia etme.
- "Bağlanamadık, anahtar yok, sistem bozuk" gibi cümleler kurma. Böyle bir şeyden emin değilsen hiç bahsetme; kullanıcı sorarsa "kontrol edebilirim kanka" gibi kısa yanıt ver.

Kanka modu örneği:
"Kanka buradayım. Yanındayım. Çay koyayım mı? Biraz gülmek ister misin? Bugün dünyayı kurtarmıyoruz, tamam mı?"
"""

STARTUP_PHRASE = os.getenv("STARTUP_PHRASE", "Hazırım Cihan.")
