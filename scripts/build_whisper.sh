#!/usr/bin/env bash
# whisper.cpp klonlar, cmake ile derler, small model indirir (Pi / Linux).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WSP="$ROOT/whisper.cpp"

need_cmd() {
  if ! command -v "$1" &>/dev/null; then
    return 1
  fi
  return 0
}

if ! need_cmd cmake || ! need_cmd g++; then
  echo "[build_whisper] cmake veya derleyici yok. Kuruluyor (sudo)..."
  sudo apt-get update -y
  sudo apt-get install -y cmake build-essential git
fi

if [[ ! -d "$WSP" ]]; then
  echo "[build_whisper] Klonlanıyor: $WSP"
  git clone https://github.com/ggerganov/whisper.cpp "$WSP"
fi

cd "$WSP"
echo "[build_whisper] CMake derlemesi..."
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc 2>/dev/null || echo 4)"

if [[ -x build/bin/whisper-cli ]]; then
  echo "[build_whisper] İkili: $WSP/build/bin/whisper-cli"
elif [[ -x build/bin/main ]]; then
  echo "[build_whisper] İkili: $WSP/build/bin/main"
elif [[ -x main ]]; then
  echo "[build_whisper] İkili: $WSP/main"
else
  echo "[build_whisper] UYARI: whisper ikili bulunamadı, build çıktısına bakın."
fi

if [[ -f models/download-ggml-model.sh ]]; then
  echo "[build_whisper] ggml-small model indiriliyor..."
  bash models/download-ggml-model.sh small
else
  echo "[build_whisper] UYARI: models/download-ggml-model.sh yok"
fi

echo "[build_whisper] Python kontrolü:"
cd "$ROOT"
export PYTHONPATH="$ROOT"
PY="$ROOT/venv/bin/python3"
[[ -x "$PY" ]] || PY="python3"
"$PY" -c "import config; print('WHISPER_BINARY=', config.WHISPER_BINARY, 'exists=', config.WHISPER_BINARY.is_file()); print('WHISPER_MODEL =', config.WHISPER_MODEL, 'exists=', config.WHISPER_MODEL.is_file())"

echo "[build_whisper] Bitti."
