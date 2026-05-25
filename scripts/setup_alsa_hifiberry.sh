#!/usr/bin/env bash
# HiFiBerry / Robot-HAT hoparlör ALSA ayarı (pcm.hb)
# Pi: bash scripts/setup_alsa_hifiberry.sh
set -euo pipefail

ASOUNDRC="${HOME}/.asoundrc"
if [[ -f "${ASOUNDRC}" ]]; then
  cp -n "${ASOUNDRC}" "${ASOUNDRC}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
fi

cat > "${ASOUNDRC}" <<'EOF'
# Robot Kanka — HiFiBerry I2S (install.sh ile aynı)
pcm.hb {
    type plug
    slave.pcm "hw:CARD=sndrpihifiberry,DEV=0"
}

ctl.hb {
    type hw
    card sndrpihifiberry
}

defaults.pcm.card sndrpihifiberry
defaults.ctl.card sndrpihifiberry
EOF

echo "[alsa] ${ASOUNDRC} yazıldı."
echo "[alsa] Test:"
speaker-test -D hb -t wav -c 2 -l 1 || {
  echo "[alsa] hb başarısız — doğrudan kart 0:"
  speaker-test -D plughw:0,0 -t wav -c 2 -l 1
}
echo ""
echo "[alsa] .env içine ekleyin:"
echo "  AUDIO_OUTPUT_ALSA_DEVICE=hb"
echo "  (hb çalışmazsa: AUDIO_OUTPUT_ALSA_DEVICE=plughw:0,0)"
