"""Thin HTTP transport over CoachAI's principal-bound application services.

Workers remain separate processes. HTTP routes authenticate once, use a
request-scoped unit of work, and delegate domain behavior to the existing
application-service boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from threading import Lock
from typing import Any, Callable, Mapping
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from backend_config import BackendSettings, cors_allowed_origins as configured_cors_origins
from backend_errors import BackendError, ErrorCode
from operational_health import (
    HealthCheckResult,
    HealthRegistry,
    HealthStatus,
    database_ping_check,
)
from identity import TokenVerifier
from api_auth import FirebaseBearerAuthenticator
from api_dependencies import RequestUnitOfWorkProvider
from api_routes import create_task_router


logger = logging.getLogger("coachai.api")
ReadinessCheck = Callable[[], Mapping[str, Any]]


_ERROR_STATUS = {
    ErrorCode.AUTHENTICATION_REQUIRED: 401,
    ErrorCode.AUTHORIZATION_DENIED: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.TENANT_MISMATCH: 403,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.SCHEDULING_CONFLICT: 409,
    ErrorCode.CAPACITY_EXCEEDED: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.EXTERNAL_PROVIDER_FAILURE: 502,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CONFIGURATION_ERROR: 503,
}


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", uuid4().hex)


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "details": dict(details or {}),
            "correlation_id": _correlation_id(request),
        },
        headers=dict(headers or {}),
    )


def _safe_health_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Keep probe output useful without returning exception or config details."""
    checks = snapshot.get("checks", {})
    public_checks: dict[str, dict[str, str]] = {}
    if isinstance(checks, Mapping):
        for name, result in checks.items():
            if not isinstance(name, str) or not isinstance(result, Mapping):
                continue
            public_checks[name] = {
                "status": str(result.get("status", HealthStatus.UNHEALTHY.value)),
                "summary": str(result.get("summary", "Health check failed.")),
            }
    return {
        "status": str(snapshot.get("status", HealthStatus.UNHEALTHY.value)),
        "checks": public_checks,
    }


class ApiReadinessProbe:
    """Validate mandatory configuration, PostgreSQL connectivity, and schema."""

    def __init__(self) -> None:
        self._database_factory: Any | None = None
        self._lock = Lock()

    def _configuration(self) -> HealthCheckResult:
        try:
            settings = BackendSettings.from_env()
            if make_url(settings.database_url).get_backend_name() != "postgresql":
                raise ValueError("The API runtime requires PostgreSQL.")
        except Exception:
            return HealthCheckResult(
                HealthStatus.UNHEALTHY,
                "Runtime configuration is invalid.",
            )
        return HealthCheckResult(
            HealthStatus.HEALTHY,
            "Runtime configuration is valid.",
        )

    def _factory(self):
        with self._lock:
            if self._database_factory is None:
                settings = BackendSettings.from_env()
                database_url = make_url(settings.database_url)
                if database_url.get_backend_name() != "postgresql":
                    raise ValueError("The API runtime requires PostgreSQL.")
                database_url = database_url.update_query_dict(
                    {"connect_timeout": str(settings.external_timeout_seconds)}
                )
                # Importing the database layer is deliberately deferred so a
                # broken dependency cannot prevent the liveness route loading.
                from unit_of_work import DatabaseFactory

                self._database_factory = DatabaseFactory(database_url)
            return self._database_factory

    def _database(self) -> HealthCheckResult:
        def ping() -> None:
            from sqlalchemy import text

            factory = self._factory()
            with factory.engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
            # DatabaseFactory's first checkout validates the Alembic head.
            db = factory()
            db.close()

        return database_ping_check(ping)

    def __call__(self) -> Mapping[str, Any]:
        registry = HealthRegistry()
        registry.register("configuration", self._configuration)
        registry.register("database", self._database)
        return registry.readiness()

    def close(self) -> None:
        with self._lock:
            if self._database_factory is not None:
                self._database_factory.close()
                self._database_factory = None


def create_app(
    *,
    readiness_check: ReadinessCheck | None = None,
    token_verifier: TokenVerifier | None = None,
    database_factory: Callable[[], Any] | None = None,
    cors_allowed_origins: tuple[str, ...] | None = None,
) -> FastAPI:
    """Build the CoachAI FastAPI application."""
    probe = ApiReadinessProbe() if readiness_check is None else None
    check = readiness_check or probe
    if database_factory is None and probe is not None:
        database_factory = lambda: probe._factory()()
    request_units = RequestUnitOfWorkProvider(database_factory)
    authenticate = FirebaseBearerAuthenticator(token_verifier)

    def application_dependency(
        principal=Depends(authenticate),
        unit_of_work=Depends(request_units),
    ):
        from application_services import CoachAIApplication
        from identity import get_or_create_user_from_principal

        if unit_of_work.db is None:
            raise RuntimeError("The request unit of work is unavailable.")
        get_or_create_user_from_principal(unit_of_work.db, principal)
        return CoachAIApplication(unit_of_work.db, principal)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        request_units.close()
        if probe is not None:
            probe.close()

    app = FastAPI(
        title="CoachAI API",
        version="0.2.0",
        lifespan=lifespan,
    )
    origins = (
        configured_cors_origins()
        if cors_allowed_origins is None
        else configured_cors_origins(",".join(cors_allowed_origins))
    )
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_credentials=False,
            allow_methods=["GET", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            expose_headers=["X-Correlation-ID"],
        )
    app.state.authenticate = authenticate
    app.state.unit_of_work = request_units
    app.state.application = application_dependency
    app.include_router(create_task_router(application_dependency))

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        request.state.correlation_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(BackendError)
    async def backend_error_handler(request: Request, exc: BackendError):
        status_code = _ERROR_STATUS.get(exc.code, 500)
        headers = None
        if exc.code == ErrorCode.RATE_LIMITED and exc.details:
            retry_after = exc.details.get("retry_after_seconds")
            if isinstance(retry_after, int) and retry_after >= 0:
                headers = {"Retry-After": str(retry_after)}
        return _error_response(
            request,
            status_code=status_code,
            code=exc.code.value,
            message=exc.message,
            details=exc.details,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _exc: RequestValidationError):
        return _error_response(
            request,
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR.value,
            message="The request contains an invalid value.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            code = ErrorCode.RESOURCE_NOT_FOUND.value
            message = "The requested resource was not found."
        else:
            code = ErrorCode.VALIDATION_ERROR.value
            message = "The request could not be processed."
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.error(
            "api_unexpected_error correlation_id=%s error_type=%s",
            _correlation_id(request),
            type(exc).__name__,
        )
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": HealthStatus.HEALTHY.value}

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        try:
            snapshot = _safe_health_snapshot(check())
        except Exception:
            snapshot = {"status": HealthStatus.UNHEALTHY.value, "checks": {}}
        status_code = 200 if snapshot["status"] == HealthStatus.HEALTHY.value else 503
        return JSONResponse(status_code=status_code, content=snapshot)

    return app


app = create_app()
