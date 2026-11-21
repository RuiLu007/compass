"""Business-facing starter cards have a purpose allowlist separate from prompt administration."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from compass_agent.api.models import Context
from compass_agent.database import get_session
from compass_agent.models.errors import ModelError
from compass_agent.promptops.starter_catalog import starter_cards
from compass_agent.schemas import PromptStarterCards

router = APIRouter(tags=["prompt-starters"])


@router.get("/v1/prompt-starters", response_model=PromptStarterCards)
def read_starters(
    request: Request, context: Context, session: Annotated[Session, Depends(get_session)]
):
    if request.query_params:
        raise ModelError("validation_failed", 422)
    return starter_cards(session, context)
