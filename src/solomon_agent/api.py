import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Config
from .runtime import DemoRuntime, PydanticRuntime, RespondService


class RespondRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    mode: Literal["draft", "voice"] = "draft"
    session_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    source_ids: list[str] | None = None


def build_service(config, demo=False):
    from .knowledge import MarkdownKnowledgeProvider
    from .storage import SessionStore

    knowledge = MarkdownKnowledgeProvider(config.sources, config.data_dir)
    return RespondService(
        DemoRuntime() if demo else PydanticRuntime(config),
        knowledge,
        SessionStore(config.data_dir),
        config.sources,
        history_char_budget=config.history_char_budget,
    )


def create_app(config: Config | None = None, *, demo=False, service=None):
    active = {}
    sessions = set()
    seen = set()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or build_service(config or Config.load(), demo)
        try:
            yield
        finally:
            tasks = [entry["task"] for entry in active.values()]
            for entry in active.values():
                if not entry["cancelled"] and not entry["task"].done():
                    entry["cancelled"] = True
                    entry["task"].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.service.runtime.close()

    app = FastAPI(title="Solomon Agent", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "simulation": demo}

    @app.post("/v1/sessions")
    async def new_session():
        return {"session_id": str(uuid4())}

    @app.delete("/v1/sessions/{session_id}")
    async def clear_session(session_id: str):
        if session_id in sessions:
            raise HTTPException(409, "Session has an active request")
        await asyncio.to_thread(app.state.service.storage.clear, session_id)
        return {"cleared": True, "archived": True, "new_session_id": str(uuid4())}

    @app.post("/v1/requests/{request_id}/cancel")
    async def cancel(request_id: str):
        entry = active.get(request_id)
        if entry is None:
            raise HTTPException(404, "Request is not active")
        if entry["cancelled"]:
            return {"request_id": request_id, "cancelled": True}
        entry["cancelled"] = True
        entry["task"].cancel()
        queue = entry["queue"]
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait({"event": "cancelled", "data": {}})
        return {"request_id": request_id, "cancelled": True}

    @app.post("/v1/respond")
    async def respond(body: RespondRequest):
        svc = app.state.service
        if body.source_ids is not None and not set(body.source_ids).issubset(svc.sources):
            raise HTTPException(422, "Unknown or unauthorized source_ids")
        if await asyncio.to_thread(svc.storage.is_session_archived, body.session_id):
            raise HTTPException(409, "Session archived; create a new session")
        if await asyncio.to_thread(svc.storage.has_request, body.request_id):
            raise HTTPException(409, "Duplicate request_id")
        if body.request_id in seen or body.session_id in sessions:
            raise HTTPException(409, "Duplicate request or busy session")
        # Do not accept unlimited lifetime IDs; start a new worker before this capacity.
        if len(seen) >= 10000:
            raise HTTPException(503, "Worker request capacity reached; restart when idle")
        seen.add(body.request_id)
        sessions.add(body.session_id)
        queue = asyncio.Queue(maxsize=32)
        started = time.perf_counter()
        entry = {"cancelled": False, "queue": queue}

        async def produce():
            first_text_ms = None
            completion = {}
            generated_text = ""
            try:
                async for event in svc.stream(
                    body.message, body.mode, body.session_id, body.request_id, body.source_ids
                ):
                    if entry["cancelled"]:
                        break
                    if event["event"] == "completed":
                        completion = event["data"]
                        continue
                    if event["event"] == "text_delta":
                        generated_text += event["data"]["delta"]
                        if first_text_ms is None:
                            first_text_ms = (time.perf_counter() - started) * 1000
                    await queue.put(event)
                if not entry["cancelled"]:
                    await queue.put(
                        {
                            "event": "completed",
                            "data": {
                                **completion,
                                "text": generated_text,
                                "first_text_ms": first_text_ms,
                                "total_ms": (time.perf_counter() - started) * 1000,
                                "simulation": demo,
                            },
                        }
                    )
            except asyncio.CancelledError:
                entry["cancelled"] = True
                # Drop queued late content before the terminal cancellation event.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"event": "cancelled", "data": {}})
            except Exception:
                await queue.put(
                    {
                        "event": "error",
                        "data": {
                            "code": "generation_failed",
                            "message": "Generation failed; verify local configuration and endpoint",
                        },
                    }
                )
            finally:
                sessions.discard(body.session_id)

        entry["task"] = asyncio.create_task(produce())
        active[body.request_id] = entry
        entry["task"].add_done_callback(lambda task: sessions.discard(body.session_id))

        async def events():
            def encode(event):
                payload = {
                    **event["data"],
                    "request_id": body.request_id,
                    "session_id": body.session_id,
                }
                return (
                    "event: "
                    + event["event"]
                    + "\ndata: "
                    + json.dumps(payload, ensure_ascii=False)
                    + "\n\n"
                )

            try:
                yield encode({"event": "started", "data": {"simulation": demo}})
                while True:
                    event = await queue.get()
                    if entry["cancelled"] and event["event"] != "cancelled":
                        continue
                    yield encode(event)
                    if event["event"] in {"completed", "error", "cancelled"}:
                        break
            finally:
                # Starlette disconnect handling cancels an AnyIO task group.
                # Shield the complete cleanup, including SQLite finalization.
                with anyio.CancelScope(shield=True):
                    if not entry["task"].done() and not entry["cancelled"]:
                        entry["cancelled"] = True
                        entry["task"].cancel()
                    await asyncio.gather(entry["task"], return_exceptions=True)
                    active.pop(body.request_id, None)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
