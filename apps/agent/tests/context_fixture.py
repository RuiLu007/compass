"""Factory publication used by integration fixtures that accept new managed Runs."""

from compass_agent.promptops.service import bootstrap
from sqlalchemy.orm import Session


def install_prompts(engine):
    with Session(engine) as tx, tx.begin():
        bootstrap(tx)
    return engine


async def capability_grants(context):
    return {"analysis": True, "model": True, "plugins": True}
