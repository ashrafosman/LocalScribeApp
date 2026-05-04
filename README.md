# MyScribe

This repo ships the macOS build artifacts for MyScribe.

## Download

- `tauri-app/src-tauri/target/release/bundle/macos/MyScribe.app`
- `macos/MyScribe_1.0.0_aarch64.dmg`

## Prompt Templates

- Meeting prompt templates live in `prompts/` for reference.

## Install

1. Open the `.dmg` and drag `MyScribe.app` into Applications.
2. Launch the app and grant microphone permissions when prompted.

## Settings Location (macOS)

- `~/Library/Application Support/myscribe/settings.json`

## Troubleshooting

- If transcription works in dev but not in the packaged app, ensure mic permissions are granted in System Settings → Privacy & Security → Microphone.

## Compliance Notes

- Recording requires in-app consent acknowledgement before it can start.
- No speaker diarization or speaker identification is used.
- Local mode processes audio on-device; no audio recording is saved. Text transcripts are stored locally.
- Transcripts/summaries older than 7 days are deleted after the user approves the retention notice on app launch.
- Summarization endpoints are restricted to localhost (local) or Databricks URLs; non-compliant URLs are cleared on save and blocked at runtime.
- AI-generated summaries may be inaccurate; review for accuracy before relying on them.
- Do not use MyScribe for Legal or People/HR meetings.
- Do not include personal data unless permitted by policy. See go/llmpolicy for details.
- Do not share summaries outside Databricks without confirming they contain no confidential information.

## Capabilities

- Realtime transcription (local only).
- Generate key points, action items, and issues on demand using a compliant LLM endpoint.
- Ask follow-up questions using live notes.
- View summaries with Markdown rendering.

© 2025 Ashraf Osman
