#!/usr/bin/env bash
# Bluetooth kulaklık (bluealsa) — Robot Kanka ses çıkışı
# Pi üzerinde: bash scripts/setup_bluetooth_audio.sh
set -euo pipefail

echo "[bt-audio] bluez + bluealsa kurulumu..."
sudo apt-get update -y
sudo apt-get install -y bluez bluez-tools bluealsa

RUN_USER="${SUDO_USER:-$USER}"
if [[ -z "${RUN_USER}" || "${RUN_USER}" == "root" ]]; then
  RUN_USER="$(whoami)"
fi
if id "${RUN_USER}" &>/dev/null; then
  sudo usermod -aG bluetooth "${RUN_USER}" 2>/dev/null || true
  echo "[bt-audio] ${RUN_USER} bluetooth grubuna eklendi (oturumu yenileyin veya reboot)."
fi

echo "[bt-audio] bluealsa servisi..."
sudo systemctl enable bluealsa.service 2>/dev/null || true
sudo systemctl start bluealsa.service 2>/dev/null || true

echo "[bt-audio] PipeWire çakışması (opsiyonel — ALSA doğrudan hb kullanıyorsanız):"
echo "  systemctl --user stop pipewire.service pipewire.socket wireplumber.service pulseaudio.service pulseaudio.socket"
echo "  Kalıcı kapatmak için: systemctl --user mask pipewire.service wireplumber.service"
echo "  (yapilacak adimlar.txt ile aynı mantık)"

if command -v bluetoothctl >/dev/null 2>&1; then
  echo "[bt-audio] Adaptör:"
  bluetoothctl show 2>/dev/null | head -5 || true
fi
if command -v bluealsa-aplay >/dev/null 2>&1; then
  echo "[bt-audio] bluealsa PCM listesi (bağlı cihaz yoksa boş olabilir):"
  bluealsa-aplay -L 2>/dev/null | head -10 || true
fi

echo "[bt-audio] İlk eşleştirme (bir kerelik):"
echo "  bluetoothctl"
echo "  power on"
echo "  scan on"
echo "  pair AA:BB:CC:DD:EE:FF"
echo "  trust AA:BB:CC:DD:EE:FF"
echo ""
echo "[bt-audio] .env örneği: BLUETOOTH_ENABLED=1, AUDIO_OUTPUT_ALSA_DEVICE=hb"
