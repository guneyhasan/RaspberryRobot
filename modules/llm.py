"""OpenAI Chat Completions — sadece metin."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

import config

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_groq_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.TIMEOUT_SECONDS)
    return _client


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


def _daily_count() -> int:
    import json
    from datetime import date

    f = config.REQUEST_COUNTER_FILE
    today = str(date.today())
    if not f.is_file():
        return 0
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("day") != today:
            return 0
        return int(data.get("n", 0))
    except (ValueError, json.JSONDecodeError, TypeError):
        return 0


def _bump_daily() -> None:
    import json
    from datetime import date

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = str(date.today())
    n = 0
    if config.REQUEST_COUNTER_FILE.is_file():
        try:
            data = json.loads(config.REQUEST_COUNTER_FILE.read_text(encoding="utf-8"))
            if data.get("day") == today:
                n = int(data.get("n", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    n += 1
    config.REQUEST_COUNTER_FILE.write_text(
        json.dumps({"day": today, "n": n}),
        encoding="utf-8",
    )


def bump_request_count() -> None:
    """Vision vb. ek OpenAI çağrıları için günlük sayaç."""
    _bump_daily()


def ensure_daily_quota() -> None:
    if _daily_count() >= config.MAX_DAILY_REQUESTS:
        raise RuntimeError("Günlük istek limiti aşıldı")


def _select_provider() -> str:
    """
    Returns: "openai" | "groq"
    Seçim mantığı:
    - Sadece biri varsa otomatik.
    - İkisi de varsa LLM_PROVIDER (openai/groq) kullanılır; boşsa openai.
    """
    has_openai = bool(config.OPENAI_API_KEY)
    has_groq = bool(config.GROQ_API_KEY)
    if has_openai and not has_groq:
        return "openai"
    if has_groq and not has_openai:
        return "groq"
    if has_openai and has_groq:
        if config.LLM_PROVIDER in ("groq", "openai"):
            return config.LLM_PROVIDER
        return "openai"
    raise RuntimeError("LLM anahtarı yok (OPENAI_API_KEY veya GROQ_API_KEY gerekli)")


def selected_provider() -> str | None:
    """Şu anki .env'e göre seçilecek provider (anahtar yoksa None)."""
    try:
        return _select_provider()
    except Exception:
        return None


def is_available() -> bool:
    """Herhangi bir LLM anahtarı var mı?"""
    return bool(config.OPENAI_API_KEY or config.GROQ_API_KEY)


def ask(messages: list[dict[str, Any]]) -> str:
    """
    OpenAI veya Groq üzerinden cevap döndürür (messages OpenAI formatında).
    Groq tarafı OpenAI-benzeri Chat Completions API sağlar.
    """
    ensure_daily_quota()

    provider = _select_provider()
    last_err: Optional[Exception] = None
    delay = 1.0
    for attempt in range(config.RETRY_ATTEMPTS + 1):
        try:
            if provider == "openai":
                client = _get_client()
                resp = client.chat.completions.create(
                    model=config.MODEL,
                    messages=messages,
                    max_tokens=config.MAX_TOKENS,
                )
                _bump_daily()
                choice = resp.choices[0]
                return (choice.message.content or "").strip()

            client = _get_groq_client()
            if config.GROQ_STREAM:
                completion = client.chat.completions.create(
                    model=config.GROQ_MODEL,
                    messages=messages,
                    temperature=1,
                    max_completion_tokens=max(16, int(config.MAX_TOKENS)),
                    top_p=1,
                    stream=True,
                    stop=None,
                )
                buf: list[str] = []
                for chunk in completion:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        buf.append(delta)
                _bump_daily()
                return "".join(buf).strip()
            resp = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=messages,
                temperature=1,
                max_completion_tokens=max(16, int(config.MAX_TOKENS)),
                top_p=1,
                stream=False,
                stop=None,
            )
            _bump_daily()
            choice = resp.choices[0]
            return (choice.message.content or "").strip()

        except (APITimeoutError, RateLimitError, APIError, Exception) as e:
            last_err = e
            logger.warning("LLM deneme %s başarısız (provider=%s): %s", attempt + 1, provider, e)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"OpenAI başarısız: {last_err}")


def ask_openai(messages: list[dict[str, Any]]) -> str:
    """
    Geriye dönük uyumluluk: eski kod `ask_openai` çağırıyordu.
    Artık sağlayıcıyı otomatik seçen `ask()`'e delege eder.
    """
    return ask(messages)
