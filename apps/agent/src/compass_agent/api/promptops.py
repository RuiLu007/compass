"""Private administration endpoints. The Web BFF supplies a freshly authorized identity."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from compass_agent import schemas as dto
from compass_agent.api.models import Context, require
from compass_agent.database import get_engine
from compass_agent.promptops import service
from compass_agent.promptops.catalog import normalize_path
from compass_agent.promptops.template import render, validate_template


def admin(context: Context, request: Request) -> UUID:
    require(context, {"promptops_manage"}, "platform")
    request.state.prompt_context = context
    return context.actor_user_id


router = APIRouter(
    prefix="/v1/promptops",
    tags=["promptops"],
    dependencies=[Depends(admin)],
    responses={
        status: {"model": dto.ModelErrorResponse} for status in (401, 403, 404, 409, 413, 422, 503)
    },
)
Actor = Annotated[UUID, Depends(admin)]
Cursor = Annotated[str | None, Query(max_length=512)]
Limit = Annotated[int, Query(ge=1, le=200)]
PathParam = Annotated[str, Query(min_length=1, max_length=512)]


def read(fn, *args):
    with Session(get_engine()) as tx:
        return fn(tx, *args)


def write(request, actor, name, data, fn):
    observer = getattr(request.app.state, "observation", None)
    root = None
    if observer and observer.tracer:
        root = observer.root(
            data.request_id, request.state.prompt_context, request.headers, capture_content=False
        )
        request.state.model_observation = root
        root.set(stage=name)
    try:
        with Session(get_engine()) as tx, tx.begin():
            result = service.mutate(tx, actor, name, data, fn)
        if root:
            root.set(
                generation=result.get("generation"),
                prompt_version=result.get("version"),
                snapshot_no=result.get("snapshot_no"),
                changed_count=result.get("changed_count"),
            )
            root.complete("completed")
        return result
    except Exception as error:
        if root:
            root.complete("failed", getattr(error, "code", "internal_error"))
        raise
    finally:
        if root:
            root.end()


@router.get("/tree", response_model=dto.PromptTree)
def tree(
    prefix: Annotated[str, Query(max_length=512)] = "",
    q: Annotated[str, Query(max_length=200)] = "",
    cursor: Cursor = None,
    limit: Limit = 100,
):
    return read(service.tree, prefix, q, cursor, limit)


@router.get("/file", response_model=dto.PromptFileDetail)
def file(path: PathParam, version: Annotated[int | None, Query(gt=0)] = None):
    return read(service.file_detail, path, version)


@router.get("/history", response_model=dto.PromptHistory)
def history(path: PathParam, cursor: Cursor = None, limit: Limit = 100):
    return read(service.history, path, cursor, limit)


@router.get("/snapshots", response_model=dto.PromptSnapshots)
def snapshots(cursor: Cursor = None, limit: Limit = 100):
    return read(service.snapshots, cursor, limit)


@router.get("/snapshot", response_model=dto.PromptSnapshot)
def snapshot(snapshot_no: Annotated[int, Query(gt=0)], cursor: Cursor = None, limit: Limit = 100):
    return read(service.snapshot, snapshot_no, cursor, limit)


@router.get("/commands", response_model=dto.PromptReceipt)
def commands(actor: Actor, request_id: UUID):
    return read(service.command, actor, request_id)


@router.post("/file-draft", response_model=dto.PromptReceipt)
def draft(request: Request, actor: Actor, data: dto.PromptDraft):
    return write(request, actor, "file-draft", data, service.save_draft)


@router.post("/draft-from-version", response_model=dto.PromptReceipt)
def draft_from_version(request: Request, actor: Actor, data: dto.PromptDraftFromVersion):
    return write(request, actor, "draft-from-version", data, service.save_draft)


@router.post("/render-preview", response_model=dto.PromptRendered)
def render_preview(data: dto.PromptRenderInput):
    path = normalize_path(data.path)
    return dict(
        rendered=render(path, data.body, data.variables),
        variables=validate_template(path, data.body),
    )


@router.post("/import-preview", response_model=dto.PromptImportPreview)
def import_preview(data: dto.PromptImportPreviewInput):
    return read(service.import_preview, data)


@router.post("/import", response_model=dto.PromptReceipt)
def import_package(request: Request, actor: Actor, data: dto.PromptImport):
    return write(request, actor, "import", data, service.import_package)


@router.post("/release-preview", response_model=dto.PromptReleasePreview)
def release_preview(data: dto.PromptReleaseTarget):
    with Session(get_engine()) as tx:
        return service.release_plan(tx, service.head(tx), data).preview


@router.post("/releases", response_model=dto.PromptReceipt)
def releases(request: Request, actor: Actor, data: dto.PromptRelease):
    return write(request, actor, "releases", data, service.commit_release)
