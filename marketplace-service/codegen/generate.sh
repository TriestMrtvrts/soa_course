#!/bin/sh
# Generates app/generated (pydantic models + FastAPI router/interface) from openapi/marketplace.yaml.
# Usage: ./codegen/generate.sh   (requires `pip install -r requirements-codegen.txt`)
set -eu
cd "$(dirname "$0")/.."
rm -rf app/generated
mkdir -p app/generated
fastapi-codegen \
  --input openapi/marketplace.yaml \
  --output app/generated \
  --model-file models.py \
  --template-dir codegen/templates \
  --custom-visitor codegen/x_roles_visitor.py \
  --python-version 3.12 \
  --strict-nullable \
  --disable-timestamp
touch app/generated/__init__.py
echo "Generated: $(ls app/generated)"
