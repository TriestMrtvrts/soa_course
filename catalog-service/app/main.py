from fastapi import FastAPI

app = FastAPI(title="catalog-service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catalog-service"}
