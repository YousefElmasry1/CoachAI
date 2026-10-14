"""Thin FastAPI routes over principal-bound CoachAI application services."""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, Header, Path, Query

from api_models import ErrorResponse, TaskListResponse, TaskResponse, TaskUpdateRequest
from domain_validation import MAX_DATABASE_INTEGER, MAX_TASK_LIST_OFFSET


_ERROR_RESPONSES = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def create_task_router(application_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["tasks"])

    @router.get(
        "/tasks",
        response_model=TaskListResponse,
        responses=_ERROR_RESPONSES,
    )
    def list_tasks(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=MAX_TASK_LIST_OFFSET)] = 0,
        application=Depends(application_dependency),
    ):
        return application.tasks.list_tasks(limit=limit, offset=offset)

    @router.patch(
        "/tasks/{task_id}",
        response_model=TaskResponse,
        responses=_ERROR_RESPONSES,
    )
    def update_task(
        request: TaskUpdateRequest,
        task_id: Annotated[int, Path(gt=0, le=MAX_DATABASE_INTEGER)],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        application=Depends(application_dependency),
    ):
        return application.tasks.update_task(
            task_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            changes=request.changes(),
        )

    return router
