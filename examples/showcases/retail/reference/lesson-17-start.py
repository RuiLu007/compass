"""Restart the fixed-port Lesson 17 preview with the configured Agent and Retail."""

import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from secrets import token_urlsafe

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work/lesson-17-try"
PG_DATA = ROOT / "work/lesson-17-implementation/pg"
STATE = WORK / "processes.json"
HOST = "127.0.0.1"
WEB_PORT = 8118
AGENT_PORT = 8017
PG_PORT = 55617
WEB_ORIGIN = f"http://{HOST}:{WEB_PORT}"
AGENT_ORIGIN = f"http://{HOST}:{AGENT_PORT}"
SCOPE = {
    "tenantId": "99500000-0000-4000-8000-000000000010",
    "workspaceId": "99500000-0000-4000-8000-000000000020",
}
ANALYSIS_PATH = "/t/yuanshan/w/zongbu/analysis"
WEB_PASSWORD = "teaching-995-only-password"
PG_ADMIN_PASSWORD = "local-987-admin"
PG_ADMIN = f"postgresql://compass_test_admin:{PG_ADMIN_PASSWORD}@{HOST}:{PG_PORT}/postgres"
NEXT = ROOT / "apps/web/node_modules/next/dist/bin/next"
# Run bounds visible model messages to 192 KiB. Leave 64 KiB for tool schemas and framing.
PREVIEW_INPUT_TOKENS = 262144


def preview_env() -> dict[str, str]:
    required = (
        "DATABASE_URL",
        "AGENT_MIGRATION_DATABASE_URL",
        "COMPASS_AUTONOMY_MODEL_PROFILES",
        "COMPASS_MODEL_ENCRYPTION_KEY",
    )
    if missing := [name for name in required if not os.environ.get(name)]:
        raise RuntimeError("Configure the root .env values: " + ", ".join(missing))
    profiles = json.loads(os.environ["COMPASS_AUTONOMY_MODEL_PROFILES"])
    for profile in profiles:
        profile["input_tokens"] = min(profile["input_tokens"], PREVIEW_INPUT_TOKENS)
    with psycopg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1),
        connect_timeout=4,
    ) as connection:
        if not connection.execute(
            "SELECT 1 FROM model_provider WHERE enabled AND chat_visible LIMIT 1"
        ).fetchone():
            raise RuntimeError("Configure an enabled Chat model in the Agent database.")
    web_database = f"{HOST}:{PG_PORT}/compass_web_test_workflow_browser"
    return {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "WEB_TEST_DATABASE_URL": (f"postgresql://compass_web_runtime:local-987-web@{web_database}"),
        "WEB_TEST_MIGRATION_DATABASE_URL": (
            f"postgresql://compass_web_migrator:local-987-web-migration@{web_database}"
        ),
        "WEB_TEST_ADMIN_URL": PG_ADMIN,
        "WEB_TEST_PASSWORD": WEB_PASSWORD,
        "WEB_PUBLIC_ORIGIN": WEB_ORIGIN,
        "WEB_AUTH_ALLOW_LOCAL_HTTP": "true",
        "WEB_TEST_ISOLATED_BUILD": "true",
        "AGENT_API_URL": AGENT_ORIGIN,
        "COMPASS_AGENT_SERVICE_TOKEN": token_urlsafe(32),
        "COMPASS_HYBRID_WORKFLOW_ENABLED": "true",
        "COMPASS_CONTROLLED_AUTONOMY_ENABLED": "true",
        "COMPASS_AUTONOMY_MODEL_PROFILES": json.dumps(profiles),
        "COMPASS_WORKFLOW_BUDGET_TENANT_ID": SCOPE["tenantId"],
        "COMPASS_WORKFLOW_BUDGET_WORKSPACE_ID": SCOPE["workspaceId"],
        "COMPASS_OBSERVABILITY_ENABLED": "false",
        "COMPASS_DEMO_ENABLED": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    }


