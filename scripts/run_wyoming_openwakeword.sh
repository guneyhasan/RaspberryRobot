#!/usr/bin/env bash
# Wyoming openWakeWord sunucusunu yerelde başlatır (openWakeWord + tflite bu venv içinde çalışır).
# Alternatif: docker run -d -p 10400:10400 rhasspy/wyoming-openwakeword
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWW_DIR="${WYOMING_OWW_DIR:-$ROOT/vendor/wyoming-openwakeword}"
URI="${WYOMING_OWW_URI:-tcp://0.0.0.0:10400}"

if [[ ! -d "$OWW_DIR" ]]; then
  echo "[wyoming-oww] Klonlanıyor: $OWW_DIR"
  mkdir -p "$(dirname "$OWW_DIR")"
  git clone --depth 1 https://github.com/rhasspy/wyoming-openwakeword.git "$OWW_DIR"
fi

cd "$OWW_DIR"
if [[ -x script/setup ]]; then
  script/setup
fi
exec script/run --uri "$URI" "$@"
