"""Business/contract errors and their mapping to the ErrorResponse format from OpenAPI."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.generated.models import ErrorResponse


class ApiError(Exception):
    def __init__(self, status: int, error_code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message
        self.details = details


def validation_error(errors: list[dict[str, Any]]) -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})


def token_invalid(message: str = "Access token is missing or invalid") -> ApiError:
    return ApiError(401, "TOKEN_INVALID", message)


def token_expired() -> ApiError:
    return ApiError(401, "TOKEN_EXPIRED", "Access token has expired")


def refresh_token_invalid() -> ApiError:
    return ApiError(401, "REFRESH_TOKEN_INVALID", "Refresh token is invalid or expired")


def invalid_credentials() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "Invalid email or password")


def access_denied(message: str = "Insufficient permissions for this operation") -> ApiError:
    return ApiError(403, "ACCESS_DENIED", message)


def order_ownership_violation(order_id) -> ApiError:
    return ApiError(403, "ORDER_OWNERSHIP_VIOLATION", "Order belongs to another user", {"order_id": str(order_id)})


def product_not_found(product_id) -> ApiError:
    return ApiError(404, "PRODUCT_NOT_FOUND", "Product not found", {"product_id": str(product_id)})


def order_not_found(order_id) -> ApiError:
    return ApiError(404, "ORDER_NOT_FOUND", "Order not found", {"order_id": str(order_id)})


def product_inactive(product_id, status: str) -> ApiError:
    return ApiError(
        409, "PRODUCT_INACTIVE", "Product is not available for ordering",
        {"product_id": str(product_id), "status": status},
    )


def order_has_active(order_id) -> ApiError:
    return ApiError(409, "ORDER_HAS_ACTIVE", "User already has an active order", {"order_id": str(order_id)})


def invalid_state_transition(current: str, target: str) -> ApiError:
    return ApiError(
        409, "INVALID_STATE_TRANSITION", f"Transition {current} -> {target} is not allowed",
        {"current_status": current, "target_status": target},
    )


def insufficient_stock(items: list[dict[str, Any]]) -> ApiError:
    return ApiError(409, "INSUFFICIENT_STOCK", "Insufficient stock for one or more products", {"items": items})


def promo_code_invalid(code: str, reason: str) -> ApiError:
    return ApiError(422, "PROMO_CODE_INVALID", "Promo code is not applicable", {"promo_code": code, "reason": reason})


def promo_code_min_amount(code: str, min_amount, order_amount) -> ApiError:
    return ApiError(
        422, "PROMO_CODE_MIN_AMOUNT", "Order amount is below the promo code minimum",
        {"promo_code": code, "min_order_amount": float(min_amount), "order_amount": float(order_amount)},
    )


def order_limit_exceeded(operation: str, retry_after_seconds: int) -> ApiError:
    return ApiError(
        429, "ORDER_LIMIT_EXCEEDED", "Too many order operations, try again later",
        {"operation": operation, "retry_after_seconds": retry_after_seconds},
    )


def error_response(status: int, error_code: str, message: str, details: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error_code=error_code, message=message, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "TOKEN_INVALID", 403: "ACCESS_DENIED"}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.error_code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return error_response(400, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return error_response(exc.status_code, code, str(exc.detail))
