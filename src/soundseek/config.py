"""Application settings, loaded from environment / .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOUNDSEEK_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (GOOGLE_API_KEY is read directly by langchain-google-genai)
    llm_model: str = "gemini-2.5-flash"

    # Storage layout
    data_dir: Path = PROJECT_ROOT / "data"

    # Scraping politeness
    fetch_delay_seconds: float = 2.0
    fetch_timeout_seconds: float = 60.0
    headless: bool = True

    @property
    def raw_html_dir(self) -> Path:
        return self.data_dir / "raw_html"

    @property
    def setlists_dir(self) -> Path:
        return self.data_dir / "setlists"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser_profile"


settings = Settings()
