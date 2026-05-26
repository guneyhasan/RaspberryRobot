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

def _env_str(key: str, default: str = "") -> str:
    """Boş string'i (KEY=) 'unset' gibi ele al."""
    v = os.getenv(key)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


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
    # Hız önceliği (Türkçe için base tipik sweet spot); yoksa small/tiny düşer.
    if models_dir.is_dir():
        for name in (
            "ggml-base-q5_0.bin",
            "ggml-base-q5_1.bin",
            "ggml-base-q8_0.bin",
            "ggml-base.bin",
            "ggml-tiny-q8_0.bin",
            "ggml-tiny-q5_1.bin",
            "ggml-tiny.bin",
            "ggml-small-q5_0.bin",
            "ggml-small-q8_0.bin",
            "ggml-small.bin",
        ):
            c = (models_dir / name).resolve()
            if c.is_file():
                return c
    return (base / "models" / "ggml-base.bin").resolve()


WHISPER_BINARY = _find_whisper_binary(WHISPER_CPP_DIR)
WHISPER_MODEL = _find_whisper_model(WHISPER_CPP_DIR)


def _default_whisper_threads() -> int:
    raw = os.getenv("WHISPER_THREADS", "").strip()
    if raw:
        return max(1, int(raw))
    n = os.cpu_count() or 4
    return max(1, min(n, 8))


WHISPER_THREADS = _default_whisper_threads()


