# ДЗ №3: Flight Booking (gRPC + Redis)

```
Client (REST :8081) → Booking Service ──gRPC :50051 + x-api-key──→ Flight Service
                            ↓                                           ↓
                      booking-db (PG)                    flight-db (PG) + Redis Sentinel
                                                         (master + replica + 3 sentinels)
```

Стек: Python 3.12, grpcio + protoc (`grpcio-tools`), FastAPI, psycopg 3, Flyway, redis-py (Sentinel).

## Запуск

```bash
cd flight-booking
docker compose up --build                   # БД → Flyway-миграции → Redis Sentinel → flight-service → booking-service
python3 e2e/e2e_test.py                     # REST-сценарии + гонка за последнее место
python3 e2e/e2e_test.py --resilience        # + retry, circuit breaker, failover Redis (останавливает контейнеры)
docker compose exec -T booking-service python - < e2e/grpc_checks.py   # прямые gRPC-проверки: auth, коды, идемпотентность, кеш
docker compose down -v
```

Кодогенерация из `.proto` одной командой: `make proto` (или `sh scripts/generate_proto.sh`). В Docker она выполняется
в отдельной build-стадии; сгенерированные `*/gen/` лежат в `.gitignore`.

Параметры передаются через переменные окружения (значения по умолчанию заданы в `docker-compose.yml`, переопределяются через `.env`):
`FLIGHT_SERVICE_API_KEY`, `CACHE_TTL_SECONDS`, `GRPC_TIMEOUT_SECONDS`, `RETRY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY_MS`,
`CB_FAILURE_THRESHOLD`, `CB_WINDOW_SECONDS`, `CB_OPEN_TIMEOUT_SECONDS`.

## Структура

```
flight-booking/
├── proto/flight/v1/flight_service.proto   # контракт Flight Service
├── scripts/generate_proto.sh              # protoc → */gen
├── docs/er-diagram.md                     # ER-диаграмма (Mermaid) + обоснование 3NF
├── flight-service/
│   ├── app/service.py                     # SearchFlights, GetFlight, ReserveSeats, ReleaseReservation, UpdateFlight
│   ├── app/cache.py                       # cache-aside, Redis Sentinel
│   ├── app/interceptors.py                # проверка x-api-key (UNAUTHENTICATED), логирование вызовов
│   └── db/migration/                      # V1 схема, V2 тестовые рейсы
├── booking-service/
│   ├── app/main.py                        # REST API
│   ├── app/flight_client.py               # gRPC-канал с цепочкой interceptor'ов
│   ├── app/resilience.py                  # CircuitBreaker → Retry → ApiKey interceptors
│   └── db/migration/
└── e2e/                                   # e2e_test.py (REST), grpc_checks.py (gRPC)
```

## Booking Service REST API

| Метод | Путь | Ответы |
|---|---|---|
| GET | `/flights?origin=SVO&destination=LED&date=2026-12-01` | 200 список SCHEDULED-рейсов; 400 |
| GET | `/flights/{id}` | 200; 404 `FLIGHT_NOT_FOUND` |
| POST | `/bookings` `{user_id, flight_id, passenger_name, passenger_email, seat_count}` | 201; 400; 404 `FLIGHT_NOT_FOUND`; 409 `NOT_ENOUGH_SEATS` / `FLIGHT_NOT_AVAILABLE` |
| GET | `/bookings/{id}` | 200; 404 `BOOKING_NOT_FOUND` |
| GET | `/bookings?user_id=X` | 200 |
| POST | `/bookings/{id}/cancel` | 200; 404; 409 `INVALID_BOOKING_STATUS` |
| GET | `/health` | состояние circuit breaker |

Ошибки возвращаются в формате `{error_code, message, details}`. Если Flight Service недоступен: 503 `FLIGHT_SERVICE_UNAVAILABLE`
(после всех retry), 504 `FLIGHT_SERVICE_TIMEOUT`, 503 `CIRCUIT_OPEN` с заголовком `Retry-After` (мгновенно, без вызова).
Swagger UI: http://localhost:8081/docs.

Тестовые рейсы (фиксированные id): `1111…` SU1234 SVO→LED, `2222…` SU1236, `3333…` S7 0042, `4444…` DP0101 VKO→LED
с **1 местом** (для гонки), `5555…` SU1238 **CANCELLED**, `6666…` DME→AER, `7777…` VKO→KZN.

## Как выполнены пункты

