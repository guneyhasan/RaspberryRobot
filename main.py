#!/usr/bin/env python3
"""Robot Kanka — ana döngü: VAD → wake → STT → (intent/LLM) → TTS."""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from modules import battery, camera, llm, memory, motion, stt, tts, wake_word  # noqa: E402
from modules import health  # noqa: E402

logger = logging.getLogger("robot_kanka")


def setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.LOGS_DIR / "robot-kanka-app.log"
    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    root = logging.getLogger()
    level_name = os.getenv("LOG_LEVEL", "INFO").upper().strip()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
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


def _trace_id(seq: int) -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{seq:05d}"


def _fmt_ms(sec: float) -> str:
    return f"{sec * 1000:.0f}ms"


def _safe_preview(text: str, limit: int = 220) -> str:
    t = " ".join((text or "").strip().split())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def _has_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    def norm(s: str) -> str:
        # casefold: Türkçe I/İ gibi harflerde daha güvenilir
        s = (s or "").casefold()
        # noktalama/emoji vs temizle
        s = re.sub(r"[^0-9a-zA-Zçğıöşü\s]", " ", s, flags=re.UNICODE)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    t = norm(text or "")
    if not phrases:
        return False
    for p in phrases:
        if not p:
            continue
        if norm(p) in t:
            return True
    return False


