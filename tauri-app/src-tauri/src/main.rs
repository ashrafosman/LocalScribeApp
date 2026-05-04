#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use base64::{engine::general_purpose::STANDARD, Engine as _};
use chrono::{DateTime, Local};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};
use tauri::{AppHandle, Manager, State};
use uuid::Uuid;

const WHISPER_MODEL_FILENAME: &str = "ggml-small.en.bin";
const WHISPER_MODEL_URL: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin";
const WHISPER_RESOURCE_STREAM: &str = "whisper/stream";
const RETENTION_DAYS: i64 = 7;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct AppSettings {
    calls_output_path: String,
    summary_api_url: String,
    summary_api_model: String,
    summary_api_token: String,
    whisper_mode: String,
    whisper_api_url: String,
    whisper_api_token: String,
    whisper_api_sample_rate: u32,
    whisper_api_chunk_duration: u32,
    whisper_cpp_path: String,
    whisper_stream_path: String,
    whisper_model_path: String,
    whisper_threads: u32,
}

impl Default for AppSettings {
    fn default() -> Self {
        let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
        let calls_output = home.join("Documents").join("MyScribe").join("Calls");
        let whisper_cpp = home.join("Documents").join("Work").join("whisper.cpp");
        let whisper_model = whisper_cpp.join("models").join("ggml-small.en.bin");
        Self {
            calls_output_path: calls_output.to_string_lossy().to_string(),
            summary_api_url: "https://api.perplexity.ai/chat/completions".to_string(),
            summary_api_model: "sonar".to_string(),
            summary_api_token: String::new(),
            whisper_mode: "local".to_string(),
            whisper_api_url: String::new(),
            whisper_api_token: String::new(),
            whisper_api_sample_rate: 16_000,
            whisper_api_chunk_duration: 3,
            whisper_cpp_path: whisper_cpp.to_string_lossy().to_string(),
            whisper_stream_path: whisper_cpp.join("stream").to_string_lossy().to_string(),
            whisper_model_path: whisper_model.to_string_lossy().to_string(),
            whisper_threads: 8,
        }
    }
}

#[derive(Clone, Serialize)]
struct DeviceOption {
    id: i32,
    name: String,
}

#[derive(Clone, Serialize)]
struct PromptOption {
    id: String,
    name: String,
}

#[derive(Clone)]
struct RecordingControl {
    id: String,
    name: String,
    transcript_path: PathBuf,
    summary_path: PathBuf,
    log_path: PathBuf,
    transcript_text: Arc<Mutex<String>>,
    stop_flag: Arc<AtomicBool>,
    process: Arc<Mutex<Option<Child>>>,
    prompt_type: String,
    start_time: SystemTime,
}

struct AppState {
    settings: Mutex<AppSettings>,
    recording: Mutex<Option<RecordingControl>>,
}

#[derive(Clone, Serialize)]
struct StatusPayload {
    status: String,
    message: String,
}

#[derive(Clone, Serialize)]
struct ModelDownloadPayload {
    bytes: u64,
    total: Option<u64>,
    percent: Option<u8>,
    done: bool,
}

#[derive(Clone, Serialize)]
struct SummaryPayload {
    keypoints: Vec<String>,
    actions: Vec<String>,
    issues: Vec<String>,
    raw: String,
}

#[tauri::command]
fn get_settings(app: AppHandle, state: State<AppState>) -> Result<AppSettings, String> {
    let mut settings = load_or_init_settings()?;
    let changed = apply_bundled_whisper_paths(&app, &mut settings)?;
    if changed {
        save_settings_to_disk(&settings)?;
    }
    *state.settings.lock().map_err(|_| "Settings lock error")? = settings.clone();
    Ok(settings)
}

#[tauri::command]
fn save_settings(state: State<AppState>, settings: AppSettings) -> Result<(), String> {
    save_settings_to_disk(&settings)?;
    *state.settings.lock().map_err(|_| "Settings lock error")? = settings;
    Ok(())
}

#[derive(Clone, Serialize)]
struct ExpiredArtifactsSummary {
    count: usize,
    total_bytes: u64,
}

#[tauri::command]
fn get_expired_artifacts_summary(state: State<AppState>) -> Result<ExpiredArtifactsSummary, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let expired = list_expired_artifacts(&settings)?;
    let mut total_bytes = 0u64;
    for path in expired.iter() {
        if let Ok(metadata) = fs::metadata(path) {
            total_bytes = total_bytes.saturating_add(metadata.len());
        }
    }
    Ok(ExpiredArtifactsSummary {
        count: expired.len(),
        total_bytes,
    })
}

#[tauri::command]
fn delete_expired_artifacts(state: State<AppState>) -> Result<usize, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let expired = list_expired_artifacts(&settings)?;
    let mut deleted = 0usize;
    for path in expired {
        if fs::remove_file(&path).is_ok() {
            deleted += 1;
        }
    }
    Ok(deleted)
}

#[tauri::command]
fn re_summarize_recording(transcript_path: String, prompt_type: String) -> Result<String, String> {
    let settings = load_or_init_settings()?;
    let transcript = PathBuf::from(&transcript_path);
    if !transcript.exists() {
        return Err("Transcript file not found".to_string());
    }
    let log_path = log_path_for_transcript(&transcript);
    let summary_path = transcript.with_file_name(format!(
        "{}-summarized.txt",
        transcript.file_name().and_then(|name| name.to_str()).unwrap_or("summary.txt")
    ));
    let prompt_content = get_prompt_content(&prompt_type);
    let transcript_text = fs::read_to_string(&transcript).map_err(|err| format!("Failed to read transcript: {err}"))?;
    let summary_text = summarize_text(&settings, &transcript_text, &prompt_content).map_err(|err| {
        append_log_path(&log_path, &format!("Summary API error: {err}"));
        err
    })?;
    fs::write(&summary_path, &summary_text).map_err(|err| format!("Failed to write summary: {err}"))?;
    Ok(summary_path.to_string_lossy().to_string())
}

#[tauri::command]
fn ensure_whisper_ready(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    let mut settings = load_or_init_settings()?;
    let mut changed = apply_bundled_whisper_paths(&app, &mut settings)?;
    if settings.whisper_mode != "api" {
        let model_missing = !Path::new(&settings.whisper_model_path).exists();
        let app_handle = app.clone();
        let settings_clone = settings.clone();
        thread::spawn(move || {
            if let Err(err) = ensure_whisper_model(&app_handle, &settings_clone) {
                emit_status(&app_handle, "error", &err);
            }
        });
        if model_missing {
            changed = true;
        }
    }
    if changed {
        save_settings_to_disk(&settings)?;
    }
    *state.settings.lock().map_err(|_| "Settings lock error")? = settings;
    Ok(())
}

