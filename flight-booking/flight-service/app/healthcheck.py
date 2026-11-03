"""Docker HEALTHCHECK: standard grpc.health.v1 probe."""
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app import config

with grpc.insecure_channel(f"localhost:{config.GRPC_PORT}") as channel:
    try:
        status = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), timeout=2).status
    except grpc.RpcError:
        sys.exit(1)
sys.exit(0 if status == health_pb2.HealthCheckResponse.SERVING else 1)
