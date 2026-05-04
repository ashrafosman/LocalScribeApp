#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

APP_NAME="$(python3 - <<'PY'
import json
with open("src-tauri/tauri.conf.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print(data["package"]["productName"])
PY
)"

VERSION="$(python3 - <<'PY'
import json
with open("src-tauri/tauri.conf.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print(data["package"]["version"])
PY
)"

ARCH="$(uname -m)"
if [[ "${ARCH}" == "arm64" ]]; then
  ARCH="aarch64"
fi

BUNDLE_DIR="${ROOT_DIR}/src-tauri/target/release/bundle"
APP_DIR="${BUNDLE_DIR}/macos/${APP_NAME}.app"
PLIST_PATH="${APP_DIR}/Contents/Info.plist"
STREAM_PATH="${APP_DIR}/Contents/Resources/whisper/stream"
DMG_SCRIPT="${BUNDLE_DIR}/dmg/bundle_dmg.sh"
DMG_NAME="${APP_NAME}_${VERSION}_${ARCH}.dmg"
DMG_ICON="${BUNDLE_DIR}/dmg/icon.icns"

if [[ -f "${PLIST_PATH}" ]]; then
  if ! /usr/libexec/PlistBuddy -c "Print :NSMicrophoneUsageDescription" "${PLIST_PATH}" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string MyScribe needs access to the microphone for transcription." "${PLIST_PATH}"
  fi
fi

if [[ -f "${STREAM_PATH}" ]]; then
  chmod +x "${STREAM_PATH}"
fi

if [[ -x "${DMG_SCRIPT}" && -d "${APP_DIR}" ]]; then
  rm -f "${BUNDLE_DIR}/macos/${DMG_NAME}"
  rm -f "${BUNDLE_DIR}/dmg/${DMG_NAME}"
  pushd "${BUNDLE_DIR}/macos" >/dev/null
  "${DMG_SCRIPT}" \
    --volname "${APP_NAME}" \
    --icon "${APP_NAME}.app" 180 170 \
    --app-drop-link 480 170 \
    --window-size 660 400 \
    --hide-extension "${APP_NAME}.app" \
    --volicon "${DMG_ICON}" \
    "${DMG_NAME}" \
    "${APP_NAME}.app"
  popd >/dev/null

  POST_DMG="${BUNDLE_DIR}/macos/${DMG_NAME}"
  TARGET_DMG="${BUNDLE_DIR}/dmg/${DMG_NAME}"
  if [[ -f "${POST_DMG}" ]]; then
    mv -f "${POST_DMG}" "${TARGET_DMG}"
  fi
fi
