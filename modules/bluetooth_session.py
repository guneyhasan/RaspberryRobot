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
_NEW_DEVICE_LINE = re.compile(
    r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.+)$",
    re.MULTILINE,
)
_CHG_NAME_LINE = re.compile(
    r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.+)$",
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
    bluealsa_pcm: str | None = None  # bluealsa-aplay -L satırı (tam PCM adı)
    audio_on_headphone: bool = False


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
    """devices / paired list / [NEW] Device satırlarından MAC + ad."""
    by_mac: dict[str, str] = {}

    for m in _CHG_NAME_LINE.finditer(output or ""):
        by_mac[m.group(1).upper()] = m.group(2).strip()

    for regex in (_DEVICE_LINE, _NEW_DEVICE_LINE):
        for m in regex.finditer(output or ""):
            mac = m.group(1).upper()
            name = m.group(2).strip()
            if mac not in by_mac or len(name) > len(by_mac.get(mac, "")):
                by_mac[mac] = name

    return list(by_mac.items())


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
    """Tahmini PCM adı; mümkünse discover_bluealsa_pcm kullanın."""
    mac = _normalize_mac(mac)
    if _session.bluealsa_pcm and _session.connected_mac == mac:
        return _session.bluealsa_pcm
    profile = getattr(config, "BLUETOOTH_A2DP_PROFILE", "a2dp") or "a2dp"
    return f"bluealsa:DEV={mac},PROFILE={profile}"


