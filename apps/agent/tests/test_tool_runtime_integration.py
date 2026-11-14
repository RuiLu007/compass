"""Lesson 18 PostgreSQL and real OpenSandbox/NFS acceptance; explicit isolated targets only."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from compass_agent.models.errors import ModelError
from compass_agent.schemas import McpConnectionSave, McpToolSelection, TenantScope
from compass_agent.tools.builtin.python_analysis import python_analysis_tool
from compass_agent.tools.contracts import AuthorizedInput, SandboxBinding, ToolRejected
from compass_agent.tools.mcp.repository import McpRepository
from compass_agent.tools.sandbox.allocations import Allocations
from compass_agent.tools.sandbox.client import SandboxBackend
from pydantic import SecretStr
from sqlalchemy import create_engine

from .test_agent_run import CHAT
from .test_tool_dispatcher import context


@pytest.fixture(scope="module")
def engine():
    target = os.environ["AGENT_TEST_DATABASE_URL"]
    if "/compass_agent_test_" not in target:
        pytest.fail("isolated Agent database required")
    value = create_engine(target)
    yield value
    value.dispose()


def owner():
    return uuid4(), uuid4(), uuid4()


def real_allocations(engine):
    return Allocations(
        engine,
        os.environ.get("COMPASS_SANDBOX_TEST_HOST", "lesson18-local"),
        slots=int(os.environ.get("COMPASS_SANDBOX_TEST_SLOTS", "16")),
    )


def real_backend(engine, store=None):
    return SandboxBackend(
        store if store is not None else real_allocations(engine),
        domain=os.environ.get("COMPASS_SANDBOX_TEST_DOMAIN", "127.0.0.1:8112"),
        api_key=os.environ["COMPASS_SANDBOX_API_KEY"],
        image="compass-sandbox-python:lesson18",
        volume_prefix=os.environ.get("COMPASS_SANDBOX_TEST_VOLUME_PREFIX", "compass-sandbox-slot"),
    )


def real_execution(engine):
    ctx = context(
        "python_analysis",
        trusted=CHAT.model_copy(
            update={
                "actor_user_id": uuid4(),
                "scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
            }
        ),
    )
    ctx = replace(
        ctx, call_id="fault-test", sources={"input": AuthorizedInput(ctx.run_id, ctx.owner, {})}
    )
    backend = real_backend(engine)
    return ctx, backend


@pytest.fixture
def network_target():
    import json
    import subprocess
    import time

    image = "compass-sandbox-python:lesson18"
    container = (
        subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--network",
                "bridge",
                "--entrypoint",
                "python",
                "--label",
                "compass.lesson18.test=network",
                image,
                "-m",
                "http.server",
                "18081",
            ]
        )
        .decode()
        .strip()
    )
    try:
        info = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        address = info["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        probe = f"import socket; socket.create_connection(('{address}',18081),2).close()"
        # Positive control from a separate unrestricted container on the same Docker network.
        ready = None
        for _ in range(5):
            ready = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "bridge",
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    probe,
                ],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.1)
        assert ready is not None and ready.returncode == 0, (
            "network fixture must be reachable before testing denial"
        )
        yield address
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, capture_output=True)


async def release_test_volume(engine, backend, identifier, key):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from compass_agent.tools.sandbox.entities import SandboxAllocation
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        session.execute(
            update(SandboxAllocation)
            .where(SandboxAllocation.id == UUID(identifier))
            .values(retain_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    allocation = backend.allocations.get(UUID(identifier), key)
    receipt = await backend._collect(backend.allocations.phase(allocation, "clean"), 30, clean=True)
    assert receipt.status == "ok" and receipt.cleanup == "confirmed", receipt


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_real_container_restrictions_and_chmod_workspace_cleanup(engine, network_target):
    async def exercise():
        ctx, backend = real_execution(engine)
        receipt = await backend.execute(
            SandboxBinding(),
            {
                "source_query_id": "input",
                "code": """
import os, socket
from pathlib import Path
lines = Path('/proc/self/status').read_text().splitlines()
status = dict(line.split(':',1) for line in lines if ':' in line)
try:
    socket.create_connection(('NETWORK_TARGET', 18081), timeout=2).close()
    network = 'reachable'
