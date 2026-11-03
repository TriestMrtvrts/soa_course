# build context: flight-booking/ (the proto contract is shared by both services)
FROM python:3.12-slim AS codegen
RUN pip install --no-cache-dir grpcio-tools==1.85.0 protobuf==7.36.2
COPY proto /build/proto
COPY scripts/generate_proto.sh /build/scripts/generate_proto.sh
RUN sh /build/scripts/generate_proto.sh /gen

FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/srv/gen
COPY booking-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=codegen /gen ./gen
COPY booking-service/app ./app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
