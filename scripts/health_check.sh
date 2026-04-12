#!/usr/bin/env bash
# systemd ExecStartPre — ses + mikrofon zorunlu; internet yoksa Piper uyarısı
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
ERR_LOG="${ROBOT_KANKA_ERR_LOG:-$ROOT/logs/health_err.log}"
log_err() { echo "[$(date -Iseconds)] $*" >>"$ERR_LOG"; }

if ! aplay -l 2>/dev/null | grep -qi card; then
  log_err "[ERROR] Ses kartı bulunamadı (aplay -l)"
  exit 1
fi

if ! arecord -l 2>/dev/null | grep -qi card; then
  log_err "[ERROR] Mikrofon bulunamadı (arecord -l)"
  exit 1
fi

if ! ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
  ONNX=$(find "$ROOT/models/tr_TR-ahmet-medium" "$ROOT/models" -maxdepth 1 -name '*.onnx' 2>/dev/null | head -1)
  JSON=$(find "$ROOT/models/tr_TR-ahmet-medium" "$ROOT/models" -maxdepth 1 -name '*.onnx.json' 2>/dev/null | head -1)
  MSG="İnternet bağlantısı yok, offline modda çalışıyorum."
  if [[ -n "$ONNX" ]] && command -v piper &>/dev/null; then
    ARGS=(--model "$ONNX")
    [[ -n "$JSON" && -f "$JSON" ]] && ARGS+=(--config "$JSON")
    echo "$MSG" | piper "${ARGS[@]}" 2>/dev/null | aplay -q - || true
  fi
fi

exit 0
