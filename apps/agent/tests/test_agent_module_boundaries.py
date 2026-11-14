"""Module ownership must remain visible in imports as new execution backends are added."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src/compass_agent"


def test_tool_implementations_do_not_import_run_or_inference_orchestration():
    forbidden = (
        "compass_agent.analysis",
        "compass_agent.runtime",
        "compass_agent.harness.executor",
        "compass_agent.workflows",
    )
    for path in (ROOT / "tools").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            )
            assert not any(name.startswith(forbidden) for name in names), path.relative_to(ROOT)
            assert all(
                not name.startswith("compass_agent.harness")
                or name in {"compass_agent.harness.contracts", "compass_agent.harness.autonomy"}
                for name in names
            ), path.relative_to(ROOT)


def test_harness_has_no_runtime_or_business_implementation_dependency():
    forbidden = (
        "compass_agent.analysis",
        "compass_agent.runtime",
        "compass_agent.tools.builtin",
        "compass_agent.tools.business",
        "compass_agent.workflows",
    )
    for path in (ROOT / "harness").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            )
            assert not any(name.startswith(forbidden) for name in names), path.relative_to(ROOT)


def test_root_contains_no_tool_or_workflow_business_modules():
    assert not any(
        path.name.startswith(("mcp_", "retail_", "workflow_", "sandbox_"))
        for path in ROOT.glob("*.py")
    )
    for module in (
        "api/runs.py",
        "runtime/service.py",
        "harness/context.py",
        "harness/executor.py",
        "workflows/script.py",
        "tools/dispatcher.py",
        "tools/mcp/client.py",
        "tools/sandbox/client.py",
    ):
        assert (ROOT / module).is_file()


def test_removed_analysis_package_is_not_reintroduced():
    assert not (ROOT / "analysis").exists()
    for path in ROOT.rglob("*.py"):
        assert "compass_agent.analysis" not in path.read_text(), path.relative_to(ROOT)
