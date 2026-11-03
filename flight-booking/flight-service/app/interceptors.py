"""Server interceptors: API-key authentication and per-call logging."""
import hmac
import logging
import time

import grpc

logger = logging.getLogger("flight.grpc")

API_KEY_HEADER = "x-api-key"
# infrastructure services stay open so grpcurl / docker healthchecks work without a key
PUBLIC_PREFIXES = ("/grpc.reflection.", "/grpc.health.")


def _abort_handler(code: grpc.StatusCode, message: str) -> grpc.RpcMethodHandler:
    def abort(request, context):
        context.abort(code, message)

    return grpc.unary_unary_rpc_method_handler(abort)


class ApiKeyAuthInterceptor(grpc.ServerInterceptor):
    def __init__(self, api_keys: set[str]):
        if not api_keys:
            raise RuntimeError("FLIGHT_SERVICE_API_KEYS is empty: refusing to start without credentials")
        self._keys = [key.encode() for key in api_keys]

    def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method
        if method.startswith(PUBLIC_PREFIXES):
            return continuation(handler_call_details)
        metadata = dict(handler_call_details.invocation_metadata or ())
        provided = metadata.get(API_KEY_HEADER)
        if provided is None:
            logger.warning("unauthenticated call", extra={"fields": {"method": method, "reason": "missing api key"}})
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, f"missing {API_KEY_HEADER} metadata")
        if not any(hmac.compare_digest(provided.encode(), key) for key in self._keys):
            logger.warning("unauthenticated call", extra={"fields": {"method": method, "reason": "invalid api key"}})
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, "invalid api key")
        return continuation(handler_call_details)


class LoggingInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        method = handler_call_details.method
        inner = handler.unary_unary

        def logged(request, context):
            started = time.perf_counter()
            try:
                return inner(request, context)
            finally:
                code = context.code() or grpc.StatusCode.OK
                logger.info("grpc call", extra={"fields": {
                    "method": method,
                    "code": code.name if hasattr(code, "name") else str(code),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }})

        return grpc.unary_unary_rpc_method_handler(
            logged,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
