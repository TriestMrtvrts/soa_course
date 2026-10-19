"""Order business logic. Every public method runs inside one DB transaction (see app.db.transaction)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import Order, OrderItem, Product, PromoCode, User, UserOperation
from app.generated.models import OrderCreate, OrderItemRequest, OrderStatus, OrderUpdate, ProductStatus, Role
from app.security import Principal
from app.services.promo_codes import calculate_discount, invalid_reason

CREATE_ORDER = "CREATE_ORDER"
UPDATE_ORDER = "UPDATE_ORDER"

ACTIVE_STATUSES = (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING)

# CREATED -> PAYMENT_PENDING -> PAID -> SHIPPED -> COMPLETED; CREATED | PAYMENT_PENDING -> CANCELED
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.PAYMENT_PENDING, OrderStatus.CANCELED},
    OrderStatus.PAYMENT_PENDING: {OrderStatus.PAID, OrderStatus.CANCELED},
    OrderStatus.PAID: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELED: set(),
}


class OrderService:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------ create
    def create(self, principal: Principal, body: OrderCreate) -> Order:
        user_id = principal.user_id
        self._lock_user(user_id)  # serializes concurrent order operations of one user

        # 1. rate limit
        self._check_rate_limit(user_id, CREATE_ORDER)

        # 2. active orders
        active_id = self.session.scalar(
            select(Order.id).where(Order.user_id == user_id, Order.status.in_(ACTIVE_STATUSES)).limit(1)
        )
        if active_id is not None:
            raise errors.order_has_active(active_id)

        # 3-5. catalog, stock, reservation
        products = self._lock_products({item.product_id for item in body.items})
        self._reserve(body.items, products)

        # 6. price snapshot
        order = Order(user_id=user_id, status=OrderStatus.CREATED, discount_amount=Decimal("0.00"))
        order.items = [
            OrderItem(product_id=item.product_id, quantity=item.quantity, price_at_order=products[item.product_id].price)
            for item in body.items
        ]

        # 7. amount and promo code
        subtotal = self._subtotal(order)
        discount = Decimal("0.00")
        if body.promo_code is not None:
            promo = self.session.scalar(
                select(PromoCode).where(PromoCode.code == body.promo_code).with_for_update()
            )
            if promo is None:
                raise errors.promo_code_invalid(body.promo_code, "NOT_FOUND")
            reason = invalid_reason(promo)
            if reason is not None:
                raise errors.promo_code_invalid(promo.code, reason)
            if subtotal < promo.min_order_amount:
                raise errors.promo_code_min_amount(promo.code, promo.min_order_amount, subtotal)
            discount = calculate_discount(promo, subtotal)
            promo.current_uses += 1
            order.promo_code = promo
        order.discount_amount = discount
        order.total_amount = subtotal - discount

        # 8. operation log + status
        self.session.add(order)
        self.session.add(UserOperation(user_id=user_id, operation_type=CREATE_ORDER))
        self.session.flush()
        return order

    # --------------------------------------------------------------------- get
    def get(self, principal: Principal, order_id: UUID) -> Order:
        order = self.session.get(Order, order_id)
        if order is None:
            raise errors.order_not_found(order_id)
        self._check_owner(principal, order)
        return order

    # ------------------------------------------------------------------ update
    def update(self, principal: Principal, order_id: UUID, body: OrderUpdate) -> Order:
        self._lock_user(principal.user_id)
        order = self._get_for_update(order_id)

        # 1. owner
        self._check_owner(principal, order)
        # 2. state
        if order.status != OrderStatus.CREATED:
            raise errors.invalid_state_transition(order.status.value, OrderStatus.CREATED.value)
        # 3. rate limit
        self._check_rate_limit(principal.user_id, UPDATE_ORDER)

        product_ids = {item.product_id for item in order.items} | {item.product_id for item in body.items}
        products = self._lock_products(product_ids)

        # 4. return previous reservation
        for item in order.items:
            products[item.product_id].stock += item.quantity

        # 5. check and reserve new items
        self._reserve(body.items, products)
        order.items = [
            OrderItem(product_id=item.product_id, quantity=item.quantity, price_at_order=products[item.product_id].price)
            for item in body.items
        ]

        # 6. recalculate amount and re-validate the promo code
        subtotal = self._subtotal(order)
        discount = Decimal("0.00")
        promo = order.promo_code
        if promo is not None:
            promo = self.session.get(PromoCode, promo.id, with_for_update=True, populate_existing=True)
            # usage limit is not re-checked: this order already holds one of the uses
            if invalid_reason(promo, check_uses=False) is None and subtotal >= promo.min_order_amount:
                discount = calculate_discount(promo, subtotal)
            else:
                promo.current_uses -= 1
                order.promo_code = None
        order.discount_amount = discount
        order.total_amount = subtotal - discount

        # 7. operation log
        self.session.add(UserOperation(user_id=principal.user_id, operation_type=UPDATE_ORDER))
        self.session.flush()
        self.session.refresh(order)
        return order

    # ------------------------------------------------------------------ cancel
    def cancel(self, principal: Principal, order_id: UUID) -> Order:
        order = self._get_for_update(order_id)
        self._check_owner(principal, order)
        return self._cancel(order)

    def _cancel(self, order: Order) -> Order:
        if order.status not in ACTIVE_STATUSES:
            raise errors.invalid_state_transition(order.status.value, OrderStatus.CANCELED.value)
        products = self._lock_products({item.product_id for item in order.items})
        for item in order.items:
            products[item.product_id].stock += item.quantity
        if order.promo_code_id is not None:
            promo = self.session.get(PromoCode, order.promo_code_id, with_for_update=True, populate_existing=True)
            promo.current_uses -= 1
        order.status = OrderStatus.CANCELED
        self.session.flush()
        return order

    # ----------------------------------------------------------- status change
    def change_status(self, principal: Principal, order_id: UUID, target: OrderStatus) -> Order:
        order = self._get_for_update(order_id)
        self._check_owner(principal, order)
        if target not in TRANSITIONS[order.status]:
            raise errors.invalid_state_transition(order.status.value, target.value)
        if principal.role != Role.ADMIN and target != OrderStatus.PAYMENT_PENDING:
            raise errors.access_denied("Only ADMIN can move an order beyond PAYMENT_PENDING")
        if target == OrderStatus.CANCELED:
            return self._cancel(order)
        order.status = target
        self.session.flush()
        return order

    # ----------------------------------------------------------------- helpers
    def _lock_user(self, user_id: UUID) -> None:
        self.session.execute(select(User.id).where(User.id == user_id).with_for_update())

    def _check_rate_limit(self, user_id: UUID, operation: str) -> None:
        last = self.session.scalar(
            select(UserOperation.created_at)
            .where(UserOperation.user_id == user_id, UserOperation.operation_type == operation)
            .order_by(UserOperation.created_at.desc())
            .limit(1)
        )
        if last is None:
            return
        window = timedelta(minutes=settings.order_rate_limit_minutes)
        elapsed = datetime.now(timezone.utc) - last
        if elapsed < window:
            raise errors.order_limit_exceeded(operation, int((window - elapsed).total_seconds()) + 1)

    def _get_for_update(self, order_id: UUID) -> Order:
        order = self.session.get(Order, order_id, with_for_update={"of": Order})
        if order is None:
            raise errors.order_not_found(order_id)
        return order

    @staticmethod
    def _check_owner(principal: Principal, order: Order) -> None:
        if principal.role != Role.ADMIN and order.user_id != principal.user_id:
            raise errors.order_ownership_violation(order.id)

    def _lock_products(self, product_ids: set[UUID]) -> dict[UUID, Product]:
        # sorted locking order prevents deadlocks between concurrent orders
        rows = self.session.scalars(
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {product.id: product for product in rows}

    @staticmethod
    def _reserve(items: list[OrderItemRequest], products: dict[UUID, Product]) -> None:
        # 3. catalog: product exists and is ACTIVE
        for item in items:
            product = products.get(item.product_id)
            if product is None:
                raise errors.product_not_found(item.product_id)
            if product.status != ProductStatus.ACTIVE:
                raise errors.product_inactive(product.id, product.status.value)
        # 4. stock: report every product that is short
        shortages = [
            {"product_id": str(item.product_id), "requested": item.quantity, "available": products[item.product_id].stock}
            for item in items
            if products[item.product_id].stock < item.quantity
        ]
        if shortages:
            raise errors.insufficient_stock(shortages)
        # 5. reservation
        for item in items:
            products[item.product_id].stock -= item.quantity

    @staticmethod
    def _subtotal(order: Order) -> Decimal:
        return sum((item.price_at_order * item.quantity for item in order.items), Decimal("0.00"))
