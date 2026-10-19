import yaml
from fastapi import FastAPI

from app.config import settings
from app.errors import register_exception_handlers
from app.generated.api import API_TITLE, API_VERSION, create_router
from app.handlers import MarketplaceHandlers
from app.request_logging import RequestLoggingMiddleware, configure_logging
from app.security import authenticate, require_roles

configure_logging()

app = FastAPI(title=API_TITLE, version=API_VERSION)
app.include_router(create_router(MarketplaceHandlers(), authenticate, require_roles))
register_exception_handlers(app)
app.add_middleware(RequestLoggingMiddleware)

# /openapi.json and /docs serve the hand-written contract itself, not FastAPI's reconstruction of it.
with open(settings.openapi_path, encoding="utf-8") as spec_file:
    _contract = yaml.safe_load(spec_file)
app.openapi = lambda: _contract
