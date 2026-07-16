"""Application settings, loaded from environment / .env."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Expose non-prefixed vars (OPENROUTER_API_KEY) to libraries that read os.environ.
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOUNDSEEK_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM via OpenRouter (OPENROUTER_API_KEY is read directly by langchain-openrouter)
    llm_model: str = "google/gemma-4-31b-it:free"

    # Storage layout
    data_dir: Path = PROJECT_ROOT / "data"

    # Scraping politeness
    fetch_delay_seconds: float = 2.0
    fetch_timeout_seconds: float = 60.0
    headless: bool = True

    # Resolution (Step 2)
    resolve_min_confidence: float = 0.75  # gate for STORING a platform match
    resolve_agent_band: float = 0.45  # scores in [band, min) are ambiguous -> agent
    agent_max_iterations: int = 8
    resolve_save_every: int = 5  # persist setlist every N resolved tracks
    resolve_api_delay_seconds: float = 0.3  # politeness between API calls

    @property
    def raw_html_dir(self) -> Path:
        return self.data_dir / "raw_html"

    @property
    def setlists_dir(self) -> Path:
        return self.data_dir / "setlists"

    @property
    def llm_inputs_dir(self) -> Path:
        return self.data_dir / "llm_inputs"

    @property
    def tracks_path(self) -> Path:
        return self.data_dir / "tracks.json"

    @property
    def resolution_logs_dir(self) -> Path:
        return self.data_dir / "resolution_logs"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser_profile"


settings = Settings()
