"""gRPC client of Flight Service with resilience interceptors."""
import grpc

from flight.v1 import flight_service_pb2 as pb
from flight.v1 import flight_service_pb2_grpc as pb_grpc

from app import config
from app.resilience import (
    ApiKeyInterceptor,
    CircuitBreaker,
    CircuitBreakerInterceptor,
    RetryInterceptor,
)

breaker = CircuitBreaker(
    "flight-service",
    failure_threshold=config.CB_FAILURE_THRESHOLD,
    window_seconds=config.CB_WINDOW_SECONDS,
    open_timeout_seconds=config.CB_OPEN_TIMEOUT_SECONDS,
)

_channel = grpc.intercept_channel(
    grpc.insecure_channel(config.FLIGHT_SERVICE_ADDR, options=[
        # reconnect quickly once Flight Service is back (default backoff grows up to 120s)
        ("grpc.initial_reconnect_backoff_ms", 200),
        ("grpc.min_reconnect_backoff_ms", 200),
        ("grpc.max_reconnect_backoff_ms", 2000),
    ]),
    CircuitBreakerInterceptor(breaker),  # outermost
    RetryInterceptor(config.RETRY_MAX_ATTEMPTS, config.RETRY_BASE_DELAY_MS),
    ApiKeyInterceptor(config.FLIGHT_SERVICE_API_KEY),
)
_stub = pb_grpc.FlightServiceStub(_channel)
_timeout = config.GRPC_TIMEOUT_SECONDS


def search_flights(origin: str, destination: str, day=None) -> list[pb.Flight]:
    request = pb.SearchFlightsRequest(origin=origin, destination=destination)
    if day is not None:
        request.date.CopyFrom(pb.Date(year=day.year, month=day.month, day=day.day))
    return list(_stub.SearchFlights(request, timeout=_timeout).flights)


def get_flight(flight_id: str) -> pb.Flight:
    return _stub.GetFlight(pb.GetFlightRequest(flight_id=flight_id), timeout=_timeout).flight


def reserve_seats(flight_id: str, seat_count: int, booking_id: str) -> pb.ReserveSeatsResponse:
    # safe to retry: booking_id is the idempotency key on the Flight Service side
    request = pb.ReserveSeatsRequest(flight_id=flight_id, seat_count=seat_count, booking_id=booking_id)
    return _stub.ReserveSeats(request, timeout=_timeout)


def release_reservation(booking_id: str) -> pb.ReleaseReservationResponse:
    return _stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=booking_id), timeout=_timeout)
