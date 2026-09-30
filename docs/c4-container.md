# Так называемое C4 Container diagram


```mermaid
flowchart TB
    buyer["Покупатель<br/>[Person]"]
    seller["Продавец<br/>[Person]"]

    subgraph marketplace["Маркетплейс [Software System]"]
        direction TB

        gateway["API Gateway<br/>[Container: HTTP API]<br/>Маршрутизация, JWT, rate limiting"]

        subgraph services["Доменные контейнеры"]
            direction LR
            user["User Service<br/>[Container: FastAPI]<br/>Пользователи, роли, профили"]
            catalog["Catalog Service<br/>[Container: FastAPI]<br/>Товары, цены, остатки"]
            feed["Feed Service<br/>[Container: FastAPI]<br/>Персональная лента"]
            order["Order Service<br/>[Container: FastAPI]<br/>Заказы и статусы"]
            payment["Payment Service<br/>[Container: FastAPI]<br/>Платежи и возвраты"]
            notification["Notification Service<br/>[Container: consumer]<br/>Отправка уведомлений"]
        end

        broker[("Kafka<br/>[Container: message broker]<br/>Доменные события")]

        subgraph data["Изолированные хранилища — общих БД нет"]
            direction LR
            userdb[("User DB<br/>PostgreSQL")]
            catalogdb[("Catalog DB<br/>PostgreSQL")]
            feeddb[("Feed Store<br/>Redis + OpenSearch")]
            orderdb[("Order DB<br/>PostgreSQL")]
            paymentdb[("Payment DB<br/>PostgreSQL")]
            notificationdb[("Notification DB<br/>PostgreSQL")]
        end
    end

    psp["Платёжный провайдер<br/>[External System]"]
    messages["Email / SMS / Push<br/>[External System]"]

    buyer -->|HTTPS| gateway
    seller -->|HTTPS| gateway

    gateway -->|REST| user
    gateway -->|REST| catalog
    gateway -->|REST| feed
    gateway -->|REST| order

    order -->|резерв товара, REST| catalog
    order -->|создать платёж, REST| payment

    user -.->|события| broker
    catalog -.->|события| broker
    order -.->|события| broker
    payment -.->|события| broker
    broker -.->|события| feed
    broker -.->|события| order
    broker -.->|события| notification

    user -->|владеет| userdb
    catalog -->|владеет| catalogdb
    feed -->|владеет| feeddb
    order -->|владеет| orderdb
    payment -->|владеет| paymentdb
    notification -->|владеет| notificationdb

    payment -->|HTTPS| psp
    notification -->|HTTPS / SMTP| messages

    classDef person fill:#084c61,color:#fff,stroke:#063b4b;
    classDef container fill:#d9eef7,color:#17252a,stroke:#277da1;
    classDef gatewayStyle fill:#168f88,color:#fff,stroke:#0f6d68;
    classDef database fill:#fff0c2,color:#3d3420,stroke:#b5852b;
    classDef queue fill:#eadcf8,color:#30223c,stroke:#8557a5;
    classDef external fill:#f2f2f2,color:#222,stroke:#777;

    class buyer,seller person;
    class user,catalog,feed,order,payment,notification container;
    class gateway gatewayStyle;
    class userdb,catalogdb,feeddb,orderdb,paymentdb,notificationdb database;
    class broker queue;
    class psp,messages external;
```

## События

| Источник | События | Получатели |
|---|---|---|
| User Service | `UserRegistered` | Feed Service |
| Catalog Service | `ProductCreated`, `ProductUpdated` | Feed Service |
| Order Service | `OrderCreated`, `OrderStatusChanged` | Feed Service, Notification Service |
| Payment Service | `PaymentSucceeded`, `PaymentFailed` | Order Service, Notification Service |

