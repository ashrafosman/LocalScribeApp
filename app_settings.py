import json
from dataclasses import dataclass, asdict
from pathlib import Path
import os


DEFAULT_SUMMARY_API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_SUMMARY_MODEL = "sonar"


def _default_calls_output():
    return Path.home() / "Documents" / "Work" / "calls"


def _default_whisper_cpp():
    return Path.home() / "Documents" / "Work" / "whisper.cpp"


def _default_whisper_model():
    return _default_whisper_cpp() / "models" / "ggml-small.en-tdrz.bin"


@dataclass
class AppSettings:
    calls_output_path: str = str(_default_calls_output())
    summary_api_url: str = DEFAULT_SUMMARY_API_URL
    summary_api_model: str = DEFAULT_SUMMARY_MODEL
    summary_api_token: str = ""
    whisper_mode: str = "local"
    whisper_api_url: str = ""
    whisper_api_token: str = ""
    whisper_api_sample_rate: int = 16000
    whisper_api_chunk_duration: int = 3
    whisper_cpp_path: str = str(_default_whisper_cpp())
    whisper_stream_path: str = str(_default_whisper_cpp() / "stream")
    whisper_model_path: str = str(_default_whisper_model())
    whisper_threads: int = 8

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except Exception:
            return cls()
        return cls(**{**asdict(cls()), **data})

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    def validate_paths(self):
        errors = []

        if self.summary_api_url.startswith("https://api.perplexity.ai") and not self.summary_api_token:
            errors.append("SUMMARY_API_TOKEN is required for the Perplexity endpoint")

        if self.whisper_mode == "api" and not self.whisper_api_url:
            errors.append("WHISPER_API_URL is required when whisper mode is API")

        if self.whisper_mode != "api":
            whisper_cpp_path = Path(self.whisper_cpp_path)
            whisper_stream_path = Path(self.whisper_stream_path)
            whisper_model_path = Path(self.whisper_model_path)

            if not whisper_cpp_path.exists():
                errors.append(f"Whisper.cpp path not found: {whisper_cpp_path}")
            if not whisper_stream_path.exists():
                errors.append(f"Whisper stream executable not found: {whisper_stream_path}")
            if not whisper_model_path.exists():
                errors.append(f"Whisper model not found: {whisper_model_path}")

        calls_output_path = Path(self.calls_output_path)
        calls_output_path.mkdir(parents=True, exist_ok=True)

        return errors

    @staticmethod
    def sanitize_filename(filename: str):
        allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. "
        max_length = 100

        sanitized = filename.replace("/", "").replace("\\", "")
        sanitized = "".join(c for c in sanitized if c in allowed_chars)
        sanitized = sanitized[:max_length]
        if not sanitized or sanitized.strip(".") == "":
            sanitized = "meeting"
        return sanitized.strip()


def settings_path():
    return Path(__file__).parent / "data" / "settings.json"