def list_bluealsa_playback_pcms(*, log: bool = True) -> list[tuple[str, str]]:
    """bluealsa-aplay -L → [(MAC, tam_pcm_adi), ...]"""
    try:
        r = subprocess.run(
            ["bluealsa-aplay", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logger.warning("bluealsa-aplay bulunamadı")
        return []
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("bluealsa-aplay -L hatası: %s", e)
        return []

    listing = (r.stdout or "") + (r.stderr or "")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_line in listing.splitlines():
        line = raw_line.strip()
        if not line.startswith("bluealsa:DEV="):
            continue
        pcm = line.split()[0]
        m = re.search(r"DEV=([0-9A-Fa-f:]{17})", pcm, re.I)
        if not m:
            continue
        mac = _normalize_mac(m.group(1))
        if mac in seen:
            continue
        seen.add(mac)
        if "a2dp" in pcm.lower() or "PROFILE=" in pcm.upper():
            out.append((mac, pcm))
    if log:
        if out:
            logger.info("bluealsa PCM listesi: %s", [p[1] for p in out])
        else:
            logger.debug("bluealsa PCM listesi: (boş)")
    return out


def discover_bluealsa_pcm(mac: str | None = None, *, log: bool = False) -> str | None:
    """Gerçekten açılabilir PCM adını döndürür; yoksa None."""
    mac_n = _normalize_mac(mac) if mac else None
    pcms = list_bluealsa_playback_pcms(log=log)
    if not pcms:
        return None
    if mac_n:
        for m, pcm in pcms:
            if m == mac_n:
                return pcm
        return None
    if len(pcms) == 1:
        return pcms[0][1]
    last = _load_last_mac()
    if last:
        for m, pcm in pcms:
            if m == last:
                return pcm
    return pcms[0][1]


def ensure_adapter_ready() -> tuple[bool, str]:
    rc, out = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "power on\n",
        timeout=15,
    )
    if rc == 127:
        return False, "bluetoothctl bulunamadı kanka."
    if "Powered: yes" in out or "succeeded" in out.lower() or rc == 0:
        settle = float(getattr(config, "BLUETOOTH_ADAPTER_SETTLE_SEC", 1.5))
        if settle > 0:
            time.sleep(settle)
        return True, ""
    return False, "Bluetooth adaptörü açılamadı kanka."


def _bt_device_info(mac: str) -> str:
    _, info = _run_bluetoothctl_script(f"info {_normalize_mac(mac)}\n", timeout=10)
    return info


def _bt_is_connected(mac: str) -> bool:
    return "Connected: yes" in _bt_device_info(mac)


def disconnect_device(mac: str | None = None) -> None:
    """Kulaklığı kes; adaptör açık kalır (yeniden bağlanma için önerilir)."""
    if mac:
        _run_bluetoothctl_script(f"disconnect {_normalize_mac(mac)}\n", timeout=12)
    else:
        _run_bluetoothctl_script("disconnect\n", timeout=12)


def _reconnect_bluetooth_mac(mac: str) -> None:
    """A2DP/PCM gelmiyorsa disconnect → connect (bluealsa profilini yeniden tetikler)."""
    mac = _normalize_mac(mac)
    gap = float(getattr(config, "BLUETOOTH_RECONNECT_GAP_SEC", 2))
    logger.info("BT A2DP yeniden bağlanıyor: %s", mac)
    _run_bluetoothctl_script(
        f"""{_BT_AGENT_SCRIPT}
power on
disconnect {mac}
""",
        timeout=15,
    )
    time.sleep(max(0.5, gap))
    timeout = float(config.BLUETOOTH_CONNECT_TIMEOUT_SEC)
    _run_bluetoothctl_script(
        f"""{_BT_AGENT_SCRIPT}
trust {mac}
connect {mac}
""",
        timeout=timeout + 5,
    )
    time.sleep(max(0.5, gap))


def restart_bluealsa_service() -> bool:
    if not getattr(config, "BLUETOOTH_RESTART_BLUEALSA_ON_RECONNECT", True):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "restart", "bluealsa"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        logger.warning("bluealsa restart başarısız: %s", e)
        return False
    if r.returncode != 0:
        logger.warning(
            "bluealsa restart rc=%s: %s",
            r.returncode,
            ((r.stderr or "") + (r.stdout or ""))[:200],
        )
        return False
    logger.info("bluealsa servisi yeniden başlatıldı")
    time.sleep(2.0)
    return True


def _bluetoothctl_live_scan(wait_sec: float) -> str:
    """
    Tek bluetoothctl sürecinde scan on → bekle → devices.
    Ayrı süreçlerde scan on + quit taramayı hemen durdurur (BlueZ 5.66+).
    """
    wait_sec = max(4.0, wait_sec)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.stdin is None or proc.stdout is None:
            return ""
        for chunk in (_BT_AGENT_SCRIPT, "power on\n", "scan on\n"):
            proc.stdin.write(chunk)
            proc.stdin.flush()
        time.sleep(wait_sec)
        proc.stdin.write("devices\nscan off\nquit\n")
        proc.stdin.flush()
        out, _ = proc.communicate(timeout=wait_sec + 25)
        logger.debug("BT live scan çıktı uzunluğu: %s", len(out or ""))
        return out or ""
    except subprocess.TimeoutExpired:
        logger.warning("BT live scan zaman aşımı")
        if proc is not None:
            proc.kill()
        return ""
    except OSError as e:
        logger.warning("BT live scan hatası: %s", e)
        return ""


def list_paired_devices() -> list[tuple[str, str]]:
    """BlueZ 5.66+: paired-devices yok; menu paired veya devices+info."""
    _, out_menu = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "menu paired\nlist\nback\n",
        timeout=20,
    )
    devs = _parse_devices(out_menu)
    if devs:
        logger.info("BT paired (menu paired): %s cihaz", len(devs))
        return devs

    _, out_legacy = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "paired-devices\n",
        timeout=15,
    )
    devs = _parse_devices(out_legacy)
    if devs:
        logger.info("BT paired (legacy): %s cihaz", len(devs))
        return devs

    _, out_all = _run_bluetoothctl_script(
        _BT_AGENT_SCRIPT + "devices\n",
        timeout=15,
    )
    all_devs = _parse_devices(out_all)
    paired: list[tuple[str, str]] = []
    check_limit = int(getattr(config, "BLUETOOTH_MAX_LIST", 0))
    check_devs = all_devs if check_limit <= 0 else all_devs[: check_limit * 2]
    for mac, name in check_devs:
        _, info = _run_bluetoothctl_script(f"info {mac}\n", timeout=10)
        if "Paired: yes" in info:
            paired.append((mac, name))
    logger.info("BT paired (info filter): %s / %s", len(paired), len(all_devs))
    return paired


def scan_devices(timeout_sec: float | None = None) -> list[tuple[str, str]]:
    """Yakındaki + eşleşmiş cihazları birleştir (tek oturumda tarama)."""
    timeout_sec = timeout_sec if timeout_sec is not None else float(config.BLUETOOTH_SCAN_SEC)

    out = _bluetoothctl_live_scan(timeout_sec)
    discovered = _parse_devices(out)
    paired = list_paired_devices()
    merged = _merge_device_lists(paired, discovered)
    logger.info(
        "BT tarama: paired=%s discovered=%s merged=%s (live_scan=%s)",
        len(paired),
        len(discovered),
        len(merged),
        len(out),
    )
    return merged