#[tauri::command]
fn download_whisper_model(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    let mut settings = load_or_init_settings()?;
    let changed = apply_bundled_whisper_paths(&app, &mut settings)?;
    let app_handle = app.clone();
    let settings_clone = settings.clone();
    thread::spawn(move || {
        let model_path = Path::new(&settings_clone.whisper_model_path);
        if model_path.exists() {
            if let Err(err) = fs::remove_file(model_path) {
                emit_status(&app_handle, "error", &format!("Failed to remove Whisper model: {err}"));
                return;
            }
        }
        if let Err(err) = ensure_whisper_model(&app_handle, &settings_clone) {
            emit_status(&app_handle, "error", &err);
        } else {
            emit_status(&app_handle, "complete", "Whisper model updated.");
        }
    });

    if changed {
        save_settings_to_disk(&settings)?;
    }
    *state.settings.lock().map_err(|_| "Settings lock error")? = settings;
    Ok(())
}

#[tauri::command]
fn list_audio_devices(state: State<AppState>) -> Result<Vec<DeviceOption>, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    if settings.whisper_mode == "api" {
        return Ok(list_cpal_devices());
    }

    let stream_path = Path::new(&settings.whisper_stream_path);
    if !stream_path.exists() {
        return Ok(list_cpal_devices());
    }

    let mut child = Command::new(stream_path)
        .arg("-c")
        .arg("-2")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();

    let output = if let Ok(mut process) = child {
        thread::sleep(Duration::from_secs(1));
        let _ = process.kill();
        process.wait_with_output().ok()
    } else {
        None
    };

    let mut devices = Vec::new();
    let combined = match output {
        Some(output) => format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)),
        None => String::new(),
    };
    for line in combined.lines() {
        if let Some(pos) = line.find("Capture device #") {
            let tail = &line[pos + "Capture device #".len()..];
            if let Some((id_part, name_part)) = tail.split_once(":") {
                let id = id_part.trim().parse::<i32>().unwrap_or(-1);
                let name = if let Some(start) = name_part.find('\'') {
                    let name_tail = &name_part[start + 1..];
                    name_tail.split('\'').next().unwrap_or("Device").trim().to_string()
                } else {
                    format!("Device {id}")
                };
                devices.push(DeviceOption { id, name });
            }
        }
    }

    if devices.is_empty() {
        return Ok(list_cpal_devices());
    }

    let mut final_devices = vec![DeviceOption { id: -1, name: "System Default".to_string() }];
    for mut device in devices {
        let lower = device.name.to_lowercase();
        if lower.contains("blackhole") {
            device.name = format!("{} (System Audio + Mic - Requires Setup)", device.name);
        } else if lower.contains("aggregate") {
            device.name = format!("{} (Multi-Input - May Capture Both)", device.name);
        }
        final_devices.push(device);
    }

    Ok(final_devices)
}

fn list_cpal_devices() -> Vec<DeviceOption> {
    let host = cpal::default_host();
    let default_name = host
        .default_input_device()
        .and_then(|device| device.name().ok())
        .unwrap_or_else(|| "System Default".to_string());
    let mut devices = vec![DeviceOption {
        id: -1,
        name: format!("System Default ({default_name})"),
    }];
    if let Ok(inputs) = host.input_devices() {
        for (index, device) in inputs.enumerate() {
            let name = device.name().unwrap_or_else(|_| format!("Input {index}"));
            devices.push(DeviceOption { id: index as i32, name });
        }
    }
    devices
}

#[tauri::command]
fn list_prompts() -> Result<Vec<PromptOption>, String> {
    let prompt_files: HashMap<&str, &str> = HashMap::from([
        ("meeting", "Executive Meeting"),
        ("technical", "Technical Review"),
        ("sales", "Sales Call"),
        ("standup", "Daily Standup"),
        ("one_on_one", "1:1 Meeting"),
        ("staff", "Staff Meeting"),
    ]);

    let mut prompts = Vec::new();
    if let Some(dir) = find_prompts_dir() {
        for (id, name) in prompt_files.iter() {
            let file_path = dir.join(format!("{id}.txt"));
            if file_path.exists() {
                prompts.push(PromptOption { id: (*id).to_string(), name: (*name).to_string() });
            }
        }
    }

    if prompts.is_empty() {
        prompts.push(PromptOption { id: "meeting".to_string(), name: "Executive Meeting".to_string() });
    }

    Ok(prompts)
}

#[tauri::command]
fn list_recordings(state: State<AppState>) -> Result<Vec<HashMap<String, String>>, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    Ok(get_meeting_files(&settings))
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let (command, args) = if cfg!(target_os = "macos") {
        ("open", vec![path])
    } else if cfg!(target_os = "windows") {
        ("cmd", vec!["/C".to_string(), "start".to_string(), path])
    } else {
        ("xdg-open", vec![path])
    };

    Command::new(command)
        .args(args)
        .spawn()
        .map_err(|err| format!("Failed to open path: {err}"))?;
    Ok(())
}

#[tauri::command]
fn read_summary_file(state: State<AppState>, path: String) -> Result<String, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let output_dir = Path::new(&settings.calls_output_path)
        .canonicalize()
        .map_err(|err| format!("Invalid output path: {err}"))?;
    let summary_path = Path::new(&path)
        .canonicalize()
        .map_err(|err| format!("Invalid summary path: {err}"))?;
    if !summary_path.starts_with(&output_dir) {
        return Err("Summary path is outside the output folder".to_string());
    }
    fs::read_to_string(summary_path).map_err(|err| format!("Failed to read summary: {err}"))
}

#[tauri::command]
fn check_summary_ready(state: State<AppState>) -> Result<bool, String> {
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let messages = vec![
        json!({"role": "system", "content": "You are a test assistant."}),
        json!({"role": "user", "content": "Respond with OK."}),
    ];
    call_chat_api(&settings, &messages, Some(json!({"max_tokens": 4})))?;
    Ok(true)
}

#[tauri::command]
fn ask_question(state: State<AppState>, transcript_text: String, question: String) -> Result<String, String> {
    if transcript_text.trim().is_empty() {
        return Err("Transcript text is empty".to_string());
    }
    if question.trim().is_empty() {
        return Err("Question is empty".to_string());
    }

    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let system_prompt = "You are a meeting assistant. Answer the user's question using only the meeting transcript. If the answer is not in the transcript, say you don't know. Keep the response concise.";
    let user_prompt = format!("Transcript:\n{transcript_text}\n\nQuestion: {question}");
    let messages = vec![
        json!({"role": "system", "content": system_prompt}),
        json!({"role": "user", "content": user_prompt}),
    ];
    call_chat_api(&settings, &messages, None)
}

