"""gRPC client interceptors: API key, retry with exponential backoff, circuit breaker.

They wrap the channel (see flight_client.py), so business code only sees a plain stub.
Order (outermost first): CircuitBreaker -> Retry -> ApiKey -> network.
One logical call = one breaker outcome, however many retry attempts it took.
"""
import collections
import logging
import threading
import time
from enum import Enum

import grpc

logger = logging.getLogger("booking.resilience")

RETRYABLE_CODES = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
# Business errors (NOT_FOUND, RESOURCE_EXHAUSTED, ...) mean the service is healthy: they don't trip the breaker.
BREAKER_FAILURE_CODES = {
    grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.INTERNAL, grpc.StatusCode.UNKNOWN,
}


class _CallDetails(
    collections.namedtuple("_CallDetails", ("method", "timeout", "metadata", "credentials", "wait_for_ready", "compression")),
    grpc.ClientCallDetails,
):
    pass


class ApiKeyInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, api_key: str):
        self._metadata = ("x-api-key", api_key)

    def intercept_unary_unary(self, continuation, details, request):
        metadata = list(details.metadata or []) + [self._metadata]
        new_details = _CallDetails(details.method, details.timeout, metadata, details.credentials,
                                   details.wait_for_ready, details.compression)
        return continuation(new_details, request)


class RetryInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, max_attempts: int, base_delay_ms: int):
        self.max_attempts = max_attempts
        self.base_delay = base_delay_ms / 1000

    def intercept_unary_unary(self, continuation, details, request):
        for attempt in range(1, self.max_attempts + 1):
            call = continuation(details, request)
            code = call.code()
            if code not in RETRYABLE_CODES or attempt == self.max_attempts:
                if code in RETRYABLE_CODES:
                    logger.error("retries exhausted", extra={"fields": {
                        "method": details.method, "attempts": attempt, "code": code.name}})
                return call
            delay = self.base_delay * 2 ** (attempt - 1)
            logger.warning("retrying gRPC call", extra={"fields": {
                "method": details.method, "attempt": attempt, "code": code.name, "backoff_ms": int(delay * 1000)}})
            time.sleep(delay)


class State(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, retry_after: float):
        super().__init__("circuit breaker is open")
        self.retry_after = retry_after


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int, window_seconds: float, open_timeout_seconds: float):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window = window_seconds
        self.open_timeout = open_timeout_seconds
        self.state = State.CLOSED
        self._failures: collections.deque[float] = collections.deque()
        self._opened_at = 0.0
        self._trial_in_flight = False
        self._lock = threading.Lock()

    def _transition(self, new_state: State, reason: str) -> None:
        logger.warning(f"circuit breaker {self.state.value} -> {new_state.value}", extra={"fields": {
            "breaker": self.name, "from": self.state.value, "to": new_state.value, "reason": reason}})
        self.state = new_state

    def before_call(self) -> None:
        with self._lock:
            if self.state == State.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed < self.open_timeout:
                    raise CircuitOpenError(self.open_timeout - elapsed)
                self._transition(State.HALF_OPEN, f"open timeout {self.open_timeout}s elapsed")
            if self.state == State.HALF_OPEN:
                if self._trial_in_flight:  # exactly one probe request at a time
                    raise CircuitOpenError(1)
                self._trial_in_flight = True

    def on_success(self) -> None:
        with self._lock:
            self._failures.clear()
            if self.state == State.HALF_OPEN:
                self._trial_in_flight = False
                self._transition(State.CLOSED, "trial request succeeded")

    def on_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self.state == State.HALF_OPEN:
                self._trial_in_flight = False
                self._opened_at = now
                self._transition(State.OPEN, "trial request failed")
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window:
                self._failures.popleft()
            if self.state == State.CLOSED and len(self._failures) >= self.failure_threshold:
                self._failures.clear()
                self._opened_at = now
                self._transition(State.OPEN, f"{self.failure_threshold} failures within {self.window}s")


class CircuitBreakerInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    def intercept_unary_unary(self, continuation, details, request):
        self.breaker.before_call()  # raises CircuitOpenError without touching the network
        try:
            call = continuation(details, request)
            failed = call.code() in BREAKER_FAILURE_CODES
        except Exception:
            self.breaker.on_failure()
            raise
        if failed:
            self.breaker.on_failure()
        else:
            self.breaker.on_success()
        return call
