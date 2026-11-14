import os
import selectors
import signal
import subprocess
import sys
import textwrap
from time import monotonic

import pytest


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_worker_consumer_stops_cleanly_without_network_services(stop_signal: int) -> None:
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "COMPASS_WORKER_TOKEN": "isolated-process-token",
        "COMPASS_WORKER_ID": "worker-process-test",
        "COMPASS_RUNTIME_LEASE_SECONDS": "15",
        "AGENT_API_URL": "http://agent.invalid",
    }
    entrypoint = textwrap.dedent(
        """\
        import httpx
        from compass_worker import main
        from compass_worker.run_consumer import RunConsumer

        def respond(request):
            assert request.url.host == "agent.invalid"
            assert request.url.path == "/internal/runtime/jobs/claim"
            assert request.headers["Authorization"] == "Bearer isolated-process-token"
            return httpx.Response(204)

        main.RunConsumer = lambda settings: RunConsumer(
            settings, transport=httpx.MockTransport(respond)
        )
        main.main()
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", entrypoint],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        assert process.stdout is not None
        startup = b""
        deadline = monotonic() + 10
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"Worker queue idle" not in startup:
                remaining = deadline - monotonic()
                assert remaining > 0, "Worker did not consume the isolated queue"
                assert selector.select(timeout=remaining), "Worker did not become ready"
                chunk = os.read(process.stdout.fileno(), 4096)
                assert chunk, startup.decode()
                startup += chunk
        process.send_signal(stop_signal)
        output, _ = process.communicate(timeout=10)
        transcript = (startup + output).decode()
        assert process.returncode == 0, transcript
        assert "Worker consumer started; worker=worker-process-test boot=" in transcript
        assert "Worker queue idle" in transcript
        assert "Worker stopped" in transcript
        assert "isolated-process-token" not in transcript
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
