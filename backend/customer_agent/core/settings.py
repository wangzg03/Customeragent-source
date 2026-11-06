from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("EVAL_DATA_DIR", ROOT / "data"))
    )
    runtime_dir: Path = field(
        default_factory=lambda: Path(os.getenv("EVAL_RUNTIME_DIR", ROOT / "runtime"))
    )
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    )
    api_key: str = field(default_factory=lambda: os.getenv("EVAL_API_KEY", ""))
    environment: str = field(default_factory=lambda: os.getenv("EVAL_ENV", "local"))
    sandbox: str = field(default_factory=lambda: os.getenv("SANDBOX_MODE", "process"))
    sandbox_image: str = field(
        default_factory=lambda: os.getenv("SANDBOX_IMAGE", "customeragent-sandbox:4.5")
    )
    model_timeout: float = 90.0
    tool_timeout: float = 15.0
    job_limit: int = 16
    max_cases: int = 100
    max_turns: int = 24
    episode_timeout: float = 420.0
    max_tokens: int = 65000
    context_chars: int = 18000
    max_stalls: int = 6
    max_body_bytes: int = 65536
    models: tuple[str, ...] = (
        "qwen3.5:4b",
        "qwen3.5:9b",
        "qwen2.5:7b",
        "qwen2.5:7b-instruct",
    )

    def validate(self):
        parsed = urlparse(self.ollama_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "OLLAMA_URL must be an administrator-configured HTTP endpoint"
            )
        if self.sandbox not in {"process", "docker"}:
            raise ValueError("SANDBOX_MODE must be process or docker")
        if self.environment == "production" and (
            len(self.api_key) < 24 or self.sandbox != "docker"
        ):
            raise ValueError(
                "production requires EVAL_API_KEY >=24 characters and SANDBOX_MODE=docker"
            )
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        return self