def wait_a2dp_ready(mac: str, timeout_sec: float | None = None) -> bool:
    """bluealsa-aplay -L içinde PCM görünene kadar bekler; gerekirse BT/bluealsa kurtarma."""
    timeout_sec = timeout_sec if timeout_sec is not None else float(
        getattr(config, "BLUETOOTH_A2DP_WAIT_SEC", config.BLUETOOTH_CONNECT_TIMEOUT_SEC)
    )
    mac = _normalize_mac(mac)
    t0 = time.time()
    deadline = t0 + timeout_sec
    reconnects = 0
    bluealsa_restarted = False
    poll = 0

    while time.time() < deadline:
        pcm = discover_bluealsa_pcm(mac, log=False)
        if pcm:
            _session.bluealsa_pcm = pcm
            logger.info("A2DP hazır (%0.1fs): %s", time.time() - t0, pcm)
            return True

        poll += 1
        elapsed = time.time() - t0
        connected = _bt_is_connected(mac) if poll % 3 == 0 else False

        if connected and reconnects < 1 and elapsed >= 4.0:
            _reconnect_bluetooth_mac(mac)
            reconnects += 1
        elif connected and reconnects == 1 and elapsed >= timeout_sec * 0.45:
            if restart_bluealsa_service():
                bluealsa_restarted = True
            _reconnect_bluetooth_mac(mac)
            reconnects += 2
        elif (
            connected
            and reconnects >= 2
            and not bluealsa_restarted
            and elapsed >= timeout_sec * 0.7
        ):
            if restart_bluealsa_service():
                bluealsa_restarted = True
                _reconnect_bluetooth_mac(mac)
                reconnects += 1

        time.sleep(1.2)

    logger.warning(
        "A2DP/bluealsa PCM hazır değil: %s (%.0fs, reconnects=%s, bluealsa_restart=%s)",
        mac,
        time.time() - t0,
        reconnects,
        bluealsa_restarted,
    )
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
    if not _bt_is_connected(mac):
        return False, "Bağlantı kurulamadı kanka."
    return True, ""


def disconnect_and_power_off(mac: str | None = None) -> None:
    """Varsayılan: yalnızca disconnect (adaptör açık). İsteğe bağlı power off."""
    disconnect_device(mac)
    if getattr(config, "BLUETOOTH_POWER_OFF_ON_CLOSE", False):
        _run_bluetoothctl_script("power off\n", timeout=15)
        logger.info("BT adaptör kapatıldı (BLUETOOTH_POWER_OFF_ON_CLOSE=1)")


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


def set_headphone_output(mac: str) -> bool:
    """Kulaklık PCM'i bluealsa-aplay -L'den alır; yoksa False."""
    pcm = discover_bluealsa_pcm(mac)
    if not pcm:
        logger.error(
            "bluealsa PCM yok MAC=%s — kulaklık bağlı olsa bile A2DP hazır değil. "
            "Kontrol: bluealsa-aplay -L",
            mac,
        )
        return False
    _session.bluealsa_pcm = pcm
    _session.audio_on_headphone = True
    tts.set_output_device(pcm)
    logger.info("BT: kulaklık çıkışı → %s", pcm)
    return True


def ensure_headphone_audio(mac: str | None = None) -> bool:
    """Bağlı oturumda PCM yoksa yeniden dene; yoksa hoparlör."""
    mac = _normalize_mac(mac or _session.connected_mac or "")
    if not mac:
        return False
    if set_headphone_output(mac):
        return True
    set_speaker_output()
    _session.audio_on_headphone = False
    return False


def _list_limit() -> int | None:
    """None = sınırsız (tüm taranan cihazlar)."""
    n = int(getattr(config, "BLUETOOTH_MAX_LIST", 0))
    return n if n > 0 else None


def _mac_dashed(mac: str) -> str:
    return _normalize_mac(mac).replace(":", "-")


def _is_friendly_name(name: str, mac: str) -> bool:
    """Gerçek cihaz adı mı yoksa sadece MAC benzeri mi?"""
    name = (name or "").strip()
    if not name:
        return False
    mac_u = _normalize_mac(mac)
    name_u = name.upper().replace(":", "-")
    if name_u == _mac_dashed(mac):
        return False
    hex_only = re.sub(r"[^0-9A-F]", "", name_u)
    mac_hex = mac_u.replace(":", "")
    if hex_only == mac_hex and len(hex_only) >= 10:
        return False
    if re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", name):
        return True
    if " " in name and len(name) > 6:
        return True
    if re.fullmatch(r"[0-9A-Fa-f:-]+", name) and "-" in name:
        return False
    return len(name) >= 5 and not re.fullmatch(r"[0-9A-Fa-f:-]+", name)


