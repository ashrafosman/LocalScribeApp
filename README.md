# LocalScribe

This repo ships the macOS build artifacts for LocalScribe.

## Download

- `tauri-app/src-tauri/target/release/bundle/macos/localscribe.app`
- `macos/localscribe_0.1.0_aarch64.dmg`

## Prompt Templates

- Meeting prompt templates live in `prompts/` for reference.

## Install

1. Open the `.dmg` and drag `localscribe.app` into Applications.
2. Launch the app and grant microphone permissions when prompted.

## Settings Location (macOS)

- `~/Library/Application Support/localscribe/settings.json`

## Troubleshooting

- If transcription works in dev but not in the packaged app, ensure mic permissions are granted in System Settings → Privacy & Security → Microphone.
- For local transcription, `whisper.cpp` and its `stream` binary must exist at the paths in settings.

## Capabilities

- Realtime transcription (local or API).
- Generate key points, action items, and issues on demand.
- Ask follow-up questions using live notes.
- View summaries with Markdown rendering.

© 2025 Ashraf Osman
