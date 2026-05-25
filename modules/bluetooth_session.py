"""Sesli Bluetooth kulaklık modu: tarama, numaralı liste, bağlan, bluealsa çıkış."""
from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Literal

import config
from modules import phrases, tts

logger = logging.getLogger(__name__)

Phase = Literal["idle", "scanning", "awaiting", "connected"]

_DEVICE_LINE = re.compile(
    r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$",
    re.MULTILINE,
)

_TURKISH_NUMBERS: dict[str, int] = {
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "uc": 3,
    "dört": 4,
    "dort": 4,
    "beş": 5,
    "bes": 5,
    "altı": 6,
    "alti": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
}


@dataclass
class BtSession:
    active: bool = False
    phase: Phase = "idle"
    devices: list[tuple[int, str, str]] = field(default_factory=list)
    connected_mac: str | None = None
    connected_index: int | None = None


_session = BtSession()


def session() -> BtSession:
    return _session


def is_enabled() -> bool:
    return bool(getattr(config, "BLUETOOTH_ENABLED", True))


def is_awaiting_selection() -> bool:
    s = _session
    return s.active and s.phase == "awaiting"


def is_bt_mode_active() -> bool:
    return _session.active


def _norm_text(text: str) -> str:
    t = (text or "").casefold()
    t = re.sub(r"[^0-9a-zA-Zçğıöşü\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    # STT bazen ASCII yazar; eşleştirme için Türkçe harfleri sadeleştir
    t = (
        t.replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    return t


def _run_bluetoothctl_script(commands: str, timeout: float = 30.0) -> tuple[int, str]:
    script = "\n".join(line.strip() for line in commands.strip().splitlines() if line.strip())
    script += "\nquit\n"
    try:
        r = subprocess.run(
            ["bluetoothctl"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def _parse_devices(output: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for m in _DEVICE_LINE.finditer(output or ""):
        mac = m.group(1).upper()
        name = m.group(2).strip()
        if mac in seen:
            continue
        seen.add(mac)
        result.append((mac, name))
    return result


def _normalize_mac(mac: str) -> str:
    m = mac.upper().replace("-", ":")
    parts = re.findall(r"[0-9A-F]{2}", m.replace(":", ""))
    if len(parts) == 6:
        return ":".join(parts)
    return m


def bluealsa_device_for_mac(mac: str) -> str:
    mac = _normalize_mac(mac)
    profile = getattr(config, "BLUETOOTH_A2DP_PROFILE", "a2dp") or "a2dp"
    return f"bluealsa:DEV={mac},PROFILE={profile}"


def ensure_adapter_ready() -> tuple[bool, str]:
    rc, out = _run_bluetoothctl_script("power on\n", timeout=15)
    if rc == 127:
        return False, "bluetoothctl bulunamadı kanka."
    if "Powered: yes" in out or "succeeded" in out.lower() or rc == 0:
        return True, ""
    return False, "Bluetooth adaptörü açılamadı kanka."


def scan_devices(timeout_sec: float | None = None) -> list[tuple[str, str]]:
    timeout_sec = timeout_sec if timeout_sec is not None else float(config.BLUETOOTH_SCAN_SEC)
    paired_only = bool(getattr(config, "BLUETOOTH_LIST_PAIRED_ONLY", False))

    _run_bluetoothctl_script("power on\n", timeout=10)
    if not paired_only:
        _run_bluetoothctl_script("scan on\n", timeout=5)
        time.sleep(max(3.0, timeout_sec - 2))
        _run_bluetoothctl_script("scan off\n", timeout=8)

    cmd = "paired-devices\n" if paired_only else "devices\n"
    _, out = _run_bluetoothctl_script(cmd, timeout=15)
    devices = _parse_devices(out)
    if not devices and paired_only:
        _, out2 = _run_bluetoothctl_script("devices\n", timeout=15)
        devices = _parse_devices(out2)
    return devices


def wait_a2dp_ready(mac: str, timeout_sec: float | None = None) -> bool:
    timeout_sec = timeout_sec if timeout_sec is not None else float(config.BLUETOOTH_CONNECT_TIMEOUT_SEC)
    mac = _normalize_mac(mac)
    target = bluealsa_device_for_mac(mac)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["bluealsa-aplay", "-L"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            listing = (r.stdout or "") + (r.stderr or "")
            if target in listing or mac.replace(":", "") in listing.replace(":", ""):
                return True
        except FileNotFoundError:
            _, info = _run_bluetoothctl_script(f"info {mac}\n", timeout=10)
            if "Connected: yes" in info and ("UUID: Audio Sink" in info or "A2DP" in info):
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        _, info = _run_bluetoothctl_script(f"info {mac}\n", timeout=10)
        if "Connected: yes" in info:
            return True
        time.sleep(1.0)
    return False


def connect_mac(mac: str) -> tuple[bool, str]:
    mac = _normalize_mac(mac)
    timeout = float(config.BLUETOOTH_CONNECT_TIMEOUT_SEC)
    script = f"""
power on
trust {mac}
pair {mac}
connect {mac}
"""
    rc, out = _run_bluetoothctl_script(script, timeout=timeout + 5)
    if "Failed" in out and "Connected: yes" not in out:
        _, out2 = _run_bluetoothctl_script(f"connect {mac}\n", timeout=timeout)
        out = out + out2
        if "Failed" in out2 and "Connected: yes" not in out2:
            return False, "Bağlantı kurulamadı kanka."
    if not wait_a2dp_ready(mac, timeout_sec=timeout):
        logger.warning("A2DP/bluealsa hazır değil, yine de devam: %s", mac)
    return True, ""


def disconnect_and_power_off(mac: str | None = None) -> None:
    if mac:
        mac = _normalize_mac(mac)
        _run_bluetoothctl_script(f"disconnect {mac}\n", timeout=12)
    _run_bluetoothctl_script("disconnect\npower off\n", timeout=15)


def default_speaker_alsa_device() -> str:
    """Robot hoparlörü — AUDIO_OUTPUT_ALSA_DEVICE ile aynı (hb yoksa plughw:0,0)."""
    for attr in ("BLUETOOTH_SPEAKER_ALSA_DEVICE", "AUDIO_OUTPUT_ALSA_DEVICE"):
        v = (getattr(config, attr, "") or "").strip()
        if v:
            return v
    return "plughw:0,0"


def set_speaker_output() -> None:
    dev = default_speaker_alsa_device()
    logger.info("BT: hoparlör çıkışı → %s", dev)
    tts.set_output_device(dev)


def set_headphone_output(mac: str) -> None:
    tts.set_output_device(bluealsa_device_for_mac(mac))


def _numbered_list(devices: list[tuple[str, str]]) -> list[tuple[int, str, str]]:
    max_n = int(getattr(config, "BLUETOOTH_MAX_LIST", 8))
    out: list[tuple[int, str, str]] = []
    for i, (mac, name) in enumerate(devices[:max_n], start=1):
        out.append((i, name, mac))
    return out


def _parse_connect_number(text: str) -> int | None:
    low = _norm_text(text)
    m = re.search(r"(\d+)\s*numara", low)
    if m:
        return int(m.group(1))
    m = re.search(r"numara\s*(\d+)", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*numaraya\s+baglan", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\.\s*numara", low)
    if m:
        return int(m.group(1))
    for word, num in _TURKISH_NUMBERS.items():
        if re.search(rf"\b{word}\s+numara", low):
            return num
        if re.search(rf"\b{word}\s+numaraya\s+baglan", low):
            return num
        if re.search(rf"numara\s+{word}\b", low):
            return num
    return None


def _matches_open(text: str) -> bool:
    low = _norm_text(text)
    triggers = (
        "bluetooth kulaklik modunu ac",
        "kulaklik modunu ac",
        "bluetooth modunu ac",
        "bluetooth kulaklik modu ac",
    )
    return any(t in low for t in triggers)


def _matches_close(text: str) -> bool:
    low = _norm_text(text)
    triggers = (
        "bluetooth kulaklik modunu kapat",
        "kulaklik modunu kapat",
        "bluetooth modunu kapat",
    )
    return any(t in low for t in triggers)


def _reset_session() -> None:
    global _session
    _session = BtSession()


def open_mode() -> list[str]:
    global _session
    if not is_enabled():
        return [phrases.pick("bt_error_disabled", fallback="Bluetooth modu kapalı kanka.")]

    set_speaker_output()
    ok, err = ensure_adapter_ready()
    if not ok:
        return [err or phrases.pick("bt_error_adapter", fallback="Bluetooth açılamadı kanka.")]

    _session.active = True
    _session.phase = "scanning"
    _session.connected_mac = None
    _session.connected_index = None

    raw = scan_devices()
    if not raw:
        _session.phase = "awaiting"
        _session.devices = []
        return [
            phrases.pick("bt_open", fallback="Bluetooth kulaklık modunu açtım kanka."),
            phrases.pick(
                "bt_error_no_devices",
                fallback="Eşleşmiş cihaz bulamadım kanka. Kulaklığı bir kez bluetoothctl ile eşleştirmen gerekebilir.",
            ),
            phrases.pick("bt_pair_hint", fallback="Kulaklığı eşleştirme moduna al, sonra tekrar dene kanka."),
        ]

    _session.devices = _numbered_list(raw)
    _session.phase = "awaiting"

    replies: list[str] = [
        phrases.pick("bt_open", fallback="Bluetooth kulaklık modunu açtım kanka."),
        phrases.pick("bt_list_intro", fallback="Bağlanabileceğin cihazlar kanka:"),
    ]
    for num, name, _mac in _session.devices:
        replies.append(f"{num}. {name}")
    replies.append(
        phrases.pick(
            "bt_await_number",
            fallback="Hangi numaraya bağlanmak istiyorsun kanka?",
        )
    )
    return replies


def connect_by_index(index: int) -> list[str]:
    s = _session
    if not s.active or s.phase not in ("awaiting", "connected"):
        return [phrases.pick("bt_error_not_active", fallback="Önce kulaklık modunu aç kanka.")]

    if index < 1 or index > len(s.devices):
        return [
            phrases.pick(
                "bt_error_invalid_number",
                fallback=f"Geçersiz numara kanka. 1 ile {len(s.devices)} arasında söyle.",
            )
        ]

    _num, name, mac = s.devices[index - 1]
    ok, err = connect_mac(mac)
    if not ok:
        return [err or phrases.pick("bt_error_connect", fallback="Bağlanamadım kanka, bir daha dene.")]

    set_headphone_output(mac)
    s.phase = "connected"
    s.connected_mac = mac
    s.connected_index = index

    msg = phrases.pick(
        "bt_connected",
        fallback=f"{index} numaraya bağlandım kanka, ses artık kulaklıktan geliyor.",
    )
    return [msg.replace("{n}", str(index)).replace("{name}", name)]


def close_mode() -> list[str]:
    global _session
    mac = _session.connected_mac
    set_speaker_output()
    try:
        disconnect_and_power_off(mac)
    except Exception as e:
        logger.warning("BT kapatma hatası: %s", e)
    tts.set_output_device(None)
    replies = [
        phrases.pick(
            "bt_closed",
            fallback="Kanka bluetooth kulaklık modunu kapattım, normal hoparlöre döndüm.",
        )
    ]
    _reset_session()
    return replies


def handle_turn(text: str) -> tuple[bool, list[str]]:
    """
    Returns (handled, replies).
    handled=True → ana döngü replies'i TTS ile okur; awaiting fazında LLM atlanır.
    """
    if not is_enabled():
        if _matches_open(text) or _matches_close(text):
            return True, [phrases.pick("bt_error_disabled", fallback="Bluetooth modu kapalı kanka.")]
        return False, []

    if _matches_close(text):
        if _session.active:
            return True, close_mode()
        return True, [
            phrases.pick(
                "bt_closed",
                fallback="Kanka bluetooth kulaklık modunu kapattım, normal hoparlöre döndüm.",
            )
        ]

    if _matches_open(text):
        return True, open_mode()

    num = _parse_connect_number(text)
    if num is not None:
        return True, connect_by_index(num)

    if _session.active and _session.phase == "awaiting":
        low = _norm_text(text)
        if "baglan" in low or "bağlan" in text.casefold():
            return True, [
                phrases.pick(
                    "bt_await_number",
                    fallback="Hangi numaraya bağlanmak istiyorsun kanka? Örneğin iki numaraya bağlan.",
                )
            ]

    return False, []
