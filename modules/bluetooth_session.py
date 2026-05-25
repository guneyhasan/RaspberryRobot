"""Sesli Bluetooth kulaklık modu: eşleşmiş bağlan, tara, numara ile eşleştir/bağlan."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
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

_BT_AGENT_SCRIPT = """agent NoInputNoOutput
default-agent
"""


@dataclass
class BtSession:
    active: bool = False
    phase: Phase = "idle"
    devices: list[tuple[int, str, str]] = field(default_factory=list)
    connected_mac: str | None = None
    connected_index: int | None = None
    discovered_only: bool = False  # True → listede keşfedilen (eşleştirme gerekebilir)


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


def _last_device_path() -> Path:
    return config.DATA_DIR / "bt_last_device.json"


def _load_last_mac() -> str | None:
    p = _last_device_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        mac = (data.get("mac") or "").strip()
        return _normalize_mac(mac) if mac else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_last_mac(mac: str, name: str = "") -> None:
    p = _last_device_path()
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"mac": _normalize_mac(mac), "name": name}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("bt_last_device yazılamadı: %s", e)


def _norm_text(text: str) -> str:
    t = (text or "").casefold()
    t = re.sub(r"[^0-9a-zA-Zçğıöşü\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
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


def _merge_device_lists(*lists: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for lst in lists:
        for mac, name in lst:
            if mac in seen:
                continue
            seen.add(mac)
            out.append((mac, name))
    return out


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
    rc, out = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "power on\n",
        timeout=15,
    )
    if rc == 127:
        return False, "bluetoothctl bulunamadı kanka."
    if "Powered: yes" in out or "succeeded" in out.lower() or rc == 0:
        return True, ""
    return False, "Bluetooth adaptörü açılamadı kanka."


def list_paired_devices() -> list[tuple[str, str]]:
    _, out = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "paired-devices\n",
        timeout=15,
    )
    return _parse_devices(out)


def scan_devices(timeout_sec: float | None = None) -> list[tuple[str, str]]:
    """Yakındaki + eşleşmiş cihazları birleştir."""
    timeout_sec = timeout_sec if timeout_sec is not None else float(config.BLUETOOTH_SCAN_SEC)

    _run_bluetoothctl_script(_BT_AGENT_SCRIPT + "power on\n", timeout=10)
    _run_bluetoothctl_script("scan on\n", timeout=5)
    time.sleep(max(4.0, timeout_sec))
    _run_bluetoothctl_script("scan off\n", timeout=10)

    _, out_dev = _run_bluetoothctl_script("devices\n", timeout=15)
    discovered = _parse_devices(out_dev)
    paired = list_paired_devices()
    merged = _merge_device_lists(paired, discovered)
    logger.info("BT tarama: paired=%s discovered=%s merged=%s", len(paired), len(discovered), len(merged))
    return merged


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
            pass
        except (OSError, subprocess.TimeoutExpired):
            pass
        _, info = _run_bluetoothctl_script(f"info {mac}\n", timeout=10)
        if "Connected: yes" in info:
            return True
        time.sleep(1.0)
    return False


def _mac_is_paired(mac: str) -> bool:
    mac = _normalize_mac(mac)
    return any(m == mac for m, _ in list_paired_devices())


def pair_mac(mac: str) -> tuple[bool, str]:
    mac = _normalize_mac(mac)
    timeout = float(getattr(config, "BLUETOOTH_PAIR_TIMEOUT_SEC", 45))
    script = f"""{_BT_AGENT_SCRIPT}
power on
pair {mac}
trust {mac}
"""
    _, out = _run_bluetoothctl_script(script, timeout=timeout)
    if "Pairing successful" in out or "AlreadyExists" in out or "succeeded" in out.lower():
        return True, ""
    if "Failed" in out and "Paired: yes" not in out:
        return False, "Eşleştirme tamamlanamadı kanka. Kulaklık eşleştirme modunda mı?"
    return True, ""


def connect_mac(mac: str, *, try_pair: bool = True) -> tuple[bool, str]:
    mac = _normalize_mac(mac)
    if try_pair and not _mac_is_paired(mac):
        ok, err = pair_mac(mac)
        if not ok:
            return False, err

    timeout = float(config.BLUETOOTH_CONNECT_TIMEOUT_SEC)
    script = f"""{_BT_AGENT_SCRIPT}
