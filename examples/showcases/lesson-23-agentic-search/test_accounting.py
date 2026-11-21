"""The classroom cost report keeps an unmetered provider attempt's reservation separate."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


def test_ledger_counts_measured_usage_without_charging_an_unknown_call(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    from compare import summarize
    from evaluate import model_accounting

    run_id = str(uuid4())
    paid = dict(
        provider_id=uuid4(),
        requested_model="classroom",
        config_version=3,
        sent=True,
        status="completed",
        outcome_unknown=False,
        cleanup_confirmed=True,
        usage={"input_tokens": 10, "output_tokens": 3, "reasoning_tokens": 1},
        actual_cost_microusd=125,
        reserved_tokens=700,
        reserved_cost_microusd=2_000,
    )
    unmetered = {**paid, "status": "failed", "usage": {}, "actual_cost_microusd": None}
    unsent = {**unmetered, "sent": False, "status": "canceled"}
    database = SimpleNamespace(
        execute=lambda query, args: SimpleNamespace(fetchall=lambda: [paid, unmetered, unsent])
    )
    accounting = model_accounting(database, run_id)
    assert accounting["usageByModel"][0]["tokens"] == 13
    assert accounting["usageByModel"][0]["unsent_attempts"] == 1
    assert not accounting["usageComplete"]
    base = {"runId": run_id, "case": "main", "group": "candidate", "status": "failed"}
    raw = tmp_path / "old.json"
    revised = tmp_path / "recounted.json"
    raw.write_text(json.dumps({**base, "trace": {"usage": {"cost_microusd": 2125}}}))
    revised.write_text(json.dumps({**base, "trace": accounting}))
    (summary,) = summarize([raw, revised])
    assert summary["submitted"] == 1
    assert summary["attributedTokens"] == 13
    assert summary["attributedCostUsd"] == 0.000125
    assert summary["unknownAttempts"] == 1
    assert summary["reservedTokens"] == 700
    assert summary["reservedCostUsd"] == 0.002
    (legacy,) = summarize([raw])
    assert legacy["unverifiedLedgerRuns"] == 1
    assert legacy["attributedCostUsd"] is None
