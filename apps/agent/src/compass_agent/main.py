import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from compass_agent.database import DemoSale, get_engine, get_session
from compass_agent.llm.admission import ExecutionGate
from compass_agent.model_api import ModelBoundaryMiddleware, error_response
from compass_agent.model_api import router as model_router
from compass_agent.models.entities import ModelCall, ModelChange, Provider
from compass_agent.models.errors import ModelError
from compass_agent.models.lifecycle import ExecutionTasks
from compass_agent.observability.observer import Observer
from compass_agent.schemas import DemoOverview, Health
from compass_agent.settings import get_settings


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    app.state.execution_gate = ExecutionGate(
        settings.compass_model_max_concurrency, settings.compass_model_max_inflight
    )
    app.state.model_tasks = ExecutionTasks()
    app.state.observation = Observer(settings)
    app.state.showcase_running = False
    try:
        yield
    finally:
        await app.state.model_tasks.close()
        await asyncio.to_thread(app.state.observation.shutdown)


app = FastAPI(
    lifespan=lifespan,
    title="Compass Agent",
    version="0.1.0",
    description="Commerce analysis scaffold",
)


@app.get("/healthz", response_model=Health, operation_id="health")
def health() -> Health:
    return Health()


@app.get("/readyz", response_model=Health, operation_id="readiness")
def readiness() -> Health:
    try:
        with get_engine().connect() as connection:
            revisions = connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            if revisions != ["0004"]:
                raise HTTPException(status_code=503, detail="Database or migration unavailable")
            # Resolve required columns without fetching configuration, credentials or call data.
            for model in (DemoSale, Provider, ModelChange, ModelCall):
                connection.execute(select(model).limit(0))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database or migration unavailable") from exc
    return Health()


@app.get("/v1/demo/overview", response_model=DemoOverview, operation_id="demo_overview")
def demo_overview(
    response: Response, session: Annotated[Session, Depends(get_session)]
) -> DemoOverview:
    # Opt-in fixture endpoint; this never accepts a tenant or a user-supplied query.
    if not get_settings().compass_demo_enabled:
        raise HTTPException(status_code=404, detail="Demo disabled")
    response.headers["Cache-Control"] = "no-store"
    try:
        row = session.execute(
            select(
                func.min(DemoSale.day),
                func.max(DemoSale.day),
                func.sum(DemoSale.revenue_minor),
                func.sum(DemoSale.orders),
                func.count(),
            ).select_from(DemoSale)
        ).one()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Demo database unavailable") from exc
    start, end, revenue, orders, days = row
    if not days:
        raise HTTPException(status_code=503, detail="Run the demo seed first")
    return DemoOverview(
        start_date=start, end_date=end, revenue_minor=revenue, orders=orders, days=days
    )


# Private model endpoints are isolated from the opt-in synthetic demo API.

app.add_middleware(ModelBoundaryMiddleware)
app.include_router(model_router)


@app.exception_handler(ModelError)
async def model_error_handler(request, error):
    return error_response(error)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request, error):
    if request.url.path.startswith("/v1/models"):
        return error_response(ModelError("validation_failed"))
    from fastapi.exception_handlers import request_validation_exception_handler

    return await request_validation_exception_handler(request, error)
