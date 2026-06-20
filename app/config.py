from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "sqlite:///./nomos.db"
    app_encryption_key: str = ""
    app_base_url: str = "http://localhost:8000"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    session_secret_key: str = "change-me"

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache
def get_settings() -> Settings:
    return Settings()
