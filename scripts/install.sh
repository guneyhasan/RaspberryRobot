#!/usr/bin/env bash
# Tek komutla temel kurulum (Pi üzerinde çalıştırın)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT"

echo "[install] Sistem paketleri (sudo gerekir)..."
sudo apt-get update -y
sudo apt-get install -y \
  git python3-pip python3-venv alsa-utils \
  libportaudio2 portaudio19-dev \
  ffmpeg

if [[ ! -d "$ROOT/venv" ]]; then
  python3 -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"
echo "[install] Not: OpenWakeWord Pi'de çoğu zaman kurulmaz (tflite-runtime ARM tekerleği yok)."
echo "       USE_OPENWAKEWORD=0 bırakın veya gerekiyorsa: pip install -r requirements-optional.txt"

mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/models"

if [[ ! -d "$ROOT/whisper.cpp" ]]; then
  echo "[install] whisper.cpp klonlanıyor..."
  git clone https://github.com/ggerganov/whisper.cpp "$ROOT/whisper.cpp"
  (cd "$ROOT/whisper.cpp" && make -j4)
  bash "$ROOT/whisper.cpp/models/download-ggml-model.sh" small
fi

echo "[install] Piper modeli: https://github.com/rhasspy/piper/releases adresinden"
echo "  tr_TR-ahmet-medium.onnx ve .onnx.json dosyalarını şuraya koyun:"
echo "  $ROOT/models/tr_TR-ahmet-medium/"
echo ""
echo "venv: $ROOT/venv"
echo ".env dosyasını kopyalayın: cp .env.example .env && nano .env"
echo "systemd: sudo cp systemd/robot-kanka.service /etc/systemd/system/ && sudo systemctl daemon-reload"

chmod +x "$ROOT/scripts/"*.sh 2>/dev/null || true
chmod +x "$ROOT/scripts/"*.py "$ROOT/main.py" 2>/dev/null || true
