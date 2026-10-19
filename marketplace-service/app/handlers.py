"""Implementation of the generated MarketplaceApi interface.

Thin layer: contract-level checks the OpenAPI schema can't express in pydantic,
a transaction per call, delegation to services and mapping of entities to generated DTOs.
"""
from uuid import UUID

from app import errors
from app.db import Order, Product, PromoCode, User, transaction
from app.generated.api import MarketplaceApi
from app.generated.models import (
    HealthResponse,
    LoginRequest,
    OrderCreate,
    OrderItemRequest,
    OrderItemResponse,
    OrderResponse,
    OrderStatusChange,
    OrderUpdate,
    ProductCreate,
    ProductPage,
    ProductResponse,
    ProductStatus,
    ProductUpdate,
    PromoCodeCreate,
    PromoCodeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from app.security import Principal
from app.services.auth import AuthService
from app.services.orders import OrderService
from app.services.products import ProductService
from app.services.promo_codes import PromoCodeService

NULLABLE_PRODUCT_FIELDS = {"description"}


# ------------------------------------------------------------ contract checks
def validate_product_update(body: ProductUpdate) -> None:
    """`minProperties: 1` and non-nullable fields of ProductUpdate."""
    if not body.model_fields_set:
        raise errors.validation_error(
            [{"field": "body", "message": "At least one field must be provided", "type": "min_properties"}]
        )
    nulls = [
        {"field": f"body.{name}", "message": "Field may not be null", "type": "not_nullable"}
        for name in body.model_fields_set
        if getattr(body, name) is None and name not in NULLABLE_PRODUCT_FIELDS
    ]
    if nulls:
        raise errors.validation_error(nulls)


def validate_unique_items(items: list[OrderItemRequest]) -> None:
    seen: set[UUID] = set()
    problems = []
    for index, item in enumerate(items):
        if item.product_id in seen:
            problems.append({
                "field": f"body.items.{index}.product_id",
                "message": "Duplicate product_id; merge quantities into one item",
                "type": "duplicate",
            })
        seen.add(item.product_id)
    if problems:
        raise errors.validation_error(problems)


# -------------------------------------------------------------------- mappers
def to_product(product: Product) -> ProductResponse:
    return ProductResponse.model_validate(product, from_attributes=True)


def to_order(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        items=[OrderItemResponse.model_validate(item, from_attributes=True) for item in order.items],
        total_amount=order.total_amount,
        discount_amount=order.discount_amount,
        promo_code=order.promo_code.code if order.promo_code else None,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def to_promo(promo: PromoCode) -> PromoCodeResponse:
    return PromoCodeResponse.model_validate(promo, from_attributes=True)


def to_user(user: User) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


class MarketplaceHandlers(MarketplaceApi):
    def health(self) -> HealthResponse:
        return HealthResponse(status="ok")

    # ------------------------------------------------------------------- auth
    def register(self, body: RegisterRequest) -> UserResponse:
        with transaction() as session:
            return to_user(AuthService(session).register(body))

    def login(self, body: LoginRequest) -> TokenPair:
        with transaction() as session:
            return AuthService(session).login(body)

    def refresh(self, body: RefreshRequest) -> TokenPair:
        with transaction() as session:
            return AuthService(session).refresh(body)

    # --------------------------------------------------------------- products
    def create_product(self, body: ProductCreate, *, principal: Principal) -> ProductResponse:
        with transaction() as session:
            return to_product(ProductService(session).create(principal, body))

    def list_products(
        self,
        page: int = 0,
        size: int = 20,
        status: ProductStatus | None = None,
        category: str | None = None,
        *,
        principal: Principal,
    ) -> ProductPage:
        with transaction() as session:
            items, total = ProductService(session).list(page, size, status, category)
            return ProductPage(items=[to_product(p) for p in items], totalElements=total, page=page, size=size)

    def get_product(self, id: UUID, *, principal: Principal) -> ProductResponse:
        with transaction() as session:
            return to_product(ProductService(session).get(id))

    def update_product(self, id: UUID, body: ProductUpdate, *, principal: Principal) -> ProductResponse:
        validate_product_update(body)
        with transaction() as session:
            return to_product(ProductService(session).update(principal, id, body))

    def delete_product(self, id: UUID, *, principal: Principal) -> ProductResponse:
        with transaction() as session:
            return to_product(ProductService(session).archive(principal, id))

    # ----------------------------------------------------------------- orders
    def create_order(self, body: OrderCreate, *, principal: Principal) -> OrderResponse:
        validate_unique_items(body.items)
        with transaction() as session:
            return to_order(OrderService(session).create(principal, body))

    def get_order(self, id: UUID, *, principal: Principal) -> OrderResponse:
        with transaction() as session:
            return to_order(OrderService(session).get(principal, id))

    def update_order(self, id: UUID, body: OrderUpdate, *, principal: Principal) -> OrderResponse:
        validate_unique_items(body.items)
        with transaction() as session:
            return to_order(OrderService(session).update(principal, id, body))

    def cancel_order(self, id: UUID, *, principal: Principal) -> OrderResponse:
        with transaction() as session:
            return to_order(OrderService(session).cancel(principal, id))

    def change_order_status(self, id: UUID, body: OrderStatusChange, *, principal: Principal) -> OrderResponse:
        with transaction() as session:
            return to_order(OrderService(session).change_status(principal, id, body.status))

    # ------------------------------------------------------------ promo codes
    def create_promo_code(self, body: PromoCodeCreate, *, principal: Principal) -> PromoCodeResponse:
        with transaction() as session:
            return to_promo(PromoCodeService(session).create(body))