def _sort_devices_for_display(devices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """İsimli cihazlar önce, MAC/isimsiz olanlar sonra (numaralar buna göre)."""
    named: list[tuple[str, str]] = []
    unnamed: list[tuple[str, str]] = []
    for mac, name in devices:
        if _is_friendly_name(name, mac):
            named.append((mac, name))
        else:
            unnamed.append((mac, name))
    return named + unnamed


def _tts_device_line(index: int, name: str, mac: str) -> str:
    if _is_friendly_name(name, mac):
        return f"{index}. {name}"
    return f"{index} numara, isimsiz bluetooth cihazı"


def _numbered_list(devices: list[tuple[str, str]]) -> list[tuple[int, str, str]]:
    ordered = _sort_devices_for_display(devices)
    lim = _list_limit()
    if lim is not None:
        ordered = ordered[:lim]
    return [(i, name, mac) for i, (mac, name) in enumerate(ordered, start=1)]


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
    total = len(devices)
    replies: list[str] = [
        phrases.pick(intro_key, fallback="Cihazlar kanka:"),
        phrases.pick(
            "bt_list_total",
            fallback=f"Toplam {total} cihaz buldum kanka.",
        ).replace("{n}", str(total)),
    ]

    named = [(n, name, mac) for n, name, mac in devices if _is_friendly_name(name, mac)]
    unnamed = [(n, name, mac) for n, name, mac in devices if not _is_friendly_name(name, mac)]

    if named:
        replies.append(
            phrases.pick(
                "bt_list_named_first",
                fallback="Önce isimli cihazlar kanka:",
            )
        )
        for num, name, mac in named:
            replies.append(_tts_device_line(num, name, mac))

    if unnamed:
        if named:
            replies.append(
                phrases.pick(
                    "bt_list_unnamed_rest",
                    fallback="İsimsiz veya MAC adresli cihazlar da var kanka:",
                )
            )
        for num, name, mac in unnamed:
            replies.append(_tts_device_line(num, name, mac))

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
    _session.phase = "connected"
    _session.connected_mac = mac
    _session.connected_index = index
    _save_last_mac(mac, name)

    wait_a2dp_ready(mac)
    if set_headphone_output(mac):
        msg = phrases.pick(
            "bt_connected",
            fallback=f"{name} kulaklığına bağlandım kanka, ses artık kulaklıktan geliyor.",
        )
    else:
        set_speaker_output()
        msg = phrases.pick(
            "bt_connected_speaker_fallback",
            fallback=(
                f"Bağlandım kanka ama kulaklık sesi henüz hazır değil; şimdilik robot hoparlöründen konuşuyorum. "
                f"bluealsa-aplay -L boşsa: sudo systemctl restart bluealsa"
            ),
        )
    n = str(index) if index is not None else "1"
    return [msg.replace("{n}", n).replace("{name}", name)]


def _matches_manual_sync(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in (
            "kulakliga baglandim",
            "kulaklik baglandim",
            "bluetooth kulakliga baglandim",
            "bluetooth kulaklig baglandim",
            "kulakliga bagli",
            "kulaklik moduna baglandim",
        )
    )


def sync_headphone_from_system() -> list[str]:
    """Elle bluetoothctl ile bağlandıktan sonra bluealsa PCM'den oturumu senkronize et."""
    global _session
    pcms = list_bluealsa_playback_pcms()
    if not pcms:
        return [
            phrases.pick(
                "bt_error_no_bluealsa_pcm",
                fallback=(
                    "Kulaklık sistemde görünmüyor kanka. bluealsa-aplay -L boş mu? "
                    "Kulaklığı bağla, sonra: sudo systemctl restart bluealsa"
                ),
            )
        ]

    mac, pcm = pcms[0]
    last = _load_last_mac()
    if last:
        for m, p in pcms:
            if m == last:
                mac, pcm = m, p
                break

    _session.active = True
    _session.phase = "connected"
    _session.connected_mac = mac
    _session.bluealsa_pcm = pcm
    _session.audio_on_headphone = True
    tts.set_output_device(pcm)
    _save_last_mac(mac, "")
    logger.info("BT manuel senkron: %s → %s", mac, pcm)
    return [
        phrases.pick(
            "bt_manual_sync_ok",
            fallback="Tamam kanka, kulaklık sesini duyacaksın artık.",
        )
    ]


def _try_auto_connect_paired(paired: list[tuple[str, str]]) -> list[str] | None:
    """Eşleşmiş cihaza bağlan; A2DP bekleme _finish_connect içinde."""
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

    if _matches_manual_sync(text):
        return True, sync_headphone_from_system()

    if (
        _session.phase == "connected"
        and _session.connected_mac
        and not _session.audio_on_headphone
    ):
        ensure_headphone_audio(_session.connected_mac)

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