def run(*command: str, env: dict[str, str], cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def groups() -> dict[int, dict[int, str]]:
    output = subprocess.check_output(["ps", "-axo", "pid=,pgid=,stat=,lstart="], text=True)
    result: dict[int, dict[int, str]] = {}
    for row in output.splitlines():
        pid, group, status, stamp = row.split(maxsplit=3)
        if not status.startswith("Z"):
            result.setdefault(int(group), {})[int(pid)] = stamp
    return result


def stop_group(name: str, identity: dict, timeout: float = 8) -> None:
    pid = identity["pid"]
    if type(pid) is not int or pid <= 1:
        raise RuntimeError(f"Invalid {name} process identity in {STATE}")
    original = groups().get(pid, {})
    known = {str(pid): identity["started"], **identity.get("members", {})}
    if not any(known.get(str(member)) == stamp for member, stamp in original.items()):
        return

    def still_owned() -> bool:
        current = groups().get(pid, {})
        return any(current.get(member) == stamp for member, stamp in original.items())

    print(f"Stopping previous {name} process group {pid}.", flush=True)
    for stop_signal, duration in ((signal.SIGTERM, timeout), (signal.SIGKILL, 3)):
        if not still_owned():
            return
        try:
            os.killpg(pid, stop_signal)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not still_owned():
                return
            time.sleep(0.1)
    raise RuntimeError(f"Previous {name} process group {pid} did not exit.")


def stop_database(env: dict[str, str]) -> None:
    if not (PG_DATA / "PG_VERSION").is_file():
        return
    result = subprocess.run(
        ["pg_ctl", "-D", str(PG_DATA), "status"], stdout=subprocess.DEVNULL, check=False
    )
    if result.returncode == 0:
        run("pg_ctl", "-D", str(PG_DATA), "-m", "fast", "-w", "-t", "20", "stop", env=env)
    elif result.returncode != 3:
        raise RuntimeError(f"Cannot check PostgreSQL in {PG_DATA}.")


def ensure_free_ports() -> None:
    for name, port in (("Web", WEB_PORT), ("Agent", AGENT_PORT), ("PostgreSQL", PG_PORT)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((HOST, port))
            except OSError as error:
                raise RuntimeError(
                    f"{name} port {port} is occupied. Check its process and retry."
                ) from error


def start_database(env: dict[str, str]) -> None:
    if not (PG_DATA / "PG_VERSION").is_file():
        PG_DATA.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=WORK) as password:
            password.write(PG_ADMIN_PASSWORD + "\n")
            password.flush()
            run(
                "initdb",
                "-D",
                str(PG_DATA),
                "-U",
                "compass_test_admin",
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
                "--pwfile",
                password.name,
                "--locale=C",
                "-E",
                "UTF8",
                env=env,
            )
    run(
        "pg_ctl",
        "-D",
        str(PG_DATA),
        "-l",
        str(WORK / "postgresql.log"),
        "-o",
        f"-h {HOST} -p {PG_PORT} -k /tmp",
        "-w",
        "-t",
        "20",
        "start",
        env=env,
    )


def prepare_databases(env: dict[str, str]) -> None:
    run(sys.executable, "apps/agent/scripts/database.py", "migrate", env=env)
    run(sys.executable, "-m", "compass_agent.seed", env=env)
    run("node", "apps/web/scripts/test-setup.mjs", "--fixture", "teaching", env=env)


def wait_ready(origin: str, child: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + 35
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            try:
                if client.get(origin + "/readyz").status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.2)
    raise RuntimeError(f"{origin} did not reach readiness. See {log}.")


def verify_login() -> None:
    with httpx.Client(
        base_url=WEB_ORIGIN, trust_env=False, timeout=8, follow_redirects=True
    ) as client:
        client.post(
            "/api/web/v1/auth/login",
            headers={"Origin": WEB_ORIGIN},
            json={"loginName": "teaching.admin", "password": WEB_PASSWORD},
        ).raise_for_status()
        page = client.get(ANALYSIS_PATH).raise_for_status()
        if page.url.path != ANALYSIS_PATH:
            raise RuntimeError("The authenticated analysis page did not open.")
        models = client.get("/api/web/v1/models/available", params={**SCOPE, "view": "chat"})
        models.raise_for_status()
        if not models.json()["items"]:
            raise RuntimeError("The demo model catalog is empty.")
        client.get("/api/web/v1/analysis/workflows", params=SCOPE).raise_for_status()


def restart() -> None:
    for program in ("ps", "node", "npm", "pg_ctl", "initdb"):
        if not shutil.which(program):
            raise RuntimeError(f"Required program missing: {program}")
    if not NEXT.is_file():
        raise RuntimeError("Install workspace dependencies with pnpm install --frozen-lockfile.")
    env = preview_env()
    env["WEB_DATABASE_URL"] = env["WEB_TEST_DATABASE_URL"]
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    for name in ("web", "agent"):
        if name in previous:
            stop_group(name, previous[name])
    STATE.unlink(missing_ok=True)
    stop_database(env)
    ensure_free_ports()

    if not (ROOT / "apps/agent/workflow-runner/node_modules/quickjs-emscripten").is_dir():
        run("npm", "ci", "--prefix", "apps/agent/workflow-runner", "--ignore-scripts", env=env)
    start_database(env)
    started = {}
    temporary = STATE.with_suffix(".tmp")
    try:
        prepare_databases(env)
        run("node", str(NEXT), "build", cwd=ROOT / "apps/web", env=env)
        services = [
            (
                "agent",
                AGENT_ORIGIN,
                ROOT,
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "compass_agent.main:app",
                    "--host",
                    HOST,
                    "--port",
                    str(AGENT_PORT),
                ],
            ),
            (
                "web",
                WEB_ORIGIN,
                ROOT / "apps/web",
                [
                    "node",
                    str(NEXT),
                    "start",
                    "--hostname",
                    HOST,
                    "--port",
                    str(WEB_PORT),
                ],
            ),
        ]
        for name, origin, cwd, command in services:
            log = WORK / f"{name}.log"
            with log.open("a") as stream:
                child = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            identity = groups().get(child.pid, {}).get(child.pid)
            if not identity:
                raise RuntimeError(f"{name} exited during startup. See {log}.")
            started[name] = {"pid": child.pid, "started": identity}
            temporary.write_text(json.dumps(started))
            temporary.replace(STATE)
            wait_ready(origin, child, log)
            started[name]["members"] = {
                str(member): stamp for member, stamp in groups().get(child.pid, {}).items()
            }
        verify_login()
        temporary.write_text(json.dumps(started))
        temporary.replace(STATE)
    except BaseException:
        for name, identity in reversed(started.items()):
            stop_group(name, identity)
        STATE.unlink(missing_ok=True)
        stop_database(env)
        raise
    print(f"Lesson 17 is ready at {WEB_ORIGIN}{ANALYSIS_PATH}", flush=True)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / "startup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restart()


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        subprocess.CalledProcessError,
        httpx.HTTPError,
        psycopg.Error,
    ) as exc:
        raise SystemExit(f"Lesson 17 startup failed: {exc}") from None
