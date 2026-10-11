import asyncio
import json

import pytest

from solomon_agent.benchmark import _percentile, _sentence_ready, run_benchmark


class Storage:
    def __init__(self):
        self.cleared = []

    def clear(self, session_id):
        self.cleared.append(session_id)


class Service:
    def __init__(self, events):
        self.storage = Storage()
        self.events = events
        self.calls = []

    async def stream(self, message, mode, session_id, request_id):
        self.calls.append((message, mode, session_id, request_id))
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


def delta(text):
    return {"event": "text_delta", "data": {"delta": text}}


COMPLETED = {"event": "completed", "data": {}}


def test_nearest_rank_percentiles():
    assert _percentile([5, 1, 4, 2, 3], 0.5) == 3
    assert _percentile([5, 1, 4, 2, 3], 0.95) == 5
    assert _percentile([], 0.95) is None


def test_sentence_delimiters_defer_decimal_and_incomplete_dot():
    assert not _sentence_ready("耗时 3.")
    assert not _sentence_ready("耗时 3.14")
    assert _sentence_ready("耗时 3.14 秒。")
    assert not _sentence_ready("Done.")
    assert _sentence_ready("Done. ")
    assert _sentence_ready("Done.", completed=True)
    assert not _sentence_ready("\n")
    assert _sentence_ready("说明\n")


def test_unique_sessions_and_report_privacy():
    service = Service([delta("private-answer"), delta("。"), COMPLETED])
    report = asyncio.run(run_benchmark(service, "private-question", runs=3, simulation=True))
    assert report["sample_count"] == 3
    assert report["error_count"] == 0
    assert report["simulation"] is True
    assert report["speech_measured"] is False
    assert report["limited_samples"] is True
    assert len({call[2] for call in service.calls}) == 3
    assert len({call[3] for call in service.calls}) == 3
    assert len(service.storage.cleared) == 3
    rendered = json.dumps(report)
    assert "private-question" not in rendered and "private-answer" not in rendered
    assert report["aggregates"]["first_text_ms"]["sample_count"] == 3


def test_segment_timing_decimal_and_completion_tail(monkeypatch):
    import solomon_agent.benchmark as benchmark

    clock = [0.0]
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: clock[0])

    class Segmented(Service):
        async def stream(self, *args):
            for moment, event in [
                (0.1, delta("3.")),
                (0.2, delta("14 秒")),
                (0.3, delta("。")),
                (0.4, COMPLETED),
            ]:
                clock[0] = moment
                yield event

    report = asyncio.run(run_benchmark(Segmented([]), "question", runs=1))
    row = report["runs"][0]
    assert row["first_text_ms"] == 100
    assert row["first_sentence_ms"] == 300
    assert row["total_ms"] == 400
    assert row["sentence_at_completion"] is False

    tail = asyncio.run(run_benchmark(Service([delta("没有句号"), COMPLETED]), "q", runs=1))
    assert tail["runs"][0]["sentence_at_completion"] is True
    assert tail["runs"][0]["first_sentence_ms"] == tail["runs"][0]["total_ms"]


@pytest.mark.parametrize(
    "events,code",
    [
        ([delta("partial"), RuntimeError("secret-key")], "generation_failed"),
        ([delta("partial")], "missing_completed"),
        ([COMPLETED], "empty_response"),
        ([delta("  "), COMPLETED], "empty_response"),
        ([{"event": "error", "data": {"message": "secret-key"}}], "generation_failed"),
    ],
)
def test_failures_never_count_as_success_or_expose_exception(events, code):
    report = asyncio.run(run_benchmark(Service(events), "secret-question", runs=1))
    assert report["sample_count"] == 0 and report["error_count"] == 1
    assert report["runs"][0]["error_code"] == code
    assert report["aggregates"]["first_text_ms"]["p95"] is None
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("runs", [0, 101, -1, True])
def test_runs_rejected(runs):
    with pytest.raises(ValueError):
        asyncio.run(run_benchmark(Service([]), "q", runs=runs))


def test_cli_setup_failure_json_and_nonzero(monkeypatch, capsys):
    import sys

    from solomon_agent.cli import main
    from solomon_agent.config import Config

    monkeypatch.setattr(sys, "argv", ["solomon-agent", "benchmark", "--runs", "1"])

    def failing_config(*args):
        raise RuntimeError("sensitive-endpoint-and-key")

    monkeypatch.setattr(Config, "load", failing_config)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["error_code"] == "setup_failed"
    assert report["speech_measured"] is False
    assert "sensitive" not in output.out + output.err


def test_cli_generation_failure_reports_error_exit(monkeypatch, capsys, tmp_path):
    import sys

    import solomon_agent.api as api
    from solomon_agent.cli import main
    from solomon_agent.config import Config

    service = Service([delta("secret-answer"), RuntimeError("secret-error")])

    async def close():
        pass

    service.runtime = type("Runtime", (), {"close": staticmethod(close)})()
    monkeypatch.setattr(Config, "load", lambda *args: Config(tmp_path))
    monkeypatch.setattr(api, "build_service", lambda *args: service)
    monkeypatch.setattr(sys, "argv", ["solomon-agent", "benchmark", "--runs", "1", "--demo"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error_count"] == 1
    assert report["sample_count"] == 0
    assert report["simulation"] is True
    assert "secret" not in json.dumps(report)
