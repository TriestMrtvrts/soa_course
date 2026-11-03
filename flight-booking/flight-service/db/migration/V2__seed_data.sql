-- Demo data. Fixed UUIDs make the flights easy to reference during the demo.
INSERT INTO airlines (iata_code, name) VALUES
    ('SU', 'Aeroflot'),
    ('S7', 'S7 Airlines'),
    ('DP', 'Pobeda');

INSERT INTO airports (iata_code, name, city) VALUES
    ('SVO', 'Sheremetyevo', 'Moscow'),
    ('VKO', 'Vnukovo', 'Moscow'),
    ('DME', 'Domodedovo', 'Moscow'),
    ('LED', 'Pulkovo', 'Saint Petersburg'),
    ('KZN', 'Kazan International', 'Kazan'),
    ('AER', 'Sochi International', 'Sochi');

INSERT INTO flights (id, airline_code, flight_number, origin_code, destination_code,
                     departure_time, arrival_time, total_seats, available_seats, price, status) VALUES
    ('11111111-1111-1111-1111-111111111111', 'SU', '1234', 'SVO', 'LED',
     '2026-12-01 07:00+00', '2026-12-01 08:30+00', 150, 150, 5500.00, 'SCHEDULED'),
    ('22222222-2222-2222-2222-222222222222', 'SU', '1236', 'SVO', 'LED',
     '2026-12-01 15:00+00', '2026-12-01 16:30+00', 150, 150, 6200.00, 'SCHEDULED'),
    ('33333333-3333-3333-3333-333333333333', 'S7', '0042', 'SVO', 'LED',
     '2026-12-02 09:00+00', '2026-12-02 10:30+00', 120, 120, 4900.00, 'SCHEDULED'),
    -- one seat left: used to demonstrate the "last seat" race condition
    ('44444444-4444-4444-4444-444444444444', 'DP', '0101', 'VKO', 'LED',
     '2026-12-01 10:00+00', '2026-12-01 11:30+00', 180, 1, 3200.00, 'SCHEDULED'),
    -- cancelled: never returned by SearchFlights, cannot be booked
    ('55555555-5555-5555-5555-555555555555', 'SU', '1238', 'SVO', 'LED',
     '2026-12-01 20:00+00', '2026-12-01 21:30+00', 150, 150, 5100.00, 'CANCELLED'),
    ('66666666-6666-6666-6666-666666666666', 'S7', '1001', 'DME', 'AER',
     '2026-12-05 06:00+00', '2026-12-05 09:50+00', 200, 200, 8900.00, 'SCHEDULED'),
    ('77777777-7777-7777-7777-777777777777', 'DP', '0330', 'VKO', 'KZN',
     '2026-12-03 12:00+00', '2026-12-03 13:40+00', 180, 180, 2900.00, 'SCHEDULED');
