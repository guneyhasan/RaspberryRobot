#!/usr/bin/env bash
# Bluetooth kulaklık (BlueALSA / bluez-alsa) — Robot Kanka ses çıkışı
# Pi üzerinde: bash scripts/setup_bluetooth_audio.sh
set -euo pipefail

install_bluealsa_packages() {
  echo "[bt-audio] bluez kurulumu..."
  sudo apt-get update -y
  sudo apt-get install -y bluez bluez-tools

  echo "[bt-audio] BlueALSA paketi aranıyor (dağıtıma göre ad değişir)..."
  local pkg=""
  for candidate in bluez-alsa-utils bluealsa; do
    if apt-cache show "${candidate}" &>/dev/null 2>&1; then
      pkg="${candidate}"
      break
    fi
  done

  if [[ -z "${pkg}" ]]; then
    echo ""
    echo "[bt-audio] UYARI: BlueALSA apt deposunda yok."
    echo "  - Raspberry Pi OS 12 (Bookworm): genelde paket adı bluez-alsa-utils"
    echo "  - Pi OS 11 (Bullseye): bluealsa paketi yok; OS güncellemesi veya kaynaktan derleme gerekir"
    echo "  - Alternatif: PipeWire + bluetooth (plan dışı; .env ile AUDIO backend değişikliği gerekir)"
    echo ""
    echo "  Sesli kulaklık modu TTS'i bluealsa:DEV=... ile çalar; paket yoksa bağlansanız bile ses kulaklığa gitmez."
    echo "  Kaynak: https://github.com/arkq/bluez-alsa"
    return 1
  fi

  echo "[bt-audio] Kuruluyor: ${pkg}"
  sudo apt-get install -y "${pkg}"
}

install_bluealsa_packages

RUN_USER="${SUDO_USER:-$USER}"
if [[ -z "${RUN_USER}" || "${RUN_USER}" == "root" ]]; then
  RUN_USER="$(whoami)"
fi
if id "${RUN_USER}" &>/dev/null; then
  sudo usermod -aG bluetooth "${RUN_USER}" 2>/dev/null || true
  echo "[bt-audio] ${RUN_USER} bluetooth grubuna eklendi (oturumu yenileyin veya reboot)."
fi

echo "[bt-audio] bluealsa servisi..."
if systemctl list-unit-files bluealsa.service &>/dev/null 2>&1; then
  sudo systemctl enable bluealsa.service 2>/dev/null || true
  sudo systemctl start bluealsa.service 2>/dev/null || true
else
  echo "[bt-audio] bluealsa.service bulunamadı — paket kurulumunu kontrol edin."
fi

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
else
  echo "[bt-audio] bluealsa-aplay yok — kulaklık ses çıkışı çalışmaz."
fi

echo "[bt-audio] İlk eşleştirme (bir kerelik):"
echo "  bluetoothctl"
echo "  power on"
echo "  scan on"
echo "  pair AA:BB:CC:DD:EE:FF"
echo "  trust AA:BB:CC:DD:EE:FF"
echo ""
echo "[bt-audio] .env örneği: BLUETOOTH_ENABLED=1, AUDIO_OUTPUT_ALSA_DEVICE=hb"
