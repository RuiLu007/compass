"""Isolate the real Agent, Scheduler, PostgreSQL and Redis for the CI regression."""

import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in HERE.parents if (parent / "apps/agent/pyproject.toml").exists())
POSTGRES = (
    "pgvector/pgvector:pg17@sha256:feb68f4f15446397d8cac7f4fe48fe4586de83160d1fc48b46283312d1a33966"
)
REDIS = (
    "public.ecr.aws/docker/library/redis:8.2.11-alpine@sha256:"
    "fe9daae35cd4fd6ec22416aa4fe0cf6b3e36741e06ff45b9986253fe2a81c041"
)


def wait_until(probe, explanation, *, timeout=20, transient=()):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except transient:
            pass
        time.sleep(0.05)
    raise AssertionError(explanation)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


class IsolatedRuntime:
    def __init__(self, stack: ExitStack, directory: Path):
        self.stack, self.directory = stack, directory
        self.identifier = uuid4().hex[:12]
        self.token = secrets.token_urlsafe(32)
        pg_password, redis_password = secrets.token_hex(16), secrets.token_hex(16)
        pg = self.container(
            "pg",
            POSTGRES,
            5432,
            "--env",
            f"POSTGRES_PASSWORD={pg_password}",
            "--tmpfs",
            "/var/lib/postgresql/data",
        )
        redis_port = self.container(
            "redis",
            REDIS,
            6379,
            "--tmpfs",
            "/data",
            command=(
                "redis-server",
                "--appendonly",
                "yes",
                "--appendfsync",
                "always",
                "--maxmemory-policy",
                "noeviction",
                "--requirepass",
                redis_password,
            ),
        )
        database = f"compass_agent_test_984_{self.identifier}"
        host = f"127.0.0.1:{pg}"
        admin = f"postgresql://postgres:{pg_password}@{host}/postgres"
        self.owner = f"postgresql://postgres:{pg_password}@{host}/{database}"

        def dsn(role):
            return f"postgresql+psycopg://compass_agent_{role}:{secrets.token_hex(16)}@{host}/{database}"

        def ping():
            with psycopg.connect(admin, connect_timeout=1) as connection:
                return connection.execute("SELECT 1").fetchone()

        wait_until(ping, "PostgreSQL failed readiness", transient=(psycopg.OperationalError,))
        redis_url = f"redis://127.0.0.1:{redis_port}/0"
        with Redis.from_url(redis_url, password=redis_password) as redis:
            wait_until(redis.ping, "Redis failed readiness", transient=(RedisConnectionError,))
        self.environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join((str(HERE), str(ROOT))),
            "PYTHONUNBUFFERED": "1",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "DATABASE_URL": dsn("runtime"),
            "AGENT_MIGRATION_DATABASE_URL": dsn("migrator"),
            "AGENT_PROVISION_DATABASE_URL": admin,
            "COMPASS_SCHEDULER_DATABASE_URL": dsn("scheduler"),
            "COMPASS_SCHEDULER_TOKEN": secrets.token_urlsafe(32),
            "COMPASS_AGENT_SERVICE_TOKEN": self.token,
            "COMPASS_MODEL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "COMPASS_RUNTIME_STATE_KEY": Fernet.generate_key().decode(),
            "COMPASS_REDIS_URL": redis_url,
            "COMPASS_REDIS_PASSWORD": redis_password,
            "COMPASS_RUNTIME_MAX_ACTIVE": "2",
            "COMPASS_RUNTIME_TENANT_ACTIVE": "1",
            "COMPASS_RUNTIME_MAX_BACKLOG": "12",
            "COMPASS_RUNTIME_TENANT_BACKLOG": "4",
            "COMPASS_RUNTIME_CONSUMER_SLOTS": "1",
            "COMPASS_OBSERVABILITY_ENABLED": "false",
            "COMPASS_TEST_RUNTIME_DIRECTORY": str(directory),
        }
        for task in ("provision", "migrate"):
            subprocess.run(
                [sys.executable, "apps/agent/scripts/database.py", task],
                cwd=ROOT,
                env=self.environment,
                check=True,
                timeout=40,
            )

    def container(self, role, image, port, *options, command=()):
        name = f"compass-ci-runtime-{self.identifier}-{role}"
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--publish",
                f"127.0.0.1::{port}",
                *options,
                image,
                *command,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=45,
        )
        self.stack.callback(
            subprocess.run,
            ["docker", "rm", "--force", "--volumes", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        address = subprocess.check_output(["docker", "port", name, f"{port}/tcp"], text=True)
        return address.strip().split(":")[-1]

    def spawn(self, label, *command, **settings):
        log = self.stack.enter_context((self.directory / f"{label}.log").open("wb"))
        process = subprocess.Popen(
            [sys.executable, "-m", *command],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**self.environment, **settings},
        )
        self.stack.callback(stop, process)
        return process

    def http(self, label, application, *, factory=False, **settings):
        port = free_port()
        options = ["--factory"] if factory else []
        process = self.spawn(
            label,
            "uvicorn",
            application,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            *options,
            **settings,
        )
        return f"http://127.0.0.1:{port}", process
