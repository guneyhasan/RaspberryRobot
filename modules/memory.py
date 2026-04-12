"""Profil, offline yanıtlar ve son konuşmalar."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import config

_MAX_CONV_LINES = 20


def read_profile() -> dict[str, Any]:
    path = config.PROFILE_PATH
    if not path.is_file():
        return {"name": "Cihan", "preferences": [], "memories": [], "last_updated": str(date.today())}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_profile(data: dict[str, Any]) -> None:
    data["last_updated"] = str(date.today())
    config.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_memory(text: str) -> None:
    p = read_profile()
    memories = p.setdefault("memories", [])
    memories.append(text.strip())
    write_profile(p)


def pop_last_memory() -> bool:
    p = read_profile()
    memories = p.setdefault("memories", [])
    if not memories:
        return False
    memories.pop()
    write_profile(p)
    return True


def clear_memories() -> None:
    p = read_profile()
    p["memories"] = []
    write_profile(p)


def load_offline_responses() -> dict[str, str]:
    if not config.OFFLINE_RESPONSES_PATH.is_file():
        return {}
    with open(config.OFFLINE_RESPONSES_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_offline_response(user_text: str) -> Optional[str]:
    t = user_text.lower().strip()
    data = load_offline_responses()
    for key, val in data.items():
        if key.lower() in t:
            return val
    return None


def append_conversation_line(role: str, text: str) -> None:
    config.CONVERSATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{role}: {text.strip()}\n"
    prev = ""
    if config.CONVERSATIONS_PATH.is_file():
        prev = config.CONVERSATIONS_PATH.read_text(encoding="utf-8")
    lines = (prev + line).splitlines()
    keep = lines[-_MAX_CONV_LINES:]
    config.CONVERSATIONS_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")


def recent_conversations_text() -> str:
    if not config.CONVERSATIONS_PATH.is_file():
        return ""
    return config.CONVERSATIONS_PATH.read_text(encoding="utf-8").strip()


def build_system_prompt() -> str:
    profile = read_profile()
    recent = recent_conversations_text()
    parts = [
        config.BASE_SYSTEM_PROMPT,
        "\n\nKullanıcı profili:\n",
        json.dumps(profile, ensure_ascii=False, indent=2),
    ]
    if recent:
        parts.extend(["\n\nSon konuşmalar:\n", recent])
    return "".join(parts)


_REMEMBER = re.compile(r"^bunu\s+hatırla\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_REMEMBER2 = re.compile(r"hatırla\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


def try_handle_memory_command(user_text: str) -> Optional[str]:
    """Hafıza komutları için sabit yanıt veya None (LLM'e gitsin)."""
    t = user_text.strip()
    low = t.lower()

    if low in ("bunu unut", "bunu unut.", "unut bunu"):
        if pop_last_memory():
            return "Tamam kanka, son kaydı sildim."
        return "Silinecek bir şey yoktu."

    if low in ("hafızanı sil", "hafızayı sil", "hafızanı temizle"):
        clear_memories()
        return "Hafızayı temizledim kanka."

    m = _REMEMBER.match(t) or _REMEMBER2.search(t)
    if m:
        info = m.group(1).strip()
        if info:
            append_memory(info)
            return f"Tamam, not aldım: {info}"
    return None
