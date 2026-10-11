"""Text-path measurements only; reports never include prompts or generated content."""

import asyncio
import math
import re
import time
from contextlib import aclosing
from uuid import uuid4

_METRICS = ("first_text_ms", "first_sentence_ms", "total_ms")


def _sentence_ready(text: str, *, completed: bool = False) -> bool:
    # A dot is ambiguous while streaming: wait for whitespace, or end-of-output.
    # This deliberately remains a delimiter heuristic, not semantic verification.
    pattern = r"[。！？!?\n]|\.(?=\s|$)" if completed else r"[。！？!?\n]|\.(?=\s)"
    return any(text[: match.start()].strip() for match in re.finditer(pattern, text))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _report(rows: list[dict], runs: int, simulation: bool, *, setup_error=False) -> dict:
    successful = [row for row in rows if row["status"] == "completed"]
    report = {
        "requested_runs": runs,
        "attempted_count": len(rows),
        "sample_count": len(successful),
        "error_count": len(rows) - len(successful) + int(setup_error),
        "simulation": simulation,
        "speech_measured": False,
        "limited_samples": len(successful) < 20,
        "notes": [
            "Text-path timings exclude ASR, TTS, playback and transport to the digital human.",
            "Sentence timing is heuristic; an unterminated tail waits for completion.",
            "Fewer than 20 successful samples cannot establish a stable P95.",
            "No speech latency target verdict is produced.",
        ],
        "aggregates": {},
        "runs": rows,
    }
    for metric in _METRICS:
        values = [row[metric] for row in successful if row[metric] is not None]
        report["aggregates"][metric] = {
            "sample_count": len(values),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
        }
    if setup_error:
        report["error_code"] = "setup_failed"
    return report


def setup_failure_report(runs: int, simulation: bool) -> dict:
    return _report([], runs, simulation, setup_error=True)


async def run_benchmark(service, message: str, runs=5, mode="voice", *, simulation=False) -> dict:
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= 100:
        raise ValueError("runs must be an integer from 1 to 100")
    if mode not in {"draft", "voice"} or not message.strip():
        raise ValueError("A message and supported mode are required")
    rows = []
    for number in range(1, runs + 1):
        request_id, session_id = str(uuid4()), str(uuid4())
        row = {
            "run": number,
            "status": "error",
            "first_text_ms": None,
            "first_sentence_ms": None,
            "total_ms": None,
            "sentence_at_completion": False,
        }
        text = ""
        started = time.perf_counter()
        try:
            completed = False
            async with aclosing(service.stream(message, mode, session_id, request_id)) as events:
                async for event in events:
                    elapsed = round((time.perf_counter() - started) * 1000, 3)
                    if event["event"] == "text_delta":
                        text += event["data"]["delta"]
                        if text.strip() and row["first_text_ms"] is None:
                            row["first_text_ms"] = elapsed
                        if row["first_sentence_ms"] is None and _sentence_ready(text):
                            row["first_sentence_ms"] = elapsed
                    elif event["event"] == "completed":
                        completed = True
                        row["total_ms"] = elapsed
                        if not text.strip():
                            row["error_code"] = "empty_response"
                        else:
                            row["status"] = "completed"
                            if row["first_sentence_ms"] is None:
                                row["first_sentence_ms"] = elapsed
                                row["sentence_at_completion"] = True
                        break
                    elif event["event"] in {"error", "cancelled"}:
                        row["error_code"] = "generation_failed"
                        break
            if not completed and "error_code" not in row:
                row["error_code"] = "missing_completed"
        except Exception:
            row["error_code"] = "generation_failed"
        finally:
            try:
                await asyncio.to_thread(service.storage.clear, session_id)
            except Exception:
                row["status"] = "error"
                row["error_code"] = "session_cleanup_failed"
        rows.append(row)
    return _report(rows, runs, simulation)
