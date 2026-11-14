"""Run one authenticated consumer slot and drain it on process signals."""

import argparse
import asyncio
import logging
import signal
import sys

from compass_worker.run_consumer import ConsumerSettings, RunConsumer

logger = logging.getLogger("compass_worker")


async def serve() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    consumer = RunConsumer(ConsumerSettings.from_env())
    try:
        await consumer.run(stop)
    finally:
        await consumer.close()
        for sig in signals:
            loop.remove_signal_handler(sig)
        logger.info("Worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compass asynchronous Run consumer")
    parser.add_argument("--version", action="version", version="compass-worker 0.1.0")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(serve())
