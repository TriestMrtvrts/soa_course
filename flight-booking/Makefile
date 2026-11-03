.PHONY: proto up down e2e e2e-resilience grpc-checks

# gRPC stubs from proto/ (also generated automatically in docker build)
proto:
	test -x .venv-proto/bin/python || (python3 -m venv .venv-proto && .venv-proto/bin/pip install -q grpcio-tools==1.85.0 protobuf==7.36.2)
	PYTHON=.venv-proto/bin/python sh scripts/generate_proto.sh

up:
	docker compose up --build

down:
	docker compose down -v

e2e:
	python3 e2e/e2e_test.py

e2e-resilience:
	python3 e2e/e2e_test.py --resilience

grpc-checks:
	docker compose exec -T booking-service python - < e2e/grpc_checks.py
