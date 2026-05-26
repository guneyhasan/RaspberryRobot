"""Robot konuşurken kullanıcı araya girme (barge-in)."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import config
from modules import stt, tts

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _norm_text(s: str) -> str:
    s = (s or "").casefold()
    s = re.sub(r"[^0-9a-zA-Zçğıöşü\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _is_echo_like(user_text: str, speaking_context: str) -> bool:
    """Hoparlörden gelen robot sesinin STT'ye düşmesini azalt."""
    u = _norm_text(user_text)
    sp = _norm_text(speaking_context)
    if not u or not sp:
        return False
    if len(u) < 8:
        return False
    if u in sp or sp in u:
        return True
    u_words = set(u.split())
    sp_words = set(sp.split())
    if len(u_words) >= 3 and len(u_words & sp_words) >= max(2, len(u_words) * 2 // 3):
        return True
    return False


def default_accept(
    text: str,
    *,
    speaking_context: str = "",
    min_chars: int | None = None,
) -> bool:
    t = (text or "").strip()
    mc = min_chars if min_chars is not None else int(getattr(config, "BARGE_IN_MIN_CHARS", 4))
    if len(t) < mc:
        return False
    if speaking_context and _is_echo_like(t, speaking_context):
        logger.debug("Barge-in yankı filtresi: %r", t[:80])
        return False
    return True


def _listen_chunk(
    listen_chunk_sec: float,
    *,
    require_wake: bool,
    vad_threshold: float | None,
) -> tuple[str, float]:
    chunk = min(listen_chunk_sec, 2.0)
    thr = vad_threshold if vad_threshold is not None else getattr(config, "BARGE_IN_VAD_THRESHOLD", None)
    return stt.listen_for_speech_and_transcribe(
        chunk,
        require_wake=require_wake,
        vad_threshold=thr,
    )


def speak_with_barge_in(
    speak_fn: Callable[[str], tuple[str, float]],
    text: str,
    *,
    listen_chunk_sec: float | None = None,
    require_wake: bool = False,
    accept_fn: Callable[[str], bool] | None = None,
    speaking_context: str = "",
) -> str | None:
    """
    TTS'i arka planda çalıştırır; kullanıcı konuşursa keser ve metni döndürür.
    """
    if not (text or "").strip():
        return None

    listen_sec = listen_chunk_sec
    if listen_sec is None:
        listen_sec = float(getattr(config, "BARGE_IN_LISTEN_SEC", 2.0))

    ctx = speaking_context or text
    accept = accept_fn or (lambda t: default_accept(t, speaking_context=ctx))

    tts.clear_stop_flag()
    done = threading.Event()
    errors: list[BaseException] = []

    def _runner() -> None:
        try:
            speak_fn(text)
        except BaseException as e:
            errors.append(e)
        finally:
            done.set()

    th = threading.Thread(target=_runner, daemon=True, name="barge-in-tts")
    th.start()
    line_deadline = time.time() + 120.0

    while not done.is_set() and time.time() < line_deadline:
        try:
            heard, _conf = _listen_chunk(listen_sec, require_wake=require_wake, vad_threshold=None)
        except Exception as e:
            logger.debug("Barge-in dinleme: %s", e)
            heard = ""
        if heard.strip() and accept(heard):
            logger.info("Barge-in kesildi: %r", heard[:100])
            tts.stop_speaking()
            done.wait(timeout=3.0)
            return heard.strip()
        if done.wait(timeout=0.15):
            break

    th.join(timeout=1.0)
    if errors:
        err = errors[0]
        err_name = type(err).__name__
        if err_name != "TtsInterrupted" and "kesildi" not in str(err).lower():
            raise err
    return None


class BargeInListener:
    """LLM+TTS turu boyunca arka planda dinler; kesinti olunca cancel ve metin set eder."""

    def __init__(
        self,
        cancel: threading.Event,
        *,
        listen_chunk_sec: float | None = None,
        require_wake: bool = False,
        accept_fn: Callable[[str], bool] | None = None,
        speaking_context: str = "",
    ) -> None:
        self.cancel = cancel
        self.interrupted_text: str | None = None
        self._listen_sec = listen_chunk_sec or float(getattr(config, "BARGE_IN_LISTEN_SEC", 2.0))
        self._require_wake = require_wake
        self._accept_fn = accept_fn
        self._speaking_context = speaking_context
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-in-listener")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        accept = self._accept_fn or (
            lambda t: default_accept(t, speaking_context=self._speaking_context)
        )
        while not self._stop.is_set() and not self.cancel.is_set():
            try:
                heard, _conf = _listen_chunk(
                    self._listen_sec,
                    require_wake=self._require_wake,
                    vad_threshold=None,
                )
            except Exception as e:
                logger.debug("Barge-in listener: %s", e)
                heard = ""
            if heard.strip() and accept(heard):
                logger.info("Barge-in listener kesildi: %r", heard[:100])
                self.interrupted_text = heard.strip()
                self.cancel.set()
                tts.stop_speaking()
                return
            if self._stop.wait(timeout=0.1):
                break


def should_use_barge_in(*, conversation_mode: bool) -> bool:
    if not bool(getattr(config, "BARGE_IN_ENABLED", True)):
        return False
    if bool(getattr(config, "BARGE_IN_ONLY_CONVERSATION_MODE", True)):
        return conversation_mode
    return True
