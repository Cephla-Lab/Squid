"""FastAPI app factory for the Squid Core Service."""

import secrets

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from squid_service import faults as F
from squid_service.config import ServiceConfig
from squid_service.rest.routers import build_routers
from squid_service.rest.sse import build_sse_router

OPEN_PATHS = {"/v1/healthz", "/v1/system/auth_status", "/openapi.json", "/docs", "/redoc"}


def create_app(service, config: ServiceConfig) -> FastAPI:
    app = FastAPI(title="Squid Core Service", version="1.0.0")
    app.state.service = service
    app.state.config = config

    @app.exception_handler(F.FaultError)
    async def fault_handler(request: Request, exc: F.FaultError):
        return JSONResponse(status_code=F.http_status_for(exc.fault), content={"error": exc.fault.model_dump()})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fault = F.make_fault(
            F.FaultCategory.PROTOCOL,
            F.PROTOCOL_SCHEMA_VIOLATION,
            "Request schema violation",
            detail={"errors": jsonable_encoder(exc.errors())},
        )
        return JSONResponse(status_code=422, content={"error": fault.model_dump()})

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if config.auth_enabled and request.url.path not in OPEN_PATHS:
            header = request.headers.get("authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            if not (token and secrets.compare_digest(token, config.auth_token)):
                fault = F.make_fault(F.FaultCategory.PROTOCOL, F.PROTOCOL_AUTH, "Missing or invalid bearer token")
                return JSONResponse(status_code=401, content={"error": fault.model_dump()})
        return await call_next(request)

    for router in build_routers():
        app.include_router(router)
    app.include_router(build_sse_router())
    return app