#[tauri::command]
fn summarize_section(
    state: State<AppState>,
    transcript_text: String,
    section: String,
) -> Result<String, String> {
    if transcript_text.trim().is_empty() {
        return Err("Transcript text is empty".to_string());
    }
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let section_label = match section.as_str() {
        "keypoints" => "Key Points",
        "actions" => "Action Items",
        "issues" => "Issues & Solutions",
        _ => "Summary",
    };
    let prompt = format!(
        "You are a meeting assistant. Extract the {section_label} from the transcript. Return a concise Markdown bullet list."
    );
    let messages = vec![
        json!({"role": "system", "content": prompt}),
        json!({"role": "user", "content": format!("Transcript:\n{transcript_text}")}),
    ];
    call_chat_api(&settings, &messages, None)
}

#[tauri::command]
fn suggest_questions(state: State<AppState>, transcript_text: String) -> Result<String, String> {
    if transcript_text.trim().is_empty() {
        return Err("Transcript text is empty".to_string());
    }
    let settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let prompt = "You are a meeting assistant. Suggest 3-5 concise follow-up questions to ask next based on the transcript. Return a Markdown bullet list.";
    let messages = vec![
        json!({"role": "system", "content": prompt}),
        json!({"role": "user", "content": format!("Transcript:\n{transcript_text}")}),
    ];
    call_chat_api(&settings, &messages, None)
}

#[tauri::command]
fn start_recording(
    app: AppHandle,
    state: State<AppState>,
    meeting_name: String,
    device_id: i32,
    device_name: String,
    prompt_type: String,
) -> Result<String, String> {
    if meeting_name.trim().is_empty() {
        return Err("Meeting name is required".to_string());
    }

    let mut settings = state.settings.lock().map_err(|_| "Settings lock error")?.clone();
    let mut changed = apply_bundled_whisper_paths(&app, &mut settings)?;
    if settings.whisper_mode != "api" {
        let model_missing = !Path::new(&settings.whisper_model_path).exists();
        ensure_whisper_model(&app, &settings)?;
        if model_missing {
            changed = true;
        }
    }
    if changed {
        save_settings_to_disk(&settings)?;
        *state.settings.lock().map_err(|_| "Settings lock error")? = settings.clone();
    }

    let errors = validate_settings(&settings);
    if !errors.is_empty() {
        return Err(format!("Configuration errors: {}", errors.join(", ")));
    }

    let mut recording_lock = state.recording.lock().map_err(|_| "Recording lock error")?;
    if recording_lock.is_some() {
        return Err("Recording already in progress".to_string());
    }

    let meeting_id = Uuid::new_v4().to_string();
    let sanitized = sanitize_filename(&meeting_name);
    let today = Local::now().format("%Y-%m-%d").to_string();
    let base_name = format!("{}_{}", today, sanitized);

    let output_dir = PathBuf::from(&settings.calls_output_path);
    fs::create_dir_all(&output_dir).map_err(|err| format!("Failed to create output dir: {err}"))?;

    let (transcript_filename, summary_filename) = unique_name(&output_dir, &base_name);
    let transcript_path = output_dir.join(&transcript_filename);
    let summary_path = output_dir.join(&summary_filename);
    let log_path = log_path_for_transcript(&transcript_path);

    let control = RecordingControl {
        id: meeting_id.clone(),
        name: meeting_name.clone(),
        transcript_path: transcript_path.clone(),
        summary_path: summary_path.clone(),
        log_path: log_path.clone(),
        transcript_text: Arc::new(Mutex::new(String::new())),
        stop_flag: Arc::new(AtomicBool::new(false)),
        process: Arc::new(Mutex::new(None)),
        prompt_type: prompt_type.clone(),
        start_time: SystemTime::now(),
    };

    *recording_lock = Some(control.clone());

    let app_handle = app.clone();
    thread::spawn(move || {
        let result = if settings.whisper_mode == "api" && !settings.whisper_api_url.trim().is_empty() {
            run_api_recording(&app_handle, &settings, &control, device_name)
        } else {
            run_local_recording(&app_handle, &settings, &control, device_id)
        };

        if let Err(err) = result {
            append_log(&control, &format!("Recording error: {err}"));
            emit_status(&app_handle, "error", &format!("Recording error: {err}"));
            let state: State<AppState> = app_handle.state();
            if let Ok(mut recording_lock) = state.recording.lock() {
                *recording_lock = None;
            }
            return;
        }
        if let Err(err) = finalize_recording(&app_handle, &settings, &control) {
            append_log(&control, &format!("Processing error: {err}"));
            emit_status(&app_handle, "error", &format!("Processing error: {err}"));
        }
    });

    Ok(meeting_id)
}

#[tauri::command]
fn stop_recording(state: State<AppState>) -> Result<(), String> {
    let mut recording_lock = state.recording.lock().map_err(|_| "Recording lock error")?;
    let recording = recording_lock.as_mut().ok_or("No active recording")?;
    recording.stop_flag.store(true, Ordering::SeqCst);

    if let Some(child) = recording.process.lock().map_err(|_| "Process lock error")?.as_mut() {
        unsafe {
            libc::kill(child.id() as i32, libc::SIGINT);
        }
    }

    Ok(())
}

