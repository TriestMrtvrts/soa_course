#!/bin/sh
# Generates Python gRPC stubs from proto/ into OUT_DIR (default: both services' gen/ dirs).
# Usage: sh scripts/generate_proto.sh [OUT_DIR]     (requires grpcio-tools)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
generate() {
  rm -rf "$1" && mkdir -p "$1"
  "$PYTHON" -m grpc_tools.protoc -I "$ROOT/proto" \
    --python_out="$1" --grpc_python_out="$1" --pyi_out="$1" \
    "$ROOT/proto/flight/v1/flight_service.proto"
  echo "generated -> $1"
}
if [ $# -gt 0 ]; then
  generate "$1"
else
  generate "$ROOT/flight-service/gen"
  generate "$ROOT/booking-service/gen"
fi
