import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://marketplace:marketplace@localhost:5432/marketplace"
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me-dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "15"))
    refresh_token_ttl_days: int = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))
    # N from the spec: minimum interval between two CREATE_ORDER (or two UPDATE_ORDER) operations
    order_rate_limit_minutes: int = int(os.getenv("ORDER_RATE_LIMIT_MINUTES", "1"))
    openapi_path: str = os.getenv(
        "OPENAPI_PATH", os.path.join(os.path.dirname(__file__), "..", "openapi", "marketplace.yaml")
    )


settings = Settings()