def route_intents(text: str) -> str | None:
    mem_reply = memory.try_handle_memory_command(text)
    if mem_reply is not None:
        return mem_reply

    low = text.lower().strip()

    # Hareket komutları (LLM'e gitmeden)
    # Not: Güvenlik için kısa süreli hareket (DEFAULT_MOVE_SECONDS) şeklinde ele alıyoruz.
    # "dur" komutu her zaman anında durdurur.
    if any(w in low for w in ("dur", "stop", "bekle", "kapan", "fren")):
        motion.safe_stop("voice_command_stop")
        return "Tamam kanka, durdum."

    # Basit yön komutları: ileri/geri/sağ/sol
    forward_triggers = (
        "ileri git",
        "ileri",
        "yürü",
        "yuru",
        "gaza bas",
        "devam et",
    )
    backward_triggers = (
        "geri gel",
        "geri git",
        "geri",
        "kaç",
        "kac",
    )
    left_triggers = ("sola dön", "sola don", "sol", "sola")
    right_triggers = ("sağa dön", "saga dön", "saga don", "sağa don", "sağ", "sag", "sağa", "saga")

    move_sec = float(getattr(config, "DEFAULT_MOVE_SECONDS", 1.0))
    throttle = int(getattr(config, "DEFAULT_DRIVE_THROTTLE", 55))
    turn_deg = float(getattr(config, "DEFAULT_TURN_DEG", 25.0))

    if any(t in low for t in forward_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=float(getattr(config, "STEERING_CENTER_DEG", 0.0)), seconds=move_sec)
        return "Tamam kanka."

    if any(t in low for t in backward_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=-abs(throttle), steering=float(getattr(config, "STEERING_CENTER_DEG", 0.0)), seconds=move_sec)
        return "Tamam kanka."

    if any(t in low for t in left_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=-abs(turn_deg), seconds=move_sec)
        return "Tamam kanka."

    if any(t in low for t in right_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=abs(turn_deg), seconds=move_sec)
        return "Tamam kanka."

    # Pil durumu soruları (LLM'e gitmeden direkt yanıt)
    battery_triggers = (
        "pilin kaç",
        "pil kac",
        "pil yüzde",
        "pil yuzde",
        "şarjın kaç",
        "sarjin kac",
        "şarj kaç",
        "sarj kac",
        "şarjım kaç",
        "sarjim kac",
        "şarjım ne kadar",
        "sarjim ne kadar",
        "ne kadar şarjın kaldı",
        "ne kadar sarjin kaldi",
    )
    if any(t in low for t in battery_triggers):
        r = battery.get_cached_reading(max_age_sec=120.0) or battery.read_battery()
        if r is None:
            return "Şu an pil seviyesini okuyamadım kanka."
        return f"Kanka şarjım yüzde {r.percent}. Voltajım da {r.voltage:.2f} volt."

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

    logger.info(
        "Wake ayarları: audio_wake=%s | require_wake_phrase=%s | wake_phrases=%s",
        wake_word.audio_wake_enabled(),
        config.REQUIRE_WAKE_PHRASE,
        ", ".join(config.WAKE_PHRASES) if config.WAKE_PHRASES else "(boş)",
    )
    logger.info(
        "Konuşma modu tetikleri: activate=%s | deactivate=%s",
        ", ".join(config.CONVERSATION_ACTIVATE_PHRASES) if config.CONVERSATION_ACTIVATE_PHRASES else "(boş)",
        ", ".join(config.CONVERSATION_DEACTIVATE_PHRASES) if config.CONVERSATION_DEACTIVATE_PHRASES else "(boş)",
    )

    try:
        tts.speak(config.STARTUP_PHRASE, prefer_online=False)
    except Exception as e:
        logger.warning("Açılış anonsu atlandı: %s", e)

    # Pil izleme thread'i (Robot-HAT voltajından % hesaplar)
    import threading
    import subprocess

    stop_batt = threading.Event()

    def _batt_drop(percent: int, voltage: float) -> None:
        msg = f"Kanka şarjım yüzde {percent}. Bir ara şarja takar mısın?"
        _log_line("BATTERY", f"drop_10pct | {percent}% | {voltage:.2f}V | announce")
        try:
            tts.speak(msg, prefer_online=False)
        except Exception as e:
            logger.warning("Pil uyarı TTS atlandı: %s", e)

    def _batt_critical(percent: int, voltage: float) -> None:
        msg = "Kanka şarjım bitmek üzere. Beni şarja takar mısın? Kapanıyorum."
        _log_line("BATTERY", f"critical | {percent}% | {voltage:.2f}V | poweroff")
        try:
            tts.speak(msg, prefer_online=False)
        except Exception as e:
            logger.warning("Kritik pil TTS atlandı: %s", e)
        try:
            motion.safe_stop("battery_critical")
        except Exception:
            pass
        try:
            subprocess.run(["sudo", "poweroff"], check=False)
        except Exception as e:
            logger.error("poweroff çalıştırılamadı: %s", e)

    batt_th = threading.Thread(
        target=battery.monitor_loop,
        kwargs={"on_drop_10pct": _batt_drop, "on_critical": _batt_critical, "stop_event": stop_batt},
        daemon=True,
        name="battery-monitor",
    )
    batt_th.start()

    seq = 0
    conversation_mode = False
    while True:
        seq += 1
        tid = _trace_id(seq)
        _log_line(
            "LOOP",
            f"{tid} | dinleme başladı (VAD → wake → STT → route → TTS) | conversation_mode={'on' if conversation_mode else 'off'}",
        )
        try:
            t_listen0 = time.perf_counter()
            try:
                text, conf = stt.listen_and_transcribe()
            except Exception as e:
                _log_line("STT_ERROR", f"{tid} | listen_and_transcribe exception: {type(e).__name__}: {e}")
                raise
            t_listen1 = time.perf_counter()

            if not text.strip():
                _log_line("SKIP", f"{tid} | konuşma yok / wake gate geçmedi | listen_total={_fmt_ms(t_listen1 - t_listen0)}")
                continue

            _log_line(
                "STT",
                f'{tid} | text="{_safe_preview(text)}" | confidence={conf:.2f} | total={_fmt_ms(t_listen1 - t_listen0)}',
            )

            # Konuşma modu: "hey kanka" ile aç, "görüşürüz kanka" ile kapat.
            if not conversation_mode and _has_any_phrase(text, config.CONVERSATION_ACTIVATE_PHRASES):
                conversation_mode = True
                _log_line("MODE", f"{tid} | conversation_mode=on | trigger=activate")
                reply = "Buradayım kanka. Dinliyorum."
                _log_line("RESPONSE", reply)
                try:
                    kind, duration = tts.speak(reply, prefer_online=True)
                except Exception as e:
                    _log_line("TTS_ERR", f"{tid} | prefer_online failed: {type(e).__name__}: {e}")
                    kind, duration = tts.speak(reply, prefer_online=False)
                _log_line("TTS", f"{tid} | {kind} | synth+play={duration:.1f}s | text_len={len(reply)}")
                continue

            if conversation_mode and _has_any_phrase(text, config.CONVERSATION_DEACTIVATE_PHRASES):
                conversation_mode = False
                _log_line("MODE", f"{tid} | conversation_mode=off | trigger=deactivate")
                reply = "Görüşürüz kanka."
                _log_line("RESPONSE", reply)
                try:
                    kind, duration = tts.speak(reply, prefer_online=True)
                except Exception as e:
                    _log_line("TTS_ERR", f"{tid} | prefer_online failed: {type(e).__name__}: {e}")
                    kind, duration = tts.speak(reply, prefer_online=False)
                _log_line("TTS", f"{tid} | {kind} | synth+play={duration:.1f}s | text_len={len(reply)}")
                continue

            # Konuşma modu kapalıysa wake zorunluluğu uygula; mod açıksa direkt devam et.
            if not conversation_mode:
                if not wake_word.audio_wake_enabled() and config.REQUIRE_WAKE_PHRASE:
                    ok_transcript = wake_word.transcript_has_wake_phrase(text)
                    _log_line("WAKE_TXT", f"{tid} | audio_wake=off | transcript_match={ok_transcript}")
                    if not ok_transcript:
                        _log_line("SKIP", f'{tid} | metin wake eşleşmedi | text="{_safe_preview(text)}"')
                        continue
                elif not wake_word.audio_wake_enabled() and not config.REQUIRE_WAKE_PHRASE:
                    _log_line("WAKE_TXT", f"{tid} | audio_wake=off | require_wake_phrase=off | transcript_check=skipped")
                else:
                    _log_line("WAKE_AUDIO", f"{tid} | audio_wake=on (Wyoming/Porcupine) | transcript kontrolü opsiyonel")
            else:
                _log_line("MODE", f"{tid} | conversation_mode=on | wake_check=skipped")

            _log_line("HEARD", f"{text} | confidence: {conf:.2f}")

            t_route0 = time.perf_counter()
            reply: str | None = route_intents(text)
            if reply is None:
                has_net = tts.internet_available()
                provider = llm.selected_provider() or "none"
                _log_line(
                    "ROUTE",
                    f"{tid} | intent=none | internet_available={has_net} | llm_provider={provider} | llm_key={'yes' if llm.is_available() else 'no'}",
                )
                if not has_net:
                    reply = memory.get_offline_response(text)
                    _log_line("OFFLINE", f"{tid} | internet yok → offline_responses eşleşti mi? {'yes' if reply else 'no'}")
                if reply is None:
                    if not llm.is_available():
                        reply = memory.get_offline_response(text) or "Şu an bağlantı veya anahtar yok kanka."
                        _log_line("ROUTE", f"{tid} | llm_key yok → offline/fallback seçildi")
                    else:
                        try:
                            _log_line("SENT_TO_LLM", text)
                            sys_prompt = memory.build_system_prompt()
                            messages = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": text},
                            ]
                            t_llm0 = time.perf_counter()
                            reply = llm.ask(messages)
                            t_llm1 = time.perf_counter()
                            _log_line(
                                "LLM_OK",
                                f'{tid} | provider={llm.selected_provider()} | model={config.MODEL if (llm.selected_provider() or "")=="openai" else config.GROQ_MODEL} | elapsed={_fmt_ms(t_llm1 - t_llm0)} | reply_preview="{_safe_preview(reply)}"',
                            )
                        except Exception as e:
                            logger.warning("LLM hatası: %s", e)
                            reply = (
                                memory.get_offline_response(text)
                                or "Bir saniye kanka, bağlantı yavaş."
                            )
                            if "limiti" in str(e).lower():
                                reply = "Günlük konuşma limitine yaklaştık kanka."
                            _log_line(
                                "LLM_ERR",
                                f"{tid} | {type(e).__name__}: {e} | fallback={'offline' if memory.get_offline_response(text) else 'generic'}",
                            )
            t_route1 = time.perf_counter()
            _log_line("ROUTE_OK", f"{tid} | route_elapsed={_fmt_ms(t_route1 - t_route0)} | reply_len={len(reply or '')}")

            assert reply is not None
            _log_line("RESPONSE", reply)

            memory.append_conversation_line("Kullanıcı", text)
            memory.append_conversation_line("Kanka", reply)

            try:
                t_tts0 = time.perf_counter()
                kind, duration = tts.speak(reply, prefer_online=True)
            except Exception as e:
                logger.warning("TTS hatası, Piper deneniyor: %s", e)
                _log_line("TTS_ERR", f"{tid} | prefer_online failed: {type(e).__name__}: {e}")
                kind, duration = tts.speak(reply, prefer_online=False)
            t_tts1 = time.perf_counter()
            _log_line(
                "TTS",
                f"{tid} | {kind} | synth+play={duration:.1f}s | call_elapsed={_fmt_ms(t_tts1 - t_tts0)} | text_len={len(reply)}",
            )

        except KeyboardInterrupt:
            logger.info("Kullanıcı durdurdu.")
            stop_batt.set()
            motion.safe_stop("keyboard_interrupt")
            break
        except Exception:
            logger.exception("Ana döngü hatası")
            motion.safe_stop("main_loop_exception")
            time.sleep(2)


def main() -> None:
    setup_logging()
    run_loop()


if __name__ == "__main__":
    main()
