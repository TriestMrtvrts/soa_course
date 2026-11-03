"""FlightService gRPC implementation: PostgreSQL (source of truth) + Redis cache-aside."""
import functools
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import grpc
import psycopg
from psycopg import errors as pg_errors
from google.protobuf.timestamp_pb2 import Timestamp
from psycopg_pool import ConnectionPool

from flight.v1 import flight_service_pb2 as pb
from flight.v1 import flight_service_pb2_grpc as pb_grpc

from app.cache import Cache, flight_key, search_key

logger = logging.getLogger("flight.service")

IATA_AIRPORT = re.compile(r"^[A-Z]{3}$")
CURRENCY = "RUB"

FLIGHT_STATUS_TO_PB = {
    "SCHEDULED": pb.FLIGHT_STATUS_SCHEDULED,
    "DEPARTED": pb.FLIGHT_STATUS_DEPARTED,
    "CANCELLED": pb.FLIGHT_STATUS_CANCELLED,
    "COMPLETED": pb.FLIGHT_STATUS_COMPLETED,
}
FLIGHT_STATUS_FROM_PB = {v: k for k, v in FLIGHT_STATUS_TO_PB.items()}
RESERVATION_STATUS_TO_PB = {
    "ACTIVE": pb.RESERVATION_STATUS_ACTIVE,
    "RELEASED": pb.RESERVATION_STATUS_RELEASED,
    "EXPIRED": pb.RESERVATION_STATUS_EXPIRED,
}

FLIGHT_SELECT = """
    SELECT f.id, f.airline_code, al.name AS airline_name, f.flight_number,
           f.origin_code, o.name AS origin_name, o.city AS origin_city,
           f.destination_code, d.name AS destination_name, d.city AS destination_city,
           f.departure_time, f.arrival_time, f.total_seats, f.available_seats, f.price, f.status
    FROM flights f
    JOIN airlines al ON al.iata_code = f.airline_code
    JOIN airports o  ON o.iata_code = f.origin_code
    JOIN airports d  ON d.iata_code = f.destination_code
"""


class ServiceError(Exception):
    def __init__(self, code: grpc.StatusCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def rpc(method):
    """Maps ServiceError / DB errors to gRPC status codes. Raised inside `with conn.transaction()`,
    so the transaction is already rolled back when the status is sent."""

    @functools.wraps(method)
    def wrapper(self, request, context):
        try:
            return method(self, request)
        except ServiceError as exc:
            context.abort(exc.code, exc.message)
        except psycopg.OperationalError as exc:
            logger.error("database unavailable", extra={"fields": {"error": str(exc)}})
            context.abort(grpc.StatusCode.UNAVAILABLE, "database unavailable")

    return wrapper


# ------------------------------------------------------------------ helpers
def _uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, f"{field} must be a UUID")


def _timestamp(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt.astimezone(timezone.utc))
    return ts


