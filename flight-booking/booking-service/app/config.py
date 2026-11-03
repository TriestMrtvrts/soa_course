import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://booking:booking@localhost:5432/booking")

FLIGHT_SERVICE_ADDR = os.getenv("FLIGHT_SERVICE_ADDR", "localhost:50051")
FLIGHT_SERVICE_API_KEY = os.getenv("FLIGHT_SERVICE_API_KEY", "")
GRPC_TIMEOUT_SECONDS = float(os.getenv("GRPC_TIMEOUT_SECONDS", "2"))

# Retry: attempts in total (first call included), backoff = base * 2^(attempt-1): 100ms, 200ms, ...
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY_MS = int(os.getenv("RETRY_BASE_DELAY_MS", "100"))

# Circuit breaker: OPEN after FAILURE_THRESHOLD failures within WINDOW_SECONDS,
# HALF_OPEN after OPEN_TIMEOUT_SECONDS.
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_WINDOW_SECONDS = float(os.getenv("CB_WINDOW_SECONDS", "30"))
CB_OPEN_TIMEOUT_SECONDS = float(os.getenv("CB_OPEN_TIMEOUT_SECONDS", "15"))
