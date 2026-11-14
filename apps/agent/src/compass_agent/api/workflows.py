"""Private analysis-workflow management API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from compass_agent.api.runs import RunContext
from compass_agent.database import get_engine
from compass_agent.schemas import (
    WorkflowAssetDelete,
    WorkflowAssetList,
    WorkflowAssetPatch,
    WorkflowAssetView,
    WorkflowDraftConfirm,
    WorkflowDraftView,
)
from compass_agent.workflows.assets import WorkflowAssets
from compass_agent.workflows.drafts import WorkflowDrafts

router = APIRouter(prefix="/v1/analysis/workflows", tags=["analysis-workflows"])


def available_workflows() -> WorkflowAssets:
    return WorkflowAssets(get_engine())


Assets = Annotated[WorkflowAssets, Depends(available_workflows)]


def get_workflow_drafts(request: Request) -> WorkflowDrafts:
    return request.app.state.workflow_drafts


Drafts = Annotated[WorkflowDrafts, Depends(get_workflow_drafts)]


@router.get("/drafts/{draft_id}", response_model=WorkflowDraftView)
def get_draft(
    draft_id: UUID, context: RunContext, _assets: Assets, drafts: Drafts, response: Response
) -> WorkflowDraftView:
    response.headers["Cache-Control"] = "no-store"
    return drafts.get(context, draft_id)


@router.post("/drafts/{draft_id}/confirm", response_model=WorkflowAssetView)
def confirm_draft(
    draft_id: UUID,
    value: WorkflowDraftConfirm,
    context: RunContext,
    assets: Assets,
    drafts: Drafts,
    response: Response,
) -> WorkflowAssetView:
    response.headers["Cache-Control"] = "no-store"
    return drafts.confirm(context, draft_id, value.name, assets)


@router.get("", response_model=WorkflowAssetList)
def list_assets(
    context: RunContext,
    assets: Assets,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
) -> WorkflowAssetList:
    response.headers["Cache-Control"] = "no-store"
    return assets.list(context, cursor=cursor)


@router.get("/{asset_id}", response_model=WorkflowAssetView)
def get_asset(
    asset_id: UUID, context: RunContext, assets: Assets, response: Response
) -> WorkflowAssetView:
    response.headers["Cache-Control"] = "no-store"
    return assets.get(context, asset_id)


@router.patch("/{asset_id}", response_model=WorkflowAssetView)
def patch_asset(
    asset_id: UUID,
    value: WorkflowAssetPatch,
    context: RunContext,
    assets: Assets,
    response: Response,
) -> WorkflowAssetView:
    response.headers["Cache-Control"] = "no-store"
    return assets.patch(context, asset_id, value)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    asset_id: UUID, value: WorkflowAssetDelete, context: RunContext, assets: Assets
) -> Response:
    assets.delete(context, asset_id, value.metadata_revision)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
