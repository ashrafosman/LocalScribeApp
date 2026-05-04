use std::fs;
use std::path::Path;

fn main() {
    tauri_build::build();
    inject_microphone_usage_description();
}

fn inject_microphone_usage_description() {
    let out_dir = match std::env::var("OUT_DIR") {
        Ok(value) => value,
        Err(_) => return,
    };
    let plist_path = Path::new(&out_dir).join("Info.plist");
    let Ok(contents) = fs::read_to_string(&plist_path) else {
        return;
    };
    if contents.contains("NSMicrophoneUsageDescription") {
        return;
    }
    let insert = "  <key>NSMicrophoneUsageDescription</key>\n  <string>MyScribe needs access to the microphone for transcription.</string>\n";
    let updated = if let Some(pos) = contents.rfind("</dict>") {
        let mut result = String::new();
        result.push_str(&contents[..pos]);
        result.push_str(insert);
        result.push_str(&contents[pos..]);
        result
    } else {
        contents
    };
    let _ = fs::write(&plist_path, updated);
}
