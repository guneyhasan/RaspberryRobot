#!/usr/bin/env bash
# Robot servis kullanıcısının şifresiz sudo nmcli çalıştırması (sesli WiFi).
set -euo pipefail

USER_NAME="${1:-$(whoami)}"
SUDOERS_FILE="/etc/sudoers.d/robot-kanka-wifi"

collect_bins() {
  local -a found=()
  local b
  for b in /usr/bin/nmcli /bin/nmcli; do
    if [[ -x "$b" ]]; then
      found+=("$b")
    fi
  done
  if command -v nmcli &>/dev/null; then
    b="$(command -v nmcli)"
    [[ " ${found[*]} " != *" $b "* ]] && found+=("$b")
  fi
  if ((${#found[@]} == 0)); then
    echo "[wifi-sudo] nmcli bulunamadı. Önce: sudo apt install network-manager" >&2
    exit 1
  fi
  printf '%s\n' "${found[@]}"
}

mapfile -t BINS < <(collect_bins)
LINE="${USER_NAME} ALL=(ALL) NOPASSWD: $(IFS=,; echo "${BINS[*]}")"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf '%s\n' "$LINE" >"$TMP"
sudo visudo -cf "$TMP" >/dev/null

echo "[wifi-sudo] Kuruluyor: ${SUDOERS_FILE}"
echo "$LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

echo "[wifi-sudo] Tamam. Test (şifre sormamalı):"
NMCLI="$(command -v nmcli)"
echo "  sudo -n ${NMCLI} dev wifi list >/dev/null && echo OK || echo FAIL"
