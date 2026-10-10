import asyncio
import json

from solomon_agent.knowledge import Evidence
from solomon_agent.runtime import SYSTEM_PROMPT, DemoRuntime, RespondService
from solomon_agent.storage import SessionStore


class Knowledge:
    def __init__(self):
        self.calls = []

    def search(self, query, limit=3, source_ids=None):
        self.calls.append((query, limit, source_ids))
        return [Evidence("sample", "笔记", "已知事实", "note.md")]


class FakeRuntime:
    def __init__(self):
        self.prompts = []

    async def stream(self, prompt):
        self.prompts.append(json.loads(prompt))
        yield "草稿"

    async def close(self):
        pass


def test_one_generation_with_pre_retrieval_and_mode_history(tmp_path):
    async def run():
        runtime, knowledge = FakeRuntime(), Knowledge()
        storage = SessionStore(tmp_path)
        service = RespondService(runtime, knowledge, storage, ["sample"])
        events = [event async for event in service.stream("问题", "draft", "session", "request")]
        assert len(runtime.prompts) == 1
        assert knowledge.calls == [("问题", 3, None)]
        assert events[0]["data"]["claim_verification"] is False
        assert events[-1]["event"] == "completed"
        assert events[-1]["data"]["text"] == "草稿"
        assert events[-1]["data"]["basis"] == "mixed"
        assert storage.recent("session", "draft")[-1]["content"] == "草稿"
        assert storage.recent("session", "voice") == []
        assert "禁止推断" in SYSTEM_PROMPT

    asyncio.run(run())


def test_demo_is_explicit_simulation():
    async def run():
        text = "".join([part async for part in DemoRuntime().stream("test")])
        assert "离线模拟" in text and "未调用模型" in text

    asyncio.run(run())


def test_cancelled_generation_does_not_enter_history(tmp_path):
    async def run():
        class WaitingRuntime:
            async def stream(self, prompt):
                yield "partial"
                await asyncio.Event().wait()

        storage = SessionStore(tmp_path)
        service = RespondService(WaitingRuntime(), Knowledge(), storage, ["sample"])
        entered = asyncio.Event()

        async def consume():
            async for event in service.stream("question", "voice", "s", "r"):
                if event["event"] == "text_delta":
                    entered.set()

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert storage.recent("s", "voice") == []

    asyncio.run(run())


def test_pydantic_custom_endpoint_single_openai_stream(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from solomon_agent.config import Config
    from solomon_agent.runtime import PydanticRuntime

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for text, reason in [("测试", None), ("回复", None), ("", "stop")]:
                chunk = {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "protocol-test",
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": reason}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def run():
        runtime = PydanticRuntime(
            Config(
                tmp_path,
                model="protocol-test",
                api_key="test-only",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
            )
        )
        try:
            assert "".join([delta async for delta in runtime.stream("问题")]) == "测试回复"
        finally:
            await runtime.close()

    try:
        asyncio.run(run())
        assert len(calls) == 1
        assert calls[0][0] == "/v1/chat/completions"
        assert calls[0][1]["stream"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_history_budget_and_no_reference_basis(tmp_path):
    class EmptyKnowledge:
        def search(self, *args, **kwargs):
            return []

    async def run():
        storage = SessionStore(tmp_path)
        storage.start_request("old", "s", "draft", "previous-long-user-message")
        storage.finish_request("old", "previous-long-answer")
        runtime = FakeRuntime()
        service = RespondService(runtime, EmptyKnowledge(), storage, [], history_char_budget=10)
        events = [event async for event in service.stream("new", "draft", "s", "new")]
        history = runtime.prompts[0]["history"]
        assert history == []
        assert events[-1]["data"]["basis"] == "model_generated"

    asyncio.run(run())


def test_cancel_during_storage_insert_finalizes_request(tmp_path):
    import sqlite3
    from threading import Event

    async def run():
        storage = SessionStore(tmp_path)
        original = storage.start_request
        entered, release = Event(), Event()

        def slow_insert(*args):
            entered.set()
            release.wait(timeout=3)
            original(*args)

        storage.start_request = slow_insert
        service = RespondService(FakeRuntime(), Knowledge(), storage, ["sample"])

        async def consume():
            async for _ in service.stream("q", "voice", "s", "r"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        with sqlite3.connect(storage.db_path) as db:
            assert db.execute("SELECT status FROM requests WHERE request_id='r'").fetchone() == (
                "cancelled",
            )

    asyncio.run(run())


def test_history_budget_preserves_only_complete_recent_turns(tmp_path):
    async def run():
        storage = SessionStore(tmp_path)
        storage.start_request("old", "s", "draft", "older")
        storage.finish_request("old", "answer")
        storage.start_request("recent", "s", "draft", "user")
        storage.finish_request("recent", "reply")
        runtime = FakeRuntime()
        service = RespondService(runtime, Knowledge(), storage, ["sample"], history_char_budget=9)
        async for _ in service.stream("q", "draft", "s", "current"):
            pass
        assert runtime.prompts[0]["history"] == [
            {"role": "user", "content": "user"},
            {"role": "assistant", "content": "reply"},
        ]

    asyncio.run(run())
