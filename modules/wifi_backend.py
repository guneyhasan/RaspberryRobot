"""WiFi tarama ve bağlantı: NetworkManager (nmcli) birincil, wpa_cli yedek."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

import config

logger = logging.getLogger(__name__)

BackendName = Literal["nmcli", "wpa_cli", "none"]


@dataclass(frozen=True)
class WiFiNetwork:
    ssid: str
    signal: int
    security: str
    in_use: bool

    @property
    def needs_password(self) -> bool:
        sec = (self.security or "").strip()
        if not sec or sec == "--":
            return False
        low = sec.casefold()
        if low in ("open", "none", "owe"):
            return False
        return True


_cached_backend: BackendName | None = None


def _run(
    args: list[str],
    *,
    timeout: float = 30.0,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _nmcli_bin() -> str | None:
    return shutil.which("nmcli")


def _wpa_cli_bin() -> str | None:
    return shutil.which("wpa_cli")


def _network_manager_active() -> bool:
    rc, out, _ = _run(["systemctl", "is-active", "NetworkManager"], timeout=5.0)
    if rc == 0 and "active" in out.strip().casefold():
        return True
    rc, out, _ = _run(["nmcli", "general", "status"], timeout=8.0)
    return rc == 0 and "connected" in out.casefold() or "disconnected" in out.casefold()


def detect_backend(force_refresh: bool = False) -> BackendName:
    global _cached_backend
    if _cached_backend is not None and not force_refresh:
        return _cached_backend

    if _nmcli_bin() and _network_manager_active():
        _cached_backend = "nmcli"
        return _cached_backend

    iface = str(getattr(config, "WIFI_WPA_INTERFACE", "wlan0"))
    if _wpa_cli_bin():
        rc, _, _ = _run([_wpa_cli_bin() or "wpa_cli", "-i", iface, "ping"], timeout=5.0)
        if rc == 0:
            _cached_backend = "wpa_cli"
            return _cached_backend

    _cached_backend = "none"
    return _cached_backend


def backend_label() -> str:
    b = detect_backend()
    if b == "nmcli":
        return "NetworkManager (nmcli)"
    if b == "wpa_cli":
        return f"wpa_cli ({getattr(config, 'WIFI_WPA_INTERFACE', 'wlan0')})"
    return "yok"


def _run_nmcli(*extra: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Önce doğrudan nmcli; yetki hatasında şifresiz sudo dene."""
    bin_path = _nmcli_bin()
    if not bin_path:
        return 127, "", "nmcli bulunamadı"
    last = (127, "", "")
    for prefix in ([], ["sudo", "-n"]):
        args = [*prefix, bin_path, *extra]
        rc, out, err = _run(args, timeout=timeout)
        last = (rc, out, err)
        if rc == 127:
            continue
        combined = f"{out}\n{err}".casefold()
        if rc == 0 or "not authorized" not in combined and "permission" not in combined:
            return rc, out, err
    return last


def _parse_nmcli_wifi_line(line: str) -> WiFiNetwork | None:
    line = line.strip()
    if not line:
        return None
    parts: list[str] = []
    buf = ""
    esc = False
    for ch in line:
        if esc:
            buf += ch
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == ":":
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    parts.append(buf)
    if len(parts) < 4:
        return None
    in_use = parts[0].strip() == "*"
    ssid = parts[1].strip()
    if not ssid:
        return None
    try:
        signal = int(parts[2].strip() or "0")
    except ValueError:
        signal = 0
    security = parts[3].strip()
    return WiFiNetwork(ssid=ssid, signal=signal, security=security, in_use=in_use)


def _dedupe_networks(networks: list[WiFiNetwork]) -> list[WiFiNetwork]:
    by_ssid: dict[str, WiFiNetwork] = {}
    for net in networks:
        key = net.ssid.casefold()
        prev = by_ssid.get(key)
        if prev is None or net.signal > prev.signal or (net.in_use and not prev.in_use):
            by_ssid[key] = net
    ordered = sorted(
        by_ssid.values(),
        key=lambda n: (not n.in_use, -n.signal, n.ssid.casefold()),
    )
    return ordered


def scan_networks_nmcli() -> tuple[list[WiFiNetwork], str | None]:
    wait = float(getattr(config, "WIFI_SCAN_SEC", 4))
    _run_nmcli("dev", "wifi", "rescan", timeout=max(wait + 5, 10))
    time.sleep(min(wait, 6.0))
    rc, out, err = _run_nmcli(
        "-t",
        "-f",
        "IN-USE,SSID,SIGNAL,SECURITY",
        "dev",
        "wifi",
        "list",
        timeout=25.0,
    )
    if rc != 0:
        msg = (err or out or "nmcli tarama başarısız").strip()
        return [], msg
    nets = []
    for line in out.splitlines():
        net = _parse_nmcli_wifi_line(line)
        if net:
            nets.append(net)
    return _dedupe_networks(nets), None


