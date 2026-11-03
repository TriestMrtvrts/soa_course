"""Direct gRPC checks of Flight Service (auth, error codes, idempotency, cache invalidation).

Runs inside the booking-service container, which already has grpcio and the generated stubs:
    docker compose exec -T booking-service python - < e2e/grpc_checks.py
"""
import os
import sys
import uuid

import grpc

from flight.v1 import flight_service_pb2 as pb
from flight.v1 import flight_service_pb2_grpc as pb_grpc

ADDR = os.environ["FLIGHT_SERVICE_ADDR"]
KEY = os.environ["FLIGHT_SERVICE_API_KEY"]
FLIGHT = "77777777-7777-7777-7777-777777777777"  # VKO -> KZN, 180 seats
stub = pb_grpc.FlightServiceStub(grpc.insecure_channel(ADDR))
auth = [("x-api-key", KEY)]
passed = 0


def ok(name, condition, extra=""):
    global passed
    print(f"[{'OK ' if condition else 'FAIL'}] {name} {extra}")
    if not condition:
        sys.exit(1)
    passed += 1


def code_of(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return grpc.StatusCode.OK
    except grpc.RpcError as exc:
        return exc.code()


# ------------------------------------------------------------- auth
ok("no api key -> UNAUTHENTICATED",
   code_of(stub.GetFlight, pb.GetFlightRequest(flight_id=FLIGHT)) == grpc.StatusCode.UNAUTHENTICATED)
ok("wrong api key -> UNAUTHENTICATED",
   code_of(stub.GetFlight, pb.GetFlightRequest(flight_id=FLIGHT), metadata=[("x-api-key", "wrong")])
   == grpc.StatusCode.UNAUTHENTICATED)
ok("no api key on ReserveSeats -> UNAUTHENTICATED",
   code_of(stub.ReserveSeats, pb.ReserveSeatsRequest(flight_id=FLIGHT, seat_count=1, booking_id=str(uuid.uuid4())))
   == grpc.StatusCode.UNAUTHENTICATED)

# ------------------------------------------------------- error codes
ok("unknown flight -> NOT_FOUND",
   code_of(stub.GetFlight, pb.GetFlightRequest(flight_id=str(uuid.uuid4())), metadata=auth) == grpc.StatusCode.NOT_FOUND)
ok("bad uuid -> INVALID_ARGUMENT",
   code_of(stub.GetFlight, pb.GetFlightRequest(flight_id="nope"), metadata=auth) == grpc.StatusCode.INVALID_ARGUMENT)
ok("bad IATA -> INVALID_ARGUMENT",
   code_of(stub.SearchFlights, pb.SearchFlightsRequest(origin="MOSCOW", destination="LED"), metadata=auth)
   == grpc.StatusCode.INVALID_ARGUMENT)
ok("too many seats -> RESOURCE_EXHAUSTED",
   code_of(stub.ReserveSeats, pb.ReserveSeatsRequest(flight_id=FLIGHT, seat_count=1000, booking_id=str(uuid.uuid4())),
           metadata=auth) == grpc.StatusCode.RESOURCE_EXHAUSTED)
ok("cancelled flight -> FAILED_PRECONDITION",
   code_of(stub.ReserveSeats, pb.ReserveSeatsRequest(flight_id="55555555-5555-5555-5555-555555555555", seat_count=1,
                                                      booking_id=str(uuid.uuid4())), metadata=auth)
   == grpc.StatusCode.FAILED_PRECONDITION)
ok("release unknown booking -> NOT_FOUND",
   code_of(stub.ReleaseReservation, pb.ReleaseReservationRequest(booking_id=str(uuid.uuid4())), metadata=auth)
   == grpc.StatusCode.NOT_FOUND)

# ------------------------------------------------------- idempotency
seats_before = stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth).flight.available_seats
booking_id = str(uuid.uuid4())
request = pb.ReserveSeatsRequest(flight_id=FLIGHT, seat_count=2, booking_id=booking_id)
first = stub.ReserveSeats(request, metadata=auth)
second = stub.ReserveSeats(request, metadata=auth)  # what a retry after a lost response looks like
ok("ReserveSeats replay returns the same reservation", first.reservation.id == second.reservation.id)
seats_after = stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth).flight.available_seats
ok("seats decremented only once", seats_after == seats_before - 2, f"({seats_before} -> {seats_after})")
ok("same booking_id, other seat_count -> ALREADY_EXISTS",
   code_of(stub.ReserveSeats, pb.ReserveSeatsRequest(flight_id=FLIGHT, seat_count=3, booking_id=booking_id),
           metadata=auth) == grpc.StatusCode.ALREADY_EXISTS)

released = stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=booking_id), metadata=auth)
again = stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=booking_id), metadata=auth)
ok("release -> RELEASED", released.reservation.status == pb.RESERVATION_STATUS_RELEASED)
ok("repeated release does not return seats twice", again.available_seats == released.available_seats == seats_before)
ok("reserve after release -> FAILED_PRECONDITION",
   code_of(stub.ReserveSeats, request, metadata=auth) == grpc.StatusCode.FAILED_PRECONDITION)

# ------------------------------------------- cache invalidation on UpdateFlight
original = stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth).flight.price.amount_minor
stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth)  # now surely cached
stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=FLIGHT, price=pb.Money(amount_minor=original + 10000, currency="RUB")),
                  metadata=auth)
fresh = stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth).flight.price.amount_minor
ok("GetFlight after UpdateFlight returns new price (cache invalidated)", fresh == original + 10000)
search = stub.SearchFlights(pb.SearchFlightsRequest(origin="VKO", destination="KZN"), metadata=auth)
ok("SearchFlights after UpdateFlight returns new price", search.flights[0].price.amount_minor == original + 10000)
stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=FLIGHT, price=pb.Money(amount_minor=original, currency="RUB")),
                  metadata=auth)
ok("UpdateFlight bad times -> INVALID_ARGUMENT",
   code_of(stub.UpdateFlight, pb.UpdateFlightRequest(
       flight_id=FLIGHT, arrival_time=stub.GetFlight(pb.GetFlightRequest(flight_id=FLIGHT), metadata=auth).flight.departure_time),
       metadata=auth) == grpc.StatusCode.INVALID_ARGUMENT)

print(f"\nAll {passed} gRPC checks passed")