fn run_local_recording(
    app: &AppHandle,
    settings: &AppSettings,
    control: &RecordingControl,
    device_id: i32,
) -> Result<(), String> {
    emit_status(app, "recording", "Recording started");

    let mut cmd = Command::new(&settings.whisper_stream_path);
    cmd.arg("-m")
        .arg(&settings.whisper_model_path)
        .arg("-t")
        .arg(settings.whisper_threads.to_string())
        .arg("-kc");

    if settings.whisper_model_path.contains("tdrz") {
        cmd.arg("-tdrz");
    }

    if device_id != -1 {
        cmd.arg("-c").arg(device_id.to_string());
    }

    cmd.arg("-f")
        .arg(&control.transcript_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|err| format!("Failed to start whisper stream: {err}"))?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    *control.process.lock().map_err(|_| "Process lock error")? = Some(child);

    if let Some(out) = stdout {
        let app_handle = app.clone();
        let transcript_text = control.transcript_text.clone();
        thread::spawn(move || stream_lines(out, &app_handle, &transcript_text, None, true, false));
    }
    if let Some(err) = stderr {
        let app_handle = app.clone();
        let transcript_text = control.transcript_text.clone();
        let log_path = control.log_path.clone();
        thread::spawn(move || stream_lines(err, &app_handle, &transcript_text, Some(log_path), true, true));
    }

    loop {
        if control.stop_flag.load(Ordering::SeqCst) {
            emit_status(app, "processing", "Processing and summarizing...");
            break;
        }
        let finished = {
            let mut process_lock = control.process.lock().map_err(|_| "Process lock error")?;
            if let Some(proc) = process_lock.as_mut() {
                proc.try_wait().map_err(|err| format!("Failed to wait for process: {err}"))?.is_some()
            } else {
                true
            }
        };
        if finished {
            emit_status(app, "processing", "Processing and summarizing...");
            break;
        }
        thread::sleep(Duration::from_millis(200));
    }

    if let Some(mut proc) = control.process.lock().map_err(|_| "Process lock error")?.take() {
        let _ = proc.wait();
    }
    Ok(())
}

