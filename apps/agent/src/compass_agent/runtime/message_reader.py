"""Read complete Run projections in one repeatable PostgreSQL snapshot."""

import base64
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentMessage, AgentRun, AgentSession
from compass_agent.runtime.store import TERMINAL, message_snapshot, owner_filter, session_item
from compass_agent.schemas import AgentResponse, SessionList


def encode_cursor(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(value):
    try:
        if len(value) > 500:
            raise ValueError()
        return json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
    except (ValueError, TypeError, UnicodeError):
        raise ModelError("cursor_invalid", 422) from None


class MessageReader:
    def __init__(self, store):
        self.store = store

    def session(self, session_id, context):
        with Session(self.store.engine) as tx:
            session = tx.scalar(
                select(AgentSession).where(AgentSession.id == session_id, *owner_filter(context))
            )
            if session is None:
                raise ModelError("not_found", 404)
            run = tx.scalar(
                select(AgentRun)
                .where(AgentRun.session_id == session.id)
                .order_by(AgentRun.ordinal.desc())
                .limit(1)
            )
            return session_item(session, run)

    def sessions(self, context, *, limit=20, cursor=None):
        with Session(self.store.engine) as tx:
            query = select(AgentSession).where(*owner_filter(context))
            if cursor:
                try:
                    stamp, identifier = decode_cursor(cursor)
                    query = query.where(
                        tuple_(AgentSession.updated_at, AgentSession.id)
                        < tuple_(datetime.fromisoformat(stamp), UUID(identifier))
                    )
                except (TypeError, ValueError):
                    raise ModelError("cursor_invalid", 422) from None
            sessions = tx.scalars(
                query.order_by(AgentSession.updated_at.desc(), AgentSession.id.desc()).limit(
                    limit + 1
                )
            ).all()
            items = []
            for session in sessions[:limit]:
                run = tx.scalar(
                    select(AgentRun)
                    .where(AgentRun.session_id == session.id)
                    .order_by(AgentRun.ordinal.desc())
                    .limit(1)
                )
                items.append(session_item(session, run))
            last = sessions[min(len(sessions), limit) - 1] if sessions else None
            return SessionList.model_validate(
                {
                    "items": items,
                    "nextCursor": encode_cursor([last.updated_at.isoformat(), str(last.id)])
                    if len(sessions) > limit and last
                    else None,
                }
            )

    def messages(self, session_id, context, *, cursor=None, run_id=None, limit=10):
        with (
            self.store.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            Session(connection) as tx,
        ):
            session = tx.scalar(
                select(AgentSession).where(AgentSession.id == session_id, *owner_filter(context))
            )
            if session is None:
                raise ModelError("not_found", 404)
            query = select(AgentRun).where(AgentRun.session_id == session_id)
            upper = (
                tx.scalar(
                    select(AgentRun.ordinal)
                    .where(AgentRun.session_id == session_id)
                    .order_by(AgentRun.ordinal.desc())
                    .limit(1)
                )
                or 0
            )
            before = upper + 1
            if cursor:
                try:
                    saved_session, upper, before = decode_cursor(cursor)
                    if (
                        saved_session != str(session_id)
                        or type(upper) is not int
                        or type(before) is not int
                        or not 0 < before <= upper + 1
                    ):
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ModelError("cursor_invalid", 422) from None
            if run_id:
                query = query.where(AgentRun.id == run_id)
            runs = tx.scalars(
                query.where(AgentRun.ordinal <= upper, AgentRun.ordinal < before)
                .order_by(AgentRun.ordinal.desc())
                .limit(limit + 1)
            ).all()
            if run_id and not runs:
                raise ModelError("not_found", 404)
            packets, cursors, size = [], [], 0
            consumed = []
            for run in runs[:limit]:
                events = [{"type": "run.upsert", "payload": run.public_view["run"]}]
                events += [
                    {"type": "message.upsert", "payload": message_snapshot(m)}
                    for m in tx.scalars(
                        select(AgentMessage)
                        .where(
                            AgentMessage.run_id == run.id, AgentMessage.channel == "conversation"
                        )
                        .order_by(AgentMessage.ordinal)
                    )
                ]
                events += [
                    {"type": "view.upsert", "payload": value}
                    for value in run.public_view["objects"].values()
                ]
                batch = []
                for index, event in enumerate(events):
                    occurred_at = run.created_at
                    if (
                        event["type"] == "run.upsert"
                        and event["payload"]["status"] in TERMINAL
                        and run.finished_at is not None
                    ):
                        occurred_at = run.finished_at
                    batch.append(
                        {
                            "packetId": f"snapshot:{run.id}:{run.projected_seq}:{index}",
                            "runId": str(run.id),
                            "seq": None,
                            "requestId": str(run.request_id),
                            "occurredAt": occurred_at.astimezone(UTC).isoformat(),
                            "event": event,
                        }
                    )
                batch_size = len(json.dumps(batch, ensure_ascii=False).encode())
                if consumed and size + batch_size > 8 * 1024 * 1024:
                    break
                size += batch_size
                packets.extend(batch)
                cursors.append({"runId": str(run.id), "seq": run.projected_seq})
                consumed.append(run)
            next_cursor = (
                encode_cursor([str(session_id), upper, consumed[-1].ordinal])
                if consumed and len(runs) > len(consumed) and not run_id
                else None
            )
            return AgentResponse.model_validate(
                {
                    "protocol": "compass.run",
                    "version": 2,
                    "sessionId": str(session_id),
                    "mode": "snapshot",
                    "packets": packets,
                    "cursors": cursors,
                    "page": {"nextCursor": next_cursor},
                }
            )
