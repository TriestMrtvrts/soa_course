# Marketplace

- **ДЗ №1** — архитектура (C4, домены) и стартовый `catalog-service` с `/health` (порт 8000). Описание ниже, начиная с раздела «1. C4 Container диаграмма».
- **ДЗ №2** — `marketplace-service`: OpenAPI + CRUD + заказы + JWT + роли (порт 8080). Описание ниже.

## ДЗ №2: Marketplace API (OpenAPI + CRUD)

Стек: Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, Flyway, PyJWT, `fastapi-code-generator`.

### Запуск

```bash
docker compose up --build          # postgres -> flyway migrate -> marketplace-service
# Swagger UI: http://localhost:8080/docs   (отдаётся сама спецификация openapi/marketplace.yaml)
python3 marketplace-service/e2e/e2e_test.py          # ~110 E2E-проверок (только stdlib)
python3 marketplace-service/e2e/e2e_test.py --wait   # + проверки после окна rate limit (~1 мин)
make psql                          # psql внутри контейнера для SELECT
docker compose down -v             # остановить и удалить данные
```

Seed-администратор (роль ADMIN нельзя получить через регистрацию): `admin@marketplace.dev` / `Admin12345`.

Переменные окружения сервиса (см. `docker-compose.yml`): `ORDER_RATE_LIMIT_MINUTES` (N для лимита частоты, по умолчанию 1),
`ACCESS_TOKEN_TTL_MINUTES` (15), `REFRESH_TOKEN_TTL_DAYS` (7), `JWT_SECRET`.

### Как устроено

```
marketplace-service/
├── openapi/marketplace.yaml     # контракт: эндпоинты, схемы, ограничения, ошибки, security, x-roles
├── codegen/
│   ├── generate.sh              # кодогенерация одной командой
│   ├── templates/api.jinja2     # шаблон: интерфейс MarketplaceApi + APIRouter
│   └── x_roles_visitor.py       # передаёт x-roles из спецификации в шаблон
├── app/
│   ├── generated/               # НЕ в git: models.py (pydantic DTO) + api.py (роутер/интерфейс)
│   ├── main.py                  # сборка приложения
│   ├── handlers.py              # реализация сгенерированного MarketplaceApi
│   ├── services/                # бизнес-логика: auth, products, orders, promo_codes
│   ├── db.py                    # SQLAlchemy-сущности, транзакция на запрос
│   ├── security.py              # JWT access/refresh, bcrypt, зависимости authenticate/require_roles
│   ├── errors.py                # ApiError + единый формат ErrorResponse
│   └── request_logging.py       # JSON-логи, request_id, X-Request-Id
├── db/migration/                # Flyway: V1__init_schema.sql, V2__seed_admin.sql
└── e2e/e2e_test.py              # сценарии для защиты
```

| Пункт | Реализация |
|---|---|
| 1–2. OpenAPI | `openapi/marketplace.yaml`: `ProductCreate` / `ProductUpdate` / `ProductResponse`, `format: decimal / date-time / uuid`, `enum`, `nullable`, `required`, `additionalProperties: false`. `PUT /products/{id}` — частичное обновление (`minProperties: 1`) |
| 3. Кодогенерация | `make generate` (или `sh codegen/generate.sh`). В Docker выполняется в отдельной build-стадии, `app/generated/` в `.gitignore`. Генерируются DTO и контроллеры: `create_router()` регистрирует все эндпоинты и вызывает методы абстрактного класса `MarketplaceApi`, который реализует `app/handlers.py`. Ручных DTO нет |
| 4. PostgreSQL | Flyway-миграции, индекс `idx_products_status`, `created_at` — `DEFAULT now()`, `updated_at` — триггер `set_updated_at`. `DELETE` переводит товар в `ARCHIVED` |
| 5. Ошибки | Единый `ErrorResponse {error_code, message, details}`, каталог кодов в `info.description` и в `responses` каждого эндпоинта |
| 6. Валидация | Ограничения из спецификации попадают в pydantic-модели; FastAPI проверяет их до вызова хендлера и возвращает `400 VALIDATION_ERROR` со списком `details.errors[{field, message, type}]` |
| 7. Заказы | `services/orders.py`: проверки в порядке из ТЗ, одна транзакция на операцию, `SELECT … FOR UPDATE` на пользователя, товары (в порядке id — без дедлоков) и промокод; снапшот цены `price_at_order`; модель состояний `TRANSITIONS` |
| 8. Логи | Один JSON на запрос: `request_id, method, endpoint, status_code, duration_ms, user_id, timestamp` + `request_body` для POST/PUT/DELETE, пароли и токены заменяются на `***`. `X-Request-Id` в ответе |
| 9. JWT | `/auth/register`, `/auth/login`, `/auth/refresh`; access 15 мин, refresh 7 дней (claim `type` различает их); `TOKEN_EXPIRED` / `TOKEN_INVALID` / `REFRESH_TOKEN_INVALID` |
| 10. Роли | Claim `role`; роли операции заданы в спецификации (`x-roles`) и проверяются сгенерированным роутером (`ACCESS_DENIED`); владение (`seller_id`, `user_id` заказа) проверяется в сервисах |

