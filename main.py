#!/usr/bin/env python3
"""Robot Kanka — ana döngü: VAD → wake → STT → (intent/LLM) → TTS."""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from modules import battery, barge_in, bluetooth_session, camera, head, llm, memory, motion, phrases, stt, tts, vad, wake_word  # noqa: E402
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


def _build_llm_messages(user_text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": memory.build_system_prompt()}]
    messages.extend(memory.recent_chat_messages(max_turns=3))
    messages.append({"role": "user", "content": user_text})
    return messages


def _speak_reply(
    reply: str,
    *,
    prefer_online: bool = True,
    force_piper: bool = False,
    tid: str = "",
) -> tuple[str, float]:
    try:
        if force_piper:
            kind, duration = tts.speak(reply, prefer_online=False)
        else:
            kind, duration = tts.speak(reply, prefer_online=prefer_online)
    except Exception as e:
        _log_line("TTS_ERR", f"{tid} | prefer_online failed: {type(e).__name__}: {e}")
        kind, duration = tts.speak(reply, prefer_online=False)
    return kind, duration


def _speak_reply_with_barge_in(
    reply: str,
    *,
    conversation_mode: bool,
    prefer_online: bool = True,
    force_piper: bool = False,
    tid: str = "",
) -> tuple[str | None, str, float]:
    """Dönüş: (kesinti_metni veya None, tts_kind, süre_s)."""

    def _do(txt: str) -> tuple[str, float]:
        return _speak_reply(
            txt,
            prefer_online=prefer_online,
            force_piper=force_piper,
            tid=tid,
        )

    if barge_in.should_use_barge_in(conversation_mode=conversation_mode):
        interrupted = barge_in.speak_with_barge_in(
            _do,
            reply,
            require_wake=False,
            speaking_context=reply,
        )
        if interrupted:
            return interrupted, "barge-in", 0.0
    kind, duration = _speak_reply(
        reply,
        prefer_online=prefer_online,
        force_piper=force_piper,
        tid=tid,
    )
    return None, kind, duration


def _llm_reply_and_speak(
    text: str,
    tid: str,
    *,
    conversation_mode: bool,
) -> tuple[str, str | None]:
    import threading

    messages = _build_llm_messages(text)
    parts: list[str] = []
    cancel = threading.Event()
    listener: barge_in.BargeInListener | None = None
    if barge_in.should_use_barge_in(conversation_mode=conversation_mode):
        listener = barge_in.BargeInListener(cancel, require_wake=False)
        listener.start()

    def on_sentence(s: str) -> None:
        if cancel.is_set():
            return
        parts.append(s)
        try:
            tts.speak(s, prefer_online=True)
        except Exception as e:
            if cancel.is_set():
                return
            logger.warning("TTS cümle hatası, Piper: %s", e)
            tts.speak(s, prefer_online=False)

    t_llm0 = time.perf_counter()
    try:
        full = llm.ask_stream_sentences(messages, on_sentence=on_sentence, cancel=cancel)
    finally:
        if listener is not None:
            listener.stop()

    interrupted = listener.interrupted_text if listener else None
    t_llm1 = time.perf_counter()
    reply = (full or " ".join(parts)).strip()
    _log_line(
        "LLM_OK",
        f'{tid} | provider={llm.selected_provider()} | stream=1 | elapsed={_fmt_ms(t_llm1 - t_llm0)} | '
        f'interrupted={bool(interrupted)} | reply_preview="{_safe_preview(reply)}"',
    )
    if interrupted:
        return reply, interrupted
    return reply or phrases.pick("llm_fallback"), None


def _has_any_phrase(text: str, phrases_tuple: tuple[str, ...]) -> bool:
    def norm(s: str) -> str:
        # casefold: Türkçe I/İ gibi harflerde daha güvenilir
        s = (s or "").casefold()
        # noktalama/emoji vs temizle
        s = re.sub(r"[^0-9a-zA-Zçğıöşü\s]", " ", s, flags=re.UNICODE)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    t = norm(text or "")
    if not phrases_tuple:
        return False
    for p in phrases_tuple:
        if not p:
            continue
        if norm(p) in t:
            return True
    return False


POWEROFF_VOICE_TRIGGERS = (
    "kanka robotu tamamen kapat",
    "robotu tamamen kapat",
    "kanka robotu tamamen kapa",
    "robotu tamamen kapa",
)


def _text_matches_poweroff_intent(text: str) -> bool:
    low = (text or "").lower().strip()
    return any(t in low for t in POWEROFF_VOICE_TRIGGERS)


def _system_poweroff(reason: str) -> None:
    _log_line("POWEROFF", reason)
    try:
        motion.safe_stop(reason)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["sudo", "-n", "poweroff"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            logger.error(
                "poweroff başarısız (rc=%s). Şifresiz sudo yoksa: bash scripts/setup_poweroff_sudo.sh | stderr=%s",
                r.returncode,
                (r.stderr or "").strip() or "(boş)",
            )
    except Exception as e:
        logger.error("poweroff çalıştırılamadı: %s", e)


def route_intents(text: str) -> str | None:
    mem_reply = memory.try_handle_memory_command(text)
    if mem_reply is not None:
        return mem_reply

    low = text.lower().strip()

    # Kafa (kamera pan/tilt) komutları
    if any(p in low for p in ("kafayı ortala", "kafani ortala", "kafanı ortala", "kafa ortala")):
        if not head.is_available():
            return "Kafa servoları hazır değil kanka."
        head.safe_center("voice_head_center")
        return phrases.pick("ack")

    if any(p in low for p in ("kafa sağ", "kafayı sağa", "kafani saga", "kafanı sağa", "kafayi saga", "sağa bak", "saga bak")):
        if not head.is_available():
            return "Kafa servoları hazır değil kanka."
        head.nudge(pan_delta=abs(float(getattr(config, "HEAD_NUDGE_DEG", 20.0))))
        return phrases.pick("ack")

    if any(p in low for p in ("kafa sol", "kafayı sola", "kafani sola", "kafanı sola", "sola bak")):
        if not head.is_available():
            return "Kafa servoları hazır değil kanka."
        head.nudge(pan_delta=-abs(float(getattr(config, "HEAD_NUDGE_DEG", 20.0))))
        return phrases.pick("ack")

    if any(p in low for p in ("kafa yukarı", "kafayı yukarı", "kafani yukari", "kafanı yukarı", "yukarı bak", "yukari bak")):
        if not head.is_available():
            return "Kafa servoları hazır değil kanka."
        head.nudge(tilt_delta=abs(float(getattr(config, "HEAD_NUDGE_DEG", 20.0))))
        return phrases.pick("ack")

    if any(p in low for p in ("kafa aşağı", "kafayı aşağı", "kafani asagi", "kafanı aşağı", "aşağı bak", "asagi bak")):
        if not head.is_available():
            return "Kafa servoları hazır değil kanka."
        head.nudge(tilt_delta=-abs(float(getattr(config, "HEAD_NUDGE_DEG", 20.0))))
        return phrases.pick("ack")

    # Hareket komutları (LLM'e gitmeden)
    # Not: Güvenlik için kısa süreli hareket (DEFAULT_MOVE_SECONDS) şeklinde ele alıyoruz.
    # "dur" komutu her zaman anında durdurur.
    if any(w in low for w in ("dur", "stop", "bekle", "kapan", "fren")):
        motion.safe_stop("voice_command_stop")
        return phrases.pick("ack") + " Durdum."

    # Basit yön komutları: ileri/geri/sağ/sol
    # Hareket triggerları: yalnızca hareket kastı açık olan ifadeler.
    # Kısa / belirsiz kelimeler ("kaç", "ileri", "sol" gibi) çıkarıldı —
    # bunlar günlük cümlelerde sık geçer ve yanlış tetikler.
    forward_triggers = (
        "ileri git",
        "yürü bakalım",
        "yürü kanka",
        "gaza bas",
        "devam et kanka",
        "ilerle",
    )
    backward_triggers = (
        "geri gel",
        "geri git",
        "geri geri",
        "geri dön",
        "geri kanka",
    )
    left_triggers = (
        "sola dön",
        "sola don",
        "sola git",
        "sola kanka",
    )
    right_triggers = (
        "sağa dön",
        "saga don",
        "sağa git",
        "saga git",
        "sağa kanka",
        "saga kanka",
    )

    move_sec = float(getattr(config, "DEFAULT_MOVE_SECONDS", 1.0))
    throttle = int(getattr(config, "DEFAULT_DRIVE_THROTTLE", 55))
    turn_deg = float(getattr(config, "DEFAULT_TURN_DEG", 25.0))

    if any(t in low for t in forward_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=float(getattr(config, "STEERING_CENTER_DEG", 0.0)), seconds=move_sec)
        return phrases.pick("ack")

    if any(t in low for t in backward_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=-abs(throttle), steering=float(getattr(config, "STEERING_CENTER_DEG", 0.0)), seconds=move_sec)
        return phrases.pick("ack")

    if any(t in low for t in left_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=-abs(turn_deg), seconds=move_sec)
        return phrases.pick("ack")

    if any(t in low for t in right_triggers):
        if not motion.is_available():
            return "Hareket için Robot-HAT kütüphanesi hazır değil kanka."
        motion.drive_for(throttle=throttle, steering=abs(turn_deg), seconds=move_sec)
        return phrases.pick("ack")

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

    # ── Kamera kapat ────────────────────────────────────────────────────────
    camera_close_triggers = (
        "gözlerini kapat",
        "gozlerini kapat",
        "gözlerini kapa",
        "gozlerini kapa",
        "kanka gözlerini kapat",
        "kanka gozlerini kapat",
    )
    if any(p in low for p in camera_close_triggers):
        camera.set_camera_enabled(False)
        return "Gözlerimi kapattım kanka."

    # ── Kamera aç ───────────────────────────────────────────────────────────
    camera_open_triggers = (
        "gözlerini aç",
        "gozlerini ac",
        "kanka gözlerini aç",
        "kanka gozlerini ac",
        "gözlerin aç",
        "gozlerin ac",
    )
    if any(p in low for p in camera_open_triggers):
        camera.set_camera_enabled(True)
        return "Gözlerimi açtım kanka, görüyorum artık."

    # ── Vision: bak / ne var / ne görüyorsun ────────────────────────────────
    vision_triggers = (
        "ne görüyorsun",
        "ne goruyorsun",
        "burada ne var",
        "orada ne var",
        "önümde ne var",
        "onumde ne var",
        "etrafta ne var",
        "çevrede ne var",
        "cevrede ne var",
        "ne var önünde",
        "ne var onunde",
        "önümde ne görüyorsun",
        "onumde ne goruyorsun",
    )
    is_vision_cmd = low in ("bak", "bak.") or any(t in low for t in vision_triggers)
    if is_vision_cmd:
        if not config.OPENAI_API_KEY and not config.GROQ_API_KEY:
            return "Görmem için en az bir API anahtarı lazım kanka."
        # Kamera kapalıysa önce aç
        if not camera.is_camera_enabled():
            camera.set_camera_enabled(True)
            logger.info("Vision komutu: kamera otomatik açıldı")
        try:
            if camera.camera_frozen():
                logger.warning("Kamera kilit şüphesi — yeniden denenecek.")
            return camera.look_and_describe()
        except Exception:
            logger.exception("Vision/kamera hatası")
            return "Kamerada sorun oldu kanka, bir daha dener misin?"

    if _text_matches_poweroff_intent(text):
        return "Tamam kanka, kapanıyorum. Görüşürüz."

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
    logger.info(
        "Barge-in: enabled=%s | listen_sec=%s | only_conversation_mode=%s | vad_thr=%s",
        getattr(config, "BARGE_IN_ENABLED", True),
        getattr(config, "BARGE_IN_LISTEN_SEC", 2.0),
        getattr(config, "BARGE_IN_ONLY_CONVERSATION_MODE", True),
        getattr(config, "BARGE_IN_VAD_THRESHOLD", None) or "(varsayılan VAD)",
    )
    logger.info(
        "STT: backend=%s | server_spawn=%s | base_url=%s",
        getattr(config, "WHISPER_STT_BACKEND", "cli"),
        getattr(config, "WHISPER_SERVER_SPAWN", True),
        getattr(config, "WHISPER_SERVER_BASE_URL", ""),
    )
    try:
        stt.ensure_whisper_backend_ready()
        logger.info("STT: arka uç hazır (model ilk yükleme tamamlandıysa sonraki transkripsiyonlar hızlı olur).")
    except Exception as e:
        logger.exception("STT arka uç hazırlığı başarısız: %s", e)
        raise

    try:
        vad.warmup_vad()
        logger.info("VAD: model hazır.")
    except Exception as e:
        logger.exception("VAD modeli yüklenemedi: %s", e)
        raise

    stt.clear_stt_dialogue_hint()

    tts.set_output_device(None)
    out_dev = (
        tts.get_output_device()
        or getattr(config, "AUDIO_OUTPUT_ALSA_DEVICE", "")
        or "(ALSA varsayılan)"
    )
    logger.info("TTS çıkış cihazı: %s", out_dev)
    try:
        kind, duration = tts.speak(config.STARTUP_PHRASE, prefer_online=False)
        logger.info("Açılış TTS tamam: %s | %.1fs | device=%s", kind, duration, out_dev)
    except Exception as e:
        logger.error(
            "Açılış anonsu çalınamadı (device=%s). Test: aplay -l; speaker-test -D hb; "
            "sudo systemctl start speaker-enable. Hata: %s",
            out_dev,
            e,
        )

    # Pil izleme thread'i (Robot-HAT voltajından % hesaplar)
    import threading

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
        _system_poweroff("battery_critical")

    batt_th = threading.Thread(
        target=battery.monitor_loop,
        kwargs={"on_drop_10pct": _batt_drop, "on_critical": _batt_critical, "stop_event": stop_batt},
        daemon=True,
        name="battery-monitor",
    )
    batt_th.start()

    # ── Kamera otomatik kapanma izleme thread'i ──────────────────────────────
    stop_cam = threading.Event()
    _cam_auto_close_sec = float(getattr(config, "CAMERA_AUTO_CLOSE_MIN", 25)) * 60

    def _camera_watchdog(stop_event: threading.Event) -> None:
        """Kamera açık ve %cam_auto_close_sec süre vision komutu gelmediyse kapat."""
        while not stop_event.wait(30):   # her 30 saniyede kontrol
            if not camera.is_camera_enabled():
                continue
            if camera.seconds_since_opened() >= _cam_auto_close_sec:
                logger.info("CAMERA: %d dk doldu, otomatik kapanıyor", int(_cam_auto_close_sec // 60))
                camera.set_camera_enabled(False)
                _log_line("CAMERA", f"auto_close | {int(_cam_auto_close_sec // 60)}dk doldu")
                if not tts.is_speaking():
                    try:
                        tts.speak(
                            f"Kanka {int(_cam_auto_close_sec // 60)} dakikadır kimse bakmadı, gözlerimi kapattım.",
                            prefer_online=False,
                        )
                    except Exception as e:
                        logger.warning("Kamera kapanma bildirimi TTS hatası: %s", e)

    cam_th = threading.Thread(
        target=_camera_watchdog,
        args=(stop_cam,),
        daemon=True,
        name="camera-watchdog",
    )
    cam_th.start()

    seq = 0
    conversation_mode = False
    last_nudge_at = 0.0
    pending_user_text: str | None = None
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
                if pending_user_text:
                    text, conf = pending_user_text, 1.0
                    pending_user_text = None
                    _log_line("BARGE_IN", f"{tid} | kesinti sonrası tur | text=\"{_safe_preview(text)}\"")
                elif (
                    conversation_mode
                    and getattr(config, "CONVERSATION_NUDGE_ENABLED", True)
                ):
                    text, conf = stt.listen_for_speech_and_transcribe(
                        float(getattr(config, "CONVERSATION_NUDGE_SEC", 8)),
                        require_wake=False,
                    )
                    if not text.strip():
                        now = time.time()
                        cooldown = float(getattr(config, "CONVERSATION_NUDGE_COOLDOWN_SEC", 25))
                        if now - last_nudge_at >= cooldown:
                            nudge_this_turn = True
                            reply = phrases.pick("nudge")
                            _log_line("NUDGE", f"{tid} | {reply}")
                            force_piper = bool(getattr(config, "TTS_PREFER_PIPER_FOR_NUDGE", True))
                            interrupted, kind, duration = _speak_reply_with_barge_in(
                                reply,
                                conversation_mode=conversation_mode,
                                prefer_online=not force_piper,
                                force_piper=force_piper,
                                tid=tid,
                            )
                            if interrupted:
                                pending_user_text = interrupted
                                continue
                            _log_line("TTS", f"{tid} | {kind} | nudge | duration={duration:.1f}s")
                            last_nudge_at = now
                        else:
                            _log_line("SKIP", f"{tid} | sessizlik (nudge cooldown)")
                        continue
                else:
                    wait_sec = float(getattr(config, "LISTEN_WAIT_SEC", 30))
                    text, conf = stt.listen_for_speech_and_transcribe(
                        wait_sec,
                        require_wake=not conversation_mode,
                    )
            except Exception as e:
                _log_line("STT_ERROR", f"{tid} | listen exception: {type(e).__name__}: {e}")
                raise
            t_listen1 = time.perf_counter()

            if not text.strip():
                listen_sec = t_listen1 - t_listen0
                _log_line(
                    "SKIP",
                    f"{tid} | konuşma yok / wake gate geçmedi | listen_total={_fmt_ms(listen_sec)}",
                )
                # arecord anında kapanınca saniyede yüzlerce SKIP olmasın diye kısa bekleme
                if listen_sec < 0.25:
                    backoff = float(getattr(config, "LISTEN_FAST_FAIL_BACKOFF_SEC", 0.35))
                    time.sleep(backoff)
                continue

            _log_line(
                "STT",
                f'{tid} | text="{_safe_preview(text)}" | confidence={conf:.2f} | total={_fmt_ms(t_listen1 - t_listen0)}',
            )

            # Konuşma modu: "hey kanka" ile aç, "görüşürüz kanka" ile kapat.
            if not conversation_mode and _has_any_phrase(text, config.CONVERSATION_ACTIVATE_PHRASES):
                conversation_mode = True
                _log_line("MODE", f"{tid} | conversation_mode=on | trigger=activate")
                reply = phrases.pick("activate")
                _log_line("RESPONSE", reply)
                interrupted, kind, duration = _speak_reply_with_barge_in(
                    reply, conversation_mode=conversation_mode, prefer_online=True, tid=tid
                )
                if interrupted:
                    pending_user_text = interrupted
                    continue
                _log_line("TTS", f"{tid} | {kind} | synth+play={duration:.1f}s | text_len={len(reply)}")
                stt.record_dialogue_turn_for_stt(text, reply)
                continue

            if conversation_mode and _has_any_phrase(text, config.CONVERSATION_DEACTIVATE_PHRASES):
                _log_line("MODE", f"{tid} | conversation_mode=off | trigger=deactivate")
                reply = phrases.pick("deactivate")
                _log_line("RESPONSE", reply)
                interrupted, kind, duration = _speak_reply_with_barge_in(
                    reply, conversation_mode=True, prefer_online=True, tid=tid
                )
                if interrupted:
                    pending_user_text = interrupted
                    continue
                conversation_mode = False
                _log_line("TTS", f"{tid} | {kind} | synth+play={duration:.1f}s | text_len={len(reply)}")
                stt.clear_stt_dialogue_hint()
                continue

            # Konuşma modu kapalıysa wake zorunluluğu uygula; mod açıksa direkt devam et.
            if not conversation_mode:
                if not wake_word.audio_wake_enabled() and config.REQUIRE_WAKE_PHRASE:
                    ok_transcript = wake_word.transcript_has_wake_phrase(text)
                    _log_line("WAKE_TXT", f"{tid} | audio_wake=off | transcript_match={ok_transcript}")
                    if not ok_transcript:
                        _log_line("SKIP", f'{tid} | metin wake eşleşmedi | text="{_safe_preview(text)}"')
                        continue
                    # Wake phrase eşleşti → konuşma modunu otomatik aç
                    conversation_mode = True
                    _log_line("MODE", f"{tid} | conversation_mode=on | trigger=wake_auto")
                elif not wake_word.audio_wake_enabled() and not config.REQUIRE_WAKE_PHRASE:
                    _log_line("WAKE_TXT", f"{tid} | audio_wake=off | require_wake_phrase=off | transcript_check=skipped")
                    conversation_mode = True
                    _log_line("MODE", f"{tid} | conversation_mode=on | trigger=wake_auto")
                else:
                    _log_line("WAKE_AUDIO", f"{tid} | audio_wake=on (Wyoming/Porcupine) | transcript kontrolü opsiyonel")
                    conversation_mode = True
                    _log_line("MODE", f"{tid} | conversation_mode=on | trigger=wake_auto")
            else:
                _log_line("MODE", f"{tid} | conversation_mode=on | wake_check=skipped")

            _log_line("HEARD", f"{text} | confidence: {conf:.2f}")

            bt_handled, bt_replies = bluetooth_session.handle_turn(text)
            if bt_handled:
                _log_line("BT", f"{tid} | replies={len(bt_replies)} | phase={bluetooth_session.session().phase}")
                skip_open_ack = bluetooth_session.consume_open_ack_skip()
                full_bt = " ".join(bt_replies)
                memory.append_conversation_line("Kullanıcı", text)
                memory.append_conversation_line("Kanka", full_bt)
                stt.record_dialogue_turn_for_stt(text, full_bt)
                def _bt_on_response(line: str, i: int, total: int) -> None:
                    _log_line(
                        "RESPONSE",
                        line if total == 1 else f"[{i}/{total}] {line}",
                    )

                def _bt_on_tts(kind: str, duration: float) -> None:
                    _log_line("TTS", f"{tid} | {kind} | bt | duration={duration:.1f}s")

                def _bt_speak_line(line: str) -> tuple[str, float]:
                    return _speak_reply(line, prefer_online=True, tid=tid)

                try:
                    bluetooth_session.speak_replies(
                        bt_replies,
                        skip_first=skip_open_ack,
                        speak_line=_bt_speak_line,
                        on_response=_bt_on_response,
                        on_tts=_bt_on_tts,
                    )
                except Exception as e:
                    _log_line("TTS_ERR", f"{tid} | bt: {type(e).__name__}: {e}")
                continue

            t_route0 = time.perf_counter()
            tts_via_llm_stream = False
            llm_interrupted: str | None = None
            poweroff_after_tts = _text_matches_poweroff_intent(text)
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
                        reply = memory.get_offline_response(text) or phrases.pick(
                            "offline_no_key",
                            fallback="Şu an bağlantı veya anahtar yok kanka.",
                        )
                        _log_line("ROUTE", f"{tid} | llm_key yok → offline/fallback seçildi")
                    else:
                        try:
                            _log_line("SENT_TO_LLM", text)
                            t_llm0 = time.perf_counter()
                            reply, llm_interrupted = _llm_reply_and_speak(
                                text, tid, conversation_mode=conversation_mode
                            )
                            tts_via_llm_stream = True
                            t_llm1 = time.perf_counter()
                            _log_line(
                                "LLM_DONE",
                                f"{tid} | total_with_tts={_fmt_ms(t_llm1 - t_llm0)} | interrupted={bool(llm_interrupted)}",
                            )
                        except Exception as e:
                            logger.warning("LLM hatası: %s", e)
                            reply = memory.get_offline_response(text) or phrases.pick("llm_fallback")
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

            if llm_interrupted:
                memory.append_conversation_line("Kullanıcı", text)
                pending_user_text = llm_interrupted
                continue

            if not tts_via_llm_stream:
                t_tts0 = time.perf_counter()
                interrupted, kind, duration = _speak_reply_with_barge_in(
                    reply,
                    conversation_mode=conversation_mode,
                    prefer_online=True,
                    tid=tid,
                )
                t_tts1 = time.perf_counter()
                if interrupted:
                    memory.append_conversation_line("Kullanıcı", text)
                    pending_user_text = interrupted
                    continue
                _log_line(
                    "TTS",
                    f"{tid} | {kind} | synth+play={duration:.1f}s | call_elapsed={_fmt_ms(t_tts1 - t_tts0)} | text_len={len(reply)}",
                )
            else:
                _log_line("TTS", f"{tid} | streamed_during_llm | text_len={len(reply)}")

            if poweroff_after_tts and reply is not None and not llm_interrupted and not tts_via_llm_stream:
                memory.append_conversation_line("Kullanıcı", text)
                memory.append_conversation_line("Kanka", reply)
                _system_poweroff("voice_command")
                break

            memory.append_conversation_line("Kullanıcı", text)
            memory.append_conversation_line("Kanka", reply)
            stt.record_dialogue_turn_for_stt(text, reply)

        except KeyboardInterrupt:
            logger.info("Kullanıcı durdurdu.")
            vad.terminate_active_capture()
            stop_batt.set()
            stop_cam.set()
            motion.safe_stop("keyboard_interrupt")
            break
        except Exception:
            logger.exception("Ana döngü hatası")
            motion.safe_stop("main_loop_exception")
            time.sleep(2)


def main() -> None:
    setup_logging()

    def _shutdown_signal(signum: int, _frame) -> None:
        logger.info("Durdurma sinyali (%s)", signum)
        vad.terminate_active_capture()
        try:
            motion.safe_stop("signal")
        except Exception:
            pass
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _shutdown_signal)
    signal.signal(signal.SIGTERM, _shutdown_signal)

    run_loop()


if __name__ == "__main__":
    main()