power on
trust {mac}
connect {mac}
"""
    _, out = _run_bluetoothctl_script(script, timeout=timeout + 5)
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
    return [(i, name, mac) for i, (mac, name) in enumerate(devices[:max_n], start=1)]


def _parse_device_number(text: str) -> int | None:
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
    m = re.search(r"(\d+)\s*numaraya\s+eslestir", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\.\s*numara", low)
    if m:
        return int(m.group(1))
    m = re.search(r"^(\d+)\s*$", low)
    if m and len(low) <= 4:
        return int(m.group(1))
    for word, num in _TURKISH_NUMBERS.items():
        if re.search(rf"\b{word}\s+numara", low):
            return num
        if re.search(rf"\b{word}\s+numaraya\s+baglan", low):
            return num
        if re.search(rf"\b{word}\s+numaraya\s+eslestir", low):
            return num
        if re.search(rf"numara\s+{word}\b", low):
            return num
    return None


def _wants_pair(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in (
            "eslestir",
            "esle",
            "pair",
            "bagla ve eslestir",
        )
    )


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


def _matches_rescan(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in (
            "yeniden tara",
            "tekrar tara",
            "cihazlari tara",
            "bluetooth tara",
            "tara",
        )
    )


def _reset_session() -> None:
    global _session
    _session = BtSession()


def _list_replies(intro_key: str, devices: list[tuple[int, str, str]], *, scan_hint: bool) -> list[str]:
    replies: list[str] = [
        phrases.pick(intro_key, fallback="Cihazlar kanka:"),
    ]
    for num, name, _mac in devices:
        replies.append(f"{num}. {name}")
    if scan_hint:
        replies.append(
            phrases.pick(
                "bt_await_pair_or_connect",
                fallback="Numara söyle kanka. Bağlanmak için iki numaraya bağlan, ilk eşleştirmek için iki numaraya eşleştir de.",
            )
        )
    else:
        replies.append(
            phrases.pick(
                "bt_await_number",
                fallback="Hangi numaraya bağlanmak istiyorsun kanka?",
            )
        )
    return replies


def _finish_connect(mac: str, name: str, index: int | None = None) -> list[str]:
    global _session
    set_headphone_output(mac)
    _session.phase = "connected"
    _session.connected_mac = mac
    _session.connected_index = index
    _save_last_mac(mac, name)
    msg = phrases.pick(
        "bt_connected",
        fallback=f"{name} kulaklığına bağlandım kanka, ses artık kulaklıktan geliyor.",
    )
    n = str(index) if index is not None else "1"
    return [msg.replace("{n}", n).replace("{name}", name)]


def _try_auto_connect_paired(paired: list[tuple[str, str]]) -> list[str] | None:
    """Tek eşleşmiş veya son kullanılan cihaza otomatik bağlan."""
    if not paired:
        return None

    auto = bool(getattr(config, "BLUETOOTH_AUTO_CONNECT_PAIRED", True))
    if not auto:
        return None

    candidates: list[tuple[str, str]] = []
    last = _load_last_mac()
    if last:
        for mac, name in paired:
            if mac == last:
                candidates.append((mac, name))
                break
    if not candidates:
        candidates = list(paired)

    if len(paired) == 1:
        candidates = list(paired)

    for mac, name in candidates:
        logger.info("BT otomatik bağlanıyor: %s (%s)", name, mac)
        ok, err = connect_mac(mac, try_pair=False)
        if ok:
            return _finish_connect(mac, name, index=1)

    if len(paired) == 1:
        return [
            phrases.pick("bt_error_connect", fallback="Eşleşmiş cihaza bağlanamadım kanka."),
        ]

    return None


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
    _session.discovered_only = False

    replies: list[str] = [
        phrases.pick("bt_open", fallback="Bluetooth kulaklık modunu açtım kanka."),
    ]

    paired = list_paired_devices()
    _try_auto_connect_paired(paired)
    if _session.phase == "connected":
        return replies + [
            phrases.pick(
                "bt_auto_connected",
                fallback="Eşleşmiş kulaklığa bağlandım kanka, ses kulaklıktan geliyor.",
            )
        ]

    if paired:
        _session.devices = _numbered_list(paired)
        _session.phase = "awaiting"
        _session.discovered_only = False
        replies += _list_replies("bt_paired_list_intro", _session.devices, scan_hint=False)
        return replies

    raw = scan_devices()
    if not raw:
        _session.phase = "awaiting"
        _session.devices = []
        _session.discovered_only = True
        replies += [
            phrases.pick(
                "bt_error_no_devices",
                fallback="Hiç cihaz görünmüyor kanka. Kulaklığı eşleştirme moduna al ve yeniden tara de.",
            ),
            phrases.pick(
                "bt_pair_hint",
                fallback="Kulaklığı eşleştirme moduna al kanka. Sonra yeniden tara veya numara ile eşleştir.",
            ),
        ]
        return replies

    _session.devices = _numbered_list(raw)
    _session.phase = "awaiting"
    _session.discovered_only = True
    replies += _list_replies("bt_scan_list_intro", _session.devices, scan_hint=True)
    return replies


def rescan_devices() -> list[str]:
    global _session
    if not _session.active:
        return [phrases.pick("bt_error_not_active", fallback="Önce kulaklık modunu aç kanka.")]

    set_speaker_output()
    raw = scan_devices()
    if not raw:
        _session.devices = []
        _session.phase = "awaiting"
        return [
            phrases.pick("bt_error_no_devices", fallback="Yine cihaz bulamadım kanka."),
            phrases.pick("bt_pair_hint", fallback="Kulaklığı eşleştirme modunda tut kanka."),
        ]

    _session.devices = _numbered_list(raw)
    _session.phase = "awaiting"
    _session.discovered_only = True
    out = [phrases.pick("bt_rescan_ok", fallback="Tamam kanka, yeniden taradım.")]
    out += _list_replies("bt_scan_list_intro", _session.devices, scan_hint=True)
    return out


def pair_and_connect_by_index(index: int) -> list[str]:
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
    ok, err = pair_mac(mac)
    if not ok:
        return [err or phrases.pick("bt_error_pair", fallback="Eşleştiremedim kanka.")]
    ok, err = connect_mac(mac, try_pair=False)
    if not ok:
        return [err or phrases.pick("bt_error_connect", fallback="Eşleştirdim ama bağlanamadım kanka.")]

    return _finish_connect(mac, name, index=index)


def connect_by_index(index: int, *, force_pair: bool = False) -> list[str]:
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
    try_pair = force_pair or (s.discovered_only and not _mac_is_paired(mac))
    ok, err = connect_mac(mac, try_pair=try_pair)
    if not ok:
        return [err or phrases.pick("bt_error_connect", fallback="Bağlanamadım kanka, bir daha dene.")]

    return _finish_connect(mac, name, index=index)


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

    if _session.active and _matches_rescan(text):
        return True, rescan_devices()

    num = _parse_device_number(text)
    if num is not None and _session.active:
        if _wants_pair(text):
            return True, pair_and_connect_by_index(num)
        if _session.phase in ("awaiting", "connected") or "baglan" in _norm_text(text):
            return True, connect_by_index(num, force_pair=_wants_pair(text))

    if _session.active and _session.phase == "awaiting":
        low = _norm_text(text)
        if "baglan" in low or "eslestir" in low:
            return True, [
                phrases.pick(
                    "bt_await_pair_or_connect",
                    fallback="Numara söyle kanka. Örneğin bir numaraya bağlan veya iki numaraya eşleştir.",
                )
            ]

    return False, []
