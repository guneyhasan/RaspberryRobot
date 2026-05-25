#!/usr/bin/env bash
# Mikrofon kartlarını listeler ve kısa kayıt testi yapar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== arecord -l ==="
arecord -l || true
echo ""

ENV_DEV=""
if [[ -f "${ROOT}/.env" ]]; then
  ENV_DEV="$(grep -E '^AUDIO_INPUT_ALSA_DEVICE=' "${ROOT}/.env" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d ' ')"
  if [[ -n "${ENV_DEV}" ]]; then
    echo "=== .env cihazı: ${ENV_DEV} ==="
    if arecord -q -D "${ENV_DEV}" -f S16_LE -r 16000 -c 1 -d 2 /dev/null; then
      echo "OK: ${ENV_DEV} açılabildi"
    else
      echo "HATA: ${ENV_DEV} açılamadı — kart numarası değişmiş olabilir"
    fi
    echo ""
  fi
fi

echo "=== Otomatik keşif (Python) ==="
SUMMARY="$(cd "${ROOT}" && ./venv/bin/python -c "import config; from modules import alsa_devices; print(alsa_devices.format_capture_device_summary())")"
SELECTED="$(cd "${ROOT}" && ./venv/bin/python -c "import config; from modules import alsa_devices; print(alsa_devices.resolve_capture_device(rescan=True) or '')")"
echo "${SUMMARY}"
echo "Seçilen: ${SELECTED:-(yok)}"

OUT="$(mktemp /tmp/mic_test_XXXXXX.wav)"
TEST_DEV="${SELECTED:-${ENV_DEV:-default}}"
echo ""
echo "[mic_test] 3 saniye kayıt (${TEST_DEV}): ${OUT}"
if arecord -D "${TEST_DEV}" -d 3 -f cd -t wav "${OUT}"; then
  echo "[mic_test] çalınıyor..."
  aplay "${OUT}"
  rm -f "${OUT}"
  echo "[mic_test] bitti."
else
  echo "Kayıt başarısız. PipeWire: systemctl --user stop pipewire wireplumber"
  rm -f "${OUT}"
  exit 1
fi
