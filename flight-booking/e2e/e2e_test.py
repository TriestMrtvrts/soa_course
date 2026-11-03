"""End-to-end checks through Booking Service REST (stdlib only). Run from flight-booking/:

    python3 e2e/e2e_test.py               # functional scenarios + last-seat race
    python3 e2e/e2e_test.py --resilience  # + retry, circuit breaker (stops flight-service) and Redis failover
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

B = "http://localhost:8081"
SU1234 = "11111111-1111-1111-1111-111111111111"
LAST_SEAT = "44444444-4444-4444-4444-444444444444"
CANCELLED = "55555555-5555-5555-5555-555555555555"
passed = 0


def call(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read()), err.headers


def check(name, method, path, body=None, status=200, code=None):
    global passed
    got, payload, headers = call(method, path, body)
    good = got == status and (code is None or payload.get("error_code") == code)
    print(f"[{'OK ' if good else 'FAIL'}] {name}: {got} {payload.get('error_code', '') if isinstance(payload, dict) else ''}")
    if not good:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(1)
    passed += 1
    return payload, headers


def booking(flight_id=SU1234, seats=1, user="e2e-user"):
    return {"user_id": user, "flight_id": flight_id, "passenger_name": "Ivan Petrov",
            "passenger_email": "ivan@example.com", "seat_count": seats}


def compose(*args):
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True).stdout


# ------------------------------------------------------------------ flights
flights, _ = check("search SVO->LED on 2026-12-01", "GET", "/flights?origin=SVO&destination=LED&date=2026-12-01")
assert [f["flight_number"] for f in flights] == ["SU1234", "SU1236"], flights  # cancelled SU1238 not listed
flights, _ = check("search SVO->LED any date", "GET", "/flights?origin=svo&destination=led")
assert all(f["status"] == "SCHEDULED" for f in flights) and len(flights) == 3
check("search without destination", "GET", "/flights?origin=SVO", status=400, code="VALIDATION_ERROR")
check("search bad date", "GET", "/flights?origin=SVO&destination=LED&date=2026-13-01", status=400, code="VALIDATION_ERROR")
flight, _ = check("get flight", "GET", f"/flights/{SU1234}")
check("get unknown flight", "GET", f"/flights/{uuid.uuid4()}", status=404, code="FLIGHT_NOT_FOUND")

# ----------------------------------------------------------------- bookings
user = f"user-{uuid.uuid4().hex[:6]}"
seats_before = flight["available_seats"]
created, _ = check("create booking (3 seats)", "POST", "/bookings", booking(seats=3, user=user), status=201)
assert created["status"] == "CONFIRMED" and created["total_price"] == 3 * flight["price"], created
after, _ = check("seats reserved in Flight Service", "GET", f"/flights/{SU1234}")
assert after["available_seats"] == seats_before - 3, (seats_before, after["available_seats"])
check("get booking", "GET", f"/bookings/{created['id']}")
check("get unknown booking", "GET", f"/bookings/{uuid.uuid4()}", status=404, code="BOOKING_NOT_FOUND")
mine, _ = check("list user bookings", "GET", f"/bookings?user_id={user}")
assert [b["id"] for b in mine] == [created["id"]]
check("list without user_id", "GET", "/bookings", status=400, code="VALIDATION_ERROR")

check("booking: unknown flight", "POST", "/bookings", booking(flight_id=str(uuid.uuid4())), status=404, code="FLIGHT_NOT_FOUND")
check("booking: cancelled flight", "POST", "/bookings", booking(flight_id=CANCELLED), status=409, code="FLIGHT_NOT_AVAILABLE")
check("booking: not enough seats", "POST", "/bookings", booking(seats=50, flight_id=LAST_SEAT), status=409, code="NOT_ENOUGH_SEATS")
check("booking: seat_count 0", "POST", "/bookings", booking(seats=0), status=400, code="VALIDATION_ERROR")
check("booking: bad email", "POST", "/bookings", {**booking(), "passenger_email": "nope"}, status=400, code="VALIDATION_ERROR")
check("booking: empty name", "POST", "/bookings", {**booking(), "passenger_name": "  "}, status=400, code="VALIDATION_ERROR")
check("booking: bad flight_id", "POST", "/bookings", {**booking(), "flight_id": "x"}, status=400, code="VALIDATION_ERROR")

cancelled, _ = check("cancel booking", "POST", f"/bookings/{created['id']}/cancel")
assert cancelled["status"] == "CANCELLED"
restored, _ = check("seats returned", "GET", f"/flights/{SU1234}")
assert restored["available_seats"] == seats_before
check("cancel twice", "POST", f"/bookings/{created['id']}/cancel", status=409, code="INVALID_BOOKING_STATUS")

# ---------------------------------------------- race for the last seat (SELECT FOR UPDATE)
with ThreadPoolExecutor(max_workers=10) as pool:
    results = list(pool.map(lambda _: call("POST", "/bookings", booking(flight_id=LAST_SEAT, user="race")), range(10)))
winners = [payload for status, payload, _ in results if status == 201]
losers = [payload["error_code"] for status, payload, _ in results if status != 201]
print(f"[{'OK ' if len(winners) == 1 else 'FAIL'}] 10 parallel bookings of the last seat: "
      f"{len(winners)} created, losers: {sorted(set(losers))}")
assert len(winners) == 1 and set(losers) == {"NOT_ENOUGH_SEATS"}
passed += 1
last, _ = check("last-seat flight has 0 seats", "GET", f"/flights/{LAST_SEAT}")
assert last["available_seats"] == 0
check("cancel the winner (restores the seat for re-runs)", "POST", f"/bookings/{winners[0]['id']}/cancel")

# ---------------------------------------------------------------- resilience
if "--resilience" in sys.argv:
    print("\n--- Flight Service down: retry + circuit breaker")
    compose("stop", "flight-service")
    started = time.perf_counter()
    check("flight-service down -> 503 after retries", "POST", "/bookings", booking(), status=503, code="FLIGHT_SERVICE_UNAVAILABLE")
    logs = compose("logs", "booking-service", "--since", "20s")
    retries = logs.count("retrying gRPC call")
    print(f"     retry log lines: {retries}, took {time.perf_counter() - started:.2f}s")
    assert retries >= 2
    for i in range(4):  # 5th failure in the window opens the circuit
        call("GET", f"/flights/{SU1234}")
    started = time.perf_counter()
    _, headers = check("circuit OPEN -> immediate 503", "GET", f"/flights/{SU1234}", status=503, code="CIRCUIT_OPEN")
    elapsed = time.perf_counter() - started
    print(f"     answered in {elapsed * 1000:.0f} ms, Retry-After: {headers.get('Retry-After')}")
    assert elapsed < 0.5
    health = call("GET", "/health")[1]
    assert health["flight_service_circuit"] == "OPEN", health

    compose("start", "flight-service")
    timeout = int(compose("exec", "-T", "booking-service", "printenv", "CB_OPEN_TIMEOUT_SECONDS").strip() or 15)
    print(f"     flight-service restarted, waiting {timeout + 2}s for OPEN -> HALF_OPEN")
    time.sleep(timeout + 2)
    check("HALF_OPEN trial succeeds -> CLOSED", "GET", f"/flights/{SU1234}")
    assert call("GET", "/health")[1]["flight_service_circuit"] == "CLOSED"
    transitions = [line.split('"message": "')[1].split('"')[0] for line in compose("logs", "booking-service").splitlines()
                   if "circuit breaker" in line and "->" in line]
    print("     transitions:", transitions[-3:])
    assert transitions[-3:] == ["circuit breaker CLOSED -> OPEN", "circuit breaker OPEN -> HALF_OPEN",
                                "circuit breaker HALF_OPEN -> CLOSED"]
    passed += 1

    print("\n--- Redis master down: Sentinel failover")
    sentinel = ["exec", "-T", "redis-sentinel-1", "redis-cli", "-p", "26379", "sentinel", "get-master-addr-by-name", "mymaster"]
    print("     master before:", compose(*sentinel).split())
    compose("stop", "redis-master")
    check("Redis master down -> service still answers (DB fallback)", "GET", f"/flights/{SU1234}")
    for _ in range(30):
        time.sleep(1)
        master = compose(*sentinel).split()
        if master and master[0] != "redis-master":
            break
    print("     master after failover:", master)
    assert master[0] == "redis-replica"
    check("after failover: booking works", "POST", "/bookings", booking(user="failover"), status=201)
    call("GET", f"/flights/{SU1234}")
    call("GET", f"/flights/{SU1234}")
    hits = compose("logs", "flight-service", "--since", "5s").count('"cache": "hit"')
    print(f"     cache hits on the new master: {hits}")
    assert hits >= 1
    passed += 1
    compose("start", "redis-master")  # comes back as a replica of the new master

print(f"\nAll {passed} checks passed")