except OSError:
    network = 'blocked'
Path('/workspace/locked').mkdir()
Path('/workspace/locked/private').write_text('remove during retention cleanup')
os.chmod('/workspace/locked', 0)
result = {'uid': os.getuid(), 'capabilities': status['CapEff'].strip(),
          'noNewPrivileges': status['NoNewPrivs'].strip(), 'network': network,
          'dockerSocket': Path('/var/run/docker.sock').exists()}
""".replace("NETWORK_TARGET", network_target),
            },
            ctx,
        )
        assert receipt.status == "ok" and receipt.payload == {
            "uid": 1000,
            "capabilities": "0000000000000000",
            "noNewPrivileges": "1",
            "network": "blocked",
            "dockerSocket": False,
        }, receipt
        await release_test_volume(engine, backend, receipt.execution_id, ctx.owner)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
@pytest.mark.parametrize("cancel", [True, False])
def test_real_python_cancel_or_cpu_timeout_confirms_compute_deletion(engine, monkeypatch, cancel):
    async def exercise():
        ctx, backend = real_execution(engine)
        created = asyncio.Event()
        original = backend._create

        async def create(*args, **kwargs):
            result = await original(*args, **kwargs)
            created.set()
            return result

        monkeypatch.setattr(backend, "_create", create)
        task = asyncio.create_task(
            backend.execute(
                SandboxBinding(), {"source_query_id": "input", "code": "while True: pass"}, ctx
            )
        )
        await asyncio.wait_for(created.wait(), 35)
        if cancel:
            await asyncio.sleep(0.3)
            task.cancel()
        receipt = await asyncio.wait_for(task, 35)
        assert receipt.status in ({"canceled"} if cancel else {"error", "timeout"}), receipt
        assert receipt.cleanup == "confirmed" and receipt.file is None
        await release_test_volume(engine, backend, receipt.execution_id, ctx.owner)

    asyncio.run(exercise())


@pytest.mark.skipif(
    os.environ.get("COMPASS_SANDBOX_FAULT_TESTS") != "true",
    reason="isolated Docker fault tests require opt-in",
)
@pytest.mark.parametrize("service", ["opensandbox", "nas"])
def test_real_control_restart_and_nas_disconnect_preserve_owner_and_files(engine, service):
    """Pause only the labelled Lesson 18 lab service and always restore it in finally."""

    async def exercise():
        import json
        import subprocess
        from datetime import UTC, datetime, timedelta
        from uuid import UUID

        from compass_agent.tools.sandbox.entities import SandboxAllocation
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        dedicated_nas = os.environ.get("COMPASS_SANDBOX_TEST_NAS_CONTAINER")
        name = (
            dedicated_nas if service == "nas" and dedicated_nas else f"compass-sandbox-{service}-1"
        )
        info = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
        labels = info["Config"]["Labels"]
        if service == "nas" and dedicated_nas:
            assert labels.get("compass.acceptance.owner") == os.environ["COMPASS_SANDBOX_TEST_HOST"]
        else:
            assert labels.get("com.docker.compose.project") == "compass-sandbox"
        ctx, backend = real_execution(engine)
        receipt = await backend.execute(
            SandboxBinding(), {"source_query_id": "input", "code": "result = {'retained': 17}"}, ctx
        )
        assert receipt.status == "ok", receipt
        identifier = UUID(receipt.execution_id)
        if service == "opensandbox":
            await asyncio.to_thread(
                subprocess.run, ["docker", "restart", name], check=True, capture_output=True
            )
            from opensandbox import SandboxManager
            from opensandbox.models.sandboxes import SandboxFilter

            async with asyncio.timeout(30):
                while True:
                    manager = await SandboxManager.create(backend.cleanup_connection)
                    try:
                        await manager.list_sandbox_infos(SandboxFilter(page=1, page_size=1))
                        break
                    except Exception:
                        await asyncio.sleep(0.25)
                    finally:
                        await manager.close()
        else:
            allocation = backend.allocations.phase(
                backend.allocations.get(identifier, ctx.owner), "inspect", ctx.owner
            )
            await asyncio.to_thread(
                subprocess.run, ["docker", "pause", name], check=True, capture_output=True
            )
            try:
                failed = await backend._collect(allocation, 3)
                assert failed.status != "ok" and failed.file is None
                assert backend.allocations.get(identifier, ctx.owner).state in {
                    "unknown",
                    "retained",
                }
            finally:
                await asyncio.to_thread(
                    subprocess.run, ["docker", "unpause", name], check=True, capture_output=True
                )
            # The canceled create request can still finish on the control plane. Observe that
            # real resource before advancing the test clock, so reconciliation cannot race it.
            from opensandbox import SandboxManager
            from opensandbox.models.sandboxes import SandboxFilter

            manager = await SandboxManager.create(backend.cleanup_connection)
            try:
                async with asyncio.timeout(35):
                    while True:
                        matches = await manager.list_sandbox_infos(
                            SandboxFilter(metadata=allocation.metadata, page=1, page_size=100)
                        )
                        if matches.sandbox_infos:
                            break
                        await asyncio.sleep(0.25)
            finally:
                await manager.close()
            with Session(engine) as session, session.begin():
                session.execute(
                    update(SandboxAllocation)
                    .where(SandboxAllocation.id == identifier)
                    .values(
                        resource_deadline=datetime.now(UTC) - timedelta(seconds=1),
                        created_at=datetime.now(UTC) - timedelta(minutes=4),
                    )
                )
            # Reconciliation claims a bounded batch across all retained allocations.
            # Observe this allocation's receipt, including when earlier cases left work due.
            async with asyncio.timeout(45):
                while True:
                    await backend.reconcile()
                    current = backend.allocations.get(identifier, ctx.owner)
                    if current.state == "retained" and current.sandbox_id is None:
                        break
                    await asyncio.sleep(0.25)
        inspected = await backend.inspect(identifier, ctx.owner)
        assert inspected.status == "ok" and inspected.payload == {"retained": 17}, inspected
        await release_test_volume(engine, backend, str(identifier), ctx.owner)

    asyncio.run(exercise())


def command(revision=0, **kwargs):
    return McpConnectionSave(
        revision=revision,
        name="个人工具",
        endpoint="https://fixture.example/mcp",
        tool_policies=[McpToolSelection(name="read")],
        **kwargs,
    )


def test_personal_mcp_owner_scope_encrypted_secret_and_revision(engine):
    repo = McpRepository(engine, os.environ["COMPASS_MODEL_ENCRYPTION_KEY"])
    key = owner()
    saved = repo.save(key, command(token=SecretStr("private-test-token")), None)
    assert saved.has_token and "private-test-token" not in saved.model_dump_json()
    assert repo.list((key[0], key[1], uuid4())) == []
    with pytest.raises(ModelError):
        repo.load((key[0], key[1], uuid4()), saved.id)
    (config,) = repo.load(key)
    assert config.token.get_secret_value() == "private-test-token" and repo.current(config)
    saved = repo.save(key, command(revision=1, enabled=False), saved.id)
    assert saved.revision == 2 and not repo.current(config)
    with pytest.raises(ModelError):
        repo.save(key, command(revision=1), saved.id)
    repo.delete(key, saved.id, saved.revision)
    assert repo.list(key) == []


def test_personal_connection_limit_is_serialized_across_replicas(engine):
    key = owner()

    def save(index):
        repo = McpRepository(engine, os.environ["COMPASS_MODEL_ENCRYPTION_KEY"])
        try:
            return repo.save(
                key, command().model_copy(update={"name": f"connection {index}"}), None
            )
        except ModelError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(save, range(8)))
    assert sum(value is not None for value in values) == 4


def test_allocations_enforce_shared_tenant_quota_and_retained_slot_ownership(engine):
    host = uuid4().hex
    key = owner()

    def reserve(index):
        store = Allocations(engine, host, slots=4)
        try:
            return store.reserve(key, uuid4(), uuid4(), f"call{index}", uuid4(), "0" * 64)
        except ToolRejected:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(reserve, range(6)))
    allocated = [value for value in values if value is not None]
    assert len(allocated) == 2 and len({value.slot for value in allocated}) == 2
    store = Allocations(engine, host, slots=4)
    first = allocated[0]
    retained = store.confirmed(first)
    second = reserve(8)
    assert second is not None and second.slot not in {a.slot for a in allocated}
    with pytest.raises(ToolRejected, match="sandbox_not_found"):
        store.get(first.id, (key[0], key[1], uuid4()))
    with pytest.raises(ToolRejected, match="sandbox_capacity"):
        store.phase(retained, "collect")


def test_phase_fence_rejects_delayed_callbacks_and_inspection_requires_owner(engine):
    key = owner()
    store = Allocations(engine, uuid4().hex)
    first = store.reserve(key, uuid4(), uuid4(), "call", uuid4(), "1" * 64)
    attached = store.attach(first, "test-compute")
    retained = store.confirmed(attached)
    with pytest.raises(ToolRejected, match="sandbox_not_found"):
        store.phase(retained, "inspect", owner())
    inspected = store.phase(retained, "inspect", key)
    assert inspected.seq == first.seq + 1
    with pytest.raises(ToolRejected, match="sandbox_phase_stale"):
        store.confirmed(first)
    store.confirmed(inspected)


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_real_compute_destroy_collect_png_and_readonly_nas_remount(engine):
    async def exercise():
        ctx = context(
            "python_analysis",
            trusted=CHAT.model_copy(
                update={
                    "actor_user_id": uuid4(),
                    "scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
                }
            ),
        )
        source_id = str(uuid4())
        source = AuthorizedInput(ctx.run_id, ctx.owner, {"values": [2, 3, 5]})
        ctx = replace(ctx, call_id="real-python", sources={source_id: source})
        store = real_allocations(engine)
        backend = real_backend(engine, store)
        args = {
            "source_query_id": source_id,
            "code": """
