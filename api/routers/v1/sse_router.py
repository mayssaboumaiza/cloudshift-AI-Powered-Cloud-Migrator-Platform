"""
sse_router.py — Server-Sent Events stream for real-time migration pipeline monitoring.

SSE is used instead of WebSocket because communication is strictly one-directional
(server → client). Benefits over WebSocket:
  - Browser EventSource reconnects automatically and sends Last-Event-ID so the
    server can resume from the last delivered event without any client-side retry logic.
  - Works with standard HTTP proxies and load balancers without upgrade negotiation.
  - Simpler implementation — no shared connection state, no broadcast manager.

Endpoint:
  GET /api/v1/migrations/{migration_id}/stream
  (Accept: text/event-stream — set automatically by EventSource)

SSE event format:
  id: <event_id>
  data: {"id":1,"thread_id":"...","event_type":"phase_started","phase":"agent_01",...}

  (blank line terminates each event)
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from data.repositories.migration_repository import MigrationRepository
from configuration.database import async_session

logger = logging.getLogger("SSERouter")

router = APIRouter(tags=["SSE"])

_POLL_INTERVAL_S = 0.5


@router.get("/migrations/{migration_id}/stream")
async def stream_migration_events(migration_id: str, request: Request):
    """
    SSE stream of workflow events for a migration.

    Sends events from the workflow_events table as they are written by the
    pipeline agents.  The client's EventSource automatically sends Last-Event-ID
    on reconnect so streaming resumes without duplicating events.
    """
    last_event_id_header = request.headers.get("last-event-id", "0")
    try:
        initial_last_id = int(last_event_id_header)
    except ValueError:
        initial_last_id = 0

    # Resolve migration → get the thread_id used by EventPublisher.
    # For new migrations thread_id == migration_id (same UUID).
    # For old migrations they differ — we must query by thread_id, not migration_id.
    thread_id_for_stream = migration_id  # default: new migrations
    async with async_session() as db:
        repo = MigrationRepository(db)
        try:
            migration = await repo.get_by_id(migration_id)
            if migration.thread_id:
                thread_id_for_stream = migration.thread_id
        except Exception:
            async def _not_found():
                payload = json.dumps({"message": f"Migration {migration_id} not found"})
                yield f"event: error\ndata: {payload}\n\n"

            return StreamingResponse(
                _not_found(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    async def event_generator():
        last_id = initial_last_id

        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", migration_id)
                break

            try:
                from agents.checkpoint_postgres import get_checkpoint_saver

                saver = get_checkpoint_saver()

                # Events come from pipeline_events table.
                # Query by thread_id (the key used by EventPublisher), not migration_id.
                async for event in saver.get_events_stream(thread_id_for_stream, after_index=last_id):
                    # Schema: api/schemas/sse_events.ProgressEvent
                    # event_type maps to ProgressEvent.step; phase to ProgressEvent.phase
                    payload = {
                        "id": event["id"],
                        "thread_id": migration_id,
                        "event_type": event.get("event_type", ""),
                        "phase": event.get("phase", ""),
                        "progress": event.get("progress"),
                        "details": event.get("details") or {},
                        "created_at": event.get("ts", ""),
                    }
                    yield f"id: {event['id']}\ndata: {json.dumps(payload)}\n\n"
                    last_id = event["id"]

            except Exception as exc:
                logger.error("SSE stream error for %s: %s", migration_id, exc)
                # Schema: api/schemas/sse_events.ErrorEvent
                yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

            await asyncio.sleep(_POLL_INTERVAL_S)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )
