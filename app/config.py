from pydantic_settings import BaseSettings,SettingsConfigDict
class Settings(BaseSettings):
    bot_token:str
    database_url:str
    log_level:str='INFO'
    model_config=SettingsConfigDict(env_file='.env',extra='ignore')
settings=Settings()
