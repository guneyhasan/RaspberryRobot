#!/usr/bin/env python3
"""Robot Kanka — ana döngü: VAD → wake → STT → (intent/LLM) → TTS."""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from modules import camera, llm, memory, stt, tts, wake_word  # noqa: E402
from modules import health  # noqa: E402

logger = logging.getLogger("robot_kanka")


def setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.LOGS_DIR / "robot-kanka-app.log"
    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt))
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)


def _log_line(kind: str, body: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {kind}: {body}"
    logger.info(line)


def route_intents(text: str) -> str | None:
    mem_reply = memory.try_handle_memory_command(text)
    if mem_reply is not None:
        return mem_reply

    low = text.lower().strip()

    if any(
        p in low
        for p in (
            "gözlerini kapat",
            "gozlerini kapat",
            "gözlerini kapa",
            "gozlerini kapa",
        )
    ):
        camera.set_camera_enabled(False)
        return "Gözlerimi kapattım kanka."

    if any(p in low for p in ("gözlerini aç", "gozlerini ac", "gözlerini ac")):
        camera.set_camera_enabled(True)
        return "Gözlerimi açtım kanka."

    vision_triggers = (
        "önümde ne var",
        "onumde ne var",
        "önümde ne görüyorsun",
        "onumde ne goruyorsun",
        "etrafta ne var",
    )
    if low in ("bak", "bak.") or any(t in low for t in vision_triggers):
        if not config.OPENAI_API_KEY:
            return "Görmem için API anahtarı lazım kanka."
        try:
            if camera.camera_frozen():
                logger.warning("Kamera kilit şüphesi — yeniden denenecek.")
            return camera.look_and_describe()
        except Exception:
            logger.exception("Vision/kamera hatası")
            return "Kamerada sorun oldu kanka, bir daha dener misin?"

    return memory.try_handle_memory_command(text)


def run_loop() -> None:
    ok, msg = health.run_preflight()
    if not ok:
        logger.error("Preflight başarısız: %s", msg)
    else:
        logger.info("Preflight: %s", msg)

    try:
        tts.speak(config.STARTUP_PHRASE, prefer_online=False)
    except Exception as e:
        logger.warning("Açılış anonsu atlandı: %s", e)

    while True:
        try:
            text, conf = stt.listen_and_transcribe()
            if not text.strip():
                continue

            if not wake_word.audio_wake_enabled() and not wake_word.transcript_has_wake_phrase(text):
                logger.debug("Metin wake eşleşmedi, atlandı: %s", text)
                continue

            _log_line("HEARD", f"{text} | confidence: {conf:.2f}")

            reply: str | None = route_intents(text)
            if reply is None:
                if not tts.internet_available():
                    reply = memory.get_offline_response(text)
                if reply is None:
                    if not config.OPENAI_API_KEY:
                        reply = memory.get_offline_response(text) or "Şu an bağlantı veya anahtar yok kanka."
                    else:
                        try:
                            _log_line("SENT_TO_LLM", text)
                            sys_prompt = memory.build_system_prompt()
                            messages = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": text},
                            ]
                            reply = llm.ask_openai(messages)
                        except Exception as e:
                            logger.warning("LLM hatası: %s", e)
                            reply = (
                                memory.get_offline_response(text)
                                or "Bir saniye kanka, bağlantı yavaş."
                            )
                            if "limiti" in str(e).lower():
                                reply = "Günlük konuşma limitine yaklaştık kanka."

            assert reply is not None
            _log_line("RESPONSE", reply)

            memory.append_conversation_line("Kullanıcı", text)
            memory.append_conversation_line("Kanka", reply)

            t0 = time.perf_counter()
            try:
                kind, duration = tts.speak(reply, prefer_online=True)
            except Exception as e:
                logger.warning("TTS hatası, Piper deneniyor: %s", e)
                kind, duration = tts.speak(reply, prefer_online=False)
            _log_line("TTS", f"{kind} | duration: {duration:.1f}s | elapsed_since_heard: {time.perf_counter() - t0:.1f}s")

        except KeyboardInterrupt:
            logger.info("Kullanıcı durdurdu.")
            break
        except Exception:
            logger.exception("Ana döngü hatası")
            time.sleep(2)


def main() -> None:
    setup_logging()
    run_loop()


if __name__ == "__main__":
    main()
