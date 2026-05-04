#!/usr/bin/env bash
set -euo pipefail

WHISPER_CPP_PATH="${WHISPER_CPP_PATH:-/Users/ashraf.osman/Documents/Work/whisper.cpp}"
STREAM_BIN="${WHISPER_CPP_PATH}/stream"
RESOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src-tauri/resources/whisper"

if [[ ! -x "${STREAM_BIN}" ]]; then
  echo "Missing whisper.cpp stream binary at ${STREAM_BIN}" >&2
  exit 1
fi

mkdir -p "${RESOURCE_DIR}"
cp "${STREAM_BIN}" "${RESOURCE_DIR}/stream"
chmod +x "${RESOURCE_DIR}/stream"

STREAM_DIR="$(dirname "${STREAM_BIN}")"
while IFS= read -r -d '' dylib; do
  cp "${dylib}" "${RESOURCE_DIR}/"
done < <(find "${STREAM_DIR}" -maxdepth 1 -type f -name "*.dylib" -print0)
