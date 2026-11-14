"""Public replay algebra, native Redis cursors and private state trust boundaries."""

from copy import deepcopy
from uuid import uuid4

import pytest
from compass_agent.models.errors import ModelError
from compass_agent.runtime.private_state import PrivateState
from compass_agent.runtime.public_projection import stream_position
from compass_agent.runtime.redis_stream import complete_prefix
from cryptography.fernet import Fernet


def test_native_cursor_uses_integer_pairs_without_float_precision_loss():
    assert stream_position("18446744073709551615-2") > stream_position("9999999999999999999-12")
    for invalid in ["01-0", "1", "-1-0", "1.2-0", "18446744073709551616-0"]:
        with pytest.raises(ModelError):
            stream_position(invalid)


def test_private_state_binds_run_purpose_key_and_version():
    key, run_id = Fernet.generate_key().decode(), uuid4()
    private = PrivateState(key)
    encrypted = private.seal(run_id, "checkpoint", {"history": "private sentinel"})
    assert b"private sentinel" not in encrypted
    assert private.open(run_id, "checkpoint", encrypted) == {"history": "private sentinel"}
    for reader, run, purpose in [
        (private, uuid4(), "checkpoint"),
        (private, run_id, "output"),
        (PrivateState(Fernet.generate_key().decode()), run_id, "checkpoint"),
    ]:
        with pytest.raises(ModelError):
            reader.open(run, purpose, encrypted)


def test_partial_outbox_batch_remains_invisible_until_final_entry():
    first = {"outboxId": "batch", "batchIndex": 0, "batchSize": 2}
    last = {**first, "batchIndex": 1}
    assert complete_prefix([("1-0", first)]) == []
    assert complete_prefix([("1-0", first), ("1-1", last)]) == [("1-0", first), ("1-1", last)]
    with pytest.raises(ModelError):
        complete_prefix([("1-0", last)])


def test_public_message_revisions_reject_gaps_and_conflicting_repeat():
    from compass_agent.runtime.public_projection import reduce_packet as reduce

    def reduce_packet(view, messages, packet):
        reduce(
            view,
            messages,
            {
                "packetId": "packet",
                "runId": str(uuid4()),
                "requestId": str(uuid4()),
                "occurredAt": "2026-11-12T00:00:00Z",
                "seq": None,
                **packet,
            },
        )

    view, messages = {"run": {}, "objects": {}}, {}
    identifier = str(uuid4())
    message = {
        "messageId": identifier,
        "ordinal": 2,
        "role": "assistant",
        "revision": 1,
        "status": "streaming",
        "content": {"text": "", "artifactIds": []},
    }
    packet = {"event": {"type": "message.upsert", "payload": message}}
    reduce_packet(view, messages, packet)
    reduce_packet(view, messages, deepcopy(packet))
    reduce_packet(
        view,
        messages,
        {
            "event": {
                "type": "message.delta",
                "payload": {"messageId": identifier, "revision": 2, "text": "已保存"},
            }
        },
    )
    assert messages[identifier]["content"]["text"] == "已保存"
    with pytest.raises(ModelError):
        reduce_packet(
            view,
            messages,
            {
                "event": {
                    "type": "message.delta",
                    "payload": {"messageId": identifier, "revision": 4, "text": "跳步"},
                }
            },
        )