fn run_api_recording(
    app: &AppHandle,
    settings: &AppSettings,
    control: &RecordingControl,
    device_name: String,
) -> Result<(), String> {
    emit_status(app, "recording", "Recording started (API)");

    let sample_rate = settings.whisper_api_sample_rate;
    let chunk_duration = settings.whisper_api_chunk_duration;
    let chunk_samples = sample_rate as usize * chunk_duration as usize;
    let stop_flag = control.stop_flag.clone();
    let transcript_text = control.transcript_text.clone();
    let transcript_path = control.transcript_path.clone();
    let api_url = settings.whisper_api_url.clone();
    let api_token = settings.whisper_api_token.clone();
    let app_handle = app.clone();

    let host = cpal::default_host();
    let device = if !device_name.trim().is_empty() {
        host.input_devices()
            .ok()
            .and_then(|mut devices| {
                devices.find(|device| {
                    device
                        .name()
                        .map(|name| name.to_lowercase().contains(&device_name.to_lowercase()))
                        .unwrap_or(false)
                })
            })
            .or_else(|| host.default_input_device())
    } else {
        host.default_input_device()
    };

    let device = device.ok_or("No input audio device found")?;
    let config = device
        .default_input_config()
        .map_err(|err| format!("Failed to read default input config: {err}"))?;
    let mut stream_config = config.config();
    stream_config.channels = 1;
    stream_config.sample_rate = cpal::SampleRate(sample_rate);

    let (sender, receiver) = mpsc::channel::<Vec<f32>>();
    let buffer = Arc::new(Mutex::new(Vec::<f32>::new()));
    let buffer_clone = buffer.clone();
    let sender_clone = sender.clone();

    let app_handle_error = app_handle.clone();
    let log_path = control.log_path.clone();
    let err_fn = move |err| {
        append_log_path(&log_path, &format!("Audio stream error: {err}"));
        emit_status(&app_handle_error, "error", &format!("Audio stream error: {err}"));
    };

    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => device.build_input_stream(
            &stream_config,
            move |data: &[f32], _| {
                let mut buf = buffer_clone.lock().unwrap();
                buf.extend_from_slice(data);
                while buf.len() >= chunk_samples {
                    let chunk: Vec<f32> = buf.drain(..chunk_samples).collect();
                    let _ = sender_clone.send(chunk);
                }
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::I16 => device.build_input_stream(
            &stream_config,
            move |data: &[i16], _| {
                let mut buf = buffer_clone.lock().unwrap();
                buf.extend(data.iter().map(|value| *value as f32 / i16::MAX as f32));
                while buf.len() >= chunk_samples {
                    let chunk: Vec<f32> = buf.drain(..chunk_samples).collect();
                    let _ = sender_clone.send(chunk);
                }
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::U16 => device.build_input_stream(
            &stream_config,
            move |data: &[u16], _| {
                let mut buf = buffer_clone.lock().unwrap();
                buf.extend(data.iter().map(|value| *value as f32 / u16::MAX as f32 - 0.5));
                while buf.len() >= chunk_samples {
                    let chunk: Vec<f32> = buf.drain(..chunk_samples).collect();
                    let _ = sender_clone.send(chunk);
                }
            },
            err_fn,
            None,
        ),
        _ => return Err("Unsupported audio sample format".to_string()),
    }
    .map_err(|err| format!("Failed to build input stream: {err}"))?;

    stream.play().map_err(|err| format!("Failed to start audio stream: {err}"))?;

    let app_handle_writer = app_handle.clone();
    let log_path = control.log_path.clone();
    let writer_thread = thread::spawn(move || {
        let client = reqwest::blocking::Client::new();
        while !stop_flag.load(Ordering::SeqCst) {
            let chunk = match receiver.recv_timeout(Duration::from_millis(500)) {
                Ok(chunk) => chunk,
                Err(mpsc::RecvTimeoutError::Timeout) => continue,
                Err(_) => break,
            };

            if stop_flag.load(Ordering::SeqCst) {
                break;
            }

            match transcribe_chunk(&client, &api_url, &api_token, sample_rate, &chunk) {
                Ok(text) => {
                    if !text.trim().is_empty() {
                        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(&transcript_path) {
                            let _ = writeln!(file, "{text}");
                        }
                        if let Ok(mut stored) = transcript_text.lock() {
                            stored.push_str(&text);
                            stored.push('\n');
                        }
                        let _ = app_handle_writer.emit_all("transcription", json!({"text": text}));
                    }
                }
                Err(err) => {
                    append_log_path(&log_path, &format!("Whisper API error: {err}"));
                }
            }
        }
    });

    while !control.stop_flag.load(Ordering::SeqCst) {
        thread::sleep(Duration::from_millis(200));
    }

    let _ = writer_thread.join();
    emit_status(app, "processing", "Processing and summarizing...");
    Ok(())
}

fn finalize_recording(app: &AppHandle, settings: &AppSettings, control: &RecordingControl) -> Result<(), String> {
    if control.stop_flag.load(Ordering::SeqCst) {
        thread::sleep(Duration::from_millis(400));
    }

    let transcript_path = &control.transcript_path;
    if !transcript_path.exists() {
        append_log(control, "Transcript file not found");
        emit_status(app, "error", "Transcript file not found");
        return Err("Transcript file not found".to_string());
    }

    let prompt_content = get_prompt_content(&control.prompt_type);
    let transcript_text = fs::read_to_string(transcript_path).map_err(|err| format!("Failed to read transcript: {err}"))?;
    let summary_text = summarize_text(settings, &transcript_text, &prompt_content)?;
    fs::write(&control.summary_path, &summary_text).map_err(|err| format!("Failed to write summary: {err}"))?;

    let summary_payload = parse_summary_sections(&summary_text);
    let _ = app.emit_all("summary", summary_payload);
    emit_status(app, "complete", "Meeting processing complete");
    let state: State<AppState> = app.state();
    if let Ok(mut recording_lock) = state.recording.lock() {
        *recording_lock = None;
    }
    Ok(())
}

fn stream_lines(
    stream: impl std::io::Read,
    app: &AppHandle,
    transcript_text: &Arc<Mutex<String>>,
    log_path: Option<PathBuf>,
    emit_transcript: bool,
    log_all: bool,
) {
    let reader = BufReader::new(stream);
    let skip_patterns = vec![
        "whisper_init_from_file",
        "whisper_init_with_params",
        "whisper_model_load",
        "whisper_backend_init",
        "ggml_metal_init",
        "whisper_init_state",
        "main: processing",
        "main: n_new_line",
        "[ Silence ]",
        "[BLANK_AUDIO]",
        "[Start speaking]",
        "init:",
        "whisper_print_timings",
        "found ",
        "attempt to open",
        "obtained spec",
        "sample rate:",
        "format:",
        "channels:",
        "samples per frame:",
    ];
    for line in reader.lines().flatten() {
        let clean = strip_ansi(&line);
        let trimmed = clean.trim();
        if trimmed.is_empty() {
            continue;
        }
        if log_all {
            if let Some(path) = &log_path {
                append_log_path(path, trimmed);
            }
        }
        if !emit_transcript {
            continue;
        }
        if skip_patterns.iter().any(|pattern| trimmed.contains(pattern)) {
            continue;
        }
        if trimmed.len() <= 1 || trimmed == "." || trimmed == ".." || trimmed == "..." {
            continue;
        }
        let mut normalized = trimmed.replace("\u{1b}[2K", "");
        normalized = normalized.replace("[2K]", "");
        normalized = normalized.replace("2K", "");
        normalized = normalized.split_whitespace().collect::<Vec<_>>().join(" ");
        if normalized.is_empty() || normalized == "2K" {
            continue;
        }

        if let Ok(mut stored) = transcript_text.lock() {
            stored.push_str(&normalized);
            stored.push('\n');
        }
        let _ = app.emit_all("transcription", json!({"text": normalized}));
    }
}

fn strip_ansi(input: &str) -> String {
    let mut result = String::new();
    let mut chars = input.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\u{1b}' {
            while let Some(next) = chars.next() {
                if ('@'..='~').contains(&next) {
                    break;
                }
            }
        } else {
            result.push(ch);
        }
    }
    result.replace("[2K", "").trim().to_string()
}

fn transcribe_chunk(
    client: &reqwest::blocking::Client,
    url: &str,
    token: &str,
    sample_rate: u32,
    chunk: &[f32],
) -> Result<String, String> {
    let mut cursor = Cursor::new(Vec::new());
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    {
        let mut writer = hound::WavWriter::new(&mut cursor, spec)
            .map_err(|err| format!("Failed to create WAV writer: {err}"))?;
        for sample in chunk {
            let clamped = (sample * 32767.0).clamp(-32768.0, 32767.0) as i16;
            writer.write_sample(clamped).ok();
        }
        writer.finalize().map_err(|err| format!("Failed to finalize WAV: {err}"))?;
    }
    let encoded = STANDARD.encode(cursor.into_inner());

    let mut last_error = None;
    for attempt in 1..=3 {
        let mut request = client.post(url).json(&json!({"inputs": [encoded]}));
        if !token.trim().is_empty() {
            request = request.bearer_auth(token);
        }
        match request.send() {
            Ok(response) => {
                let status = response.status();
                if !status.is_success() {
                    let text = response.text().unwrap_or_default();
                    last_error = Some(format!("Whisper API error: {status} {text}"));
                } else {
                    let data: Value = response
                        .json()
                        .map_err(|err| format!("Whisper API parse error: {err}"))?;
                    return Ok(extract_whisper_text(&data));
                }
            }
            Err(err) => {
                last_error = Some(format!("Whisper API error: {err}"));
            }
        }
        thread::sleep(Duration::from_millis(300 * attempt));
    }
    Err(last_error.unwrap_or_else(|| "Whisper API error".to_string()))
}

fn extract_whisper_text(value: &Value) -> String {
    if let Some(text) = value.as_str() {
        return text.trim().to_string();
    }
    if let Some(predictions) = value.get("predictions").and_then(|v| v.as_array()) {
        if let Some(first) = predictions.first() {
            return extract_whisper_text(first);
        }
    }
    if let Some(text) = value.get("text").and_then(|v| v.as_str()) {
        return text.trim().to_string();
    }
    if let Some(transcription) = value.get("transcription").and_then(|v| v.as_str()) {
        return transcription.trim().to_string();
    }
    if let Some(transcript) = value.get("transcript").and_then(|v| v.as_str()) {
        return transcript.trim().to_string();
    }
    if let Some(output) = value.get("output").and_then(|v| v.as_str()) {
        return output.trim().to_string();
    }
    if let Some(choices) = value.get("choices").and_then(|v| v.as_array()) {
        if let Some(first) = choices.first() {
            if let Some(message) = first.get("message") {
                return extract_message_content(message);
            }
        }
    }
    String::new()
}

fn summarize_text(settings: &AppSettings, transcript_text: &str, prompt_content: &str) -> Result<String, String> {
    if transcript_text.trim().is_empty() {
        return Err("Transcript text is empty".to_string());
    }
    if prompt_content.trim().is_empty() {
        return Err("Prompt content is empty".to_string());
    }
    let messages = vec![
        json!({"role": "system", "content": prompt_content}),
        json!({"role": "user", "content": format!("Please summarize the following meeting transcript accordingly:\n\n{transcript_text}")}),
    ];
    call_chat_api(settings, &messages, None)
}

fn call_chat_api(settings: &AppSettings, messages: &[Value], extra_payload: Option<Value>) -> Result<String, String> {
    if settings.summary_api_url.trim().is_empty() {
        return Err("Summary API URL is required".to_string());
    }
    let client = reqwest::blocking::Client::new();
    let mut payload = json!({
        "model": settings.summary_api_model,
        "messages": messages,
    });
    if settings.summary_api_url.trim().starts_with("http://localhost:11434") {
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("stream".to_string(), Value::Bool(false));
        }
    }
    if let Some(extra) = extra_payload {
        if let Some(obj) = payload.as_object_mut() {
            if let Some(extra_obj) = extra.as_object() {
                for (key, value) in extra_obj.iter() {
                    obj.insert(key.to_string(), value.clone());
                }
            }
        }
    }

    let mut last_error = None;
    for attempt in 1..=3 {
        let mut request = client.post(&settings.summary_api_url).json(&payload);
        if !settings.summary_api_token.trim().is_empty() {
            request = request.bearer_auth(&settings.summary_api_token);
        }
        match request.send() {
            Ok(response) => {
                let status = response.status();
                let body = response.text().unwrap_or_default();
                let trimmed_body = trim_api_body(&body);
                let data: Result<Value, _> = serde_json::from_str(&body);
                match data {
                    Ok(data) => {
                        if !status.is_success() {
                            last_error = Some(format!("API Error: {status} {trimmed_body}"));
                        } else if let Some(choices) = data.get("choices").and_then(|v| v.as_array()) {
                            if let Some(message) = choices.first().and_then(|value| value.get("message")) {
                                return Ok(extract_message_content(message));
                            }
                        } else if let Some(predictions) = data.get("predictions").and_then(|v| v.as_array()) {
                            if let Some(first) = predictions.first() {
                                if let Some(choices) = first.get("choices") {
                                    return Ok(extract_message_content(choices));
                                }
                                return Ok(extract_message_content(first));
                            }
                        } else {
                            return Ok(extract_message_content(&data));
                        }
                    }
                    Err(err) => {
                        if status.is_success() {
                            last_error = Some(format!("API parse error: {err}; body: {trimmed_body}"));
                        } else {
                            last_error = Some(format!("API Error: {status} {trimmed_body}"));
                        }
                    }
                }
            }
            Err(err) => {
                last_error = Some(format!("API error: {err}"));
            }
        }
        thread::sleep(Duration::from_millis(300 * attempt));
    }
    Err(last_error.unwrap_or_else(|| "API error".to_string()))
}