import matplotlib.pyplot as plt
result = {'total': sum(data['values'])}
plt.plot(data['values'])
plt.savefig('/workspace/chart.png')
""",
        }
        python_analysis_tool().authorize(args, ctx)
        receipt = await backend.execute(SandboxBinding(), args, ctx)
        assert receipt.status == "ok", receipt
        assert receipt.cleanup == "confirmed" and receipt.payload == {"total": 10}
        assert receipt.file is not None
        from compass_agent.tools.artifacts import ArtifactStore

        artifact = ArtifactStore().register(receipt.file, ctx)
        assert artifact.media_type == "image/png"
        from uuid import UUID

        allocation = store.get(UUID(receipt.execution_id), ctx.owner)
        assert allocation.state == "retained" and allocation.sandbox_id is None
        assert allocation.phase == "collect" and allocation.seq == 2
        inspected = await backend.inspect(allocation.id, ctx.owner)
        assert inspected.status == "ok" and inspected.payload == {"total": 10}
        assert inspected.file == receipt.file
        with pytest.raises(ToolRejected):
            await backend.inspect(allocation.id, (ctx.owner[0], ctx.owner[1], uuid4()))
        await release_test_volume(engine, backend, receipt.execution_id, ctx.owner)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_real_nas_quota_is_enforced_by_filesystem(engine):
    async def exercise():
        ctx = context(
            "python_analysis",
            trusted=CHAT.model_copy(
                update={
                    "actor_user_id": uuid4(),
                    "scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
                }
            ),
        )
        source_id = str(uuid4())
        ctx = replace(
            ctx,
            call_id="quota-python",
            sources={source_id: AuthorizedInput(ctx.run_id, ctx.owner, {})},
        )
        backend = real_backend(engine)
        args = {
            "source_query_id": source_id,
            "code": """
