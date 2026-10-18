"""Settings from environment variables and an optional .env file (stdlib only)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader. Real environment variables win over the file."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    def __init__(self) -> None:
        load_dotenv()
        self.anthropic_key = _env("ANTHROPIC_API_KEY")
        self.model = _env("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        # Chalk AI Router: Claude calls go through Chalk (budgets, fallbacks, usage per key)
        self.chalk_router_key = _env("CHALK_ROUTER_API_KEY")
        self.chalk_env_id = _env("CHALK_ENV_ID")
        self.chalk_api_host = _env("CHALK_API_HOST", "https://api.chalk.ai").rstrip("/")
        # HackHarvard: Chalk gives every team a key for its own environment with OpenAI models.
        self.chalk_model = _env("CHALK_MODEL") or "openai/gpt-6.1-sol"
        style = _env("CHALK_API_STYLE").lower()
        self.chalk_api_style = style if style in {"openai", "anthropic"} else (
            "anthropic" if self.chalk_model.startswith(("anthropic/", "claude")) else "openai")
        has_chalk = bool(self.chalk_router_key and self.chalk_env_id)
        mode = _env("LLM_MODE").lower()
        if mode not in {"anthropic", "chalk", "mock"}:
            mode = "chalk" if has_chalk else "anthropic" if self.anthropic_key else "mock"
        # Never pick a provider whose credentials are missing: fall through to the next one, then the mock.
        if mode == "chalk" and not has_chalk:
            mode = "anthropic" if self.anthropic_key else "mock"
        if mode == "anthropic" and not self.anthropic_key:
            mode = "mock"
        self.llm_mode = mode

        self.photon_key = _env("PHOTON_API_KEY")
        self.photon_project = _env("PHOTON_PROJECT_ID")
        self.photon_platform = _env("PHOTON_PLATFORM", "imessage")
        self.photon_origin = _env("PHOTON_ORIGIN", "https://api.photon.codes").rstrip("/")
        self.photon_poll_seconds = float(_env("PHOTON_POLL_SECONDS", "2") or 2)
        self.photon_thread_replies = _env("PHOTON_THREAD_REPLIES", "0") == "1"

        self.db_path = Path(_env("DB_PATH", str(ROOT / "data" / "trialmate.db")))
        self.host = _env("HOST", "127.0.0.1")
        self.port = int(_env("PORT", "8000") or 8000)
        self.tz = _env("STUDY_TZ", "America/New_York")
        # medical = hold replies on medical turns (default); all = hold every reply; none = never hold
        self.approval_mode = _env("APPROVAL_MODE", "medical").lower()
        self.dashboard_token = _env("DASHBOARD_TOKEN")

    @property
    def photon_enabled(self) -> bool:
        return bool(self.photon_key and self.photon_project)