fn extract_message_content(value: &Value) -> String {
    if let Some(text) = value.as_str() {
        return text.to_string();
    }
    if let Some(message) = value.get("message") {
        return extract_message_content(message);
    }
    if let Some(content) = value.get("content") {
        return extract_message_content(content);
    }
    if let Some(list) = value.as_array() {
        let parts: Vec<String> = list.iter().map(extract_message_content).filter(|item| !item.is_empty()).collect();
        return parts.join("\n");
    }
    if let Some(text) = value.get("text").and_then(|v| v.as_str()) {
        return text.to_string();
    }
    if let Some(summary_text) = value.get("summary_text").and_then(|v| v.as_str()) {
        return summary_text.to_string();
    }
    if let Some(generated) = value.get("generated_text").and_then(|v| v.as_str()) {
        return generated.to_string();
    }
    String::new()
}

fn parse_summary_sections(summary: &str) -> SummaryPayload {
    let mut current = "keypoints";
    let mut keypoints = Vec::new();
    let mut actions = Vec::new();
    let mut issues = Vec::new();

    for line in summary.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lower = trimmed.to_lowercase();
        if lower.contains("key point") || lower.contains("keypoints") {
            current = "keypoints";
            continue;
        }
        if lower.contains("action") {
            current = "actions";
            continue;
        }
        if lower.contains("issue") || lower.contains("risk") {
            current = "issues";
            continue;
        }
        let cleaned = trimmed.trim_start_matches(['-', '*', '•']).trim().to_string();
        if cleaned.is_empty() {
            continue;
        }
        match current {
            "actions" => actions.push(cleaned),
            "issues" => issues.push(cleaned),
            _ => keypoints.push(cleaned),
        }
    }

    if keypoints.is_empty() && !summary.trim().is_empty() {
        keypoints.push(summary.trim().to_string());
    }

    SummaryPayload {
        keypoints,
        actions,
        issues,
        raw: summary.to_string(),
    }
}

fn emit_status(app: &AppHandle, status: &str, message: &str) {
    let _ = app.emit_all(
        "status",
        StatusPayload {
            status: status.to_string(),
            message: message.to_string(),
        },
    );
}

fn append_log(control: &RecordingControl, message: &str) {
    let timestamp = Local::now().format("%Y-%m-%d %H:%M:%S");
    let line = format!("[{timestamp}] {message}\n");
    if let Ok(mut file) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&control.log_path)
    {
        let _ = file.write_all(line.as_bytes());
    }
}

fn append_log_path(path: &Path, message: &str) {
    let timestamp = Local::now().format("%Y-%m-%d %H:%M:%S");
    let line = format!("[{timestamp}] {message}\n");
    if let Ok(mut file) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = file.write_all(line.as_bytes());
    }
}

fn log_path_for_transcript(transcript: &Path) -> PathBuf {
    let stem = transcript
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("meeting");
    transcript.with_file_name(format!("{stem}.log"))
}

fn trim_api_body(body: &str) -> String {
    let trimmed = body.trim();
    let limit = 1200;
    if trimmed.len() <= limit {
        return trimmed.to_string();
    }
    let mut shortened = trimmed.chars().take(limit).collect::<String>();
    shortened.push_str("... (truncated)");
    shortened
}

fn emit_model_download(app: &AppHandle, bytes: u64, total: Option<u64>, done: bool) {
    let percent = total.and_then(|total| {
        if total == 0 {
            None
        } else {
            let raw = (bytes.saturating_mul(100) / total) as u8;
            Some(raw.min(100))
        }
    });
    let _ = app.emit_all(
        "model_download",
        ModelDownloadPayload {
            bytes,
            total,
            percent,
            done,
        },
    );
}

