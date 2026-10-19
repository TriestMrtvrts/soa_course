"""Database engine, ORM entities and transaction helper.

ORM classes describe tables only; API DTOs are generated from OpenAPI (app/generated/models.py).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Enum, FetchedValue, ForeignKey, Numeric, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import settings
from app.generated.models import DiscountType, OrderStatus, ProductStatus, Role

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction() -> Iterator[Session]:
    """One request = one transaction: commit on success, rollback on any exception."""
    with SessionLocal.begin() as session:
        yield session


def pg_enum(enum_cls, name: str) -> Enum:
    # Types are created by Flyway migrations, never by SQLAlchemy.
    return Enum(enum_cls, name=name, create_type=False, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    # Read DB-generated values (id, created_at, updated_at from trigger) back via RETURNING.
    __mapper_args__ = {"eager_defaults": True}


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(pg_enum(Role, "user_role"))
    created_at: Mapped[datetime] = mapped_column(server_default=FetchedValue())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(4000))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int]
    category: Mapped[str] = mapped_column(String(100))
    status: Mapped[ProductStatus] = mapped_column(pg_enum(ProductStatus, "product_status"))
    seller_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=FetchedValue())
    updated_at: Mapped[datetime] = mapped_column(server_default=FetchedValue(), server_onupdate=FetchedValue())


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    code: Mapped[str] = mapped_column(String(20))
    discount_type: Mapped[DiscountType] = mapped_column(pg_enum(DiscountType, "discount_type"))
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    min_order_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_uses: Mapped[int]
    current_uses: Mapped[int] = mapped_column(server_default=FetchedValue())
    valid_from: Mapped[datetime]
    valid_until: Mapped[datetime]
    active: Mapped[bool]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus, "order_status"))
    promo_code_id: Mapped[UUID | None] = mapped_column(ForeignKey("promo_codes.id"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=FetchedValue())
    updated_at: Mapped[datetime] = mapped_column(server_default=FetchedValue(), server_onupdate=FetchedValue())

    items: Mapped[list["OrderItem"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderItem.id", lazy="selectin"
    )
    promo_code: Mapped[PromoCode | None] = relationship(lazy="joined")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    price_at_order: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class UserOperation(Base):
    __tablename__ = "user_operations"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=FetchedValue())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation_type: Mapped[str] = mapped_column(
        Enum("CREATE_ORDER", "UPDATE_ORDER", name="operation_type", create_type=False)
    )
    created_at: Mapped[datetime] = mapped_column(server_default=FetchedValue())
