import base64
import io
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

from app_settings import AppSettings


class MeetingService:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.active_recordings = {}
        self.audio_devices = None
        self.prompts_cache = None

    def update_settings(self, settings: AppSettings):
        self.settings = settings
        self.audio_devices = None
        self.prompts_cache = None

    def get_audio_devices(self):
        if self.audio_devices is not None:
            return self.audio_devices

        try:
            cmd = [str(Path(self.settings.whisper_stream_path)), "-c", "-2"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            time.sleep(1)
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)

            devices = []
            output = stdout + stderr
            for line in output.split("\n"):
                if "Capture device #" in line:
                    parts = line.split("#")
                    if len(parts) > 1:
                        device_part = parts[1]
                        device_id = device_part.split(":")[0].strip()
                        device_name = device_part.split("'")[1] if "'" in device_part else f"Device {device_id}"
                        devices.append({"id": int(device_id), "name": device_name})

            final_devices = [{"id": -1, "name": "System Default"}]
            other_devices = []
            for device in devices:
                if "blackhole" in device["name"].lower():
                    device["name"] = f"{device['name']} (System Audio + Mic - Requires Setup)"
                elif "aggregate" in device["name"].lower():
                    device["name"] = f"{device['name']} (Multi-Input - May Capture Both)"
                other_devices.append(device)
            final_devices.extend(other_devices)

            self.audio_devices = final_devices
            return final_devices
        except Exception:
            return [{"id": -1, "name": "Default Device"}]

    def get_available_prompts(self):
        if self.prompts_cache is not None:
            return self.prompts_cache

        prompts = []
        prompts_dir = Path(__file__).parent / "prompts"
        prompt_files = {
            "meeting": "Executive Meeting",
            "technical": "Technical Review",
            "sales": "Sales Call",
            "standup": "Daily Standup",
            "one_on_one": "1:1 Meeting",
            "staff": "Staff Meeting",
        }

        for file_key, display_name in prompt_files.items():
            prompt_file = prompts_dir / f"{file_key}.txt"
            if prompt_file.exists():
                prompts.append({"id": file_key, "name": display_name, "file": str(prompt_file)})

        self.prompts_cache = prompts
        return prompts

    def get_prompt_content(self, prompt_id):
        prompts = self.get_available_prompts()
        for prompt in prompts:
            if prompt["id"] == prompt_id:
                try:
                    return Path(prompt["file"]).read_text().strip()
                except Exception:
                    break
        fallback = Path(__file__).parent / "prompts" / "meeting.txt"
        if fallback.exists():
            return fallback.read_text().strip()
        return "Summarize this meeting transcript with key points, action items, and attendees."

    def _extract_whisper_text(self, data):
        if isinstance(data, str):
            return data.strip()
        if not isinstance(data, dict):
            return str(data).strip()
        if "predictions" in data and isinstance(data["predictions"], list) and data["predictions"]:
            prediction = data["predictions"][0]
            if isinstance(prediction, str):
                return prediction.strip()
            if isinstance(prediction, list):
                return " ".join(str(item).strip() for item in prediction if str(item).strip())
            if isinstance(prediction, dict):
                if "transcription" in prediction:
                    return str(prediction["transcription"]).strip()
                if "transcript" in prediction:
                    return str(prediction["transcript"]).strip()
                if "text" in prediction:
                    return str(prediction["text"]).strip()
                if "generated_text" in prediction:
                    return str(prediction["generated_text"]).strip()
                if "segments" in prediction and isinstance(prediction["segments"], list):
                    segments = [
                        str(seg.get("text")).strip()
                        for seg in prediction["segments"]
                        if isinstance(seg, dict) and seg.get("text")
                    ]
                    return " ".join(seg for seg in segments if seg)
                return self._extract_whisper_text(prediction)
            return str(prediction).strip()
        if "text" in data:
            return str(data["text"]).strip()
        if "transcription" in data:
            return str(data["transcription"]).strip()
        if "transcript" in data:
            return str(data["transcript"]).strip()
        if "output" in data:
            return str(data["output"]).strip()
        if "choices" in data and data["choices"]:
            message = data["choices"][0].get("message", {})
            return self._extract_message_content(message).strip()
        return ""

    def _call_whisper_api(self, audio_base64):
        headers = {"Content-Type": "application/json"}
        if self.settings.whisper_api_token:
            headers["Authorization"] = f"Bearer {self.settings.whisper_api_token}"

        payload = {"inputs": [audio_base64]}
        response = requests.post(self.settings.whisper_api_url, json=payload, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"API Error: {response.status_code}, {response.text}")
        return response.json()

    def check_whisper_api_ready(self):
        if not self.settings.whisper_api_url:
            raise Exception("WHISPER_API_URL is not configured")
        sample_rate = self.settings.whisper_api_sample_rate
        audio = np.zeros(int(sample_rate * 0.5), dtype=np.float32)
        audio_int16 = (audio * 32767).astype(np.int16)
        byte_io = io.BytesIO()
        from scipy.io import wavfile

        wavfile.write(byte_io, sample_rate, audio_int16)
        encoded_audio = base64.b64encode(byte_io.getvalue()).decode("utf-8")
        self._call_whisper_api(encoded_audio)
        return True

    def start_recording(self, meeting_name, audio_device_id=-1, prompt_type="meeting", audio_device_name=None):
        errors = self.settings.validate_paths()
        if errors:
            raise Exception(f"Configuration errors: {', '.join(errors)}")

        meeting_id = str(uuid.uuid4())
        sanitized_name = AppSettings.sanitize_filename(meeting_name)

        current_date = datetime.now().strftime("%Y-%m-%d")
        base_filename = f"{current_date}_{sanitized_name}"

        def unique_name(base: str):
            candidate_txt = f"{base}.txt"
            candidate_sum = f"{base}.txt-summarized.txt"
            if not Path(candidate_txt).exists() and not Path(candidate_sum).exists():
                return candidate_txt, candidate_sum
            i = 2
            while True:
                candidate_txt = f"{base}_{i}.txt"
                candidate_sum = f"{base}_{i}.txt-summarized.txt"
                if not Path(candidate_txt).exists() and not Path(candidate_sum).exists():
                    return candidate_txt, candidate_sum
                i += 1

        transcript_filename, summary_filename = unique_name(base_filename)

        meeting_record = {
            "id": meeting_id,
            "name": meeting_name,
            "sanitized_name": sanitized_name,
            "audio_device_id": audio_device_id,
            "audio_device_name": audio_device_name,
            "prompt_type": prompt_type,
            "start_time": datetime.now(),
            "transcript_filename": transcript_filename,
            "summary_filename": summary_filename,
            "transcript_path": None,
            "summary_path": None,
            "process": None,
            "whisper_mode": None,
            "stop_event": None,
            "stop_requested": False,
            "status": "starting",
            "callbacks": [],
        }

        self.active_recordings[meeting_id] = meeting_record

        thread = threading.Thread(target=self._run_recording, args=(meeting_id,), daemon=True)
        thread.start()

        return meeting_id

    def stop_recording(self, meeting_id):
        if meeting_id not in self.active_recordings:
            raise Exception("Meeting not found or not active")

        meeting = self.active_recordings[meeting_id]
        meeting["stop_requested"] = True

        if meeting.get("whisper_mode") == "api":
            stop_event = meeting.get("stop_event")
            if stop_event:
                stop_event.set()
                meeting["status"] = "stopping"

        if meeting["process"] and meeting["process"].poll() is None:
            try:
                meeting["process"].send_signal(signal.SIGINT)
                meeting["status"] = "stopping"
                try:
                    meeting["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    meeting["process"].kill()
                    meeting["process"].wait()
            except Exception as exc:
                meeting["status"] = "error"
                raise exc
        return True

    def add_status_callback(self, meeting_id, callback):
        if meeting_id in self.active_recordings:
            self.active_recordings[meeting_id]["callbacks"].append(callback)

    def _run_recording(self, meeting_id):
        meeting = self.active_recordings[meeting_id]

        try:
            if meeting.get("stop_requested"):
                meeting["status"] = "error"
                self._notify_callbacks(meeting_id, "error", "Recording stopped before start")
                return

            use_api = self.settings.whisper_mode == "api" and bool(self.settings.whisper_api_url)
            fallback_reason = None
            if use_api:
                try:
                    self.check_whisper_api_ready()
                except Exception as exc:
                    use_api = False
                    fallback_reason = str(exc)

            if meeting.get("stop_requested"):
                meeting["status"] = "error"
                self._notify_callbacks(meeting_id, "error", "Recording stopped before start")
                return

            if use_api:
                meeting["whisper_mode"] = "api"
                self._run_api_recording(meeting_id)
                return

            meeting["whisper_mode"] = "local"
            meeting["status"] = "recording"
            status_message = "Recording started"
            if fallback_reason:
                status_message = "Recording started (using local transcription; API unavailable)"
            self._notify_callbacks(meeting_id, "recording", status_message)

            cmd = [
                str(Path(self.settings.whisper_stream_path)),
                "-m",
                str(Path(self.settings.whisper_model_path)),
                "-t",
                str(self.settings.whisper_threads),
                "-kc",
                "-tdrz",
            ]

            if meeting["audio_device_id"] != -1:
                cmd.extend(["-c", str(meeting["audio_device_id"])])

            cmd.extend(["-f", meeting["transcript_filename"]])

            meeting["process"] = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            self._stream_transcription(meeting_id)
            meeting["process"].wait()

            if meeting["process"].returncode in (0, -2):
                meeting["status"] = "processing"
                self._notify_callbacks(meeting_id, "processing", "Processing and summarizing...")
                self._process_recording(meeting_id)
            else:
                meeting["status"] = "error"
                self._notify_callbacks(meeting_id, "error", "Recording failed")

        except Exception as exc:
            meeting["status"] = "error"
            self._notify_callbacks(meeting_id, "error", f"Recording error: {exc}")
        finally:
            if meeting.get("process") and meeting["process"].poll() is None:
                try:
                    meeting["process"].terminate()
                    meeting["process"].wait(timeout=5)
                except Exception:
                    pass
            if meeting_id in self.active_recordings and meeting["status"] != "complete":
                meeting["status"] = "error"

    def _stream_transcription(self, meeting_id):
        meeting = self.active_recordings[meeting_id]
        process = meeting["process"]

        def read_output(stream, output_queue, stream_name):
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    output_queue.put((stream_name, line.strip()))
            except Exception:
                pass

        try:
            output_queue = queue.Queue()
            stdout_thread = threading.Thread(target=read_output, args=(process.stdout, output_queue, "stdout"))
            stderr_thread = threading.Thread(target=read_output, args=(process.stderr, output_queue, "stderr"))

            stdout_thread.daemon = True
            stderr_thread.daemon = True
            stdout_thread.start()
            stderr_thread.start()

            while process.poll() is None or not output_queue.empty():
                try:
                    _stream_name, line = output_queue.get(timeout=1)
                    if line:
                        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                        clean_line = ansi_escape.sub("", line).replace("[2K", "").strip()

                        skip_patterns = [
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
                        ]

                        should_skip = any(pattern in clean_line for pattern in skip_patterns)
                        is_meaningful = (
                            clean_line
                            and not should_skip
                            and clean_line.strip()
                            and not clean_line.isspace()
                            and clean_line not in {".", "..", "..."}
                            and len(clean_line.strip()) > 1
                            and not clean_line.strip().replace(".", "").strip() == ""
                        )

                        if is_meaningful:
                            last_transcription = meeting.get("_last_transcription", "")
                            if last_transcription != clean_line:
                                meeting["_last_transcription"] = clean_line
                                for callback in meeting.get("callbacks", []):
                                    try:
                                        callback(meeting_id, "transcription", clean_line)
                                    except Exception:
                                        pass
                except queue.Empty:
                    continue
                except Exception:
                    break
        except Exception:
            pass

    def _run_api_recording(self, meeting_id):
        meeting = self.active_recordings[meeting_id]
        stop_event = threading.Event()
        meeting["stop_event"] = stop_event
        meeting["status"] = "recording"
        self._notify_callbacks(meeting_id, "recording", "Recording started (API)")

        audio_queue = queue.Queue()
        sample_rate = self.settings.whisper_api_sample_rate
        chunk_duration = self.settings.whisper_api_chunk_duration
        device_id = meeting["audio_device_id"]
        device_name = meeting.get("audio_device_name")
        transcript_path = Path(meeting["transcript_filename"])

        def record_audio():
            import sounddevice as sd

            try:
                stream_device = None if device_id == -1 else device_id
                if device_name:
                    try:
                        devices = sd.query_devices()
                        matches = [
                            idx
                            for idx, dev in enumerate(devices)
                            if isinstance(dev.get("name"), str)
                            and device_name.lower() in dev["name"].lower()
                            and dev.get("max_input_channels", 0) > 0
                        ]
                        if matches:
                            stream_device = matches[0]
                    except Exception:
                        pass
                try:
                    device_info = sd.query_devices(stream_device, "input")
                    max_channels = int(device_info.get("max_input_channels", 0))
                except Exception:
                    max_channels = 1

                if max_channels < 1:
                    raise Exception("Selected device has no input channels")
                channels = 1 if max_channels >= 1 else max_channels
                try:
                    default_in = sd.query_devices(None, "input")
                    default_channels = int(default_in.get("max_input_channels", 1))
                except Exception:
                    default_channels = 1

                def callback(indata, frames, time_info, status):
                    if status:
                        print(status)
                    audio_queue.put(indata.copy())

                stream_channels = channels
                stream_device_choice = stream_device
                try:
                    with sd.InputStream(
                        device=stream_device_choice,
                        samplerate=sample_rate,
                        channels=stream_channels,
                        callback=callback,
                        blocksize=int(sample_rate * chunk_duration),
                    ):
                        while not stop_event.is_set():
                            time.sleep(0.1)
                except Exception:
                    stream_device_choice = None
                    stream_channels = 1 if default_channels >= 1 else channels
                    with sd.InputStream(
                        device=stream_device_choice,
                        samplerate=sample_rate,
                        channels=stream_channels,
                        callback=callback,
                        blocksize=int(sample_rate * chunk_duration),
                    ):
                        while not stop_event.is_set():
                            time.sleep(0.1)
            except Exception as exc:
                meeting["status"] = "error"
                stop_event.set()
                self._notify_callbacks(meeting_id, "error", f"Recording error: {exc}")

        def transcription_worker():
            with open(transcript_path, "a") as transcript_file:
                while not stop_event.is_set() or not audio_queue.empty():
                    try:
                        audio_data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    try:
                        from scipy.io import wavfile

                        if audio_data.ndim > 1:
                            audio_data = np.mean(audio_data, axis=1)
                        audio_int16 = (audio_data * 32767).astype(np.int16)
                        byte_io = io.BytesIO()
                        wavfile.write(byte_io, sample_rate, audio_int16)
                        encoded_audio = base64.b64encode(byte_io.getvalue()).decode("utf-8")

                        response = self._call_whisper_api(encoded_audio)
                        transcript_text = self._extract_whisper_text(response)

                        if transcript_text:
                            last_transcription = meeting.get("_last_transcription", "")
                            if last_transcription != transcript_text:
                                meeting["_last_transcription"] = transcript_text
                                transcript_file.write(transcript_text + "\n")
                                transcript_file.flush()
                                for callback in meeting.get("callbacks", []):
                                    try:
                                        callback(meeting_id, "transcription", transcript_text)
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    time.sleep(1)

        record_thread = threading.Thread(target=record_audio, daemon=True)
        worker_thread = threading.Thread(target=transcription_worker, daemon=True)
        record_thread.start()
        worker_thread.start()

        while not stop_event.is_set():
            time.sleep(0.2)

        record_thread.join(timeout=5)
        worker_thread.join(timeout=5)

        if meeting["status"] == "error":
            return

        meeting["status"] = "processing"
        self._notify_callbacks(meeting_id, "processing", "Processing and summarizing...")
        self._process_recording(meeting_id)

    def _process_recording(self, meeting_id):
        meeting = self.active_recordings[meeting_id]
        try:
            transcript_path = Path(meeting["transcript_filename"])
            if transcript_path.exists():
                self._summarize_with_prompt(meeting_id, transcript_path)
                self._move_files(meeting_id)
                meeting["status"] = "complete"
                self._notify_callbacks(meeting_id, "complete", "Meeting processing complete")
            else:
                meeting["status"] = "error"
                self._notify_callbacks(meeting_id, "error", "Transcript file not found")
        except Exception as exc:
            meeting["status"] = "error"
            self._notify_callbacks(meeting_id, "error", f"Processing error: {exc}")

    def _summarize_with_prompt(self, meeting_id, transcript_path):
        meeting = self.active_recordings[meeting_id]
        prompt_content = self.get_prompt_content(meeting["prompt_type"])
        transcript_text = Path(transcript_path).read_text()
        summary = self._call_summarization_api(transcript_text, prompt_content)
        summary_path = Path(meeting["summary_filename"])
        summary_path.write_text(summary)

    def _call_summarization_api(self, transcript_text, prompt_content):
        messages = [
            {"role": "system", "content": prompt_content},
            {"role": "user", "content": f"Please summarize the following meeting transcript accordingly:\n\n{transcript_text}"},
        ]
        return self._call_chat_api(messages)

    def _extract_message_content(self, message):
        if isinstance(message, str):
            return message
        if not isinstance(message, dict):
            return str(message)
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    parts.append(str(item))
                    continue
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                if "summary_text" in item and isinstance(item["summary_text"], str):
                    parts.append(item["summary_text"])
                if "summary" in item and isinstance(item["summary"], list):
                    for summary in item["summary"]:
                        if isinstance(summary, dict) and isinstance(summary.get("summary_text"), str):
                            parts.append(summary["summary_text"])
            return "\n".join([part for part in parts if part])
        return str(content) if content is not None else ""

    def _call_chat_api(self, messages, extra_payload=None):
        url = self.settings.summary_api_url
        headers = {"Content-Type": "application/json"}
        if self.settings.summary_api_token:
            headers["Authorization"] = f"Bearer {self.settings.summary_api_token}"

        payload = {"model": self.settings.summary_api_model, "messages": messages}
        if extra_payload:
            payload.update(extra_payload)

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "choices" in data:
                message = data["choices"][0].get("message", {})
                return self._extract_message_content(message)
            if isinstance(data, dict) and "predictions" in data:
                prediction = data["predictions"][0]
                if isinstance(prediction, dict) and "choices" in prediction:
                    message = prediction["choices"][0].get("message", {})
                    return self._extract_message_content(message)
                if isinstance(prediction, dict) and "generated_text" in prediction:
                    return prediction["generated_text"]
                return self._extract_message_content(prediction)
            return self._extract_message_content(data)
        raise Exception(f"API Error: {response.status_code}, {response.text}")

    def summarize_text(self, transcript_text, prompt_content):
        if not transcript_text.strip():
            raise ValueError("Transcript text is empty")
        if not prompt_content.strip():
            raise ValueError("Prompt content is empty")
        return self._call_summarization_api(transcript_text, prompt_content)

    def ask_question(self, transcript_text, question):
        if not transcript_text.strip():
            raise ValueError("Transcript text is empty")
        if not question.strip():
            raise ValueError("Question is empty")

        system_prompt = (
            "You are a meeting assistant. Answer the user's question using only the meeting transcript. "
            "If the answer is not in the transcript, say you don't know. Keep the response concise."
        )
        user_prompt = f"Transcript:\n{transcript_text}\n\nQuestion: {question}"
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        return self._call_chat_api(messages)

    def check_summary_ready(self):
        messages = [
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": "Respond with OK."},
        ]
        self._call_chat_api(messages, extra_payload={"max_tokens": 4})
        return True

    def _move_files(self, meeting_id):
        meeting = self.active_recordings[meeting_id]
        output_dir = Path(self.settings.calls_output_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        transcript_src = Path(meeting["transcript_filename"])
        if transcript_src.exists():
            transcript_dst = output_dir / meeting["transcript_filename"]
            transcript_src.rename(transcript_dst)
            meeting["transcript_path"] = str(transcript_dst)

        summary_src = Path(meeting["summary_filename"])
        if summary_src.exists():
            summary_dst = output_dir / meeting["summary_filename"]
            summary_src.rename(summary_dst)
            meeting["summary_path"] = str(summary_dst)

    def _notify_callbacks(self, meeting_id, status, message):
        meeting = self.active_recordings.get(meeting_id)
        if meeting and "callbacks" in meeting:
            for callback in meeting["callbacks"]:
                try:
                    callback(meeting_id, status, message)
                except Exception:
                    pass

    def get_meeting_files(self):
        files = []
        output_dir = Path(self.settings.calls_output_path)
        if not output_dir.exists():
            return files

        today = datetime.now().date()
        for transcript_file in output_dir.glob("*.txt"):
            if transcript_file.name.endswith("-summarized.txt"):
                continue
            mtime = datetime.fromtimestamp(transcript_file.stat().st_mtime).date()
            if mtime != today:
                continue

            parts = transcript_file.stem.split("_", 1)
            if len(parts) >= 2:
                date_str = parts[0]
                name = parts[1]
            else:
                date_str = "unknown"
                name = transcript_file.stem

            summary_file = transcript_file.parent / f"{transcript_file.name}-summarized.txt"
            file_info = {
                "name": name.replace("_", " ").title(),
                "date": date_str,
                "mtime": transcript_file.stat().st_mtime,
                "size": self._format_file_size(transcript_file.stat().st_size),
                "transcript_path": str(transcript_file),
                "summary_path": str(summary_file) if summary_file.exists() else None,
            }
            files.append(file_info)

        files.sort(key=lambda x: x["mtime"], reverse=True)
        for file_info in files:
            file_info.pop("mtime", None)
        return files

    @staticmethod
    def _format_file_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"