Принятые решения там, где ТЗ допускает трактовки:
- **Процентная скидка > 70%** не применяется: заказ оплачивается по полной цене, но промокод считается использованным. Процент ≤ 70% даёт обычную скидку.
- **Смена статуса заказа**: добавлен `POST /orders/{id}/status`, иначе состояния после `CREATED` недостижимы. USER может только `CREATED → PAYMENT_PENDING`, ADMIN — любой допустимый переход, `CANCELED` — только через `/cancel`.
- **Лимит частоты** считается по пользователю, который выполняет операцию (для ADMIN — по самому админу).
- **Промокод при обновлении заказа** перепроверяется (активность, срок, `min_order_amount`; лимит использований не проверяется — заказ уже занимает одно использование). Если не проходит — скидка снимается, `current_uses` уменьшается.
- **Повтор одного `product_id`** в `items` → `VALIDATION_ERROR` (количество нужно указывать в одной позиции).
- Дополнительные коды ошибок: `INVALID_CREDENTIALS`, `USER_ALREADY_EXISTS`, `PROMO_CODE_ALREADY_EXISTS`, `NOT_FOUND`, `INTERNAL_ERROR`.

### Демонстрация на защите

```bash
B=http://localhost:8080
ADMIN=$(curl -s $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@marketplace.dev","password":"Admin12345"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s $B/products -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"name":"Phone","price":100,"stock":10,"category":"phones","status":"ACTIVE"}'
```

SELECT-запросы (`make psql`):

```sql
SELECT id, name, price, stock, status, seller_id, created_at, updated_at FROM products ORDER BY created_at DESC LIMIT 10;
SELECT id, user_id, status, total_amount, discount_amount, promo_code_id FROM orders ORDER BY created_at DESC LIMIT 10;
SELECT order_id, product_id, quantity, price_at_order FROM order_items;
SELECT code, discount_type, discount_value, min_order_amount, current_uses, max_uses, active FROM promo_codes;
SELECT user_id, operation_type, created_at FROM user_operations ORDER BY created_at DESC LIMIT 10;
SELECT email, role FROM users;
SELECT version, description, success FROM flyway_schema_history;
```

## ДЗ №1: запуск catalog-service

```bash
docker compose up --build -d catalog-service
curl -i http://localhost:8000/health
```

## 1. C4 Container диаграмма

См. [docs/c4-container.md](docs/c4-container.md) 

## 2. Домены и их ответственность

| Домен | Ответственность |
|---|---|
| Identity & Users | Регистрация, аутентификация, профили покупателей и продавцов, роли |
| Catalog | Карточки товаров, категории, цены, остатки; CRUD для продавцов |
| Feed / Personalization | Построение персональной ленты: профиль интересов, ранжирование, индекс поиска |
| Orders | Корзина, оформление заказа, жизненный цикл статусов, оркестрация saga |
| Payments | Инициация и учёт платежей, возвраты, ledger, интеграция с провайдером |
| Notifications | Шаблоны и доставка уведомлений о статусах заказов (email/SMS/push) |

## 3. Распределение доменов по сервисам

Один домен — один сервис (User, Catalog, Feed, Order, Payment, Notification Service) плюс API Gateway
как единая точка входа. Логика разбиения: домены имеют разные профили нагрузки (лента — read-heavy,
платежи — строгая консистентность), разные команды-владельцы и разные требования к надёжности,
поэтому масштабируются и релизятся независимо.