def _find_whisper_server_binary(base: Path) -> Path:
    """Kalıcı model için `whisper-server` (examples/server)."""
    env = os.getenv("WHISPER_SERVER_BINARY", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    for rel in (
        "build/bin/whisper-server",
        "build/bin/server",
    ):
        c = (base / rel).resolve()
        if c.is_file():
            return c
    bdir = base / "build"
    if bdir.is_dir():
        for p in bdir.rglob("whisper-server"):
            if p.is_file():
                try:
                    if p.stat().st_size < 30_000:
                        continue
                except OSError:
                    continue
                return p.resolve()
    return (base / "build" / "bin" / "whisper-server").resolve()


WHISPER_SERVER_BINARY = _find_whisper_server_binary(WHISPER_CPP_DIR)


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    s = v.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def _default_whisper_stt_backend() -> str:
    """
    groq:   Groq Whisper API (~0.3s, internet gerekli, GROQ_API_KEY şart)
    server: yerel whisper-server (model bellekte kalır, ~3-6s Pi'de)
    cli:    her seferinde whisper-cli başlatır (en yavaş)

    Öncelik: 1) WHISPER_STT_BACKEND env'i 2) groq key varsa groq 3) server binary varsa server 4) cli
    """
    explicit = os.getenv("WHISPER_STT_BACKEND", "").strip().lower()
    if explicit in ("groq", "server", "cli"):
        return explicit
    # GROQ_API_KEY varsa otomatik olarak Groq Whisper API kullan (çok daha hızlı)
    if os.getenv("GROQ_API_KEY", "").strip():
        return "groq"
    return "server" if WHISPER_SERVER_BINARY.is_file() else "cli"


WHISPER_STT_BACKEND = _default_whisper_stt_backend()
WHISPER_SERVER_SPAWN = _env_bool("WHISPER_SERVER_SPAWN", True)
WHISPER_SERVER_HOST = _env_str("WHISPER_SERVER_HOST", "127.0.0.1")
WHISPER_SERVER_PORT = int(os.getenv("WHISPER_SERVER_PORT", "8777"))
WHISPER_SERVER_START_TIMEOUT_SEC = float(os.getenv("WHISPER_SERVER_START_TIMEOUT_SEC", "180"))
WHISPER_SERVER_INFER_TIMEOUT_SEC = float(os.getenv("WHISPER_SERVER_INFER_TIMEOUT_SEC", "120"))
WHISPER_SERVER_BASE_URL = _env_str(
    "WHISPER_SERVER_BASE_URL",
    f"http://{WHISPER_SERVER_HOST}:{WHISPER_SERVER_PORT}",
).rstrip("/")

# Sunucu + HTTP inference: düşük gecikme (robot); kapatırsanız whisper varsayılanları (daha ağır).
WHISPER_FAST_DECODE = _env_bool("WHISPER_FAST_DECODE", True)
# Son kullanıcı + robot cümlesini Whisper "prompt" ile gönderir (ağırlık öğrenmesi değil; bağlam ipucu).
WHISPER_STT_DIALOG_HINT = _env_bool("WHISPER_STT_DIALOG_HINT", True)
WHISPER_STT_PROMPT_MAX_CHARS = int(os.getenv("WHISPER_STT_PROMPT_MAX_CHARS", "200"))
# Decoder önceki tur bağlamını taşır; biraz daha ağır olabilir. Hız öncelikliyse 0.
WHISPER_STT_CARRY_CONTEXT = _env_bool("WHISPER_STT_CARRY_CONTEXT", False)

PIPER_BINARY = _env_str("PIPER_BINARY", "piper")
PIPER_MODEL_DIR = Path(_env_str("PIPER_MODEL_DIR", str(MODELS_DIR / "tr_TR-ahmet-medium")))
PIPER_MODEL_PATH = _env_str("PIPER_MODEL_PATH", "")

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
SILENCE_END_SEC = float(os.getenv("SILENCE_END_SEC", "0.7"))
MAX_UTTERANCE_SEC = float(os.getenv("MAX_UTTERANCE_SEC", "30"))
# Çok kısa gürültü segmentlerini at (saniye).
VAD_MIN_SPEECH_SEC = float(os.getenv("VAD_MIN_SPEECH_SEC", "0.25"))
# Konuşma yeterince uzunsa sessizlik eşiğini düşür (erken endpointing).
VAD_ADAPTIVE_ENDPOINTING = _env_bool("VAD_ADAPTIVE_ENDPOINTING", True)
VAD_ADAPTIVE_MIN_SPEECH_SEC = float(os.getenv("VAD_ADAPTIVE_MIN_SPEECH_SEC", "0.5"))
VAD_ADAPTIVE_SILENCE_SEC = float(os.getenv("VAD_ADAPTIVE_SILENCE_SEC", "0.35"))

# Konuşma modu: sessizlik yoklaması
CONVERSATION_NUDGE_ENABLED = _env_bool("CONVERSATION_NUDGE_ENABLED", True)
CONVERSATION_NUDGE_SEC = float(os.getenv("CONVERSATION_NUDGE_SEC", "8"))
CONVERSATION_NUDGE_COOLDOWN_SEC = float(os.getenv("CONVERSATION_NUDGE_COOLDOWN_SEC", "25"))

# LLM / TTS streaming
OPENAI_STREAM = _env_bool("OPENAI_STREAM", True)
LLM_STREAM_ENABLED = _env_bool("LLM_STREAM_ENABLED", True)
LLM_STREAM_MIN_SENTENCE_CHARS = int(os.getenv("LLM_STREAM_MIN_SENTENCE_CHARS", "12"))
TTS_SENTENCE_PAUSE_SEC = float(os.getenv("TTS_SENTENCE_PAUSE_SEC", "0.1"))
TTS_PREFER_PIPER_FOR_NUDGE = _env_bool("TTS_PREFER_PIPER_FOR_NUDGE", True)

# Opsiyonel kısmi STT (deneysel, varsayılan kapalı)
WHISPER_PARTIAL_HINT = _env_bool("WHISPER_PARTIAL_HINT", False)
WHISPER_PARTIAL_INTERVAL_SEC = float(os.getenv("WHISPER_PARTIAL_INTERVAL_SEC", "1.2"))

# sounddevice (PortAudio) input device selection.
# Example: AUDIO_INPUT_DEVICE="USB PnP Sound Device" or AUDIO_INPUT_DEVICE="2"
AUDIO_INPUT_DEVICE = _env_str("AUDIO_INPUT_DEVICE", "")

# ALSA input device override (arecord -D). Example: "plughw:3,0"
# If set, VAD will read audio via `arecord` instead of PortAudio/sounddevice.
# Kart numarası reboot sonrası değişebilir; AUTO_DETECT açıkken alternatifler denenir.
AUDIO_INPUT_ALSA_DEVICE = _env_str("AUDIO_INPUT_ALSA_DEVICE", "")
AUDIO_INPUT_AUTO_DETECT = _env_bool("AUDIO_INPUT_AUTO_DETECT", True)

# Ana döngü: konuşma başlaması için bekleme (saniye). arecord anında kapanırsa yine hızlı SKIP olur.
LISTEN_WAIT_SEC = float(os.getenv("LISTEN_WAIT_SEC", "30"))
# Mikrofon/arecord hatasında CPU döngüsünü önlemek için kısa bekleme (saniye).
LISTEN_FAST_FAIL_BACKOFF_SEC = float(os.getenv("LISTEN_FAST_FAIL_BACKOFF_SEC", "0.35"))
# arecord stdout okuma zaman aşımı (saniye); idle timeout'un çalışması için gerekli.
VAD_ARECORD_READ_TIMEOUT_SEC = float(os.getenv("VAD_ARECORD_READ_TIMEOUT_SEC", "0.25"))

# ALSA output device override (aplay -D). Example: "hb" (from ~/.asoundrc) or "plughw:0,0"
# If empty, `aplay` uses the default ALSA device.
# Boş = ALSA varsayılanı. Robot-HAT için install.sh ~/.asoundrc pcm.hb → "hb"
AUDIO_OUTPUT_ALSA_DEVICE = _env_str("AUDIO_OUTPUT_ALSA_DEVICE", "")

# Bluetooth kulaklık modu (bluealsa + bluetoothctl)
BLUETOOTH_ENABLED = _env_bool("BLUETOOTH_ENABLED", True)
BLUETOOTH_SCAN_SEC = float(os.getenv("BLUETOOTH_SCAN_SEC", "12"))
BLUETOOTH_CONNECT_TIMEOUT_SEC = float(os.getenv("BLUETOOTH_CONNECT_TIMEOUT_SEC", "25"))
BLUETOOTH_A2DP_WAIT_SEC = float(os.getenv("BLUETOOTH_A2DP_WAIT_SEC", "35"))
# Kapatırken adaptörü power off yapma (yeniden açılışta A2DP/PCM daha güvenilir)
BLUETOOTH_POWER_OFF_ON_CLOSE = _env_bool("BLUETOOTH_POWER_OFF_ON_CLOSE", False)
BLUETOOTH_ADAPTER_SETTLE_SEC = float(os.getenv("BLUETOOTH_ADAPTER_SETTLE_SEC", "1.5"))
BLUETOOTH_RECONNECT_GAP_SEC = float(os.getenv("BLUETOOTH_RECONNECT_GAP_SEC", "2"))
BLUETOOTH_RESTART_BLUEALSA_ON_RECONNECT = _env_bool("BLUETOOTH_RESTART_BLUEALSA_ON_RECONNECT", True)
BLUETOOTH_BT_CONNECT_WAIT_SEC = float(os.getenv("BLUETOOTH_BT_CONNECT_WAIT_SEC", "15"))
BLUETOOTH_ACTIVE_SCAN_SEC = float(os.getenv("BLUETOOTH_ACTIVE_SCAN_SEC", "4"))
BLUETOOTH_LIST_BARGE_IN = _env_bool("BLUETOOTH_LIST_BARGE_IN", True)
BLUETOOTH_BARGE_IN_LISTEN_SEC = float(os.getenv("BLUETOOTH_BARGE_IN_LISTEN_SEC", "2.0"))
BLUETOOTH_A2DP_PROFILE = _env_str("BLUETOOTH_A2DP_PROFILE", "a2dp")
BLUETOOTH_LIST_PAIRED_ONLY = _env_bool("BLUETOOTH_LIST_PAIRED_ONLY", False)
# 0 = taramada bulunan tüm cihazları sesli listele; >0 üst sınır
BLUETOOTH_MAX_LIST = int(os.getenv("BLUETOOTH_MAX_LIST", "0"))
BLUETOOTH_AUTO_CONNECT_PAIRED = _env_bool("BLUETOOTH_AUTO_CONNECT_PAIRED", True)
BLUETOOTH_PAIR_TIMEOUT_SEC = float(os.getenv("BLUETOOTH_PAIR_TIMEOUT_SEC", "60"))
BLUETOOTH_PAIR_SCAN_SEC = float(os.getenv("BLUETOOTH_PAIR_SCAN_SEC", "5"))
# pair öncesi hedef MAC taramada görünene kadar bekleme (saniye)
BLUETOOTH_PAIR_TARGET_WAIT_SEC = float(os.getenv("BLUETOOTH_PAIR_TARGET_WAIT_SEC", "30"))
BLUETOOTH_KILL_STALE_CTL = _env_bool("BLUETOOTH_KILL_STALE_CTL", False)
BLUETOOTH_AGENT_DAEMON = _env_bool("BLUETOOTH_AGENT_DAEMON", True)
# Boş bırakılırsa AUDIO_OUTPUT_ALSA_DEVICE kullanılır (ör. plughw:0,0)
BLUETOOTH_SPEAKER_ALSA_DEVICE = _env_str("BLUETOOTH_SPEAKER_ALSA_DEVICE", "")

# Battery monitoring (Robot-HAT voltage → %).
# 2S Li-ion pack typical: 8.4V full, ~6.4V empty (under load değişir).
BATTERY_VOLTAGE_MIN = float(os.getenv("BATTERY_VOLTAGE_MIN", "6.4"))
BATTERY_VOLTAGE_MAX = float(os.getenv("BATTERY_VOLTAGE_MAX", "8.4"))
BATTERY_POLL_SEC = float(os.getenv("BATTERY_POLL_SEC", "20"))
BATTERY_CRITICAL_PERCENT = int(os.getenv("BATTERY_CRITICAL_PERCENT", "10"))

# Motion (Robot-HAT motor/servo)
# Notlar:
# - SunFounder robot-hat Motor.wheel(speed, motor_id) genelde -100..100 bekler (motor_id: 0/1)
# - Servo.angle(deg) -90..90 aralığında çalışır
MOTION_ENABLED = os.getenv("MOTION_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
DRIVE_MOTOR_LEFT = int(os.getenv("DRIVE_MOTOR_LEFT", "1"))
DRIVE_MOTOR_RIGHT = int(os.getenv("DRIVE_MOTOR_RIGHT", "2"))
# Bazı robot_hat sürümlerinde Motor(pwm, dir) imzası var. O durumda pin/portları doldurun.
# Örnek formatlar sürüme göre değişebilir: "P12", "P13" veya BCM numarası gibi.
# PiCar-X (SunFounder) için yaygın varsayılanlar (picar-x v2.0 kaynaklarına göre):
# - Sol arka:  DIR=D4, PWM=P13
# - Sağ arka: DIR=D5, PWM=P12
DRIVE_LEFT_PWM = _env_str("DRIVE_LEFT_PWM", "P13")
DRIVE_LEFT_DIR = _env_str("DRIVE_LEFT_DIR", "D4")
DRIVE_RIGHT_PWM = _env_str("DRIVE_RIGHT_PWM", "P12")
DRIVE_RIGHT_DIR = _env_str("DRIVE_RIGHT_DIR", "D5")
# Motors() config dosyası (/opt yerine yazılabilir yer)
MOTORS_DB_PATH = _env_str("MOTORS_DB_PATH", str(DATA_DIR / "robot_hat_motors.config"))
# PiCar-X v2.0'da tipik servo kanalları:
# - P0: kamera pan, P1: kamera tilt, P2: direksiyon
STEERING_SERVO_PORT = _env_str("STEERING_SERVO_PORT", "P2")  # ör: P0..P11
STEERING_CENTER_DEG = float(os.getenv("STEERING_CENTER_DEG", "0"))
STEERING_MIN_DEG = float(os.getenv("STEERING_MIN_DEG", "-30"))
STEERING_MAX_DEG = float(os.getenv("STEERING_MAX_DEG", "30"))
DEFAULT_DRIVE_THROTTLE = int(os.getenv("DEFAULT_DRIVE_THROTTLE", "55"))
DEFAULT_TURN_DEG = float(os.getenv("DEFAULT_TURN_DEG", "25"))
DEFAULT_MOVE_SECONDS = float(os.getenv("DEFAULT_MOVE_SECONDS", "1.0"))

# Head (kamera kafası pan/tilt)
HEAD_ENABLED = os.getenv("HEAD_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
HEAD_PAN_SERVO_PORT = _env_str("HEAD_PAN_SERVO_PORT", "P0")
HEAD_TILT_SERVO_PORT = _env_str("HEAD_TILT_SERVO_PORT", "P1")
HEAD_PAN_CENTER_DEG = float(os.getenv("HEAD_PAN_CENTER_DEG", "0"))
HEAD_TILT_CENTER_DEG = float(os.getenv("HEAD_TILT_CENTER_DEG", "0"))
HEAD_PAN_MIN_DEG = float(os.getenv("HEAD_PAN_MIN_DEG", "-90"))
HEAD_PAN_MAX_DEG = float(os.getenv("HEAD_PAN_MAX_DEG", "90"))
HEAD_TILT_MIN_DEG = float(os.getenv("HEAD_TILT_MIN_DEG", "-45"))
HEAD_TILT_MAX_DEG = float(os.getenv("HEAD_TILT_MAX_DEG", "45"))
HEAD_NUDGE_DEG = float(os.getenv("HEAD_NUDGE_DEG", "20"))

OPENAI_API_KEY = _env_str("OPENAI_API_KEY", "")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
TTS_VOICE = os.getenv("TTS_VOICE", "spruce")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "100"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "8"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "2"))

# LLM provider selection:
# - If only one of OPENAI_API_KEY / GROQ_API_KEY is set → auto
# - If both are set → LLM_PROVIDER controls ("openai" or "groq"), default=openai
GROQ_API_KEY = _env_str("GROQ_API_KEY", "")
GROQ_MODEL = _env_str("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_STREAM = os.getenv("GROQ_STREAM", "1").strip().lower() in ("1", "true", "yes", "on")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.6"))
GROQ_TOP_P = float(os.getenv("GROQ_TOP_P", "1"))
LLM_PROVIDER = _env_str("LLM_PROVIDER", "").lower()

