# Stage 1: generate models + router from the OpenAPI contract (code is never committed)
FROM python:3.12-slim AS codegen
WORKDIR /build
COPY requirements-codegen.txt .
RUN pip install --no-cache-dir -r requirements-codegen.txt
COPY openapi ./openapi
COPY codegen ./codegen
RUN sh codegen/generate.sh

# Stage 2: runtime
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY openapi ./openapi
COPY app ./app
COPY --from=codegen /build/app/generated ./app/generated
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
