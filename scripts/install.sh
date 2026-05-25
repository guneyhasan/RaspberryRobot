#!/usr/bin/env bash
# Tek komutla temel kurulum (Pi üzerinde çalıştırın)
set -euo pipefail

echo "[install] Kamera overlay (imx219) kontrol ediliyor..."
BOOT_CONFIG=""
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  BOOT_CONFIG="/boot/config.txt"
fi
if [[ -n "${BOOT_CONFIG}" ]]; then
  if ! sudo grep -qE '^\s*dtoverlay=imx219,cam0\s*$' "${BOOT_CONFIG}"; then
    echo "[install] ${BOOT_CONFIG} içine dtoverlay=imx219,cam0 ekleniyor..."
    sudo cp -n "${BOOT_CONFIG}" "${BOOT_CONFIG}.bak" 2>/dev/null || true
    echo "dtoverlay=imx219,cam0" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
  else
    echo "[install] dtoverlay zaten var."
  fi
else
  echo "[install] Boot config bulunamadı (/boot/firmware/config.txt veya /boot/config.txt). Atlanıyor."
fi

echo "[install] Robot HAT kurulumu (sunfounder/robot-hat)..."
ROBOT_HAT_DIR="${HOME}/robot-hat"
if [[ ! -d "${ROBOT_HAT_DIR}" ]]; then
  git clone https://github.com/sunfounder/robot-hat.git "${ROBOT_HAT_DIR}"
else
  echo "[install] ${ROBOT_HAT_DIR} zaten var, güncelleniyor..."
  (cd "${ROBOT_HAT_DIR}" && git pull --ff-only) || true
fi
if [[ -f "${ROBOT_HAT_DIR}/setup.py" ]]; then
  (cd "${ROBOT_HAT_DIR}" && sudo python3 setup.py install)
fi
if [[ -f "${ROBOT_HAT_DIR}/i2samp.sh" ]]; then
  (cd "${ROBOT_HAT_DIR}" && sudo bash i2samp.sh) || true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[install] ALSA (.asoundrc) ayarlanıyor..."
bash "${SCRIPT_DIR}/setup_alsa_hifiberry.sh"

pkill -f speaker-test 2>/dev/null || true
pkill -f aplay 2>/dev/null || true

cd "$ROOT"
echo "[install] Sistem paketleri (sudo gerekir)..."
sudo apt-get update -y
sudo apt-get install -y \
  git python3-pip python3-venv alsa-utils \
  libportaudio2 portaudio19-dev \
  ffmpeg \
  cmake build-essential \
  bluez bluez-tools

echo "[install] BlueALSA (kulaklık sesi) — ayrı script, paket adı OS'e göre değişir..."
bash "${SCRIPT_DIR}/setup_bluetooth_audio.sh" || echo "[install] BlueALSA kurulamadı; docs/bluetooth-kulaklik.md"

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
    { cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4 && \
      cmake --build build -j4 --target whisper-server 2>/dev/null || true; } || \
    { make -j4; })
fi
if [[ -d "$ROOT/whisper.cpp/models" ]] && [[ ! -f "$ROOT/whisper.cpp/models/ggml-base.bin" ]] && [[ ! -f "$ROOT/whisper.cpp/models/ggml-small.bin" ]]; then
  bash "$ROOT/whisper.cpp/models/download-ggml-model.sh" base || true
fi

echo "[install] whisper.cpp yoksa veya derleme hatası: bash scripts/build_whisper.sh"

echo "[install] Piper modeli: https://github.com/rhasspy/piper/releases adresinden"
echo "  tr_TR-ahmet-medium.onnx ve .onnx.json dosyalarını şuraya koyun:"
echo "  $ROOT/models/tr_TR-ahmet-medium/"
echo ""
echo "venv: $ROOT/venv"
echo ".env dosyasını kopyalayın: cp .env.example .env && nano .env"

chmod +x "$ROOT/scripts/"*.sh 2>/dev/null || true
chmod +x "$ROOT/scripts/"*.py "$ROOT/main.py" 2>/dev/null || true

echo "[install] systemd — otomatik başlatma (açılışta main.py)..."
PI_USER="$(whoami)"
sudo cp "$ROOT/systemd/speaker-enable.service" /etc/systemd/system/
sudo systemctl enable speaker-enable.service
sudo systemctl start speaker-enable.service || true

sed -e "s|@USER@|${PI_USER}|g" -e "s|@ROOT@|${ROOT}|g" \
  "$ROOT/systemd/robot-kanka.service" | sudo tee /etc/systemd/system/robot-kanka.service >/dev/null

if systemctl list-unit-files NetworkManager-wait-online.service &>/dev/null; then
  sudo systemctl enable NetworkManager-wait-online.service 2>/dev/null || true
elif systemctl list-unit-files systemd-networkd-wait-online.service &>/dev/null; then
  sudo systemctl enable systemd-networkd-wait-online.service 2>/dev/null || true
fi

sudo systemctl daemon-reload
sudo systemctl enable robot-kanka.service
echo "[install] Servis kuruldu. Başlat: sudo systemctl start robot-kanka"
echo "[install] Log: journalctl -u robot-kanka -f  |  /var/log/robot-kanka.log"
echo "[install] Detay: docs/otomatik-baslatma.md"
