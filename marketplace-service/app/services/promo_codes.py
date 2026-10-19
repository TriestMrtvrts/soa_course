from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.db import PromoCode
from app.generated.models import DiscountType, PromoCodeCreate

CENT = Decimal("0.01")
# Spec: a percentage discount may not exceed 70% of the order; otherwise the full price (100%) is charged.
MAX_PERCENTAGE_DISCOUNT = Decimal("70")


def invalid_reason(promo: PromoCode, check_uses: bool = True) -> str | None:
    """Why the promo code cannot be applied right now, or None if it can."""
    now = datetime.now(timezone.utc)
    if not promo.active:
        return "INACTIVE"
    if check_uses and promo.current_uses >= promo.max_uses:
        return "USAGE_LIMIT_REACHED"
    if now < promo.valid_from:
        return "NOT_YET_VALID"
    if now > promo.valid_until:
        return "EXPIRED"
    return None


def calculate_discount(promo: PromoCode, amount: Decimal) -> Decimal:
    if promo.discount_type == DiscountType.PERCENTAGE:
        if promo.discount_value > MAX_PERCENTAGE_DISCOUNT:
            return Decimal("0.00")
        return (amount * promo.discount_value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return min(promo.discount_value, amount)


class PromoCodeService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, body: PromoCodeCreate) -> PromoCode:
        problems = []
        if body.valid_until <= body.valid_from:
            problems.append({"field": "body.valid_until", "message": "must be after valid_from", "type": "date_range"})
        if body.discount_type == DiscountType.PERCENTAGE and body.discount_value > 100:
            problems.append({"field": "body.discount_value", "message": "percentage must be <= 100", "type": "less_than_equal"})
        if problems:
            raise errors.validation_error(problems)
        if self.session.scalar(select(PromoCode.id).where(PromoCode.code == body.code)) is not None:
            raise errors.ApiError(409, "PROMO_CODE_ALREADY_EXISTS", "Promo code already exists", {"code": body.code})
        promo = PromoCode(
            code=body.code,
            discount_type=body.discount_type,
            discount_value=body.discount_value,
            min_order_amount=Decimal(body.min_order_amount),
            max_uses=body.max_uses,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            active=body.active,
        )
        self.session.add(promo)
        self.session.flush()
        return promo