## 4. Владение данными и взаимодействия

Каждый сервис владеет своей БД, общих баз нет; чужие данные доступны только через API или события.

| Сервис | Владеет данными | Отвечает за |
|---|---|---|
| User | пользователи, роли, учётные данные (PostgreSQL) | регистрация, вход, выдача JWT |
| Catalog | товары, категории, остатки (PostgreSQL) | управление каталогом, резерв остатков |
| Feed | профили интересов, поисковый индекс — производные копии (Redis/OpenSearch) | выдача ленты |
| Order | заказы, позиции, история статусов (PostgreSQL) | оформление заказа, saga |
| Payment | платежи, ledger (PostgreSQL) | оплата, возвраты |
| Notification | шаблоны, журнал отправок (PostgreSQL) | доставка уведомлений |

Взаимодействия:

| От → К | Способ | Зачем |
|---|---|---|
| Client → Gateway → сервисы | синхронно, REST | пользовательские запросы |
| Order → Catalog | синхронно, REST | проверка и резерв товара при оформлении |
| Order → Payment | синхронно, REST | создание платежа |
| Catalog → Feed | асинхронно, Kafka (`ProductCreated/Updated`) | обновление индекса ленты |
| Order → Feed, Notification | асинхронно, Kafka (`OrderCreated/StatusChanged`) | сигналы для ленты, уведомления |
| Payment → Order, Notification | асинхронно, Kafka (`PaymentSucceeded/Failed`) | продвижение saga, уведомления |
| User → остальные | асинхронно, Kafka (`UserRegistered`) | инициализация профилей |

Синхронно — только там, где ответ нужен немедленно (резерв, платёж); всё остальное асинхронно,
чтобы сбой уведомлений или ленты не блокировал заказ.

## 5. Альтернативные варианты декомпозиции

**Вариант A. Модульный монолит.** Все домены — модули одного приложения с одной БД (схемы по модулям).
- Плюсы: простота разработки и деплоя, локальные транзакции, минимум инфраструктуры, низкая задержка вызовов.
- Минусы: нельзя независимо масштабировать ленту и платежи; единая точка отказа; риск размывания границ модулей; общий цикл релизов.

**Вариант B. Микросервисы по доменам (6 сервисов + Gateway + Kafka).** Описан выше.
- Плюсы: независимое масштабирование и релиз, изоляция отказов, чёткое владение данными, подходящая технология под каждый домен (Redis/OpenSearch для ленты).
- Минусы: распределённые транзакции (saga), eventual consistency, сложность эксплуатации (наблюдаемость, CI/CD, брокер), сетевые задержки.

**Вариант C. Укрупнённые сервисы (3 сервиса):** «Core» (User + Catalog), «Trading» (Order + Payment), «Engagement» (Feed + Notification).
- Плюсы: меньше сервисов и интеграций, локальные транзакции заказ+платёж, проще операции.
- Минусы: сервисы смешивают домены с разной нагрузкой (Feed и Notification масштабируются вместе); платёжный контур не изолирован (PCI-scope шире); крупные сервисы со временем превращаются в мини-монолиты.

## 6. Финальный выбор — Вариант B

Выбран вариант B, потому что требования кейса на него опираются:
- **Персонализированная лента** — read-heavy и требует собственного хранилища/индекса, а не реляционной БД каталога; отдельный Feed Service масштабируется независимо.
- **Платежи** требуют строгой консистентности и изоляции (аудит, безопасность), чего сложнее добиться в монолите или укрупнённом сервисе.
- **Уведомления** по природе асинхронны: сбой провайдера не должен влиять на заказ.
- **Продавцы и покупатели** — разные профили нагрузки на каталог (запись) и ленту (чтение).

Принятые компромиссы: консистентность между заказом, платежом и резервом — через saga и идемпотентные события;
рост операционной сложности компенсируется единым шаблоном сервиса, health-check в каждом сервисе и контейнеризацией.
Вариант A разумен для MVP, но не отвечает на требования независимого масштабирования; вариант C — компромисс,
но объединяет домены с несовместимыми профилями нагрузки.

## Структура репозитория

```
├── README.md
├── Makefile
├── docker-compose.yml
├── docs/c4-container.md
├── catalog-service/        # ДЗ №1
└── marketplace-service/    # ДЗ №2
```
