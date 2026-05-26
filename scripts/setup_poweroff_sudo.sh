#!/usr/bin/env bash
# Robot servis kullanıcısının şifresiz sudo poweroff çalıştırması (sesli kapanma + kritik pil).
set -euo pipefail

USER_NAME="${1:-$(whoami)}"
SUDOERS_FILE="/etc/sudoers.d/robot-kanka-poweroff"

collect_bins() {
  local -a found=()
  local b
  for b in /sbin/poweroff /usr/sbin/poweroff /sbin/shutdown /usr/sbin/shutdown; do
    if [[ -x "$b" ]]; then
      found+=("$b")
    fi
  done
  if command -v poweroff &>/dev/null; then
    b="$(command -v poweroff)"
    [[ " ${found[*]} " != *" $b "* ]] && found+=("$b")
  fi
  if command -v shutdown &>/dev/null; then
    b="$(command -v shutdown)"
    [[ " ${found[*]} " != *" $b "* ]] && found+=("$b")
  fi
  if ((${#found[@]} == 0)); then
    echo "[poweroff-sudo] poweroff/shutdown bulunamadı." >&2
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

echo "[poweroff-sudo] Kuruluyor: ${SUDOERS_FILE}"
echo "$LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

echo "[poweroff-sudo] Tamam. Test (şifre sormamalı):"
echo "  sudo -n $(command -v poweroff || echo poweroff) --help >/dev/null && echo OK || echo FAIL"
