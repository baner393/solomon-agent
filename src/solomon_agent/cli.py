import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from .config import Config


def _main():
    parser = argparse.ArgumentParser(description="Solomon Agent: local knowledge and reply drafts")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--demo", action="store_true")
    draft = sub.add_parser("draft")
    draft.add_argument("message")
    draft.add_argument("--session-id", default="cli")
    draft.add_argument("--mode", choices=["draft", "voice"], default="draft")
    draft.add_argument("--demo", action="store_true")
    sub.add_parser("index")
    sub.add_parser("doctor")
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "model": config.model,
                    "endpoint_configured": bool(config.base_url),
                    "api_key_configured": bool(config.api_key),
                    "data_dir": str(config.data_dir),
                    "sources": {key: str(value) for key, value in config.sources.items()},
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "index":
        from .knowledge import MarkdownKnowledgeProvider

        print(json.dumps(MarkdownKnowledgeProvider(config.sources, config.data_dir).index()))
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(config, demo=args.demo), host="127.0.0.1", port=args.port)
    else:
        from .api import build_service

        async def run():
            service = build_service(config, args.demo)
            try:
                async for event in service.stream(
                    args.message, args.mode, args.session_id, str(uuid4())
                ):
                    if event["event"] == "text_delta":
                        print(event["data"]["delta"], end="", flush=True)
                    elif event["event"] == "evidence":
                        print(json.dumps(event, ensure_ascii=False))
                print()
            finally:
                await service.runtime.close()

        asyncio.run(run())


def main():
    try:
        _main()
    except Exception:
        import sys

        print("Solomon Agent failed; check configuration, index and endpoint.", file=sys.stderr)
        raise SystemExit(1) from None
