from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads from environment variables / .env. Field names map to UPPER_CASE env vars."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://tt:tt@localhost:5432/tt"
    data_provider: str = "mock"  # "mock" now, "betsapi" once the key arrives
    betsapi_token: str | None = None


settings = Settings()
