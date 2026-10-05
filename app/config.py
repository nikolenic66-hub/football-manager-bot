from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    log_level: str = 'INFO'

    @field_validator('bot_token')
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        value=value.strip()
        if len(value) < 20 or ':' not in value:
            raise ValueError('BOT_TOKEN looks invalid.')
        return value

    @field_validator('database_url')
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        value=value.strip()
        if not value.startswith(('postgresql+asyncpg://','postgresql://')):
            raise ValueError('DATABASE_URL must be a PostgreSQL URL.')
        return value

    model_config=SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)


settings=Settings()
