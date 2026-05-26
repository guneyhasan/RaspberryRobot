"""Sesli WiFi modu: tara, numara veya isimle seç, şifreli ağda sesle şifre al."""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import config
from modules import phrases, tts, wifi_backend
from modules.wifi_backend import WiFiNetwork

logger = logging.getLogger(__name__)

Phase = Literal["idle", "scanning", "awaiting", "awaiting_password", "connecting"]

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

_wifi_io_lock = threading.RLock()


@dataclass
class WifiSession:
    active: bool = False
    phase: Phase = "idle"
    networks: list[tuple[int, str, int, str, bool]] = field(default_factory=list)
    # (index, ssid, signal, security, needs_password)
    selected_index: int | None = None
    selected_ssid: str | None = None
    pending_password: str | None = None


_session = WifiSession()
_open_ack_spoken = False


def session() -> WifiSession:
    return _session


def is_enabled() -> bool:
    return bool(getattr(config, "WIFI_ENABLED", True))


def is_wifi_mode_active() -> bool:
    return _session.active


def is_password_turn() -> bool:
    return _session.active and _session.phase == "awaiting_password"


def is_awaiting_selection() -> bool:
    return _session.active and _session.phase == "awaiting"


def consume_open_ack_skip() -> bool:
    global _open_ack_spoken
    if _open_ack_spoken:
        _open_ack_spoken = False
        return True
    return False


def _speak_immediate(text: str) -> None:
    s = (text or "").strip()
    if not s:
        return
    try:
        tts.speak(s, prefer_online=False)
    except Exception as e:
        logger.warning("WiFi anlık TTS başarısız: %s", e)


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


def _list_limit() -> int | None:
    lim = int(getattr(config, "WIFI_MAX_LIST", 10))
    if lim <= 0:
        return None
    return lim


def _reset_session() -> None:
    global _session
    _session = WifiSession()


def _signal_label(signal: int) -> str:
    if signal >= 70:
        return "güçlü"
    if signal >= 45:
        return "orta"
    return "zayıf"


def _security_label(needs_password: bool) -> str:
    return "şifreli" if needs_password else "açık"


def _network_line(index: int, ssid: str, signal: int, needs_password: bool) -> str:
    return (
        f"{index}. {ssid}, {_signal_label(signal)}, {_security_label(needs_password)}."
    )


def _numbered_list(networks: list[WiFiNetwork]) -> list[tuple[int, str, int, str, bool]]:
    lim = _list_limit()
    items = networks if lim is None else networks[:lim]
    return [
        (i, n.ssid, n.signal, n.security, n.needs_password)
        for i, n in enumerate(items, start=1)
    ]


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
    m = re.search(r"^(\d+)\s*$", low)
    if m and len(low) <= 4:
        return int(m.group(1))
    for word, num in _TURKISH_NUMBERS.items():
        if re.search(rf"\b{word}\s+numara", low):
            return num
        if re.search(rf"\b{word}\s+numaraya\s+baglan", low):
            return num
        if re.search(rf"numara\s+{word}\b", low):
            return num
    if re.fullmatch(r"[a-z]+", low) and low in _TURKISH_NUMBERS:
        return _TURKISH_NUMBERS[low]
    return None


def _matches_open(text: str) -> bool:
    low = _norm_text(text)
    triggers = (
        "wifi modunu ac",
        "wifi modu ac",
        "wifi aglarini listele",
        "wifi aglarini goster",
        "wifi aglarina bak",
        "wifi tara",
        "internete baglan",
        "wifi baglan",
        "wifi ac",
    )
    return any(t in low for t in triggers)


def _matches_close(text: str) -> bool:
    low = _norm_text(text)
    return any(
        t in low
        for t in (
            "wifi modunu kapat",
            "wifi modu kapat",
            "wifi kapat",
        )
    )


def _matches_rescan(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in (
            "yeniden tara",
            "tekrar tara",
            "wifi tara",
            "aglari tara",
        )
    )