fn load_or_init_settings() -> Result<AppSettings, String> {
    let path = settings_path()?;
    if !path.exists() {
        if let Some(legacy_path) = legacy_settings_path() {
            if legacy_path.exists() {
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)
                        .map_err(|err| format!("Failed to create settings dir: {err}"))?;
                }
                let _ = fs::copy(&legacy_path, &path);
            }
        }
    }
    if !path.exists() {
        let settings = AppSettings::default();
        save_settings_to_disk(&settings)?;
        return Ok(settings);
    }
    ensure_settings_backup(&path)?;
    let content = fs::read_to_string(&path).unwrap_or_default();
    let mut data: Value = serde_json::from_str(&content).unwrap_or_else(|_| json!({}));
    let default_settings = AppSettings::default();
    let default_value = serde_json::to_value(&default_settings).map_err(|err| err.to_string())?;
    merge_values(&mut data, &default_value);
    if let Some(obj) = data.as_object_mut() {
        if let Some(Value::String(output_path)) = obj.get("calls_output_path") {
            if output_path.trim().is_empty() {
                obj.insert(
                    "calls_output_path".to_string(),
                    Value::String(default_settings.calls_output_path.clone()),
                );
            }
        }
        obj.insert("whisper_mode".to_string(), Value::String("local".to_string()));
        obj.insert("whisper_api_url".to_string(), Value::String(String::new()));
        obj.insert("whisper_api_token".to_string(), Value::String(String::new()));
    }
    let content = serde_json::to_string_pretty(&data).map_err(|err| err.to_string())?;
    fs::write(&path, content).map_err(|err| format!("Failed to write settings: {err}"))?;
    let settings: AppSettings = serde_json::from_value(data).map_err(|err| err.to_string())?;
    Ok(settings)
}

fn save_settings_to_disk(settings: &AppSettings) -> Result<(), String> {
    let path = settings_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("Failed to create settings dir: {err}"))?;
    }
    if path.exists() {
        ensure_settings_backup(&path)?;
    }
    let mut data: Value = if path.exists() {
        let content = fs::read_to_string(&path).unwrap_or_default();
        serde_json::from_str(&content).unwrap_or_else(|_| json!({}))
    } else {
        json!({})
    };
    let settings_value = serde_json::to_value(settings).map_err(|err| err.to_string())?;
    merge_override(&mut data, &settings_value);
    let content = serde_json::to_string_pretty(&data).map_err(|err| err.to_string())?;
    fs::write(path, content).map_err(|err| format!("Failed to write settings: {err}"))
}

fn settings_path() -> Result<PathBuf, String> {
    let base = dirs_next::config_dir().ok_or("Failed to resolve config directory")?;
    Ok(base.join("myscribe").join("settings.json"))
}

fn legacy_settings_path() -> Option<PathBuf> {
    let base = dirs_next::config_dir()?;
    Some(base.join("localscribe").join("settings.json"))
}

fn ensure_settings_backup(path: &Path) -> Result<(), String> {
    let backup_path = path.with_extension("json.bak");
    if !backup_path.exists() {
        fs::copy(path, &backup_path).map_err(|err| format!("Failed to backup settings: {err}"))?;
    }
    Ok(())
}

fn model_cache_path() -> Result<PathBuf, String> {
    let base = dirs_next::config_dir().ok_or("Failed to resolve config directory")?;
    Ok(base.join("myscribe").join("models").join(WHISPER_MODEL_FILENAME))
}

fn apply_bundled_whisper_paths(app: &AppHandle, settings: &mut AppSettings) -> Result<bool, String> {
    let bundled_stream = match app.path_resolver().resolve_resource(WHISPER_RESOURCE_STREAM) {
        Some(path) => path,
        None => return Ok(false),
    };
    let bundled_root = bundled_stream.parent().unwrap_or_else(|| Path::new("")).to_path_buf();
    let mut changed = false;

    if !Path::new(&settings.whisper_stream_path).exists() {
        settings.whisper_stream_path = bundled_stream.to_string_lossy().to_string();
        changed = true;
    }
    if !Path::new(&settings.whisper_cpp_path).exists() {
        settings.whisper_cpp_path = bundled_root.to_string_lossy().to_string();
        changed = true;
    }
    if !Path::new(&settings.whisper_model_path).exists() {
        settings.whisper_model_path = model_cache_path()?.to_string_lossy().to_string();
        changed = true;
    }

    Ok(changed)
}

fn ensure_whisper_model(app: &AppHandle, settings: &AppSettings) -> Result<(), String> {
    if settings.whisper_mode == "api" {
        return Ok(());
    }
    let model_path = Path::new(&settings.whisper_model_path);
    if model_path.exists() {
        return Ok(());
    }
    if let Some(parent) = model_path.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("Failed to create model dir: {err}"))?;
    }

    emit_status(app, "info", "Downloading Whisper model...");
    let mut response = reqwest::blocking::get(WHISPER_MODEL_URL)
        .map_err(|err| format!("Failed to download Whisper model: {err}"))?;
    if !response.status().is_success() {
        return Err(format!("Failed to download Whisper model: HTTP {}", response.status()));
    }

    let mut file = fs::File::create(model_path).map_err(|err| format!("Failed to create model file: {err}"))?;
    let total = response.content_length();
    let mut downloaded: u64 = 0;
    let mut last_percent: u8 = 0;
    let mut last_emit_bytes: u64 = 0;
    let mut buffer = [0u8; 262_144];

    emit_model_download(app, downloaded, total, false);
    let download_result: Result<(), String> = (|| {
        loop {
            let read = response.read(&mut buffer).map_err(|err| format!("Failed to read Whisper model: {err}"))?;
            if read == 0 {
                break;
            }
            file.write_all(&buffer[..read])
                .map_err(|err| format!("Failed to write Whisper model: {err}"))?;
            downloaded = downloaded.saturating_add(read as u64);
            if let Some(total) = total {
                if total > 0 {
                    let percent = (downloaded.saturating_mul(100) / total) as u8;
                    if percent != last_percent {
                        last_percent = percent;
                        emit_model_download(app, downloaded, Some(total), false);
                    }
                }
            } else if downloaded.saturating_sub(last_emit_bytes) >= 1_048_576 {
                last_emit_bytes = downloaded;
                emit_model_download(app, downloaded, None, false);
            }
        }
        Ok(())
    })();
    if let Err(err) = download_result {
        let _ = fs::remove_file(model_path);
        return Err(err);
    }
    emit_model_download(app, downloaded, total, true);

    emit_status(app, "info", "Whisper model downloaded.");
    Ok(())
}

