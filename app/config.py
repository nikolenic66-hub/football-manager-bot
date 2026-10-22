from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    log_level: str = 'INFO'
    admin_id: int | None = None

    @field_validator('bot_token', mode='before')
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError('BOT_TOKEN must be a string.')
        value = value.strip()
        if len(value) < 20 or ':' not in value:
            raise ValueError('BOT_TOKEN looks invalid.')
        return value

    @field_validator('database_url', mode='before')
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError('DATABASE_URL must be a string.')
        value = value.strip()
        # create_async_engine requires an async driver. Managed PostgreSQL
        # providers often expose DATABASE_URL as postgresql:// (or postgres://).
        # Accept those provider URLs but normalize them before SQLAlchemy sees them.
        if value.startswith('postgres://'):
            value = 'postgresql+asyncpg://' + value[len('postgres://'):]
        elif value.startswith('postgresql://'):
            value = 'postgresql+asyncpg://' + value[len('postgresql://'):]
        if not value.startswith('postgresql+asyncpg://'):
            raise ValueError('DATABASE_URL must be a PostgreSQL URL using asyncpg.')
        return value

    model_config=SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)


settings=Settings()
