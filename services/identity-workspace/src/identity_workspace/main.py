"""FastAPI application factory and database resource lifecycle."""

from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from typing import TextIO

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import iter_route_contexts
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool

from identity_workspace.config import Settings, load_settings
from identity_workspace.auth.google import GoogleTokenVerifier
from identity_workspace.auth.routes import router as auth_router
from identity_workspace.auth.service import AuthService
from identity_workspace.auth.store import AuthStore
from identity_workspace.database import DatabaseResources, create_database
from identity_workspace.health import router as health_router
from identity_workspace.errors import APIError, api_error_handler, http_error_handler, validation_error_handler
from identity_workspace.http_contract import ContractHTTPMiddleware
from identity_workspace.observability import Observability, router as metrics_router
from identity_workspace.memberships.routes import router as membership_router
from identity_workspace.memberships.service import MembershipService
from identity_workspace.memberships.store import MembershipStore
from identity_workspace.invitations.routes import router as invitation_router
from identity_workspace.invitations.service import InvitationService
from identity_workspace.invitations.store import InvitationStore
from identity_workspace.private_api.credentials import ServiceKeyRegistry
from identity_workspace.private_api.routes import router as private_router
from identity_workspace.private_api.service import PrivateIdentityService
from identity_workspace.private_api.store import PrivateIdentityStore
from identity_workspace.workspaces.routes import router as workspace_router
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import WorkspaceStore


class ApplicationLifecycleError(RuntimeError):
    """A credential-safe failure for server-managed lifespan tracebacks."""


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: Callable[[Settings], DatabaseResources] = create_database,
    log_stream: TextIO | None = None,
    google_verifier_factory=GoogleTokenVerifier,
    auth_store_factory=AuthStore,
    workspace_store_factory=WorkspaceStore,
    membership_store_factory=MembershipStore,
    invitation_store_factory=InvitationStore,
    private_store_factory=PrivateIdentityStore,
) -> FastAPI:
    app_settings = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        startup_failed = False
        database = verifier = None
        try:
            database = await run_in_threadpool(database_factory, app_settings)
            verifier = google_verifier_factory(app_settings)
            app.state.auth = AuthService(auth_store_factory(database), verifier, app_settings)
            app.state.workspaces = WorkspaceService(workspace_store_factory(database))
            app.state.memberships = MembershipService(membership_store_factory(database))
            app.state.invitations = InvitationService(invitation_store_factory(database))
            app.state.private_identity = PrivateIdentityService(private_store_factory(database), ServiceKeyRegistry(app_settings))
        except Exception:
            for resource in (verifier, database):
                if resource is not None:
                    try:
                        await run_in_threadpool(resource.close)
                    except Exception:
                        pass
            app.state.auth = None
            app.state.workspaces = None
            app.state.memberships = None
            app.state.invitations = None
            app.state.private_identity = None
            app.state.observability.log.event(logging.ERROR, "application_startup_failed", error_category="startup_failed")
            app.state.observability.close()
            startup_failed = True
        if startup_failed:
            raise ApplicationLifecycleError("Identity application startup failed.") from None
        app.state.database = database
        app.state.observability.reset_readiness()
        app.state.observability.log.event(logging.INFO, "application_started")
        try:
            yield
        finally:
            shutdown_failed = False
            try:
                for resource in (verifier, database):
                    try:
                        await run_in_threadpool(resource.close)
                    except Exception:
                        app.state.observability.log.event(logging.ERROR, "application_shutdown_failed", error_category="shutdown_failed")
                        shutdown_failed = True
            finally:
                app.state.auth = None
                app.state.workspaces = None
                app.state.memberships = None
                app.state.invitations = None
                app.state.private_identity = None
                app.state.database = None
                app.state.observability.log.event(logging.INFO, "application_stopped")
                app.state.observability.close()
            if shutdown_failed:
                raise ApplicationLifecycleError("Identity application shutdown failed.") from None

    app = FastAPI(
        title="Identity & Workspace Service",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = app_settings
    app.state.database = None
    app.state.auth = None
    app.state.workspaces = None
    app.state.memberships = None
    app.state.invitations = None
    app.state.private_identity = None
    app.router.redirect_slashes = False
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(auth_router)
    app.include_router(workspace_router)
    app.include_router(membership_router)
    app.include_router(invitation_router)
    app.include_router(private_router)

    def route_names():
        return {
            route.path_format for route in iter_route_contexts(app.router.routes)
            if isinstance(route.path_format, str)
        }

    app.state.observability = Observability(app_settings.log_level, route_names, stream=log_stream)
    app.add_middleware(
        ContractHTTPMiddleware, routes=app.router.routes,
        allowed_origins=app_settings.allowed_origins,
        observability=app.state.observability,
    )
    return app