fn merge_values(target: &mut Value, defaults: &Value) {
    match (target, defaults) {
        (Value::Object(target_map), Value::Object(default_map)) => {
            for (key, value) in default_map {
                match target_map.get_mut(key) {
                    Some(existing) => merge_values(existing, value),
                    None => {
                        target_map.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        _ => {}
    }
}

fn merge_override(target: &mut Value, updates: &Value) {
    match (target, updates) {
        (Value::Object(target_map), Value::Object(update_map)) => {
            for (key, value) in update_map {
                match target_map.get_mut(key) {
                    Some(existing) => merge_override(existing, value),
                    None => {
                        target_map.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        (target_value, update_value) => {
            *target_value = update_value.clone();
        }
    }
}

fn validate_settings(settings: &AppSettings) -> Vec<String> {
    let mut errors = Vec::new();
    if settings.whisper_mode == "api" && settings.whisper_api_url.trim().is_empty() {
        errors.push("WHISPER_API_URL is required when whisper mode is API".to_string());
    }
    if settings.whisper_mode != "api" {
        let cpp = Path::new(&settings.whisper_cpp_path);
        let stream = Path::new(&settings.whisper_stream_path);
        let model = Path::new(&settings.whisper_model_path);
        if !cpp.exists() {
            errors.push(format!("Whisper.cpp path not found: {}", cpp.display()));
        }
        if !stream.exists() {
            errors.push(format!("Whisper stream executable not found: {}", stream.display()));
        }
        if !model.exists() {
            errors.push(format!("Whisper model not found: {}", model.display()));
        }
    }
    let output_path = Path::new(&settings.calls_output_path);
    if let Err(err) = fs::create_dir_all(output_path) {
        errors.push(format!("Unable to create transcripts folder: {err}"));
    }
    errors
}

fn list_expired_artifacts(settings: &AppSettings) -> Result<Vec<PathBuf>, String> {
    let output_dir = Path::new(&settings.calls_output_path);
    if !output_dir.exists() {
        return Ok(Vec::new());
    }
    let cutoff = SystemTime::now()
        .checked_sub(Duration::from_secs((RETENTION_DAYS as u64) * 24 * 60 * 60))
        .ok_or("Failed to compute retention cutoff")?;
    let mut results = Vec::new();
    let entries = output_dir
        .read_dir()
        .map_err(|err| format!("Failed to read output dir: {err}"))?;
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let ext = path.extension().and_then(|ext| ext.to_str()).unwrap_or("");
        if ext != "txt" && ext != "log" {
            continue;
        }
        if let Ok(metadata) = entry.metadata() {
            if let Ok(modified) = metadata.modified() {
                if modified < cutoff {
                    results.push(path);
                }
            }
        }
    }
    Ok(results)
}

fn sanitize_filename(name: &str) -> String {
    let allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. ";
    let mut sanitized: String = name.replace('/', "").replace('\\', "");
    sanitized.retain(|c| allowed.contains(c));
    let trimmed = sanitized.trim();
    let mut result = trimmed.to_string();
    if result.is_empty() || result.trim_matches('.').is_empty() {
        result = "meeting".to_string();
    }
    if result.len() > 100 {
        result.truncate(100);
    }
    result
}

fn unique_name(output_dir: &Path, base: &str) -> (String, String) {
    let mut index = 1;
    loop {
        let suffix = if index == 1 { String::new() } else { format!("_{index}") };
        let transcript = format!("{base}{suffix}.txt");
        let summary = format!("{base}{suffix}.txt-summarized.txt");
        if !output_dir.join(&transcript).exists() && !output_dir.join(&summary).exists() {
            return (transcript, summary);
        }
        index += 1;
    }
}

fn get_prompt_content(prompt_id: &str) -> String {
    if let Some(dir) = find_prompts_dir() {
        let path = dir.join(format!("{prompt_id}.txt"));
        if path.exists() {
            if let Ok(content) = fs::read_to_string(path) {
                let trimmed = content.trim();
                if !trimmed.is_empty() {
                    return trimmed.to_string();
                }
            }
        }
    }
    "Summarize this meeting transcript with key points, action items, and attendees.".to_string()
}

fn find_prompts_dir() -> Option<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let packaged = manifest_dir.join("..").join("..").join("prompts");
    if packaged.exists() {
        return Some(packaged);
    }
    if let Ok(current_dir) = std::env::current_dir() {
        for ancestor in current_dir.ancestors().take(6) {
            let candidate = ancestor.join("prompts");
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }
    None
}

fn get_meeting_files(settings: &AppSettings) -> Vec<HashMap<String, String>> {
    let mut files = Vec::new();
    let output_dir = Path::new(&settings.calls_output_path);
    if !output_dir.exists() {
        return files;
    }
    if let Ok(read_dir) = output_dir.read_dir() {
        for entry in read_dir.flatten() {
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("txt") {
                continue;
            }
            let file_name = path.file_name().and_then(|name| name.to_str()).unwrap_or("");
            if file_name.ends_with("-summarized.txt") {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(metadata) => metadata,
                Err(_) => continue,
            };
            let modified: DateTime<Local> = metadata
                .modified()
                .map(DateTime::from)
                .unwrap_or_else(|_| Local::now());
            let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
            let parts: Vec<&str> = stem.splitn(2, '_').collect();
            let (date_str, name) = if parts.len() >= 2 { (parts[0], parts[1]) } else { ("unknown", stem) };
            let summary_path = path.with_file_name(format!("{file_name}-summarized.txt"));
            let mut info = HashMap::new();
            info.insert("name".to_string(), name.replace('_', " ").to_string());
            info.insert("date".to_string(), date_str.to_string());
            info.insert("size".to_string(), format_file_size(metadata.len()));
            info.insert("mtime".to_string(), modified.timestamp().to_string());
            info.insert("transcript_path".to_string(), path.to_string_lossy().to_string());
            if summary_path.exists() {
                info.insert("summary_path".to_string(), summary_path.to_string_lossy().to_string());
            }
            files.push(info);
        }
    }
    files.sort_by(|a, b| {
        let a_ts = a.get("mtime").and_then(|val| val.parse::<i64>().ok()).unwrap_or(0);
        let b_ts = b.get("mtime").and_then(|val| val.parse::<i64>().ok()).unwrap_or(0);
        b_ts.cmp(&a_ts)
    });
    for info in files.iter_mut() {
        info.remove("mtime");
    }
    files
}

fn format_file_size(size: u64) -> String {
    if size < 1024 {
        return format!("{size} B");
    }
    if size < 1024 * 1024 {
        return format!("{:.1} KB", size as f64 / 1024.0);
    }
    format!("{:.1} MB", size as f64 / (1024.0 * 1024.0))
}

fn main() {
    tauri::Builder::default()
        .manage(AppState {
            settings: Mutex::new(AppSettings::default()),
            recording: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            ensure_whisper_ready,
            download_whisper_model,
            get_expired_artifacts_summary,
            delete_expired_artifacts,
            list_audio_devices,
            list_prompts,
            list_recordings,
            open_path,
            read_summary_file,
            check_summary_ready,
            ask_question,
            summarize_section,
            suggest_questions,
            re_summarize_recording,
            start_recording,
            stop_recording,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
