"""Booking Service REST API."""
import logging
import math
import time
import uuid
from datetime import timezone
from typing import Annotated
from uuid import UUID

import grpc
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from flight.v1 import flight_service_pb2 as pb

from app import config, flight_client
from app.logging_setup import configure_logging
from app.resilience import CircuitOpenError
from app.schemas import Booking, BookingCreate, BookingStatus, FlightOut, FlightSearchParams, UserId, money

configure_logging()
logger = logging.getLogger("booking.api")

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=True)
app = FastAPI(title="Booking Service", version="1.0.0")


# ------------------------------------------------------------------- errors
class ApiError(Exception):
    def __init__(self, status: int, error_code: str, message: str, details: dict | None = None,
                 headers: dict | None = None):
        super().__init__(message)
        self.status, self.error_code, self.message, self.details, self.headers = status, error_code, message, details, headers


# gRPC status of Flight Service -> HTTP answer of Booking Service
GRPC_TO_HTTP = {
    grpc.StatusCode.NOT_FOUND: (404, "FLIGHT_NOT_FOUND"),
    grpc.StatusCode.RESOURCE_EXHAUSTED: (409, "NOT_ENOUGH_SEATS"),
    grpc.StatusCode.FAILED_PRECONDITION: (409, "FLIGHT_NOT_AVAILABLE"),
    grpc.StatusCode.ALREADY_EXISTS: (409, "RESERVATION_CONFLICT"),
    grpc.StatusCode.INVALID_ARGUMENT: (400, "VALIDATION_ERROR"),
    grpc.StatusCode.UNAUTHENTICATED: (502, "UPSTREAM_AUTH_FAILED"),
    grpc.StatusCode.UNAVAILABLE: (503, "FLIGHT_SERVICE_UNAVAILABLE"),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, "FLIGHT_SERVICE_TIMEOUT"),
}


def from_grpc(exc: grpc.RpcError) -> ApiError:
    status, code = GRPC_TO_HTTP.get(exc.code(), (502, "UPSTREAM_ERROR"))
    return ApiError(status, code, exc.details() or exc.code().name, {"grpc_code": exc.code().name})


def circuit_open(exc: CircuitOpenError) -> ApiError:
    retry_after = max(1, math.ceil(exc.retry_after))
    return ApiError(503, "CIRCUIT_OPEN", "Flight Service is unavailable, circuit breaker is open",
                    {"retry_after_seconds": retry_after}, {"Retry-After": str(retry_after)})


def error_body(status: int, error_code: str, message: str, details=None, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"error_code": error_code, "message": message, "details": details})


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return error_body(exc.status, exc.error_code, exc.message, exc.details, exc.headers)


@app.exception_handler(grpc.RpcError)
async def _grpc_error(_: Request, exc: grpc.RpcError):
    err = from_grpc(exc)
    return error_body(err.status, err.error_code, err.message, err.details)


@app.exception_handler(CircuitOpenError)
async def _circuit_open(_: Request, exc: CircuitOpenError):
    err = circuit_open(exc)
    return error_body(err.status, err.error_code, err.message, err.details, err.headers)


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
    return error_body(400, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})