def _to_minor(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def flight_to_pb(row: dict) -> pb.Flight:
    return pb.Flight(
        id=str(row["id"]),
        flight_number=f"{row['airline_code']}{row['flight_number']}",
        airline=pb.Airline(iata_code=row["airline_code"], name=row["airline_name"]),
        origin=pb.Airport(iata_code=row["origin_code"], name=row["origin_name"], city=row["origin_city"]),
        destination=pb.Airport(
            iata_code=row["destination_code"], name=row["destination_name"], city=row["destination_city"]
        ),
        departure_time=_timestamp(row["departure_time"]),
        arrival_time=_timestamp(row["arrival_time"]),
        total_seats=row["total_seats"],
        available_seats=row["available_seats"],
        price=pb.Money(amount_minor=_to_minor(row["price"]), currency=CURRENCY),
        status=FLIGHT_STATUS_TO_PB[row["status"]],
    )


def reservation_to_pb(row: dict) -> pb.SeatReservation:
    return pb.SeatReservation(
        id=str(row["id"]),
        booking_id=str(row["booking_id"]),
        flight_id=str(row["flight_id"]),
        seat_count=row["seat_count"],
        status=RESERVATION_STATUS_TO_PB[row["status"]],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


def _flight_cache_keys(row: dict) -> list[str]:
    """Every cached entry that may contain this flight."""
    day = row["departure_time"].astimezone(timezone.utc).date().isoformat()
    return [
        flight_key(str(row["id"])),
        search_key(row["origin_code"], row["destination_code"], day),
        search_key(row["origin_code"], row["destination_code"], None),
    ]


class FlightService(pb_grpc.FlightServiceServicer):
    def __init__(self, pool: ConnectionPool, cache: Cache):
        self.pool = pool
        self.cache = cache

    # -------------------------------------------------------------- queries
    @rpc
    def SearchFlights(self, request: pb.SearchFlightsRequest) -> pb.SearchFlightsResponse:
        origin, destination = request.origin.upper(), request.destination.upper()
        if not IATA_AIRPORT.match(origin) or not IATA_AIRPORT.match(destination):
            raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "origin and destination must be IATA codes like SVO")
        day = None
        if request.HasField("date"):
            try:
                day = date(request.date.year, request.date.month, request.date.day)
            except ValueError:
                raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "date is not a valid calendar date")

        key = search_key(origin, destination, day.isoformat() if day else None)
        cached = self.cache.get(key)
        if cached is not None:
            return pb.SearchFlightsResponse.FromString(cached)

        sql = FLIGHT_SELECT + " WHERE f.origin_code = %s AND f.destination_code = %s AND f.status = 'SCHEDULED'"
        params: list = [origin, destination]
        if day is not None:
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            sql += " AND f.departure_time >= %s AND f.departure_time < %s"
            params += [start, start + timedelta(days=1)]
        sql += " ORDER BY f.departure_time"
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        response = pb.SearchFlightsResponse(flights=[flight_to_pb(row) for row in rows])
        self.cache.set(key, response.SerializeToString())
        return response

    @rpc
    def GetFlight(self, request: pb.GetFlightRequest) -> pb.GetFlightResponse:
        flight_id = _uuid(request.flight_id, "flight_id")
        key = flight_key(flight_id)
        cached = self.cache.get(key)
        if cached is not None:
            return pb.GetFlightResponse(flight=pb.Flight.FromString(cached))

        with self.pool.connection() as conn:
            row = conn.execute(FLIGHT_SELECT + " WHERE f.id = %s", [flight_id]).fetchone()
        if row is None:
            raise ServiceError(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")
        flight = flight_to_pb(row)
        self.cache.set(key, flight.SerializeToString())
        return pb.GetFlightResponse(flight=flight)

    # ------------------------------------------------------------ mutations
    @rpc
    def ReserveSeats(self, request: pb.ReserveSeatsRequest) -> pb.ReserveSeatsResponse:
        flight_id = _uuid(request.flight_id, "flight_id")
        booking_id = _uuid(request.booking_id, "booking_id")
        if request.seat_count <= 0:
            raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be > 0")

        with self.pool.connection() as conn, conn.transaction():
            # Row lock on the flight: concurrent reservations of the same flight are serialized,
            # so the last seat cannot be sold twice.
            flight = conn.execute(FLIGHT_SELECT + " WHERE f.id = %s FOR UPDATE OF f", [flight_id]).fetchone()
            if flight is None:
                raise ServiceError(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")

            existing = conn.execute("SELECT * FROM seat_reservations WHERE booking_id = %s", [booking_id]).fetchone()
            if existing is not None:
                return self._replay(existing, flight, request)

            if flight["status"] != "SCHEDULED":
                raise ServiceError(grpc.StatusCode.FAILED_PRECONDITION, f"flight is {flight['status']}, not SCHEDULED")
            if flight["available_seats"] < request.seat_count:
                raise ServiceError(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"not enough seats: requested {request.seat_count}, available {flight['available_seats']}",
                )
            available = conn.execute(
                "UPDATE flights SET available_seats = available_seats - %s WHERE id = %s RETURNING available_seats",
                [request.seat_count, flight_id],
            ).fetchone()["available_seats"]
            reservation = conn.execute(
                "INSERT INTO seat_reservations (flight_id, booking_id, seat_count) VALUES (%s, %s, %s) RETURNING *",
                [flight_id, booking_id, request.seat_count],
            ).fetchone()

        logger.info("seats reserved", extra={"fields": {
            "booking_id": booking_id, "flight_id": flight_id, "seat_count": request.seat_count, "available": available}})
        self.cache.invalidate(*_flight_cache_keys(flight))  # after commit: cache never outlives the DB change
        return pb.ReserveSeatsResponse(reservation=reservation_to_pb(reservation), available_seats=available)

    @staticmethod
    def _replay(existing: dict, flight: dict, request: pb.ReserveSeatsRequest) -> pb.ReserveSeatsResponse:
        """Idempotency: the same booking_id never produces a second reservation."""
        same = str(existing["flight_id"]) == str(flight["id"]) and existing["seat_count"] == request.seat_count
        if not same:
            raise ServiceError(grpc.StatusCode.ALREADY_EXISTS,
                               "booking_id already has a reservation with different flight_id/seat_count")
        if existing["status"] != "ACTIVE":
            raise ServiceError(grpc.StatusCode.FAILED_PRECONDITION, f"reservation is already {existing['status']}")
        logger.info("idempotent replay of ReserveSeats", extra={"fields": {"booking_id": str(existing["booking_id"])}})
        return pb.ReserveSeatsResponse(reservation=reservation_to_pb(existing), available_seats=flight["available_seats"])

    @rpc
    def ReleaseReservation(self, request: pb.ReleaseReservationRequest) -> pb.ReleaseReservationResponse:
        booking_id = _uuid(request.booking_id, "booking_id")
        with self.pool.connection() as conn, conn.transaction():
            found = conn.execute("SELECT flight_id FROM seat_reservations WHERE booking_id = %s", [booking_id]).fetchone()
            if found is None:
                raise ServiceError(grpc.StatusCode.NOT_FOUND, f"no reservation for booking {booking_id}")
            # same lock order as ReserveSeats (flight, then reservation) -> no deadlocks
            flight = conn.execute(FLIGHT_SELECT + " WHERE f.id = %s FOR UPDATE OF f", [found["flight_id"]]).fetchone()
            reservation = conn.execute(
                "SELECT * FROM seat_reservations WHERE booking_id = %s FOR UPDATE", [booking_id]
            ).fetchone()
            if reservation["status"] != "ACTIVE":
                # idempotent: a retried release must not return the seats twice
                return pb.ReleaseReservationResponse(
                    reservation=reservation_to_pb(reservation), available_seats=flight["available_seats"]
                )
            available = conn.execute(
                "UPDATE flights SET available_seats = available_seats + %s WHERE id = %s RETURNING available_seats",
                [reservation["seat_count"], reservation["flight_id"]],
            ).fetchone()["available_seats"]
            reservation = conn.execute(
                "UPDATE seat_reservations SET status = 'RELEASED' WHERE id = %s RETURNING *", [reservation["id"]]
            ).fetchone()

        logger.info("reservation released", extra={"fields": {"booking_id": booking_id, "available": available}})
        self.cache.invalidate(*_flight_cache_keys(flight))
        return pb.ReleaseReservationResponse(reservation=reservation_to_pb(reservation), available_seats=available)

    @rpc
    def UpdateFlight(self, request: pb.UpdateFlightRequest) -> pb.UpdateFlightResponse:
        flight_id = _uuid(request.flight_id, "flight_id")
        changes: dict = {}
        if request.HasField("price"):
            if request.price.amount_minor <= 0 or request.price.currency not in ("", CURRENCY):
                raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, f"price must be > 0 and in {CURRENCY}")
            changes["price"] = Decimal(request.price.amount_minor) / 100
        if request.HasField("status"):
            if request.status not in FLIGHT_STATUS_FROM_PB:
                raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "status must be specified")
            changes["status"] = FLIGHT_STATUS_FROM_PB[request.status]
        if request.HasField("departure_time"):
            changes["departure_time"] = request.departure_time.ToDatetime(tzinfo=timezone.utc)
        if request.HasField("arrival_time"):
            changes["arrival_time"] = request.arrival_time.ToDatetime(tzinfo=timezone.utc)
        if not changes:
            raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "nothing to update")

        try:
            with self.pool.connection() as conn, conn.transaction():
                before = conn.execute(FLIGHT_SELECT + " WHERE f.id = %s FOR UPDATE OF f", [flight_id]).fetchone()
                if before is None:
                    raise ServiceError(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")
                assignments = ", ".join(f"{column} = %s" for column in changes)  # column names are fixed above
                conn.execute(f"UPDATE flights SET {assignments} WHERE id = %s", [*changes.values(), flight_id])
                after = conn.execute(FLIGHT_SELECT + " WHERE f.id = %s", [flight_id]).fetchone()
        except pg_errors.CheckViolation:
            raise ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "arrival_time must be after departure_time")
        except pg_errors.UniqueViolation:
            raise ServiceError(grpc.StatusCode.ALREADY_EXISTS, "this flight number already exists on that date")

        # the departure date may have changed: drop entries for both the old and the new date
        self.cache.invalidate(*set(_flight_cache_keys(before) + _flight_cache_keys(after)))
        return pb.UpdateFlightResponse(flight=flight_to_pb(after))