def _matches_cancel(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in ("iptal", "vazgec", "wifi modundan cik")
    )


def _matches_password_retry(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in (
            "sifreyi tekrar",
            "tekrar söyle",
            "tekrar soyle",
            "yanlis sifre",
        )
    )


def _matches_confirm_yes(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in ("evet", "onayla", "onayliyorum", "tamam baglan", "dogru")
    )


def _matches_confirm_no(text: str) -> bool:
    low = _norm_text(text)
    return any(
        k in low
        for k in ("hayir", "yanlis", "tekrar", "iptal", "vazgec")
    )


def _bt_blocks_wifi() -> bool:
    try:
        from modules import bluetooth_session

        if bluetooth_session.is_bt_mode_active():
            return True
    except Exception:
        pass
    return False


def _wifi_blocks_bt_message() -> str:
    return phrases.pick(
        "wifi_error_bt_active",
        fallback="Önce bluetooth kulaklık modunu kapat kanka, sonra wifi moduna geçelim.",
    )


def _extract_password(text: str) -> str:
    raw = (text or "").strip()
    low = _norm_text(raw)
    for prefix in (
        "sifrem",
        "sifre",
        "parola",
        "password",
        "wifi sifresi",
        "ag sifresi",
    ):
        if low.startswith(prefix):
            raw = raw[len(prefix) :].strip(" :,-")
            break
    return raw.strip()


def _find_ssid_by_name(text: str) -> int | None:
    low = _norm_text(text)
    if not low or not _session.networks:
        return None
    best: tuple[int, int] | None = None
    for num, ssid, _sig, _sec, _np in _session.networks:
        ssid_norm = _norm_text(ssid)
        if not ssid_norm:
            continue
        if ssid_norm in low or low in ssid_norm:
            score = len(ssid_norm)
            if best is None or score > best[1]:
                best = (num, score)
        elif len(ssid_norm) >= 4 and ssid_norm[:4] in low:
            score = 4
            if best is None or score > best[1]:
                best = (num, score)
    return best[0] if best else None


def _list_replies(networks: list[tuple[int, str, int, str, bool]]) -> list[str]:
    total = len(networks)
    replies: list[str] = [
        phrases.pick("wifi_list_intro", fallback="Yakındaki wifi ağları kanka:"),
        phrases.pick(
            "wifi_list_total",
            fallback=f"Toplam {total} ağ buldum kanka.",
        ).replace("{n}", str(total)),
    ]
    for num, ssid, signal, _sec, needs_pw in networks:
        replies.append(_network_line(num, ssid, signal, needs_pw))
    replies.append(
        phrases.pick(
            "wifi_await_number",
            fallback="Hangi numaraya bağlanmak istiyorsun? Açık ağlara şifresiz bağlanırım.",
        )
    )
    return replies


def _scan_and_list() -> list[str]:
    with _wifi_io_lock:
        nets, err = wifi_backend.scan_networks()
    if err and not nets:
        _session.phase = "awaiting"
        _session.networks = []
        return [
            phrases.pick(
                "wifi_error_scan",
                fallback=f"Tarama yapamadım kanka. {err}",
            )
        ]
    if not nets:
        _session.phase = "awaiting"
        _session.networks = []
        return [
            phrases.pick(
                "wifi_error_no_networks",
                fallback="Hiç wifi ağı görünmüyor kanka. Yeniden tara de.",
            )
        ]
    _session.networks = _numbered_list(nets)
    _session.phase = "awaiting"
    return _list_replies(_session.networks)


def _select_network(index: int) -> list[str]:
    if index < 1 or index > len(_session.networks):
        return [
            phrases.pick(
                "wifi_error_invalid_number",
                fallback=f"Geçersiz numara kanka. 1 ile {len(_session.networks)} arasında söyle.",
            )
        ]
    _num, ssid, _signal, _sec, needs_pw = _session.networks[index - 1]
    _session.selected_index = index
    _session.selected_ssid = ssid
    _session.pending_password = None

    if needs_pw:
        _session.phase = "awaiting_password"
        return [
            phrases.pick(
                "wifi_await_password",
                fallback=f"{ssid} şifreli kanka. Şifreyi söyle, bağlayayım.",
            )
        ]

    return _connect_selected(None)


