import os


def _sentinels(raw: str) -> list[tuple[str, int]]:
    result = []
    for item in raw.split(","):
        host, _, port = item.strip().rpartition(":")
        result.append((host, int(port)))
    return result


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://flight:flight@localhost:5432/flight")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
GRPC_WORKERS = int(os.getenv("GRPC_WORKERS", "16"))

# Comma-separated list of accepted API keys (several keys allow rotation without downtime).
API_KEYS = {key.strip() for key in os.getenv("FLIGHT_SERVICE_API_KEYS", "").split(",") if key.strip()}

REDIS_SENTINELS = _sentinels(os.getenv("REDIS_SENTINELS", "localhost:26379"))
REDIS_MASTER_NAME = os.getenv("REDIS_MASTER_NAME", "mymaster")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
