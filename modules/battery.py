"""Robot-HAT pil izleme (voltaj → yüzde) + eşik olayları."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)

_last_reading: Optional[BatteryReading] = None
_last_ts: float = 0.0


@dataclass(frozen=True)
class BatteryReading:
    voltage: float
    percent: int


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def voltage_to_percent(v: float) -> int:
    """
    2S Li-ion için kabaca voltaj → yüzde dönüşümü.
    Varsayılan aralıklar config'ten gelir (Battery pack'e göre ayarlanabilir).
    """
    vmin = float(getattr(config, "BATTERY_VOLTAGE_MIN", 6.4))
    vmax = float(getattr(config, "BATTERY_VOLTAGE_MAX", 8.4))
    if vmax <= vmin:
        vmax = vmin + 1e-6
    x = (v - vmin) / (vmax - vmin)
    pct = int(round(_clamp(x, 0.0, 1.0) * 100))
    return int(_clamp(float(pct), 0.0, 100.0))


def read_voltage() -> Optional[float]:
    """
    Robot-HAT voltajını okur. Kütüphane yoksa None döner.
    Docs: robot_hat.utils.get_battery_voltage()
    """
    try:
        from robot_hat import utils  # type: ignore

        v = float(utils.get_battery_voltage())
        if v <= 0:
            return None
        return v
    except Exception as e:
        logger.debug("Pil voltajı okunamadı (robot_hat): %s", e)
        return None


def read_battery() -> Optional[BatteryReading]:
    v = read_voltage()
    if v is None:
        return None
    r = BatteryReading(voltage=v, percent=voltage_to_percent(v))
    global _last_reading, _last_ts
    _last_reading = r
    _last_ts = time.time()
    return r


def get_cached_reading(max_age_sec: float = 60.0) -> Optional[BatteryReading]:
    """
    Monitor thread'i çalışıyorsa en son okunan değeri döndürür.
    Çok eskiyse (max_age_sec) None döndürür.
    """
    if _last_reading is None:
        return None
    if (time.time() - _last_ts) > max_age_sec:
        return None
    return _last_reading


def monitor_loop(
    *,
    on_drop_10pct,
    on_critical,
    stop_event,
) -> None:
    """
    Arka plan izleyici.
    - Her okuma aralığı: config.BATTERY_POLL_SEC
    - Her %10 düşüşte on_drop_10pct(percent, voltage)
    - <= config.BATTERY_CRITICAL_PERCENT: on_critical(percent, voltage)
    """
    poll = float(getattr(config, "BATTERY_POLL_SEC", 20.0))
    critical = int(getattr(config, "BATTERY_CRITICAL_PERCENT", 10))
    critical = max(1, min(critical, 100))

    last_bucket: Optional[int] = None
    critical_fired = False

    logger.info("Pil izleme başladı (poll=%ss, critical<=%s%%)", poll, critical)

    while not stop_event.is_set():
        r = read_battery()
        if r is not None:
            bucket = int(r.percent / 10) * 10
            if last_bucket is None:
                last_bucket = bucket
                logger.info("Pil: %s%% (%.2fV)", r.percent, r.voltage)
            else:
                # sadece düşüşte olay ver
                if bucket <= last_bucket - 10:
                    last_bucket = bucket
                    try:
                        on_drop_10pct(r.percent, r.voltage)
                    except Exception:
                        logger.exception("on_drop_10pct hata")

            if (not critical_fired) and (r.percent <= critical):
                critical_fired = True
                try:
                    on_critical(r.percent, r.voltage)
                except Exception:
                    logger.exception("on_critical hata")
        time.sleep(poll)

