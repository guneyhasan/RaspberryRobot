#!/usr/bin/env bash
# systemd ExecStartPre — boot'ta ses/mikrofon/servisler hazır olana kadar bekler
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
ERR_LOG="${ROBOT_KANKA_ERR_LOG:-$ROOT/logs/health_err.log}"
log_err() { echo "[$(date -Iseconds)] $*" >>"$ERR_LOG"; }

if [[ -f "$ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$ROOT/.env" 2>/dev/null || true
  set +a
fi

WAIT_MAX="${HEALTH_WAIT_MAX_SEC:-60}"
WAIT_INTERVAL="${HEALTH_WAIT_INTERVAL_SEC:-2}"
BOOT_DELAY="${BOOT_DELAY_SEC:-0}"
MIC_SETTLE="${MIC_SETTLE_SEC:-0}"

if [[ "$BOOT_DELAY" =~ ^[0-9]+$ ]] && (( BOOT_DELAY > 0 )); then
  log_err "[INFO] BOOT_DELAY_SEC=${BOOT_DELAY}, bekleniyor..."
  sleep "$BOOT_DELAY"
fi

_bt_enabled() {
  local v="${BLUETOOTH_ENABLED:-0}"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" ]]
}

_unit_exists() {
  systemctl list-unit-files "$1" &>/dev/null 2>&1
}

_unit_active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

_audio_ready() {
  aplay -l 2>/dev/null | grep -qi card
}

_mic_ready() {
  if arecord -l 2>/dev/null | grep -qi card; then
    return 0
  fi
  # arecord boşken /proc (systemd / USB enumerate gecikmesi)
  grep -qE '^\s*[0-9]+\s+\[' /proc/asound/cards 2>/dev/null || return 1
  grep -qiE 'usb|device|mic' /proc/asound/cards 2>/dev/null
}

_deps_ready() {
  if _unit_exists speaker-enable.service && ! _unit_active speaker-enable.service; then
    return 1
  fi
  if _bt_enabled && _unit_exists bluealsa.service && ! _unit_active bluealsa.service; then
    return 1
  fi
  _audio_ready && _mic_ready
}

elapsed=0
while ! _deps_ready; do
  if (( elapsed >= WAIT_MAX )); then
    if ! _audio_ready; then
      log_err "[ERROR] Ses kartı bulunamadı (aplay -l) — ${WAIT_MAX}s beklendi"
      exit 1
    fi
    if ! _mic_ready; then
      log_err "[ERROR] Mikrofon bulunamadı (arecord -l) — ${WAIT_MAX}s beklendi"
      exit 1
    fi
    if _unit_exists speaker-enable.service && ! _unit_active speaker-enable.service; then
      log_err "[ERROR] speaker-enable.service aktif değil — ${WAIT_MAX}s beklendi"
      exit 1
    fi
    if _bt_enabled && _unit_exists bluealsa.service && ! _unit_active bluealsa.service; then
      log_err "[ERROR] bluealsa.service aktif değil — ${WAIT_MAX}s beklendi"
      exit 1
    fi
    break
  fi
  sleep "$WAIT_INTERVAL"
  elapsed=$(( elapsed + WAIT_INTERVAL ))
done

if [[ "$MIC_SETTLE" =~ ^[0-9]+$ ]] && (( MIC_SETTLE > 0 )); then
  log_err "[INFO] MIC_SETTLE_SEC=${MIC_SETTLE} (USB mic enumerate sonrası), bekleniyor..."
  sleep "$MIC_SETTLE"
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
