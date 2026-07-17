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
    store_backend: str = "json"  # "json" (local/CLI) or "postgres" (Neon)

    # Scraping politeness
    fetch_delay_seconds: float = 2.0
    fetch_timeout_seconds: float = 60.0
    headless: bool = True

    # LLM output cap (ChatOpenRouter otherwise requests a huge max_tokens,
    # which OpenRouter rejects against low credit balances)
    llm_max_tokens: int = 16384  # must fit a full tracklist JSON / batch of picks

    # Resolution (Step 2)
    resolve_min_confidence: float = 0.65  # gate for STORING a platform match
    resolve_batch_size: int = 20  # units per LLM pick call
    resolve_candidates_per_platform: int = 5
    resolve_api_delay_seconds: float = 0.3  # politeness between API calls

    # Export (Step 4) — OAuth loopback
    export_redirect_port: int = 8888
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    export_api_delay_seconds: float = 0.2  # between YouTube playlistItems inserts

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

    @property
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"


settings = Settings()
