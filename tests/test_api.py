import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from solomon_agent.api import create_app


class Storage:
    def clear(self, session_id):
        self.cleared = session_id

    def has_request(self, request_id):
        return request_id == "persisted"

    def is_session_archived(self, session_id):
        return session_id == "archived"


class Service:
    sources = {"sample"}
    storage = Storage()
    runtime = SimpleNamespace()

    async def stream(self, *args):
        yield {"event": "evidence", "data": {"items": []}}
        yield {"event": "text_delta", "data": {"delta": "回答"}}


async def close():
    pass


Service.runtime.close = close


def test_sse_contract_and_source_authorization():
    with TestClient(create_app(service=Service(), demo=True)) as client:
        denied = client.post("/v1/respond", json={"message": "question", "source_ids": ["private"]})
        assert denied.status_code == 422
        response = client.post(
            "/v1/respond", json={"message": "question", "request_id": "r", "session_id": "s"}
        )
        assert response.status_code == 200
        assert "event: started" in response.text
        assert "event: text_delta" in response.text
        assert "event: completed" in response.text
        assert '"request_id": "r"' in response.text
        assert (
            client.post("/v1/respond", json={"message": "again", "request_id": "r"}).status_code
            == 409
        )
        assert client.delete("/v1/sessions/s").status_code == 200


def test_cancel_drops_late_content_and_frees_session():
    async def run():
        entered = asyncio.Event()

        class Waiting(Service):
            async def stream(self, *args):
                entered.set()
                await asyncio.Event().wait()
                yield {"event": "text_delta", "data": {"delta": "late"}}

        app = create_app(service=Waiting())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                pending = asyncio.create_task(
                    client.post(
                        "/v1/respond", json={"message": "q", "session_id": "s", "request_id": "r"}
                    )
                )
                await entered.wait()
                assert (await client.delete("/v1/sessions/s")).status_code == 409
                assert (await client.post("/v1/requests/r/cancel")).status_code == 200
                response = await asyncio.wait_for(pending, 2)
                assert "event: cancelled" in response.text
                assert "late" not in response.text
                assert "event: completed" not in response.text
                assert (await client.delete("/v1/sessions/s")).status_code == 200

    asyncio.run(run())


def test_failure_has_sanitized_error_and_no_completed():
    class Broken(Service):
        async def stream(self, *args):
            raise RuntimeError("secret-key")
            yield

    with TestClient(create_app(service=Broken())) as client:
        response = client.post("/v1/respond", json={"message": "q"})
        assert "event: error" in response.text
        assert "secret-key" not in response.text
        assert "event: completed" not in response.text


def test_persistent_duplicate_and_archived_session_rejected():
    with TestClient(create_app(service=Service())) as client:
        assert (
            client.post("/v1/respond", json={"message": "q", "request_id": "persisted"}).status_code
            == 409
        )
        assert (
            client.post("/v1/respond", json={"message": "q", "session_id": "archived"}).status_code
            == 409
        )


def test_asgi_disconnect_finalizes_persistent_request(tmp_path):
    import sqlite3

    from solomon_agent.runtime import RespondService
    from solomon_agent.storage import SessionStore

    async def run():
        class EmptyKnowledge:
            def search(self, *args, **kwargs):
                return []

        class WaitingRuntime:
            async def stream(self, prompt):
                yield "first"
                await asyncio.Event().wait()

            async def close(self):
                pass

        storage = SessionStore(tmp_path)
        service = RespondService(WaitingRuntime(), EmptyKnowledge(), storage, [])
        app = create_app(service=service)
        disconnected = asyncio.Event()
        received = False
        chunks = []
        payload = json.dumps(
            {"message": "q", "mode": "voice", "request_id": "r", "session_id": "s"}
        ).encode()

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if b"text_delta" in message.get("body", b""):
                    disconnected.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/respond",
            "raw_path": b"/v1/respond",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "server": ("localhost", 80),
            "client": ("localhost", 1234),
        }
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(scope, receive, send), timeout=3)
            assert disconnected.is_set()
            with sqlite3.connect(storage.db_path) as db:
                assert db.execute(
                    "SELECT status FROM requests WHERE request_id='r'"
                ).fetchone() == ("cancelled",)
            assert storage.recent("s", "voice") == []
        assert b"completed" not in b"".join(chunks)

    asyncio.run(run())
