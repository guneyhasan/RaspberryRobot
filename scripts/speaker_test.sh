#!/usr/bin/env bash
set -euo pipefail
echo "[speaker_test] 3 saniye WAV test tonu..."
speaker-test -t wav -c 2 -l 1 || true
echo "[speaker_test] bitti."