def _parse_wpa_scan_results(text: str) -> list[WiFiNetwork]:
    nets: list[WiFiNetwork] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("bssid"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        flags = parts[3] if len(parts) > 3 else ""
        ssid = parts[4].strip() if len(parts) > 4 else ""
        if not ssid:
            continue
        try:
            signal = int(parts[2])
        except (ValueError, IndexError):
            signal = 0
        security = "open" if "[ESS]" in flags and "WPA" not in flags and "WEP" not in flags else "secured"
        nets.append(WiFiNetwork(ssid=ssid, signal=signal, security=security, in_use=False))
    return _dedupe_networks(nets)


def scan_networks_wpa() -> tuple[list[WiFiNetwork], str | None]:
    iface = str(getattr(config, "WIFI_WPA_INTERFACE", "wlan0"))
    cli = _wpa_cli_bin()
    if not cli:
        return [], "wpa_cli bulunamadı"
    rc, _, err = _run([cli, "-i", iface, "scan"], timeout=15.0)
    if rc != 0:
        return [], (err or "wpa_cli scan başarısız").strip()
    wait = float(getattr(config, "WIFI_SCAN_SEC", 4))
    time.sleep(min(wait, 6.0))
    rc, out, err = _run([cli, "-i", iface, "scan_results"], timeout=15.0)
    if rc != 0:
        return [], (err or "wpa_cli scan_results başarısız").strip()
    return _parse_wpa_scan_results(out), None


def scan_networks() -> tuple[list[WiFiNetwork], str | None]:
    backend = detect_backend()
    if backend == "nmcli":
        return scan_networks_nmcli()
    if backend == "wpa_cli":
        return scan_networks_wpa()
    return [], "WiFi yönetimi bu sistemde kurulu değil (nmcli veya wpa_cli yok)."


def connect_nmcli(ssid: str, password: str | None = None) -> tuple[bool, str]:
    timeout = float(getattr(config, "WIFI_CONNECT_TIMEOUT_SEC", 45))
    args: list[str] = ["dev", "wifi", "connect", ssid]
    if password:
        args.extend(["password", password])
    rc, out, err = _run_nmcli(*args, timeout=timeout)
    combined = f"{out}\n{err}".strip()
    if rc == 0:
        return True, combined
    low = combined.casefold()
    if "secret" in low or "password" in low or "802.1x" in low:
        return False, "Şifre gerekli veya şifre yanlış olabilir."
    return False, combined or "Bağlantı başarısız"


def connect_wpa(ssid: str, password: str | None = None) -> tuple[bool, str]:
    iface = str(getattr(config, "WIFI_WPA_INTERFACE", "wlan0"))
    cli = _wpa_cli_bin()
    if not cli:
        return False, "wpa_cli bulunamadı"

    def wpa(*cmd: str) -> tuple[int, str]:
        rc, o, e = _run([cli, "-i", iface, *cmd], timeout=20.0)
        return rc, (o or e).strip()

    rc, out = wpa("add_network")
    if rc != 0:
        return False, out or "add_network başarısız"
    m = re.search(r"(\d+)", out)
    if not m:
        return False, f"Ağ kimliği alınamadı: {out}"
    net_id = m.group(1)

    steps: list[tuple[str, ...]] = [
        ("set_network", net_id, "ssid", ssid),
    ]
    if password:
        steps.append(("set_network", net_id, "psk", password))
    else:
        steps.append(("set_network", net_id, "key_mgmt", "NONE"))

    for step in steps:
        rc, o = wpa(*step)
        if rc != 0:
            wpa("remove_network", net_id)
            return False, o or "wpa_cli yapılandırma hatası"

    rc, o = wpa("enable_network", net_id)
    if rc != 0:
        wpa("remove_network", net_id)
        return False, o or "enable_network başarısız"
    wpa("select_network", net_id)
    wpa("save_config")

    deadline = time.time() + float(getattr(config, "WIFI_CONNECT_TIMEOUT_SEC", 45))
    while time.time() < deadline:
        rc, status = wpa("status")
        if rc == 0 and "wpa_state=completed" in status:
            return True, status
        time.sleep(1.0)
    return False, "Bağlantı zaman aşımı"


def connect(ssid: str, password: str | None = None) -> tuple[bool, str]:
    backend = detect_backend()
    if backend == "nmcli":
        return connect_nmcli(ssid, password)
    if backend == "wpa_cli":
        return connect_wpa(ssid, password)
    return False, "WiFi backend yok"