# Groq Whisper STT ayarları
# whisper-large-v3-turbo: hızlı + Türkçe destekli (önerilen)
# whisper-large-v3:       daha doğru ama biraz yavaş
GROQ_STT_MODEL = _env_str("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_STT_TIMEOUT_SEC = float(os.getenv("GROQ_STT_TIMEOUT_SEC", "15.0"))

# Groq vision modeli (görüntü yorumlama)
# llama-4-scout hızlı ve ücretsiz tier'da çalışır
GROQ_VISION_MODEL = _env_str("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

MAX_DAILY_REQUESTS = int(os.getenv("MAX_DAILY_REQUESTS", "500"))
REQUEST_COUNTER_FILE = LOGS_DIR / "daily_request_count.txt"

WAKE_PHRASES = tuple(
    p.strip().lower()
    for p in os.getenv("WAKE_PHRASES", "kanka,cihan,hey robot").split(",")
    if p.strip()
)

PICOVOICE_ACCESS_KEY = _env_str("PICOVOICE_ACCESS_KEY", "")

# rhasspy/wyoming-openwakeword — ör. tcp://127.0.0.1:10400 (ayrı süreç veya Docker)
WYOMING_OPENWAKEWORD_URI = _env_str("WYOMING_OPENWAKEWORD_URI", "")

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
- En fazla 1-2 kısa cümle; gereksiz açıklama yok
- Aynı kalıbı ve cümleyi tekrarlama; her seferinde biraz farklı ifade et
- Gündelik, sözlü Türkçe (resmi dil değil)
- Markdown, liste, başlık kullanma (ses olarak okunacak)
- Sadece düz, doğal Türkçe cümleler
- API anahtarı, sağlayıcı seçimi, bağlantı durumu, entegrasyon hatası var/yok gibi altyapı detaylarını kendiliğinden iddia etme.
- "Bağlanamadık, anahtar yok, sistem bozuk" gibi cümleler kurma. Böyle bir şeyden emin değilsen hiç bahsetme; kullanıcı sorarsa "kontrol edebilirim kanka" gibi kısa yanıt ver.

Kanka modu örneği:
"Kanka buradayım. Yanındayım. Çay koyayım mı? Biraz gülmek ister misin? Bugün dünyayı kurtarmıyoruz, tamam mı?"
"""

# Kamera: komut olmaksızın bu süre (dakika) açık kalırsa otomatik kapat
CAMERA_AUTO_CLOSE_MIN = float(os.getenv("CAMERA_AUTO_CLOSE_MIN", "25"))

STARTUP_PHRASE = os.getenv("STARTUP_PHRASE", "Hazırım Cihan.")