def _connect_selected(password: str | None) -> list[str]:
    ssid = _session.selected_ssid
    if not ssid:
        _session.phase = "awaiting"
        return [
            phrases.pick(
                "wifi_error_not_active",
                fallback="Önce bir ağ seç kanka.",
            )
        ]

    _session.phase = "connecting"
    logger.info("WiFi bağlanıyor: ssid=%s (şifre: %s)", ssid, "evet" if password else "hayır")

    with _wifi_io_lock:
        ok, detail = wifi_backend.connect(ssid, password)

    if ok:
        _session.phase = "idle"
        _session.active = False
        _session.pending_password = None
        msg = phrases.pick(
            "wifi_connected",
            fallback=f"{ssid} ağına bağlandım kanka.",
        ).replace("{ssid}", ssid)
        logger.info("WiFi bağlantı OK: %s", ssid)
        return [msg]

    logger.warning("WiFi bağlantı hata: %s | %s", ssid, detail[:200] if detail else "")
    _session.phase = "awaiting_password" if password is not None else "awaiting"
    if password is not None:
        return [
            phrases.pick(
                "wifi_error_connect_password",
                fallback="Bağlanamadım kanka, şifre yanlış olabilir. Şifreyi tekrar söyle veya başka numara seç.",
            )
        ]
    return [
        phrases.pick(
            "wifi_error_connect",
            fallback="Bağlanamadım kanka, bir daha dene veya başka ağ seç.",
        )
    ]


def _handle_password_turn(text: str) -> list[str]:
    if _matches_cancel(text):
        _session.phase = "awaiting"
        _session.pending_password = None
        return [
            phrases.pick(
                "wifi_password_cancelled",
                fallback="Tamam kanka, şifreyi iptal ettim. Numara veya ağ adı söyle.",
            )
        ]

    if _matches_password_retry(text):
        return [
            phrases.pick(
                "wifi_await_password",
                fallback="Tamam, şifreyi tekrar söyle kanka.",
            )
        ]

    pwd = _extract_password(text)
    if not pwd or len(pwd) < 1:
        return [
            phrases.pick(
                "wifi_await_password",
                fallback="Şifreyi duyamadım kanka, tekrar söyle.",
            )
        ]

    if bool(getattr(config, "WIFI_PASSWORD_CONFIRM", False)):
        _session.pending_password = pwd
        _session.phase = "awaiting_password"
        confirm_msg = phrases.pick(
            "wifi_password_confirm",
            fallback="Şifren {n} karakter. Onaylıyor musun? Evet de veya şifreyi tekrar söyle.",
        ).replace("{n}", str(len(pwd)))
        return [confirm_msg]

    return _connect_selected(pwd)


def _handle_password_confirm(text: str) -> list[str]:
    if _matches_confirm_yes(text) and _session.pending_password:
        pwd = _session.pending_password
        _session.pending_password = None
        return _connect_selected(pwd)
    if _matches_confirm_no(text):
        _session.pending_password = None
        return [
            phrases.pick(
                "wifi_await_password",
                fallback="Tamam kanka, şifreyi tekrar söyle.",
            )
        ]
    return _handle_password_turn(text)


def connect_by_index(index: int) -> list[str]:
    return _select_network(index)


def connect_by_ssid_name(text: str) -> list[str] | None:
    idx = _find_ssid_by_name(text)
    if idx is None:
        return None
    return _select_network(idx)


