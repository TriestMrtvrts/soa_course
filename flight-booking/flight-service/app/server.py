import logging
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from flight.v1 import flight_service_pb2, flight_service_pb2_grpc

from app import config
from app.cache import Cache
from app.interceptors import ApiKeyAuthInterceptor, LoggingInterceptor
from app.logging_setup import configure_logging
from app.service import FlightService

logger = logging.getLogger("flight.server")


def main() -> None:
    configure_logging()
    pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=config.GRPC_WORKERS,
                          kwargs={"row_factory": dict_row}, open=True)
    cache = Cache(config.REDIS_SENTINELS, config.REDIS_MASTER_NAME, config.CACHE_TTL_SECONDS)

    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=config.GRPC_WORKERS),
        interceptors=[LoggingInterceptor(), ApiKeyAuthInterceptor(config.API_KEYS)],
    )
    flight_service_pb2_grpc.add_FlightServiceServicer_to_server(FlightService(pool, cache), server)

    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    reflection.enable_server_reflection(
        (flight_service_pb2.DESCRIPTOR.services_by_name["FlightService"].full_name,
         health.SERVICE_NAME, reflection.SERVICE_NAME),
        server,
    )

    server.add_insecure_port(f"[::]:{config.GRPC_PORT}")
    server.start()
    logger.info("flight-service started", extra={"fields": {"port": config.GRPC_PORT}})
    server.wait_for_termination()


if __name__ == "__main__":
    main()