| # | Реализация |
|---|---|
| 1 | `proto/flight/v1/flight_service.proto`: бизнес-методы, а не CRUD (`ReserveSeats` атомарно проверяет и резервирует); `google.protobuf.Timestamp`; enum'ы `FlightStatus`, `ReservationStatus`; `Money` в копейках; коды ошибок описаны в контракте и используются: `NOT_FOUND`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`, `ALREADY_EXISTS`, `INVALID_ARGUMENT`, `UNAUTHENTICATED`, `UNAVAILABLE` |
| 2 | [docs/er-diagram.md](docs/er-diagram.md); CHECK-ограничения: `total_seats > 0`, `0 <= available_seats <= total_seats`, `price > 0`, `seat_count > 0`, `arrival > departure`, `origin <> destination` |
| 3 | Два PostgreSQL, Flyway-контейнер на каждый сервис; REST в Booking, gRPC-сервер во Flight |
| 4 | `POST /bookings`: `GetFlight` → `ReserveSeats` → `total_price = seat_count × price` → INSERT `CONFIRMED`. Ошибка резервирования — бронирование не создаётся |
| 5 | Flight: `SELECT … FOR UPDATE OF f` на строку рейса, UPDATE счётчика + INSERT резервации в одной транзакции; Release — в одной транзакции в том же порядке блокировок (без дедлоков). Booking: `booking_id` генерируется заранее; если INSERT упал, выполняется компенсация (`ReleaseReservation`); при таймауте ReserveSeats резервация тоже освобождается; отмена держит `FOR UPDATE` на бронировании и откатывается, если Release не удался |
| 6 | API key в metadata `x-api-key`; серверный interceptor проверяет все методы `FlightService` → `UNAUTHENTICATED`; ключи из env `FLIGHT_SERVICE_API_KEYS` (можно перечислить несколько для ротации). Health и reflection открыты для healthcheck/grpcurl |
| 7 | Cache-aside: `flight:{id}`, `search:{origin}:{destination}:{date\|any}`, TTL 300 с у всех ключей; после коммита ReserveSeats/ReleaseReservation/UpdateFlight удаляются `flight:{id}` и оба search-ключа маршрута (для UpdateFlight — и для старой, и для новой даты). Логи `cache hit` / `cache miss` / `cache invalidated`. Если Redis недоступен — запрос идёт в БД |
| 8 | `RetryInterceptor`: 3 попытки, паузы 100 → 200 мс, повтор только для `UNAVAILABLE`/`DEADLINE_EXCEEDED`. ReserveSeats идемпотентен по `booking_id` (UNIQUE + повтор возвращает ту же резервацию); ReleaseReservation тоже идемпотентен |
| 9 | Redis Sentinel: master + replica + 3 sentinel (кворум 2); клиент `redis.sentinel.Sentinel.master_for` сам находит нового мастера после failover |
| 10 | `CircuitBreakerInterceptor` (внешний interceptor канала): CLOSED → OPEN после `CB_FAILURE_THRESHOLD` ошибок за `CB_WINDOW_SECONDS`; OPEN → HALF_OPEN через `CB_OPEN_TIMEOUT_SECONDS`; в HALF_OPEN пропускается ровно один пробный запрос. Бизнес-ошибки (NOT_FOUND, RESOURCE_EXHAUSTED) не считаются отказами. Переходы пишутся в лог, в OPEN клиент получает 503 `CIRCUIT_OPEN` |

## Демонстрация

```bash
# gRPC напрямую (reflection включён)
docker run --rm --network flight-booking_default fullstorydev/grpcurl -plaintext flight-service:50051 list
docker run --rm --network flight-booking_default fullstorydev/grpcurl -plaintext \
  -d '{"flight_id":"11111111-1111-1111-1111-111111111111"}' flight-service:50051 flight.v1.FlightService/GetFlight   # UNAUTHENTICATED
docker run --rm --network flight-booking_default fullstorydev/grpcurl -plaintext -H 'x-api-key: dev-flight-api-key' \
  -d '{"flight_id":"11111111-1111-1111-1111-111111111111"}' flight-service:50051 flight.v1.FlightService/GetFlight

# бронирование
curl -s -XPOST localhost:8081/bookings -H 'Content-Type: application/json' -d '{"user_id":"u1",
  "flight_id":"11111111-1111-1111-1111-111111111111","passenger_name":"Ivan Petrov","passenger_email":"ivan@example.com","seat_count":2}'

# содержимое БД
docker compose exec flight-db psql -U flight -c "SELECT airline_code||flight_number AS flight, total_seats, available_seats, price, status FROM flights"
docker compose exec flight-db psql -U flight -c "SELECT booking_id, flight_id, seat_count, status FROM seat_reservations"
docker compose exec booking-db psql -U booking -c "SELECT id, user_id, flight_id, seat_count, total_price, status FROM bookings"

# кеш и логи
docker compose exec redis-replica redis-cli --scan --pattern '*'        # ключи (на текущем мастере)
docker compose exec redis-replica redis-cli ttl flight:11111111-1111-1111-1111-111111111111
docker compose logs -f flight-service | grep cache
docker compose logs -f booking-service | grep -E "retry|circuit"

# отказоустойчивость
docker compose stop flight-service      # retry → 503, после 5 ошибок CIRCUIT_OPEN; docker compose start flight-service
docker compose stop redis-master        # sentinel повышает реплику (~5–10 с)
docker compose exec redis-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster
```

## Принятые решения

- **Цена в контракте** — `Money{amount_minor, currency}` в копейках, чтобы не терять точность на float.
- **Дата в поиске** — собственное сообщение `Date` (`google.type.Date` не входит в `grpcio-tools`), сравнение идёт по UTC-дате вылета.
- **`UpdateFlight`** добавлен в контракт: в ТЗ он упоминается в требованиях к инвалидации кеша.
- **Статус `EXPIRED`** заложен в схему и контракт, но в текущем флоу не используется: бронирование подтверждается сразу, временных холдов нет.
- **Известное ограничение:** если ReserveSeats завершился по таймауту, а компенсация тоже не прошла (например, открыт circuit breaker), резервация может «зависнуть». Это видно в логах (`compensation failed`), и решается фоновым sweeper'ом, который переводит такие резервации в `EXPIRED`.
- **Окно гонки при инвалидации:** кеш удаляется после коммита, поэтому параллельное чтение может положить в кеш старое значение — не более чем на TTL.
