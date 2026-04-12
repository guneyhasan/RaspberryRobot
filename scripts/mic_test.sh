#!/usr/bin/env bash
set -euo pipefail
OUT="$(mktemp /tmp/mic_test_XXXXXX.wav)"
echo "[mic_test] 5 saniye kayıt: $OUT"
arecord -d 5 -f cd -t wav "$OUT"
echo "[mic_test] çalınıyor..."
aplay "$OUT"
rm -f "$OUT"
echo "[mic_test] bitti."