def open_mode() -> list[str]:
    global _session, _open_ack_spoken
    if not is_enabled():
        return [
            phrases.pick(
                "wifi_error_disabled",
                fallback="Wifi modu kapalı kanka.",
            )
        ]

    if _bt_blocks_wifi():
        return [_wifi_blocks_bt_message()]

    backend = wifi_backend.detect_backend()
    if backend == "none":
        return [
            phrases.pick(
                "wifi_error_no_backend",
                fallback="WiFi yönetimi kurulu değil kanka. network-manager veya wpa_cli gerekli.",
            )
        ]

    _open_ack_spoken = False
    ack = phrases.pick(
        "wifi_open",
        fallback="Tamam kanka, wifi ağlarına bakıyorum.",
    )
    _session = WifiSession(active=True, phase="scanning")
    _speak_immediate(ack)
    _open_ack_spoken = True

    replies = [ack] + _scan_and_list()
    return replies


def rescan_devices() -> list[str]:
    if not _session.active:
        return [
            phrases.pick(
                "wifi_error_not_active",
                fallback="Önce wifi modunu aç kanka.",
            )
        ]
    _session.phase = "scanning"
    intro = phrases.pick("wifi_rescan_ok", fallback="Tamam kanka, yeniden taradım.")
    return [intro] + _scan_and_list()


def close_mode() -> list[str]:
    replies = [
        phrases.pick(
            "wifi_closed",
            fallback="Wifi modunu kapattım kanka.",
        )
    ]
    _reset_session()
    return replies


def speak_replies(
    replies: list[str],
    *,
    skip_first: bool = False,
    speak_line: Callable[[str], tuple[str, float]],
    on_response: Callable[[str, int, int], None] | None = None,
    on_tts: Callable[[str, float], None] | None = None,
) -> None:
    total = len(replies)
    for idx, line in enumerate(replies):
        if not line.strip():
            continue
        if skip_first and idx == 0:
            if on_response:
                on_response(f"{line} (anlık TTS, atlandı)", idx + 1, total)
            continue
        if on_response:
            on_response(line, idx + 1, total)
        try:
            kind, duration = speak_line(line)
            if on_tts:
                on_tts(kind, duration)
        except Exception as e:
            logger.warning("WiFi TTS: %s", e)


def handle_turn(text: str) -> tuple[bool, list[str]]:
    if not is_enabled():
        if _matches_open(text) or _matches_close(text):
            return True, [
                phrases.pick(
                    "wifi_error_disabled",
                    fallback="Wifi modu kapalı kanka.",
                )
            ]
        return False, []

    if _matches_close(text):
        if _session.active:
            return True, close_mode()
        return True, [
            phrases.pick(
                "wifi_closed",
                fallback="Wifi modu zaten kapalı kanka.",
            )
        ]

    if _matches_open(text):
        return True, open_mode()

    if _session.active and _session.phase == "awaiting_password":
        if _session.pending_password and bool(getattr(config, "WIFI_PASSWORD_CONFIRM", False)):
            if _matches_confirm_yes(text) or _matches_confirm_no(text):
                return True, _handle_password_confirm(text)
        return True, _handle_password_turn(text)

    if _session.active and _matches_rescan(text):
        return True, rescan_devices()

    if _session.active and _matches_cancel(text):
        return True, close_mode()

    num = _parse_device_number(text)
    if num is not None and _session.active and _session.phase in ("awaiting", "connected"):
        return True, connect_by_index(num)

    if _session.active and _session.phase == "awaiting":
        ssid_replies = connect_by_ssid_name(text)
        if ssid_replies is not None:
            return True, ssid_replies
        low = _norm_text(text)
        if "baglan" in low:
            return True, [
                phrases.pick(
                    "wifi_await_number",
                    fallback="Numara veya ağ adı söyle kanka.",
                )
            ]

    return False, []


def notify_bt_open_blocked() -> str | None:
    """Bluetooth modu açılırken WiFi oturumu aktifse uyarı metni."""
    if is_wifi_mode_active():
        return phrases.pick(
            "wifi_error_wifi_active",
            fallback="Önce wifi modunu kapat kanka, sonra bluetooth kulaklık moduna geçelim.",
        )
    return None
