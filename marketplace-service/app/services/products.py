from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.db import Product
from app.generated.models import ProductCreate, ProductStatus, ProductUpdate, Role
from app.security import Principal


class ProductService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, principal: Principal, body: ProductCreate) -> Product:
        product = Product(
            name=body.name,
            description=body.description,
            price=body.price,
            stock=body.stock,
            category=body.category,
            status=body.status,
            seller_id=principal.user_id,
        )
        self.session.add(product)
        self.session.flush()
        return product

    def get(self, product_id: UUID) -> Product:
        product = self.session.get(Product, product_id)
        if product is None:
            raise errors.product_not_found(product_id)
        return product

    def list(
        self, page: int, size: int, status: ProductStatus | None, category: str | None
    ) -> tuple[list[Product], int]:
        query = select(Product)
        if status is not None:
            query = query.where(Product.status == status)
        if category is not None:
            query = query.where(Product.category == category)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        items = self.session.scalars(
            query.order_by(Product.created_at.desc(), Product.id).offset(page * size).limit(size)
        ).all()
        return list(items), total

    def update(self, principal: Principal, product_id: UUID, body: ProductUpdate) -> Product:
        product = self._get_owned_for_update(principal, product_id)
        for field in body.model_fields_set:  # partial update: only fields present in the request
            setattr(product, field, getattr(body, field))
        self.session.flush()
        return product

    def archive(self, principal: Principal, product_id: UUID) -> Product:
        product = self._get_owned_for_update(principal, product_id)
        product.status = ProductStatus.ARCHIVED  # soft delete
        self.session.flush()
        return product

    def _get_owned_for_update(self, principal: Principal, product_id: UUID) -> Product:
        product = self.session.get(Product, product_id, with_for_update=True)
        if product is None:
            raise errors.product_not_found(product_id)
        if principal.role == Role.SELLER and product.seller_id != principal.user_id:
            raise errors.access_denied("Seller can manage only own products")
        return product
