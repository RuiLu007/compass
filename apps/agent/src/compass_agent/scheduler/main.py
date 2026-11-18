"""Run the Scheduler role using the Agent package and the restricted database login."""

import argparse
import asyncio
import logging
import signal

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from compass_agent.models.errors import ModelError
from compass_agent.scheduler.client import RunAdmissionClient
from compass_agent.scheduler.lease import SchedulerLeaseStore
from compass_agent.scheduler.repository import ScheduleRepository
from compass_agent.scheduler.runner import SchedulerRunner
from compass_agent.settings import get_settings

logger = logging.getLogger(__name__)


async def serve(settings, engine):
    token = settings.compass_scheduler_token.get_secret_value()
    state_key = settings.compass_runtime_state_key.get_secret_value()
    if len(token) < 16 or token == settings.compass_agent_service_token or not state_key:
        raise ModelError("scheduler_configuration_invalid", 503)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    leases = SchedulerLeaseStore(engine)
    repository = ScheduleRepository(engine, state_key)
    client = RunAdmissionClient(settings.compass_scheduler_agent_url, token)
    try:
        return await SchedulerRunner(leases, repository, client).run(stop)
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the live Scheduler lease")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    url = settings.compass_scheduler_database_url.get_secret_value()
    if not url or url == settings.database_url:
        logger.error("scheduler.configuration_invalid")
        return 2
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        if options.check:
            SchedulerLeaseStore(engine).ready()
            return 0
        return asyncio.run(serve(settings, engine))
    except (ModelError, SQLAlchemyError) as error:
        logger.error(
            "scheduler.unavailable reason=%s",
            error.code if isinstance(error, ModelError) else type(error).__name__,
        )
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
