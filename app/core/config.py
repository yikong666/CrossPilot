from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    embedding_model: str = "BAAI/bge-m3"
    api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
