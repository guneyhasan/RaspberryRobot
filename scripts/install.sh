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
  ffmpeg \
  cmake build-essential

if [[ ! -d "$ROOT/venv" ]]; then
  python3 -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"
echo "[install] Wyoming OpenWakeWord ayrı süreç: scripts/run_wyoming_openwakeword.sh veya Docker."
echo "       .env içinde WYOMING_OPENWAKEWORD_URI=tcp://127.0.0.1:10400 ile bağlanırsınız."

mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/models"

if [[ ! -d "$ROOT/whisper.cpp" ]]; then
  echo "[install] whisper.cpp klonlanıyor..."
  git clone https://github.com/ggerganov/whisper.cpp "$ROOT/whisper.cpp"
fi
if [[ -d "$ROOT/whisper.cpp" ]] && [[ ! -f "$ROOT/whisper.cpp/main" ]] && [[ ! -f "$ROOT/whisper.cpp/build/bin/whisper-cli" ]]; then
  echo "[install] whisper.cpp derleniyor (CMake veya make)..."
  (cd "$ROOT/whisper.cpp" && \
    { cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4; } || \
    { make -j4; })
fi
if [[ -d "$ROOT/whisper.cpp/models" ]] && [[ ! -f "$ROOT/whisper.cpp/models/ggml-small.bin" ]]; then
  bash "$ROOT/whisper.cpp/models/download-ggml-model.sh" small || true
fi

echo "[install] whisper.cpp yoksa veya derleme hatası: bash scripts/build_whisper.sh"

echo "[install] Piper modeli: https://github.com/rhasspy/piper/releases adresinden"
echo "  tr_TR-ahmet-medium.onnx ve .onnx.json dosyalarını şuraya koyun:"
echo "  $ROOT/models/tr_TR-ahmet-medium/"
echo ""
echo "venv: $ROOT/venv"
echo ".env dosyasını kopyalayın: cp .env.example .env && nano .env"
echo "systemd: sudo cp systemd/robot-kanka.service /etc/systemd/system/ && sudo systemctl daemon-reload"

chmod +x "$ROOT/scripts/"*.sh 2>/dev/null || true
chmod +x "$ROOT/scripts/"*.py "$ROOT/main.py" 2>/dev/null || true
