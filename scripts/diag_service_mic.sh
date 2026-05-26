#!/usr/bin/env bash
# Servis ortamına yakın mikrofon teşhisi (Pi üzerinde)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${1:-$(whoami)}"

echo "=== SSH oturumu ($(whoami)) ==="
groups
arecord -l || true
echo

echo "=== sudo -u ${USER_NAME} (systemd User=) ==="
sudo -u "${USER_NAME}" -- bash -lc 'groups; echo HOME=$HOME; arecord -l' || true
echo

echo "=== systemd-run (robot-kanka benzeri) ==="
sudo systemd-run --uid="${USER_NAME}" --gid="${USER_NAME}" \
  --property=SupplementaryGroups=audio \
  --property=WorkingDirectory="${ROOT}" \
  --setenv=HOME="/home/${USER_NAME}" \
  --setenv=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin \
  --pipe --wait bash -lc "cd '${ROOT}' && arecord -l && ./venv/bin/python -c \"
from modules import alsa_devices
print(alsa_devices.format_capture_device_summary())
print('resolved:', alsa_devices.resolve_capture_device(rescan=True, allow_wait=True))
\"" || true
