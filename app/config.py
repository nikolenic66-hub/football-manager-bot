from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_example_env() -> dict[str, str]:
    example_path = Path(__file__).resolve().parent.parent / ".env.example"
    if not example_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in example_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://football:football@localhost:5432/football_manager"
    log_level: str = "INFO"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AppSettings(Settings):
    @classmethod
    def from_env(cls) -> "AppSettings":
        env_values = _load_example_env()
        if Path(".env").exists():
            return cls()
        if env_values:
            return cls(**env_values)
        return cls()


settings = AppSettings.from_env()
