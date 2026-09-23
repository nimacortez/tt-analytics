from app.config import settings
from app.providers.base import DataProvider


def get_provider() -> DataProvider:
    if settings.data_provider == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()
    raise ValueError(f"Unknown provider '{settings.data_provider}' (BetsAPI client not built yet)")
