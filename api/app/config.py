from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    jwt_secret: str = "change_me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 48

    database_url: str = ""
    redis_url: str = "redis://redis:6379/0"

    navidrome_url: str = "http://navidrome:4533"
    navidrome_username: str = "admin"
    navidrome_password: str = "admin"

    worker_tagger_url: str = "http://tagger:8093"
    mcp_url: str = "http://mcp:8090"
    library_dir: str = "/data/library"

    openai_api_base: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"
    omni_model: str = "mimo-v2-omni"
    embedding_api_url: str = "http://embedding:8000/v1/embeddings"

    icm_partner_key: str = ""
    icm_default_region: str = "us"
    icm_fallback_region: str = "ru"

    stream_auto_acquire_threshold: int = 3

    class Config:
        env_file = ".env"
        # Ignore env vars that aren't declared here. Without this, a stray var
        # in the environment (e.g. shared DATA_ROOT / POSTGRES_PASSWORD) makes
        # pydantic raise on startup and the whole API fails to boot.
        extra = "ignore"


settings = Settings()
