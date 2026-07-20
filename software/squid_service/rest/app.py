"""FastAPI app factory for the Squid Core Service."""

import secrets

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import squid.logging
from squid_service import faults as F
from squid_service.config import ServiceConfig
from squid_service.rest.routers import build_routers
from squid_service.rest.sse import build_sse_router

OPEN_PATHS = {"/v1/healthz", "/v1/system/auth_status", "/openapi.json", "/docs", "/redoc"}

_log = squid.logging.get_logger("squid_service.rest.app")


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

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Turn an unmatched route (404) into a canonical PROTOCOL_UNKNOWN_RESOURCE
        # fault so scheduler clients get the same {"error": Fault} envelope everywhere.
        if exc.status_code == 404:
            fault = F.make_fault(
                F.FaultCategory.PROTOCOL,
                F.PROTOCOL_UNKNOWN_RESOURCE,
                "Resource not found",
                detail={"path": request.url.path},
            )
            return JSONResponse(status_code=404, content={"error": fault.model_dump()})
        # Other HTTP errors (405, etc.) fall back to FastAPI's default handler.
        return await default_http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        # Any exception that escaped the service layer (not a FaultError): log the
        # full traceback server-side, but return a sanitized canonical fault with a
        # fixed message (URS API-ERR-003 -- never leak str(exc)/internals to clients).
        _log.exception("Unhandled exception handling %s %s", request.method, request.url.path)
        fault = F.make_fault(
            F.FaultCategory.HARDWARE_FAULT,
            F.HARDWARE_FAULT_INTERNAL,
            "Internal server error",
        )
        fault_log = getattr(getattr(request.app.state, "service", None), "fault_log", None)
        if fault_log is not None:
            try:
                fault = fault_log.record(fault)
            except Exception:
                _log.exception("failed to record internal-error fault")
        return JSONResponse(status_code=F.http_status_for(fault), content={"error": fault.model_dump()})

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
