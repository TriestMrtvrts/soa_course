-- Flight Service database (3NF): airlines, airports, flights, seat_reservations.

CREATE TYPE flight_status      AS ENUM ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED');
CREATE TYPE reservation_status AS ENUM ('ACTIVE', 'RELEASED', 'EXPIRED');

CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE airlines (
    iata_code CHAR(2)      PRIMARY KEY CHECK (iata_code ~ '^[A-Z0-9]{2}$'),
    name      VARCHAR(100) NOT NULL
);

CREATE TABLE airports (
    iata_code CHAR(3)      PRIMARY KEY CHECK (iata_code ~ '^[A-Z]{3}$'),
    name      VARCHAR(150) NOT NULL,
    city      VARCHAR(100) NOT NULL
);

CREATE TABLE flights (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    airline_code        CHAR(2)        NOT NULL REFERENCES airlines (iata_code),
    -- numeric part only: the airline prefix lives in airline_code (no redundancy), "SU" + "1234" = SU1234
    flight_number       VARCHAR(4)     NOT NULL CHECK (flight_number ~ '^[0-9]{1,4}$'),
    origin_code         CHAR(3)        NOT NULL REFERENCES airports (iata_code),
    destination_code    CHAR(3)        NOT NULL REFERENCES airports (iata_code),
    departure_time      TIMESTAMPTZ    NOT NULL,
    arrival_time        TIMESTAMPTZ    NOT NULL,
    total_seats         INTEGER        NOT NULL CHECK (total_seats > 0),
    available_seats     INTEGER        NOT NULL CHECK (available_seats >= 0),
    price               NUMERIC(12, 2) NOT NULL CHECK (price > 0),
    status              flight_status  NOT NULL DEFAULT 'SCHEDULED',
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CHECK (available_seats <= total_seats),
    CHECK (origin_code <> destination_code),
    CHECK (arrival_time > departure_time)
);

-- immutable wrapper so the UTC date can be used in an index expression
CREATE FUNCTION utc_date(ts TIMESTAMPTZ) RETURNS DATE AS $$
    SELECT (ts AT TIME ZONE 'UTC')::date
$$ LANGUAGE sql IMMUTABLE;

-- "SU1234 on 2026-04-01" is one flight: the date is derived from departure_time, not stored twice
CREATE UNIQUE INDEX uq_flights_number_date ON flights (airline_code, flight_number, utc_date(departure_time));
CREATE INDEX idx_flights_route_departure ON flights (origin_code, destination_code, departure_time);
CREATE TRIGGER trg_flights_updated_at BEFORE UPDATE ON flights
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE seat_reservations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id  UUID               NOT NULL REFERENCES flights (id),
    -- one reservation per booking; also the idempotency key of ReserveSeats
    booking_id UUID               NOT NULL UNIQUE,
    seat_count INTEGER            NOT NULL CHECK (seat_count > 0),
    status     reservation_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ        NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ        NOT NULL DEFAULT now()
);
CREATE INDEX idx_seat_reservations_flight ON seat_reservations (flight_id);
CREATE TRIGGER trg_seat_reservations_updated_at BEFORE UPDATE ON seat_reservations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