import errno
from pathlib import Path
written = 0
failure = None
try:
    for index in range(160):
        path = Path('/workspace/quota-' + str(index))
        with path.open('wb') as stream:
            written += stream.write(b'x' * (512 * 1024))
except OSError as error:
    failure = error.errno
finally:
    for path in Path('/workspace').glob('quota-*'):
        path.unlink()
result = {'errno': failure, 'written': written}
""",
        }
        receipt = await backend.execute(SandboxBinding(), args, ctx)
        assert receipt.status == "ok" and isinstance(receipt.payload, dict), receipt
        assert receipt.payload["errno"] == 28
        assert 32 * 1024 * 1024 < receipt.payload["written"] < 64 * 1024 * 1024
        await release_test_volume(engine, backend, receipt.execution_id, ctx.owner)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_lost_create_response_stays_quarantined_until_metadata_reconciliation(engine, monkeypatch):
    async def exercise():
        from datetime import UTC, datetime, timedelta

        from compass_agent.tools.sandbox.entities import SandboxAllocation
        from opensandbox import Sandbox
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        ctx = context(
            "python_analysis",
            trusted=CHAT.model_copy(
                update={
                    "actor_user_id": uuid4(),
                    "scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
                }
            ),
        )
        source_id = str(uuid4())
        ctx = replace(
            ctx,
            call_id="lost-create",
            sources={source_id: AuthorizedInput(ctx.run_id, ctx.owner, {})},
        )
        store = real_allocations(engine)
        backend = real_backend(engine, store)
        create = Sandbox.create

        async def lose_response(*args, **kwargs):
            sandbox = await create(*args, **kwargs)
            await sandbox.close()
            raise TimeoutError("fixture discards create response")

        with monkeypatch.context() as patcher:
            patcher.setattr(Sandbox, "create", lose_response)
            receipt = await backend.execute(
                SandboxBinding(), {"source_query_id": source_id, "code": "result = 1"}, ctx
            )
        assert receipt.cleanup == "unconfirmed" and receipt.status == "unknown"
        from uuid import UUID

        identifier = UUID(receipt.execution_id)
        allocation = store.get(identifier, ctx.owner)
        assert allocation.state == "unknown" and allocation.sandbox_id is None
        with pytest.raises(ToolRejected, match="sandbox_attempt_exists"):
            await backend.execute(
                SandboxBinding(), {"source_query_id": source_id, "code": "result = 1"}, ctx
            )
        # Advance the lease clock; the real remote resource still requires deletion.
        with Session(engine) as session, session.begin():
            session.execute(
                update(SandboxAllocation)
                .where(SandboxAllocation.id == identifier)
                .values(
                    created_at=datetime.now(UTC) - timedelta(minutes=4),
                    resource_deadline=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        assert str(identifier) in await backend.reconcile()
        assert store.get(identifier, ctx.owner).state == "retained"
        await release_test_volume(engine, backend, receipt.execution_id, ctx.owner)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_real_two_tenants_retention_cleanup_reuse_and_artifact_independence(engine):
    async def exercise():
        from datetime import UTC, datetime, timedelta
        from uuid import UUID

        from compass_agent.tools.artifacts import ArtifactStore
        from compass_agent.tools.sandbox.entities import SandboxAllocation
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        store = real_allocations(engine)
        backend = real_backend(engine, store)
        contexts = []
        for index in range(2):
            ctx = context(
                "python_analysis",
                trusted=CHAT.model_copy(
                    update={
                        "actor_user_id": uuid4(),
                        "scope": TenantScope(
                            kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()
                        ),
                    }
                ),
            )
            contexts.append(
                replace(
                    ctx,
                    call_id=f"tenant-{index}",
                    sources={"input": AuthorizedInput(ctx.run_id, ctx.owner, {"owner": index})},
                )
            )
        receipts = await asyncio.gather(
            *(
                backend.execute(
                    SandboxBinding(),
                    {
                        "source_query_id": "input",
                        "code": """
