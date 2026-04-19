#!/usr/bin/env python3
"""
whisper-server + STT modülü teşhisi (süreler ve gerçekten HTTP kullanılıyor mu).

Proje kökünden çalıştırın:
  cd ~/RaspberryRobot && source venv/bin/activate && python3 scripts/debug_stt_server.py

İsteğe bağlı:
  WHISPER_STT_BACKEND=server|cli  (varsayılan: .env / config)
  python3 scripts/debug_stt_server.py --rounds 3
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

import config  # noqa: E402
from modules import stt, vad  # noqa: E402


def _fmt_ms(sec: float) -> str:
    return f"{sec * 1000:.0f} ms"


def _health_url() -> str:
    base = getattr(config, "WHISPER_SERVER_BASE_URL", "http://127.0.0.1:8777").rstrip("/")
    return urljoin(base + "/", "health")


def _infer_url() -> str:
    base = getattr(config, "WHISPER_SERVER_BASE_URL", "http://127.0.0.1:8777").rstrip("/")
    return urljoin(base + "/", "inference")


def _get_json(url: str, timeout: float) -> tuple[float, dict[str, Any] | None, str | None]:
    t0 = time.perf_counter()
    err: str | None = None
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return time.perf_counter() - t0, json.loads(raw), None
    except (URLError, HTTPError, json.JSONDecodeError, OSError) as e:
        err = f"{type(e).__name__}: {e}"
        return time.perf_counter() - t0, None, err


def _port_connect_check(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _multipart_infer(wav_bytes: bytes, infer_url: str, timeout: float) -> tuple[float, dict[str, Any] | None, str | None]:
    boundary = f"----dbg{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(b'Content-Disposition: form-data; name="file"; filename="t.wav"' + crlf)
    parts.append(b"Content-Type: audio/wav" + crlf + crlf)
    parts.append(wav_bytes + crlf)
    for name, val in (("language", "tr"), ("response_format", "json"), ("no_timestamps", "true")):
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf)
        parts.append(val.encode("utf-8") + crlf)
    parts.append(f"--{boundary}--".encode() + crlf)
    body = b"".join(parts)
    ctype = f"multipart/form-data; boundary={boundary}"
    req = Request(infer_url, data=body, method="POST", headers={"Content-Type": ctype})
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return time.perf_counter() - t0, data if isinstance(data, dict) else None, None
    except (URLError, HTTPError, json.JSONDecodeError, OSError) as e:
        return time.perf_counter() - t0, None, f"{type(e).__name__}: {e}"


def _synth_pcm(sec: float, sr: int) -> np.ndarray:
    """Yaklaşık `sec` saniye mono int16 (hafif gürültü + ton) — süre ölçümü için."""
    n = int(sec * sr)
    rng = np.random.default_rng(42)
    noise = (rng.standard_normal(n) * 800).astype(np.float32)
    t = np.linspace(0.0, sec, n, endpoint=False, dtype=np.float32)
    tone = (np.sin(2 * np.pi * 220.0 * t) * 4000.0).astype(np.float32)
    x = np.clip(noise + tone, -32768, 32767).astype(np.int16)
    return x


def main() -> int:
    ap = argparse.ArgumentParser(description="whisper-server / STT debug")
    ap.add_argument("--rounds", type=int, default=2, help="Ardışık transcribe_pcm denemesi sayısı")
    ap.add_argument("--audio-sec", type=float, default=1.5, help="Sentetik ses süresi (saniye)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("debug_stt")

    print("=== config ===")
    print(f"  WHISPER_STT_BACKEND     = {getattr(config, 'WHISPER_STT_BACKEND', '?')}")
    print(f"  WHISPER_SERVER_SPAWN    = {getattr(config, 'WHISPER_SERVER_SPAWN', '?')}")
    print(f"  WHISPER_SERVER_BASE_URL = {getattr(config, 'WHISPER_SERVER_BASE_URL', '?')}")
    print(f"  WHISPER_SERVER_HOST:PORT = {getattr(config, 'WHISPER_SERVER_HOST', '?')}:{getattr(config, 'WHISPER_SERVER_PORT', '?')}")
    print(f"  WHISPER_SERVER_BINARY   = {getattr(config, 'WHISPER_SERVER_BINARY', '?')}")
    print(f"    exists = {Path(getattr(config, 'WHISPER_SERVER_BINARY')).is_file()}")
    print(f"  WHISPER_BINARY (cli)    = {config.WHISPER_BINARY}")
    print(f"    exists = {config.WHISPER_BINARY.is_file()}")
    print(f"  WHISPER_MODEL           = {config.WHISPER_MODEL}")
    print(f"    exists = {config.WHISPER_MODEL.is_file()}")
    print(f"  WHISPER_THREADS         = {config.WHISPER_THREADS}")
    print()

    host = getattr(config, "WHISPER_SERVER_HOST", "127.0.0.1")
    port = int(getattr(config, "WHISPER_SERVER_PORT", 8777))
    print("=== port (TCP) ===")
    open_tcp = _port_connect_check(host, port, timeout=0.4)
    print(f"  {host}:{port} bağlanılabilir = {open_tcp}")
    print()

    print("=== health (ensure öncesi) ===")
    elapsed, j, err = _get_json(_health_url(), timeout=3.0)
    print(f"  süre = {_fmt_ms(elapsed)}")
    if j is not None:
        print(f"  JSON = {j}")
    else:
        print(f"  hata = {err}")
    print()

    print("=== stt.ensure_whisper_backend_ready() ===")
    t0 = time.perf_counter()
    try:
        stt.ensure_whisper_backend_ready()
    except Exception as e:
        print(f"  HATA: {type(e).__name__}: {e}")
        return 1
    t1 = time.perf_counter()
    print(f"  toplam süre = {_fmt_ms(t1 - t0)}")
    proc = getattr(stt, "_server_proc", None)
    if proc is not None:
        poll = proc.poll()
        print(f"  stt._server_proc pid={proc.pid} poll={poll} (None=çalışıyor)")
    else:
        print("  stt._server_proc = None (SPAWN=0 veya backend=cli olabilir)")
    print()

    print("=== health (ensure sonrası) ===")
    elapsed, j, err = _get_json(_health_url(), timeout=3.0)
    print(f"  süre = {_fmt_ms(elapsed)}")
    if j is not None:
        print(f"  JSON = {j}")
    else:
        print(f"  hata = {err}")
    print()

    backend = getattr(config, "WHISPER_STT_BACKEND", "cli")
    if backend != "server":
        print("backend=cli — sunucu HTTP testi atlandı. Sunucu testi için WHISPER_STT_BACKEND=server kullanın.")
        return 0

    sr = config.SAMPLE_RATE
    pcm = _synth_pcm(args.audio_sec, sr)
    print(f"=== sentetik ses === samples={len(pcm)} duration={len(pcm)/sr:.2f}s sr={sr}")
    print()

    print("=== doğrudan HTTP POST /inference (stt.transcribe_pcm dışında) ===")
    wav_path = ROOT / "data" / ".debug_stt_temp.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        vad.save_wav_int16(wav_path, pcm, sr)
        wav_bytes = wav_path.read_bytes()
        elapsed, data, err = _multipart_infer(wav_bytes, _infer_url(), timeout=120.0)
        print(f"  süre = {_fmt_ms(elapsed)}")
        if data is not None:
            txt = (data.get("text") or "").strip()
            print(f"  text (ilk 120 char) = {txt[:120]!r}")
        else:
            print(f"  hata = {err}")
    finally:
        wav_path.unlink(missing_ok=True)
    print()

    print(f"=== stt.transcribe_pcm x {args.rounds} (modülün kullandığı yol) ===")
    for i in range(args.rounds):
        t0 = time.perf_counter()
        text, conf = stt.transcribe_pcm(pcm)
        dt = time.perf_counter() - t0
        print(f"  #{i + 1} süre={_fmt_ms(dt)} conf={conf:.2f} text={text[:100]!r}")
    print()

    print("=== yorum ===")
    print("  - ensure süresi çok uzunsa: model ilk yükleme (normal, bir kez).")
    print("  - transcribe #1 >> #2 ise: hâlâ cli veya her seferinde yeni süreç olabilir; backend ve logları kontrol edin.")
    print("  - HTTP inference ile transcribe_pcm süreleri yakınsa: darboğaz whisper hesabı / ses uzunluğu.")
    print("  - HTTP çok hızlı, transcribe_pcm yavaşsa: WAV yazımı veya kilit beklemesi (nadiren).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