@app.exception_handler(StarletteHTTPException)
async def _http(_: Request, exc: StarletteHTTPException):
    return error_body(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail))


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra={"fields": {"request_id": request_id}})
        response = error_body(500, "INTERNAL_ERROR", "Internal server error")
    response.headers["X-Request-Id"] = request_id
    logger.info("request", extra={"fields": {
        "request_id": request_id, "method": request.method, "endpoint": request.url.path,
        "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}})
    return response


# ------------------------------------------------------------------ mappers
def flight_out(flight: pb.Flight) -> FlightOut:
    return FlightOut(
        id=flight.id,
        flight_number=flight.flight_number,
        airline={"iata_code": flight.airline.iata_code, "name": flight.airline.name},
        origin={"iata_code": flight.origin.iata_code, "name": flight.origin.name, "city": flight.origin.city},
        destination={"iata_code": flight.destination.iata_code, "name": flight.destination.name,
                     "city": flight.destination.city},
        departure_time=flight.departure_time.ToDatetime(tzinfo=timezone.utc),
        arrival_time=flight.arrival_time.ToDatetime(tzinfo=timezone.utc),
        total_seats=flight.total_seats,
        available_seats=flight.available_seats,
        price=float(money(flight.price.amount_minor)),
        currency=flight.price.currency,
        status=pb.FlightStatus.Name(flight.status).removeprefix("FLIGHT_STATUS_"),
    )


def booking_out(row: dict) -> Booking:
    return Booking(**{**row, "total_price": float(row["total_price"])})


# ------------------------------------------------------------------- health
@app.get("/health")
def health():
    return {"status": "ok", "flight_service_circuit": flight_client.breaker.state.value}


# ------------------------------------------------------------------ flights
@app.get("/flights", response_model=list[FlightOut])
def search_flights(params: Annotated[FlightSearchParams, Query()]):
    flights = flight_client.search_flights(params.origin.upper(), params.destination.upper(), params.date)
    return [flight_out(f) for f in flights]


@app.get("/flights/{flight_id}", response_model=FlightOut)
def get_flight(flight_id: UUID):
    return flight_out(flight_client.get_flight(str(flight_id)))


# ----------------------------------------------------------------- bookings
@app.post("/bookings", response_model=Booking, status_code=201)
def create_booking(body: BookingCreate):
    booking_id = str(uuid.uuid4())  # known before ReserveSeats -> retries of ReserveSeats are idempotent

    # 1. flight info (price)
    flight = flight_client.get_flight(str(body.flight_id))

    # 2. reserve seats (atomic check + reserve on Flight Service side)
    try:
        flight_client.reserve_seats(str(body.flight_id), body.seat_count, booking_id)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            # the reservation may have been created although we never saw the answer
            _compensate(booking_id, "reserve timed out")
        raise

    # 3. price snapshot, 4. persist
    total_price = money(flight.price.amount_minor) * body.seat_count
    try:
        with pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO bookings (id, user_id, flight_id, passenger_name, passenger_email, seat_count, total_price)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                [booking_id, body.user_id, body.flight_id, body.passenger_name, body.passenger_email,
                 body.seat_count, total_price],
            ).fetchone()
    except Exception:
        # no partially applied changes: seats must not stay reserved for a booking that does not exist
        _compensate(booking_id, "booking insert failed")
        raise
    logger.info("booking created", extra={"fields": {"booking_id": booking_id, "total_price": str(total_price)}})
    return booking_out(row)


def _compensate(booking_id: str, reason: str) -> None:
    try:
        flight_client.release_reservation(booking_id)
        logger.warning("reservation compensated", extra={"fields": {"booking_id": booking_id, "reason": reason}})
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.NOT_FOUND:
            logger.error("compensation failed", extra={"fields": {
                "booking_id": booking_id, "reason": reason, "code": exc.code().name}})
    except CircuitOpenError:
        logger.error("compensation skipped: circuit open", extra={"fields": {"booking_id": booking_id}})


@app.get("/bookings/{booking_id}", response_model=Booking)
def get_booking(booking_id: UUID):
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = %s", [booking_id]).fetchone()
    if row is None:
        raise ApiError(404, "BOOKING_NOT_FOUND", "Booking not found", {"booking_id": str(booking_id)})
    return booking_out(row)


@app.get("/bookings", response_model=list[Booking])
def list_bookings(user_id: Annotated[UserId, Query()]):
    with pool.connection() as conn:
        rows = conn.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY created_at DESC", [user_id]).fetchall()
    return [booking_out(r) for r in rows]


@app.post("/bookings/{booking_id}/cancel", response_model=Booking)
def cancel_booking(booking_id: UUID):
    with pool.connection() as conn, conn.transaction():
        # row lock: two concurrent cancels of one booking are serialized
        row = conn.execute("SELECT * FROM bookings WHERE id = %s FOR UPDATE", [booking_id]).fetchone()
        if row is None:
            raise ApiError(404, "BOOKING_NOT_FOUND", "Booking not found", {"booking_id": str(booking_id)})
        if row["status"] != BookingStatus.CONFIRMED.value:
            raise ApiError(409, "INVALID_BOOKING_STATUS", f"Booking is {row['status']}, only CONFIRMED can be cancelled",
                           {"status": row["status"]})
        # If Flight Service fails here, the exception rolls the transaction back: the booking stays CONFIRMED.
        # ReleaseReservation is idempotent, so a repeated cancel is safe.
        try:
            flight_client.release_reservation(str(booking_id))
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.NOT_FOUND:
                raise
            logger.warning("no reservation to release", extra={"fields": {"booking_id": str(booking_id)}})
        row = conn.execute(
            "UPDATE bookings SET status = 'CANCELLED' WHERE id = %s RETURNING *", [booking_id]
        ).fetchone()
    return booking_out(row)
