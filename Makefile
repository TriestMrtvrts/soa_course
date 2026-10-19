SERVICE := marketplace-service
CODEGEN_VENV := $(SERVICE)/.venv-codegen

.PHONY: generate up down logs psql

# Regenerate app/generated from openapi/marketplace.yaml (also done automatically in docker build)
generate:
	test -x $(CODEGEN_VENV)/bin/fastapi-codegen || (python3 -m venv $(CODEGEN_VENV) && $(CODEGEN_VENV)/bin/pip install -q -r $(SERVICE)/requirements-codegen.txt)
	PATH="$(CURDIR)/$(CODEGEN_VENV)/bin:$$PATH" sh $(SERVICE)/codegen/generate.sh

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f marketplace-service

psql:
	docker compose exec postgres psql -U marketplace -d marketplace