import matplotlib.pyplot as plt
from pathlib import Path
Path('/workspace/owner.txt').write_text(str(data['owner']))
result = {'owner': data['owner']}
plt.plot([data['owner'], 2]); plt.savefig('/workspace/chart.png')
""",
                    },
                    ctx,
                )
                for ctx in contexts
            )
        )
        allocations = []
        artifacts = ArtifactStore()
        accepted = []
        for index, (receipt, ctx) in enumerate(zip(receipts, contexts, strict=True)):
            assert receipt.status == "ok" and receipt.payload == {"owner": index}, receipt
            assert receipt.file is not None
            accepted.append(artifacts.register(receipt.file, ctx))
            allocation = store.get(UUID(receipt.execution_id), ctx.owner)
            allocations.append(allocation)
        assert allocations[0].slot != allocations[1].slot
        with pytest.raises(ToolRejected):
            await backend.inspect(allocations[0].id, contexts[1].owner)
        for allocation in allocations:
            with Session(engine) as session, session.begin():
                session.execute(
                    update(SandboxAllocation)
                    .where(SandboxAllocation.id == allocation.id)
                    .values(retain_until=datetime.now(UTC) - timedelta(seconds=1))
                )
            current = store.get(allocation.id, allocation.owner)
            cleaned = await backend._collect(store.phase(current, "clean"), 30, clean=True)
            assert cleaned.status == "ok" and cleaned.cleanup == "confirmed"
            with pytest.raises(ToolRejected):
                store.get(allocation.id, allocation.owner)
        # Accepted bytes remain independent of all workspace deletion and subsequent leases.
        assert (
            artifacts.read(accepted[0].id, contexts[0].owner, contexts[0].run_id).content
            == receipts[0].file
        )
        fresh = replace(
            contexts[0], run_id=contexts[0].control.id, step_id=uuid4(), call_id="fresh"
        )
        result = await backend.execute(
            SandboxBinding(),
            {
                "source_query_id": "input",
                "code": "from pathlib import Path\n"
                "result = {'oldOwnerPresent': Path('/workspace/owner.txt').exists()}",
            },
            fresh,
        )
        assert result.status == "ok" and result.payload == {"oldOwnerPresent": False}, result
        assert store.get(UUID(result.execution_id), fresh.owner).slot in {
            a.slot for a in allocations
        }
        await release_test_volume(engine, backend, result.execution_id, fresh.owner)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("raise ValueError('synthetic failure')", "error"),
        ("result = 'x' * (70 * 1024)", "error"),
        (
            "result = {'value': 3}\nimport os\nos.symlink('/etc/passwd', '/workspace/chart.png')",
            "ok",
        ),
        ("result = {'value': 3}\nopen('/workspace/chart.png','wb').write(b'not-a-png')", "ok"),
        ("result = bytearray(1024**3)", "error"),
    ],
)
@pytest.mark.skipif(
    not os.environ.get("COMPASS_SANDBOX_API_KEY"), reason="real Sandbox credentials required"
)
def test_real_python_faults_and_unsafe_files_never_become_artifacts(engine, code, status):
    async def exercise():
        from datetime import UTC, datetime, timedelta
        from uuid import UUID

        from compass_agent.tools.artifacts import ArtifactStore
        from compass_agent.tools.sandbox.entities import SandboxAllocation
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        ctx = context(
            "python_analysis",
            trusted=CHAT.model_copy(
                update={
                    "actor_user_id": uuid4(),
                    "scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
                }
            ),
        )
        ctx = replace(
            ctx, call_id="fault", sources={"input": AuthorizedInput(ctx.run_id, ctx.owner, {})}
        )
        store = real_allocations(engine)
        backend = real_backend(engine, store)
        receipt = await backend.execute(
            SandboxBinding(), {"source_query_id": "input", "code": code}, ctx
        )
        assert receipt.status == status and receipt.cleanup == "confirmed", receipt
        if receipt.file is not None:
            with pytest.raises(ToolRejected):
                ArtifactStore().register(receipt.file, ctx)
            assert receipt.payload == {"value": 3}
        identifier = UUID(receipt.execution_id)
        with Session(engine) as session, session.begin():
            session.execute(
                update(SandboxAllocation)
                .where(SandboxAllocation.id == identifier)
                .values(retain_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        cleaned = await backend._collect(
            store.phase(store.get(identifier, ctx.owner), "clean"), 30, clean=True
        )
        assert cleaned.status == "ok" and cleaned.cleanup == "confirmed"

    asyncio.run(exercise())


def test_unknown_lease_is_discovered_early_and_confirmation_is_shared(engine):
    host = f"replicas-{uuid4()}"
    first, second = Allocations(engine, host), Allocations(engine, host)
    allocation = first.reserve(owner(), uuid4(), uuid4(), "lost-response", uuid4(), "digest")
    first.unknown(allocation)
    claimed = second.claim_due()
    assert [item.id for item in claimed] == [allocation.id]
    assert first.confirmed_executions({str(allocation.id)}) == []
    second.confirmed(claimed[0])
    assert first.confirmed_executions({str(allocation.id), "invalid-id"}) == [str(allocation.id)]
    assert Allocations(engine, "other-host").confirmed_executions({str(allocation.id)}) == []
