#!/usr/bin/env bash
# Robot hoparlör testi — Pi üzerinde çalıştırın
set -euo pipefail

echo "[speaker_test] Playback cihazları:"
aplay -l || true

HB_DEV="${AUDIO_OUTPUT_ALSA_DEVICE:-hb}"
echo "[speaker_test] speaker-test -D ${HB_DEV} ..."
if speaker-test -D "${HB_DEV}" -t wav -c 2 -l 1 2>/dev/null; then
  echo "[speaker_test] ${HB_DEV} OK"
else
  echo "[speaker_test] ${HB_DEV} başarısız — aplay -l ile kart numarası deneyin:"
  echo "  export AUDIO_OUTPUT_ALSA_DEVICE=plughw:KART,0"
  echo "  speaker-test -D plughw:KART,0 -t wav -c 2 -l 1"
fi

echo "[speaker_test] Amplifikatör (Robot-HAT GPIO 20):"
if systemctl is-active speaker-enable &>/dev/null; then
  echo "  speaker-enable.service: active"
else
  echo "  UYARI: sudo systemctl enable --now speaker-enable"
fi

echo "[speaker_test] bitti."
